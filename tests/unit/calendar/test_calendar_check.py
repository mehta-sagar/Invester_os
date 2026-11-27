"""S1-T4 A19, A20 (+R13): `investos check calendar`, WARNINGs, planted failure."""
from __future__ import annotations

import io
import json
import re
from collections import Counter
from datetime import date

import pytest

from _cal_helpers import Tables, D, srow, yrow, confirm_rows, read_csv, write_raw, SESSIONS_HEADER, SHIPPED


@pytest.fixture
def run_check(tables, tmp_path):
    """run_check(argv, tables=tables) -> (exit_code, stdout + written report text)"""
    from investos.core.log import configure_logging
    configure_logging(None, run_id="t-calendar")

    def _run(argv=("calendar",), t=None):
        from investos.check.cli import main
        from _cal_helpers import load_cfg
        t = t or tables
        buf = io.StringIO()
        code = main(list(argv), cfg=load_cfg(t.repo), stdout=buf)
        rep = ""
        d = t.repo / "reports" / "checks"
        for p in sorted(d.glob("*.json")) if d.is_dir() else []:
            for r in json.loads(p.read_text()).get("results", []):
                rep += "\n".join([r.get("message", "")] + list(r.get("details", []))) + "\n"
            p.unlink()          # one report per call, so counts compare cleanly
        return code, buf.getvalue() + "\n" + rep
    return _run


def _inject_today(monkeypatch, d):
    """The check reads `date.today()` (spec R5). Patch the `date` name in the module that holds the check."""
    from investos.check.cli import load_builtin_checks
    from investos.check.registry import DEFAULT_REGISTRY
    load_builtin_checks()
    mod = __import__(DEFAULT_REGISTRY.get("calendar").__module__, fromlist=["x"])

    class FakeDate(date):
        @classmethod
        def today(cls):
            return cls(d.year, d.month, d.day)

    patched = False
    if hasattr(mod, "date"):
        monkeypatch.setattr(mod, "date", FakeDate)
        patched = True
    for name in ("_today", "today"):
        if callable(getattr(mod, name, None)):
            monkeypatch.setattr(mod, name, lambda: d)
            patched = True
    assert patched, f"{mod.__name__}: cannot inject today (expected `from datetime import date` + date.today())"


def _warnings(text):
    return [ln for ln in text.splitlines() if "WARNING" in ln]


# ---- A19 --------------------------------------------------------------------
def test_A19_check_passes_on_shipped_tables(run_check, era_tables):
    code, out = run_check(t=era_tables)
    assert code == 0, out
    assert "calendar" in out and "pass" in out


def test_A19_check_runs_in_default_all(run_check, era_tables):
    code, out = run_check(("all",), t=era_tables)
    assert "calendar" in out


def test_A19_counts_by_verified_value_printed(run_check, era_tables):
    _h, rows = read_csv(era_tables.eras_path)
    counts = Counter(r["verified"] for r in rows)
    assert counts["press"] > 0 and counts["assumed"] > 0
    code, out = run_check(t=era_tables)
    for k, n in counts.items():
        assert re.search(rf"{k}\D{{0,6}}{n}\b|\b{n}\s*{k}", out), f"count for {k}={n} not printed:\n{out}"


def test_A19_warns_for_unverified_and_assumed_rows(run_check, era_tables):
    _h, rows = read_csv(era_tables.eras_path)
    code, out = run_check(t=era_tables)
    w = "\n".join(_warnings(out))
    assert "E3-CM-non_fo" in w                      # the unverified row (R7)
    assigned = [r["era_id"] for r in rows if r["verified"] == "assumed"]
    assert assigned and all(i in w for i in assigned)
    assert code == 0


def test_A19_check_fails_on_era_gap(run_check, era_tables):
    era_tables.set_era("E2-CM-cat1", effective_to="2026-12-05")
    code, out = run_check(t=era_tables)
    assert code == 1
    assert "E2-CM-cat1" in out


@pytest.mark.parametrize("break_", ["conflict", "bad_header", "dup", "trading_holiday_list"])
def test_R13_check_fails_on_session_errors(run_check, era_tables, break_):
    h = ",".join(SESSIONS_HEADER)
    M = "2026-11-16"
    lines = {
        "conflict": [f"{M},NSE,CM,holiday,,,holiday_list,l,2026-01-01", f"{M},NSE,CM,trading,,,bhavcopy,s:k,2026-11-17"],
        "bad_header": None,
        "dup": [f"{M},NSE,CM,trading,,,manual,x,2026-11-17", f"{M},NSE,CM,trading,,,manual,y,2026-11-17"],
        "trading_holiday_list": [f"{M},NSE,CM,trading,,,holiday_list,l,2026-11-17"],
    }[break_]
    text = (h.replace("status", "state") + "\n" if lines is None else h + "\n" + "\n".join(lines) + "\n")
    write_raw(era_tables.sessions_path, text if lines is not None else text + f"{M},NSE,CM,trading,,,manual,x,2026-11-17\n")
    code, out = run_check(t=era_tables)
    assert code == 1


def test_R13_check_does_not_modify_tables(run_check, era_tables):
    before = {p: p.read_bytes() for p in (era_tables.eras_path, era_tables.sessions_path, era_tables.years_path)}
    run_check(t=era_tables)
    assert {p: p.read_bytes() for p in before} == before


def test_A19_planted_failure_is_caught(run_check, era_tables):
    code, out = run_check(("calendar", "--planted"), t=era_tables)
    assert code == 0, out
    assert "caught" in out


def test_A19_planted_leaves_real_tables_untouched(run_check, era_tables):
    before = era_tables.eras_path.read_bytes()
    run_check(("calendar", "--planted"), t=era_tables)
    assert era_tables.eras_path.read_bytes() == before
    code, _ = run_check(t=era_tables)
    assert code == 0


def test_A19_check_is_registered_with_a_plant():
    from investos.check.cli import load_builtin_checks
    from investos.check.registry import DEFAULT_REGISTRY
    load_builtin_checks()
    assert "calendar" in DEFAULT_REGISTRY.names()
    assert DEFAULT_REGISTRY.plant_of("calendar") is not None


# ---- A20 / R5 ---------------------------------------------------------------
def test_A20_december_2026_with_2027_not_loaded_warns(run_check, era_tables, monkeypatch):
    _inject_today(monkeypatch, D(2026, 12, 15))
    code, out = run_check(t=era_tables)
    assert code == 0
    w_dec = len(_warnings(out))
    era_tables.set_years([yrow(2026), yrow(2026, "BSE"), yrow(2027), yrow(2027, "BSE")])
    code2, out2 = run_check(t=era_tables)
    assert code2 == 0
    assert len(_warnings(out2)) < w_dec


def test_A20_mid_year_with_year_loaded_adds_no_warning(run_check, era_tables, monkeypatch):
    _inject_today(monkeypatch, D(2026, 6, 15))
    _c, out_june = run_check(t=era_tables)
    era_tables.set_years([yrow(2026), yrow(2026, "BSE"), yrow(2027), yrow(2027, "BSE")])
    _inject_today(monkeypatch, D(2026, 12, 15))
    _c, out_dec_loaded = run_check(t=era_tables)
    assert len(_warnings(out_june)) == len(_warnings(out_dec_loaded))


def test_A20_today_in_a_year_not_loaded_warns(run_check, era_tables, monkeypatch):
    _inject_today(monkeypatch, D(2026, 6, 15))
    _c, base = run_check(t=era_tables)
    _inject_today(monkeypatch, D(2028, 3, 1))
    code, out = run_check(t=era_tables)
    assert code == 0 and len(_warnings(out)) > len(_warnings(base))


# ---- shipped tables ---------------------------------------------------------
def test_shipped_tables_load_with_the_real_calendar(tmp_path):
    from _cal_helpers import load_cfg, make_repo
    import shutil
    repo = make_repo(tmp_path / "r")
    shutil.copytree(SHIPPED, repo / "config" / "calendar")
    from investos.calendar.trading import TradingCalendar
    cal = TradingCalendar(load_cfg(repo))               # default paths under <repo_root>/config/calendar
    assert cal.era_for(D(2026, 12, 7), "CM", "cat1").era_id == "E3-CM-cat1"


def test_shipped_sessions_csv_is_header_only_or_valid():
    from _cal_helpers import SESSIONS_HEADER
    h, rows = read_csv(SHIPPED / "sessions.csv")
    assert h == SESSIONS_HEADER
