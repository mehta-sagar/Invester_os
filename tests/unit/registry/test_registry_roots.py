"""S0-T6 acceptance tests A13-A16 for daily roots (rules R9, R10), incl. IST day-boundary tests."""
from __future__ import annotations

import json

import pytest

from _registry_helpers import (FakeClock, canon, edit_line, new_ledger, read_lines, rehash, write_lines)


def roots_path(lpath):
    return lpath.with_suffix(".roots.jsonl")


def append_at(ledger, clock, ts, payload=None):
    clock.set(ts)
    return ledger.append(payload or {"at": ts})


# ---------- A13 ----------

def test_A13_one_root_for_closed_ist_day(ledger, lpath, clock):
    r1 = append_at(ledger, clock, "2026-11-19T20:00:00")   # IST 2026-11-20 01:30
    r2 = append_at(ledger, clock, "2026-11-20T12:00:00")   # IST 2026-11-20 17:30
    r3 = append_at(ledger, clock, "2026-11-20T19:00:00")   # IST 2026-11-21 00:30 (open day)
    clock.set("2026-11-21T10:00:00")                        # IST 2026-11-21
    new = ledger.write_roots()
    assert [(r.date, r.seq, r.hash) for r in new] == [("2026-11-20", 2, r2.hash)]
    assert r1.hash != r2.hash != r3.hash
    rp = roots_path(lpath)
    assert rp.read_bytes() == (canon({"date": "2026-11-20", "hash": r2.hash, "seq": 2}) + "\n").encode()
    assert ledger.roots() == new
    assert ledger.verify().ok


def test_A13b_ist_boundary_235959_vs_000000(ledger, lpath, clock):
    """23:59:59 IST (18:29:59Z) and 00:00:00 IST next day (18:30:00Z) fall on different IST days."""
    a = append_at(ledger, clock, "2026-11-20T18:29:59")
    b = append_at(ledger, clock, "2026-11-20T18:30:00")
    clock.set("2026-11-22T00:00:00")                        # IST 2026-11-22 05:30: both days closed
    new = ledger.write_roots()
    assert [(r.date, r.seq, r.hash) for r in new] == [("2026-11-20", 1, a.hash), ("2026-11-21", 2, b.hash)]
    assert ledger.verify().ok


def test_A13c_open_day_closes_exactly_at_ist_midnight(ledger, lpath, clock):
    a = append_at(ledger, clock, "2026-11-20T10:00:00")
    clock.set("2026-11-20T18:29:59.999999")                 # still IST 2026-11-20 23:59:59.999999
    assert ledger.write_roots() == []
    assert ledger.roots() == []
    clock.set("2026-11-20T18:30:00")                        # IST 2026-11-21 00:00:00
    new = ledger.write_roots()
    assert [(r.date, r.seq, r.hash) for r in new] == [("2026-11-20", 1, a.hash)]


def test_A13d_utc_date_differs_from_ist_date(ledger, clock):
    """A row at UTC 2026-11-20T19:00Z belongs to IST 2026-11-21, not to UTC day 2026-11-20."""
    append_at(ledger, clock, "2026-11-20T12:00:00")
    b = append_at(ledger, clock, "2026-11-20T19:00:00")
    clock.set("2026-11-22T10:00:00")
    new = ledger.write_roots()
    assert [(r.date, r.seq) for r in new] == [("2026-11-20", 1), ("2026-11-21", 2)]
    assert new[1].hash == b.hash


def test_A13e_days_without_rows_get_no_root(ledger, clock):
    append_at(ledger, clock, "2026-11-10T10:00:00")
    append_at(ledger, clock, "2026-11-14T10:00:00")
    clock.set("2026-11-20T10:00:00")
    assert [r.date for r in ledger.write_roots()] == ["2026-11-10", "2026-11-14"]


# ---------- A14 ----------

@pytest.mark.planted
def test_A14_second_run_noop_file_unchanged(ledger, lpath, clock):
    append_at(ledger, clock, "2026-11-19T20:00:00")
    append_at(ledger, clock, "2026-11-20T12:00:00")
    clock.set("2026-11-21T10:00:00")
    ledger.write_roots()
    before = roots_path(lpath).read_bytes()
    assert ledger.write_roots() == []
    assert roots_path(lpath).read_bytes() == before


@pytest.mark.planted
def test_A14b_later_day_appends_only_new_root_earlier_lines_identical(ledger, lpath, clock):
    append_at(ledger, clock, "2026-11-20T12:00:00")
    clock.set("2026-11-21T10:00:00")
    ledger.write_roots()
    before = roots_path(lpath).read_bytes()
    b = append_at(ledger, clock, "2026-11-21T12:00:00")
    append_at(ledger, clock, "2026-11-22T12:00:00")           # IST 22 Nov: open at clock below
    clock.set("2026-11-22T13:00:00")
    new = ledger.write_roots()
    assert [(r.date, r.seq, r.hash) for r in new][0] == ("2026-11-21", 2, b.hash)
    assert [r.date for r in new] == ["2026-11-21"]
    after = roots_path(lpath).read_bytes()
    assert after.startswith(before) and after[len(before):].count(b"\n") == 1
    assert [r.date for r in ledger.roots()] == ["2026-11-20", "2026-11-21"]


@pytest.mark.planted
def test_A14c_chain_tamper_under_existing_root_blocks_write_roots(ledger, lpath, clock):
    from investos.registry.chain import LedgerCorruptError, RootMismatchError
    append_at(ledger, clock, "2026-11-20T10:00:00")
    append_at(ledger, clock, "2026-11-20T11:00:00")
    clock.set("2026-11-21T10:00:00")
    ledger.write_roots()
    append_at(ledger, clock, "2026-11-21T11:00:00")           # row of a day that would close below
    edit_line(lpath, 0, lambda o: o["payload"].__setitem__("at", "evil"))
    clock.set("2026-11-23T10:00:00")
    roots_before = roots_path(lpath).read_bytes()
    with pytest.raises((LedgerCorruptError, RootMismatchError)):
        ledger.write_roots()
    assert roots_path(lpath).read_bytes() == roots_before


@pytest.mark.planted
def test_A14d_forged_tail_with_valid_chain_blocks_write_roots(ledger, lpath, clock):
    from investos.registry.chain import LedgerCorruptError, RootMismatchError
    append_at(ledger, clock, "2026-11-20T10:00:00")
    append_at(ledger, clock, "2026-11-20T11:00:00")
    clock.set("2026-11-21T10:00:00")
    ledger.write_roots()
    append_at(ledger, clock, "2026-11-21T11:00:00")
    # rewrite row 2 (last of its root day) and fix the links of row 3 so the chain itself stays valid
    lines = read_lines(lpath)
    o2 = json.loads(lines[1]); o2["payload"]["at"] = "evil"; rehash(o2); lines[1] = (canon(o2) + "\n").encode()
    o3 = json.loads(lines[2]); o3["prev_hash"] = o2["hash"]; rehash(o3); lines[2] = (canon(o3) + "\n").encode()
    write_lines(lpath, lines)
    assert ledger.verify(roots=False).ok
    clock.set("2026-11-23T10:00:00")
    roots_before = roots_path(lpath).read_bytes()
    with pytest.raises((LedgerCorruptError, RootMismatchError)):
        ledger.write_roots()
    assert roots_path(lpath).read_bytes() == roots_before


@pytest.mark.planted
def test_A14e_only_todays_rows_no_root(ledger, lpath, clock):
    append_at(ledger, clock, "2026-11-21T08:00:00")
    clock.set("2026-11-21T10:00:00")
    assert ledger.write_roots() == []
    assert ledger.roots() == []
    assert not roots_path(lpath).exists() or roots_path(lpath).read_bytes() == b""


def test_A14f_roots_path_is_ledger_path_with_roots_suffix(five, lpath, clock):
    clock.set("2026-11-21T10:00:00")
    five.write_roots()
    assert roots_path(lpath).name == "test.roots.jsonl" and roots_path(lpath).exists()


# ---------- A15 / A16: roots verification ----------
# Fixture: 6 rows, two per IST day 18/19/20 Nov (seq 1-2, 3-4, 5-6); roots (18,2) (19,4) (20,6).

@pytest.fixture
def rooted(lpath, clock):
    led = new_ledger(lpath, clock)
    rows = []
    for day in (18, 19, 20):
        rows.append(append_at(led, clock, f"2026-11-{day}T10:00:00"))
        rows.append(append_at(led, clock, f"2026-11-{day}T11:00:00"))
    clock.set("2026-11-21T10:00:00")
    new = led.write_roots()
    assert [(r.date, r.seq) for r in new] == [("2026-11-18", 2), ("2026-11-19", 4), ("2026-11-20", 6)]
    assert led.verify().ok
    return led, rows


def _edit_root(lpath, idx, fn):
    rp = roots_path(lpath)
    lines = read_lines(rp)
    o = json.loads(lines[idx]); fn(o)
    lines[idx] = (canon(o) + "\n").encode()
    write_lines(rp, lines)


@pytest.mark.planted
def test_A15_root_with_wrong_hash(rooted, lpath):
    led, rows = rooted
    _edit_root(lpath, 1, lambda o: o.__setitem__("hash", "ab" * 32))
    res = led.verify()
    assert not res.ok and res.scope == "roots" and res.first_broken_seq == 4
    assert "root mismatch" in res.reason


@pytest.mark.planted
def test_A15b_root_seq_beyond_row_count(rooted, lpath):
    led, rows = rooted
    _edit_root(lpath, 2, lambda o: o.__setitem__("seq", 99))
    res = led.verify()
    assert not res.ok and res.scope == "roots" and res.first_broken_seq == 99
    assert "ledger truncated" in res.reason


@pytest.mark.planted
def test_A15c_root_date_not_matching_ist_date_of_row(rooted, lpath):
    led, rows = rooted
    _edit_root(lpath, 2, lambda o: o.__setitem__("date", "2026-11-30"))
    res = led.verify()
    assert not res.ok and res.scope == "roots" and res.first_broken_seq == 6


@pytest.mark.planted
def test_A15d_root_pointing_at_row_not_last_of_day(rooted, lpath):
    led, rows = rooted
    first_of_day19 = rows[2]
    assert first_of_day19.seq == 3
    _edit_root(lpath, 1, lambda o: o.update(seq=3, hash=first_of_day19.hash))
    res = led.verify()
    assert not res.ok and res.scope == "roots" and res.first_broken_seq == 3
    assert "root not last of day" in res.reason


@pytest.mark.planted
def test_A15e_duplicated_root_line_date_not_increasing(rooted, lpath):
    led, rows = rooted
    rp = roots_path(lpath)
    lines = read_lines(rp)
    write_lines(rp, [lines[0], lines[1], lines[1], lines[2]])
    res = led.verify()
    assert not res.ok and res.scope == "roots"
    assert res.first_broken_seq in (None, 4)


@pytest.mark.planted
def test_A15f_unsorted_root_dates(rooted, lpath):
    led, rows = rooted
    rp = roots_path(lpath)
    lines = read_lines(rp)
    write_lines(rp, [lines[1], lines[0], lines[2]])
    res = led.verify()
    assert not res.ok and res.scope == "roots"
    assert res.first_broken_seq in (None, 2, 4)


@pytest.mark.planted
def test_A15g_invalid_json_root_line(rooted, lpath):
    led, rows = rooted
    rp = roots_path(lpath)
    lines = read_lines(rp)
    lines[1] = b"{broken\n"
    write_lines(rp, lines)
    res = led.verify()
    assert not res.ok and res.scope == "roots" and res.first_broken_seq is None
    assert res.reason


def test_A15h_valid_roots_ok_and_roots_false_skips_them(rooted, lpath):
    led, rows = rooted
    assert led.verify().ok
    _edit_root(lpath, 0, lambda o: o.__setitem__("hash", "ab" * 32))
    assert led.verify(roots=False).ok
    assert not led.verify(roots=True).ok


def test_A15i_absent_roots_file_is_ok_and_missing_roots_not_a_failure(five, lpath, clock):
    clock.set("2026-11-25T10:00:00")        # day 20 is closed but no root written yet
    assert not roots_path(lpath).exists()
    res = five.verify()
    assert res.ok and res.rows == 5


@pytest.mark.planted
def test_A16_chain_break_row2_beats_root_mismatch_seq4(rooted, lpath):
    led, rows = rooted
    edit_line(lpath, 1, lambda o: o["payload"].__setitem__("at", "evil"))
    _edit_root(lpath, 1, lambda o: o.__setitem__("hash", "ab" * 32))
    res = led.verify()
    assert not res.ok and res.first_broken_seq == 2


@pytest.mark.planted
def test_A16b_smallest_root_finding_is_reported(rooted, lpath):
    led, rows = rooted
    _edit_root(lpath, 2, lambda o: o.__setitem__("hash", "cd" * 32))   # seq 6
    _edit_root(lpath, 0, lambda o: o.__setitem__("hash", "ab" * 32))   # seq 2
    res = led.verify()
    assert not res.ok and res.scope == "roots" and res.first_broken_seq == 2
