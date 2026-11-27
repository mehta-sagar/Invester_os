"""Collector base class (spec: specs/collect_base.md, brief S0-T4; module placement: decision 0001).

A collector fetches raw bytes for a set of logical keys, refuses bodies that are not real data, and
stores what it fetched byte-for-byte through the ManifestStore only. It never parses content: the
subclass's ``check_body`` is the only code that looks inside a body. HTTP goes through an injected
``Transport``; time and sleeping are injected so tests run instantly.

stdlib only (plus ``investos.core`` and ``investos.acquire.manifest``). ``json`` is used for the
run sidecar only.
"""
from __future__ import annotations

import io
import json
import os
import re
import socket
import stat
import time
import uuid
import zipfile
from abc import ABC, abstractmethod
from dataclasses import dataclass
from datetime import date, datetime, timezone
from enum import Enum
from pathlib import Path
from typing import Callable, ClassVar, Iterable, Literal, Mapping, Protocol
from urllib.parse import urldefrag, urlparse

from investos import __version__
from investos.acquire.access import AccessState, AccessStateError, DailyCounter, HostLimiter, check_decision_file
from investos.acquire.manifest import ManifestEntry, ManifestStore, RawIntegrityError
from investos.core import log
from investos.core.config import Config
from investos.core.names import SOURCE_RE as _SOURCE_RE
from investos.core.notify import NullNotifier, Notifier

__all__ = [
    "Collector", "Outcome", "Response", "Transport", "TransportError", "BodyVerdict", "RunRange",
    "UnzipLimits", "RunResult", "safe_unzip", "UnsafeArchiveError", "AccessMode", "BrowserResponse",
    "BrowserTransport", "DateEvidence",
]

MAX_ATTEMPTS = 4                                  # first try + 3 retries
_BLOCK_STATUSES = frozenset({401, 403, 429, 451})
_SIGNATURE_SCAN_BYTES = 262144                    # R24: only the first 256 KiB of a body is searched
_FLOOR_INTERVAL_S = 2.5                           # R28: real_browser / scoped_exception never go faster
# headers no mode may set (compared lower-case, "_" as "-"); the base owns accept-encoding
_DENIED_ALWAYS = frozenset({
    "origin", "authorization", "proxy-authorization", "host", "forwarded", "via", "x-forwarded-for",
    "x-forwarded-host", "x-real-ip", "x-client-ip", "true-client-ip", "cookie2", "accept-encoding",
    "connection", "content-length", "transfer-encoding"})
_DENIED_HONEST = _DENIED_ALWAYS | {"user-agent", "referer", "cookie"}
_HEADER_NAME_RE = re.compile(r"[!#$%&'*+.^_`|~0-9A-Za-z-]+", re.ASCII)      # RFC 7230 token
_ISO_KEY_RE = re.compile(r"[0-9]{4}-[0-9]{2}-[0-9]{2}", re.ASCII)
_WAIVER_MIN = 20
_RUN_ID_RE = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]{0,127}", re.ASCII)
_DRIVE_RE = re.compile(r"[A-Za-z]:")
_MAX_MEMBER_NAME = 400                            # manifest logical_key cap is 512 incl. "<key>/"
_CHUNK = 64 * 1024


class Outcome(str, Enum):
    FETCHED = "fetched"
    FETCHED_EMPTY = "fetched_empty"
    MISSING_HOLIDAY = "missing_holiday"
    UNREADABLE = "unreadable"
    SKIPPED = "skipped"
    INCOMPLETE = "incomplete"
    REJECTED = "rejected"             # well-formed body that is not what the key names (R34): nothing stored


_COUNT_KEYS = tuple(o.value for o in Outcome) + ("not_attempted",)


class AccessMode(str, Enum):
    HONEST = "honest"
    REAL_BROWSER = "real_browser"
    SCOPED_EXCEPTION = "scoped_exception"


@dataclass(frozen=True)
class Response:
    status: int
    headers: Mapping[str, str]
    body: bytes
    url: str


class TransportError(Exception):
    """Timeouts, connection reset, DNS failure."""


class Transport(Protocol):
    accept_encoding: str                  # optional: encodings the transport can decode; absent means "identity"

    def get(self, url: str, *, headers: Mapping[str, str], timeout: float) -> Response:
        """Must NOT follow redirects; 3xx is returned as-is."""
        ...


@dataclass(frozen=True)
class BrowserResponse:
    status: int
    headers: Mapping[str, str]            # response headers, lowercased names
    body: bytes                           # bytes after the browser's own content decoding
    final_url: str                        # URL after any redirects, fragment included if reported
    request_headers: Mapping[str, str] | None   # headers the browser reports it sent; None if unavailable


class BrowserTransport(Protocol):
    def fetch(self, url: str) -> BrowserResponse:
        """Raises TransportError on timeout / page crash / navigation failure."""
        ...


@dataclass(frozen=True)
class BodyVerdict:
    outcome: Outcome
    reason: str = ""
    record_count: int | None = None


@dataclass(frozen=True)
class DateEvidence:
    """What an adapter read inside a file: the distinct trade dates, the field read, the rows looked at (R36)."""
    dates: frozenset[date]
    field: str
    rows_examined: int


@dataclass(frozen=True)
class RunRange:
    start: date
    end: date

    def __post_init__(self) -> None:
        if self.end < self.start:
            raise ValueError(f"RunRange end {self.end} is before start {self.start}")


@dataclass(frozen=True)
class UnzipLimits:
    max_members: int = 10_000
    max_total_bytes: int = 1_073_741_824
    max_ratio: float = 100.0
    max_member_bytes: int = 536_870_912


@dataclass(frozen=True)
class RunResult:
    status: Literal["complete", "incomplete", "aborted"]
    outcomes: Mapping[str, Outcome]
    http_calls: int
    sidecar_path: Path
    sidecar_replaced_previous: bool


# --------------------------------------------------------------------------- archives
class UnsafeArchiveError(Exception):
    pass


def _normalise_member(raw: str) -> str:
    """Validate and normalise one zip member name; raises UnsafeArchiveError."""
    if "\x00" in raw:
        raise UnsafeArchiveError("member name contains NUL")
    name = raw.replace("\\", "/")
    if name.startswith("/"):
        raise UnsafeArchiveError(f"absolute member name {raw!r}")
    if _DRIVE_RE.match(name):
        raise UnsafeArchiveError(f"drive letter in member name {raw!r}")
    parts = []
    for p in name.split("/"):
        if p == "..":
            raise UnsafeArchiveError(f"path traversal in member name {raw!r}")
        if p in ("", "."):
            continue
        parts.append(p)
    if not parts:
        raise UnsafeArchiveError(f"empty member name {raw!r}")
    out = "/".join(parts)
    if len(out) > _MAX_MEMBER_NAME:
        raise UnsafeArchiveError("member name too long")
    return out


def safe_unzip(data: bytes, *, limits: UnzipLimits = UnzipLimits()) -> list[tuple[str, bytes]]:
    """Extract a zip fully in memory with zip-slip and zip-bomb guards. Never touches the disk."""
    if not isinstance(data, (bytes, bytearray)) or not data:
        raise UnsafeArchiveError("not a zip file")
    try:
        zf = zipfile.ZipFile(io.BytesIO(bytes(data)))
    except Exception as e:  # BadZipFile, struct errors, ...
        raise UnsafeArchiveError(f"not a valid zip: {e}") from None
    with zf:
        infos = zf.infolist()
        if len(infos) > limits.max_members:
            raise UnsafeArchiveError(f"{len(infos)} members exceeds limit {limits.max_members}")
        # names, symlinks, encryption, duplicates: all checked before a single byte is inflated
        seen: set[str] = set()
        todo: list[tuple[str, zipfile.ZipInfo]] = []
        for zi in infos:
            is_dir = zi.is_dir() or zi.filename.endswith(("/", "\\"))
            if is_dir and zi.filename.replace("\\", "/").strip("/") in ("", "."):
                continue                                    # "./" style root directory entry
            name = _normalise_member(zi.filename)
            if stat.S_ISLNK(zi.external_attr >> 16):
                raise UnsafeArchiveError(f"symlink member {zi.filename!r}")
            if zi.flag_bits & 0x1:
                raise UnsafeArchiveError(f"encrypted member {zi.filename!r}")
            if name in seen:
                raise UnsafeArchiveError(f"duplicate member {name!r}")
            seen.add(name)
            if is_dir:
                continue
            if zi.file_size > limits.max_member_bytes:
                raise UnsafeArchiveError(f"member {name!r} declares {zi.file_size} bytes, over limit")
            todo.append((name, zi))
        if not todo:
            raise UnsafeArchiveError("archive has no file members")
        # headers are not trusted: count while inflating and stop as soon as a limit is crossed
        budget = min(limits.max_total_bytes, limits.max_ratio * len(data))
        total = 0
        out: list[tuple[str, bytes]] = []
        for name, zi in todo:
            buf = bytearray()
            try:
                with zf.open(zi) as fh:
                    while True:
                        chunk = fh.read(_CHUNK)
                        if not chunk:
                            break
                        buf += chunk
                        total += len(chunk)
                        if len(buf) > limits.max_member_bytes:
                            raise UnsafeArchiveError(f"member {name!r} exceeds {limits.max_member_bytes} bytes")
                        if total > limits.max_total_bytes:
                            raise UnsafeArchiveError(f"archive exceeds {limits.max_total_bytes} uncompressed bytes")
                        if total > budget:
                            raise UnsafeArchiveError(f"compression ratio exceeds {limits.max_ratio}")
            except UnsafeArchiveError:
                raise
            except Exception as e:  # corrupt stream, bad CRC, unsupported compression, password
                raise UnsafeArchiveError(f"cannot read member {name!r}: {e}") from None
            out.append((name, bytes(buf)))
    out.sort(key=lambda p: p[0])
    return out


# --------------------------------------------------------------------------- collector
@dataclass
class _Rec:
    outcome: Outcome | None
    reason: str = ""
    outputs: list[tuple[str, str]] | None = None      # (key, sha256) rows for the sidecar
    not_attempted: bool = False                       # no request was made (access state, halt or cap)
    trade_date: str | None = None                     # verified ISO trade date (date check passed)
    check: dict | None = None                         # rejected: file_dates, field, rows_examined for the log


class _Stop(Exception):
    """Raised inside a key when access state or the daily cap forbids the request: the run aborts."""

    def __init__(self, rec: _Rec, reason: str) -> None:
        super().__init__(reason)
        self.rec, self.reason = rec, reason


def _fmt_ts(dt: datetime) -> str:
    return dt.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%fZ")


def _fsync_dir(path: Path) -> None:
    fd = os.open(path, os.O_RDONLY)
    try:
        os.fsync(fd)
    finally:
        os.close(fd)


def _validate_header_map(mapping: object, denied: frozenset[str], label: str) -> dict[str, str]:
    """Return {name: value} with "_" normalised to "-". ValueError on a bad name/value, a denied name or a duplicate."""
    if not isinstance(mapping, Mapping):
        raise ValueError(f"{label} must be a mapping")
    out: dict[str, str] = {}
    seen: set[str] = set()
    for name, value in mapping.items():
        if not isinstance(name, str) or not _HEADER_NAME_RE.fullmatch(name):
            raise ValueError(f"{label}: header {name!r} is not allowed (bad name)")
        norm = name.lower().replace("_", "-")
        if norm in denied:
            raise ValueError(f"{label}: header {name!r} is not allowed in this access mode")
        if norm in seen:
            raise ValueError(f"{label}: header {name!r} is given twice")
        seen.add(norm)
        if not isinstance(value, str) or any(c in value for c in "\r\n\0"):
            raise ValueError(f"{label}: header {name!r} has an invalid value")
        out[name.replace("_", "-")] = value
    return out


def _check_contact(contact: object, transport: object) -> str:
    if contact is None or contact == "":
        raise ValueError("collect.contact is not configured")
    if not isinstance(contact, str) or not 3 <= len(contact) <= 100 \
            or any(not 0x20 <= ord(c) <= 0x7E for c in contact) or any(c in contact for c in ";()") \
            or contact != contact.strip(" "):
        raise ValueError("collect.contact must be 3 to 100 printable ASCII characters without ';', '(' or ')' "
                         "and without leading or trailing space")
    if contact.lower().endswith(".invalid") and getattr(transport, "real_network", False):
        raise ValueError("collect.contact is still the placeholder (.invalid); set a real address")
    return contact


class Collector(ABC):
    source: ClassVar[str]
    source_version: ClassVar[str]
    page_limit: ClassVar[int | None] = None
    archive: ClassVar[bool] = False
    extra_headers: ClassVar[Mapping[str, str]] = {}
    timeout_s: ClassVar[float | None] = None          # None: cfg.collect.timeout_s
    unzip_limits: ClassVar[UnzipLimits] = UnzipLimits()
    access_mode: ClassVar[AccessMode] = AccessMode.HONEST
    date_keyed: ClassVar[bool] = False                # True: keys are trade dates and R35 to R41 apply
    date_check_waiver: ClassVar[str | None] = None    # reason for a source whose files carry no date (R35)

    def __init__(self, *, transport: Transport | None = None, browser: BrowserTransport | None = None,
                 manifest: ManifestStore, cfg: Config, run_id: str | None = None, sidecar_dir: Path | None = None,
                 clock: Callable[[], datetime] | None = None,
                 monotonic: Callable[[], float] | None = None,
                 sleeper: Callable[[float], None] | None = None,
                 wall_clock: Callable[[], float] | None = None,
                 notifier: Notifier | None = None,
                 hostname: Callable[[], str] | None = None) -> None:
        cls = type(self)
        source = getattr(cls, "source", None)
        # order of failures (R31): contact, mode/config agreement, decision row, wiring, headers, the rest
        contact = _check_contact(cfg.collect.contact, transport)
        mode_raw = getattr(cls, "access_mode", None)
        try:
            mode = AccessMode(getattr(mode_raw, "value", mode_raw))
        except ValueError:
            raise ValueError(f"{source}: access_mode {mode_raw!r} is not one of the three modes") from None
        entry = cfg.collect.access.get(source) if isinstance(source, str) else None
        cfg_mode = entry.mode if entry is not None else "honest"
        if cfg_mode != mode.value:
            raise ValueError(f"{source}: class access_mode is {mode.value!r} but config says {cfg_mode!r}")
        if mode is not AccessMode.HONEST and (entry is None or entry.daily_cap is None):
            raise ValueError(f"{source}: access mode {mode.value} needs [collect.access.{source}] with daily_cap")
        if mode is AccessMode.SCOPED_EXCEPTION and entry.decision is None:
            raise ValueError(f"{source}: scoped_exception needs a decision id in config")
        if entry is not None and entry.decision is not None and mode is not AccessMode.HONEST:
            check_decision_file(cfg.repo_root, entry.decision,
                                [f"Adapter: {source}", f"Access-mode: {mode.value}"])
        if mode is AccessMode.REAL_BROWSER:
            if browser is None or transport is not None:
                raise ValueError(f"{source}: a real_browser adapter needs browser= and no transport=")
        elif transport is None or browser is not None:
            raise ValueError(f"{source}: a {mode.value} adapter needs transport= and no browser=")
        denied = _DENIED_HONEST if mode is AccessMode.HONEST else _DENIED_ALWAYS
        extra = _validate_header_map(self.extra_headers, denied if mode is not AccessMode.REAL_BROWSER
                                     else _DENIED_HONEST, "extra_headers")
        if not isinstance(source, str) or not _SOURCE_RE.fullmatch(source):
            raise ValueError(f"invalid source name {source!r}")
        if not isinstance(getattr(cls, "source_version", None), str) or not self.source_version:
            raise ValueError("source_version must be a non-empty str class attribute")
        if self.page_limit is not None and (isinstance(self.page_limit, bool)
                                            or not isinstance(self.page_limit, int) or self.page_limit < 1):
            raise ValueError("page_limit must be None or an int >= 1")
        self._date_check = self._declare_date_check(source)
        rid = run_id if run_id is not None else log.get_run_id()
        if not isinstance(rid, str) or not _RUN_ID_RE.fullmatch(rid):
            raise ValueError(f"invalid run_id {rid!r}")
        self._run_id = rid
        self._mode = mode
        self._entry = entry
        self._signatures = tuple((s, s.encode("utf-8")) for s in (entry.challenge_signatures if entry else ()))
        self._daily_cap = entry.daily_cap if entry is not None else None
        self._denied = denied
        self._ua = f"InvestOS/{__version__} (personal research; {contact})"
        self._transport = transport
        self._browser = browser
        self._manifest = manifest
        self._cfg = cfg
        self._sidecar_dir = Path(sidecar_dir) if sidecar_dir is not None \
            else Path(cfg.zones["manifests"]) / f"{source}.runs"
        self._clock = clock or (lambda: datetime.now(timezone.utc))
        self._mono = monotonic or time.monotonic
        self._sleep = sleeper or time.sleep
        if wall_clock is not None:
            self._wall = wall_clock
        elif monotonic is None:
            self._wall = time.time
        else:                                                   # injected monotonic: keep wall time consistent with it
            anchor = time.time() - self._mono()
            self._wall = lambda: anchor + self._mono()
        self._notifier: Notifier = notifier if notifier is not None else NullNotifier()
        self._hostname = hostname or socket.gethostname
        self._timeout = float(cls.timeout_s) if cls.timeout_s is not None else cfg.collect.timeout_s
        state_dir = Path(cfg.zones["ledger"]) / ".state"
        self._access = AccessState(cfg, source, clock=self._clock)
        self._limiter = HostLimiter(state_dir, wall_clock=self._wall, monotonic=self._mono, sleeper=self._sleep)
        self._counter = DailyCounter(state_dir, source, clock=self._clock)
        self._log = log.get_logger(f"collect.{source}")
        self._calls = 0
        self._deny_reason: str | None = None
        self._state_broken: str | None = None                  # set when a block could not be persisted

    # ---- subclass interface
    @abstractmethod
    def keys(self, run_range: RunRange) -> Iterable[str]: ...

    @abstractmethod
    def url(self, key: str) -> str: ...

    @abstractmethod
    def check_body(self, key: str, resp: Response) -> BodyVerdict: ...

    def logical_date(self, key: str) -> date:
        """The date a key names. Default: exactly YYYY-MM-DD (R41); an adapter may override for other spellings."""
        if not isinstance(key, str) or not _ISO_KEY_RE.fullmatch(key):
            raise ValueError(f"key {key!r} is not of the form YYYY-MM-DD")
        return date.fromisoformat(key)

    def internal_dates(self, key: str, member: str | None, body: bytes) -> DateEvidence | None:
        """Adapter hook (R36): the trade dates written inside the file. Required when date_keyed and not waived."""
        raise NotImplementedError("internal_dates must be overridden by a date_keyed adapter")

    def _declare_date_check(self, source: object) -> str:
        cls = type(self)
        keyed = getattr(cls, "date_keyed", False)
        waiver = getattr(cls, "date_check_waiver", None)
        overridden = getattr(cls, "internal_dates", None) is not Collector.internal_dates
        if not isinstance(keyed, bool):
            raise ValueError(f"{source}: date_keyed must be a bool")
        if not keyed:
            if waiver is not None:
                raise ValueError(f"{source}: date_check_waiver is set but date_keyed is False")
            if overridden:
                raise ValueError(f"{source}: internal_dates is overridden but date_keyed is False (dead code)")
            return "not_date_keyed"
        if waiver is not None:
            if not isinstance(waiver, str) or len(waiver.strip()) < _WAIVER_MIN:
                raise ValueError(f"{source}: date_check_waiver must be a reason of at least {_WAIVER_MIN} characters")
            if overridden:
                raise ValueError(f"{source}: both internal_dates and date_check_waiver are declared; use one")
            return "waived"
        if not overridden:
            raise ValueError(f"{source}: date_keyed needs an internal_dates override or a date_check_waiver")
        return "enforced"

    def _validate_keys(self, keys: list[str]) -> None:
        """R41: every key names a date; two keys never name the same date. ValueError before any HTTP call."""
        seen: dict[date, str] = {}
        for k in keys:
            try:
                d = self.logical_date(k)
            except Exception as e:
                raise ValueError(f"key {k!r}: logical_date failed: {e}") from None
            if not isinstance(d, date) or isinstance(d, datetime):
                raise ValueError(f"key {k!r}: logical_date must return a datetime.date")
            if d in seen:
                raise ValueError(f"keys {seen[d]!r} and {k!r} name the same date {d.isoformat()}")
            seen[d] = k

    def headers_for(self, key: str, url: str) -> Mapping[str, str]:
        """Extra request headers for one request (validated on every call). scoped_exception adapters may override."""
        return dict(self.extra_headers)

    # ---- run
    def run(self, run_range: RunRange) -> RunResult:
        if not isinstance(run_range, RunRange) or run_range.end < run_range.start:
            raise ValueError("run_range must be a RunRange with end >= start")
        keys = list(dict.fromkeys(self.keys(run_range)))        # propagates; collapses duplicates
        for k in keys:
            if not isinstance(k, str) or not k:
                raise TypeError(f"keys() must yield non-empty str, got {k!r}")
        if self._date_check != "not_date_keyed":
            self._validate_keys(keys)
        started = self._clock()
        self._calls = 0
        self._deny_reason = None
        recs: dict[str, _Rec] = {}
        aborted_after: str | None = None
        if self._date_check == "waived":
            self._log.warning("date_check_waived", reason=type(self).date_check_waiver)
        for i, key in enumerate(keys):
            try:
                rec, abort = self._do_key(key)
            except OSError as e:                                # e.g. ENOSPC while storing
                rec, abort = _Rec(Outcome.UNREADABLE, f"storage failure: {e}"), True
            recs[key] = rec
            if abort:
                aborted_after = key
                self._log.error("run_aborted", key=key, reason=rec.reason)
                if self._deny_reason is not None:               # R31: stored keys still count as skipped
                    for later in keys[i + 1:]:
                        recs[later] = self._after_denial(later)
                break
        finished = self._clock()
        sidecar = self._build_sidecar(run_range, keys, recs, aborted_after, started, finished)
        path, replaced = self._write_sidecar(run_range, sidecar)
        c = sidecar["outcomes"]
        self._log.info("run_end", status=sidecar["status"], expected=sidecar["expected"],
                       received=sidecar["received"], sidecar=str(path), **c)
        return RunResult(status=sidecar["status"],
                         outcomes={k: r.outcome for k, r in recs.items() if not r.not_attempted},
                         http_calls=self._calls, sidecar_path=path, sidecar_replaced_previous=replaced)

    def _after_denial(self, key: str) -> _Rec:
        existing = self._manifest.get_by_key(self.source, key)
        if existing is None:
            return _Rec(None, self._deny_reason or "", None, True)
        try:
            return _Rec(Outcome.SKIPPED, "manifest already holds key", self._known_outputs(key, existing))
        except (RawIntegrityError, UnsafeArchiveError, OSError) as e:
            return _Rec(Outcome.UNREADABLE, f"stored archive failed integrity check: {e}")

    # ---- one key
    def _do_key(self, key: str) -> tuple[_Rec, bool]:
        try:
            return self._do_key_inner(key)
        except _Stop as s:
            self._deny_reason = s.reason
            return s.rec, True

    def _gate(self, key: str, host: str, interval: float, attempts: int, cause: str) -> None:
        """Access state, then limiter, then cap (R31 step 2 and 3). Raises _Stop when the request must not go out."""
        reason = None
        if self._state_broken is not None:
            raise _Stop(_Rec(None, self._state_broken, None, True), self._state_broken) if attempts == 1 \
                else _Stop(_Rec(Outcome.UNREADABLE, f"{cause}; {self._state_broken}"), self._state_broken)
        try:
            self._access.verify_writable(host)
            st = self._access.status(host)
            if st.reason:
                reason = st.reason
        except AccessStateError as e:
            self._log.error("access_state_unreadable", key=key, host=host, detail=str(e))
            reason = f"access state unreadable: {e}"
        if reason is None:
            self._limiter.acquire(host, interval)
            if not self._counter.reserve(self._daily_cap):
                reason = f"daily cap {self._daily_cap} reached"
                self._log.warning("cap_reached", key=key, cap=self._daily_cap, date=self._access.today())
        if reason is not None:
            if attempts == 1:
                raise _Stop(_Rec(None, reason, None, True), reason)
            raise _Stop(_Rec(Outcome.UNREADABLE, f"{cause}; {reason}"), reason)

    def _do_key_inner(self, key: str) -> tuple[_Rec, bool]:
        existing = self._manifest.get_by_key(self.source, key)
        if existing is not None:
            try:
                rec = _Rec(Outcome.SKIPPED, "manifest already holds key", self._known_outputs(key, existing))
            except (RawIntegrityError, UnsafeArchiveError, OSError) as e:
                rec = _Rec(Outcome.UNREADABLE, f"stored archive failed integrity check: {e}")
                self._log.error("unreadable", key=key, url=self.url(key), attempts=0, reason=rec.reason)
                return rec, False
            self._log.info("skipped", key=key, url=self.url(key), attempts=0, reason=rec.reason)
            return rec, False

        url = self.url(key)
        host = (urlparse(url).hostname or "").lower()
        if not host:
            raise ValueError(f"url({key!r}) has no host: {url!r}")
        interval = self._cfg.rate_limits.interval_for(host)
        if self._mode is not AccessMode.HONEST:
            interval = max(interval, _FLOOR_INTERVAL_S)
        sent: dict[str, str] | None = None
        if self._browser is None:
            sent = self._build_headers(key, url)

        attempts = 0
        resp: Response | None = None
        received_at: datetime | None = None
        final_url = url
        reported: Mapping[str, str] | None = None
        cause = ""
        while True:
            attempts += 1
            self._gate(key, host, interval, attempts, cause)
            self._calls += 1
            try:
                if self._browser is not None:
                    br = self._browser.fetch(url)
                    resp = Response(br.status, br.headers, br.body, url)
                    final_url, reported = br.final_url, br.request_headers
                else:
                    resp = self._transport.get(url, headers=dict(sent), timeout=self._timeout)
                    final_url = getattr(resp, "url", "") or url
            except TransportError as e:
                resp, cause = None, f"transport error: {e}"
            else:
                received_at = self._clock()
                blocked = self._block_reason(resp)
                if blocked is not None:
                    reason, fields = blocked
                    self._record_block(key, host, reason, fields)
                    return self._finish(key, url, _Rec(Outcome.UNREADABLE, reason), attempts, resp, abort=True)
                if 500 <= resp.status <= 599:
                    cause = f"http {resp.status}"
                else:
                    break
            if attempts >= MAX_ATTEMPTS:
                return self._finish(key, url, _Rec(Outcome.UNREADABLE, f"{cause} after {attempts} attempts"),
                                    attempts, resp, abort=True)
            self._log.warning("retry", key=key, url=url, attempt=attempts, reason=cause)
            self._sleep(2 ** (attempts - 1))

        if self._browser is not None and urldefrag(final_url)[0] != urldefrag(url)[0]:
            return self._finish(key, url, _Rec(Outcome.UNREADABLE, f"redirected to {final_url}"),
                                attempts, resp, abort=False)
        enc = next((v for k, v in resp.headers.items() if k.lower() == "content-encoding"), "")
        if enc.strip() and enc.strip().lower() != "identity":
            return self._finish(key, url, _Rec(Outcome.UNREADABLE, f"undecoded content-encoding {enc.strip()}"),
                                attempts, resp, abort=False)
        rec = self._judge(key, url, resp, received_at, final_url, dict(sent) if sent is not None
                          else (dict(reported) if reported is not None else None))
        return self._finish(key, url, rec, attempts, resp, abort=False)

    # ---- headers
    def _build_headers(self, key: str, url: str) -> dict[str, str]:
        extra = _validate_header_map(self.headers_for(key, url), self._denied, "headers_for")
        enc = getattr(self._transport, "accept_encoding", "identity")
        out = {"User-Agent": self._ua, "Accept-Encoding": enc}
        for name, value in extra.items():
            if name.lower() == "user-agent":
                out["User-Agent"] = value
            else:
                out[name] = value
        return out

    # ---- blocks
    def _block_reason(self, resp: Response) -> tuple[str, dict] | None:
        if resp.status in _BLOCK_STATUSES:
            return f"blocked http {resp.status}", {"status": resp.status}
        if self._signatures:
            head = resp.body[:_SIGNATURE_SCAN_BYTES]
            for text, raw in self._signatures:
                if raw in head:
                    return f"challenge page: {text}", {"signature": text}
        return None

    def _record_block(self, key: str, host: str, reason: str, fields: dict) -> None:
        detail = reason.removeprefix("blocked ")
        halt = None
        try:
            self._access.record_block(host=host, detail=detail, run_id=self._run_id)
            halt = self._access.last_halt
        except (AccessStateError, OSError) as e:
            self._state_broken = f"access state unreadable: block not persisted: {e}"
            self._log.error("access_state_unreadable", key=key, host=host, detail=str(e))
        self._log.error("blocked", key=key, host=host, source=self.source, **fields)
        if halt is not None:
            self._log.error("halted", source=self.source, host=host, detail=halt.detail)
            try:
                self._notifier.notify("Invest OS: source halted", f"{self.source} halted: {halt.detail} (host {host})")
            except Exception as e:                              # the alert never changes the outcome
                self._log.warning("notify_failed", reason=type(e).__name__)

    def _judge(self, key: str, url: str, resp: Response, received_at: datetime, final_url: str,
               sent: Mapping[str, str] | None) -> _Rec:
        try:
            v = self.check_body(key, resp)
        except Exception as e:
            return _Rec(Outcome.UNREADABLE, f"check_body error: {type(e).__name__}: {e}")
        if not isinstance(v, BodyVerdict) or not isinstance(v.outcome, Outcome) \
                or v.outcome in (Outcome.SKIPPED, Outcome.INCOMPLETE, Outcome.REJECTED):
            return _Rec(Outcome.UNREADABLE, "invalid verdict")
        reason = v.reason or ""
        if v.outcome is Outcome.UNREADABLE:
            return _Rec(Outcome.UNREADABLE, reason or "check_body: unreadable body")
        if v.outcome is Outcome.MISSING_HOLIDAY:
            return _Rec(Outcome.MISSING_HOLIDAY, reason or "source has no file for key")
        # FETCHED / FETCHED_EMPTY from here
        if not 200 <= resp.status <= 299:
            return _Rec(Outcome.UNREADABLE, f"non-2xx status {resp.status}")
        if (self.page_limit is not None and v.outcome is Outcome.FETCHED
                and v.record_count is not None and v.record_count >= self.page_limit):
            return _Rec(Outcome.INCOMPLETE, f"page limit {self.page_limit} reached")
        members: list[tuple[str, bytes]] = []
        if self.archive:
            try:
                members = safe_unzip(resp.body, limits=self.unzip_limits)
            except UnsafeArchiveError as e:
                return _Rec(Outcome.UNREADABLE, f"unsafe archive: {e}")
        trade_date: str | None = None
        if self._date_check == "enforced":
            bad, trade_date = self._date_check_files(key, resp.body, members)
            if bad is not None:
                return bad
        outputs: list[tuple[str, str]] = []
        # members first, zip last: the zip key is what makes a re-run skip, so it is written only
        # once every member is safely in the manifest
        for name, data in members:
            e = self._store(f"{key}/{name}", data, url, received_at, final_url, sent)
            outputs.append((e.logical_key, e.sha256))
        e = self._store(key, resp.body, url, received_at, final_url, sent)
        outputs.append((e.logical_key, e.sha256))
        outputs.sort()
        return _Rec(v.outcome, reason, outputs, trade_date=trade_date)

    # ---- date check (R36 to R39)
    def _date_check_files(self, key: str, body: bytes,
                          members: list[tuple[str, bytes]]) -> tuple[_Rec | None, str | None]:
        """Compare the dates read inside the file (or every member) with the key. (None, iso) when it passes."""
        target = self.logical_date(key)
        if not self.archive:
            bad = self._date_check_one(key, target, None, body)
            return bad, (None if bad else target.isoformat())
        found = False
        for name, data in members:
            bad = self._date_check_one(key, target, name, data, archive=True)
            if bad is not None:
                return bad, None
            found = found or self._last_member_had_evidence
        if not found:
            return self._rejected("date_missing: no archive member carries a trade date (0 rows)",
                                  [], "", 0), None
        return None, target.isoformat()

    def _date_check_one(self, key: str, target: date, member: str | None, body: bytes,
                        archive: bool = False) -> _Rec | None:
        where = f" (member {member})" if member is not None else ""
        self._last_member_had_evidence = False
        try:
            ev = self.internal_dates(key, member, body)
        except Exception as e:
            return _Rec(Outcome.UNREADABLE, f"date check error{where}: {type(e).__name__}: {e}")
        if ev is None:
            if archive:
                return None
            return self._rejected("date_missing: no trade date found (adapter reported no evidence)", [], "", 0)
        dates = getattr(ev, "dates", None)
        rows = getattr(ev, "rows_examined", None)
        field = getattr(ev, "field", None)
        if (not isinstance(ev, DateEvidence) or not isinstance(dates, (set, frozenset))
                or any(not isinstance(d, date) or isinstance(d, datetime) for d in dates)
                or not isinstance(field, str) or isinstance(rows, bool) or not isinstance(rows, int) or rows < 0):
            return _Rec(Outcome.UNREADABLE, f"date check error{where}: internal_dates returned invalid evidence")
        self._last_member_had_evidence = True
        iso = sorted(d.isoformat() for d in dates)
        if not dates:
            return self._rejected(f"date_missing: no trade date found in {field} ({rows} rows){where}",
                                  iso, field, rows)
        if dates != {target}:
            return self._rejected(f"date_mismatch: key {key} but file has {','.join(iso)} in {field}{where}",
                                  iso, field, rows)
        return None

    @staticmethod
    def _rejected(reason: str, file_dates: list[str], field: str, rows: int) -> _Rec:
        return _Rec(Outcome.REJECTED, reason,
                    check={"file_dates": file_dates, "field": field, "rows_examined": rows})

    def _store(self, logical_key: str, data: bytes, url: str, fetched_at: datetime,
               final_url: str, sent: Mapping[str, str] | None) -> ManifestEntry:
        return self._manifest.store(self.source, data, url=url, source_version=self.source_version,
                                    logical_key=logical_key, run_id=self._run_id, fetched_at=fetched_at,
                                    access_mode=self._mode.value,
                                    request_headers=None if sent is None else dict(sent),
                                    final_url=final_url, fetch_host=self._hostname())

    def _known_outputs(self, key: str, entry: ManifestEntry) -> list[tuple[str, str]]:
        rows = [(key, entry.sha256)]
        if self.archive:
            # members are named inside the stored zip itself; each is looked up by exact key, so only the
            # public get_by_key/read_bytes are used and a stray "<key>/..." top-level key is never pulled in
            for name, _ in safe_unzip(self._manifest.read_bytes(self.source, entry.sha256), limits=self.unzip_limits):
                m = self._manifest.get_by_key(self.source, f"{key}/{name}")
                if m is not None:
                    rows.append((m.logical_key, m.sha256))
        return sorted(rows)

    def _finish(self, key: str, url: str, rec: _Rec, attempts: int, resp: Response | None,
                abort: bool) -> tuple[_Rec, bool]:
        fields = dict(key=key, url=url, attempts=attempts, reason=rec.reason)
        if resp is not None:
            fields["size"] = len(resp.body)
        if rec.outcome is Outcome.UNREADABLE and abort:
            self._log.error("unreadable", **fields)
        elif rec.outcome is Outcome.REJECTED:
            self._log.warning("rejected", key=key, url=url, reason=rec.reason, **(rec.check or {}))
        else:
            if rec.trade_date is not None:
                fields["trade_date"] = rec.trade_date
            self._log.info(rec.outcome.value, **fields)
        return rec, abort

    # ---- sidecar
    def _build_sidecar(self, run_range: RunRange, keys: list[str], recs: dict[str, _Rec],
                       aborted_after: str | None, started: datetime, finished: datetime) -> dict:
        counts = {k: 0 for k in _COUNT_KEYS}
        gaps, outputs = [], []
        for key in keys:
            rec = recs.get(key)
            if rec is None:
                counts["not_attempted"] += 1
                gaps.append({"key": key, "outcome": "not_attempted", "reason": f"run aborted after {aborted_after}"})
                continue
            if rec.not_attempted:
                counts["not_attempted"] += 1
                gaps.append({"key": key, "outcome": "not_attempted", "reason": rec.reason or "unspecified"})
                continue
            counts[rec.outcome.value] += 1
            if rec.outcome in (Outcome.UNREADABLE, Outcome.INCOMPLETE, Outcome.REJECTED):
                gaps.append({"key": key, "outcome": rec.outcome.value, "reason": rec.reason or "unspecified"})
            for k, h in rec.outputs or []:
                outputs.append({"key": k, "sha256": h})
        gaps.sort(key=lambda g: g["key"])
        outputs.sort(key=lambda o: o["key"])
        received = (counts["fetched"] + counts["fetched_empty"] + counts["skipped"] + counts["missing_holiday"])
        if aborted_after is not None:
            status = "aborted"
        elif keys and not gaps:
            status = "complete"
        else:
            status = "incomplete"
        return {
            "schema": 2, "access_mode": self._mode.value, "source": self.source,
            "source_version": self.source_version, "run_id": self._run_id,
            "range": {"start": run_range.start.isoformat(), "end": run_range.end.isoformat()},
            "started_at": _fmt_ts(started), "finished_at": _fmt_ts(finished), "status": status,
            "expected": len(keys), "received": received, "outcomes": counts, "gaps": gaps, "outputs": outputs,
            "date_check": self._date_check,
        }

    def _write_sidecar(self, run_range: RunRange, obj: dict) -> tuple[Path, bool]:
        d = self._sidecar_dir
        d.mkdir(parents=True, exist_ok=True)
        stem = f"{run_range.start.isoformat()}_{run_range.end.isoformat()}"
        target = d / f"{stem}.json"
        replaced = False
        if os.path.lexists(target):
            existing_status = None
            try:
                prev = json.loads(target.read_bytes().decode("utf-8"))
            except (ValueError, OSError):                       # invalid JSON / bad UTF-8 / unreadable
                os.replace(target, d / f"{stem}.corrupt-{self._run_id}.json")
            else:
                if isinstance(prev, dict) and prev.get("schema") not in (1, 2):
                    os.replace(target, d / f"{stem}.corrupt-{self._run_id}.json")      # unknown schema: like bad JSON
                else:
                    if isinstance(prev, dict):
                        existing_status = prev.get("status")
                    if existing_status == "complete" and obj["status"] != "complete":
                        target = d / f"{stem}.attempt-{self._run_id}.json"
                    else:
                        replaced = True
        payload = (json.dumps(obj, sort_keys=True, separators=(",", ":"), ensure_ascii=False) + "\n").encode("utf-8")
        tmp = d / f".tmp-{uuid.uuid4().hex}"
        try:
            fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o644)
            try:
                view = memoryview(payload)
                while view:
                    view = view[os.write(fd, view):]
                os.fsync(fd)
            finally:
                os.close(fd)
            os.replace(tmp, target)
            _fsync_dir(d)
        finally:
            try:
                os.unlink(tmp)
            except FileNotFoundError:
                pass
        return target, replaced
