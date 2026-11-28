"""S1-T8: the endpoint table (R2, A1) and the symbol list (R22)."""
from __future__ import annotations

import pytest

from _backfill_helpers import (COLS, HEADER, IDS, SHIPPED_TABLE, default_rows, table_row, write_table)


def load(path):
    from investos.backfill.endpoints import load_endpoints
    return load_endpoints(path)


def err(path):
    from investos.backfill.endpoints import EndpointTableError
    return EndpointTableError


# ------------------------------------------------------------------ A1 the shipped table
def test_A1_shipped_table_has_the_eight_catalogue_endpoints_all_to_probe():
    eps = load(SHIPPED_TABLE)
    assert sorted(eps) == sorted(IDS)
    assert all(e.status == "to_probe" for e in eps.values())
    assert all(e.known_at_rule.strip() for e in eps.values())


def test_A1_shipped_table_header_is_exact_and_lf_only():
    raw = SHIPPED_TABLE.read_bytes()
    assert raw.split(b"\n")[0].decode() == HEADER
    assert b"\r" not in raw


def test_A1_shipped_table_paths_are_on_the_pages_own_origin_with_known_hosts_only():
    for e in load(SHIPPED_TABLE).values():
        assert e.api_path.startswith("/") and "://" not in e.api_path
        assert e.page_url.startswith("/") or e.page_url.startswith("https://www.nseindia.com/")
        assert e.anchor_url.startswith("/") or e.anchor_url.startswith("https://www.nseindia.com/")
        assert "robots.txt" not in e.anchor_url


def test_A1_row_hash_is_stable_and_changes_with_any_one_cell(tmp_path):
    p = write_table(tmp_path / "t.csv", [table_row("announcements")])
    e1, e2 = load(p)["announcements"], load(p)["announcements"]
    assert e1.row_hash() == e2.row_hash() and len(e1.row_hash()) == 64
    changed = {"page_url": "/page/other", "anchor_url": "/page/other", "api_path": "/api/x?from_date={from}&to_date={to}",
               "params": "from={from}&to={to}", "date_format": "YYYY-MM-DD", "to_inclusive": "yes",
               "max_window_days": "2", "row_cap": "70", "rows_path": "data", "date_field": "dt",
               "start_date": "2026-11-02", "time_of_day": "all", "known_at_rule": "other", "status": "probed",
               "notes": "n"}
    assert set(changed) == set(COLS) - {"endpoint_id"}
    hashes = {e1.row_hash()}
    for col, val in changed.items():
        row = table_row("announcements")
        row[col] = val
        h = load(write_table(tmp_path / f"{col}.csv", [row]))["announcements"].row_hash()
        assert h not in hashes, col
        hashes.add(h)


def test_A1_endpoint_source_and_fields(tmp_path):
    e = load(write_table(tmp_path / "t.csv", [table_row("bulk_deals", row_cap="70", to_inclusive="no",
                                                        start_date="2010-01-01")]))["bulk_deals"]
    assert e.source == "backfill_bulk_deals"
    assert e.row_cap == 70 and e.to_inclusive is False and e.max_window_days == 1
    assert e.start_date.isoformat() == "2010-01-01"


# ------------------------------------------------------------------ A1 planted CSVs (R2)
def _bad(tmp_path, rows=None, header=HEADER, text=None):
    p = tmp_path / "bad.csv"
    if text is not None:
        p.write_text(text)
    else:
        write_table(p, rows, header=header)
    return p


PLANTS = {
    "missing_column": lambda: dict(header=",".join(COLS[:-1]), rows=[table_row("a")]),
    "extra_column": lambda: dict(header=HEADER + ",extra", rows=[table_row("a")]),
    "duplicate_id": lambda: dict(rows=[table_row("a"), table_row("a")]),
    "scheme_in_api_path": lambda: dict(rows=[table_row("a", api_path="https://www.nseindia.in/api/a?x={from}")]),
    "foreign_host_in_anchor": lambda: dict(rows=[table_row("a", anchor_url="https://example.com/x")]),
    "nse_in_host_for_anchor": lambda: dict(rows=[table_row("a", anchor_url="https://www.nseindia.in/x")]),
    "start_date_1999": lambda: dict(rows=[table_row("a", start_date="1999-12-31")]),
    "unknown_placeholder": lambda: dict(rows=[table_row("a", params="{foo}")]),
    "bad_id": lambda: dict(rows=[table_row("A-b")]),
    "id_too_long": lambda: dict(rows=[table_row("a" * 32)]),
    "bad_date_format": lambda: dict(rows=[table_row("a", date_format="MM/DD/YYYY")]),
    "window_zero": lambda: dict(rows=[table_row("a", max_window_days="0")]),
    "cap_zero": lambda: dict(rows=[table_row("a", row_cap="0")]),
    "cap_text": lambda: dict(rows=[table_row("a", row_cap="many")]),
    "bad_status": lambda: dict(rows=[table_row("a", status="done")]),
    "bad_to_inclusive": lambda: dict(rows=[table_row("a", to_inclusive="maybe")]),
    "bad_start_date": lambda: dict(rows=[table_row("a", start_date="2026-13-01")]),
    "relative_api_path": lambda: dict(rows=[table_row("a", api_path="api/a")]),
    "protocol_relative_api_path": lambda: dict(rows=[table_row("a", api_path="//evil.example/api/a")]),
}


@pytest.mark.parametrize("name", sorted(PLANTS))
def test_A1_planted_bad_table_raises_endpoint_table_error(tmp_path, name):
    from investos.backfill.endpoints import EndpointTableError
    with pytest.raises(EndpointTableError) as ei:
        load(_bad(tmp_path, **PLANTS[name]()))
    assert str(ei.value)


def test_A1_error_names_the_line_of_the_offending_row(tmp_path):
    from investos.backfill.endpoints import EndpointTableError
    rows = [table_row("a"), table_row("b"), table_row("c", status="done")]
    with pytest.raises(EndpointTableError) as ei:
        load(_bad(tmp_path, rows))
    assert "4" in str(ei.value)


def test_R2_valid_edge_values_are_accepted(tmp_path):
    rows = [table_row("a", start_date="2000-01-01", row_cap="1", to_inclusive="yes", date_format="YYYY-MM-DD",
                      anchor_url="https://www.nseindia.com/page/a", params="{symbol}"),
            table_row("b0_x", to_inclusive="no", max_window_days="92")]
    eps = load(_bad(tmp_path, rows))
    assert list(eps) == ["a", "b0_x"]                      # table order is kept


def test_R2_unreadable_or_missing_file_is_a_table_error(tmp_path):
    from investos.backfill.endpoints import EndpointTableError
    with pytest.raises(EndpointTableError):
        load(tmp_path / "nope.csv")


# ------------------------------------------------------------------ R22 symbol list
def test_R22_symbol_list_is_validated_like_the_table(tmp_path):
    from investos.backfill.endpoints import EndpointTableError, load_symbols
    ok = tmp_path / "ok.csv"
    ok.write_text("symbol\nRELIANCE\nM&M\nBAJAJ-AUTO\n")
    assert load_symbols(ok) == ["RELIANCE", "M&M", "BAJAJ-AUTO"]
    for name, text in {"header": "sym\nA\n", "lower": "symbol\nabc\n", "dup": "symbol\nA\nA\n", "long": "symbol\n" + "A" * 21 + "\n",
                       "space": "symbol\nA B\n", "empty": "symbol\n\n"}.items():
        p = tmp_path / f"{name}.csv"
        p.write_text(text)
        with pytest.raises(EndpointTableError):
            load_symbols(p)
