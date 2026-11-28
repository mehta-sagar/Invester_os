"""S1-T8: window planner and adaptive split (R6, R7, A14, A15 tiling)."""
from __future__ import annotations

from datetime import timedelta

import pytest

from _backfill_helpers import BfEnv, day, make_probe


@pytest.fixture
def e(tmp_path):
    return BfEnv(tmp_path, start_date="2016-01-01")


def plan(e, eid="announcements", end="2016-04-10", probe_over=None, ep_over=None, **kw):
    from investos.backfill.planner import plan_windows
    probe_over = {"max_window_days": 7, "earliest_date": "2016-03-01", **(probe_over or {})}
    return plan_windows(e.ep(eid), make_probe(e.ep(eid), **probe_over), day(end), **kw)


def tiles(ws):
    return all(a.end + timedelta(days=1) == b.start for a, b in zip(ws, ws[1:]))


# ------------------------------------------------------------------ A14
def test_A14_windows_are_disjoint_ordered_gap_free_and_cover_up_to_the_last_completed_day(e):
    ws = plan(e)
    assert ws[0].start == day("2016-03-01") and ws[-1].end == day("2016-04-09")
    assert tiles(ws) and all(w.days <= 7 for w in ws)
    assert [w.start for w in ws] == sorted(w.start for w in ws)
    assert all(w.endpoint_id == "announcements" and w.part == "" for w in ws)
    assert all(w.end < day("2016-04-10") for w in ws)                         # today is never inside a window
    assert sum(w.days for w in ws) == 40


def test_A14_start_is_the_later_of_table_start_and_probe_earliest(e):
    ws = plan(e, probe_over={"earliest_date": "2015-01-01"})                  # table start 2016-01-01 wins
    assert ws[0].start == day("2016-01-01")
    ws = plan(e, probe_over={"earliest_date": "2016-03-20"})
    assert ws[0].start == day("2016-03-20")


def test_A14_nothing_to_plan_when_start_is_today_or_later(e):
    assert plan(e, end="2016-03-01") == []                                     # last completed day is Feb 29
    one = plan(e, end="2016-03-02")
    assert len(one) == 1 and one[0].days == 1 and one[0].start == day("2016-03-01")


def test_A14_dense_endpoint_gets_narrower_windows_so_expected_rows_stay_under_80_percent_of_cap(e):
    ws = plan(e, probe_over={"row_cap": 500, "rows_per_day": 800 / 7})          # 800 rows a week, cap 500
    assert tiles(ws) and ws[-1].end == day("2016-04-09")
    assert max(w.days for w in ws) * (800 / 7) <= 0.8 * 500 + 1e-9
    assert max(w.days for w in ws) >= 2                                         # not needlessly one-day


def test_A14_cap_none_uses_max_window_days(e):
    ws = plan(e, probe_over={"row_cap": None, "rows_per_day": None, "max_window_days": 5})
    assert max(w.days for w in ws) == 5 and ws[0].days == 5


def test_A14_one_day_windows_when_max_window_is_one(e):
    ws = plan(e, probe_over={"max_window_days": 1})
    assert len(ws) == 40 and all(w.days == 1 for w in ws)


def test_A14_bulk_deals_skips_calendar_closed_days_and_keeps_unknown_days(e):
    class Cal:
        def day_status(self, d):
            return "closed" if d.weekday() >= 5 or d == day("2016-03-08") else ("unknown" if d == day("2016-03-10") else "trading")
    ws = plan(e, eid="bulk_deals", calendar=Cal(), probe_over={"max_window_days": 7})
    covered = {w.start + timedelta(days=i) for w in ws for i in range(w.days)}
    assert day("2016-03-05") not in covered and day("2016-03-06") not in covered and day("2016-03-08") not in covered
    assert day("2016-03-10") in covered and day("2016-03-09") in covered
    every = {day("2016-03-01") + timedelta(days=i) for i in range(40)}
    assert covered == {d for d in every if d.weekday() < 5 and d != day("2016-03-08")}
    for w in ws:                                                               # no window spans a closed day
        assert all(Cal().day_status(w.start + timedelta(days=i)) != "closed" for i in range(w.days))


def test_A14_replan_keeps_the_borders_of_done_windows_and_plans_only_uncovered_dates(e):
    first = plan(e)
    done = first[:2]
    again = plan(e, probe_over={"max_window_days": 5}, done=done)
    assert again[:2] == done
    assert tiles(again) and again[0].start == day("2016-03-01") and again[-1].end == day("2016-04-09")
    assert all(w.days <= 5 for w in again[2:])
    assert again[2].start == done[-1].end + timedelta(days=1)


def test_A14_replan_with_a_done_window_in_the_middle(e):
    first = plan(e)
    again = plan(e, probe_over={"max_window_days": 3}, done=[first[3]])
    assert first[3] in again and tiles(again)
    assert sum(w.days for w in again) == 40


def test_A14_plan_is_deterministic(e):
    assert plan(e) == plan(e)


def test_R6_write_plan_and_read_plan_roundtrip_and_same_file_on_replan(e):
    from investos.backfill.planner import read_plan, write_plan
    probe = make_probe(e.ep("announcements"), max_window_days=7, earliest_date="2016-03-01")
    ws = write_plan(e.cfg, e.ep("announcements"), probe, day("2016-04-10"))
    p = e.ledger_dir / "backfill" / "plan" / "announcements.json"
    assert p.exists() and read_plan(e.cfg, "announcements") == ws
    before = p.read_bytes()
    write_plan(e.cfg, e.ep("announcements"), probe, day("2016-04-10"))
    assert p.read_bytes() == before
    assert read_plan(e.cfg, "pit") is None


# ------------------------------------------------------------------ R7 / A15 halves
def test_A15_split_halves_tile_the_parent_for_odd_and_even_day_counts():
    from investos.backfill.ledger import WindowKey
    from investos.backfill.planner import split
    for n in (2, 3, 4, 5, 7, 8, 31):
        k = WindowKey("announcements", day("2026-03-01"), day("2026-03-01") + timedelta(days=n - 1))
        a, b = split(k)
        assert a.start == k.start and b.end == k.end and a.end + timedelta(days=1) == b.start
        assert a.days == (n + 1) // 2 and b.days == n // 2 and a.days >= b.days
        assert (a.part, b.part) == ("a", "b") and a.endpoint_id == "announcements"


def test_A15_seven_days_split_four_plus_three():
    from investos.backfill.ledger import WindowKey
    from investos.backfill.planner import split
    a, b = split(WindowKey("announcements", day("2026-03-01"), day("2026-03-07")))
    assert (a.days, b.days) == (4, 3)


def test_R7_part_suffixes_nest():
    from investos.backfill.ledger import WindowKey
    from investos.backfill.planner import split
    a, b = split(WindowKey("announcements", day("2026-03-01"), day("2026-03-08")))
    aa, ab = split(a)
    assert (aa.part, ab.part) == ("aa", "ab") and aa.logical_key().endswith("/aa")
    ba, bb = split(b)
    assert (ba.part, bb.part) == ("ba", "bb")


def test_R7_a_one_day_window_cannot_be_split():
    from investos.backfill.ledger import WindowKey
    from investos.backfill.planner import split
    with pytest.raises(ValueError):
        split(WindowKey("announcements", day("2026-03-01"), day("2026-03-01")))
