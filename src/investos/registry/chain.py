"""Append-only hash-chained JSONL ledger with daily roots (spec registry_chain R2-R12).

The only file modes used are read and append. There is no update/delete/rewrite API.
"""
from __future__ import annotations

import fcntl
import json
import os
import re
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Literal, Mapping
from zoneinfo import ZoneInfo

from .canon import CanonicalJsonError, canonical_json, sha256_hex

GENESIS = "0" * 64
_IST = ZoneInfo("Asia/Kolkata")
_HEX64 = re.compile(r"[0-9a-f]{64}", re.ASCII)
_TS = re.compile(r"\d{4}-\d\d-\d\dT\d\d:\d\d:\d\d\.\d{6}Z", re.ASCII)
_DATE = re.compile(r"\d{4}-\d\d-\d\d", re.ASCII)
_ROW_KEYS = frozenset({"seq", "ts", "prev_hash", "payload", "hash"})
_ROOT_KEYS = frozenset({"date", "seq", "hash"})


class LedgerError(Exception):
    pass


class LedgerCorruptError(LedgerError):
    def __init__(self, message: str, seq: int | None = None):
        super().__init__(message)
        self.seq = seq


class PayloadError(LedgerError):
    pass


class RootMismatchError(LedgerError):
    def __init__(self, message: str, seq: int | None = None):
        super().__init__(message)
        self.seq = seq


@dataclass(frozen=True)
class Row:
    seq: int
    ts: str
    prev_hash: str
    payload: Mapping[str, Any]
    hash: str


@dataclass(frozen=True)
class VerifyResult:
    ok: bool
    rows: int
    first_broken_seq: int | None
    scope: Literal["chain", "roots", "none"]
    reason: str


@dataclass(frozen=True)
class Root:
    date: str
    seq: int
    hash: str


def _row_hash(seq: int, ts: str, prev_hash: str, payload: Mapping[str, Any]) -> str:
    return sha256_hex(canonical_json({"seq": seq, "ts": ts, "prev_hash": prev_hash, "payload": payload}))


def _valid_ts(ts: Any) -> bool:
    if type(ts) is not str or not _TS.fullmatch(ts):
        return False
    try:
        datetime.strptime(ts, "%Y-%m-%dT%H:%M:%S.%fZ")
    except ValueError:
        return False
    return True


def _ist_date(ts: str) -> str:
    dt = datetime.strptime(ts, "%Y-%m-%dT%H:%M:%S.%fZ").replace(tzinfo=timezone.utc)
    return dt.astimezone(_IST).date().isoformat()


def _valid_date(s: Any) -> bool:
    if type(s) is not str or not _DATE.fullmatch(s):
        return False
    try:
        return datetime.strptime(s, "%Y-%m-%d").date().isoformat() == s
    except ValueError:
        return False


def _split_lines(data: bytes) -> list[bytes]:
    parts = data.split(b"\n")
    if parts and parts[-1] == b"":
        parts.pop()
        return [p + b"\n" for p in parts]
    return [p + b"\n" for p in parts[:-1]] + [parts[-1]]  # last line unterminated


def _walk(data: bytes) -> tuple[list[Row], tuple[int, str] | None]:
    """Strict chain walk. Returns (well-formed rows before first problem, (position, reason) | None)."""
    rows: list[Row] = []
    if not data:
        return rows, None
    lines = _split_lines(data)
    for p, line in enumerate(lines, start=1):
        body = line[:-1] if line.endswith(b"\n") else line
        if not body:
            return rows, (p, "blank line")
        if b"\r" in body:
            return rows, (p, "carriage return in line")
        try:
            obj = json.loads(body.decode("utf-8"))
        except (UnicodeDecodeError, ValueError, RecursionError):
            return rows, (p, "not valid UTF-8 JSON")
        if type(obj) is not dict:
            return rows, (p, "line is not a JSON object")
        if set(obj) != _ROW_KEYS:
            return rows, (p, "missing or extra row keys")
        seq, ts, prev, payload, h = obj["seq"], obj["ts"], obj["prev_hash"], obj["payload"], obj["hash"]
        if (type(seq) is not int or not _valid_ts(ts) or type(prev) is not str or not _HEX64.fullmatch(prev)
                or type(payload) is not dict or type(h) is not str or not _HEX64.fullmatch(h)):
            return rows, (p, "wrong field types or malformed ts/prev_hash/hash")
        if seq != p:
            return rows, (p, f"seq {seq} does not match position")
        expect_prev = rows[-1].hash if rows else GENESIS
        if prev != expect_prev:
            return rows, (p, "prev_hash does not match previous row")
        try:
            recomputed = _row_hash(seq, ts, prev, payload)
        except CanonicalJsonError:
            return rows, (p, "payload not canonical-JSON valid")
        if recomputed != h:
            return rows, (p, "hash mismatch")
        if rows and ts < rows[-1].ts:
            return rows, (p, "ts earlier than previous row")
        if not line.endswith(b"\n"):
            return rows, (p, "file does not end with newline")
        rows.append(Row(seq, ts, prev, payload, h))
    return rows, None


class Ledger:
    def __init__(self, path: Path, *, kind: str, validate: Callable[[Mapping, "Ledger"], None],
                 clock: Callable[[], datetime] | None = None):
        self._path = Path(path)
        self._kind = kind
        self._validate = validate
        self._clock = clock or (lambda: datetime.now(timezone.utc))

    @property
    def path(self) -> Path:
        return self._path

    @property
    def kind(self) -> str:
        return self._kind

    # ---- internals ----
    @property
    def _roots_path(self) -> Path:
        return self._path.with_suffix(".roots.jsonl")

    @property
    def _lock_path(self) -> Path:
        return Path(str(self._path) + ".lock")

    def _read(self) -> bytes:
        try:
            return self._path.read_bytes()
        except FileNotFoundError:
            return b""

    def _strict_rows(self) -> list[Row]:
        rows, problem = _walk(self._read())
        if problem:
            raise LedgerCorruptError(f"{self._kind}: broken at seq {problem[0]}: {problem[1]}", problem[0])
        return rows

    def _now_ts(self) -> str:
        now = self._clock()
        if not isinstance(now, datetime) or now.tzinfo is None or now.utcoffset() is None:
            raise ValueError("clock must return an aware datetime")
        return now.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%fZ")

    def _now_ist_date(self) -> str:
        self._now_ts()  # validates awareness
        return self._clock().astimezone(_IST).date().isoformat()

    def _locked(self):
        return _Lock(self._lock_path)

    # ---- public API ----
    def rows(self) -> list[Row]:
        return self._strict_rows()

    def head(self) -> Row | None:
        rows = self._strict_rows()
        return rows[-1] if rows else None

    def append(self, payload: Mapping[str, Any]) -> Row:
        if type(payload) is not dict:
            raise PayloadError("payload must be a dict")
        text = canonical_json(payload)  # CanonicalJsonError before anything is written
        with self._locked():
            rows = self._strict_rows()
            self._validate(payload, self)
            ts = self._now_ts()
            if rows and ts < rows[-1].ts:
                raise LedgerError("clock moved backwards")
            prev = rows[-1].hash if rows else GENESIS
            seq = len(rows) + 1
            stored = json.loads(text)
            h = _row_hash(seq, ts, prev, stored)
            line = (canonical_json({"seq": seq, "ts": ts, "prev_hash": prev, "payload": stored, "hash": h})
                    + "\n").encode("utf-8")
            with open(self._path, "ab", buffering=0) as fh:
                fh.write(line)
                os.fsync(fh.fileno())
            return Row(seq, ts, prev, stored, h)

    # ---- roots ----
    def _read_roots(self) -> tuple[list[Root], str | None]:
        try:
            data = self._roots_path.read_bytes()
        except FileNotFoundError:
            return [], None
        roots: list[Root] = []
        if not data:
            return roots, None
        if not data.endswith(b"\n"):
            return roots, "roots file does not end with newline"
        for n, line in enumerate(data.split(b"\n")[:-1], start=1):
            try:
                obj = json.loads(line.decode("utf-8"))
            except (UnicodeDecodeError, ValueError):
                return roots, f"roots line {n}: not valid JSON"
            if (type(obj) is not dict or set(obj) != _ROOT_KEYS or not _valid_date(obj["date"])
                    or type(obj["seq"]) is not int or obj["seq"] < 1
                    or type(obj["hash"]) is not str or not _HEX64.fullmatch(obj["hash"])):
                return roots, f"roots line {n}: malformed root"
            if roots and obj["date"] <= roots[-1].date:
                return roots, f"roots line {n}: dates not strictly increasing"
            roots.append(Root(obj["date"], obj["seq"], obj["hash"]))
        return roots, None

    def roots(self) -> list[Root]:
        roots, problem = self._read_roots()
        if problem:
            raise RootMismatchError(f"{self._kind}: {problem}")
        return roots

    def _verify_roots(self, rows: list[Row]) -> VerifyResult:
        roots, problem = self._read_roots()
        if problem:
            return VerifyResult(False, len(rows), None, "roots", problem)
        findings: list[tuple[int, str]] = []
        for r in roots:
            if r.seq > len(rows):
                findings.append((r.seq, f"ledger truncated: root for {r.date} points at seq {r.seq}, "
                                        f"only {len(rows)} rows"))
                continue
            row = rows[r.seq - 1]
            if row.hash != r.hash:
                findings.append((r.seq, f"root mismatch: root for {r.date} does not match row {r.seq}"))
            elif _ist_date(row.ts) != r.date:
                findings.append((r.seq, f"root date mismatch: row {r.seq} is on {_ist_date(row.ts)}, root says {r.date}"))
            elif r.seq < len(rows) and _ist_date(rows[r.seq].ts) == r.date:
                findings.append((r.seq, f"root not last of day: row {r.seq + 1} is on the same IST day {r.date}"))
        if findings:
            seq, reason = min(findings, key=lambda f: f[0])
            return VerifyResult(False, len(rows), seq, "roots", reason)
        return VerifyResult(True, len(rows), None, "roots" if roots else "none", "")

    def verify(self, *, roots: bool = True) -> VerifyResult:
        rows, problem = _walk(self._read())
        if problem:
            return VerifyResult(False, len(rows), problem[0], "chain", problem[1])
        if roots:
            res = self._verify_roots(rows)
            if not res.ok or res.scope == "roots":
                return res
        return VerifyResult(True, len(rows), None, "chain" if rows else "none", "")

    def write_roots(self) -> list[Root]:
        today = self._now_ist_date()
        with self._locked():
            rows, problem = _walk(self._read())
            if problem:
                raise LedgerCorruptError(f"{self._kind}: broken at seq {problem[0]}: {problem[1]}", problem[0])
            res = self._verify_roots(rows)
            if not res.ok:
                raise RootMismatchError(f"{self._kind}: {res.reason}", res.first_broken_seq)
            existing, _ = self._read_roots()
            last_date = existing[-1].date if existing else ""
            last_of_day: dict[str, Row] = {}
            for row in rows:
                last_of_day[_ist_date(row.ts)] = row
            new = [Root(d, row.seq, row.hash) for d, row in sorted(last_of_day.items())
                   if d < today and d > last_date]
            if not new:
                return []
            blob = "".join(canonical_json({"date": r.date, "hash": r.hash, "seq": r.seq}) + "\n"
                           for r in new).encode("utf-8")
            with open(self._roots_path, "ab", buffering=0) as fh:
                fh.write(blob)
                os.fsync(fh.fileno())
            return new


class _Lock:
    def __init__(self, path: Path):
        self._path = path
        self._fh = None

    def __enter__(self):
        self._fh = open(self._path, "ab")
        fcntl.flock(self._fh, fcntl.LOCK_EX)
        return self

    def __exit__(self, *exc):
        try:
            fcntl.flock(self._fh, fcntl.LOCK_UN)
        finally:
            self._fh.close()
