"""Shared helpers for S0-T6 tests. No investos imports at module level (every test fails
individually, not at collection, while investos.registry is absent)."""
from __future__ import annotations

import hashlib
import json
from datetime import datetime, timedelta, timezone
from pathlib import Path

ZONES = ["data/raw", "data/manifests", "data/canonical", "data/ledger", "reports", "registry"]

VALID_TOML = """\
[general]
timezone = "Asia/Kolkata"

[paths]
raw        = "data/raw"
manifests  = "data/manifests"
canonical  = "data/canonical"
ledger     = "data/ledger"
reports    = "reports"
registry   = "registry"

[db]
migrations_dir = "db/migrations"

[rate_limits]
default_interval_s = 0.35

[rate_limits.hosts]
"www.bseindia.com" = 0.35
"""

BOOTSTRAP_SQL = (
    "CREATE TABLE meta(key VARCHAR PRIMARY KEY, value VARCHAR NOT NULL);\n"
    "INSERT INTO meta VALUES ('schema_family','investos');\n"
)

ZERO = "0" * 64


def make_repo(root: Path) -> Path:
    root.mkdir(parents=True, exist_ok=True)
    (root / "pyproject.toml").write_text("[project]\nname='x'\n")
    for z in ZONES:
        (root / z).mkdir(parents=True, exist_ok=True)
    mig = root / "db" / "migrations"
    mig.mkdir(parents=True, exist_ok=True)
    (mig / "0000_bootstrap.sql").write_text(BOOTSTRAP_SQL)
    (root / "config").mkdir(exist_ok=True)
    (root / "config" / "investos.toml").write_text(VALID_TOML)
    return root


def utc(s: str) -> datetime:
    """'2026-11-20T10:00:00' / '...Z' / '...123456Z' -> aware UTC datetime."""
    return datetime.fromisoformat(s.rstrip("Z") + "+00:00") if "+" not in s else datetime.fromisoformat(s)


class FakeClock:
    """Settable aware-UTC clock."""

    def __init__(self, start: str = "2026-11-20T10:00:00"):
        self.now = utc(start)

    def __call__(self) -> datetime:
        return self.now

    def set(self, s: str) -> "FakeClock":
        self.now = utc(s)
        return self

    def advance(self, **kw) -> "FakeClock":
        self.now = self.now + timedelta(**kw)
        return self


def accept_all(payload, ledger) -> None:  # validator for generic chain tests (kind="test")
    return None


def new_ledger(path: Path, clock=None, kind: str = "test", validate=accept_all):
    from investos.registry.chain import Ledger
    return Ledger(path, kind=kind, validate=validate, clock=clock)


# --- independent (spec-literal) hashing and line helpers; deliberately do not use investos code ---

def canon(obj) -> str:
    return json.dumps(obj, sort_keys=True, separators=(",", ":"), ensure_ascii=False, allow_nan=False)


def row_hash(seq, ts, prev_hash, payload) -> str:
    text = canon({"seq": seq, "ts": ts, "prev_hash": prev_hash, "payload": payload})
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def dump_line(obj: dict) -> bytes:
    return (canon(obj) + "\n").encode("utf-8")


def rehash(obj: dict) -> dict:
    obj["hash"] = row_hash(obj["seq"], obj["ts"], obj["prev_hash"], obj["payload"])
    return obj


def read_lines(path: Path) -> list[bytes]:
    data = path.read_bytes()
    assert data.endswith(b"\n")
    return [part + b"\n" for part in data.split(b"\n")[:-1]]


def write_lines(path: Path, lines: list[bytes]) -> None:
    path.write_bytes(b"".join(lines))


def edit_line(path: Path, idx: int, fn, *, redo_hash: bool = False) -> None:
    """Parse line idx (0-based), apply fn(obj), optionally recompute own hash, write back."""
    lines = read_lines(path)
    obj = json.loads(lines[idx])
    fn(obj)
    if redo_hash:
        rehash(obj)
    lines[idx] = dump_line(obj)
    write_lines(path, lines)


def fill(ledger, clock: FakeClock, n: int, *, start: str | None = None, step_s: int = 1, tag: str = "row"):
    """Append n generic rows, clock stepping step_s seconds between rows."""
    if start:
        clock.set(start)
    out = []
    for i in range(n):
        out.append(ledger.append({"n": i + 1, "tag": tag}))
        clock.advance(seconds=step_s)
    return out
