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
from urllib.parse import urlparse

from investos import __version__
from investos.acquire.manifest import ManifestEntry, ManifestStore
from investos.core import log
from investos.core.config import Config

__all__ = [
    "Collector", "Outcome", "Response", "Transport", "TransportError", "BodyVerdict", "RunRange",
    "UnzipLimits", "RunResult", "safe_unzip", "UnsafeArchiveError",
]

MAX_ATTEMPTS = 4                                  # first try + 3 retries
_BLOCK_STATUSES = frozenset({401, 403, 451})
_FORBIDDEN_HEADERS = frozenset({"user-agent", "referer", "origin", "cookie", "x-forwarded-for", "x-real-ip"})
# same rule as specs/core_manifest.md (kept here: the manifest module exposes no public validator, test A6)
_SOURCE_RE = re.compile(r"[a-z0-9][a-z0-9_]{0,63}", re.ASCII)
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


_COUNT_KEYS = tuple(o.value for o in Outcome) + ("not_attempted",)


@dataclass(frozen=True)
class Response:
    status: int
    headers: Mapping[str, str]
    body: bytes
    url: str


class TransportError(Exception):
    """Timeouts, connection reset, DNS failure."""


class Transport(Protocol):
    def get(self, url: str, *, headers: Mapping[str, str], timeout: float) -> Response:
        """Must NOT follow redirects; 3xx is returned as-is."""
        ...


@dataclass(frozen=True)
class BodyVerdict:
    outcome: Outcome
    reason: str = ""
    record_count: int | None = None


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
    outcome: Outcome
    reason: str = ""
    outputs: list[tuple[str, str]] | None = None      # (key, sha256) rows for the sidecar


def _fmt_ts(dt: datetime) -> str:
    return dt.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%fZ")


def _fsync_dir(path: Path) -> None:
    fd = os.open(path, os.O_RDONLY)
    try:
        os.fsync(fd)
    finally:
        os.close(fd)


class Collector(ABC):
    source: ClassVar[str]
    source_version: ClassVar[str]
    page_limit: ClassVar[int | None] = None
    archive: ClassVar[bool] = False
    extra_headers: ClassVar[Mapping[str, str]] = {}
    timeout_s: ClassVar[float] = 30.0
    unzip_limits: ClassVar[UnzipLimits] = UnzipLimits()

    def __init__(self, *, transport: Transport, manifest: ManifestStore, cfg: Config,
                 run_id: str | None = None, sidecar_dir: Path | None = None,
                 clock: Callable[[], datetime] | None = None,
                 monotonic: Callable[[], float] | None = None,
                 sleeper: Callable[[float], None] | None = None) -> None:
        source = getattr(type(self), "source", None)
        if not isinstance(source, str) or not _SOURCE_RE.fullmatch(source):
            raise ValueError(f"invalid source name {source!r}")
        if not isinstance(getattr(type(self), "source_version", None), str) or not self.source_version:
            raise ValueError("source_version must be a non-empty str class attribute")
        if self.page_limit is not None and (isinstance(self.page_limit, bool)
                                            or not isinstance(self.page_limit, int) or self.page_limit < 1):
            raise ValueError("page_limit must be None or an int >= 1")
        for name in self.extra_headers:
            if not isinstance(name, str) or name.lower() in _FORBIDDEN_HEADERS:
                raise ValueError(f"extra header {name!r} is not allowed (honest headers only)")
        self._headers = {"User-Agent": f"investos/{__version__}", **self.extra_headers}
        self._transport = transport
        self._manifest = manifest
        self._cfg = cfg
        rid = run_id if run_id is not None else log.get_run_id()
        if not isinstance(rid, str) or not _RUN_ID_RE.fullmatch(rid):
            raise ValueError(f"invalid run_id {rid!r}")
        self._run_id = rid
        self._sidecar_dir = Path(sidecar_dir) if sidecar_dir is not None \
            else Path(cfg.zones["manifests"]) / f"{source}.runs"
        self._clock = clock or (lambda: datetime.now(timezone.utc))
        self._mono = monotonic or time.monotonic
        self._sleep = sleeper or time.sleep
        self._last_start: dict[str, float] = {}                 # per-host, per-instance
        self._log = log.get_logger(f"collect.{source}")
        self._calls = 0

    # ---- subclass interface
    @abstractmethod
    def keys(self, run_range: RunRange) -> Iterable[str]: ...

    @abstractmethod
    def url(self, key: str) -> str: ...

    @abstractmethod
    def check_body(self, key: str, resp: Response) -> BodyVerdict: ...

    # ---- run
    def run(self, run_range: RunRange) -> RunResult:
        if not isinstance(run_range, RunRange) or run_range.end < run_range.start:
            raise ValueError("run_range must be a RunRange with end >= start")
        keys = list(dict.fromkeys(self.keys(run_range)))        # propagates; collapses duplicates
        for k in keys:
            if not isinstance(k, str) or not k:
                raise TypeError(f"keys() must yield non-empty str, got {k!r}")
        started = self._clock()
        self._calls = 0
        recs: dict[str, _Rec] = {}
        aborted_after: str | None = None
        for key in keys:
            rec, abort = self._do_key(key)
            recs[key] = rec
            if abort:
                aborted_after = key
                self._log.error("run_aborted", key=key, reason=rec.reason)
                break
        finished = self._clock()
        sidecar = self._build_sidecar(run_range, keys, recs, aborted_after, started, finished)
        path, replaced = self._write_sidecar(run_range, sidecar)
        c = sidecar["outcomes"]
        self._log.info("run_end", status=sidecar["status"], expected=sidecar["expected"],
                       received=sidecar["received"], sidecar=str(path), **c)
        return RunResult(status=sidecar["status"], outcomes={k: r.outcome for k, r in recs.items()},
                         http_calls=self._calls, sidecar_path=path, sidecar_replaced_previous=replaced)

    # ---- one key
    def _do_key(self, key: str) -> tuple[_Rec, bool]:
        existing = self._manifest.get_by_key(self.source, key)
        if existing is not None:
            rec = _Rec(Outcome.SKIPPED, "manifest already holds key", self._known_outputs(key, existing))
            self._log.info("skipped", key=key, url=self.url(key), attempts=0, reason=rec.reason)
            return rec, False

        url = self.url(key)
        host = (urlparse(url).hostname or "").lower()
        if not host:
            raise ValueError(f"url({key!r}) has no host: {url!r}")
        interval = self._cfg.rate_limits.interval_for(host)

        attempts = 0
        resp: Response | None = None
        received_at: datetime | None = None
        cause = ""
        while True:
            attempts += 1
            self._pace(host, interval)
            self._calls += 1
            try:
                resp = self._transport.get(url, headers=dict(self._headers), timeout=self.timeout_s)
            except TransportError as e:
                resp, cause = None, f"transport error: {e}"
            else:
                received_at = self._clock()
                if resp.status in _BLOCK_STATUSES:
                    return self._finish(key, url, _Rec(Outcome.UNREADABLE, f"blocked http {resp.status}"),
                                        attempts, resp, abort=True)
                if resp.status == 429 or 500 <= resp.status <= 599:
                    cause = f"http {resp.status}"
                else:
                    break
            if attempts >= MAX_ATTEMPTS:
                return self._finish(key, url, _Rec(Outcome.UNREADABLE, f"{cause} after {attempts} attempts"),
                                    attempts, resp, abort=True)
            self._log.warning("retry", key=key, url=url, attempt=attempts, reason=cause)
            self._sleep(2 ** (attempts - 1))

        rec = self._judge(key, url, resp, received_at)
        return self._finish(key, url, rec, attempts, resp, abort=False)

    def _pace(self, host: str, interval: float) -> None:
        last = self._last_start.get(host)
        if last is not None:
            wait = interval - (self._mono() - last)
            if wait > 0:
                self._sleep(wait)
        self._last_start[host] = self._mono()

    def _judge(self, key: str, url: str, resp: Response, received_at: datetime) -> _Rec:
        try:
            v = self.check_body(key, resp)
        except Exception as e:
            return _Rec(Outcome.UNREADABLE, f"check_body error: {type(e).__name__}: {e}")
        if not isinstance(v, BodyVerdict) or not isinstance(v.outcome, Outcome) \
                or v.outcome in (Outcome.SKIPPED, Outcome.INCOMPLETE):
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
        outputs: list[tuple[str, str]] = []
        # members first, zip last: the zip key is what makes a re-run skip, so it is written only
        # once every member is safely in the manifest
        for name, data in members:
            e = self._store(f"{key}/{name}", data, url, received_at)
            outputs.append((e.logical_key, e.sha256))
        e = self._store(key, resp.body, url, received_at)
        outputs.append((e.logical_key, e.sha256))
        outputs.sort()
        return _Rec(v.outcome, reason, outputs)

    def _store(self, logical_key: str, data: bytes, url: str, fetched_at: datetime) -> ManifestEntry:
        return self._manifest.store(self.source, data, url=url, source_version=self.source_version,
                                    logical_key=logical_key, run_id=self._run_id, fetched_at=fetched_at)

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
        else:
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
            counts[rec.outcome.value] += 1
            if rec.outcome in (Outcome.UNREADABLE, Outcome.INCOMPLETE):
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
            "schema": 1, "source": self.source, "source_version": self.source_version, "run_id": self._run_id,
            "range": {"start": run_range.start.isoformat(), "end": run_range.end.isoformat()},
            "started_at": _fmt_ts(started), "finished_at": _fmt_ts(finished), "status": status,
            "expected": len(keys), "received": received, "outcomes": counts, "gaps": gaps, "outputs": outputs,
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
