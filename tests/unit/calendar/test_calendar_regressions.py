"""S1-T4 review regressions D2-D4 (one test per surviving mutation M06, M26, M35, M42, M46, M47)."""
from __future__ import annotations

import pytest

from _cal_helpers import D, srow, read_csv, write_csv, find_era
from test_calendar_check import run_check, _warnings  # noqa: F401  (fixture reuse)

MON = D(2026, 11, 16)


def _mod():
    from investos.calendar import trading
    return trading


# ---- D2 / M06: amended R4 -----------------------------------------------------
@pytest.mark.parametrize("hseg,tseg", [("ALL", "CM"), ("ALL", "FO"), ("CM", "ALL"), ("FO", "ALL")])
def test_D2_M06_holiday_vs_trading_on_overlapping_segment_rejected(tables, hseg, tseg):
    tables.set_sessions([srow(MON, "holiday", segment=hseg, evidence_kind="holiday_list"),
                         srow(MON, "trading", segment=tseg, evidence_kind="bhavcopy", evidence_ref="s:k")])
    with pytest.raises(_mod().CalendarError) as ei:
        tables.calendar()
    assert "2026-11-16" in str(ei.value)


def test_D2_M06_holiday_and_trading_on_different_segments_is_legal(tables):
    tables.set_sessions([srow(MON, "holiday", segment="CM", evidence_kind="holiday_list"),
                         srow(MON, "trading", segment="FO")])
    cal = tables.calendar()
    assert cal.day_status(MON, segment="CM") == _mod().DayStatus.CLOSED
    assert cal.day_status(MON, segment="FO") == _mod().DayStatus.TRADING


def test_D2_M06_ALL_holiday_with_special_session_stays_legal(tables):
    tables.set_sessions([srow(MON, "holiday", segment="ALL", evidence_kind="holiday_list"),
                         srow(MON, "special_session", segment="CM", open_ist="18:00", close_ist="19:00",
                              evidence_kind="circular", evidence_ref="muhurat")])
    assert tables.calendar().day_status(MON, segment="CM") == _mod().DayStatus.TRADING


# ---- D2 / M42: market_close >= cas_end -----------------------------------------
@pytest.mark.parametrize("era,mc", [("E3-CM-cat1", "15:39"), ("E2-CM-cat1", "15:34")])
def test_D2_M42_market_close_before_cas_end_rejected_naming_era(era_tables, era, mc):
    # earliest <= market_close and continuous_end <= market_close hold, so only the cas_end rule can fire
    era_tables.set_era(era, market_close=mc, close_earliest="15:30")
    with pytest.raises(_mod().CalendarError) as ei:
        era_tables.calendar()
    assert era in str(ei.value)


def test_D2_M42_market_close_equal_to_cas_end_is_fine(era_tables):
    era_tables.set_era("E3-CM-cat1", market_close="15:40", cas_end="15:40")
    era_tables.calendar()


# ---- D3 / M26: the unverified WARNING must name the unverified reason ----------
def test_D3_M26_unverified_warning_has_its_own_text(run_check, era_tables):
    _h, rows = read_csv(era_tables.eras_path)
    unver = [r["era_id"] for r in rows if r["cas_applicable"] == "unverified"]
    assert len(unver) >= 4                                   # E3, E4 non_fo, for NSE and BSE
    code, out = run_check(t=era_tables)
    assert code == 0
    lines = [ln for ln in _warnings(out) if "cas_applicable unverified" in ln]
    assert len(lines) == len(unver)
    for i in unver:
        assert any(i in ln for ln in lines), i


def test_D3_M26_no_unverified_rows_no_unverified_warning(run_check, era_tables):
    def f(h, rows):
        for r in rows:
            if r["cas_applicable"] == "unverified":
                r["cas_applicable"] = "yes"
    era_tables.edit_eras(f)
    code, out = run_check(t=era_tables)
    assert code == 0
    assert not [ln for ln in _warnings(out) if "cas_applicable unverified" in ln]


# ---- D4 / M35: the newest era row must be open-ended --------------------------
# NOTE spec R8 says "only the last has an empty effective_to" but not that the last must be open;
# the Builder rejects a closed final row (a closed final row would leave dates uncovered). Spec needs a line.
def test_D4_M35_newest_era_row_with_effective_to_rejected(era_tables):
    era_tables.set_era("E4-CM-cat1", effective_to="2030-12-31")
    with pytest.raises(_mod().CalendarError) as ei:
        era_tables.calendar()
    assert "E4-CM-cat1" in str(ei.value)


# ---- D4 / M46: times must be empty on non-special rows ------------------------
@pytest.mark.parametrize("status,kind", [("trading", "manual"), ("holiday", "holiday_list")])
@pytest.mark.parametrize("o,c", [("09:15", ""), ("", "15:30"), ("09:15", "15:30")])
def test_D4_M46_times_on_non_special_row_rejected(tables, status, kind, o, c):
    tables.set_sessions([srow(MON, status, open_ist=o, close_ist=c, evidence_kind=kind)])
    with pytest.raises(_mod().CalendarError) as ei:
        tables.calendar()
    assert "sessions.csv" in str(ei.value)


# ---- D4 / M47: unsorted era rows are sorted before the contiguity check -------
def test_D4_M47_unsorted_era_rows_still_load_and_resolve(era_tables):
    h, rows = read_csv(era_tables.eras_path)
    rows.reverse()
    write_csv(era_tables.eras_path, h, rows)
    cal = era_tables.calendar()
    assert cal.era_for(D(2026, 8, 2), "CM", "cat1").era_id == "E1-CM-cat1"
    assert cal.era_for(D(2026, 12, 7), "CM", "cat1").era_id == "E3-CM-cat1"
    assert cal.era_for(D(2027, 3, 1), "CM", "cat1").era_id == "E4-CM-cat1"


def test_D4_M47_unsorted_rows_with_a_gap_still_rejected(era_tables):
    h, rows = read_csv(era_tables.eras_path)
    find_era(rows, "E2-CM-cat1")["effective_to"] = "2026-12-05"
    rows.reverse()
    write_csv(era_tables.eras_path, h, rows)
    with pytest.raises(_mod().CalendarError):
        era_tables.calendar()
