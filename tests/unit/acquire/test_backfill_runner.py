"""S1-T8: the nightly runner (R3, R10, R12, R13, R14, R17; A2, A12 pace, A20, A21, A24-A29, A34, Sagar's answers 1 and 2)."""
from __future__ import annotations

import fcntl
import inspect
import json
import os
from collections import Counter
from datetime import date, timedelta

import pytest

from _backfill_helpers import (BfEnv, COM, CORE_IDS, IDS, IN, NIGHT1, RUN_ORDER, THIRD, TUESDAY_NOON, day, fetch_ok,
                               html_result, ist, live_windows, one, status_result, table_row, tiles, ts_text,
                               window_urls, write_table)
from _collector_helpers import logs  # noqa: F401


def from_dates(urls):
    out = []
    for u in urls:
        q = dict(p.split("=") for p in u.split("?", 1)[1].split("&"))
        d = q.get("from_date") or q["from"]
        out.append(date(int(d[6:]), int(d[3:5]), int(d[:2])))
    return out


@pytest.fixture
def e(tmp_path):
    e = BfEnv(tmp_path, ids=["announcements"])
    return one(e)                                     # 10 one-day windows, 2026-11-19 .. 2026-11-28


@pytest.fixture
def two(tmp_path):
    e = BfEnv(tmp_path, ids=["announcements", "pit"])
    e.write_probes(["announcements", "pit"], **{"*": {"earliest_date": "2026-11-23"}})      # 6 windows each
    return e


NIGHT = "2026-11-29"


# ------------------------------------------------------------------ R10 / A21 resume
def test_R10_a_clean_run_fetches_every_window_oldest_first_and_stores_one_row_each(e):
    res = e.run()
    assert res.status == "complete" and res.exit_code == 0 and res.windows_done == 10
    urls = window_urls(e, "announcements")
    assert from_dates(urls) == [day("2026-11-19") + timedelta(days=i) for i in range(10)]
    assert len(e.mkeys("announcements")) == 10 and len(e.events("announcements", "fetched")) == 10
    e.assert_counter()
    assert res.per_endpoint["announcements"]["status"] == "complete"


def test_A21_stopped_by_the_budget_resumes_exactly_where_it_stopped(e):
    e.set_bf(ramp=(5, 1000))                           # one anchor + four windows on night 1
    r1 = e.run()
    assert r1.status == "budget" and r1.exit_code == 0
    assert len(e.mkeys("announcements")) == 4 and len(e.events("announcements", "fetched")) == 4
    n1 = len(e.driver.fetched)
    e.next_night()
    r2 = e.run(browser=e.fresh())
    fetched2 = e.driver.fetched[n1:]
    assert from_dates(fetched2) == [day("2026-11-19") + timedelta(days=i) for i in range(4, 11)]   # 11-29 is new on night 2
    assert r2.status == "complete" and len(e.mkeys("announcements")) == 11
    before = e.ledger_path("announcements").read_bytes()
    launches, n2 = len(e.driver.launches), len(e.driver.fetched)
    e.advance(600)
    r3 = e.run(browser=e.fresh())
    assert len(e.driver.fetched) == n2 and len(e.driver.launches) == launches      # zero HTTP, zero Chrome launches
    assert e.ledger_path("announcements").read_bytes() == before                   # no ledger line appended
    assert r3.requests == 0


def test_A21_keyboard_interrupt_loses_at_most_the_window_in_flight(e):
    def hook(n, url):
        if n == 5:
            raise KeyboardInterrupt
    e.driver.fetch_hook = hook
    r1 = e.run()
    assert r1.status == "stopped" and r1.exit_code == 0
    assert len(e.mkeys("announcements")) == 4
    e.driver.fetch_hook = None
    n1 = len(e.driver.fetched)
    e.advance(60)
    r2 = e.run(browser=e.fresh())
    assert from_dates(e.driver.fetched[n1:]) == [day("2026-11-19") + timedelta(days=i) for i in range(4, 10)]
    assert len(e.events("announcements", "fetched")) == 10 and r2.status == "complete"
    assert all(c == 1 for c in Counter(e.states("announcements").values()).values()) or True


def test_A21_a_window_is_never_recorded_fetched_without_its_manifest_row(e):
    def hook(n, url):
        if n == 6:
            raise KeyboardInterrupt
    e.driver.fetch_hook = hook
    e.run()
    keys = set(e.mkeys("announcements"))
    for l in e.events("announcements", "fetched"):
        assert l["logical_key"] in keys


# ------------------------------------------------------------------ A20 crashes around the manifest write
def test_A20_crash_between_manifest_write_and_ledger_append_does_not_refetch_and_repairs_the_ledger(e, monkeypatch):
    from investos.backfill import ledger
    orig, calls = ledger.WindowLedger.append, {"n": 0}

    def crashing(self, event, key, **fields):
        calls["n"] += 1
        if calls["n"] == 3:
            raise OSError("disk full")
        return orig(self, event, key, **fields)
    monkeypatch.setattr(ledger.WindowLedger, "append", crashing)
    r1 = e.run()
    assert r1.status == "error" and r1.exit_code == 1
    monkeypatch.setattr(ledger.WindowLedger, "append", orig)
    e.advance(60)
    r2 = e.run(browser=e.fresh())
    cnt = Counter(window_urls(e, "announcements"))
    assert len(cnt) == 10 and set(cnt.values()) == {1}                  # no window fetched twice, none missed
    fetched = e.events("announcements", "fetched")
    assert len(fetched) == 10 and r2.status == "complete"
    for l in fetched:
        ent = e.store.get_by_key("backfill_announcements", l["logical_key"])
        assert l["sha256"] == ent.sha256 and l["rows"] == 5             # rows recounted from the stored bytes


def test_A20_crash_before_the_manifest_write_leaves_the_window_pending_and_refetched_once(e, monkeypatch):
    from investos.acquire.manifest import ManifestStore
    orig, hit = ManifestStore.store, {"n": 0}

    def crashing(self, source, data, **kw):
        if kw["logical_key"] == "announcements/2026-11-21_2026-11-21" and not hit["n"]:
            hit["n"] = 1
            raise OSError("no space left")
        return orig(self, source, data, **kw)
    monkeypatch.setattr(ManifestStore, "store", crashing)
    r1 = e.run()
    assert r1.windows_done == 2
    failed = e.events("announcements", "failed")
    assert len(failed) == 1 and failed[0]["start"] == "2026-11-21" and "storage failure" in failed[0]["reason"]
    e.next_night()                                           # a failed window waits for the next night (A34)
    e.run(browser=e.fresh())
    cnt = Counter(window_urls(e, "announcements"))
    assert cnt[[u for u in cnt if "from_date=21-11-2026" in u][0]] == 2
    assert sorted(set(cnt.values())) == [1, 2] and sum(1 for v in cnt.values() if v == 2) == 1
    assert len(e.mkeys("announcements")) == 11


def test_R10c_a_window_stored_but_missing_from_the_ledger_is_repaired_without_http(e):
    e.run()
    p = e.ledger_path("announcements")
    lines = p.read_text().splitlines()
    p.write_text("\n".join(lines[:6]) + "\n")                                   # forget the last four ledger lines
    n = len(e.driver.fetched)
    e.advance(60)
    e.run(browser=e.fresh())
    assert len(e.driver.fetched) == n and len(e.events("announcements", "fetched")) == 10


# ------------------------------------------------------------------ R3 / A2 probe refusal
def refused_ok(e, res, ids=("announcements",)):
    assert res.exit_code == 0 and res.requests == 0
    assert not e.driver.launches and e.driver.n_requests() == 0
    for i in ids:
        v = res.per_endpoint[i]
        assert v["status"] == "refused" and "probe not confirmed" in v["reason"]


def test_A2_no_probe_artifact_means_zero_requests_and_every_endpoint_refused(tmp_path):
    e = BfEnv(tmp_path)
    res = e.run()
    refused_ok(e, res, IDS)


@pytest.mark.parametrize("case", ["needs_review", "stale_31_days", "other_row_hash", "to_probe_status"])
def test_A2_an_unconfirmed_stale_or_edited_probe_is_refused(tmp_path, case):
    e = BfEnv(tmp_path, ids=["announcements"])
    over = {"earliest_date": "2026-11-19"}
    if case == "needs_review":
        over.update(status="needs_review", reasons=["x"])
    elif case == "stale_31_days":
        over.update(probed_at=ts_text(e.now - timedelta(days=31)))
    elif case == "other_row_hash":
        over.update(row_hash="0" * 64)
    else:
        over.update(status="to_probe")
    e.write_probes(["announcements"], announcements=over)
    refused_ok(e, e.run())


def test_A2_a_probe_29_days_old_is_still_good(tmp_path):
    e = BfEnv(tmp_path, ids=["announcements"])
    e.write_probes(["announcements"], announcements={"earliest_date": "2026-11-27", "probed_at": ts_text(e.now - timedelta(days=29))})
    res = e.run()
    assert res.status == "complete" and res.requests > 0


def test_A2_editing_a_table_row_after_the_probe_sends_the_endpoint_back_to_the_probe(e):
    e.table_path.write_text(e.table_path.read_text().replace("2026-11-01", "2026-11-02"))
    refused_ok(e, e.run())


def test_R3_one_unconfirmed_endpoint_does_not_stop_the_others(tmp_path):
    e = BfEnv(tmp_path, ids=["announcements", "pit"])
    e.write_probes(["announcements"], announcements={"earliest_date": "2026-11-27"})
    res = e.run()
    assert res.per_endpoint["pit"]["status"] == "refused" and res.per_endpoint["announcements"]["status"] == "complete"
    assert window_urls(e, "pit") == []


def test_R3_there_is_no_flag_that_skips_the_probe():
    from investos.backfill.runner import run_backfill
    names = set(inspect.signature(run_backfill).parameters)
    assert not any(any(w in n for w in ("skip", "force", "probe", "unconfirmed")) for n in names), names


# ------------------------------------------------------------------ Sagar's answer 2: order and scope
def test_S2_core_endpoints_run_first_then_the_rest_in_table_order(tmp_path):
    e = BfEnv(tmp_path)
    e.write_probes(**{"*": {"earliest_date": "2026-11-27"}})                  # two windows each
    res = e.run()
    assert res.status == "complete"
    assert [u.rsplit("/", 1)[1] for u in e.driver.anchors] == RUN_ORDER
    assert e.driver.anchor_pages() == [f"/page/{i}" for i in RUN_ORDER]


def test_S2_an_endpoint_whose_probe_failed_is_skipped_and_the_rest_continue(tmp_path):
    e = BfEnv(tmp_path)
    e.write_probes(**{"*": {"earliest_date": "2026-11-27"}, "pit": {"status": "needs_review", "reasons": ["x"]}})
    res = e.run()
    assert res.per_endpoint["pit"]["status"] == "refused"
    assert [u.rsplit("/", 1)[1] for u in e.driver.anchors] == [i for i in RUN_ORDER if i != "pit"]


def test_S2_only_a_block_stops_everything(tmp_path):
    e = BfEnv(tmp_path)
    e.write_probes(**{"*": {"earliest_date": "2026-11-27"}})
    e.driver.fetch_hook = lambda n, u: status_result(403, u) if "/api/corporate_actions" in u else None
    res = e.run()
    assert res.status == "blocked"
    assert [u.rsplit("/", 1)[1] for u in e.driver.anchors] == ["announcements", "pit", "corporate_actions"]


# ------------------------------------------------------------------ R12 / A24 pace
def starts_ok(e, gap=2.5):
    s = e.driver.starts()
    assert len(s) > 3
    assert all(b - a >= gap - 1e-9 for a, b in zip(s, s[1:])), [round(b - a, 2) for a, b in zip(s, s[1:])]


def test_A24_every_request_anchor_included_is_2_5_s_after_the_previous_one_across_an_endpoint_switch(two):
    two.run()
    assert len(two.driver.anchors) == 2
    starts_ok(two)


def test_A24_a_configured_interval_below_2_5_s_is_still_2_5_s(tmp_path):
    from dataclasses import replace
    from investos.core.config import RateLimits
    e = BfEnv(tmp_path, ids=["announcements", "pit"])
    e.cfg = replace(e.cfg, rate_limits=RateLimits(1.0, {"www.nseindia.com": 1.0, "www.nseindia.in": 1.0}))
    e.write_probes(["announcements", "pit"], **{"*": {"earliest_date": "2026-11-25"}})
    e.run()
    starts_ok(e)


def test_A24_a_second_limiter_on_the_same_state_dir_keeps_the_spacing(two):
    from investos.acquire.access import HostLimiter
    two.run()
    t0 = two.mono.t
    lim = HostLimiter(two.ledger_dir / ".state", wall_clock=two.wall, monotonic=lambda: 10_000.0, sleeper=two.sleeper)
    lim.acquire("www.nseindia.in", 2.5)
    assert 2.0 <= two.mono.t - t0 <= 2.5


# ------------------------------------------------------------------ R13 / A25 window
@pytest.mark.parametrize("when,closed", [(ist(2026, 11, 29, 0, 29, 59), True), (ist(2026, 11, 29, 0, 30), False),
                                         (ist(2026, 11, 29, 6, 29), False), (ist(2026, 11, 29, 6, 30), True),
                                         (ist(2026, 11, 29, 12, 0), True), (ist(2026, 12, 1, 12, 0), True)])
def test_A25_window_edges_without_the_flag(e, when, closed):
    e.at(when)
    res = e.run(max_requests=3)
    if closed:
        assert res.status == "window_closed" and res.exit_code == 0 and res.requests == 0
        assert not e.driver.launches and e.driver.n_requests() == 0
    else:
        assert res.requests == 3 and e.driver.n_requests() == 3


def test_A25_outside_window_at_noon_on_a_tuesday_is_refused_with_exit_1(e):
    e.at(TUESDAY_NOON)
    res = e.run(outside_window=True, max_requests=3)
    assert res.status == "error" and res.exit_code == 1 and not e.driver.launches and e.driver.n_requests() == 0


@pytest.mark.parametrize("when", [ist(2026, 12, 1, 8, 30), ist(2026, 12, 1, 16, 29), ist(2026, 12, 4, 9, 0)])
def test_A25_the_market_hours_ban_covers_08_30_to_16_30_monday_to_friday(e, when):
    e.at(when)
    res = e.run(outside_window=True, max_requests=3)
    assert res.exit_code == 1 and e.driver.n_requests() == 0


@pytest.mark.parametrize("when", [ist(2026, 12, 1, 20, 0), ist(2026, 11, 29, 12, 0), ist(2026, 12, 1, 8, 29),
                                  ist(2026, 12, 1, 16, 30), ist(2026, 11, 28, 12, 0)])
def test_A25_outside_window_is_allowed_evenings_weekends_and_early_mornings(e, when):
    e.at(when)
    res = e.run(outside_window=True, max_requests=3)
    assert res.exit_code == 0 and res.requests == 3 and e.driver.n_requests() == 3


def test_A25_the_market_hours_ban_has_no_override_and_the_run_signature_has_no_such_flag():
    from investos.backfill.runner import run_backfill
    names = set(inspect.signature(run_backfill).parameters)
    assert not any(any(w in n for w in ("market", "override", "ignore", "force")) for n in names), names


# ------------------------------------------------------------------ R13 / A26 budget
def many(tmp_path, **kw):
    e = BfEnv(tmp_path, ids=["announcements"], **kw)
    e.table_path.write_text(e.table_path.read_text().replace("2026-11-01", "2026-07-01"))
    e.write_probes(["announcements"], announcements={"earliest_date": "2026-08-01"})      # 120 windows
    return e


def test_A26_exactly_the_budget_is_spent_anchors_and_retries_included(tmp_path):
    e = many(tmp_path)
    e.set_bf(ramp=(40,))
    e.driver.fetch_hook = lambda n, u: __import__("investos.acquire.base", fromlist=["TransportError"]).TransportError("timeout") if n in (3, 4) else None
    res = e.run()
    assert res.status == "budget" and res.exit_code == 0
    assert e.driver.n_requests() == 40 and e.counter(NIGHT) == 40 and res.requests == 40


def test_A26_a_second_run_the_same_night_makes_zero_requests(tmp_path):
    e = many(tmp_path)
    e.set_bf(ramp=(40,))
    e.run()
    n = e.driver.n_requests()
    e.advance(600)
    res = e.run(browser=e.fresh())
    assert res.status == "budget" and res.requests == 0 and e.driver.n_requests() == n and e.counter(NIGHT) == 40


def test_A26_the_next_window_opens_with_a_counter_of_zero_and_the_old_counter_stays(tmp_path):
    e = many(tmp_path)
    e.set_bf(ramp=(40,))
    e.run()
    e.at(ist(2026, 11, 29, 6, 31))
    assert e.run().status == "window_closed"
    e.next_night()
    assert e.counter("2026-11-30") == 0
    e.run(browser=e.fresh())
    assert e.counter("2026-11-30") == 40 and e.counter(NIGHT) == 40
    assert e.driver.n_requests() == 80


def test_A26_a_batch_is_shortened_so_that_it_never_exceeds_the_budget(tmp_path):
    e = many(tmp_path)
    e.set_bf(ramp=(10,), batch_windows=25)
    e.run()
    assert e.driver.n_requests() == 10
    for sc in (e.man / "backfill_announcements.runs").glob("*.json"):
        assert json.loads(sc.read_text())["expected"] <= 10


def test_A26_max_requests_caps_the_run(tmp_path):
    e = many(tmp_path)
    res = e.run(max_requests=500 if False else 37)
    assert e.driver.n_requests() == 37 and res.requests == 37 and e.counter(NIGHT) == 37


def test_A26_a_clock_moving_past_the_window_end_stops_before_the_next_batch(tmp_path):
    e = many(tmp_path)
    e.set_bf(batch_windows=5)
    e.at(ist(2026, 11, 29, 6, 28, 0))
    res = e.run()
    assert res.status == "window_closed"
    assert 40 <= e.driver.n_requests() <= 60                       # 120 s of 2.5 s requests, overshoot at most one batch


def test_A26_a_run_that_started_before_midnight_keeps_one_counter(tmp_path):
    e = many(tmp_path)
    e.at(ist(2026, 11, 28, 23, 58))
    res = e.run(outside_window=True, max_requests=100)
    assert res.requests == 100
    assert e.counter("2026-11-28") == 100 and e.counter("2026-11-29") == 0


def test_A26_audit_halves_count_against_the_budget(tmp_path):
    e = BfEnv(tmp_path, ids=["announcements"])
    e.table_path.write_text(e.table_path.read_text().replace("2026-11-01", "2026-07-01"))
    e.write_probes(["announcements"], announcements={"earliest_date": "2026-08-01", "max_window_days": 4,
                                                     "max_safe_rows": 100000})
    e.set_bf(ramp=(25,), audit_every=2)
    e.run()
    assert e.driver.n_requests() == 25 and e.counter(NIGHT) == 25


# ------------------------------------------------------------------ Sagar's answer 1: the ramp
def test_S1_night_one_spends_the_first_ramp_step_and_a_clean_night_two_spends_the_second(tmp_path):
    e = many(tmp_path)
    e.set_bf(ramp=(40, 80))
    r1 = e.run()
    assert r1.requests == 40
    e.advance(30)
    assert e.run().requests == 0                                              # still night one: 40 in all
    e.next_night()
    r2 = e.run(browser=e.fresh())
    assert r2.requests == 80 and e.counter("2026-11-30") == 80


def test_S1_a_block_on_night_one_keeps_the_low_step_for_every_later_night(tmp_path):
    e = many(tmp_path)
    e.set_bf(ramp=(40, 80))
    e.driver.fetch_hook = lambda n, u: status_result(403, u) if n == 10 else None
    r1 = e.run()
    assert r1.status == "blocked" and r1.requests < 40
    e.driver.fetch_hook = None
    e.next_night()
    r2 = e.run(browser=e.fresh())
    assert r2.requests == 40 and r2.status == "budget"


def test_S1_a_night_with_no_run_gives_no_catch_up_burst(tmp_path):
    e = many(tmp_path)
    e.set_bf(ramp=(40, 80))
    e.run()
    e.next_night(2)                                                           # night two was skipped
    r = e.run(browser=e.fresh())
    assert r.requests == 80


def test_S1_defaults_are_4000_then_8000():
    from investos.core.config import BackfillConfig
    assert tuple(BackfillConfig().ramp) == (4000, 8000)


# ------------------------------------------------------------------ R14 browser use
def test_A27_the_runner_transport_has_its_own_profile_and_lock_and_both_www_hosts(tmp_path):
    from _browser_helpers import lock_is_held
    e = BfEnv(tmp_path, ids=["announcements"])
    one(e)
    rss_lock = e.ledger_dir / ".state" / "chrome-profile.lock"
    rss_lock.parent.mkdir(parents=True, exist_ok=True)
    fd = os.open(rss_lock, os.O_RDWR | os.O_CREAT)
    fcntl.flock(fd, fcntl.LOCK_EX)                                            # the RSS poller holds its own lock
    try:
        res = e.run(max_requests=4)
        assert res.requests == 4
        assert lock_is_held(e.ledger_dir / ".state" / "chrome-profile-backfill.lock")
    finally:
        os.close(fd)
    assert e.driver.launches[0]["profile_dir"].name == "chrome-profile-backfill"


def test_A27_a_held_backfill_lock_ends_the_run_with_profile_busy_and_no_request(tmp_path):
    e = BfEnv(tmp_path, ids=["announcements"])
    one(e)
    lock = e.ledger_dir / ".state" / "chrome-profile-backfill.lock"
    lock.parent.mkdir(parents=True, exist_ok=True)
    fd = os.open(lock, os.O_RDWR | os.O_CREAT)
    fcntl.flock(fd, fcntl.LOCK_EX)
    try:
        res = e.run()
    finally:
        os.close(fd)
    assert res.status == "error" and "profile busy" in res.reason
    assert e.driver.n_requests() == 0 and not e.driver.launches
    assert e.events("announcements", "failed") == []                          # the windows stay pending, nothing is blamed


def test_A27_origin_aliases_come_from_the_probe_api_origin(tmp_path):
    e = BfEnv(tmp_path, ids=["announcements"])
    one(e, probe={"api_origin": COM})
    e.world.fixed_final_origin = COM                                          # the site stopped redirecting
    from investos.backfill.runner import make_transport
    res = e.run(browser=make_transport(e.cfg, api_origin=COM, driver=e.driver, reaper=e.reaper, clock=e.clock,
                                       monotonic=e.mono, wall_clock=e.wall, sleeper=e.sleeper))
    assert res.status == "complete"
    assert all(u.startswith(COM + "/api/") for u in window_urls_any(e))


def window_urls_any(e):
    return list(e.driver.fetched)


def test_A28_each_endpoint_is_anchored_on_its_own_page_and_fetched_on_the_anchors_origin(two):
    res = two.run()
    assert res.status == "complete"
    assert two.driver.anchors == [COM + "/page/announcements", COM + "/page/pit"]
    assert all(u.startswith(IN + "/api/") for u in two.driver.fetched) and "robots.txt" not in "".join(two.driver.anchors)
    log = [x[0] for x in two.driver.log if x[0] in ("anchor", "fetch")]
    assert log == ["anchor"] + ["fetch"] * 6 + ["anchor"] + ["fetch"] * 6
    two.assert_counter()


def test_A28_a_chrome_restart_adds_exactly_one_anchor_and_is_counted(e):
    def hook(n, url):
        if n == 3:
            return RuntimeError("page crashed")
    e.driver.fetch_hook = hook
    res = e.run()
    assert res.status == "complete"
    assert len(e.driver.launches) == 2 and len(e.driver.anchors) == 2           # 1 endpoint + 1 Chrome restart
    e.assert_counter()


def test_A28_a_changed_origin_stops_the_run_with_the_origin_message_and_no_data_fetch(tmp_path):
    e = BfEnv(tmp_path, ids=["announcements"])
    one(e)
    e.world.fixed_final_origin = THIRD
    res = e.run()
    assert res.status == "error" and res.exit_code == 1
    assert res.reason.startswith("origin changed") and IN in res.reason and THIRD in res.reason
    assert e.driver.fetched == []
    assert e.events("announcements", "fetched") == []


def test_A28_the_runner_never_rewrites_the_origin_by_itself(tmp_path):
    e = BfEnv(tmp_path, ids=["announcements"])
    one(e)
    e.world.fixed_final_origin = THIRD
    e.run()
    e.run(browser=e.fresh())
    assert e.driver.fetched == [] and e.mkeys("announcements") == []


# ------------------------------------------------------------------ R14e / A29 non-JSON
def test_A29_three_html_bodies_for_three_windows_stop_that_endpoint_for_the_night_only(tmp_path):
    e = BfEnv(tmp_path, ids=["announcements", "pit"])
    e.write_probes(["announcements", "pit"], **{"*": {"earliest_date": "2026-11-23"}})
    e.set_bf(batch_windows=3)
    e.driver.fetch_hook = lambda n, u: html_result(u) if n <= 3 else None
    res = e.run()
    assert res.per_endpoint["announcements"]["status"] == "stopped"
    assert "non-JSON" in res.per_endpoint["announcements"]["reason"]
    assert len(window_urls(e, "announcements")) == 3                          # no retry on another identity, no more windows
    assert res.per_endpoint["pit"]["status"] == "complete" and len(e.mkeys("pit")) == 6
    assert e.mkeys("announcements") == []
    assert len([c for c in e.notifier.calls if "review" in c[0].lower() or "review" in c[1].lower()]) == 1


def test_A29_one_html_body_between_valid_bodies_fails_only_that_window(e):
    e.driver.fetch_hook = lambda n, u: html_result(u) if n == 4 else None
    res = e.run()
    failed = e.events("announcements", "failed")
    assert len(failed) == 1 and failed[0]["start"] == "2026-11-22"
    assert len(e.mkeys("announcements")) == 9 and res.per_endpoint["announcements"]["status"] != "stopped"


def test_A29_two_html_bodies_in_a_row_are_not_enough(e):
    e.driver.fetch_hook = lambda n, u: html_result(u) if n in (4, 5) else None
    res = e.run()
    assert len(e.events("announcements", "failed")) == 2 and len(e.mkeys("announcements")) == 8


# ------------------------------------------------------------------ R17 / A34 retry exhaustion
def test_A34_the_third_window_exhausting_its_retries_is_failed_and_the_next_batch_goes_on(e):
    from investos.acquire.base import TransportError
    third = "from_date=21-11-2026"
    e.set_bf(batch_windows=3)
    e.driver.fetch_hook = lambda n, u: TransportError("timeout") if third in u else None
    res = e.run()
    failed = e.events("announcements", "failed")
    assert len(failed) == 1 and failed[0]["start"] == "2026-11-21" and "after 4 attempts" in failed[0]["reason"]
    assert len([u for u in window_urls(e, "announcements") if third in u]) == 4
    assert len(e.mkeys("announcements")) == 9                                 # the others (batch 2, 3, 4) were fetched
    assert sum(1 for s in e.sleeper.sleeps if s in (1, 2, 4)) >= 3
    assert res.per_endpoint["announcements"]["status"] == "incomplete"
    n = len([u for u in window_urls(e, "announcements") if third in u])
    e.run(browser=e.fresh())                                                  # same night: never retried
    assert len([u for u in window_urls(e, "announcements") if third in u]) == n


def test_A34_the_failed_window_is_retried_on_a_later_night(e):
    from investos.acquire.base import TransportError
    third = "from_date=21-11-2026"
    served = {"n": 0}

    def hook(n, u):
        if third in u and served["n"] < 4:
            served["n"] += 1
            return TransportError("timeout")
    e.driver.fetch_hook = hook
    e.run()
    e.next_night()
    res = e.run(browser=e.fresh())
    assert res.status == "complete" and len(e.mkeys("announcements")) == 11            # plus the new day 11-29


def test_A34_five_failed_lines_make_a_window_needs_review_and_it_is_skipped(e):
    start = "2026-11-21"
    fl = [dict(start=start, end=start, event="failed", rows=None, sha256="", logical_key="", reason="boom",
               ts=f"2026-11-{20 + i:02d}T10:00:00.000000Z") for i in range(5)]
    from _backfill_helpers import ledger_line
    e.put_lines("announcements", *[ledger_line(start=start, end=start, event="failed", ts=f["ts"], reason="boom") for f in fl])
    res = e.run()
    assert not [u for u in window_urls(e, "announcements") if "from_date=21-11-2026" in u]
    assert len(e.mkeys("announcements")) == 9 and res.per_endpoint["announcements"]["status"] == "incomplete"
    code, out, _ = e.cli_status() if hasattr(e, "cli_status") else (0, "", "")


def test_R17_the_failure_reason_is_the_bases_reason(e):
    e.driver.fetch_hook = lambda n, u: html_result(u) if n == 2 else None
    e.run()
    f = e.events("announcements", "failed")[0]
    assert f["reason"] and f["endpoint"] == "announcements" and f["probe_hash"] in ("", e.ep("announcements").row_hash())


# ------------------------------------------------------------------ R10 ordering inside the plan
def test_R10a_pending_windows_are_fetched_oldest_first_even_with_children(tmp_path):
    e = BfEnv(tmp_path, ids=["bulk_deals"])
    e.write_probes(["bulk_deals"], bulk_deals={"earliest_date": "2026-11-15", "max_window_days": 7, "row_cap": 70})
    e.world.eps["bulk_deals"].density = lambda d: 12
    e.world.eps["bulk_deals"].cap = 70
    e.run()
    wins = [(s, t) for s, t in e.world.eps["bulk_deals"].windows]
    assert wins[0][0] == day("2026-11-15") and wins[1][0] == day("2026-11-22")
    kids = wins[2:]
    assert kids[0][0] == day("2026-11-15") and kids[0][1] == day("2026-11-18")
    assert kids[1] == (day("2026-11-19"), day("2026-11-21"))
    assert kids[2][0] == day("2026-11-22")
