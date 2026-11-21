"""S0-FIX A14 (short writes) and A17 (X5 roots newline, X8 decision date)."""
from __future__ import annotations

import io
import os

import pytest

from _registry_helpers import FakeClock, fill, new_ledger


def _short_write_spy(monkeypatch, target, chunk=7):
    """Make every write to the file `target` (via builtins.open in chain, or os.write) partial."""
    from investos.registry import chain
    real_open, real_write = open, os.write
    calls = {"n": 0}

    class Partial:
        def __init__(self, fh):
            self._fh = fh

        def write(self, b):
            calls["n"] += 1
            return self._fh.write(bytes(b)[:chunk])          # short write, honest return value

        def __getattr__(self, name):
            return getattr(self._fh, name)

        def __enter__(self):
            self._fh.__enter__()
            return self

        def __exit__(self, *a):
            return self._fh.__exit__(*a)

    def fake_open(file, mode="r", *a, **k):
        fh = real_open(file, mode, *a, **k)
        if os.fspath(file) == os.fspath(target) and "b" in mode and ("a" in mode or "w" in mode):
            return Partial(fh)
        return fh

    def fake_write(fd, data):
        try:
            same = os.fstat(fd).st_ino == os.stat(target).st_ino
        except OSError:
            same = False
        if same:
            calls["n"] += 1
            return real_write(fd, bytes(data)[:chunk])
        return real_write(fd, data)

    monkeypatch.setattr(chain, "open", fake_open, raising=False)
    monkeypatch.setattr(os, "write", fake_write)
    return calls


def test_A14_append_loops_on_short_writes(lpath, clock, monkeypatch):
    led = new_ledger(lpath, clock)
    lpath.touch()
    calls = _short_write_spy(monkeypatch, lpath)
    led.append({"n": 1, "tag": "a" * 50})
    clock.advance(seconds=1)
    led.append({"n": 2, "tag": "b"})
    monkeypatch.undo()
    assert calls["n"] >= 2, "short-write spy was never exercised"
    data = lpath.read_bytes()
    assert data.endswith(b"\n") and data.count(b"\n") == 2
    res = new_ledger(lpath, clock).verify()
    assert res.ok and res.rows == 2, res


def test_A14_write_roots_loops_on_short_writes(lpath, clock, monkeypatch):
    led = new_ledger(lpath, clock)
    fill(led, clock, 3)
    roots = lpath.with_suffix(".roots.jsonl")
    roots.touch()
    calls = _short_write_spy(monkeypatch, roots)
    new_ledger(lpath).write_roots()                # real clock: IST 2026-11-20 is a closed day
    monkeypatch.undo()
    assert calls["n"] >= 2, "short-write spy was never exercised"
    assert roots.read_bytes().endswith(b"\n")
    assert new_ledger(lpath, clock).verify(roots=True).ok


# ---- X5: a roots file lacking its final newline is corrupt
def test_X5_roots_file_without_final_newline_is_rejected(five, lpath, clock):
    new_ledger(lpath).write_roots()                # real clock: IST 2026-11-20 is a closed day
    roots = lpath.with_suffix(".roots.jsonl")
    data = roots.read_bytes()
    assert data.endswith(b"\n")
    roots.write_bytes(data[:-1])
    res = new_ledger(lpath, clock).verify(roots=True)
    assert not res.ok and "newline" in res.reason, res


# ---- X8: decision dates must be exactly YYYY-MM-DD
@pytest.mark.parametrize("bad", ["20261121", "2026-W47-6", "2026W476", "2026-11-21 ", " 2026-11-21", "2026-1-21"])
def test_X8_non_canonical_decision_date_rejected(cfg, bad):
    from investos.registry.chain import PayloadError
    from investos.registry.ledgers import log_decision, open_decisions
    led = open_decisions(cfg, clock=FakeClock("2026-11-20T10:00:00"))
    with pytest.raises(PayloadError):
        log_decision(led, date=bad, security_id="SEC1", action="buy", reasons=["r"], model_versions={"a": "v1"})
    assert not led.path.exists() or led.path.read_bytes() == b""


def test_X8_guard_canonical_date_accepted(cfg):
    from investos.registry.ledgers import log_decision, open_decisions
    led = open_decisions(cfg, clock=FakeClock("2026-11-20T10:00:00"))
    log_decision(led, date="2026-11-21", security_id="SEC1", action="buy", reasons=["r"], model_versions={"a": "v1"})
