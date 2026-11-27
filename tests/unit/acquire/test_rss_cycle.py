"""S1-T6 step 4b: poll_cycle, dedup, state, gap ledger, freshness, limiter, logs
(R8, R8a, R9-R13, R17, R22; A10, A10a, A11-A19, A24, A30, A31)."""
from __future__ import annotations

import fcntl
import hashlib
import json
import os
import re
import time
from datetime import timedelta

import pytest

from _collector_helpers import events, logs, resp, terr  # noqa: F401
from _rss_helpers import (IST, NSE, TS_RE, URL_RBI, RssEnv, UTC, body_of, canon, cycle_worker, env_tree, fields_of,
                          fmt_ts, frow, ist, ist_dt, item, log_text, make_rss, numbered, scheduled_between, sha,
                          slot_str, write_feeds)
from _s1_helpers import CTX, CONTACT

N0 = ist(2026, 11, 27, 9, 0, 30)                    # Friday, slot 20261127T0900
SLOT0 = "20261127T0900"
Q = timedelta(minutes=15)
URL_A, URL_B = NSE + "a.xml", NSE + "b.xml"


def one_feed(tmp, **kw):
    kw.setdefault("scope", "always")
    kw.setdefault("stale", "525600")
    return RssEnv(tmp, [frow("f1", URL_A, **kw)])


def three_feeds(tmp, **kw):
    rows = [frow("f1", URL_A, scope="always", stale="525600"), frow("f2", URL_B, scope="always", stale="525600"),
            frow("f3", URL_RBI, kind="news", publisher="RBI", scope="always", stale="525600")]
    return RssEnv(tmp, rows, **kw)


def n_calls(e, fid):
    return e.transport.n(next(r["url"] for r in e.rows if r["feed_id"] == fid))


# ================================================================== R8 / A10 dedup
def test_A10_identical_bodies_second_poll_is_skipped_without_a_row(tmp_path, logs):
    e = one_feed(tmp_path)
    body = body_of(1, 2)
    e.serve("f1", body)
    r1 = e.cycle(N0)
    assert r1.outcomes["f1"] == "fetched" and len(e.rss_manifest()) == 1
    r2 = e.cycle(N0 + Q)
    assert r2.outcomes["f1"] == "skipped" and e.transport.n() == 2 and len(e.rss_manifest()) == 1
    sk = [x for x in logs() if x["event"] == "skipped" and x["fields"].get("reason") == "unchanged_body"]
    assert len(sk) == 1
    assert e.state("f1")["last_ok_slot"] == "20261127T0915"


@pytest.mark.planted
def test_A10_one_changed_byte_stores_a_second_row_and_A_B_A_stores_the_third(tmp_path):
    e = one_feed(tmp_path)
    a = body_of(1, 2)
    b = a.replace(b"Item 1", b"Item X")
    assert len(a) == len(b) and a != b
    for i, body in enumerate([a, a, b, a]):
        e.serve("f1", body)
        e.cycle(N0 + i * Q)
    rows = e.rss_manifest()
    assert [r["sha256"] for r in rows] == [sha(a), sha(b), sha(a)]
    assert [r["logical_key"] for r in rows] == [f"f1/{slot_str(N0 + k * Q)}" for k in (0, 2, 3)]


@pytest.mark.planted
@pytest.mark.parametrize("damage", ["delete", "garbage"])
def test_A10_lost_or_garbage_state_never_skips_a_body(tmp_path, damage):
    e = one_feed(tmp_path)
    e.serve("f1", body_of(1, 2))
    e.cycle(N0)
    if damage == "delete":
        e.state_path("f1").unlink()
    else:
        e.write_state("f1", raw="garbage{{{")
    r = e.cycle(N0 + Q)
    assert r.outcomes["f1"] == "fetched" and len(e.rss_manifest()) == 2


# ================================================================== R8a / A10a content digest
LB1, LB2 = "Fri, 27 Nov 2026 22:50:08 +0530", "Fri, 27 Nov 2026 23:00:08 +0530"


def test_A10a_content_digest_ignores_only_lastbuilddate():
    from investos.acquire.rss import content_digest
    items = [numbered(1), numbered(2)]
    a, b = make_rss(items, last_build=LB1), make_rss(items, last_build=LB2)
    assert a != b and content_digest(a) == content_digest(b)
    assert content_digest(a) == hashlib.sha256(re.sub(rb"<lastBuildDate>[^<]{0,200}</lastBuildDate>", b"", a)).hexdigest()
    assert content_digest(a) != content_digest(make_rss([numbered(1), numbered(3)], last_build=LB1))
    assert content_digest(a) != content_digest(a + b"\n")


@pytest.mark.planted
def test_A10a_digest_is_exact_case_and_bounded():
    from investos.acquire.rss import content_digest
    a = make_rss([numbered(1)], channel_extra="<lastbuilddate>x</lastbuilddate>")
    b = make_rss([numbered(1)], channel_extra="<lastbuilddate>y</lastbuilddate>")
    assert content_digest(a) != content_digest(b)                       # wrong case is not removed
    long1 = make_rss([numbered(1)], last_build="a" * 201)
    long2 = make_rss([numbered(1)], last_build="b" * 201)
    assert content_digest(long1) != content_digest(long2)               # more than 200 chars is not removed


@pytest.mark.planted
def test_A10a_lastbuilddate_only_difference_is_skipped(tmp_path):
    e = one_feed(tmp_path)
    items = [numbered(1), numbered(2)]
    a, b = make_rss(items, last_build=LB1), make_rss(items, last_build=LB2)
    e.serve("f1", a)
    e.cycle(N0)
    e.serve("f1", b)
    r = e.cycle(N0 + Q)
    assert r.outcomes["f1"] == "skipped" and len(e.rss_manifest()) == 1 and e.transport.n() == 2


@pytest.mark.planted
@pytest.mark.parametrize("what", ["pubdate", "title", "byte_outside", "item_added"])
def test_A10a_any_other_difference_stores_a_new_row_byte_exact_with_digest_in_state(tmp_path, what):
    from investos.acquire.rss import content_digest
    e = one_feed(tmp_path)
    a = make_rss([numbered(1), numbered(2)], last_build=LB1)
    b = {"pubdate": make_rss([numbered(1), numbered(2, pub="27-Nov-2026 10:00:01")], last_build=LB1),
         "title": make_rss([numbered(1), item(guid="g2", link=numbered(2)["link"], title="Item 2!", pub="27-Nov-2026 10:00:00")],
                           last_build=LB1),
         "byte_outside": a + b"\n",
         "item_added": make_rss([numbered(1), numbered(2), numbered(3)], last_build=LB1)}[what]
    e.serve("f1", a)
    e.cycle(N0)
    e.serve("f1", b)
    r = e.cycle(N0 + Q)
    assert r.outcomes["f1"] == "fetched"
    rows = e.rss_manifest()
    assert len(rows) == 2 and rows[1]["sha256"] == sha(b) and e.store.read_bytes("rss", sha(b)) == b
    st = e.state("f1")
    assert st["last_stored_sha"] == sha(b) and st["last_content_digest"] == content_digest(b)


@pytest.mark.planted
def test_A10a_state_without_a_digest_means_the_body_is_stored(tmp_path):
    e = one_feed(tmp_path)
    e.serve("f1", body_of(1))
    e.cycle(N0)
    st = e.state("f1")
    st["last_content_digest"] = None
    e.write_state("f1", **{k: v for k, v in st.items() if k not in ("schema", "feed_id")})
    r = e.cycle(N0 + Q)
    assert r.outcomes["f1"] == "fetched" and len(e.rss_manifest()) == 2


def test_A10a_state_holds_digest_of_the_stored_body_and_raw_sha_separately(tmp_path):
    from investos.acquire.rss import content_digest
    e = one_feed(tmp_path)
    body = make_rss([numbered(1)], last_build=LB1)
    e.serve("f1", body)
    e.cycle(N0)
    st = e.state("f1")
    assert st["last_stored_sha"] == sha(body) and st["last_content_digest"] == content_digest(body)
    assert st["last_stored_sha"] != st["last_content_digest"]


# ================================================================== R9 / A11 schedule in the cycle
@pytest.mark.planted
def test_A11_unscheduled_slot_does_nothing(tmp_path, logs):
    e = one_feed(tmp_path)
    e.serve("f1")
    before = env_tree(e)
    r = e.cycle(ist(2026, 11, 27, 6, 0, 10))
    assert r.skipped_slot is True and r.polled == () and r.slot == "20261127T0600"
    assert e.transport.n() == 0 and env_tree(e) == before
    assert any(x["event"] == "slot_not_scheduled" and x["level"] == "INFO" for x in logs())


def test_A11_force_polls_an_unscheduled_slot(tmp_path):
    e = one_feed(tmp_path)
    e.serve("f1")
    r = e.cycle(ist(2026, 11, 27, 6, 0, 10), force=True)
    assert r.skipped_slot is False and r.polled == ("f1",) and e.transport.n() == 1
    assert e.state("f1")["last_ok_slot"] == "20261127T0600"


def test_A11_overnight_scheduled_slot_polls(tmp_path):
    e = one_feed(tmp_path)
    e.serve("f1")
    r = e.cycle(ist(2026, 11, 27, 1, 0, 5))
    assert r.skipped_slot is False and r.polled == ("f1",)


# ================================================================== R10 / A12
@pytest.mark.planted
def test_A12_each_feed_gets_its_own_run_and_a_retry_exhausted_feed_does_not_stop_the_others(tmp_path):
    e = three_feeds(tmp_path)
    e.serve("f1")
    e.serve("f2", status=503)
    e.serve("f3")
    r = e.cycle(N0)
    assert r.polled == ("f1", "f2", "f3")
    assert (r.outcomes["f1"], r.outcomes["f2"], r.outcomes["f3"]) == ("fetched", "unreadable", "fetched")
    assert n_calls(e, "f2") == 4 and n_calls(e, "f1") == 1 and n_calls(e, "f3") == 1
    for fid in ("f1", "f2", "f3"):
        sc = e.sidecar_for(fid)
        assert sc["source"] == "rss" and sc["date_check"] == "not_date_keyed" and sc["expected"] == 1
    assert e.sidecar_for("f2")["status"] == "aborted"
    assert e.transport.max_inflight == 1


@pytest.mark.planted
def test_A12_a_403_makes_same_host_feeds_not_attempted_and_other_hosts_proceed(tmp_path):
    e = three_feeds(tmp_path)
    e.serve("f1", b"denied", status=403)
    e.serve("f2")
    e.serve("f3")
    r = e.cycle(N0)
    assert r.outcomes["f1"] == "unreadable" and r.outcomes["f2"] == "not_attempted" and r.outcomes["f3"] == "fetched"
    assert n_calls(e, "f2") == 0 and n_calls(e, "f3") == 1
    assert e.host_events("nsearchives.nseindia.com")[0]["event"] == "blocked"
    assert e.sidecar_for("f2")["outcomes"]["not_attempted"] == 1


def test_R10_feeds_are_polled_in_table_order_and_disabled_rows_are_not_polled(tmp_path):
    rows = [frow("f3", URL_RBI, kind="news", scope="always"), frow("f1", URL_A, scope="always"),
            frow("f2", URL_B, enabled="no", scope="always")]
    e = RssEnv(tmp_path, rows)
    e.serve("f3")
    e.serve("f1")
    r = e.cycle(N0)
    assert r.polled == ("f3", "f1")
    assert [c["url"] for c in e.transport.calls] == [URL_RBI, URL_A]


@pytest.mark.planted
def test_R10_invalid_table_aborts_before_any_fetch(tmp_path):
    from investos.acquire.rss import FeedTableError
    e = one_feed(tmp_path)
    e.serve("f1")
    write_feeds(e.feeds_path, [frow("f1", URL_A, stale="14")])
    with pytest.raises(FeedTableError):
        e.cycle(N0)
    assert e.transport.n() == 0 and not e.state_path("f1").exists()


def test_R3_zero_enabled_rows_does_nothing_with_a_warning(tmp_path, logs):
    e = RssEnv(tmp_path, [frow("f1", URL_A, enabled="no")])
    r = e.cycle(N0)
    assert r.polled == () and e.transport.n() == 0
    assert any(x["event"] == "no_enabled_feeds" and x["level"] == "WARNING" for x in logs())


@pytest.mark.planted
def test_A13_second_cycle_in_the_same_slot_polls_nothing(tmp_path):
    e = three_feeds(tmp_path)
    e.serve_all()
    e.cycle(N0)
    n = e.transport.n()
    r = e.cycle(N0 + timedelta(minutes=5))
    assert r.polled == () and e.transport.n() == n


@pytest.mark.planted
def test_A13_only_the_failed_feed_is_polled_again_in_the_same_slot(tmp_path):
    e = three_feeds(tmp_path)
    e.serve("f1")
    e.serve("f2", status=503)
    e.serve("f3")
    e.cycle(N0)
    e.serve("f2")
    r = e.cycle(N0 + timedelta(minutes=5), run_id="cyc-2")
    assert r.polled == ("f2",) and r.outcomes["f2"] == "fetched"
    assert n_calls(e, "f1") == 1 and n_calls(e, "f3") == 1


@pytest.mark.planted
def test_A13_cycle_budget_of_600_seconds_stops_new_feeds_and_records_failed_slots(tmp_path):
    e = three_feeds(tmp_path)
    e.serve_all()
    e.transport.after.append(lambda n: setattr(e.mono, "t", e.mono.t + 601) if n == 1 else None)
    r = e.cycle(N0)
    assert e.transport.n() == 1 and n_calls(e, "f1") == 1
    lines = {l["feed_id"]: l for l in e.gap_lines()}
    assert set(lines) == {"f2", "f3"}
    for l in lines.values():
        assert l["kind"] == "failed" and l["from_slot"] == l["to_slot"] == SLOT0 and l["slots"] == 1
        assert "cycle budget" in l["reason"]
    assert e.state("f1")["last_ok_slot"] == SLOT0 and r.gaps_written == 2


def test_A13_cycle_budget_is_not_hit_just_below_600_seconds(tmp_path):
    e = three_feeds(tmp_path)
    e.serve_all()
    e.transport.after.append(lambda n: setattr(e.mono, "t", e.mono.t + 590) if n == 1 else None)
    e.cycle(N0)
    assert e.transport.n() == 3 and e.gap_lines() == []


@pytest.mark.planted
def test_A13_unknown_exception_is_logged_recorded_and_the_next_feed_is_polled(tmp_path, logs):
    e = three_feeds(tmp_path)
    e.serve("f1")
    e.script("f2", RuntimeError("boom"))
    e.serve("f3")
    r = e.cycle(N0)
    assert n_calls(e, "f3") == 1 and e.state("f3")["last_ok_slot"] == SLOT0
    assert any(x["level"] == "ERROR" for x in logs())
    g = [l for l in e.gap_lines() if l["feed_id"] == "f2"]
    assert len(g) == 1 and g[0]["kind"] == "failed" and g[0]["from_slot"] == SLOT0
    assert e.state("f2")["consecutive_failures"] == 1


@pytest.mark.planted
def test_A13_keyboard_interrupt_propagates(tmp_path):
    e = three_feeds(tmp_path)
    e.serve("f1")
    e.script("f2", KeyboardInterrupt())
    e.serve("f3")
    with pytest.raises(KeyboardInterrupt):
        e.cycle(N0)
    assert n_calls(e, "f3") == 0


# ================================================================== R11 / A14 state
def test_A14_state_after_an_ok_poll_that_stored_a_body(tmp_path):
    from investos.acquire.rss import content_digest
    e = one_feed(tmp_path)
    body = make_rss([numbered(1, "27-Nov-2026 10:00:00"), numbered(2, "27-Nov-2026 14:05:00"), numbered(3, None)])
    e.serve("f1", body)
    e.cycle(N0)
    st = e.state("f1")
    assert set(st) == {"schema", "feed_id", "last_attempt_at", "last_ok_slot", "last_ok_at", "last_stored_sha",
                       "last_content_digest", "last_change_at", "newest_pub_ist", "consecutive_failures", "alerted"}
    assert st["schema"] == 1 and st["feed_id"] == "f1" and st["last_ok_slot"] == SLOT0
    assert st["last_stored_sha"] == sha(body) and st["last_content_digest"] == content_digest(body)
    assert st["newest_pub_ist"] == "2026-11-27T14:05:00+05:30"
    assert st["consecutive_failures"] == 0 and st["alerted"] == {}
    for k in ("last_attempt_at", "last_ok_at", "last_change_at"):
        assert re.match(TS_RE, st[k]), (k, st[k])
    assert st["last_ok_at"] == st["last_change_at"] == fmt_ts(N0)
    assert e.state_path("f1").read_text().rstrip("\n") == canon(st)


def test_A14_newest_pub_is_null_when_no_item_has_a_date(tmp_path):
    e = one_feed(tmp_path)
    e.serve("f1", make_rss([numbered(1, None)]))
    e.cycle(N0)
    assert e.state("f1")["newest_pub_ist"] is None


@pytest.mark.planted
def test_A14_ok_unchanged_poll_moves_only_the_ok_fields(tmp_path):
    e = one_feed(tmp_path)
    e.serve("f1", body_of(1))
    e.cycle(N0)
    s1 = e.state("f1")
    e.cycle(N0 + Q)
    s2 = e.state("f1")
    assert s2["last_ok_slot"] == "20261127T0915" and s2["last_ok_at"] == fmt_ts(N0 + Q)
    for k in ("last_stored_sha", "last_content_digest", "last_change_at", "newest_pub_ist"):
        assert s2[k] == s1[k], k


@pytest.mark.planted
def test_A14_failure_moves_only_attempt_and_failure_count(tmp_path):
    e = one_feed(tmp_path)
    e.serve("f1", body_of(1))
    e.cycle(N0)
    s1 = e.state("f1")
    e.serve("f1", status=503)
    e.cycle(N0 + Q)
    s2 = e.state("f1")
    assert s2["consecutive_failures"] == 1 and s2["last_attempt_at"] == fmt_ts(N0 + Q)
    for k in set(s1) - {"last_attempt_at", "consecutive_failures"}:
        assert s2[k] == s1[k], k
    e.cycle(N0 + 2 * Q)
    assert e.state("f1")["consecutive_failures"] == 2
    e.serve("f1", body_of(1))
    e.cycle(N0 + 3 * Q)
    assert e.state("f1")["consecutive_failures"] == 0


@pytest.mark.planted
def test_A14_state_write_is_atomic_a_crash_before_replace_keeps_the_old_file(tmp_path, monkeypatch):
    e = one_feed(tmp_path)
    e.serve("f1", body_of(1))
    e.cycle(N0)
    before = e.state_path("f1").read_bytes()
    real = os.replace

    def boom(src, dst, *a, **k):
        if str(dst).endswith("f1.json") and ".state" in str(dst):
            raise OSError("simulated crash")
        return real(src, dst, *a, **k)
    monkeypatch.setattr(os, "replace", boom)
    try:
        e.cycle(N0 + Q)
    except Exception:
        pass
    monkeypatch.setattr(os, "replace", real)
    assert e.state_path("f1").read_bytes() == before
    assert [p.name for p in e.state_dir.iterdir() if p.name.endswith(".tmp") or p.name.startswith(".tmp")] == []


@pytest.mark.planted
def test_A14_a_held_lock_means_the_cycle_gives_up_with_exit_zero_semantics(tmp_path, logs):
    e = one_feed(tmp_path)
    e.serve("f1")
    e.state_dir.mkdir(parents=True)
    fd = os.open(e.state_dir / "f1.lock", os.O_RDWR | os.O_CREAT, 0o644)
    fcntl.flock(fd, fcntl.LOCK_EX)
    try:
        t0 = time.monotonic()
        r = e.cycle(N0)
        assert time.monotonic() - t0 < 30
    finally:
        os.close(fd)
    assert e.transport.n() == 0 and r.polled == () and not e.state_path("f1").exists()
    assert "cycle already running" in log_text(logs())


# ================================================================== R11 / A15 unreadable state
def _valid_state(e):
    return e.state("f1")


@pytest.mark.planted
@pytest.mark.parametrize("variant", ["schema2", "invalid_json", "missing_field", "unknown_field"])
def test_A15_unreadable_state_is_treated_as_absent_and_the_body_is_stored(tmp_path, logs, variant):
    e = one_feed(tmp_path)
    e.serve("f1", body_of(1, 2))
    e.cycle(N0)
    st = _valid_state(e)                                  # carries the real sha and digest: a naive reader would skip
    if variant == "schema2":
        st["schema"] = 2
    elif variant == "missing_field":
        del st["newest_pub_ist"]
    elif variant == "unknown_field":
        st["surprise"] = 1
    e.state_path("f1").write_text("{not json" if variant == "invalid_json" else canon(st) + "\n")
    r = e.cycle(N0 + Q)
    assert r.outcomes["f1"] == "fetched" and len(e.rss_manifest()) == 2
    assert any(x["event"] == "state_unreadable" and x["level"] == "WARNING" for x in logs())


# ================================================================== R12 / A16 gap ledger
def seed_ok(e, fid, slot, now=None):
    e.write_state(fid, last_ok_slot=slot, last_ok_at=fmt_ts(slot_dt_utc(slot)), last_attempt_at=fmt_ts(slot_dt_utc(slot)),
                  last_change_at=fmt_ts(slot_dt_utc(slot)))


def slot_dt_utc(slot):
    from _rss_helpers import slot_dt
    return slot_dt(slot).astimezone(UTC)


@pytest.mark.planted
def test_A16_missed_slots_are_one_line_per_feed(tmp_path):
    e = one_feed(tmp_path)
    e.serve("f1")
    seed_ok(e, "f1", "20261127T0700")
    r = e.cycle(ist(2026, 11, 27, 8, 20))
    lines = e.gap_lines()
    assert len(lines) == 1 and r.gaps_written == 1
    g = lines[0]
    assert g["kind"] == "missed" and g["from_slot"] == "20261127T0715" and g["to_slot"] == "20261127T0800"
    assert g["slots"] == len(scheduled_between("20261127T0715", "20261127T0800")) == 4
    assert g["feed_id"] == "f1" and g["schema"] == 1 and g["run_id"] == "cyc-1" and re.match(TS_RE, g["detected_at"])
    assert g["reason"]
    assert set(g) == {"schema", "feed_id", "kind", "from_slot", "to_slot", "slots", "reason", "detected_at", "run_id"}
    for raw in e.gaps_path.read_text().splitlines():
        assert raw == canon(json.loads(raw))
    assert e.gaps_path.read_text().endswith("\n")
    assert e.state("f1")["last_ok_slot"] == "20261127T0815"


@pytest.mark.planted
def test_A16_overnight_gap_counts_only_scheduled_slots(tmp_path):
    e = one_feed(tmp_path)
    e.serve("f1")
    seed_ok(e, "f1", "20261127T0100")
    e.cycle(ist(2026, 11, 27, 8, 20))
    g = e.gap_lines()
    assert len(g) == 1 and g[0]["from_slot"] == "20261127T0300" and g[0]["to_slot"] == "20261127T0800"
    assert g[0]["slots"] == len(scheduled_between("20261127T0300", "20261127T0800")) == 7


def test_A16_no_gap_when_the_previous_scheduled_slot_was_ok(tmp_path):
    e = one_feed(tmp_path)
    e.serve("f1")
    seed_ok(e, "f1", "20261127T0800")
    e.cycle(ist(2026, 11, 27, 8, 20))
    assert e.gap_lines() == []
    e2 = RssEnv(tmp_path / "b", [frow("f1", URL_A, scope="always")])
    e2.serve("f1")
    seed_ok(e2, "f1", "20261127T2345")
    e2.cycle(ist(2026, 11, 28, 0, 5), force=True)               # 00:00 is not scheduled, so nothing is missed
    assert e2.gap_lines() == []


@pytest.mark.planted
def test_A16_failed_current_slot_is_a_failed_line_with_the_reason(tmp_path):
    e = one_feed(tmp_path)
    e.serve("f1", status=503)
    seed_ok(e, "f1", "20261127T0845")
    e.cycle(N0)
    g = e.gap_lines()
    assert len(g) == 1 and g[0]["kind"] == "failed" and g[0]["from_slot"] == g[0]["to_slot"] == SLOT0
    assert g[0]["slots"] == 1 and "503" in g[0]["reason"] and g[0]["run_id"] == "cyc-1"


# ================================================================== R12 / A17
@pytest.mark.planted
def test_A17_first_run_has_no_state_and_writes_no_gap(tmp_path):
    e = one_feed(tmp_path)
    e.serve("f1")
    e.cycle(ist(2026, 11, 27, 15, 0))
    assert e.gap_lines() == []


@pytest.mark.planted
def test_A17_a_failing_gap_writer_fails_the_cycle_after_the_other_feeds_were_polled(tmp_path, logs):
    e = three_feeds(tmp_path)
    e.serve_all()
    seed_ok(e, "f1", "20261127T0700")
    e.gaps_path.parent.mkdir(parents=True)
    e.gaps_path.write_bytes(b'{"schema":1,"feed_id":"zz"')                     # torn tail: no newline
    before = e.gaps_path.read_bytes()
    try:
        e.cycle(ist(2026, 11, 27, 8, 20))
    except Exception:
        pass
    assert e.gaps_path.read_bytes() == before                                  # the writer refuses a torn tail
    assert n_calls(e, "f2") == 1 and n_calls(e, "f3") == 1                     # other feeds still polled
    assert any(x["event"] == "gap_write_failed" and x["level"] == "ERROR" for x in logs())


@pytest.mark.planted
def test_A17_two_processes_appending_concurrently_make_two_complete_lines(tmp_path):
    e = RssEnv(tmp_path, [frow("fa", URL_A, scope="always"), frow("fb", URL_B, scope="always")])
    for fid in ("fa", "fb"):
        seed_ok(e, fid, "20261127T0800")
    go = CTX.Event()
    procs = []
    for fid, url in (("fa", URL_A), ("fb", URL_B)):
        spec = {"tmp": str(tmp_path), "feed": {"feed_id": fid, "url": url}, "body": body_of(1).decode(),
                "now": ist(2026, 11, 27, 8, 50).isoformat(), "run_id": f"w-{fid}", "go": go}
        p = CTX.Process(target=cycle_worker, args=(spec, CTX.Queue()))
        p.start()
        procs.append(p)
    go.set()
    for p in procs:
        p.join(60)
        assert p.exitcode == 0
    raw = e.gaps_path.read_text()
    assert raw.endswith("\n") and len(raw.splitlines()) == 2
    assert sorted(json.loads(l)["feed_id"] for l in raw.splitlines()) == ["fa", "fb"]


# ================================================================== R13 freshness (cycle side) / A18
def trading_env(tmp_path, stale="90", scope="trading_hours", feed="f1"):
    return RssEnv(tmp_path, [frow(feed, URL_A, scope=scope, stale=stale)], trading_days=["2026-11-30"])


FRI_15 = "27-Nov-2026 15:00:00"


@pytest.mark.parametrize("h,m,s,alarm", [(10, 30, 5, False), (10, 44, 0, False), (10, 46, 0, True), (11, 0, 5, True)])
def test_A18_trading_hours_age_is_measured_from_0915_on_a_trading_day(tmp_path, h, m, s, alarm):
    e = trading_env(tmp_path)
    e.serve("f1", make_rss([numbered(1, FRI_15)]))
    e.cycle(ist(2026, 11, 30, h, m, s))                         # Monday, TRADING
    assert [c[1] for c in e.notifier.calls] == (["f1 content_stale"] if alarm else [])


@pytest.mark.planted
@pytest.mark.parametrize("when", [(2026, 11, 29, 12, 0), (2026, 11, 30, 17, 0), (2026, 11, 30, 9, 20), (2026, 11, 30, 16, 30)],
                         ids=["sunday", "after_close", "before_0930", "after_1600"])
def test_A18_trading_hours_feed_is_not_evaluated_off_hours(tmp_path, when):
    e = trading_env(tmp_path)
    e.serve("f1", make_rss([numbered(1, FRI_15)]))
    e.cycle(ist(*when), force=True)
    assert e.notifier.calls == []


def test_A18_provisional_day_counts_like_a_trading_day(tmp_path):
    e = trading_env(tmp_path)                                    # Tuesday 2026-12-01 has no session row: PROVISIONAL
    e.serve("f1", make_rss([numbered(1, "27-Nov-2026 15:00:00")]))
    e.cycle(ist(2026, 12, 1, 11, 0, 5))
    assert [c[1] for c in e.notifier.calls] == ["f1 content_stale"]


@pytest.mark.parametrize("h,m,alarm", [(9, 59, False), (10, 1, True)])
def test_A18_always_feed_with_5760_minute_window_goes_stale_on_the_fifth_day(tmp_path, h, m, alarm):
    e = one_feed(tmp_path, stale="5760")
    e.serve("f1", make_rss([numbered(1, "23-Nov-2026 10:00:00")]))
    e.cycle(ist(2026, 11, 27, h, m, 0))
    assert [c[1] for c in e.notifier.calls] == (["f1 content_stale"] if alarm else [])


@pytest.mark.planted
def test_A18_missing_pub_dates_fall_back_to_last_change_at(tmp_path):
    e = one_feed(tmp_path, stale="60")
    e.serve("f1", make_rss([numbered(1, None)]))
    e.cycle(ist(2026, 11, 27, 10, 0, 5))
    e.cycle(ist(2026, 11, 27, 10, 50, 5))
    assert e.notifier.calls == []
    e.cycle(ist(2026, 11, 27, 11, 10, 5))
    assert [c[1] for c in e.notifier.calls] == ["f1 content_stale"]


@pytest.mark.planted
def test_A19_future_dated_newest_item_cannot_hide_a_stale_feed_or_break_the_age(tmp_path):
    e = one_feed(tmp_path, stale="60")
    t0 = ist(2026, 11, 27, 10, 0, 5)
    future = (t0.astimezone(IST) + timedelta(hours=3)).strftime("%d-%b-%Y %H:%M:%S")
    e.serve("f1", make_rss([numbered(1, future)]))
    e.cycle(t0)
    e.cycle(t0 + timedelta(hours=1))
    assert e.notifier.calls == []
    e.cycle(t0 + timedelta(hours=4, minutes=1))
    assert [c[1] for c in e.notifier.calls] == ["f1 content_stale"]


@pytest.mark.planted
def test_A19_alarm_text_is_feed_and_kind_only_once_per_day_and_again_next_day(tmp_path, logs):
    e = one_feed(tmp_path)
    seed_ok(e, "f1", "20261127T0845")
    e.serve("f1", status=503)
    e.cycle(ist(2026, 11, 27, 9, 15, 5))
    assert e.notifier.calls == [("Invest OS: feed stale", "f1 poll_missed")]
    assert "http" not in e.notifier.calls[0][1] and "nseindia" not in e.notifier.calls[0][1]
    assert e.state("f1")["alerted"] == {"poll_missed": "2026-11-27"}
    e.cycle(ist(2026, 11, 27, 9, 30, 5))
    assert len(e.notifier.calls) == 1
    e.cycle(ist(2026, 11, 28, 9, 15, 5))
    assert len(e.notifier.calls) == 2 and e.state("f1")["alerted"]["poll_missed"] == "2026-11-28"
    fa = [x for x in logs() if x["event"] == "feed_alarm"]
    assert fa and fa[0]["level"] == "ERROR" and fa[0]["fields"]["feed_id"] == "f1" and fa[0]["fields"]["kind"] == "poll_missed"


def test_A19_cycle_result_alerts_name_feed_and_kind(tmp_path):
    e = one_feed(tmp_path)
    seed_ok(e, "f1", "20261127T0845")
    e.serve("f1", status=503)
    r = e.cycle(ist(2026, 11, 27, 9, 15, 5))
    assert len(r.alerts) == 1 and "f1" in r.alerts[0] and "poll_missed" in r.alerts[0]


@pytest.mark.planted
def test_A19_notifier_failure_never_breaks_the_cycle(tmp_path):
    from _s1_helpers import FakeNotifier
    e = one_feed(tmp_path)
    e.notifier = FakeNotifier(raises=RuntimeError("osascript died"))
    seed_ok(e, "f1", "20261127T0845")
    e.serve("f1", status=503)
    e.cycle(ist(2026, 11, 27, 9, 15, 5))
    assert e.state("f1")["consecutive_failures"] == 1


@pytest.mark.planted
def test_A19_unknown_calendar_year_is_judged_as_always(tmp_path):
    e = RssEnv(tmp_path, [frow("f1", URL_A, scope="trading_hours", stale="90")], calendar="empty")
    e.serve("f1", make_rss([numbered(1, FRI_15)]))
    e.cycle(ist(2026, 11, 29, 12, 0), force=True)               # a Sunday: only the 'always' reading can alarm
    assert [c[1] for c in e.notifier.calls] == ["f1 content_stale"]


# ================================================================== R17 / A24 limiter
def test_A24_example_config_gives_the_hosts_their_intervals(tmp_path):
    from investos.core.config import load_config
    from _rss_helpers import EXAMPLE_TOML
    root = tmp_path / "repo"
    for d in ("data/raw", "data/manifests", "data/canonical", "data/ledger", "reports", "registry", "db/migrations", "config"):
        (root / d).mkdir(parents=True)
    (root / "pyproject.toml").write_text("[project]\nname='x'\n")
    (root / "config" / "investos.toml").write_text(EXAMPLE_TOML.read_text())
    cfg = load_config(root / "config" / "investos.toml", repo_root=root, env={})
    rl = cfg.rate_limits
    for h in ("www.rbi.org.in", "www.sebi.gov.in", "nsearchives.nseindia.com"):
        assert rl.interval_for(h) == 1.0, h
    assert rl.interval_for("unlisted.example.org") == 0.35
    assert dict(cfg.collect.access) == {}


@pytest.mark.planted
def test_A24_24_nse_feeds_are_at_least_one_second_apart_and_need_no_daily_cap(tmp_path):
    rows = [frow(f"n{i:02d}", NSE + f"F{i}.xml", scope="always", stale="525600") for i in range(24)]
    e = RssEnv(tmp_path, rows, hosts={"nsearchives.nseindia.com": 1.0})
    e.serve_all()
    r = e.cycle(N0)
    assert len(r.polled) == 24 and e.transport.n() == 24
    ts = [c["t"] for c in e.transport.calls]
    assert all(b - a >= 1.0 - 1e-9 for a, b in zip(ts, ts[1:]))


# ================================================================== R22 / A30 logging
@pytest.mark.planted
def test_A30_cycle_log_lines_and_no_secrets(tmp_path, logs):
    e = three_feeds(tmp_path)
    secret = "BODYMARKER-7f3a"
    e.serve("f1", make_rss([item(guid="g1", title=secret, pub="27-Nov-2026 10:00:00")]))
    e.script("f2", resp(503, b"ERRBODY-9c1d", {"Set-Cookie": "HEADERSECRET-55"}))
    e.serve("f3")
    e.cycle(N0)
    recs = logs()
    cs = [x for x in recs if x["event"] == "cycle_start"]
    ce = [x for x in recs if x["event"] == "cycle_end"]
    assert len(cs) == 1 and cs[0]["task"] == "collect.rss" and cs[0]["fields"]["slot"] == SLOT0 and "feeds" in cs[0]["fields"]
    assert len(ce) == 1 and ce[0]["fields"]["gaps_written"] == 1
    assert {"ok", "failed", "not_due"} <= set(ce[0]["fields"])
    assert (ce[0]["fields"]["ok"], ce[0]["fields"]["failed"]) == (2, 1)
    assert [x for x in recs if x["event"] == "unreadable" and x["level"] == "ERROR"]
    text = log_text(recs)
    for bad in (CONTACT, secret, "ERRBODY-9c1d", "HEADERSECRET-55", "InvestOS/"):
        assert bad not in text, bad


def test_A30_feed_alarm_is_logged_at_error(tmp_path, logs):
    e = one_feed(tmp_path, stale="60")
    e.serve("f1", make_rss([numbered(1, "20-Nov-2026 10:00:00")]))
    e.cycle(N0)
    assert any(x["event"] == "feed_alarm" and x["level"] == "ERROR" for x in logs())


# ================================================================== A31 long outage
@pytest.mark.planted
def test_A31_three_day_outage_is_one_missed_line_per_feed_and_one_alarm_per_feed_and_kind(tmp_path):
    e = RssEnv(tmp_path, [frow("f1", URL_A, scope="always", stale="60"), frow("f2", URL_B, scope="always", stale="60")])
    stale_body = make_rss([numbered(1, "24-Nov-2026 10:00:00")])
    e.serve_all(stale_body)
    for fid in ("f1", "f2"):
        seed_ok(e, fid, "20261124T1200")
    now = ist(2026, 11, 27, 9, 0, 30)
    r = e.cycle(now)
    lines = e.gap_lines()
    assert sorted(l["feed_id"] for l in lines) == ["f1", "f2"] and r.gaps_written == 2
    exp = scheduled_between("20261124T1215", "20261127T0845")
    for l in lines:
        assert l["kind"] == "missed" and l["from_slot"] == "20261124T1215" and l["to_slot"] == "20261127T0845"
        assert l["slots"] == len(exp)
    assert r.outcomes == {"f1": "fetched", "f2": "fetched"}
    assert sorted(c[1] for c in e.notifier.calls) == ["f1 content_stale", "f2 content_stale"]
    e.cycle(now + Q)
    assert len(e.notifier.calls) == 2 and len(e.gap_lines()) == 2
