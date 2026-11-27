"""S1-T4 A9-A17: closing-era table, market_close_ist, after_close_public_at, known_at, price fields."""
from __future__ import annotations

from datetime import datetime, time, timedelta, timezone

import pytest

from _cal_helpers import D, srow, confirm_rows, ist, as_window, find_era, read_csv, write_csv, ERAS_HEADER
from conftest import ERA_DATES


def _mod():
    from investos.calendar import trading
    return trading


E1, E2A, E2B = D(2026, 8, 2), D(2026, 8, 3), D(2026, 12, 6)
E3A, E4A, E3Z = D(2026, 12, 7), D(2027, 2, 8), D(2027, 2, 7)

# (date, segment, category, close HH, MM)  -- spec A9/A10 plus the E4 and boundary dates
CLOSES = [
    (E1, "CM", "cat1", 15, 30), (E1, "CM", "non_fo", 15, 30),
    (E2A, "CM", "cat1", 15, 35), (E2A, "CM", "non_fo", 15, 30),
    (E2B, "CM", "cat1", 15, 35), (E2B, "CM", "non_fo", 15, 30),
    (E3A, "CM", "cat1", 15, 40), (E3A, "CM", "non_fo", 15, 40),
    (E3Z, "CM", "cat1", 15, 40), (E3Z, "CM", "non_fo", 15, 40),
    (E4A, "CM", "cat1", 15, 40), (E4A, "CM", "non_fo", 15, 40),
    (E1, "FO", "stock_fo", 15, 30), (E1, "FO", "index_fo", 15, 30), (E1, "FO", "index_fo_expiring", 15, 30),
    (E2A, "FO", "stock_fo", 15, 40), (E2A, "FO", "index_fo", 15, 40), (E2A, "FO", "index_fo_expiring", 15, 40),
    (E2B, "FO", "stock_fo", 15, 40), (E2B, "FO", "index_fo", 15, 40), (E2B, "FO", "index_fo_expiring", 15, 40),
    (E3A, "FO", "stock_fo", 15, 45), (E3A, "FO", "index_fo", 15, 45), (E3A, "FO", "index_fo_expiring", 15, 30),
    (E3Z, "FO", "stock_fo", 15, 45), (E3Z, "FO", "index_fo", 15, 45), (E3Z, "FO", "index_fo_expiring", 15, 30),
    (E4A, "FO", "stock_fo", 15, 45), (E4A, "FO", "index_fo", 15, 45), (E4A, "FO", "index_fo_expiring", 15, 30),
]


def _id(p):
    return f"{p[0]}-{p[1]}-{p[2]}"


# ---- A9 / A10 ---------------------------------------------------------------
@pytest.mark.parametrize("exchange", ["NSE", "BSE"])
@pytest.mark.parametrize("d,seg,cat,h,m", CLOSES, ids=[_id(p) for p in CLOSES])
def test_A9_A10_market_close_ist(cal, exchange, d, seg, cat, h, m):
    got = cal.market_close_ist(d, seg, cat, exchange=exchange)
    assert got == ist(d.year, d.month, d.day, h, m)
    assert got.tzinfo is not None
    assert getattr(got.tzinfo, "key", None) == "Asia/Kolkata"
    assert got.utcoffset() == timedelta(hours=5, minutes=30)


def test_A9_default_exchange_is_NSE(cal):
    assert cal.market_close_ist(E3A, "CM", "cat1") == cal.market_close_ist(E3A, "CM", "cat1", exchange="NSE")


def test_A9_era_chosen_by_trade_date_not_fetch_time(cal):
    # same moment of fetch is irrelevant: last day of E1 vs first day of E2 differ for cat1
    assert cal.market_close_ist(E1, "CM", "cat1").time() == time(15, 30)
    assert cal.market_close_ist(E2A, "CM", "cat1").time() == time(15, 35)
    assert cal.market_close_ist(E2B, "CM", "cat1").time() == time(15, 35)
    assert cal.market_close_ist(E3A, "CM", "cat1").time() == time(15, 40)


@pytest.mark.parametrize("d,era", [
    (D(2010, 1, 1), "E1"), (E1, "E1"), (E2A, "E2"), (E2B, "E2"), (E3A, "E3"), (E3Z, "E3"), (E4A, "E4"),
    (D(2030, 1, 1), "E4"),
])
@pytest.mark.parametrize("seg,cat", [("CM", "cat1"), ("CM", "non_fo"), ("FO", "index_fo"),
                                     ("FO", "index_fo_expiring"), ("FO", "stock_fo")])
def test_A9_era_for_picks_row_by_date(cal, d, era, seg, cat):
    row = cal.era_for(d, seg, cat)
    assert row.era_id.startswith(era + "-")
    assert row.effective_from <= d and (row.effective_to is None or d <= row.effective_to)
    assert row.era_id == f"{era}-{seg}-{cat}"


def test_A9_era_for_does_not_need_a_confirmed_day(cal):
    assert cal.era_for(D(2026, 11, 14), "CM", "cat1").era_id == "E2-CM-cat1"   # a Saturday


def test_A9_era_row_fields_parsed(cal):
    r = cal.era_for(E3A, "CM", "cat1")
    assert r.effective_from == E3A and r.effective_to == E3Z
    assert r.exchange == "NSE" and r.segment == "CM" and r.category == "cat1"
    assert r.continuous_end == time(15, 30)
    assert (r.cas_start, r.cas_end) == (time(15, 30), time(15, 40))
    assert r.market_close == time(15, 40) and r.close_earliest == time(15, 38)
    assert (r.post_close_start, r.post_close_end) == (time(15, 50), time(16, 0))
    assert r.close_method == "closing_auction" and r.cas_applicable == "yes"
    assert r.public_lag_min == 30 and r.verified == "press"
    assert cal.era_for(E4A, "CM", "cat1").effective_to is None


def test_A9_era_for_bse_row_is_assumed(cal):
    r = cal.era_for(E3A, "CM", "cat1", exchange="BSE")
    assert r.exchange == "BSE" and r.verified == "assumed"


def test_A9_E3_CM_non_fo_unverified_uses_latest_close(cal):
    r = cal.era_for(E3A, "CM", "non_fo")
    assert r.cas_applicable == "unverified"
    assert cal.market_close_ist(E3A, "CM", "non_fo").time() == time(15, 40)


# ---- A10 ordering: continuous trading ends ---------------------------------
@pytest.mark.parametrize("d,seg,cat,end", [
    (E1, "CM", "cat1", time(15, 30)), (E2A, "CM", "cat1", time(15, 15)), (E2A, "CM", "non_fo", time(15, 30)),
    (E3A, "CM", "cat1", time(15, 30)), (E3A, "CM", "non_fo", time(15, 30)),
    (E2A, "FO", "stock_fo", time(15, 40)), (E3A, "FO", "stock_fo", time(15, 45)),
    (E3A, "FO", "index_fo_expiring", time(15, 30)),
])
def test_A10_continuous_end(cal, d, seg, cat, end):
    assert cal.era_for(d, seg, cat).continuous_end == end


@pytest.mark.parametrize("d,cas", [(E2A, (time(15, 20), time(15, 35))), (E3A, (time(15, 30), time(15, 40)))])
def test_A9_cas_windows(cal, d, cas):
    r = cal.era_for(d, "CM", "cat1")
    assert (r.cas_start, r.cas_end) == cas


def test_A9_no_cas_in_E1_and_non_fo_E2(cal):
    for d, cat in [(E1, "cat1"), (E1, "non_fo"), (E2A, "non_fo")]:
        r = cal.era_for(d, "CM", cat)
        assert r.cas_start is None and r.cas_end is None and r.cas_applicable == "no"


@pytest.mark.parametrize("d,cat,flag", [(E3A, "cat1", "no"), (E4A, "cat1", "yes"), (E4A, "non_fo", "yes")])
def test_A9_cas_carry_forward_flag(cal, d, cat, flag):
    assert cal.era_for(d, "CM", cat).cas_carry_forward == flag


def test_A9_carry_forward_is_flag_only_no_timing_effect(cal):
    assert cal.market_close_ist(E3Z, "CM", "cat1").time() == cal.market_close_ist(E4A, "CM", "cat1").time()


# ---- A11 --------------------------------------------------------------------
def test_A11_before_2010_calendar_error(era_tables):
    cal = era_tables.calendar()
    with pytest.raises(_mod().CalendarError):
        cal.era_for(D(2009, 12, 31), "CM", "cat1")
    with pytest.raises(_mod().CalendarError):
        cal.market_close_ist(D(2009, 12, 31), "CM", "cat1")


def test_A11_first_supported_date_is_2010_01_01(cal):
    assert cal.market_close_ist(D(2010, 1, 1), "CM", "cat1").time() == time(15, 30)


def test_A11_unknown_category_and_segment_are_ValueError(cal):
    with pytest.raises(ValueError):
        cal.market_close_ist(E3A, "CM", "cat2")
    with pytest.raises(ValueError):
        cal.market_close_ist(E3A, "EQ", "cat1")
    with pytest.raises(ValueError):
        cal.era_for(E3A, "EQ", "cat1")
    with pytest.raises(ValueError):
        cal.era_for(E3A, "CM", "cat2")


@pytest.mark.parametrize("seg,cat", [("CM", "index_fo"), ("CM", "stock_fo"), ("FO", "cat1"), ("FO", "non_fo")])
def test_A11_category_must_fit_segment(cal, seg, cat):
    with pytest.raises(ValueError):
        cal.market_close_ist(E3A, seg, cat)


def test_A11_datetime_where_date_belongs_is_TypeError(cal):
    with pytest.raises(TypeError):
        cal.market_close_ist(datetime(2026, 12, 7, 12, 0), "CM", "cat1")
    with pytest.raises(TypeError):
        cal.era_for(datetime(2026, 12, 7), "CM", "cat1")


# ---- A12 / R9 special sessions ---------------------------------------------
SPECIAL = D(2026, 12, 12)


@pytest.fixture
def special_cal(era_tables):
    era_tables.set_sessions([srow(SPECIAL, "special_session", open_ist="09:15", close_ist="10:15",
                                  evidence_kind="circular", evidence_ref="c-special")])
    return era_tables.calendar()


def test_A12_special_session_overrides_era(special_cal):
    got = special_cal.market_close_ist(SPECIAL, "CM", "cat1")
    assert got == ist(2026, 12, 12, 10, 15)
    st = special_cal.session_times(SPECIAL, "CM", "cat1")
    assert st.close_era == "special:2026-12-12"
    assert st.market_close == got and st.close_earliest == got
    assert st.close_method == "vwap_last_30m"
    assert st.date == SPECIAL


def test_A12_special_session_applies_to_every_cm_category(special_cal):
    for cat in ("cat1", "non_fo"):
        assert special_cal.market_close_ist(SPECIAL, "CM", cat) == ist(2026, 12, 12, 10, 15)


def test_A12_special_FO_session_method(era_tables):
    era_tables.set_sessions([srow(SPECIAL, "special_session", segment="FO", open_ist="09:15",
                                  close_ist="10:15", evidence_kind="circular", evidence_ref="c")])
    cal = era_tables.calendar()
    st = cal.session_times(SPECIAL, "FO", "stock_fo")
    assert st.close_method == "fo_session_end" and st.close_era == "special:2026-12-12"


def test_A12_ALL_special_session_covers_both_segments(era_tables):
    era_tables.set_sessions([srow(SPECIAL, "special_session", segment="ALL", open_ist="09:15",
                                  close_ist="10:15", evidence_kind="circular", evidence_ref="c")])
    cal = era_tables.calendar()
    assert cal.market_close_ist(SPECIAL, "CM", "cat1") == ist(2026, 12, 12, 10, 15)
    assert cal.market_close_ist(SPECIAL, "FO", "index_fo") == ist(2026, 12, 12, 10, 15)


def test_A12_muhurat_evening_session_on_a_weekday(era_tables):
    d = D(2026, 11, 8)  # a Sunday used as a fixture date
    era_tables.set_sessions([srow(d, "special_session", open_ist="18:00", close_ist="19:00",
                                  evidence_kind="circular", evidence_ref="muhurat; daytime holiday")])
    cal = era_tables.calendar()
    assert cal.market_close_ist(d, "CM", "cat1") == ist(2026, 11, 8, 19, 0)


def test_A12_special_session_time_comes_from_the_row_not_the_era_table(era_tables):
    era_tables.set_sessions([srow(SPECIAL, "special_session", open_ist="09:15", close_ist="11:20",
                                  evidence_kind="circular", evidence_ref="c")])
    era_tables.set_era("E3-CM-cat1", market_close="15:40")
    assert era_tables.calendar().market_close_ist(SPECIAL, "CM", "cat1") == ist(2026, 12, 12, 11, 20)


# ---- A13 / R7 ---------------------------------------------------------------
def test_A13_unverified_row_market_close_is_the_later_time(cal):
    r = cal.era_for(E3A, "CM", "non_fo")
    assert r.cas_applicable == "unverified" and r.market_close == time(15, 40)
    assert cal.market_close_ist(E3A, "CM", "non_fo").time() == time(15, 40)


def test_A13_table_drives_the_value_changing_row_to_no(era_tables):
    era_tables.set_era("E3-CM-non_fo", cas_applicable="no", cas_start="", cas_end="", market_close="15:30",
                       close_earliest="15:30", close_method="vwap_last_30m")
    cal = era_tables.calendar()
    assert cal.market_close_ist(E3A, "CM", "non_fo") == ist(2026, 12, 7, 15, 30)
    assert cal.market_close_ist(E3A, "CM", "cat1") == ist(2026, 12, 7, 15, 40)


def test_A13_a_new_era_row_changes_result_without_code(era_tables):
    def add(h, rows):
        e4 = find_era(rows, "E4-CM-cat1")
        e4["effective_to"] = "2027-05-31"
        new = dict(e4, era_id="E5-CM-cat1", effective_from="2027-06-01", effective_to="",
                   market_close="15:45", cas_end="15:45", close_earliest="15:43")
        rows.append(new)
    era_tables.edit_eras(add)
    cal = era_tables.calendar()
    assert cal.market_close_ist(D(2027, 6, 1), "CM", "cat1") == ist(2027, 6, 1, 15, 45)
    assert cal.market_close_ist(E4A, "CM", "cat1") == ist(2027, 2, 8, 15, 40)
    assert cal.era_for(D(2027, 5, 31), "CM", "cat1").era_id == "E4-CM-cat1"


# ---- A14 / R8 planted -------------------------------------------------------
def _expect_load_error(tables, *ids):
    with pytest.raises(_mod().CalendarError) as ei:
        tables.calendar()
    msg = str(ei.value)
    for i in ids:
        assert i in msg, f"{i!r} not named in {msg!r}"


def test_A14_gap_between_E2_and_E3(era_tables):
    era_tables.set_era("E2-CM-cat1", effective_to="2026-12-05")
    _expect_load_error(era_tables, "E2-CM-cat1", "E3-CM-cat1")


def test_A14_overlap(era_tables):
    era_tables.set_era("E2-CM-cat1", effective_to="2026-12-07")
    _expect_load_error(era_tables, "E2-CM-cat1", "E3-CM-cat1")


def test_A14_open_ended_row_not_last(era_tables):
    era_tables.set_era("E2-CM-cat1", effective_to="")
    _expect_load_error(era_tables, "E2-CM-cat1")


def test_A14_market_close_before_continuous_end(era_tables):
    era_tables.set_era("E1-FO-stock_fo", market_close="15:20", close_earliest="15:20")
    _expect_load_error(era_tables, "E1-FO-stock_fo")


def test_A14_effective_to_before_from(era_tables):
    era_tables.set_era("E2-CM-non_fo", effective_to="2026-07-01")
    _expect_load_error(era_tables, "E2-CM-non_fo")


def test_A14_market_close_before_cas_end(era_tables):
    era_tables.set_era("E2-CM-cat1", market_close="15:30")
    _expect_load_error(era_tables, "E2-CM-cat1")


def test_A14_market_close_before_close_earliest(era_tables):
    era_tables.set_era("E3-CM-cat1", close_earliest="15:50")
    _expect_load_error(era_tables, "E3-CM-cat1")


def test_A14_post_close_before_market_close(era_tables):
    era_tables.set_era("E3-CM-cat1", post_close_start="15:35", post_close_end="15:45")
    _expect_load_error(era_tables, "E3-CM-cat1")


def test_A14_first_group_row_dropped_leaves_later_start_but_no_gap_is_allowed(era_tables):
    # a group's coverage starts at its first effective_from; dropping the first row shifts the start
    def drop(h, rows):
        rows[:] = [r for r in rows if r["era_id"] != "E1-CM-cat1"]
    era_tables.edit_eras(drop)
    cal = era_tables.calendar()
    with pytest.raises(_mod().CalendarError):
        cal.era_for(D(2020, 1, 1), "CM", "cat1")
    assert cal.era_for(D(2020, 1, 1), "CM", "non_fo").era_id == "E1-CM-non_fo"


def test_A14_gap_in_BSE_group_is_caught_too(era_tables):
    def f(h, rows):
        for r in rows:
            if r["exchange"] == "BSE" and r["segment"] == "FO" and r["category"] == "stock_fo" \
                    and r["effective_from"] == "2026-08-03":
                r["effective_from"] = "2026-08-05"
    era_tables.edit_eras(f)
    with pytest.raises(_mod().CalendarError):
        era_tables.calendar()


def test_A14_unmodified_shipped_copy_loads(era_tables):
    era_tables.calendar()


@pytest.mark.parametrize("field,value", [
    ("exchange", "NYSE"), ("segment", "EQ"), ("category", "cat9"), ("close_method", "twap"),
    ("verified", "guess"), ("cas_applicable", "maybe"), ("market_close", "25:61"),
    ("effective_from", "2026-02-30"), ("public_lag_min", "-1"), ("public_lag_min", "thirty"),
    ("cas_carry_forward", "perhaps"), ("continuous_end", "3pm"),
])
def test_R2_bad_era_values_rejected(era_tables, field, value):
    era_tables.set_era("E3-CM-cat1", **{field: value})
    with pytest.raises(_mod().CalendarError) as ei:
        era_tables.calendar()
    assert "close_eras.csv" in str(ei.value)


def test_R2_duplicate_era_id_rejected(era_tables):
    def dup(h, rows):
        rows[1]["era_id"] = rows[0]["era_id"]
    era_tables.edit_eras(dup)
    with pytest.raises(_mod().CalendarError):
        era_tables.calendar()


def test_R2_era_header_typo_rejected(era_tables):
    text = era_tables.eras_path.read_text().replace("public_lag_min", "public_lag_minutes", 1)
    era_tables.eras_path.write_text(text)
    with pytest.raises(_mod().CalendarError) as ei:
        era_tables.calendar()
    assert "close_eras.csv" in str(ei.value)


# ---- A15 / R10 --------------------------------------------------------------
@pytest.mark.parametrize("d,seg,cat,h,m", [(E3A, "CM", "cat1", 16, 10), (E2A, "CM", "cat1", 16, 5),
                                           (E1, "CM", "cat1", 16, 0), (E3A, "FO", "stock_fo", 16, 15),
                                           (E3A, "FO", "index_fo_expiring", 16, 0)])
def test_A15_after_close_public_at(cal, d, seg, cat, h, m):
    got = cal.after_close_public_at(d, seg, cat)
    assert got == ist(d.year, d.month, d.day, h, m)
    assert got.tzinfo is not None and getattr(got.tzinfo, "key", None) == "Asia/Kolkata"


def test_A15_public_is_close_plus_lag_for_every_group_and_date(cal):
    for d, seg, cat, _h, _m in CLOSES:
        lag = cal.era_for(d, seg, cat).public_lag_min
        assert cal.after_close_public_at(d, seg, cat) == cal.market_close_ist(d, seg, cat) + timedelta(minutes=lag)


def test_A15_special_session_lag_from_the_era_row(special_cal):
    assert special_cal.after_close_public_at(SPECIAL, "CM", "cat1") == ist(2026, 12, 12, 10, 45)


def test_A15_lag_is_read_from_table(era_tables):
    era_tables.set_era("E3-CM-cat1", public_lag_min="45")
    era_tables.set_sessions(confirm_rows([E3A]) + [srow(SPECIAL, "special_session", open_ist="09:15", close_ist="10:15",
                                  evidence_kind="circular", evidence_ref="c")])
    cal = era_tables.calendar()
    assert cal.after_close_public_at(E3A, "CM", "cat1") == ist(2026, 12, 7, 16, 25)
    assert cal.after_close_public_at(SPECIAL, "CM", "cat1") == ist(2026, 12, 12, 11, 0)
    assert cal.after_close_public_at(E3A, "CM", "non_fo") == ist(2026, 12, 7, 16, 10)


def test_A15_requires_a_trading_day(tables):
    cal = tables.calendar()
    with pytest.raises(_mod().NotATradingDay):
        cal.after_close_public_at(D(2026, 11, 14), "CM", "cat1")


# ---- A16 / R10 --------------------------------------------------------------
def test_A16_naive_fetched_at_is_ValueError(cal):
    with pytest.raises(ValueError):
        cal.known_at(E3A, "CM", "cat1", datetime(2026, 12, 8, 9, 0))


def test_A16_fetched_before_publication_gives_publication_instant(cal):
    got = cal.known_at(E3A, "CM", "cat1", ist(2026, 12, 7, 15, 35))
    assert got == ist(2026, 12, 7, 16, 10)


def test_A16_fetched_next_morning_gives_fetch_time(cal):
    f = ist(2026, 12, 8, 9, 0)
    assert cal.known_at(E3A, "CM", "cat1", f) == f


def test_A16_fetched_exactly_at_publication(cal):
    f = ist(2026, 12, 7, 16, 10)
    assert cal.known_at(E3A, "CM", "cat1", f) == f


def test_A16_other_timezone_compared_as_instants(cal):
    early = datetime(2026, 12, 7, 10, 5, tzinfo=timezone.utc)   # 15:35 IST
    late = datetime(2026, 12, 7, 11, 0, tzinfo=timezone.utc)    # 16:30 IST
    assert cal.known_at(E3A, "CM", "cat1", early) == ist(2026, 12, 7, 16, 10)
    assert cal.known_at(E3A, "CM", "cat1", late) == late


def test_A16_never_the_trade_date_midnight(cal):
    got = cal.known_at(E3A, "CM", "cat1", ist(2026, 12, 7, 0, 0))
    assert got != ist(2026, 12, 7, 0, 0)
    assert got == cal.after_close_public_at(E3A, "CM", "cat1")


def test_A16_known_at_special_session(special_cal):
    assert special_cal.known_at(SPECIAL, "CM", "cat1", ist(2026, 12, 12, 9, 0)) == ist(2026, 12, 12, 10, 45)


def test_A16_known_at_requires_trading_day(tables):
    with pytest.raises(_mod().NotATradingDay):
        tables.calendar().known_at(D(2026, 11, 16), "CM", "cat1", ist(2026, 11, 17, 9))


def test_A16_known_at_exchange_argument(cal):
    assert cal.known_at(E3A, "CM", "cat1", ist(2026, 12, 7, 15, 0), exchange="BSE") == ist(2026, 12, 7, 16, 10)


# ---- A17 / R11 --------------------------------------------------------------
def test_A17_fo_expiring_has_both_price_fields_and_settlement_window(cal):
    st = cal.session_times(E3A, "FO", "index_fo_expiring")
    assert st.price_fields == ("close_price", "settlement_price")
    assert as_window(cal.era_for(E3A, "FO", "index_fo_expiring").settlement_window) == "15:00-15:30"
    assert st.close_era == "E3-FO-index_fo_expiring"
    assert st.market_close == ist(2026, 12, 7, 15, 30)


@pytest.mark.parametrize("cat", ["index_fo", "stock_fo"])
def test_A17_E3_FO_non_expiring_also_has_settlement_window(cal, cat):
    assert cal.session_times(E3A, "FO", cat).price_fields == ("close_price", "settlement_price")
    assert as_window(cal.era_for(E3A, "FO", cat).settlement_window) == "15:00-15:30"


@pytest.mark.parametrize("d,seg,cat", [(E3A, "CM", "cat1"), (E3A, "CM", "non_fo"), (E2A, "CM", "cat1"),
                                       (E1, "FO", "stock_fo"), (E2A, "FO", "index_fo_expiring"),
                                       (E4A, "CM", "cat1")])
def test_A17_close_only_groups(cal, d, seg, cat):
    assert cal.session_times(d, seg, cat).price_fields == ("close_price",)
    assert cal.era_for(d, seg, cat).settlement_window in (None, "", ())


def test_A17_session_times_fields(cal):
    st = cal.session_times(E3A, "CM", "cat1")
    assert st.date == E3A and st.close_era == "E3-CM-cat1" and st.close_method == "closing_auction"
    assert st.market_close == ist(2026, 12, 7, 15, 40)
    assert st.close_earliest == ist(2026, 12, 7, 15, 38)
    assert st.close_earliest.tzinfo is not None
    assert st.market_close >= st.close_earliest
    assert st.market_close == cal.market_close_ist(E3A, "CM", "cat1")


@pytest.mark.parametrize("d,era,method", [(E1, "E1-CM-cat1", "vwap_last_30m"), (E2A, "E2-CM-cat1", "closing_auction"),
                                          (E2A, "E2-CM-non_fo", "vwap_last_30m")])
def test_A17_close_era_and_method_recorded(cal, d, era, method):
    cat = era.split("-", 2)[2]
    st = cal.session_times(d, "CM", cat)
    assert st.close_era == era and st.close_method == method


def test_A17_session_times_is_frozen(cal):
    import dataclasses
    st = cal.session_times(E3A, "CM", "cat1")
    with pytest.raises(dataclasses.FrozenInstanceError):
        st.market_close = st.close_earliest


def test_A17_session_times_needs_trading_day(tables):
    with pytest.raises(_mod().NotATradingDay):
        tables.calendar().session_times(D(2026, 11, 16), "CM", "cat1")
    assert tables.calendar().session_times(D(2026, 11, 16), "CM", "cat1",
                                           allow_provisional=True).close_era == "E2-CM-cat1"
