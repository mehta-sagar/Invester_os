"""Access state, per-host rate limiter and daily request caps (spec collect_base R17-R18, R24-R29).

State that must survive a re-clone (block and halt history) is append-only JSONL under the ledger zone and is
tracked in git. Machine state (limiter stamps, request counters) lives under ``<ledger>/.state/`` and is ignored.
stdlib only. All cross-process coordination is ``fcntl.flock`` on a lock file.
"""
from __future__ import annotations

import fcntl
import json
import os
import re
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Callable, Iterator
from zoneinfo import ZoneInfo

from investos.core import log
from investos.core.config import Config
from investos.core.names import SOURCE_RE as _SOURCE_RE

__all__ = ["AccessStateError", "AccessEvent", "AccessStatus", "AccessState", "clear_halt", "HostLimiter",
           "DailyCounter", "check_decision_file", "ist_date", "HALT_THRESHOLD", "HALT_WINDOW_DAYS"]

IST = ZoneInfo("Asia/Kolkata")
HALT_THRESHOLD = 3
HALT_WINDOW_DAYS = 7
HALT_DETAIL = "3 blocked days in 7"
_HOST_RE = re.compile(r"[a-z0-9][a-z0-9.-]{0,252}", re.ASCII)
_DECISION_ID_RE = re.compile(r"[0-9]{4}a?", re.ASCII)
_TS_RE = re.compile(r"[0-9]{4}-[0-9]{2}-[0-9]{2}T[0-9]{2}:[0-9]{2}:[0-9]{2}\.[0-9]{6}Z", re.ASCII)
_FIELDS = frozenset({"schema", "ts", "date", "event", "source", "host", "detail", "run_id", "decision"})
_EVENTS = frozenset({"blocked", "halted", "cleared"})
_INT_RE = re.compile(r"[0-9]+", re.ASCII)


class AccessStateError(Exception):
    """Unreadable or invalid access state (fail closed)."""


@dataclass(frozen=True)
class AccessEvent:
    ts: str
    date: str
    event: str
    source: str
    host: str | None
    detail: str
    run_id: str
    decision: str | None
    schema: int = 1


@dataclass(frozen=True)
class AccessStatus:
    blocked_today: bool
    halted: bool
    today: str
    reason: str


def ist_date(dt: datetime) -> str:
    return dt.astimezone(IST).date().isoformat()


def _fmt_ts(dt: datetime) -> str:
    return dt.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%fZ")


def _check_host(host: object) -> str:
    if not isinstance(host, str) or not _HOST_RE.fullmatch(host):
        raise AccessStateError(f"invalid host name {host!r}")
    return host


def _check_source(source: object) -> str:
    if not isinstance(source, str) or not _SOURCE_RE.fullmatch(source):
        raise ValueError(f"invalid source name {source!r}")
    return source


def _fsync_dir(path: Path) -> None:
    fd = os.open(path, os.O_RDONLY)
    try:
        os.fsync(fd)
    finally:
        os.close(fd)


@contextmanager
def _flock(lock_path: Path, mode: int) -> Iterator[None]:
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    fd = os.open(lock_path, os.O_RDWR | os.O_CREAT, 0o644)
    try:
        fcntl.flock(fd, mode)
        yield
    finally:
        os.close(fd)                                    # closing releases the lock, also on exceptions


def _lock_of(path: Path) -> Path:
    return path.with_name(path.name + ".lock")


# --------------------------------------------------------------------------- decision files (R18, R26)
def check_decision_file(repo_root: Path, decision: str, lines: list[str]) -> Path:
    """Exactly one docs/decisions/<decision>-*.md containing every given line verbatim. ValueError otherwise."""
    if not isinstance(decision, str) or not _DECISION_ID_RE.fullmatch(decision):
        raise ValueError(f"decision id {decision!r} is not of the form NNNN or NNNNa")
    d = Path(repo_root) / "docs" / "decisions"
    files = sorted(p for p in d.glob(f"{decision}-*.md") if p.is_file()) if d.is_dir() else []
    if len(files) != 1:
        raise ValueError(f"decision {decision}: expected exactly one docs/decisions/{decision}-*.md, found {len(files)}")
    try:
        have = set(files[0].read_text(encoding="utf-8").splitlines())
    except (OSError, UnicodeError) as e:
        raise ValueError(f"decision {decision}: cannot read {files[0].name}: {e}") from None
    for line in lines:
        if line not in have:
            raise ValueError(f"decision {decision}: {files[0].name} lacks the line {line!r}")
    return files[0]


# --------------------------------------------------------------------------- access state files (R25)
def _parse_file(path: Path, kind: str, source: str | None) -> list[AccessEvent]:
    """Strict read of one state file. kind is "host" or "source". Missing file = no events."""
    try:
        data = path.read_bytes()
    except FileNotFoundError:
        return []
    except OSError as e:
        raise AccessStateError(f"{path.name}: cannot read: {e}") from None
    if data and not data.endswith(b"\n"):
        # maybe a writer is mid-append: wait for its lock, then look again
        try:
            with _flock(_lock_of(path), fcntl.LOCK_SH):
                data = path.read_bytes()
        except OSError as e:
            raise AccessStateError(f"{path.name}: cannot read: {e}") from None
        if data and not data.endswith(b"\n"):
            raise AccessStateError(f"{path.name}: torn tail (no final newline)")
    out: list[AccessEvent] = []
    if not data:
        return out
    for n, raw in enumerate(data.split(b"\n")[:-1], 1):
        where = f"{path.name} line {n}"
        if not raw:
            raise AccessStateError(f"{where}: blank line")
        try:
            obj = json.loads(raw.decode("utf-8"))
        except (ValueError, UnicodeError):
            raise AccessStateError(f"{where}: invalid JSON") from None
        if not isinstance(obj, dict) or set(obj) != _FIELDS:
            raise AccessStateError(f"{where}: wrong set of fields")
        if obj["schema"] != 1 or isinstance(obj["schema"], bool):
            raise AccessStateError(f"{where}: unsupported schema {obj['schema']!r}")
        ev = obj["event"]
        if ev not in _EVENTS:
            raise AccessStateError(f"{where}: unknown event {ev!r}")
        for f in ("ts", "date", "source", "detail", "run_id"):
            if not isinstance(obj[f], str):
                raise AccessStateError(f"{where}: {f} must be a string")
        if not _TS_RE.fullmatch(obj["ts"]):
            raise AccessStateError(f"{where}: bad ts")
        try:
            dt = datetime.strptime(obj["ts"], "%Y-%m-%dT%H:%M:%S.%fZ").replace(tzinfo=timezone.utc)
        except ValueError:
            raise AccessStateError(f"{where}: bad ts") from None
        if obj["date"] != ist_date(dt):
            raise AccessStateError(f"{where}: date {obj['date']!r} is not the IST date of ts")
        if not _SOURCE_RE.fullmatch(obj["source"]):
            raise AccessStateError(f"{where}: bad source")
        host, decision = obj["host"], obj["decision"]
        if kind == "host":
            if ev != "blocked" or not isinstance(host, str) or not _HOST_RE.fullmatch(host) \
                    or host + ".jsonl" != path.name:
                raise AccessStateError(f"{where}: not a valid blocked event for this host file")
        else:
            if ev not in ("halted", "cleared") or host is not None or obj["source"] != source:
                raise AccessStateError(f"{where}: not a valid halted/cleared event for this source file")
        if ev == "cleared":
            if not isinstance(decision, str):
                raise AccessStateError(f"{where}: cleared needs a decision")
        elif decision is not None:
            raise AccessStateError(f"{where}: decision only allowed on cleared")
        out.append(AccessEvent(obj["ts"], obj["date"], ev, obj["source"], host, obj["detail"], obj["run_id"],
                               decision, 1))
    return out


def _append(path: Path, line: bytes, *, guard: Callable[[], bool] | None = None) -> bool:
    """Append one whole line under the exclusive lock. Refuses a torn tail. guard() runs under the lock;
    False means "do not write" (returns False)."""
    path.parent.mkdir(parents=True, exist_ok=True)
    with _flock(_lock_of(path), fcntl.LOCK_EX):
        if guard is not None and not guard():
            return False
        existed = path.exists()
        if existed and path.stat().st_size > 0:
            with open(path, "rb") as f:
                f.seek(-1, os.SEEK_END)
                if f.read(1) != b"\n":
                    raise AccessStateError(f"{path.name}: torn tail; refusing to append (see the torn-tail runbook)")
        fd = os.open(path, os.O_WRONLY | os.O_APPEND | os.O_CREAT, 0o644)
        try:
            os.write(fd, line)
            os.fsync(fd)
        finally:
            os.close(fd)
        if not existed:
            _fsync_dir(path.parent)
    return True


def _dump(obj: dict) -> bytes:
    return (json.dumps(obj, sort_keys=True, separators=(",", ":"), ensure_ascii=False) + "\n").encode("utf-8")


class _Cache:
    """Parsed-file cache keyed by the stat signature, re-validated on every use."""

    def __init__(self) -> None:
        self._d: dict[Path, tuple[tuple, list[AccessEvent]]] = {}

    def read(self, path: Path, kind: str, source: str | None) -> list[AccessEvent]:
        try:
            st = os.stat(path)
        except FileNotFoundError:
            self._d.pop(path, None)
            return []
        except OSError as e:
            raise AccessStateError(f"{path.name}: cannot stat: {e}") from None
        sig = (st.st_ino, st.st_size, st.st_mtime_ns, st.st_ctime_ns)
        hit = self._d.get(path)
        if hit is not None and hit[0] == sig:
            return hit[1]
        events = _parse_file(path, kind, source)
        self._d[path] = (sig, events)
        return events


def _latest_halt_state(events: list[AccessEvent]) -> AccessEvent | None:
    """The latest halted/cleared event, if it is a halted one."""
    last = events[-1] if events else None
    return last if last is not None and last.event == "halted" else None


class AccessState:
    """Per-source view of the access state files. ``last_halt`` holds the halted event appended by the most
    recent record_block, or None."""

    def __init__(self, cfg: Config, source: str, *, clock: Callable[[], datetime]) -> None:
        self._source = _check_source(source)
        self._root = Path(cfg.zones["ledger"]) / "access"
        self._clock = clock
        self._cache = _Cache()
        self.last_halt: AccessEvent | None = None

    # ---- paths
    def host_path(self, host: str) -> Path:
        return self._root / "hosts" / f"{_check_host(host)}.jsonl"

    def source_path(self, source: str | None = None) -> Path:
        return self._root / f"{_check_source(source or self._source)}.jsonl"

    def today(self) -> str:
        return ist_date(self._clock())

    # ---- reads
    def events(self) -> list[AccessEvent]:
        """Every event attributed to this source: its blocked events (all host files) and its halt/cleared events."""
        out = [e for e in self._all_blocked() if e.source == self._source]
        out += self._cache.read(self.source_path(), "source", self._source)
        out.sort(key=lambda e: e.ts)
        return out

    def _all_blocked(self) -> list[AccessEvent]:
        d = self._root / "hosts"
        out: list[AccessEvent] = []
        if d.is_dir():
            for p in sorted(d.glob("*.jsonl")):
                out += self._cache.read(p, "host", None)
        return out

    def verify_writable(self, host: str) -> None:
        """Fail closed: the host and source state files must be lockable (so a block can be persisted)."""
        for p in (self.host_path(host), self.source_path()):
            try:
                with _flock(_lock_of(p), fcntl.LOCK_SH):
                    pass
            except OSError as e:
                raise AccessStateError(f"{p.name}: state not writable: {e}") from None

    def status(self, host: str) -> AccessStatus:
        today = self.today()
        halt = _latest_halt_state(self._cache.read(self.source_path(), "source", self._source))
        blocked = any(e.date == today for e in self._cache.read(self.host_path(host), "host", None))
        if halt is not None:
            reason = f"adapter halted: {halt.detail}; needs a decision"
        elif blocked:
            reason = f"host {host} blocked for IST day {today}"
        else:
            reason = ""
        return AccessStatus(blocked, halt is not None, today, reason)

    # ---- writes
    def record_block(self, *, host: str, detail: str, run_id: str) -> AccessEvent:
        _check_host(host)
        now = self._clock()
        ev = {"schema": 1, "ts": _fmt_ts(now), "date": ist_date(now), "event": "blocked", "source": self._source,
              "host": host, "detail": str(detail), "run_id": str(run_id), "decision": None}
        _append(self.host_path(host), _dump(ev))
        self.last_halt = None
        self._maybe_halt(host, str(run_id), now)
        return AccessEvent(**ev)

    def _maybe_halt(self, host: str, run_id: str, now: datetime) -> None:
        sp = self.source_path()
        today = now.astimezone(IST).date()
        window = {(today - timedelta(days=i)).isoformat() for i in range(HALT_WINDOW_DAYS)}
        own = self._cache.read(sp, "source", self._source)
        cleared = [e.ts for e in own if e.event == "cleared"]
        since = max(cleared) if cleared else ""
        days = {e.date for e in self._all_blocked() if e.source == self._source and e.ts > since and e.date in window}
        if len(days) < HALT_THRESHOLD:
            return
        ev = {"schema": 1, "ts": _fmt_ts(now), "date": ist_date(now), "event": "halted", "source": self._source,
              "host": None, "detail": HALT_DETAIL, "run_id": run_id, "decision": None}

        def not_yet_halted() -> bool:
            return _latest_halt_state(_parse_file(sp, "source", self._source)) is None

        if _append(sp, _dump(ev), guard=not_yet_halted):
            self.last_halt = AccessEvent(**ev)


def clear_halt(cfg: Config, source: str, *, decision: str, clock: Callable[[], datetime], run_id: str) -> AccessEvent:
    """End a halt. ValueError (nothing written) unless the adapter is halted and a decision file clears it."""
    _check_source(source)
    sp = Path(cfg.zones["ledger"]) / "access" / f"{source}.jsonl"
    if _latest_halt_state(_parse_file(sp, "source", source)) is None:
        raise ValueError(f"{source} is not halted")
    check_decision_file(cfg.repo_root, decision, [f"Clears-halt: {source}"])
    now = clock()
    ev = {"schema": 1, "ts": _fmt_ts(now), "date": ist_date(now), "event": "cleared", "source": source,
          "host": None, "detail": f"halt cleared by decision {decision}", "run_id": str(run_id), "decision": decision}

    def still_halted() -> bool:
        return _latest_halt_state(_parse_file(sp, "source", source)) is not None

    if not _append(sp, _dump(ev), guard=still_halted):
        raise ValueError(f"{source} is not halted")
    return AccessEvent(**ev)


# --------------------------------------------------------------------------- limiter (R27)
class HostLimiter:
    def __init__(self, state_dir: Path, *, wall_clock: Callable[[], float], monotonic: Callable[[], float],
                 sleeper: Callable[[float], None]) -> None:
        self._dir = Path(state_dir) / "ratelimit"
        self._wall, self._mono, self._sleep = wall_clock, monotonic, sleeper
        self._last_mono: dict[str, float] = {}

    def acquire(self, host: str, interval: float) -> None:
        _check_host(host)
        last_file = self._dir / f"{host}.last"
        with _flock(self._dir / f"{host}.lock", fcntl.LOCK_EX):
            wait_wall = 0.0
            try:
                text = last_file.read_text(encoding="ascii")
            except FileNotFoundError:
                text = None
            except (OSError, UnicodeError):
                text = ""
            if text is not None:
                if _INT_RE.fullmatch(text.strip()):
                    last = int(text.strip()) / 1e9
                    wait_wall = min(max(interval - (self._wall() - last), 0.0), interval)
                else:
                    wait_wall = interval
            wait_mono = 0.0
            lm = self._last_mono.get(host)
            if lm is not None:
                wait_mono = min(max(interval - (self._mono() - lm), 0.0), interval)
            wait = max(wait_wall, wait_mono)
            if wait > 0:
                self._sleep(wait)
            self._last_mono[host] = self._mono()
            # slow part first (create + fsync), THEN take the stamp, so the stamp is as close as possible to the
            # moment the request may start; the final write and replace are not fsynced (spacing only)
            self._dir.mkdir(parents=True, exist_ok=True)
            tmp = self._dir / f".tmp-{host}-{os.getpid()}"
            fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o644)
            try:
                os.write(fd, b"0")
                os.fsync(fd)
                stamp = str(int(round(self._wall() * 1e9))).encode("ascii")
                os.ftruncate(fd, 0)
                os.pwrite(fd, stamp, 0)
            finally:
                os.close(fd)
            os.replace(tmp, last_file)


# --------------------------------------------------------------------------- daily cap (R29)
class DailyCounter:
    def __init__(self, state_dir: Path, source: str, *, clock: Callable[[], datetime]) -> None:
        self._source = _check_source(source)
        self._dir = Path(state_dir) / "requests"
        self._clock = clock

    def reserve(self, cap: int | None) -> bool:
        if cap is None:
            return True
        day = ist_date(self._clock())
        path = self._dir / f"{self._source}.{day}.count"
        with _flock(self._dir / f"{self._source}.lock", fcntl.LOCK_EX):
            try:
                text = path.read_text(encoding="ascii")
            except FileNotFoundError:
                count = 0
            except (OSError, UnicodeError) as e:
                return self._corrupt(path, f"unreadable: {e}")
            else:
                if not _INT_RE.fullmatch(text):
                    return self._corrupt(path, "not a non-negative integer")
                count = int(text)
            if count >= cap:
                return False
            tmp = self._dir / f".tmp-{self._source}-{os.getpid()}"
            fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o644)
            try:
                os.write(fd, str(count + 1).encode("ascii"))
                os.fsync(fd)
            finally:
                os.close(fd)
            os.replace(tmp, path)
            return True

    def _corrupt(self, path: Path, why: str) -> bool:
        log.get_logger(f"collect.{self._source}").error("access_state_unreadable", file=path.name, reason=why)
        return False
