"""The append-only window ledger (spec collect_backfill R9, R11) with the writer and reader rules of collect_base R25.

One JSON line per window event under ``<ledger zone>/backfill/<endpoint>.windows.jsonl``; a window's state is its last
line. The reader is strict and never skips a line: a bad line raises ``LedgerError`` (fail closed)."""
from __future__ import annotations

import fcntl
import json
import os
import re
from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Callable, Mapping
from zoneinfo import ZoneInfo

from investos.core.config import Config

__all__ = ["LedgerError", "WindowKey", "WindowState", "WindowLedger", "EVENTS", "DONE_EVENTS", "ledger_dir",
           "ledger_path"]

IST = ZoneInfo("Asia/Kolkata")
EVENTS = frozenset({"planned", "fetched", "empty", "split", "superseded", "unsplittable", "failed", "blocked",
                    "audit_shrink"})
DONE_EVENTS = frozenset({"fetched", "empty"})
_FIELDS = ("schema", "ts", "date", "endpoint", "start", "end", "part", "event", "rows", "sha256", "logical_key",
           "run_id", "probe_hash", "reason")
_TS_RE = re.compile(r"[0-9]{4}-[0-9]{2}-[0-9]{2}T[0-9]{2}:[0-9]{2}:[0-9]{2}\.[0-9]{6}Z", re.ASCII)
_PART_RE = re.compile(r"[a-z]*", re.ASCII)
_SHA_RE = re.compile(r"[0-9a-f]{64}", re.ASCII)
_ONE_DAY = timedelta(days=1)


class LedgerError(Exception):
    pass


@dataclass(frozen=True)
class WindowKey:
    endpoint_id: str
    start: date
    end: date
    part: str = ""

    @property
    def days(self) -> int:
        return (self.end - self.start).days + 1

    def logical_key(self) -> str:
        base = f"{self.endpoint_id}/{self.start.isoformat()}_{self.end.isoformat()}"
        return f"{base}/{self.part}" if self.part else base


@dataclass(frozen=True)
class WindowState:
    key: WindowKey
    event: str
    ts: str
    rows: int | None
    sha256: str
    logical_key: str
    run_id: str
    probe_hash: str
    reason: str


def ledger_dir(cfg: Config) -> Path:
    return Path(cfg.zones["ledger"]) / "backfill"


def ledger_path(cfg: Config, endpoint_id: str) -> Path:
    return ledger_dir(cfg) / f"{endpoint_id}.windows.jsonl"


def _fmt_ts(dt: datetime) -> str:
    return dt.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%fZ")


def _fsync_dir(path: Path) -> None:
    fd = os.open(path, os.O_RDONLY)
    try:
        os.fsync(fd)
    finally:
        os.close(fd)


def _is_int(v) -> bool:
    return isinstance(v, int) and not isinstance(v, bool)


class WindowLedger:
    def __init__(self, cfg: Config, endpoint_id: str, *, clock: Callable[[], datetime] | None = None) -> None:
        self._eid = endpoint_id
        self.path = ledger_path(cfg, endpoint_id)
        self._lock = self.path.with_name(self.path.name + ".lock")
        self._clock = clock or (lambda: datetime.now(timezone.utc))

    # ---- writer
    def append(self, event: str, key: WindowKey, **fields) -> None:
        if event not in EVENTS:
            raise LedgerError(f"unknown event {event!r}")
        extra = set(fields) - {"rows", "sha256", "logical_key", "run_id", "probe_hash", "reason"}
        if extra:
            raise LedgerError(f"unknown ledger field(s) {sorted(extra)}")
        now = self._clock()
        obj = {"schema": 1, "ts": _fmt_ts(now), "date": now.astimezone(IST).date().isoformat(),
               "endpoint": key.endpoint_id, "start": key.start.isoformat(), "end": key.end.isoformat(),
               "part": key.part, "event": event, "rows": None, "sha256": "", "logical_key": "", "run_id": "",
               "probe_hash": "", "reason": ""}
        obj.update(fields)
        self._validate(obj)                                      # never write what the reader would refuse
        line = (json.dumps(obj, sort_keys=True, separators=(",", ":"), ensure_ascii=False) + "\n").encode("utf-8")
        self.path.parent.mkdir(parents=True, exist_ok=True)
        fd_lock = os.open(self._lock, os.O_RDWR | os.O_CREAT, 0o644)
        try:
            fcntl.flock(fd_lock, fcntl.LOCK_EX)
            existed = self.path.exists()
            if existed and self.path.stat().st_size > 0:
                with open(self.path, "rb") as f:
                    f.seek(-1, os.SEEK_END)
                    if f.read(1) != b"\n":
                        raise LedgerError(f"{self.path.name}: torn tail; refusing to append")
            fd = os.open(self.path, os.O_WRONLY | os.O_APPEND | os.O_CREAT, 0o644)
            try:
                os.write(fd, line)
                os.fsync(fd)
            finally:
                os.close(fd)
            if not existed:
                _fsync_dir(self.path.parent)
        finally:
            os.close(fd_lock)

    # ---- reader
    def lines(self) -> list[dict]:
        """Every line, validated and in file order. Missing file: no lines."""
        if not self.path.exists():
            return []
        self.path.parent.mkdir(parents=True, exist_ok=True)
        fd_lock = os.open(self._lock, os.O_RDWR | os.O_CREAT, 0o644)
        try:
            fcntl.flock(fd_lock, fcntl.LOCK_SH)
            try:
                data = self.path.read_bytes()
            except OSError as e:
                raise LedgerError(f"{self.path.name}: cannot read: {e}") from None
        finally:
            os.close(fd_lock)
        if not data:
            return []
        if not data.endswith(b"\n"):
            raise LedgerError(f"{self.path.name}: torn tail (last line is not newline-terminated)")
        out: list[dict] = []
        for n, raw in enumerate(data.split(b"\n")[:-1], 1):
            try:
                obj = json.loads(raw.decode("utf-8"))
            except (ValueError, UnicodeError):
                raise LedgerError(f"{self.path.name} line {n}: not valid JSON") from None
            try:
                self._validate(obj)
            except LedgerError as e:
                raise LedgerError(f"{self.path.name} line {n}: {e}") from None
            if obj["endpoint"] != self._eid:
                raise LedgerError(f"{self.path.name} line {n}: endpoint {obj['endpoint']!r}")
            out.append(obj)
        return out

    def state(self) -> Mapping[WindowKey, WindowState]:
        """Latest line per window; `audit_shrink` lines are not window states."""
        out: dict[WindowKey, WindowState] = {}
        for o in self.lines():
            if o["event"] == "audit_shrink":
                continue
            k = WindowKey(o["endpoint"], date.fromisoformat(o["start"]), date.fromisoformat(o["end"]), o["part"])
            out[k] = WindowState(k, o["event"], o["ts"], o["rows"], o["sha256"], o["logical_key"], o["run_id"],
                                 o["probe_hash"], o["reason"])
        return out

    def shrink(self) -> int | None:
        """The latest `audit_shrink` value (reason `max_window_days=<n>`), or None."""
        val = None
        for o in self.lines():
            if o["event"] == "audit_shrink":
                m = re.fullmatch(r"max_window_days=([0-9]+)", o["reason"])
                if m:
                    val = int(m.group(1))
        return val

    # ---- validation (shared by writer and reader)
    @staticmethod
    def _validate(o) -> None:
        if not isinstance(o, dict) or set(o) != set(_FIELDS):
            raise LedgerError("unknown or missing fields")
        if o["schema"] != 1 or not _is_int(o["schema"]):
            raise LedgerError("schema must be 1")
        if not isinstance(o["ts"], str) or not _TS_RE.fullmatch(o["ts"]):
            raise LedgerError("ts must be UTC with 6 digits")
        ts = datetime.strptime(o["ts"], "%Y-%m-%dT%H:%M:%S.%fZ").replace(tzinfo=timezone.utc)
        if o["date"] != ts.astimezone(IST).date().isoformat():
            raise LedgerError("date is not the IST date of ts")
        if o["event"] not in EVENTS:
            raise LedgerError(f"unknown event {o['event']!r}")
        for f in ("endpoint", "sha256", "logical_key", "run_id", "probe_hash", "reason", "part", "start", "end"):
            if not isinstance(o[f], str):
                raise LedgerError(f"{f} must be a string")
        try:
            s, e = date.fromisoformat(o["start"]), date.fromisoformat(o["end"])
        except ValueError:
            raise LedgerError("start and end must be ISO dates") from None
        if s > e:
            raise LedgerError("start is after end")
        if not _PART_RE.fullmatch(o["part"]):
            raise LedgerError("part must be [a-z]*")
        if o["rows"] is not None and (not _is_int(o["rows"]) or o["rows"] < 0):
            raise LedgerError("rows must be a non-negative integer or null")
        if o["sha256"] and not _SHA_RE.fullmatch(o["sha256"]):
            raise LedgerError("sha256 must be 64 hex characters")
        if o["event"] in DONE_EVENTS and (not o["sha256"] or o["rows"] is None or not o["logical_key"]):
            raise LedgerError(f"a {o['event']} line needs sha256, rows and logical_key")
        if o["event"] == "empty" and o["rows"] != 0:
            raise LedgerError("an empty line has rows 0")
