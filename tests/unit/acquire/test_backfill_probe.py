"""S1-T8: the PROBE stage (R4, R5; A3-A13). The fake site (`World`) is the only source of data."""
from __future__ import annotations

import json
from datetime import date, timedelta

import pytest

from _backfill_helpers import (BfEnv, COM, IN, THIRD, IST, NIGHT1, TUESDAY_NOON, day, html_result, ist, status_result)


@pytest.fixture
def e(tmp_path):
    return BfEnv(tmp_path, start_date="2005-01-01")


def probe(e, eid="announcements", **kw):
    return e.direct_probe(eid, **kw)


def ep_of(e, eid="announcements"):
    return e.world.eps[eid]


# ------------------------------------------------------------------ A3 anchor and origin
def test_A3_com_anchor_landing_on_in_is_recorded_as_api_origin(e):
    rep = []
    r = probe(e, report=rep)
    assert r.api_origin == IN and r.status == "confirmed"
    assert e.driver.anchors[0] == COM + "/page/announcements"


def test_A3_anchor_without_redirect_records_its_own_origin(e):
    e.world.fixed_final_origin = COM
    assert probe(e).api_origin == COM


def test_A3_anchor_landing_on_a_third_host_fails_closed_and_stores_nothing(e):
    e.world.fixed_final_origin = THIRD
    r = probe(e)
    assert r.status == "needs_review" and any("anchor left origin" in x for x in r.reasons)
    assert e.driver.fetched == []
    assert e.store.entries("backfill_probe") == []


# ------------------------------------------------------------------ A4 shape
def test_A4_bare_list_shape_records_rows_path_empty_fields_and_date_candidates(e):
    rep = []
    r = probe(e, report=rep)
    assert r.rows_path == "" and r.date_field == "an_dt" and r.status == "confirmed"
    text = "\n".join(rep)
    for f in ("an_dt", "desc", "symbol"):
        assert f in text


def test_A4_object_with_rows_key_records_the_key(e):
    ep_of(e).wrapper = "data"
    r = probe(e)
    assert r.rows_path == "data" and r.status == "confirmed"


@pytest.mark.parametrize("mode", ["html", "empty_obj", "not_json", "error_obj"])
def test_A4_html_empty_object_or_garbage_is_needs_review_not_a_shape(e, mode):
    ep_of(e).mode = mode
    r = probe(e)
    assert r.status == "needs_review" and any("not json rows" in x for x in r.reasons)


def test_A4_a_date_field_named_in_the_table_is_used_when_it_is_a_candidate(tmp_path):
    e = BfEnv(tmp_path, start_date="2005-01-01", date_field="filed")
    e.world.eps["announcements"].date_field = "filed"
    assert probe(e).date_field == "filed"


# ------------------------------------------------------------------ A5 cap derivation (planted: do not trust the table)
def test_A5_same_count_for_two_window_widths_is_the_cap(e):
    ep_of(e).density = lambda d: 30
    ep_of(e).cap = 70
    r = probe(e)
    assert r.row_cap == 70 and r.status == "confirmed"


def test_A5_cap_is_measured_not_taken_from_the_table(tmp_path):
    e = BfEnv(tmp_path, start_date="2005-01-01", row_cap="70")           # the table says 70, the site says 100
    e.world.eps["announcements"].density = lambda d: 20
    e.world.eps["announcements"].cap = 100
    assert probe(e).row_cap == 100


def test_A5_round_count_once_with_halves_summing_to_more_is_a_cap(e):
    ep_of(e).density = lambda d: 33
    ep_of(e).cap = 500
    assert probe(e).row_cap == 500


def test_A5_round_count_with_equal_halves_is_not_a_cap(e):
    ep_of(e).density = lambda d: 17 if d.day <= 4 else 16                    # July 2026 totals exactly 500
    r = probe(e)
    assert r.row_cap is None and r.max_safe_rows >= 500


def test_A5_no_repeat_and_no_round_number_means_none_observed_and_max_safe_rows(e):
    ep_of(e).density = lambda d: 45
    r = probe(e)
    assert r.row_cap is None and r.max_safe_rows == 31 * 45 and r.status == "confirmed"


def test_A5_rows_per_day_is_recorded_for_the_planner(e):
    ep_of(e).density = lambda d: 45
    assert probe(e).rows_per_day == pytest.approx(45)


# ------------------------------------------------------------------ A6 undercount
def test_A6_undercount_sets_max_window_days_to_the_largest_complete_width(e):
    ep_of(e).undercount = lambda w, n, s: n if w <= 3 else 1
    r = probe(e)
    assert r.undercount is True and r.max_window_days == 3 and r.status == "confirmed"
    assert r.row_cap is None


def test_A6_undercount_with_no_complete_width_is_needs_review(e):
    ep_of(e).undercount = lambda w, n, s: 1 if w >= 2 and s.month == 7 else n
    r = probe(e)
    assert r.status == "needs_review" and r.undercount is True and any("undercount" in x for x in r.reasons)


def test_A6_a_normal_endpoint_is_not_flagged(e):
    assert probe(e).undercount is False


# ------------------------------------------------------------------ A7 inclusivity
def test_A7_inclusive_to(e):
    r = probe(e)
    assert r.to_inclusive is True and r.status == "confirmed"


def test_A7_exclusive_to(e):
    ep_of(e).to_inclusive = False
    r = probe(e)
    assert r.to_inclusive is False and r.status == "confirmed"


def test_A7_rows_of_the_day_appearing_zero_times_is_ambiguous(e):
    ep_of(e).from_inclusive = False
    assert probe(e).status == "needs_review"


def test_A7_rows_of_the_day_that_differ_between_the_two_windows_are_ambiguous(e):
    def hook(rows, s, t):
        if s == date(2026, 11, 26):
            return [r for i, r in enumerate(rows) if not (r["an_dt"].startswith("26-Nov") and i % 2)]
        return rows
    ep_of(e).row_hook = hook
    assert probe(e).status == "needs_review"


# ------------------------------------------------------------------ A8 earliest date
def test_A8_walks_forward_then_bisects_to_the_month_and_checks_a_month_before(e):
    ep_of(e).earliest = date(2005, 3, 15)
    r = probe(e)
    assert date(2005, 3, 1) <= r.earliest_date <= date(2005, 3, 31) and r.status == "confirmed"
    wins = ep_of(e).windows
    assert any(date(2005, 3, 15) - timedelta(days=45) <= s and t <= date(2005, 3, 15) - timedelta(days=20)
               for s, t in wins), "a window one month before the earliest date must be fetched"


def test_A8_data_before_the_table_start_walks_back_in_year_steps_and_stops_at_the_first_empty_year(tmp_path):
    e = BfEnv(tmp_path, start_date="2010-01-01")
    e.world.eps["announcements"].earliest = date(2007, 6, 15)
    r = probe(e)
    assert date(2007, 1, 1) <= r.earliest_date <= date(2008, 1, 7) and r.status == "confirmed"
    assert min(s for s, t in e.world.eps["announcements"].windows) >= date(2007, 1, 1)


def test_A8_no_data_at_all_is_needs_review(e):
    ep_of(e).earliest = date(2999, 1, 1)
    assert probe(e).status == "needs_review"


# ------------------------------------------------------------------ A9 time of day
def test_A9_no_time_before_a_year_and_time_after_is_none_before_with_the_year_found(e):
    ep_of(e).time_from = date(2012, 1, 1)
    r = probe(e)
    assert r.time_of_day.startswith("none_before:")
    d = day(r.time_of_day.split(":", 1)[1])
    assert date(2011, 1, 1) <= d <= date(2012, 12, 31)


def test_A9_all_with_time(e):
    assert probe(e).time_of_day == "all"


def test_A9_all_without_time(e):
    ep_of(e).time_from = date(2999, 1, 1)
    assert probe(e).time_of_day == "none"


def test_A9_mixed_inside_one_window(e):
    ep_of(e).time_from = date(2999, 1, 1)
    ep_of(e).mixed = True
    assert probe(e).time_of_day == "mixed"


# ------------------------------------------------------------------ A10 shareholding
def test_A10_market_wide_quarter_listing_is_per_quarter(e):
    r = probe(e, "shareholding")
    assert r.mode == "per_quarter" and r.status == "confirmed"


def test_A10_empty_listing_then_per_company_calls_give_mode_and_depth(e):
    s = e.world.eps["shareholding"]
    s.market_wide = False
    s.symbol_rows = 23
    r = probe(e, "shareholding", symbols=["RELIANCE", "SMALLCO", "DELISTED"])
    assert r.mode == "per_company" and r.depth == 23 and r.status == "confirmed"
    assert r.symbol_source
    assert date(2021, 1, 1) <= r.earliest_date <= date(2021, 12, 31)           # oldest quarter returned
    urls = [u for u in e.driver.fetched if "symbol=" in u]
    assert {u.split("symbol=")[1] for u in urls} == {"RELIANCE", "SMALLCO", "DELISTED"}


def test_A10_other_endpoints_have_no_mode(e):
    assert probe(e).mode == ""


# ------------------------------------------------------------------ A11 determinism (planted)
def test_A11_a_reference_window_whose_rows_change_is_needs_review(e):
    seen = {}

    def hook(rows, s, t):
        seen[(s, t)] = seen.get((s, t), 0) + 1
        if s == date(2026, 7, 1) and (t - s).days == 6 and seen[(s, t)] > 1:
            return rows[:-1] + [{**rows[-1], "symbol": "CHANGED"}]
        return rows
    ep_of(e).row_hook = hook
    r = probe(e)
    assert r.status == "needs_review" and any("non-deterministic" in x for x in r.reasons)


def test_A11_only_a_wrapper_timestamp_differing_is_still_confirmed(e):
    ep_of(e).wrapper = "data"                                                   # meta.call differs on every call
    assert probe(e).status == "confirmed"


# ------------------------------------------------------------------ A12 request cap and pace
def july_heavy(d):
    """July 2026 counts from July 1 are 50, 100, 250, 500, 1000 for 1, 3, 7, 14, 31 days: every rung is a round number."""
    if (d.year, d.month) != (2026, 7):
        return 5
    return {1: 50, 2: 25, 3: 25}.get(d.day) or (38 if d.day in (4, 5) else 37 if d.day in (6, 7) else
                                                36 if d.day <= 12 else 35 if d.day <= 14 else 30 if d.day <= 21 else 29)


def test_A12_one_endpoint_never_exceeds_60_requests_and_starts_are_2_5_s_apart_anchor_included(tmp_path):
    e = BfEnv(tmp_path, start_date="2000-01-01")
    s = e.world.eps["announcements"]
    s.earliest = date(2026, 10, 20)
    s.density = july_heavy
    e.probe_run(endpoints={"announcements": e.eps()["announcements"]})
    n = e.driver.n_fetch
    assert 30 <= n <= 60
    starts = e.driver.starts()
    assert all(b - a >= 2.5 - 1e-9 for a, b in zip(starts, starts[1:])), starts


def test_A12_pace_holds_across_an_endpoint_switch_and_a_second_process(e):
    e.probe_run(endpoints={k: v for k, v in e.eps().items() if k in ("announcements", "pit")})
    starts = e.driver.starts()
    assert len(e.driver.anchors) == 2
    assert all(b - a >= 2.5 - 1e-9 for a, b in zip(starts, starts[1:]))
    from investos.acquire.access import HostLimiter
    lim = HostLimiter(e.ledger_dir / ".state", wall_clock=e.wall, monotonic=e.mono, sleeper=e.sleeper)
    t0 = e.mono.t
    lim.acquire("www.nseindia.in", 2.5)                                           # a second limiter on the same state dir
    assert e.mono.t - t0 >= 0 and e.sleeper.sleeps


# ------------------------------------------------------------------ A13 artifacts (run_probe)
def three(e):
    return {k: v for k, v in e.eps().items() if k in ("announcements", "pit", "bulk_deals")}


def test_A13_run_probe_writes_the_report_and_probe_json_and_never_edits_the_table(e):
    before = e.table_path.read_bytes()
    res = e.probe_run(endpoints=three(e))
    assert res.status in ("complete", "stopped", "ok") or res.exit_code == 0
    assert e.table_path.read_bytes() == before
    report = e.cfg.zones["reports"] / "backfill" / "PROBE_2026-11-29.md"
    assert report.exists()
    pj = json.loads((e.ledger_dir / "backfill" / "probe.json").read_text())
    assert pj["schema"] == 1 and sorted(pj["endpoints"]) == ["announcements", "bulk_deals", "pit"]
    need = {"endpoint_id", "row_hash", "probed_at", "api_origin", "rows_path", "date_field", "date_format",
            "to_inclusive", "row_cap", "max_safe_rows", "max_window_days", "earliest_date", "time_of_day",
            "undercount", "mode", "status", "reasons"}
    for eid, ent in pj["endpoints"].items():
        assert need <= set(ent) and ent["endpoint_id"] == eid
        assert ent["row_hash"] == e.ep(eid).row_hash() and ent["status"] == "confirmed"
        assert ent["api_origin"] == IN


def test_A13_every_probe_body_is_in_the_manifest_and_named_in_the_report(e):
    e.probe_run(endpoints=three(e))
    entries = e.store.entries("backfill_probe")
    assert len(entries) == len(e.driver.fetched) and len(entries) > 60
    text = (e.cfg.zones["reports"] / "backfill" / "PROBE_2026-11-29.md").read_text()
    for en in entries:
        assert en.logical_key in text
        assert en.access_mode == "real_browser" and en.source_version == "backfill-v1"
        assert en.logical_key.count("/") == 2 and en.logical_key.split("/")[0] in ("announcements", "pit", "bulk_deals")


def test_A13_the_report_names_every_measurement(e):
    e.probe_run(endpoints=three(e))
    text = (e.cfg.zones["reports"] / "backfill" / "PROBE_2026-11-29.md").read_text()
    for w in ("api_origin", "rows_path", "date_field", "row_cap", "max_safe_rows", "max_window_days", "earliest_date",
              "time_of_day", "to_inclusive", "undercount", "announcements", "pit", "bulk_deals"):
        assert w in text, w


def test_A13_confirmed_only_when_every_check_passed(e):
    ep_of(e, "pit").mode = "html"
    e.probe_run(endpoints=three(e))
    pj = json.loads((e.ledger_dir / "backfill" / "probe.json").read_text())["endpoints"]
    assert pj["pit"]["status"] == "needs_review" and pj["pit"]["reasons"]
    assert pj["announcements"]["status"] == "confirmed"


def test_R5_load_probe_returns_the_stored_result_only_for_the_same_row_hash(e):
    from investos.backfill.probe import load_probe
    assert load_probe(e.cfg, e.ep("announcements")) is None
    e.probe_run(endpoints=three(e))
    r = load_probe(e.cfg, e.ep("announcements"))
    assert r is not None and r.status == "confirmed" and r.earliest_date >= date(2005, 1, 1)
    e.table_path.write_text(e.table_path.read_text().replace("2005-01-01", "2006-01-01", 1))
    assert load_probe(e.cfg, e.ep("announcements")) is None
    assert load_probe(e.cfg, e.ep("pit")) is not None


def test_R5_a_reprobe_replaces_the_endpoint_entry(e):
    e.probe_run(endpoints=three(e))
    e.world.eps["pit"].mode = "html"
    e.probe_run(endpoints={"pit": e.eps()["pit"]})
    pj = json.loads((e.ledger_dir / "backfill" / "probe.json").read_text())["endpoints"]
    assert pj["pit"]["status"] == "needs_review" and pj["announcements"]["status"] == "confirmed"


def test_R5_probe_never_edits_the_table_even_when_the_probe_disagrees_with_it(tmp_path):
    e = BfEnv(tmp_path, start_date="2005-01-01", row_cap="10", max_window_days="3")
    before = e.table_path.read_bytes()
    e.world.eps["announcements"].earliest = date(2011, 2, 1)
    e.probe_run(endpoints={"announcements": e.eps()["announcements"]})
    assert e.table_path.read_bytes() == before


# ------------------------------------------------------------------ Sagar's answer 2: the probe covers all eight
def test_S2_probe_covers_all_eight_endpoints(e):
    res = e.probe_run()
    pj = json.loads((e.ledger_dir / "backfill" / "probe.json").read_text())["endpoints"]
    assert sorted(pj) == sorted(e.eps())
    text = (e.cfg.zones["reports"] / "backfill" / "PROBE_2026-11-29.md").read_text()
    assert all(i in text for i in e.eps())
    assert res.requests == e.driver.n_requests() and res.requests > 8 * 20


def test_S2_an_endpoint_whose_probe_fails_does_not_stop_the_others(e):
    ep_of(e, "pit").mode = "html"
    e.probe_run()
    pj = json.loads((e.ledger_dir / "backfill" / "probe.json").read_text())["endpoints"]
    assert pj["pit"]["status"] == "needs_review" and pj["results"]["status"] == "confirmed"


# ------------------------------------------------------------------ R4: window, budget, blocks
def test_R4_probe_obeys_the_nightly_window_unless_outside_window(e):
    e.at(TUESDAY_NOON)
    res = e.probe_run()
    assert res.status == "window_closed" and e.driver.n_requests() == 0 and not e.driver.launches
    res = e.probe_run(outside_window=True)                                    # market hours: refused even with the flag
    assert res.exit_code == 1 and e.driver.n_requests() == 0
    e.at(ist(2026, 12, 1, 20, 0))
    assert e.probe_run(outside_window=True, endpoints=three(e)).exit_code == 0 and e.driver.n_requests() > 0


def test_R4_probe_requests_count_against_the_night_budget(e):
    e.set_bf(ramp=(30,))
    res = e.probe_run(endpoints=three(e))
    assert e.driver.n_requests() == 30 and res.status == "budget"
    assert e.counter("2026-11-29") == 30


def test_R4_a_block_during_the_probe_stops_the_whole_probe(e):
    e.driver.fetch_hook = lambda n, u: status_result(403, u) if n == 3 else None
    res = e.probe_run()
    assert res.status == "blocked" and res.exit_code == 3
    assert e.driver.n_fetch == 3
    assert len(e.notifier.calls) == 1 and "blocked" in e.notifier.calls[0][0].lower()
    assert e.host_events("www.nseindia.in") and e.host_events("www.nseindia.com")
