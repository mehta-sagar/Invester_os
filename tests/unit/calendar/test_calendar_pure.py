"""S1-T4 A18, A21: PriceFacts (R11) and purity (R14)."""
from __future__ import annotations

import ast
import dataclasses
import importlib
import re
import time as _time
from datetime import date, datetime
from pathlib import Path

import pytest

from _cal_helpers import D, REPO, ist, confirm_rows


def _mod():
    from investos.calendar import trading
    return trading


def _src_files():
    d = REPO / "src" / "investos" / "calendar"
    files = sorted(d.glob("*.py"))
    assert files, "src/investos/calendar/*.py missing"
    return files


# ---- A18 / R11 --------------------------------------------------------------
def test_A18_settlement_only_leaves_close_none():
    pf = _mod().PriceFacts(settlement_price=101.5)
    assert pf.close_price is None and pf.settlement_price == 101.5 and pf.close_era is None


def test_A18_close_only_leaves_settlement_none():
    pf = _mod().PriceFacts(close_price=99.0, close_era="E3-CM-cat1")
    assert pf.settlement_price is None and pf.close_price == 99.0 and pf.close_era == "E3-CM-cat1"


def test_A18_defaults_and_frozen_and_field_names():
    P = _mod().PriceFacts
    pf = P()
    assert (pf.close_price, pf.settlement_price, pf.close_era) == (None, None, None)
    assert [f.name for f in dataclasses.fields(P)] == ["close_price", "settlement_price", "close_era"]
    with pytest.raises(dataclasses.FrozenInstanceError):
        pf.close_price = 1.0


def test_A18_source_never_assigns_one_price_from_the_other():
    names = {"close_price", "settlement_price"}

    def refs(node):
        return {n.id for n in ast.walk(node) if isinstance(n, ast.Name)} | \
               {n.attr for n in ast.walk(node) if isinstance(n, ast.Attribute)} | \
               {n.value for n in ast.walk(node) if isinstance(n, ast.Constant) and isinstance(n.value, str)}

    def target_names(t):
        return {n.id for n in ast.walk(t) if isinstance(n, ast.Name)} | \
               {n.attr for n in ast.walk(t) if isinstance(n, ast.Attribute)}

    offenders = []
    for f in _src_files():
        tree = ast.parse(f.read_text())
        for node in ast.walk(tree):
            if isinstance(node, (ast.Assign, ast.AnnAssign, ast.AugAssign)) and node.value is not None:
                targets = node.targets if isinstance(node, ast.Assign) else [node.target]
                tn = set().union(*(target_names(t) for t in targets)) & names
                if tn and (refs(node.value) & names) - tn:
                    offenders.append((f.name, node.lineno))
            if isinstance(node, ast.keyword) and node.arg in names:
                if (refs(node.value) & names) - {node.arg}:
                    offenders.append((f.name, node.value.lineno))
    assert not offenders, offenders


def test_A18_source_never_coalesces_the_two_prices():
    for f in _src_files():
        txt = f.read_text()
        assert not re.search(r"close_price[^\n]*\bor\b[^\n]*settlement_price", txt)
        assert not re.search(r"settlement_price[^\n]*\bor\b[^\n]*close_price", txt)
        assert "coalesce" not in txt.lower()


# ---- A21 / R14 --------------------------------------------------------------
def _calls(cal, d, bad=None):
    """Every public method with a date argument; `bad` replaces the date."""
    x = bad if bad is not None else d
    f = ist(2026, 12, 8, 9)
    return [
        lambda: cal.day_status(x),
        lambda: cal.is_trading_day(x),
        lambda: cal.era_for(x, "CM", "cat1"),
        lambda: cal.market_close_ist(x, "CM", "cat1"),
        lambda: cal.after_close_public_at(x, "CM", "cat1"),
        lambda: cal.known_at(x, "CM", "cat1", f),
        lambda: cal.session_times(x, "CM", "cat1"),
    ]


def test_A21_datetime_instead_of_date_is_TypeError_for_every_method(cal):
    bad = datetime(2026, 12, 7, 9, 30)
    for call in _calls(cal, D(2026, 12, 7), bad):
        with pytest.raises(TypeError):
            call()


def test_A21_ist_aware_datetime_also_TypeError(cal):
    for call in _calls(cal, D(2026, 12, 7), ist(2026, 12, 7, 9, 30)):
        with pytest.raises(TypeError):
            call()


def test_A21_string_date_is_TypeError(cal):
    for call in _calls(cal, D(2026, 12, 7), "2026-12-07"):
        with pytest.raises(TypeError):
            call()


def test_A21_no_clock_reads(cal, monkeypatch):
    d = D(2026, 12, 7)
    with monkeypatch.context() as m:
        def boom(*a, **k):
            raise AssertionError("system clock read")
        for name in ("time", "monotonic", "perf_counter", "localtime", "gmtime", "time_ns"):
            m.setattr(_time, name, boom)
        for call in _calls(cal, d):
            call()
        cal.market_close_ist(d, "FO", "stock_fo")


def test_A21_calling_twice_returns_equal_results(cal):
    d = D(2026, 12, 7)
    first = [c() for c in _calls(cal, d)]
    second = [c() for c in _calls(cal, d)]
    assert first == second


def test_A21_two_instances_agree_and_nothing_is_cached_across_instances(era_tables):
    a = era_tables.calendar()
    assert a.market_close_ist(D(2026, 12, 7), "CM", "cat1").hour == 15
    era_tables.set_era("E3-CM-cat1", market_close="15:41")
    b = era_tables.calendar()
    assert b.market_close_ist(D(2026, 12, 7), "CM", "cat1").minute == 41
    assert a.market_close_ist(D(2026, 12, 7), "CM", "cat1").minute == 40


def test_A21_weekday_computed_with_date_weekday(tables):
    cal = tables.calendar()
    for d in (D(2026, 11, 14), D(2026, 11, 15)):
        assert cal.day_status(d).value == "closed"
    for d in (D(2026, 11, 13), D(2026, 11, 16)):
        assert cal.day_status(d).value == "provisional"


def test_R14_source_has_no_clock_network_or_write_access():
    banned = [r"\.today\(", r"\.now\(", r"utcnow", r"time\.time\(", r"monotonic", r"perf_counter",
              r"\bimport\s+(socket|urllib|requests|httpx|http|ftplib|smtplib|aiohttp|ssl)\b",
              r"\bfrom\s+(socket|urllib|requests|httpx|http|ftplib|smtplib|aiohttp|ssl)\b",
              r"\.write_text\(", r"\.write_bytes\(", r"open\([^)]*['\"][wax+]", r"os\.remove|unlink\(|shutil\."]
    for f in _src_files():
        txt = f.read_text()
        for pat in banned:
            assert not re.search(pat, txt), f"{f.name}: {pat}"


def test_R14_shipped_csv_files_are_not_modified_by_loading(tables):
    before = {p: p.read_bytes() for p in (tables.eras_path, tables.sessions_path, tables.years_path)}
    cal = tables.calendar()
    cal.day_status(D(2026, 11, 16))
    assert {p: p.read_bytes() for p in before} == before


def test_R14_only_stdlib_and_core_imports():
    allowed_roots = {"__future__", "csv", "dataclasses", "datetime", "enum", "pathlib", "re", "typing",
                     "zoneinfo", "collections", "functools", "itertools", "io", "investos", "operator",
                     "bisect", "types", "os"}
    for f in _src_files():
        tree = ast.parse(f.read_text())
        for n in ast.walk(tree):
            mods = []
            if isinstance(n, ast.Import):
                mods = [a.name for a in n.names]
            elif isinstance(n, ast.ImportFrom) and n.level == 0 and n.module:
                mods = [n.module]
            for m in mods:
                assert m.split(".")[0] in allowed_roots, f"{f.name} imports {m}"
