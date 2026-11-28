"""S1-T8: the window ledger (R9, A19) and the shape of its lines."""
from __future__ import annotations

import json
import threading
from datetime import date

import pytest

from _backfill_helpers import BfEnv, ledger_line, day


@pytest.fixture
def e(tmp_path):
    return BfEnv(tmp_path)


def key(start="2026-11-20", end="2026-11-20", part="", eid="announcements"):
    from investos.backfill.ledger import WindowKey
    return WindowKey(eid, day(start), day(end), part)


# ------------------------------------------------------------------ the key
def test_R11_logical_key_formats():
    assert key("2016-03-07", "2016-03-13").logical_key() == "announcements/2016-03-07_2016-03-13"
    assert key("2016-03-07", "2016-03-13", "ab").logical_key() == "announcements/2016-03-07_2016-03-13/ab"


def test_R9_window_key_is_hashable_and_compares_by_value():
    assert key() == key() and len({key(), key(), key(part="a")}) == 2


# ------------------------------------------------------------------ writer
def test_R9_append_writes_one_canonical_line_per_event_and_state_is_the_last_line(e):
    L = e.wl("announcements")
    L.append("fetched", key(), rows=3, sha256="a" * 64, logical_key=key().logical_key(), run_id="r1")
    L.append("split", key("2026-11-21", "2026-11-22"), reason="page limit 70 reached", run_id="r1")
    raw = e.ledger_path("announcements").read_bytes()
    assert raw.endswith(b"\n") and raw.count(b"\n") == 2
    first = json.loads(raw.split(b"\n")[0])
    assert set(first) == {"schema", "ts", "date", "endpoint", "start", "end", "part", "event", "rows", "sha256",
                          "logical_key", "run_id", "probe_hash", "reason"}
    assert first["schema"] == 1 and first["endpoint"] == "announcements" and first["event"] == "fetched"
    assert first["ts"].endswith("Z") and len(first["ts"].split(".")[1]) == 7          # 6 digits + Z
    assert raw.split(b"\n")[0].decode() == json.dumps(first, sort_keys=True, separators=(",", ":"))
    st = L.state()
    assert st[key()].event == "fetched" and st[key()].rows == 3 and st[key()].sha256 == "a" * 64
    L.append("failed", key(), reason="boom", run_id="r2")
    assert L.state()[key()].event == "failed" and L.state()[key()].reason == "boom"


def test_R9_date_field_is_the_ist_date_of_ts(e):
    e.at(__import__("_backfill_helpers").ist(2026, 11, 29, 0, 35))     # 2026-11-28T19:05Z: UTC date is the 28th
    e.wl("announcements").append("failed", key(), reason="x")
    line = e.lines("announcements")[0]
    assert line["ts"].startswith("2026-11-28T19:05") and line["date"] == "2026-11-29"


def test_R9_writer_refuses_a_file_whose_last_byte_is_not_a_newline(e):
    from investos.backfill.ledger import LedgerError
    p = e.ledger_path("announcements")
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_bytes(json.dumps(ledger_line()).encode())                        # no trailing newline: a torn tail
    before = p.read_bytes()
    with pytest.raises(LedgerError):
        e.wl("announcements").append("failed", key("2026-11-21", "2026-11-21"), reason="x")
    assert p.read_bytes() == before


def test_R9_two_threads_appending_concurrently_write_whole_lines_only(e):
    L1, L2 = e.wl("announcements"), e.wl("announcements")

    def work(L, n):
        for i in range(25):
            L.append("failed", key(f"2026-10-{n:02d}", f"2026-10-{n:02d}"), reason=f"t{n}-{i}" * 20)
    ts = [threading.Thread(target=work, args=(L1, 1)), threading.Thread(target=work, args=(L2, 2))]
    [t.start() for t in ts]
    [t.join() for t in ts]
    lines = e.ledger_path("announcements").read_bytes().split(b"\n")
    assert lines[-1] == b"" and len(lines) == 51
    for l in lines[:-1]:
        assert json.loads(l)["event"] == "failed"


# ------------------------------------------------------------------ reader (A19)
BAD_LINES = {
    "unknown_field": lambda: dict(ledger_line(), extra=1),
    "missing_field": lambda: {k: v for k, v in ledger_line().items() if k != "reason"},
    "unknown_event": lambda: ledger_line(event="weird"),
    "schema_2": lambda: ledger_line(schema=2),
    "date_not_ist_date_of_ts": lambda: ledger_line(ts="2026-11-28T19:05:00.000000Z", date="2026-11-28"),
    "start_after_end": lambda: ledger_line(start="2026-11-21", end="2026-11-20"),
    "fetched_without_sha": lambda: ledger_line(sha256=""),
    "fetched_without_rows": lambda: ledger_line(rows=None),
    "fetched_without_logical_key": lambda: ledger_line(logical_key=""),
    "bad_part_upper": lambda: ledger_line(part="A"),
    "bad_part_digit": lambda: ledger_line(part="a1"),
    "bad_ts_format": lambda: dict(ledger_line(), ts="2026-11-29 01:00:00"),
    "negative_rows": lambda: ledger_line(rows=-1),
}


@pytest.mark.parametrize("name", sorted(BAD_LINES))
def test_A19_reader_rejects_each_bad_line_and_never_skips_it(e, name):
    from investos.backfill.ledger import LedgerError
    e.put_lines("announcements", ledger_line(start="2026-11-19", end="2026-11-19"), BAD_LINES[name]())
    with pytest.raises(LedgerError):
        e.wl("announcements").state()


def test_A19_invalid_json_and_blank_lines_are_errors(e):
    from investos.backfill.ledger import LedgerError
    p = e.ledger_path("announcements")
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_bytes(json.dumps(ledger_line()).encode() + b"\n{not json}\n")
    with pytest.raises(LedgerError):
        e.wl("announcements").state()


def test_A19_torn_tail_that_persists_raises(e):
    from investos.backfill.ledger import LedgerError
    p = e.ledger_path("announcements")
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_bytes(json.dumps(ledger_line()).encode() + b"\n" + json.dumps(ledger_line(start="2026-11-21", end="2026-11-21")).encode()[:40])
    with pytest.raises(LedgerError):
        e.wl("announcements").state()


def test_A19_missing_file_is_an_empty_ledger(e):
    assert dict(e.wl("announcements").state()) == {}


def test_R9_state_ignores_audit_shrink_lines_and_keeps_children_apart(e):
    e.put_lines("announcements",
                ledger_line(start="2026-11-20", end="2026-11-23", event="fetched", rows=9),
                ledger_line(start="2026-11-20", end="2026-11-21", part="a", rows=5),
                ledger_line(start="2026-11-22", end="2026-11-23", part="b", rows=6),
                ledger_line(start="2026-11-20", end="2026-11-23", event="audit_shrink", reason="max_window_days=2"),
                ledger_line(start="2026-11-20", end="2026-11-23", event="superseded", reason="audit"))
    st = e.wl("announcements").state()
    assert st[key("2026-11-20", "2026-11-23")].event == "superseded"
    assert st[key("2026-11-20", "2026-11-21", "a")].event == "fetched"
    assert len(st) == 3
