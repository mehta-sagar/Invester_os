"""S0-T6 acceptance tests A3-A5, A10-A12, A17-A19, A22, A23 for investos.registry.chain (rules R2-R4, R7, R8, R11, R12)."""
from __future__ import annotations

import ast
import hashlib
import json
import os
import re
import subprocess
import sys
from pathlib import Path

import pytest

from _registry_helpers import (ZERO, FakeClock, canon, dump_line, edit_line, fill, new_ledger,
                               read_lines, rehash, row_hash, write_lines)

REPO = Path(__file__).resolve().parents[3]
TS_RE = re.compile(r"^\d{4}-\d\d-\d\dT\d\d:\d\d:\d\d\.\d{6}Z$")


# ---------- A3 / A4: hash, genesis, numbering, line format ----------

def test_A3_row_hash_matches_published_vector(ledger, clock):
    clock.set("2026-01-01T00:00:00")
    row = ledger.append({"a": 1})
    text = ('{"payload":{"a":1},"prev_hash":"' + ZERO + '","seq":1,"ts":"2026-01-01T00:00:00.000000Z"}')
    assert (row.seq, row.ts, row.prev_hash) == (1, "2026-01-01T00:00:00.000000Z", ZERO)
    assert row.hash == hashlib.sha256(text.encode("utf-8")).hexdigest()
    assert re.fullmatch(r"[0-9a-f]{64}", row.hash)


def test_A4_three_appends_seq_genesis_links_and_lines(ledger, lpath, clock):
    rows = fill(ledger, clock, 3)
    assert [r.seq for r in rows] == [1, 2, 3]
    assert rows[0].prev_hash == "0" * 64
    assert rows[1].prev_hash == rows[0].hash and rows[2].prev_hash == rows[1].hash
    lines = read_lines(lpath)
    assert len(lines) == 3
    for r, line in zip(rows, lines):
        obj = {"seq": r.seq, "ts": r.ts, "prev_hash": r.prev_hash, "payload": dict(r.payload), "hash": r.hash}
        assert line == (canon(obj) + "\n").encode("utf-8")
        assert r.hash == row_hash(r.seq, r.ts, r.prev_hash, dict(r.payload))
        assert TS_RE.match(r.ts)
    assert ledger.verify(roots=False).ok
    assert ledger.head() == rows[-1]
    assert [r for r in ledger.rows()] == rows


def test_A4b_file_created_on_first_append_and_key_order_irrelevant(lpath, clock):
    led = new_ledger(lpath, clock)
    assert not lpath.exists()
    led.append({"b": 1, "a": {"y": 1, "x": 2}})
    assert json.loads(lpath.read_bytes())["payload"] == {"a": {"x": 2, "y": 1}, "b": 1}
    assert b'"payload":{"a":{"x":2,"y":1},"b":1}' in lpath.read_bytes()


def test_A4c_ts_is_clock_in_utc_with_microseconds(ledger, clock):
    clock.set("2026-11-20T10:00:00.123456")
    assert ledger.append({"k": 1}).ts == "2026-11-20T10:00:00.123456Z"


def test_A22_devanagari_stored_as_utf8_and_verifies(ledger, lpath):
    row = ledger.append({"hypothesis": "सिग्नल"})
    raw = lpath.read_bytes()
    assert "सिग्नल".encode("utf-8") in raw and b"\\u0938" not in raw
    assert ledger.verify(roots=False).ok
    assert row.hash == row_hash(row.seq, row.ts, row.prev_hash, {"hypothesis": "सिग्नल"})
    assert ledger.rows()[0].payload["hypothesis"] == "सिग्नल"


# ---------- A5: append refusals ----------

@pytest.mark.planted
def test_A5_append_on_edited_chain_refused_and_file_unchanged(five, lpath):
    from investos.registry.chain import LedgerCorruptError
    edit_line(lpath, 1, lambda o: o["payload"].__setitem__("n", 999))
    before = lpath.read_bytes()
    with pytest.raises(LedgerCorruptError) as ei:
        five.append({"n": 6})
    assert ei.value.seq == 2
    assert lpath.read_bytes() == before


@pytest.mark.planted
def test_A5b_clock_moved_backwards(five, lpath, clock):
    from investos.registry.chain import LedgerError
    before = lpath.read_bytes()
    clock.set("2026-11-20T09:59:59")
    with pytest.raises(LedgerError, match="clock moved backwards"):
        five.append({"n": 6})
    assert lpath.read_bytes() == before


@pytest.mark.planted
def test_A5c_float_in_payload_refused_nothing_written(ledger, lpath):
    from investos.registry.canon import CanonicalJsonError
    from investos.registry.chain import PayloadError
    with pytest.raises((CanonicalJsonError, PayloadError)):
        ledger.append({"x": 0.5})
    assert not lpath.exists() or lpath.read_bytes() == b""


@pytest.mark.planted
def test_A5d_naive_clock_is_value_error_nothing_written(lpath):
    from datetime import datetime
    led = new_ledger(lpath, clock=lambda: datetime(2026, 11, 20, 10, 0, 0))
    with pytest.raises(ValueError):
        led.append({"n": 1})
    assert not lpath.exists() or lpath.read_bytes() == b""


def test_A5e_validator_failure_writes_nothing(lpath, clock):
    from investos.registry.chain import PayloadError

    def v(payload, led):
        raise PayloadError("nope")
    led = new_ledger(lpath, clock, validate=v)
    with pytest.raises(PayloadError):
        led.append({"n": 1})
    assert not lpath.exists() or lpath.read_bytes() == b""


def test_A5f_exception_hierarchy():
    from investos.registry.chain import LedgerCorruptError, LedgerError, PayloadError, RootMismatchError
    assert issubclass(LedgerCorruptError, LedgerError) and issubclass(PayloadError, LedgerError)
    assert issubclass(RootMismatchError, LedgerError)


# ---------- A10: verify, single-row tampering ----------

def _m_payload(lines):
    o = json.loads(lines[2]); o["payload"]["n"] = 999; lines[2] = dump_line(o)


def _m_ts(lines):
    o = json.loads(lines[2]); o["ts"] = "2026-11-20T10:00:02.500000Z"; lines[2] = dump_line(o)


def _m_hash(lines):
    o = json.loads(lines[2]); o["hash"] = "ab" * 32; lines[2] = dump_line(o)


def _m_prev_hash(lines):
    o = json.loads(lines[2]); o["prev_hash"] = "cd" * 32; lines[2] = dump_line(o)


def _m_upper(lines):
    o = json.loads(lines[2])
    assert o["hash"].upper() != o["hash"]
    o["hash"] = o["hash"].upper(); lines[2] = dump_line(o)


def _m_blank(lines):
    lines.insert(2, b"\n")


def _m_badjson(lines):
    lines[3] = b"{not json\n"


def _m_extra_key(lines):
    o = json.loads(lines[1]); o["x"] = 1; lines[1] = dump_line(o)


def _m_no_final_nl(lines):
    lines[-1] = lines[-1][:-1]


def _m_cr(lines):
    lines[1] = lines[1][:-1] + b"\r\n"


def _m_ts_backwards_rehashed(lines):
    o = json.loads(lines[2]); o["ts"] = "2026-11-20T09:00:00.000000Z"; lines[2] = dump_line(rehash(o))


def _m_not_object(lines):
    lines[2] = b"[1,2,3]\n"


def _m_seq_wrong_rehashed(lines):
    o = json.loads(lines[2]); o["seq"] = 7; lines[2] = dump_line(rehash(o))


@pytest.mark.planted
@pytest.mark.parametrize("mutate,expected", [
    pytest.param(_m_payload, 3, id="payload_row3"),
    pytest.param(_m_ts, 3, id="ts_row3"),
    pytest.param(_m_hash, 3, id="hash_row3"),
    pytest.param(_m_prev_hash, 3, id="prev_hash_row3"),
    pytest.param(_m_upper, 3, id="hash_uppercase_row3"),
    pytest.param(_m_blank, 3, id="blank_line_after_row2"),
    pytest.param(_m_badjson, 4, id="invalid_json_line4"),
    pytest.param(_m_extra_key, 2, id="extra_key_row2"),
    pytest.param(_m_no_final_nl, 5, id="no_final_newline"),
    pytest.param(_m_cr, 2, id="carriage_return_line2"),
    pytest.param(_m_ts_backwards_rehashed, 3, id="ts_earlier_than_previous_valid_hash"),
    pytest.param(_m_not_object, 3, id="line_not_an_object"),
    pytest.param(_m_seq_wrong_rehashed, 3, id="seq_not_position_valid_hash"),
])
def test_A10_verify_reports_first_broken_seq(five, lpath, mutate, expected):
    res = five.verify(roots=False)
    assert res.ok and res.rows == 5 and res.first_broken_seq is None and res.reason == ""
    lines = read_lines(lpath)
    mutate(lines)
    write_lines(lpath, lines)
    res = five.verify(roots=False)
    assert not res.ok
    assert res.first_broken_seq == expected
    assert res.scope == "chain"
    assert res.reason


# ---------- A11 / A8-of-brief: exit gate demo 4 (structural tampering) ----------

@pytest.mark.planted
def test_A11_delete_middle_row(five, lpath):
    lines = read_lines(lpath)
    del lines[2]
    write_lines(lpath, lines)
    assert five.verify(roots=False).first_broken_seq == 3


@pytest.mark.planted
def test_A11b_delete_first_row(five, lpath):
    lines = read_lines(lpath)
    del lines[0]
    write_lines(lpath, lines)
    assert five.verify(roots=False).first_broken_seq == 1


@pytest.mark.planted
def test_A11c_swap_rows_2_and_3(five, lpath):
    lines = read_lines(lpath)
    lines[1], lines[2] = lines[2], lines[1]
    write_lines(lpath, lines)
    res = five.verify(roots=False)
    assert not res.ok and res.first_broken_seq == 2 and res.scope == "chain"


@pytest.mark.planted
def test_A11d_insert_copy_of_row2_between_4_and_5(five, lpath):
    lines = read_lines(lpath)
    lines.insert(4, lines[1])
    write_lines(lpath, lines)
    assert five.verify(roots=False).first_broken_seq == 5


@pytest.mark.planted
def test_A11e_insert_foreign_well_formed_row(five, lpath):
    """A row built by someone else with a self-consistent hash but the wrong link is caught at its position."""
    lines = read_lines(lpath)
    foreign = rehash({"seq": 3, "ts": "2026-11-20T10:00:01.500000Z", "prev_hash": "ee" * 32,
                      "payload": {"n": 77, "tag": "foreign"}, "hash": ""})
    lines.insert(2, dump_line(foreign))
    write_lines(lpath, lines)
    assert five.verify(roots=False).first_broken_seq == 3


@pytest.mark.planted
def test_A11f_edit_row3_with_recomputed_own_hash_caught_at_row4(five, lpath):
    edit_line(lpath, 2, lambda o: o["payload"].__setitem__("n", 999), redo_hash=True)
    res = five.verify(roots=False)
    assert not res.ok and res.first_broken_seq == 4 and res.scope == "chain"


@pytest.mark.planted
def test_A11g_truncated_last_line(five, lpath):
    data = lpath.read_bytes()
    lpath.write_bytes(data[:-10])
    res = five.verify(roots=False)
    assert not res.ok and res.first_broken_seq == 5


@pytest.mark.planted
def test_A11h_tail_edit_with_recomputed_hash_not_caught_by_chain_alone(five, lpath):
    """Documented limit of the chain alone (R8); only committed daily roots (R10) cover it."""
    edit_line(lpath, 4, lambda o: o["payload"].__setitem__("n", 555), redo_hash=True)
    res = five.verify(roots=False)
    assert res.ok and res.rows == 5


@pytest.mark.planted
def test_A11i_deleting_final_rows_not_caught_by_chain_alone(five, lpath):
    lines = read_lines(lpath)
    write_lines(lpath, lines[:3])
    res = five.verify(roots=False)
    assert res.ok and res.rows == 3


@pytest.mark.planted
def test_A11j_tail_edit_caught_by_committed_root(five, lpath, clock):
    """Exit-gate demo 4, tail-edit leg: root written on the next IST day catches the rewritten final row."""
    clock.set("2026-11-21T10:00:00")
    roots = five.write_roots()
    assert [(r.date, r.seq) for r in roots] == [("2026-11-20", 5)]
    edit_line(lpath, 4, lambda o: o["payload"].__setitem__("n", 555), redo_hash=True)
    assert five.verify(roots=False).ok
    res = five.verify(roots=True)
    assert not res.ok and res.scope == "roots" and res.first_broken_seq == 5
    assert "root mismatch" in res.reason


@pytest.mark.planted
def test_A11k_deleting_last_row_caught_by_root_as_truncated(five, lpath, clock):
    clock.set("2026-11-21T10:00:00")
    five.write_roots()
    lines = read_lines(lpath)
    write_lines(lpath, lines[:4])
    assert five.verify(roots=False).ok
    res = five.verify(roots=True)
    assert not res.ok and res.scope == "roots" and res.first_broken_seq == 5
    assert "ledger truncated" in res.reason


# ---------- A12: empty / absent ----------

def test_A12_absent_file(ledger, lpath):
    assert not lpath.exists()
    res = ledger.verify()
    assert res.ok and res.rows == 0 and res.scope == "none" and res.first_broken_seq is None
    assert ledger.head() is None and ledger.rows() == []


def test_A12b_empty_file(ledger, lpath):
    lpath.write_bytes(b"")
    res = ledger.verify(roots=False)
    assert res.ok and res.rows == 0 and res.scope == "none"
    assert ledger.head() is None and ledger.rows() == []


# ---------- A17: append-only API surface ----------

@pytest.mark.planted
def test_A17_public_surface_is_exactly_the_append_only_api():
    from investos.registry.chain import Ledger
    public = {n for n in dir(Ledger) if not n.startswith("_")}
    assert public == {"append", "head", "kind", "path", "roots", "rows", "verify", "write_roots"}


@pytest.mark.planted
def test_A17b_source_has_no_destructive_calls_or_write_modes():
    import investos.registry as pkg
    pkg_dir = Path(pkg.__file__).parent
    # check.py legitimately hosts the planted-failure function (rewrites a temp copy), so it is excluded.
    files = [pkg_dir / n for n in ("chain.py", "canon.py", "ledgers.py", "__init__.py") if (pkg_dir / n).exists()]
    assert (pkg_dir / "chain.py") in files
    bad_attrs = {"unlink", "truncate", "rename", "rmtree", "remove", "write_text", "write_bytes"}
    problems = []
    append_open_in_chain = 0
    for f in files:
        tree = ast.parse(f.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call):
                continue
            fn = node.func
            name = fn.attr if isinstance(fn, ast.Attribute) else getattr(fn, "id", None)
            recv = getattr(getattr(fn, "value", None), "id", None)
            if name in bad_attrs:
                problems.append(f"{f.name}:{node.lineno} call {name}")
            if name == "replace" and recv == "os":
                problems.append(f"{f.name}:{node.lineno} os.replace")
            if name == "open":
                mode = None
                if len(node.args) >= 2 and isinstance(node.args[1], ast.Constant):
                    mode = node.args[1].value
                if len(node.args) >= 1 and recv is not None and recv != "os" and isinstance(node.args[0], ast.Constant) \
                        and isinstance(node.args[0].value, str) and not isinstance(fn, ast.Name):
                    mode = node.args[0].value  # Path.open("ab")
                for kw in node.keywords:
                    if kw.arg == "mode" and isinstance(kw.value, ast.Constant):
                        mode = kw.value.value
                if mode is None:
                    mode = "r"
                if mode not in ("r", "rb", "a", "ab"):
                    problems.append(f"{f.name}:{node.lineno} open mode {mode!r}")
                if "a" in mode:
                    if f.name == "chain.py":
                        append_open_in_chain += 1
                    else:
                        problems.append(f"{f.name}:{node.lineno} append-open outside chain.py")
    assert not problems, problems
    assert append_open_in_chain >= 1


# ---------- A18: rows()/head() never return partial data ----------

@pytest.mark.planted
def test_A18_rows_and_head_raise_on_corrupt_chain(five, lpath):
    from investos.registry.chain import LedgerCorruptError
    edit_line(lpath, 2, lambda o: o["payload"].__setitem__("n", 999))
    with pytest.raises(LedgerCorruptError) as e1:
        five.rows()
    assert e1.value.seq == 3
    with pytest.raises(LedgerCorruptError) as e2:
        five.head()
    assert e2.value.seq == 3


def test_A18b_rows_and_head_on_healthy_chain(five):
    rows = five.rows()
    assert [r.seq for r in rows] == [1, 2, 3, 4, 5]
    assert five.head() == rows[-1]
    assert five.path.name == "test.jsonl" and five.kind == "test"


# ---------- A19: concurrency ----------

_WORKER = r"""
import sys
from pathlib import Path
from investos.registry.chain import Ledger
path, k, m = sys.argv[1], int(sys.argv[2]), int(sys.argv[3])
led = Ledger(Path(path), kind="test", validate=lambda payload, ledger: None)
for i in range(m):
    led.append({"proc": k, "i": i})
"""


def test_A19_four_processes_25_appends_each(tmp_path):
    path = tmp_path / "conc.jsonl"
    env = {**os.environ, "PYTHONPATH": str(REPO / "src")}
    procs = [subprocess.Popen([sys.executable, "-c", _WORKER, str(path), str(k), "25"], env=env,
                              stdout=subprocess.PIPE, stderr=subprocess.PIPE) for k in range(4)]
    for p in procs:
        out, err = p.communicate(timeout=300)
        assert p.returncode == 0, err.decode()
    from investos.registry.chain import Ledger
    led = Ledger(path, kind="test", validate=lambda payload, ledger: None)
    res = led.verify(roots=False)
    assert res.ok and res.rows == 100
    data = path.read_bytes()
    assert data.endswith(b"\n") and data.count(b"\n") == 100
    objs = [json.loads(line) for line in data.split(b"\n")[:-1]]
    assert [o["seq"] for o in objs] == list(range(1, 101))
    assert sorted((o["payload"]["proc"], o["payload"]["i"]) for o in objs) == \
        sorted((k, i) for k in range(4) for i in range(25))


# ---------- A23: same-microsecond appends ----------

@pytest.mark.planted
def test_A23_same_microsecond_ok_one_microsecond_earlier_refused(ledger, lpath, clock):
    from investos.registry.chain import LedgerError
    clock.set("2026-11-20T10:00:00.000500")
    a = ledger.append({"n": 1})
    b = ledger.append({"n": 2})
    assert a.ts == b.ts
    before = lpath.read_bytes()
    clock.set("2026-11-20T10:00:00.000499")
    with pytest.raises(LedgerError):
        ledger.append({"n": 3})
    assert lpath.read_bytes() == before
    assert ledger.verify(roots=False).ok


# ---------- A10b: canonical-form rule (R7, amended 21 Nov): stored bytes must equal canonical JSON + "\n" ----------

def _nc_space_after_colon(lines):
    lines[2] = lines[2].replace(b'"n":', b'"n": ', 1)


def _nc_reordered_keys(lines):
    o = json.loads(lines[2])
    lines[2] = (json.dumps({k: o[k] for k in ("hash", "payload", "prev_hash", "seq", "ts")},
                           separators=(",", ":"), ensure_ascii=False) + "\n").encode("utf-8")


def _nc_escaped_e_acute(lines):
    o = json.loads(lines[2])
    assert "\u00e9" not in lines[2].decode()
    # payload changes from "x" to "é" with a valid recomputed hash, but stored with the \u escape
    o["payload"]["tag"] = "é"
    rehash(o)
    line = (canon(o) + "\n").encode("utf-8")
    assert "é".encode("utf-8") in line
    lines[2] = line.replace("é".encode("utf-8"), b"\\u00e9")


def _nc_trailing_space(lines):
    lines[2] = lines[2][:-1] + b" \n"


def _nc_json_dumps_default_separators(lines):
    o = json.loads(lines[2])
    lines[2] = (json.dumps(o, sort_keys=True) + "\n").encode("utf-8")


@pytest.mark.planted
@pytest.mark.parametrize("mutate", [
    pytest.param(_nc_space_after_colon, id="space_after_colon"),
    pytest.param(_nc_reordered_keys, id="reordered_keys"),
    pytest.param(_nc_escaped_e_acute, id="escaped_e_acute_vs_literal"),
    pytest.param(_nc_trailing_space, id="trailing_space_before_newline"),
    pytest.param(_nc_json_dumps_default_separators, id="default_json_separators"),
])
def test_A10b_non_canonical_stored_line_is_a_break(five, lpath, mutate):
    assert five.verify(roots=False).ok
    lines = read_lines(lpath)
    mutate(lines)
    write_lines(lpath, lines)
    res = five.verify(roots=False)
    assert not res.ok and res.first_broken_seq == 3 and res.scope == "chain" and res.reason


@pytest.mark.planted
def test_A10c_non_canonical_line_blocks_append_and_rows(five, lpath):
    from investos.registry.chain import LedgerCorruptError
    lines = read_lines(lpath)
    _nc_space_after_colon(lines)
    write_lines(lpath, lines)
    before = lpath.read_bytes()
    with pytest.raises(LedgerCorruptError) as e1:
        five.append({"n": 6, "tag": "row"})
    assert e1.value.seq == 3 and lpath.read_bytes() == before
    with pytest.raises(LedgerCorruptError):
        five.rows()
