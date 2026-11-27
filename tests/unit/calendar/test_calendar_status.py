"""S1-T4 A1-A8, A22, A23: day status (R1-R5) and strict loading (R2)."""
from __future__ import annotations

import re

import pytest

from _cal_helpers import D, srow, yrow, write_csv, write_raw, SESSIONS_HEADER, YEARS_HEADER

MON = D(2026, 11, 16)      # Monday
WED = D(2026, 11, 18)
SAT = D(2026, 12, 12)
SUN = D(2026, 12, 13)


def _mod():
    from investos.calendar import trading
    return trading


def _status(cal, d, **kw):
    return cal.day_status(d, **kw)


# ---- A1 / R1 ----------------------------------------------------------------
def test_A1_weekday_without_evidence_is_provisional_not_trading(tables):
    cal = tables.calendar()
    st = _status(cal, MON)
    assert st == _mod().DayStatus.PROVISIONAL
    assert st != _mod().DayStatus.TRADING
    assert cal.is_trading_day(MON) is False


def test_A1_market_close_raises_unless_allow_provisional(tables):
    cal = tables.calendar()
    with pytest.raises(_mod().NotATradingDay):
        cal.market_close_ist(MON, "CM", "cat1")
    with pytest.raises(_mod().NotATradingDay):
        cal.after_close_public_at(MON, "CM", "cat1")
    got = cal.market_close_ist(MON, "CM", "cat1", allow_provisional=True)
    assert got.date() == MON


def test_A1_market_close_provisional_value_is_the_era_value(tables):
    cal = tables.calendar()
    got = cal.market_close_ist(MON, "CM", "cat1", allow_provisional=True)
    assert (got.hour, got.minute) == (15, 35)
    pub = cal.after_close_public_at(MON, "CM", "cat1", allow_provisional=True)
    assert (pub.hour, pub.minute) == (16, 5)


def test_A1_every_weekday_of_a_loaded_year_is_never_trading(tables):
    cal = tables.calendar()
    d = D(2026, 1, 1)
    seen = set()
    while d.year == 2026:
        seen.add(cal.day_status(d))
        d = D.fromordinal(d.toordinal() + 1)
    assert _mod().DayStatus.TRADING not in seen
    assert seen == {_mod().DayStatus.PROVISIONAL, _mod().DayStatus.CLOSED}


def test_A1_is_trading_day_true_only_for_trading(tables):
    tables.set_sessions([srow(MON, "trading", evidence_kind="bhavcopy", evidence_ref="nse_cm:2026-11-16")])
    cal = tables.calendar()
    assert cal.is_trading_day(MON) is True
    assert cal.is_trading_day(WED) is False
    assert cal.is_trading_day(SAT) is False


# ---- A2 / R3 ----------------------------------------------------------------
@pytest.mark.parametrize("kind", ["bhavcopy", "circular", "manual"])
def test_A2_trading_row_with_evidence_is_trading(tables, kind):
    ref = "nse_cm:2026-11-16" if kind == "bhavcopy" else "ref-1"
    tables.set_sessions([srow(MON, "trading", evidence_kind=kind, evidence_ref=ref)])
    cal = tables.calendar()
    assert _status(cal, MON) == _mod().DayStatus.TRADING
    assert cal.is_trading_day(MON) is True


# ---- A3 / R3 ----------------------------------------------------------------
def test_A3_holiday_row_on_wednesday_is_closed(tables):
    tables.set_sessions([srow(WED, "holiday", evidence_kind="holiday_list")])
    cal = tables.calendar()
    assert _status(cal, WED) == _mod().DayStatus.CLOSED
    assert cal.is_trading_day(WED) is False
    with pytest.raises(_mod().NotATradingDay):
        cal.market_close_ist(WED, "CM", "cat1")
    with pytest.raises(_mod().NotATradingDay):
        cal.market_close_ist(WED, "CM", "cat1", allow_provisional=True)


def test_A3_weekend_in_loaded_year_is_closed(tables):
    cal = tables.calendar()
    assert _status(cal, SAT) == _mod().DayStatus.CLOSED
    assert _status(cal, SUN) == _mod().DayStatus.CLOSED
    with pytest.raises(_mod().NotATradingDay):
        cal.market_close_ist(SAT, "CM", "cat1", allow_provisional=True)


def test_A3_saturday_special_session_is_trading(tables):
    tables.set_sessions([srow(SAT, "special_session", open_ist="09:15", close_ist="10:15",
                              evidence_kind="circular", evidence_ref="NSE/CIR/1")])
    cal = tables.calendar()
    assert _status(cal, SAT) == _mod().DayStatus.TRADING
    assert _status(cal, SUN) == _mod().DayStatus.CLOSED


def test_A3_holiday_row_on_weekend_is_allowed_and_closed(tables):
    tables.set_sessions([srow(SAT, "holiday", evidence_kind="holiday_list")])
    assert _status(tables.calendar(), SAT) == _mod().DayStatus.CLOSED


# ---- A4 / R5 ----------------------------------------------------------------
def test_A4_year_not_loaded_is_unknown(tables):
    cal = tables.calendar()
    wd, sat = D(2028, 3, 15), D(2028, 3, 18)
    assert _status(cal, wd) == _mod().DayStatus.UNKNOWN
    assert _status(cal, sat) == _mod().DayStatus.UNKNOWN
    assert cal.is_trading_day(wd) is False
    for d in (wd, sat):
        with pytest.raises(_mod().NotATradingDay):
            cal.market_close_ist(d, "CM", "cat1")
        with pytest.raises(_mod().NotATradingDay):          # UNKNOWN raises even with allow_provisional
            cal.market_close_ist(d, "CM", "cat1", allow_provisional=True)


def test_A4_loading_a_year_does_not_create_trading_days(tables):
    tables.set_years([yrow(2026), yrow(2027)])
    cal = tables.calendar()
    assert _status(cal, D(2027, 3, 1)) == _mod().DayStatus.PROVISIONAL
    assert cal.is_trading_day(D(2027, 3, 1)) is False


def test_A4_year_boundaries_resolve_by_calendar_year(tables):
    cal = tables.calendar()
    assert _status(cal, D(2026, 12, 31)) == _mod().DayStatus.PROVISIONAL   # Thursday
    assert _status(cal, D(2027, 1, 1)) == _mod().DayStatus.UNKNOWN         # 2027 not loaded
    tables.set_years([yrow(2027)])                                          # NSE only 2027, 2026 now missing
    cal2 = tables.calendar()
    assert _status(cal2, D(2026, 12, 31)) == _mod().DayStatus.UNKNOWN
    assert _status(cal2, D(2027, 1, 1)) == _mod().DayStatus.PROVISIONAL


def test_A4_leap_day(tables):
    tables.set_years([yrow(2024)])
    assert _status(tables.calendar(), D(2024, 2, 29)) == _mod().DayStatus.PROVISIONAL  # Thursday


def test_A4_row_in_unloaded_year_still_confirms_the_day(tables):
    d = D(2028, 3, 15)
    tables.set_sessions([srow(d, "trading", evidence_kind="circular", evidence_ref="c1")])
    assert _status(tables.calendar(), d) == _mod().DayStatus.TRADING


# ---- A5 / R3 ----------------------------------------------------------------
def test_A5_trading_row_with_holiday_list_evidence_rejected(tables):
    tables.set_sessions([srow(MON, "trading", evidence_kind="holiday_list")])
    with pytest.raises(_mod().CalendarError):
        tables.calendar()


def test_A5_special_session_with_holiday_list_evidence_is_allowed(tables):
    tables.set_sessions([srow(SAT, "special_session", open_ist="09:15", close_ist="10:15",
                              evidence_kind="holiday_list", evidence_ref="list")])
    assert _status(tables.calendar(), SAT) == _mod().DayStatus.TRADING


# ---- A6 / R4 ----------------------------------------------------------------
def test_A6_holiday_and_trading_same_date_and_segment_rejected_naming_date(tables):
    tables.set_sessions([srow(MON, "holiday", evidence_kind="holiday_list"),
                         srow(MON, "trading", evidence_kind="bhavcopy", evidence_ref="nse_cm:2026-11-16")])
    with pytest.raises(_mod().CalendarError) as ei:
        tables.calendar()
    assert "2026-11-16" in str(ei.value)


def test_A6_holiday_cm_and_special_session_fo_is_legal(tables):
    tables.set_sessions([srow(MON, "holiday", segment="CM", evidence_kind="holiday_list"),
                         srow(MON, "special_session", segment="FO", open_ist="17:00", close_ist="18:00",
                              evidence_kind="circular", evidence_ref="c9")])
    cal = tables.calendar()
    assert cal.day_status(MON, segment="CM") == _mod().DayStatus.CLOSED
    assert cal.day_status(MON, segment="FO") == _mod().DayStatus.TRADING


def test_A6_FO_stays_provisional_when_only_CM_holiday(tables):
    tables.set_sessions([srow(MON, "holiday", segment="CM", evidence_kind="holiday_list")])
    cal = tables.calendar()
    assert cal.day_status(MON, segment="CM") == _mod().DayStatus.CLOSED
    assert cal.day_status(MON, segment="FO") == _mod().DayStatus.PROVISIONAL


# ---- A7 / R3 ----------------------------------------------------------------
def test_A7_specific_row_wins_over_ALL(tables):
    tables.set_sessions([srow(MON, "holiday", segment="ALL", evidence_kind="holiday_list"),
                         srow(MON, "special_session", segment="FO", open_ist="17:00", close_ist="18:00",
                              evidence_kind="circular", evidence_ref="c9")])
    cal = tables.calendar()
    assert cal.day_status(MON, segment="FO") == _mod().DayStatus.TRADING
    assert cal.day_status(MON, segment="CM") == _mod().DayStatus.CLOSED


def test_A7_ALL_row_applies_to_both_segments(tables):
    tables.set_sessions([srow(WED, "holiday", segment="ALL", evidence_kind="holiday_list"),
                         srow(MON, "trading", segment="ALL")])
    cal = tables.calendar()
    for seg in ("CM", "FO"):
        assert cal.day_status(WED, segment=seg) == _mod().DayStatus.CLOSED
        assert cal.day_status(MON, segment=seg) == _mod().DayStatus.TRADING


# ---- A22 --------------------------------------------------------------------
def test_A22_nse_and_bse_are_independent(tables):
    tables.set_sessions([srow(MON, "trading", exchange="NSE"),
                         srow(MON, "holiday", exchange="BSE", evidence_kind="holiday_list")])
    cal = tables.calendar()
    assert cal.day_status(MON, exchange="NSE") == _mod().DayStatus.TRADING
    assert cal.day_status(MON, exchange="BSE") == _mod().DayStatus.CLOSED
    assert cal.is_trading_day(MON, exchange="NSE") is True
    assert cal.is_trading_day(MON, exchange="BSE") is False


def test_A22_years_are_per_exchange(tables):
    tables.set_years([yrow(2026, "NSE")])
    cal = tables.calendar()
    assert cal.day_status(MON, exchange="NSE") == _mod().DayStatus.PROVISIONAL
    assert cal.day_status(MON, exchange="BSE") == _mod().DayStatus.UNKNOWN


# ---- A23 --------------------------------------------------------------------
def test_A23_saturday_cm_special_session_leaves_FO_closed(tables):
    tables.set_sessions([srow(SAT, "special_session", segment="CM", open_ist="09:15", close_ist="10:15",
                              evidence_kind="circular", evidence_ref="c3")])
    cal = tables.calendar()
    assert cal.day_status(SAT, segment="CM") == _mod().DayStatus.TRADING
    assert cal.day_status(SAT, segment="FO") == _mod().DayStatus.CLOSED
    assert cal.market_close_ist(SAT, "CM", "cat1").hour == 10
    with pytest.raises(_mod().NotATradingDay):
        cal.market_close_ist(SAT, "FO", "stock_fo")


# ---- A8 / R2: strict loading ------------------------------------------------
GOOD = srow(MON, "trading")


def _line(row):
    return ",".join(str(row.get(h, "")) for h in SESSIONS_HEADER)


def _sessions_text(*lines, header=None):
    return (header or ",".join(SESSIONS_HEADER)) + "\n" + "\n".join(lines) + "\n"


def _err(tables, text, which="sessions"):
    path = tables.sessions_path if which == "sessions" else tables.years_path
    write_raw(path, text)
    with pytest.raises(_mod().CalendarError) as ei:
        tables.calendar()
    return str(ei.value), path.name


def test_A8_header_typo_names_file(tables):
    text = _sessions_text(_line(GOOD), header=",".join(SESSIONS_HEADER).replace("evidence_kind", "evidence_kynd"))
    msg, name = _err(tables, text)
    assert name in msg


def test_A8_status_case_names_file_line_field(tables):
    bad = dict(GOOD, status="Holiday", evidence_kind="holiday_list")
    msg, name = _err(tables, _sessions_text(_line(bad)))
    assert name in msg and "status" in msg and re.search(r"\b2\b", msg)


def test_A8_malformed_date_names_file_line_field(tables):
    bad = dict(GOOD, date="2026-13-01")
    msg, name = _err(tables, _sessions_text(_line(GOOD), _line(dict(bad, segment="FO"))))
    assert name in msg and "date" in msg and re.search(r"\b3\b", msg)


def test_A8_duplicate_key_names_file_and_line(tables):
    dup = dict(GOOD, evidence_ref="again")
    msg, name = _err(tables, _sessions_text(_line(GOOD), _line(dup)))
    assert name in msg and re.search(r"\b3\b", msg)


def test_A8_special_session_empty_close_rejected(tables):
    bad = srow(SAT, "special_session", open_ist="09:15", close_ist="", evidence_kind="circular")
    msg, name = _err(tables, _sessions_text(_line(bad)))
    assert name in msg and "close_ist" in msg


def test_A8_special_session_empty_open_rejected(tables):
    bad = srow(SAT, "special_session", open_ist="", close_ist="10:15", evidence_kind="circular")
    msg, name = _err(tables, _sessions_text(_line(bad)))
    assert name in msg and "open_ist" in msg


@pytest.mark.parametrize("o,c", [("10:15", "09:15"), ("09:15", "09:15")])
def test_A8_special_session_close_not_after_open_rejected(tables, o, c):
    bad = srow(SAT, "special_session", open_ist=o, close_ist=c, evidence_kind="circular")
    msg, name = _err(tables, _sessions_text(_line(bad)))
    assert name in msg


@pytest.mark.parametrize("field,value", [
    ("exchange", "NYSE"), ("segment", "EQ"), ("evidence_kind", "rumour"),
    ("recorded_at", "20-11-2026"), ("recorded_at", ""), ("evidence_ref", ""),
    ("date", "16/11/2026"), ("date", ""),
])
def test_R2_bad_sessions_values_rejected(tables, field, value):
    msg, name = _err(tables, _sessions_text(_line(dict(GOOD, **{field: value}))))
    assert name in msg and field in msg


@pytest.mark.parametrize("o,c", [("9:15", "10:15"), ("25:00", "26:00"), ("09:15:00", "10:15"), ("ab", "cd")])
def test_R2_malformed_time_rejected(tables, o, c):
    bad = srow(SAT, "special_session", open_ist=o, close_ist=c, evidence_kind="circular")
    _err(tables, _sessions_text(_line(bad)))


@pytest.mark.parametrize("status", ["holiday", "trading"])
def test_R2_times_must_be_empty_for_non_special(tables, status):
    kind = "holiday_list" if status == "holiday" else "manual"
    bad = srow(WED, status, open_ist="09:15", close_ist="15:30", evidence_kind=kind)
    _err(tables, _sessions_text(_line(bad)))


def test_R2_header_only_sessions_is_valid(tables):
    write_raw(tables.sessions_path, ",".join(SESSIONS_HEADER) + "\n")
    assert tables.calendar().day_status(MON) == _mod().DayStatus.PROVISIONAL


def test_R2_years_duplicate_and_bad_values(tables):
    text = ",".join(YEARS_HEADER) + "\nNSE,2026,list,2026-01-02\nNSE,2026,list2,2026-02-01\n"
    msg, name = _err(tables, text, "years")
    assert name in msg


@pytest.mark.parametrize("line", ["NYSE,2026,list,2026-01-02", "NSE,twenty,list,2026-01-02",
                                  "NSE,2026,,2026-01-02", "NSE,2026,list,2026-02-30", "NSE,2026,list,"])
def test_R2_years_bad_rows_rejected(tables, line):
    _err(tables, ",".join(YEARS_HEADER) + "\n" + line + "\n", "years")


def test_R2_years_header_typo(tables):
    msg, name = _err(tables, "exchange,yr,list_source,loaded_at\nNSE,2026,l,2026-01-02\n", "years")
    assert name in msg


def test_R2_error_is_CalendarError_and_exception():
    m = _mod()
    assert issubclass(m.CalendarError, Exception)
    assert issubclass(m.NotATradingDay, m.CalendarError)
    assert issubclass(m.DayStatus, str)
    assert {s.value for s in m.DayStatus} == {"trading", "closed", "provisional", "unknown"}


def test_R2_unknown_exchange_or_segment_argument_is_ValueError(tables):
    cal = tables.calendar()
    with pytest.raises(ValueError):
        cal.day_status(MON, exchange="NYSE")
    with pytest.raises(ValueError):
        cal.day_status(MON, segment="EQ")
