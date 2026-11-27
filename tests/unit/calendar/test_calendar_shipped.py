"""S1-T4: the shipped close_eras.csv reproduces the spec table row for row (brief 'Done means')."""
from __future__ import annotations

from collections import defaultdict
from datetime import date

import pytest

from _cal_helpers import SHIPPED, ERAS_HEADER, YEARS_HEADER, SESSIONS_HEADER, read_csv

END1, END2, END3 = "2026-08-02", "2026-12-06", "2027-02-07"
S1, S2, S3, S4 = "2010-01-01", "2026-08-03", "2026-12-07", "2027-02-08"

CM = [("CM", "cat1"), ("CM", "non_fo")]
FO = [("FO", "index_fo"), ("FO", "index_fo_expiring"), ("FO", "stock_fo")]


def exp(era, frm, to, seg, cat, cont, cas_s, cas_e, app, method, close, **extra):
    d = dict(effective_from=frm, effective_to=to, segment=seg, category=cat, continuous_end=cont,
             cas_start=cas_s, cas_end=cas_e, close_method=method, market_close=close,
             public_lag_min="30")
    if app:                      # the spec states cas_applicable only for CM rows
        d["cas_applicable"] = app
    d.update(extra)
    return (f"{era}-{seg}-{cat}", d)


EXPECTED = []
for seg, cat in CM:
    EXPECTED.append(exp("E1", S1, END1, seg, cat, "15:30", "", "", "no", "vwap_last_30m", "15:30"))
for seg, cat in FO:
    EXPECTED.append(exp("E1", S1, END1, seg, cat, "15:30", "", "", "", "fo_session_end", "15:30"))
EXPECTED.append(exp("E2", S2, END2, "CM", "cat1", "15:15", "15:20", "15:35", "yes", "closing_auction", "15:35"))
EXPECTED.append(exp("E2", S2, END2, "CM", "non_fo", "15:30", "", "", "no", "vwap_last_30m", "15:30"))
for seg, cat in FO:
    EXPECTED.append(exp("E2", S2, END2, seg, cat, "15:40", "", "", "", "fo_session_end", "15:40"))
for era, frm, to, carry in (("E3", S3, END3, "no"), ("E4", S4, "", "yes")):
    EXPECTED.append(exp(era, frm, to, "CM", "cat1", "15:30", "15:30", "15:40", "yes", "closing_auction", "15:40",
                        close_earliest="15:38", post_close_start="15:50", post_close_end="16:00",
                        cas_carry_forward=carry))
    EXPECTED.append(exp(era, frm, to, "CM", "non_fo", "15:30", "15:30", "15:40", "unverified",
                        "closing_auction", "15:40", **({"cas_carry_forward": "yes"} if era == "E4" else {})))
    EXPECTED.append(exp(era, frm, to, "FO", "index_fo", "15:45", "", "", "", "fo_session_end", "15:45",
                        settlement_window="15:00-15:30"))
    EXPECTED.append(exp(era, frm, to, "FO", "stock_fo", "15:45", "", "", "", "fo_session_end", "15:45",
                        settlement_window="15:00-15:30"))
    EXPECTED.append(exp(era, frm, to, "FO", "index_fo_expiring", "15:30", "", "", "", "fo_session_end", "15:30",
                        settlement_window="15:00-15:30"))


@pytest.fixture(scope="module")
def eras():
    h, rows = read_csv(SHIPPED / "close_eras.csv")
    return h, rows


def _by(rows, exchange):
    return {(r["segment"], r["category"], r["effective_from"]): r for r in rows if r["exchange"] == exchange}


def test_shipped_header_exact(eras):
    assert eras[0] == ERAS_HEADER


def test_shipped_other_headers():
    assert read_csv(SHIPPED / "sessions.csv")[0] == SESSIONS_HEADER
    assert read_csv(SHIPPED / "years.csv")[0] == YEARS_HEADER


def test_shipped_row_counts(eras):
    rows = eras[1]
    assert len(EXPECTED) == 20
    assert sum(r["exchange"] == "NSE" for r in rows) == 20
    assert sum(r["exchange"] == "BSE" for r in rows) == 20
    assert len(rows) == 40
    assert len({r["era_id"] for r in rows}) == 40


@pytest.mark.parametrize("era_id,want", EXPECTED, ids=[e[0] for e in EXPECTED])
def test_shipped_nse_row_matches_spec_table(eras, era_id, want):
    nse = {r["era_id"]: r for r in eras[1] if r["exchange"] == "NSE"}
    assert era_id in nse, f"missing NSE row {era_id}"
    got = nse[era_id]
    for k, v in want.items():
        assert got[k] == v, f"{era_id}.{k}: {got[k]!r} != {v!r}"


@pytest.mark.parametrize("era_id,want", EXPECTED, ids=[e[0] for e in EXPECTED])
def test_shipped_bse_row_is_identical_copy_except_id_and_verified(eras, era_id, want):
    nse = _by(eras[1], "NSE")
    bse = _by(eras[1], "BSE")
    key = (want["segment"], want["category"], want["effective_from"])
    assert key in bse
    for col in ERAS_HEADER:
        if col in ("era_id", "verified", "exchange"):
            continue
        assert bse[key][col] == nse[key][col], col


def test_shipped_verified_values(eras):
    for r in eras[1]:
        if r["exchange"] == "NSE":
            assert r["verified"] == "press", r["era_id"]
        else:
            assert r["verified"] == "assumed", r["era_id"]
    assert not any(r["verified"] == "circular" for r in eras[1])


def test_shipped_public_lag_is_30_everywhere(eras):
    assert {r["public_lag_min"] for r in eras[1]} == {"30"}     # spec open question 4: default 30


def test_shipped_table_starts_2010_01_01_and_E4_is_open(eras):
    nse = _by(eras[1], "NSE")
    firsts = defaultdict(list)
    for (seg, cat, frm), r in nse.items():
        firsts[(seg, cat)].append(frm)
    for g, fs in firsts.items():
        assert min(fs) == "2010-01-01", g                       # spec open question 2: default start
    opens = [r for r in eras[1] if r["effective_to"] == ""]
    assert len(opens) == 10 and all(r["effective_from"] == "2027-02-08" for r in opens)


def test_shipped_era_ids_unique_pattern(eras):
    import re
    for r in eras[1]:
        if r["exchange"] == "NSE":
            assert re.fullmatch(r"E[1-4]-(CM|FO)-[a-z_0-9]+", r["era_id"])


def test_shipped_E3_E4_non_fo_unverified_everything_else_known(eras):
    cas = {(r["exchange"], r["era_id"]): r["cas_applicable"] for r in eras[1] if r["segment"] == "CM"}
    assert cas[("NSE", "E3-CM-non_fo")] == "unverified" and cas[("NSE", "E4-CM-non_fo")] == "unverified"
    assert cas[("NSE", "E2-CM-non_fo")] == "no" and cas[("NSE", "E2-CM-cat1")] == "yes"


def test_shipped_years_has_no_invented_values():
    h, rows = read_csv(SHIPPED / "years.csv")
    assert all(r["list_source"] for r in rows)                  # any shipped year names its list source
