"""S1-T6 fix round: regression tests for the review defects D1-D5 (reports/S1-T6/review.md, commit 3f5fce2).

D1 per-feed fetch time, D2 no repeated gap lines, D3 surviving-mutant pins, D4 never-succeeded feed alarm,
D5 last_ok_slot after a gap-write failure.
"""
from __future__ import annotations

import fcntl
import json
import os
from datetime import datetime, timedelta

import pytest

from _collector_helpers import demo_class
from _rss_helpers import (IST, NSE, UTC, RssEnv, body_of, fmt_ts, frow, ist, ist_dt, item, make_rss, numbered,
                          run_check, scheduled_between, set_check_clock, slot_dt, slot_str)

N0 = ist(2026, 11, 27, 9, 0, 30)                    # Friday, slot 20261127T0900
SLOT0 = "20261127T0900"
Q = timedelta(minutes=15)
URL_A, URL_B, URL_C = NSE + "a.xml", NSE + "b.xml", NSE + "c.xml"


def one_feed(tmp, **kw):
    kw.setdefault("scope", "always")
    kw.setdefault("stale", "525600")
    return RssEnv(tmp, [frow("f1", URL_A, **kw)])


def two_feeds(tmp):
    return RssEnv(tmp, [frow("f1", URL_A, scope="always", stale="525600"),
                        frow("f2", URL_B, scope="always", stale="525600")])


def seed_ok(e, fid, slot, **over):
    t = slot_dt(slot).astimezone(UTC)
    e.write_state(fid, **{**dict(last_ok_slot=slot, last_ok_at=fmt_ts(t), last_attempt_at=fmt_ts(t),
                                 last_change_at=fmt_ts(t)), **over})


def covered(lines):
    """Every scheduled slot named by a gap line, with multiplicity (computed independently of the code)."""
    out = []
    for g in lines:
        out += scheduled_between(g["from_slot"], g["to_slot"])
    return out


def torn_ledger(e):
    e.gaps_path.parent.mkdir(parents=True, exist_ok=True)
    e.gaps_path.write_bytes(b'{"schema":1,"feed_id":"zz"')                     # torn tail: no newline


# ================================================================== D1 per-feed fetch time (R14, R11)
@pytest.mark.planted
def test_D1_each_feed_is_stamped_with_its_own_fetch_time_not_the_cycle_start(tmp_path):
    e = two_feeds(tmp_path)
    e.serve_all(body_of(1))
    t = [N0]

    def wall():                                      # real time passes: 40 s per reading
        t[0] += timedelta(seconds=40)
        return t[0]

    e.cycle(N0, clock=wall)
    rows = {r["logical_key"].split("/")[0]: r for r in e.rss_manifest()}
    f1, f2 = rows["f1"]["fetched_at"], rows["f2"]["fetched_at"]
    assert f1 != fmt_ts(N0) and f2 != fmt_ts(N0)                               # never the cycle start
    assert f1 >= fmt_ts(N0 + timedelta(seconds=40))                            # at or after its own first reading
    assert f2 > f1                                                             # the later feed is stamped later
    assert f2 >= fmt_ts(N0 + timedelta(seconds=80))


@pytest.mark.planted
def test_D1_fetched_at_is_never_earlier_than_the_transport_call(tmp_path):
    e = two_feeds(tmp_path)
    e.serve_all(body_of(1))
    calls_at = {}

    def real_now():
        return N0 + timedelta(seconds=e.mono.t)

    def note(n):
        e.mono.t += 40                                                         # each download takes 40 s
        calls_at[n] = real_now()

    e.transport.after.append(note)
    start = real_now()
    e.cycle(N0, clock=real_now)
    rows = sorted(e.rss_manifest(), key=lambda r: r["logical_key"])
    assert len(rows) == 2 and len(calls_at) == 2
    assert start == N0
    for n, r in zip((1, 2), rows):
        assert r["fetched_at"] >= fmt_ts(calls_at[n]), (n, r["fetched_at"], calls_at[n])
    assert rows[1]["fetched_at"] > fmt_ts(N0 + timedelta(seconds=40))


def test_D1_without_a_clock_the_cycle_start_is_still_the_stamp(tmp_path):
    e = two_feeds(tmp_path)
    e.serve_all(body_of(1))
    e.cycle(N0)                                                                # clock=None: falls back to now
    assert {r["fetched_at"] for r in e.rss_manifest()} == {fmt_ts(N0)}
    assert e.state("f1")["last_ok_at"] == fmt_ts(N0)


@pytest.mark.planted
def test_D1_slot_and_state_stay_on_the_cycle_start_while_the_clock_moves(tmp_path):
    e = two_feeds(tmp_path)
    e.serve_all(body_of(1))
    late = N0 + timedelta(minutes=7)                                           # the clock runs past the next slot start?
    r = e.cycle(N0, clock=lambda: late)
    assert r.slot == SLOT0
    assert e.state("f2")["last_ok_slot"] == SLOT0                              # slot stays the scheduled one
    assert {x["fetched_at"] for x in e.rss_manifest()} == {fmt_ts(late)}


# ================================================================== D2 gap lines are never repeated (R12)
@pytest.mark.planted
def test_D2_eight_failing_cycles_record_each_scheduled_slot_at_most_once(tmp_path):
    e = one_feed(tmp_path)
    e.serve("f1", status=503)
    seed_ok(e, "f1", "20261127T0845")
    for i in range(8):
        e.cycle(N0 + i * Q, run_id=f"c{i}")
    slots = covered(e.gap_lines())
    dup = sorted({s for s in slots if slots.count(s) > 1})
    assert dup == [], f"slots recorded more than once: {dup}"
    expected = [slot_str(N0 + i * Q) for i in range(8)]
    assert set(expected) <= set(slots)                                         # every failed slot is recorded
    assert all(g["kind"] in ("missed", "failed") for g in e.gap_lines())


@pytest.mark.planted
def test_D2_failures_then_recovery_never_cover_a_slot_twice(tmp_path):
    e = one_feed(tmp_path)
    seed_ok(e, "f1", "20261127T0845")
    e.serve("f1", status=503)
    for i in range(3):
        e.cycle(N0 + i * Q, run_id=f"c{i}")
    e.serve("f1", body_of(1))
    r = e.cycle(N0 + 3 * Q, run_id="c3")
    assert r.outcomes["f1"] == "fetched"
    slots = covered(e.gap_lines())
    assert sorted(s for s in set(slots) if slots.count(s) > 1) == []
    assert {SLOT0, "20261127T0915", "20261127T0930"} <= set(slots)
    assert e.state("f1")["last_ok_slot"] == "20261127T0945"


def test_D2_a_single_outage_still_reports_each_missed_slot_once(tmp_path):
    """Guard: the fix must not lose real misses (Mac asleep, no attempt) - one missed line over the whole range."""
    e = one_feed(tmp_path)
    e.serve("f1")
    seed_ok(e, "f1", "20261127T0700")
    e.cycle(ist(2026, 11, 27, 8, 20))
    g = e.gap_lines()
    assert len(g) == 1 and (g[0]["from_slot"], g[0]["to_slot"], g[0]["slots"]) == ("20261127T0715", "20261127T0800", 4)


# ================================================================== D3 surviving mutants
# ---- M65 future-dated newest item. Reading pinned (spec edge case): content age uses min(newest, now), so a future
# date never gives a negative age or an early alarm, and the alarm comes once the stated date plus the window passed.
# NOTE: min(newest, now) cannot be told from "no cap" by any alarm outcome (a negative age and a zero age both stay
# under every window >= 15 min), so this test is a pin of the reading, not a kill of M65 (see RED_TESTS.md).
def test_D3_future_dated_newest_item_gives_no_early_alarm_and_alarms_after_date_plus_window(tmp_path):
    from investos.acquire.rss import judge_feed
    e = one_feed(tmp_path, stale="60")
    feed = e.feed("f1")
    now = ist(2026, 11, 27, 10, 0)
    newest = (now.astimezone(IST) + timedelta(hours=3)).isoformat()
    st = dict(schema=1, feed_id="f1", last_attempt_at=fmt_ts(now), last_ok_slot="20261127T0945",
              last_ok_at=fmt_ts(now), last_stored_sha="a" * 64, last_content_digest="b" * 64,
              last_change_at=fmt_ts(now - timedelta(days=2)), newest_pub_ist=newest, consecutive_failures=0,
              alerted={})
    def stale(at):
        return [k for k, _ in judge_feed(feed, st, at, None).alarms if k == "content_stale"]

    assert stale(now) == [] and stale(now + timedelta(hours=3, minutes=30)) == []
    assert stale(now + timedelta(hours=4, minutes=1)) == ["content_stale"]


# ---- M56 / D5 gap-write failure
def _gap_failure_env(tmp_path):
    e = one_feed(tmp_path)
    e.serve("f1")
    seed_ok(e, "f1", "20261127T0700")
    torn_ledger(e)
    return e


@pytest.mark.planted
def test_D5_after_a_gap_write_failure_last_ok_slot_is_not_advanced(tmp_path):
    e = _gap_failure_env(tmp_path)
    r = e.cycle(ist(2026, 11, 27, 8, 20))
    assert r.gap_write_failed is True and r.outcomes["f1"] == "fetched"        # the poll itself was fine
    st = e.state("f1")
    assert st["last_ok_slot"] == "20261127T0700"                               # held back: the miss stays detectable
    assert st["last_ok_at"] == fmt_ts(slot_dt("20261127T0700").astimezone(UTC))
    assert st["consecutive_failures"] == 0 and st["last_stored_sha"]           # the body is still recorded


@pytest.mark.planted
def test_D5_the_held_back_miss_is_written_once_the_ledger_works_again(tmp_path):
    e = _gap_failure_env(tmp_path)
    e.cycle(ist(2026, 11, 27, 8, 20))
    e.gaps_path.unlink()                                                       # operator repairs the ledger
    r = e.cycle(ist(2026, 11, 27, 8, 35), run_id="c2")
    assert r.gap_write_failed is False
    slots = covered(e.gap_lines())
    assert set(scheduled_between("20261127T0715", "20261127T0800")) <= set(slots)    # nothing was lost
    assert sorted(s for s in set(slots) if slots.count(s) > 1) == []
    assert e.state("f1")["last_ok_slot"] == "20261127T0830"


def test_D5_while_the_writer_stays_broken_poll_missed_is_raised(tmp_path):
    e = _gap_failure_env(tmp_path)
    e.cycle(ist(2026, 11, 27, 8, 20))
    assert [c[1] for c in e.notifier.calls] == ["f1 poll_missed"]


# ---- M22 negative UTC offsets
@pytest.mark.planted
@pytest.mark.parametrize("text,want", [
    ("Fri, 27 Nov 2026 09:00:00 -0500", ist_dt(2026, 11, 27, 19, 30)),
    ("Fri, 27 Nov 2026 09:00:00 -05:00", ist_dt(2026, 11, 27, 19, 30)),
    ("2026-11-27T09:00:00-0800", ist_dt(2026, 11, 27, 22, 30)),
    ("27 Nov 2026 23:30 -0330", ist_dt(2026, 11, 28, 8, 30)),
])
def test_D3_negative_utc_offsets_are_converted_to_ist(text, want):
    from investos.parse.rss_items import parse_feed
    it = parse_feed(make_rss([item(guid="g", pub=text)]))[0]
    assert it.pub_status == "ok" and it.pub_ist == want
    assert it.pub_ist.astimezone(UTC) == want.astimezone(UTC)


# ---- M33 two lastBuildDate elements
@pytest.mark.planted
def test_D3_every_lastbuilddate_element_is_ignored_by_the_digest():
    from investos.acquire.rss import content_digest
    a = make_rss([numbered(1)], last_build="Fri, 27 Nov 2026 10:00:00",
                 channel_extra="<lastBuildDate>Fri, 27 Nov 2026 10:00:01</lastBuildDate>")
    b = make_rss([numbered(1)], last_build="Fri, 27 Nov 2026 10:10:00",
                 channel_extra="<lastBuildDate>Fri, 27 Nov 2026 10:10:01</lastBuildDate>")
    assert a != b and content_digest(a) == content_digest(b)
    c = make_rss([numbered(2)], last_build="Fri, 27 Nov 2026 10:00:00",
                 channel_extra="<lastBuildDate>Fri, 27 Nov 2026 10:00:01</lastBuildDate>")
    assert content_digest(a) != content_digest(c)                              # a real change still shows


@pytest.mark.planted
def test_D3_two_lastbuilddate_elements_changing_alone_is_skipped_as_unchanged(tmp_path):
    e = one_feed(tmp_path)
    pair = lambda x: make_rss([numbered(1)], last_build=f"Fri, 27 Nov 2026 10:0{x}:00",
                              channel_extra=f"<lastBuildDate>Fri, 27 Nov 2026 10:0{x}:30</lastBuildDate>")
    e.serve("f1", pair(1))
    assert e.cycle(N0).outcomes["f1"] == "fetched"
    e.serve("f1", pair(2))
    assert e.cycle(N0 + Q).outcomes["f1"] == "skipped" and len(e.rss_manifest()) == 1


# ---- M45 exactly 600 s
@pytest.mark.planted
def test_D3_a_cycle_budget_of_exactly_600_seconds_is_spent(tmp_path):
    e = RssEnv(tmp_path, [frow("f1", URL_A, scope="always"), frow("f2", URL_B, scope="always"),
                          frow("f3", URL_C, scope="always")])
    e.serve_all()
    base = e.mono.t
    e.transport.after.append(lambda n: setattr(e.mono, "t", base + 600.0) if n == 1 else None)
    e.cycle(N0)
    assert e.transport.n() == 1                                                # elapsed == 600.0: no new feed starts
    assert {g["feed_id"] for g in e.gap_lines()} == {"f2", "f3"}
    assert all("cycle budget" in g["reason"] for g in e.gap_lines())


@pytest.mark.planted
def test_D3_a_cycle_just_under_600_seconds_still_polls_the_next_feed(tmp_path):
    e = RssEnv(tmp_path, [frow("f1", URL_A, scope="always"), frow("f2", URL_B, scope="always")])
    e.serve_all()
    base = e.mono.t
    e.transport.after.append(lambda n: setattr(e.mono, "t", base + 599.9) if n == 1 else None)
    e.cycle(N0)
    assert e.transport.n() == 2 and e.gap_lines() == []


# ---- M53 lock wait bound
@pytest.mark.planted
def test_D3_the_lock_wait_is_at_most_five_seconds(tmp_path):
    e = one_feed(tmp_path)
    e.serve("f1")
    e.state_dir.mkdir(parents=True)
    fd = os.open(e.state_dir / "f1.lock", os.O_RDWR | os.O_CREAT, 0o644)
    fcntl.flock(fd, fcntl.LOCK_EX)
    try:
        t0 = e.mono.t
        r = e.cycle(N0)
        waited = e.mono.t - t0
    finally:
        os.close(fd)
    assert r.polled == () and e.transport.n() == 0
    assert 4.5 <= waited <= 5.0 + 0.25, waited                                 # waits about 5 s on the injected clock
    assert sum(e.sleeper.sleeps) == pytest.approx(waited)


# ---- M90 waived listing needs date_keyed
@pytest.mark.planted
def test_D3_an_adapter_with_a_waiver_but_without_date_keyed_is_not_listed():
    from investos.acquire import registry as reg
    snap = {k: (v.copy() if hasattr(v, "copy") else v) for k, v in vars(reg).items() if k.startswith("_REGISTERED")}
    try:
        reg.register_adapter(demo_class(source="odd_waiver", date_keyed=False, date_check_waiver="x" * 25))
        reg.register_adapter(demo_class(source="real_waiver", date_keyed=True, date_check_waiver="y" * 25))
        listed = dict(reg.waived_adapters())
        assert "odd_waiver" not in listed and listed["real_waiver"] == "y" * 25
    finally:
        for k, v in snap.items():
            cur = getattr(reg, k)
            cur.clear()
            cur.update(v)


# ---- M96 duplicate manifest key in the check
@pytest.fixture
def chk(tmp_path, monkeypatch):
    now = ist(2026, 11, 28, 12, 0, 30)                                         # Saturday
    env = RssEnv(tmp_path, [frow("f1", URL_A, scope="always", stale="5760")], trading_days=["2026-11-30"])
    set_check_clock(monkeypatch, now)
    env.fresh_state("f1", now)
    return env, now


@pytest.mark.planted
def test_D3_a_duplicate_manifest_key_fails_the_check(chk):
    e, now = chk
    e.store_row("f1", "20261127T0900", b"body-one", now)
    code, _, rep = run_check(e)
    assert code == 0, rep
    p = e.man / "rss.jsonl"
    line = p.read_text().splitlines()[0]
    p.write_text(p.read_text() + line + "\n")                                  # the same key twice
    code, out, rep = run_check(e)
    assert code == 1 and "duplicate" in (out + rep).lower() and "f1/20261127T0900" in (out + rep)


# ================================================================== D4 never-succeeded feed (R13a)
@pytest.mark.planted
def test_D4_a_feed_that_never_succeeded_raises_poll_missed_without_waiting_for_three_failures():
    from investos.acquire.rss import FeedRow, judge_feed
    feed = FeedRow("f1", URL_A, "corporate", "NSE", True, 525600, "always", "confirmed", "")
    now = ist(2026, 11, 27, 9, 30, 30)
    for fails in (1, 2):
        st = dict(schema=1, feed_id="f1", last_attempt_at=fmt_ts(now - timedelta(minutes=15)), last_ok_slot=None,
                  last_ok_at=None, last_stored_sha=None, last_content_digest=None, last_change_at=None,
                  newest_pub_ist=None, consecutive_failures=fails, alerted={})
        v = judge_feed(feed, st, now, None)
        assert [k for k, _ in v.alarms] == ["poll_missed"], fails


def test_D4_a_feed_with_no_state_at_all_is_still_only_unknown():
    from investos.acquire.rss import FeedRow, judge_feed
    feed = FeedRow("f1", URL_A, "corporate", "NSE", True, 90, "always", "confirmed", "")
    v = judge_feed(feed, None, ist(2026, 11, 27, 9, 30, 30), None)
    assert v.alarms == () and v.unknown is True


@pytest.mark.planted
def test_D4_the_cycle_alerts_for_a_new_feed_that_failed_its_first_polls(tmp_path):
    e = one_feed(tmp_path)
    e.serve("f1", status=503)
    e.cycle(N0)                                                                # first ever poll: fails (1 failure)
    assert e.state("f1")["consecutive_failures"] == 1 and e.state("f1")["last_ok_slot"] is None
    assert [c[1] for c in e.notifier.calls] == ["f1 poll_missed"]              # latest due slot 0845 has no ok poll


@pytest.mark.planted
def test_D4_the_check_flags_a_never_succeeded_feed_after_one_failure(chk):
    e, now = chk
    e.write_state("f1", last_attempt_at=fmt_ts(now - timedelta(minutes=30)), consecutive_failures=1)
    code, out, rep = run_check(e)
    assert code == 1 and "poll_missed" in (out + rep)
