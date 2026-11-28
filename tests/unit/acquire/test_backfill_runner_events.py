"""S1-T8: splits, audits, blocks, halts, notifications, sleep (R7, R8, R15, R21, R23; A15, A16, A18, A30, A31, A38, A39)."""
from __future__ import annotations

import json
from datetime import timedelta
from urllib.parse import parse_qsl, urlsplit

import pytest

from _backfill_helpers import (BfEnv, COM, IN, ist, html_result, one, status_result, window_urls, day)
from _collector_helpers import logs  # noqa: F401
from _s1_helpers import EPOCH0, write_decision


def capped(tmp_path, eid="bulk_deals", earliest="2026-11-15", *, density=12, cap=70, mwd=7, **bf):
    e = BfEnv(tmp_path, ids=[eid], bf=bf or None)
    e.write_probes([eid], **{eid: {"earliest_date": earliest, "max_window_days": mwd, "row_cap": cap}})
    ep = e.world.eps[eid]
    ep.density, ep.cap = (lambda d: density), cap
    return e


# ------------------------------------------------------------------ R7 / A15 / A16 splitting inside the night
def test_A15_a_capped_window_is_split_and_the_halves_are_fetched_next_the_same_night(tmp_path):
    e = capped(tmp_path)                                    # 7 days x 12 = 84 rows served as 70: at the cap
    res = e.run()
    assert res.status == "complete"
    ev = e.events("bulk_deals")
    splits = [l for l in ev if l["event"] == "split"]
    assert {(l["start"], l["end"]) for l in splits} == {("2026-11-15", "2026-11-21"), ("2026-11-22", "2026-11-28")}
    assert not [l for l in ev if l["event"] == "fetched" and (l["start"], l["end"]) in
                {("2026-11-15", "2026-11-21"), ("2026-11-22", "2026-11-28")}]
    wins = e.world.eps["bulk_deals"].windows
    assert wins[:2] == [(day("2026-11-15"), day("2026-11-21")), (day("2026-11-22"), day("2026-11-28"))] \
        or wins[0] == (day("2026-11-15"), day("2026-11-21"))
    assert (day("2026-11-15"), day("2026-11-18")) in wins and (day("2026-11-19"), day("2026-11-21")) in wins   # 4 + 3
    assert len(e.mkeys("bulk_deals")) == 4                  # the capped bodies are never stored
    assert e.events("bulk_deals", "unsplittable") == []


def test_A15_cap_plus_one_rows_is_split_too_and_cap_minus_one_is_done(tmp_path):
    e = capped(tmp_path, earliest="2026-11-22", density=10, cap=70)          # 70 rows exactly in 7 days
    e.world.eps["bulk_deals"].density = lambda d: 10
    e.run()
    assert len(e.events("bulk_deals", "split")) == 1
    e2 = capped(tmp_path / "b", earliest="2026-11-22", density=10, cap=71)   # 70 rows below the cap of 71
    e2.run()
    assert e2.events("bulk_deals", "split") == [] and len(e2.mkeys("bulk_deals")) == 1


def test_A16_a_one_day_window_at_the_cap_is_unsplittable_once_with_no_retry_and_no_extra_parameters(tmp_path):
    e = capped(tmp_path, earliest="2026-11-27", density=100, cap=70, mwd=1)
    res = e.run()
    un = e.events("bulk_deals", "unsplittable")
    assert {(l["start"], l["end"]) for l in un} == {("2026-11-27", "2026-11-27"), ("2026-11-28", "2026-11-28")} and len(un) == 2
    assert e.mkeys("bulk_deals") == [] and e.events("bulk_deals", "split") == []
    urls = window_urls(e, "bulk_deals")
    assert len(urls) == 2 and len(set(urls)) == 2                            # one request each, never retried
    for u in urls:
        assert {k for k, _ in parse_qsl(urlsplit(u).query)} <= {"from_date", "to_date", "from", "to", "index", "symbol", "series"}
    assert res.per_endpoint["bulk_deals"]["status"] == "incomplete"
    n = len(window_urls(e, "bulk_deals"))
    e.advance(60)
    e.run(browser=e.fresh())
    assert len(window_urls(e, "bulk_deals")) == n and len(e.events("bulk_deals", "unsplittable")) == 2


# ------------------------------------------------------------------ R8b / A18 audits
def audit_env(tmp_path, hook=None):
    e = BfEnv(tmp_path, ids=["announcements"], bf={"audit_every": 3, "batch_windows": 1})
    e.write_probes(["announcements"], announcements={"earliest_date": "2026-11-19", "max_window_days": 2,
                                                     "max_safe_rows": 100000})
    e.world.eps["announcements"].row_hook = hook
    return e                                                                  # windows of 2 days: 5 of them


def parent_win(e):
    return ("2026-11-23", "2026-11-24")                                       # the 3rd window


def test_A18_halves_that_sum_to_the_parent_keep_the_parent_and_store_the_halves_as_evidence(tmp_path):
    e = audit_env(tmp_path)
    res = e.run()
    wins = e.world.eps["announcements"].windows
    assert (day("2026-11-23"), day("2026-11-23")) in wins and (day("2026-11-24"), day("2026-11-24")) in wins
    assert sum(1 for w in wins if w[0] == w[1]) == 2                           # only the 3rd window (3 % 3 == 0) was audited
    assert e.events("announcements", "audit_shrink") == [] and e.events("announcements", "superseded") == []
    assert res.per_endpoint["announcements"]["status"] == "complete"
    assert len(e.mkeys("announcements")) == 5 + 2


def test_A18_halves_that_sum_to_more_supersede_the_parent_and_halve_the_window_width(tmp_path):
    def hook(rows, s, t):
        return rows + [dict(rows[0], symbol="X1"), dict(rows[0], symbol="X2")] if s == t else rows
    e = audit_env(tmp_path, hook)
    e.run()
    sh = e.events("announcements", "audit_shrink")
    sup = e.events("announcements", "superseded")
    assert len(sh) == 1 and len(sup) == 1 and (sup[0]["start"], sup[0]["end"]) == parent_win(e)
    later = [w for w in e.world.eps["announcements"].windows if w[0] > day("2026-11-24")]
    assert later and all((b - a).days == 0 for a, b in later)                  # unfetched windows are now one day wide


def test_A18_halves_that_sum_to_less_make_the_endpoint_needs_review_and_stop_it(tmp_path):
    def hook(rows, s, t):
        return rows[:-1] if s == t else rows
    e = audit_env(tmp_path, hook)
    res = e.run()
    v = res.per_endpoint["announcements"]
    assert v["status"] == "needs_review" or v["status"] == "stopped"
    assert not [w for w in e.world.eps["announcements"].windows if w[0] > day("2026-11-24") and w[1] > day("2026-11-24")]
    assert len([c for c in e.notifier.calls if "review" in (c[0] + c[1]).lower()]) == 1


def test_A18_the_audit_runs_on_window_index_modulo_audit_every_not_on_another_index(tmp_path):
    e = BfEnv(tmp_path, ids=["announcements"], bf={"audit_every": 2})
    e.write_probes(["announcements"], announcements={"earliest_date": "2026-11-19", "max_window_days": 2,
                                                     "max_safe_rows": 100000})
    e.run()
    halves = sorted(w[0] for w in e.world.eps["announcements"].windows if w[0] == w[1])
    assert halves == [day("2026-11-21"), day("2026-11-22"), day("2026-11-25"), day("2026-11-26")]      # windows 2 and 4


# ------------------------------------------------------------------ R15 / A30 blocks
def two(tmp_path):
    e = BfEnv(tmp_path, ids=["announcements", "pit"])
    e.write_probes(["announcements", "pit"], **{"*": {"earliest_date": "2026-11-20"}})
    return e


@pytest.mark.planted
@pytest.mark.parametrize("code", [401, 403, 429, 451])
def test_A30_a_block_on_the_5th_window_stops_the_whole_run_and_notifies_once(tmp_path, code):
    e = two(tmp_path)
    e.driver.fetch_hook = lambda n, u: status_result(code, u) if n == 5 else None
    res = e.run()
    assert res.status == "blocked" and res.exit_code == 3
    bl = e.events("announcements", "blocked")
    assert len(bl) == 1 and bl[0]["start"] == "2026-11-24"
    assert e.driver.n_fetch == 5
    assert window_urls(e, "pit") == [] and e.events("pit") == []              # no later request, no other endpoint
    assert len(e.driver.anchors) == 1 and len(e.driver.launches) == 1
    assert len(e.notifier.calls) == 1
    assert [x for x in e.driver.fetched].count(e.driver.fetched[-1]) == 1     # a 429 is never retried
    for h in ("www.nseindia.com", "www.nseindia.in"):
        assert [l for l in e.host_events(h) if l["event"] == "blocked"], h    # mirrored to both hosts


@pytest.mark.planted
def test_A30_a_block_on_the_anchor_is_the_same(tmp_path):
    e = two(tmp_path)
    e.world.anchor_status = 403
    res = e.run()
    assert res.status == "blocked" and res.exit_code == 3 and e.driver.fetched == []
    assert len(e.driver.anchors) == 1 and len(e.notifier.calls) == 1
    assert [l for l in e.host_events("www.nseindia.in") if l["event"] == "blocked"]


def test_A30_a_block_on_the_com_host_also_blocks_the_in_host(tmp_path):
    e = BfEnv(tmp_path, ids=["announcements"])
    one(e, probe={"api_origin": COM})
    e.world.fixed_final_origin = COM
    from investos.backfill.runner import make_transport
    tr = make_transport(e.cfg, api_origin=COM, driver=e.driver, reaper=e.reaper, clock=e.clock, monotonic=e.mono,
                        wall_clock=e.wall, sleeper=e.sleeper)
    e.driver.fetch_hook = lambda n, u: status_result(403, u) if n == 2 else None
    res = e.run(browser=tr)
    assert res.status == "blocked"
    for h in ("www.nseindia.com", "www.nseindia.in"):
        assert [l for l in e.host_events(h) if l["event"] == "blocked"], h


# ------------------------------------------------------------------ R15f / A31
def test_A31_a_host_blocked_today_starts_no_chrome_and_exits_3_and_the_next_ist_day_proceeds(tmp_path):
    e = two(tmp_path)
    e.plant_block("www.nseindia.in", source="backfill_announcements")
    res = e.run()
    assert res.status == "blocked" and res.exit_code == 3 and res.reason
    assert not e.driver.launches and e.driver.n_requests() == 0
    e.next_night()
    res2 = e.run(browser=e.fresh())
    assert res2.requests > 0 and res2.exit_code == 0


def test_A31_a_halted_source_refuses_only_that_endpoint_and_clear_halt_lets_it_run_again(tmp_path):
    from investos.acquire.access import clear_halt
    e = two(tmp_path)
    e.plant_halt("backfill_pit")
    res = e.run()
    assert res.per_endpoint["pit"]["status"] == "halted" and window_urls(e, "pit") == []
    assert res.per_endpoint["announcements"]["status"] == "complete"
    write_decision(e.cfg.repo_root, "0007-clear.md", "Clears-halt: backfill_pit")
    clear_halt(e.cfg, "backfill_pit", decision="0007", clock=e.clock, run_id="clr-1")
    e.advance(60)
    res2 = e.run(browser=e.fresh())
    assert res2.per_endpoint["pit"]["status"] == "complete" and len(e.mkeys("pit")) == 9


# ------------------------------------------------------------------ R21 / A38 notifications
def test_A38_a_night_with_work_sends_exactly_one_notification_without_url_or_header(tmp_path):
    e = BfEnv(tmp_path, ids=["announcements"])
    one(e, earliest="2026-11-10")
    e.set_bf(ramp=(8,))
    e.run()
    assert len(e.notifier.calls) == 1
    t, m = e.notifier.calls[0]
    assert "http" not in (t + m).lower() and "user-agent" not in (t + m).lower()


def test_A38_completion_notifies_once_and_a_finished_endpoint_set_stays_silent(tmp_path):
    e = BfEnv(tmp_path, ids=["announcements"])
    one(e, earliest="2026-11-26")
    e.run()
    n = len(e.notifier.calls)
    assert 1 <= n <= 2 and any("complete" in (c[0] + c[1]).lower() for c in e.notifier.calls)
    e.next_night()
    e.run(browser=e.fresh())
    e.next_night(2)
    e.run(browser=e.fresh())
    assert not any("complete" in (c[0] + c[1]).lower() for c in e.notifier.calls[n:])


def test_A38_a_block_notifies_once_and_never_per_window(tmp_path):
    e = two(tmp_path)
    e.driver.fetch_hook = lambda n, u: status_result(429, u) if n == 3 else None
    e.run()
    assert len(e.notifier.calls) == 1


def test_A38_null_notifier_runs_no_command(tmp_path, monkeypatch):
    import subprocess
    boom = lambda *a, **k: (_ for _ in ()).throw(AssertionError("subprocess used"))
    monkeypatch.setattr(subprocess, "run", boom)
    monkeypatch.setattr(subprocess, "Popen", boom)
    from investos.core.notify import NullNotifier
    e = two(tmp_path)
    e.driver.fetch_hook = lambda n, u: status_result(403, u) if n == 2 else None
    res = e.run(notifier=NullNotifier())
    assert res.status == "blocked"


def test_A38_a_notifier_failure_never_breaks_the_run(tmp_path):
    from _s1_helpers import FakeNotifier
    e = two(tmp_path)
    e.driver.fetch_hook = lambda n, u: status_result(403, u) if n == 2 else None
    res = e.run(notifier=FakeNotifier(raises=OSError("no osascript")))
    assert res.status == "blocked" and res.exit_code == 3


# ------------------------------------------------------------------ R23 / A39 sleep
def jumping(e, at_fetch, seconds):
    off = {"v": 0.0}
    e.driver_hook_n = at_fetch

    def hook(n, u):
        if n == at_fetch:
            off["v"] += seconds
            e.t0 += timedelta(seconds=seconds)
    e.driver.fetch_hook = hook
    return (lambda: EPOCH0 + e.mono.t + off["v"])


@pytest.mark.planted
def test_A39_a_three_hour_sleep_is_logged_and_the_window_recheck_stops_the_run(tmp_path, logs):
    e = BfEnv(tmp_path, ids=["announcements"])
    one(e, earliest="2026-10-01")
    e.at(ist(2026, 11, 29, 5, 30))
    wall = jumping(e, 3, 3 * 3600)
    res = e.run(wall_clock=wall)
    assert res.status == "window_closed"
    assert any(r["event"] == "resume_after_sleep" for r in logs())
    assert e.driver.n_fetch == 3                                              # nothing fetched after the gap


@pytest.mark.planted
def test_A39_a_smaller_jump_inside_the_window_re_anchors_before_the_next_data_fetch(tmp_path, logs):
    e = BfEnv(tmp_path, ids=["announcements"])
    one(e, earliest="2026-10-01")
    wall = jumping(e, 3, 600)
    res = e.run(wall_clock=wall, max_requests=12)
    assert any(r["event"] == "resume_after_sleep" for r in logs())
    assert len(e.driver.anchors) == 2
    kinds = [x[0] for x in e.driver.log if x[0] in ("anchor", "fetch")]
    assert kinds[:6] == ["anchor", "fetch", "fetch", "fetch", "anchor", "fetch"]
    assert res.requests == 12


def test_A39_a_response_arriving_after_the_gap_that_fails_its_checks_is_never_stored(tmp_path):
    e = BfEnv(tmp_path, ids=["announcements"])
    one(e, earliest="2026-11-20")
    off = {"v": 0.0}
    wall = lambda: EPOCH0 + e.mono.t + off["v"]

    def hook(n, u):
        if n == 3:
            off["v"] += 600
            return html_result(u)
    e.driver.fetch_hook = hook
    e.run(wall_clock=wall)
    assert "from_date=22-11-2026" not in "".join(k for k in e.mkeys("announcements"))
    assert not [k for k in e.mkeys("announcements") if k.startswith("announcements/2026-11-22")]
