"""RSS poller, the first real collector adapter (spec: specs/collect_rss.md, brief S1-T6).

Honest mode only. One ``RssFeedCollector`` per (feed, 15-minute IST slot); ``poll_cycle`` runs one slot over the
feed table. The body is stored byte-exact through the manifest only when its content digest changed (R8a); feed
state, the gap ledger and the freshness alarms live next to it. Time, sleeping and the notifier are injected: this
module never reads the system clock. The only XML reading is ``investos.parse.rss_items.parse_feed``.
"""
from __future__ import annotations

import csv
import fcntl
import hashlib
import json
import os
import re
import time
import uuid
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Callable, Iterable, Iterator, Mapping, Sequence
from urllib.parse import urlsplit
from zoneinfo import ZoneInfo

from investos.acquire.access import AccessStateError, _append, _dump, _flock, _lock_of
from investos.acquire.base import AccessMode, BodyVerdict, Collector, Outcome, Response, RunRange
from investos.acquire.manifest import ManifestStore
from investos.acquire.registry import register_adapter
from investos.core import log
from investos.core.config import Config
from investos.core.notify import Notifier, NullNotifier
from investos.parse.rss_items import FeedParseError, parse_feed

__all__ = ["FeedRow", "FeedTableError", "load_feeds", "slot_of", "is_poll_slot", "content_digest",
           "RssFeedCollector", "poll_cycle", "CycleResult", "GapLedgerError", "read_gaps", "read_state",
           "judge_feed", "FeedVerdict", "CalendarView", "feed_table_path", "state_dir_of", "gaps_path_of",
           "slot_start", "latest_due_slot", "KEY_RE", "due_feeds", "ADAPTERS"]

IST = ZoneInfo("Asia/Kolkata")
_UTC = timezone.utc
SLOT_RE = re.compile(r"\d{8}T\d{4}", re.ASCII)
KEY_RE = re.compile(r"([a-z0-9][a-z0-9_]{0,47})/(\d{8}T\d{4})", re.ASCII)
_FEED_ID_RE = re.compile(r"[a-z0-9][a-z0-9_]{0,47}", re.ASCII)
_TS_RE = re.compile(r"\d{4}-\d\d-\d\dT\d\d:\d\d:\d\d\.\d{6}Z", re.ASCII)
_DATE_RE = re.compile(r"\d{4}-\d\d-\d\d", re.ASCII)
_SHA_RE = re.compile(r"[0-9a-f]{64}", re.ASCII)
_LASTBUILD_RE = re.compile(rb"<lastBuildDate>[^<]{0,200}</lastBuildDate>")
_FEED_HEADER = ("feed_id", "url", "kind", "publisher", "enabled", "stale_after_min", "stale_scope", "status", "note")
_KINDS = ("corporate", "news")
_SCOPES = ("trading_hours", "always")
_STATUSES = ("confirmed", "to_confirm")
_MAX_URL = 300
_OVERNIGHT = frozenset({(1, 0), (3, 0), (5, 0)})
_CYCLE_BUDGET_S = 10 * 60
_LOCK_WAIT_S = 5.0
_ALARM_GRACE = timedelta(minutes=10)
_ALARM_KINDS = ("poll_missed", "content_stale")
_STATE_KEYS = frozenset({"schema", "feed_id", "last_attempt_at", "last_ok_slot", "last_ok_at", "last_stored_sha",
                         "last_content_digest", "last_change_at", "newest_pub_ist", "consecutive_failures",
                         "alerted"})
_GAP_KEYS = frozenset({"schema", "feed_id", "kind", "from_slot", "to_slot", "slots", "reason", "detected_at",
                       "run_id"})
_MAX_REDIRECT_TEXT = 200
_OK_OUTCOMES = frozenset({"fetched", "fetched_empty", "skipped"})
_FAIL_OUTCOMES = frozenset({"unreadable", "incomplete", "not_attempted", "rejected", "missing_holiday"})
_MAX_LOOKBACK_SLOTS = 96 * 400


# --------------------------------------------------------------------------- feed table (R3)
class FeedTableError(Exception):
    """The feed table is invalid; the message names file, row and column."""


@dataclass(frozen=True)
class FeedRow:
    feed_id: str
    url: str
    kind: str
    publisher: str
    enabled: bool
    stale_after_min: int
    stale_scope: str
    status: str
    note: str


def feed_table_path(cfg: Config) -> Path:
    return Path(cfg.repo_root) / "config" / "rss_feeds.csv"


def _bad(path: Path, row: int, col: str, msg: str) -> FeedTableError:
    return FeedTableError(f"{path.name}: row {row}, column {col}: {msg}")


def _check_url(path: Path, row: int, url: str) -> None:
    if len(url) > _MAX_URL:
        raise _bad(path, row, "url", f"longer than {_MAX_URL} characters")
    if any(ord(c) <= 32 or ord(c) >= 127 for c in url):
        raise _bad(path, row, "url", "contains a space, control or non-ASCII character")
    try:
        parts = urlsplit(url)
        parts.port
    except ValueError as e:
        raise _bad(path, row, "url", f"not a valid URL: {e}") from None
    if parts.scheme != "https":
        raise _bad(path, row, "url", "must be an https URL")
    if not parts.netloc or not parts.hostname:
        raise _bad(path, row, "url", "has no host")
    if "@" in parts.netloc:
        raise _bad(path, row, "url", "must not carry user info")
    if "#" in url:
        raise _bad(path, row, "url", "must not carry a fragment")
    if parts.netloc != parts.netloc.lower():
        raise _bad(path, row, "url", "host must be lower case")


def load_feeds(path: Path) -> tuple[FeedRow, ...]:
    """Read and validate the feed table (read-only). Disabled rows are validated too."""
    path = Path(path)
    try:
        raw = path.read_bytes()
    except OSError as e:
        raise FeedTableError(f"{path.name}: cannot read: {e}") from None
    if b"\x00" in raw:
        raise FeedTableError(f"{path.name}: row 1, column -: NUL byte in file")
    try:
        text = raw.decode("utf-8")
    except UnicodeDecodeError:
        raise FeedTableError(f"{path.name}: row 1, column -: file is not UTF-8") from None
    try:
        records = list(csv.reader(text.splitlines(), strict=True))
    except csv.Error as e:
        raise FeedTableError(f"{path.name}: row 1, column -: not valid CSV: {e}") from None
    if not records or tuple(records[0]) != _FEED_HEADER:
        raise FeedTableError(f"{path.name}: row 1, column header: expected exactly {','.join(_FEED_HEADER)}")
    out: list[FeedRow] = []
    ids: dict[str, int] = {}
    urls: dict[str, int] = {}
    for n, rec in enumerate(records[1:], start=2):
        if not rec:
            continue
        if len(rec) != len(_FEED_HEADER):
            raise _bad(path, n, "-", f"expected {len(_FEED_HEADER)} columns, found {len(rec)}")
        r = dict(zip(_FEED_HEADER, rec))
        fid = r["feed_id"]
        if not _FEED_ID_RE.fullmatch(fid):
            raise _bad(path, n, "feed_id", f"{fid!r} does not match [a-z0-9][a-z0-9_]{{0,47}}")
        if fid in ids:
            raise _bad(path, n, "feed_id", f"duplicate of row {ids[fid]}")
        _check_url(path, n, r["url"])
        if r["url"] in urls:
            raise _bad(path, n, "url", f"duplicate of row {urls[r['url']]}")
        if r["kind"] not in _KINDS:
            raise _bad(path, n, "kind", f"must be one of {', '.join(_KINDS)}")
        if not r["publisher"].strip():
            raise _bad(path, n, "publisher", "must not be empty")
        if r["enabled"] not in ("yes", "no"):
            raise _bad(path, n, "enabled", "must be yes or no")
        if not re.fullmatch(r"[0-9]{1,9}", r["stale_after_min"]) or int(r["stale_after_min"]) < 15:
            raise _bad(path, n, "stale_after_min", "must be an integer >= 15")
        if r["stale_scope"] not in _SCOPES:
            raise _bad(path, n, "stale_scope", f"must be one of {', '.join(_SCOPES)}")
        if r["status"] not in _STATUSES:
            raise _bad(path, n, "status", f"must be one of {', '.join(_STATUSES)}")
        ids[fid] = n
        urls[r["url"]] = n
        out.append(FeedRow(fid, r["url"], r["kind"], r["publisher"], r["enabled"] == "yes",
                           int(r["stale_after_min"]), r["stale_scope"], r["status"], r["note"]))
    return tuple(out)


# --------------------------------------------------------------------------- slots (R2, R9)
def slot_of(now_utc: datetime) -> str:
    """The IST 15-minute slot of an aware instant: ``YYYYMMDDTHHMM``."""
    if not isinstance(now_utc, datetime) or now_utc.tzinfo is None or now_utc.utcoffset() is None:
        raise ValueError("slot_of needs an aware datetime")
    t = now_utc.astimezone(IST)
    return t.strftime("%Y%m%dT") + f"{t.hour:02d}{t.minute - t.minute % 15:02d}"


def slot_start(slot: str) -> datetime:
    """The aware IST start time of a slot string."""
    if not SLOT_RE.fullmatch(slot):
        raise ValueError(f"bad slot {slot!r}")
    return datetime(int(slot[:4]), int(slot[4:6]), int(slot[6:8]), int(slot[9:11]), int(slot[11:13]), tzinfo=IST)


def _slot_str(t: datetime) -> str:
    return t.astimezone(IST).strftime("%Y%m%dT%H%M")


def is_poll_slot(slot: str) -> bool:
    """R9: every quarter hour from 07:00 to 23:45 IST plus 01:00, 03:00 and 05:00."""
    t = slot_start(slot)
    if t.minute % 15:
        return False
    return t.hour >= 7 or (t.hour, t.minute) in _OVERNIGHT


def latest_due_slot(now: datetime) -> str:
    """The latest scheduled slot that is at least 10 minutes old at ``now``."""
    t = slot_start(slot_of(now - _ALARM_GRACE))
    for _ in range(32):
        if is_poll_slot(_slot_str(t)):
            return _slot_str(t)
        t -= timedelta(minutes=15)
    return _slot_str(t)


def _scheduled_between(after: str, before: str) -> list[str]:
    """Scheduled slots strictly between two slots (bounded look-back)."""
    out: list[str] = []
    t = slot_start(after) + timedelta(minutes=15)
    end = slot_start(before)
    n = 0
    while t < end and n < _MAX_LOOKBACK_SLOTS:
        s = _slot_str(t)
        if is_poll_slot(s):
            out.append(s)
        t += timedelta(minutes=15)
        n += 1
    return out


# --------------------------------------------------------------------------- content digest (R8a)
def content_digest(body: bytes) -> str:
    """sha256 of the body with every ``<lastBuildDate>`` element removed (byte regex, no XML parsing)."""
    return hashlib.sha256(_LASTBUILD_RE.sub(b"", body)).hexdigest()


def _fmt_ts(dt: datetime) -> str:
    return dt.astimezone(_UTC).strftime("%Y-%m-%dT%H:%M:%S.%fZ")


def _parse_ts(s: str) -> datetime:
    return datetime.strptime(s, "%Y-%m-%dT%H:%M:%S.%fZ").replace(tzinfo=_UTC)


# --------------------------------------------------------------------------- the collector (R1, R4, R5, R7, R8)
@register_adapter
class RssFeedCollector(Collector):
    source = "rss"
    source_version = "rss-v1"
    access_mode = AccessMode.HONEST
    date_keyed = False
    date_check_waiver = None
    page_limit = None
    archive = False
    extra_headers: Mapping[str, str] = {}

    def __init__(self, feed: FeedRow, slot: str, *, last_stored_sha: str | None = None,
                 last_content_digest: str | None = None, **collector_kwargs) -> None:
        super().__init__(**collector_kwargs)
        if not SLOT_RE.fullmatch(slot):
            raise ValueError(f"bad slot {slot!r}")
        self.feed = feed
        self.slot = slot
        self._last_sha = last_stored_sha
        self._last_digest = last_content_digest

    @property
    def key(self) -> str:
        return f"{self.feed.feed_id}/{self.slot}"

    def keys(self, run_range: RunRange) -> Iterable[str]:
        yield self.key

    def url(self, key: str) -> str:
        return self.feed.url

    def check_body(self, key: str, resp: Response) -> BodyVerdict:
        if 300 <= resp.status <= 399:
            loc = next((v for k, v in resp.headers.items() if k.lower() == "location"), "")
            return BodyVerdict(Outcome.UNREADABLE, "redirect to " + str(loc)[:_MAX_REDIRECT_TEXT])
        if not 200 <= resp.status <= 299:
            return BodyVerdict(Outcome.UNREADABLE, f"http {resp.status}")
        try:
            items = parse_feed(resp.body)
        except FeedParseError as e:
            return BodyVerdict(Outcome.UNREADABLE, f"not a usable RSS 2.0 feed: {e}"[:300])
        if not items:
            return BodyVerdict(Outcome.FETCHED_EMPTY, "valid feed without items", 0)
        return BodyVerdict(Outcome.FETCHED, "", len(items))

    def unchanged_body(self, key: str, sha256: str, body: bytes) -> str | None:
        if self._last_digest is not None and self._last_digest == content_digest(body):
            return "unchanged_body"
        return None


ADAPTERS = (RssFeedCollector,)


# --------------------------------------------------------------------------- feed state (R11)
def state_dir_of(cfg: Config) -> Path:
    return Path(cfg.zones["ledger"]) / ".state" / "rss"


def gaps_path_of(cfg: Config) -> Path:
    return Path(cfg.zones["ledger"]) / "rss" / "gaps.jsonl"


def _blank_state(feed_id: str) -> dict:
    return {"schema": 1, "feed_id": feed_id, "last_attempt_at": None, "last_ok_slot": None, "last_ok_at": None,
            "last_stored_sha": None, "last_content_digest": None, "last_change_at": None,
            "newest_pub_ist": None, "consecutive_failures": 0, "alerted": {}}


def _opt(v, pred) -> bool:
    return v is None or (isinstance(v, str) and pred(v))


def _valid_state(obj: object, feed_id: str) -> str | None:
    """None when valid, else the reason."""
    if not isinstance(obj, dict) or set(obj) != _STATE_KEYS:
        return "wrong set of fields"
    if obj["schema"] != 1 or isinstance(obj["schema"], bool):
        return "unsupported schema"
    if obj["feed_id"] != feed_id:
        return "feed_id does not match the file"
    for k in ("last_attempt_at", "last_ok_at", "last_change_at"):
        if not _opt(obj[k], _TS_RE.fullmatch):
            return f"bad {k}"
    if not _opt(obj["last_ok_slot"], SLOT_RE.fullmatch):
        return "bad last_ok_slot"
    for k in ("last_stored_sha", "last_content_digest"):
        if not _opt(obj[k], _SHA_RE.fullmatch):
            return f"bad {k}"
    npub = obj["newest_pub_ist"]
    if npub is not None:
        try:
            ok = isinstance(npub, str) and datetime.fromisoformat(npub).tzinfo is not None
        except ValueError:
            ok = False
        if not ok:
            return "bad newest_pub_ist"
    cf = obj["consecutive_failures"]
    if isinstance(cf, bool) or not isinstance(cf, int) or cf < 0:
        return "bad consecutive_failures"
    al = obj["alerted"]
    if not isinstance(al, dict) or any(k not in _ALARM_KINDS or not isinstance(v, str) or not _DATE_RE.fullmatch(v)
                                       for k, v in al.items()):
        return "bad alerted"
    return None


def read_state(path: Path, feed_id: str, logger=None) -> dict | None:
    """The state of a feed, or None when absent or unreadable (WARNING ``state_unreadable``)."""
    try:
        data = Path(path).read_bytes()
    except FileNotFoundError:
        return None
    except OSError as e:
        why = f"cannot read: {e}"
    else:
        try:
            obj = json.loads(data.decode("utf-8"))
        except (ValueError, UnicodeError):
            why = "invalid JSON"
        else:
            why = _valid_state(obj, feed_id)
            if why is None:
                return obj
    if logger is not None:
        logger.warning("state_unreadable", feed_id=feed_id, reason=why)
    return None


def _fsync_dir(path: Path) -> None:
    fd = os.open(path, os.O_RDONLY)
    try:
        os.fsync(fd)
    finally:
        os.close(fd)


def _write_state(path: Path, obj: dict) -> None:
    """Canonical JSON, temp file plus fsync plus ``os.replace``; a failure leaves the old file intact."""
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = (json.dumps(obj, sort_keys=True, separators=(",", ":"), ensure_ascii=False) + "\n").encode("utf-8")
    tmp = path.parent / f".tmp-{uuid.uuid4().hex}"
    try:
        fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o644)
        try:
            view = memoryview(payload)
            while view:
                view = view[os.write(fd, view):]
            os.fsync(fd)
        finally:
            os.close(fd)
        os.replace(tmp, path)
        _fsync_dir(path.parent)
    finally:
        try:
            os.unlink(tmp)
        except FileNotFoundError:
            pass


@contextmanager
def _feed_lock(path: Path, *, mono: Callable[[], float], sleeper: Callable[[float], None]) -> Iterator[bool]:
    """Exclusive per-feed lock, waiting at most 5 s on the injected clock. Yields whether it was taken."""
    path.parent.mkdir(parents=True, exist_ok=True)
    fd = os.open(path, os.O_RDWR | os.O_CREAT, 0o644)
    got = False
    try:
        start = mono()
        for _ in range(int(_LOCK_WAIT_S / 0.25) + 2):
            try:
                fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
                got = True
                break
            except BlockingIOError:
                if mono() - start >= _LOCK_WAIT_S:
                    break
                sleeper(0.25)
        yield got
    finally:
        os.close(fd)                                              # closing releases the lock


# --------------------------------------------------------------------------- gap ledger (R12)
class GapLedgerError(Exception):
    """The gap ledger is unreadable or invalid."""


def read_gaps(path: Path) -> list[dict]:
    """Strict reader of ``gaps.jsonl``. A missing file is an empty ledger."""
    path = Path(path)
    try:
        data = path.read_bytes()
    except FileNotFoundError:
        return []
    except OSError as e:
        raise GapLedgerError(f"{path.name}: cannot read: {e}") from None
    if data and not data.endswith(b"\n"):
        lock = _lock_of(path)
        if lock.exists():                                          # a writer may be mid-append: let it finish
            try:
                with _flock(lock, fcntl.LOCK_SH):
                    data = path.read_bytes()
            except OSError as e:
                raise GapLedgerError(f"{path.name}: cannot read: {e}") from None
        if data and not data.endswith(b"\n"):
            raise GapLedgerError(f"{path.name}: torn tail (no final newline)")
    out: list[dict] = []
    for n, raw in enumerate(data.split(b"\n")[:-1] if data else [], 1):
        where = f"{path.name} line {n}"
        try:
            obj = json.loads(raw.decode("utf-8"))
        except (ValueError, UnicodeError):
            raise GapLedgerError(f"{where}: invalid JSON") from None
        if not isinstance(obj, dict) or set(obj) != _GAP_KEYS:
            raise GapLedgerError(f"{where}: wrong set of fields")
        if obj["schema"] != 1 or isinstance(obj["schema"], bool):
            raise GapLedgerError(f"{where}: unsupported schema")
        if obj["kind"] not in ("missed", "failed"):
            raise GapLedgerError(f"{where}: unknown kind {obj['kind']!r}")
        if not isinstance(obj["feed_id"], str) or not _FEED_ID_RE.fullmatch(obj["feed_id"]):
            raise GapLedgerError(f"{where}: bad feed_id")
        for k in ("from_slot", "to_slot"):
            if not isinstance(obj[k], str) or not SLOT_RE.fullmatch(obj[k]):
                raise GapLedgerError(f"{where}: bad {k}")
        if isinstance(obj["slots"], bool) or not isinstance(obj["slots"], int) or obj["slots"] < 1:
            raise GapLedgerError(f"{where}: bad slots")
        if not isinstance(obj["detected_at"], str) or not _TS_RE.fullmatch(obj["detected_at"]):
            raise GapLedgerError(f"{where}: bad detected_at")
        for k in ("reason", "run_id"):
            if not isinstance(obj[k], str):
                raise GapLedgerError(f"{where}: bad {k}")
        out.append(obj)
    return out


# --------------------------------------------------------------------------- freshness (R13)
class CalendarView:
    """NSE cash-market day status from the shipped tables; any trouble reads as ``unknown``."""

    def __init__(self, cfg: Config) -> None:
        self._cal = None
        try:
            from investos.calendar.trading import TradingCalendar
            self._cal = TradingCalendar(cfg)
        except Exception:                                          # missing or invalid tables
            self._cal = None

    def day_status(self, d: date) -> str:
        if self._cal is None:
            return "unknown"
        try:
            return self._cal.day_status(d).value
        except Exception:
            return "unknown"


@dataclass(frozen=True)
class FeedVerdict:
    alarms: tuple[tuple[str, str], ...]          # (kind, detail)
    unknown: bool
    warnings: tuple[str, ...]


def _aware_ist(s: str) -> datetime:
    return datetime.fromisoformat(s).astimezone(IST)


def judge_feed(feed: FeedRow, state: dict | None, now: datetime, calendar: CalendarView | None) -> FeedVerdict:
    """The freshness rule for one enabled feed. ``state`` None means absent or unreadable: ``unknown``."""
    if state is None:
        return FeedVerdict((), True, ())
    alarms: list[tuple[str, str]] = []
    warnings: list[str] = []
    now_ist = now.astimezone(IST)
    # (a) poll_missed
    due = latest_due_slot(now)
    last_ok = state["last_ok_slot"]
    failures = state["consecutive_failures"]
    if failures >= 3:
        alarms.append(("poll_missed", f"{failures} consecutive failures"))
    elif last_ok is not None and last_ok < due:
        alarms.append(("poll_missed", f"last ok slot {last_ok}, latest due slot {due}"))
    # (b) content_stale
    newest = state["newest_pub_ist"] or state["last_change_at"]
    if newest is None:
        return FeedVerdict(tuple(alarms), True, tuple(warnings))
    newest_dt = _aware_ist(newest) if state["newest_pub_ist"] else _parse_ts(newest).astimezone(IST)
    newest_dt = min(newest_dt, now_ist)
    scope = feed.stale_scope
    ref = newest_dt
    evaluate = True
    if scope == "trading_hours":
        status = calendar.day_status(now_ist.date()) if calendar is not None else "unknown"
        if status == "unknown":
            scope = "always"
            warnings.append(f"calendar year {now_ist.year} not loaded: {feed.feed_id} judged as always")
        elif status in ("trading", "provisional"):
            hhmm = (now_ist.hour, now_ist.minute)
            evaluate = (9, 30) <= hhmm <= (16, 0)
            ref = max(newest_dt, now_ist.replace(hour=9, minute=15, second=0, microsecond=0))
        else:
            evaluate = False
    if evaluate and now_ist - ref > timedelta(minutes=feed.stale_after_min):
        age_min = int((now_ist - ref).total_seconds() // 60)
        alarms.append(("content_stale", f"age {age_min} min, window {feed.stale_after_min} min ({scope})"))
    return FeedVerdict(tuple(alarms), False, tuple(warnings))


# --------------------------------------------------------------------------- the cycle (R10)
@dataclass(frozen=True)
class CycleResult:
    slot: str
    polled: tuple[str, ...]
    outcomes: Mapping[str, str]
    skipped_slot: bool
    gaps_written: int
    alerts: tuple[str, ...]
    gap_write_failed: bool = False


def _newest_pub(body: bytes) -> str | None:
    try:
        pubs = [i.pub_ist for i in parse_feed(body) if i.pub_ist is not None]
    except FeedParseError:
        return None
    return max(pubs).isoformat() if pubs else None


def poll_cycle(cfg: Config, *, now: datetime, transport, manifest: ManifestStore, notifier: Notifier | None = None,
               force: bool = False, feeds: Sequence[FeedRow] | None = None, run_id: str | None = None,
               monotonic: Callable[[], float] | None = None, sleeper: Callable[[float], None] | None = None,
               wall_clock: Callable[[], float] | None = None,
               hostname: Callable[[], str] | None = None) -> CycleResult:
    logger = log.get_logger("collect.rss")
    notifier = notifier if notifier is not None else NullNotifier()
    mono = monotonic or time.monotonic
    sleep = sleeper or time.sleep
    rid = run_id if run_id is not None else log.get_run_id()
    table = tuple(feeds) if feeds is not None else load_feeds(feed_table_path(cfg))      # FeedTableError aborts
    slot = slot_of(now)
    if not force and not is_poll_slot(slot):
        logger.info("slot_not_scheduled", slot=slot)
        return CycleResult(slot, (), {}, True, 0, ())
    enabled = [f for f in table if f.enabled]
    if not enabled:
        logger.warning("no_enabled_feeds", slot=slot)
        return CycleResult(slot, (), {}, False, 0, ())
    cycle = _Cycle(cfg, now=now, slot=slot, transport=transport, manifest=manifest, notifier=notifier, rid=rid,
                   mono=mono, sleep=sleep, wall_clock=wall_clock, hostname=hostname, logger=logger)
    logger.info("cycle_start", slot=slot, feeds=len(enabled))
    started = mono()
    for feed in enabled:
        over_budget = mono() - started >= _CYCLE_BUDGET_S
        cycle.process(feed, over_budget=over_budget)
    c = cycle
    logger.info("cycle_end", slot=slot, ok=c.ok, failed=c.failed, not_due=c.not_due, locked=c.locked,
                gaps_written=c.gaps_written)
    return CycleResult(slot, tuple(c.polled), dict(c.outcomes), False, c.gaps_written, tuple(c.alerts),
                       c.gap_write_failed)


class _Cycle:
    def __init__(self, cfg, *, now, slot, transport, manifest, notifier, rid, mono, sleep, wall_clock, hostname,
                 logger) -> None:
        self.cfg, self.now, self.slot, self.transport, self.manifest = cfg, now, slot, transport, manifest
        self.notifier, self.rid, self.mono, self.sleep = notifier, rid, mono, sleep
        self.wall_clock, self.hostname, self.log = wall_clock, hostname, logger
        self.state_dir = state_dir_of(cfg)
        self.gaps_path = gaps_path_of(cfg)
        self.polled: list[str] = []
        self.outcomes: dict[str, str] = {}
        self.alerts: list[str] = []
        self.ok = self.failed = self.not_due = self.locked = self.gaps_written = 0
        self.gap_write_failed = False
        self._calendar: CalendarView | None = None
        self.today = now.astimezone(IST).date()

    # ---- helpers
    def calendar(self) -> CalendarView:
        if self._calendar is None:
            self._calendar = CalendarView(self.cfg)
        return self._calendar

    def write_gap(self, feed_id: str, kind: str, from_slot: str, to_slot: str, slots: int, reason: str) -> bool:
        ev = {"schema": 1, "feed_id": feed_id, "kind": kind, "from_slot": from_slot, "to_slot": to_slot,
              "slots": slots, "reason": reason[:300], "detected_at": _fmt_ts(self.now), "run_id": self.rid}
        try:
            _append(self.gaps_path, _dump(ev))
        except (AccessStateError, OSError) as e:
            self.log.error("gap_write_failed", feed_id=feed_id, kind=kind, reason=str(e)[:200])
            self.gap_write_failed = True
            return False
        self.gaps_written += 1
        return True

    def collector(self, feed: FeedRow, state: dict | None) -> RssFeedCollector:
        kw = dict(transport=self.transport, manifest=self.manifest, cfg=self.cfg, run_id=self.rid,
                  clock=lambda: self.now, notifier=self.notifier,
                  sidecar_dir=Path(self.cfg.zones["manifests"]) / "rss.runs" / feed.feed_id,
                  monotonic=self.mono, sleeper=self.sleep)
        if self.wall_clock is not None:
            kw["wall_clock"] = self.wall_clock
        if self.hostname is not None:
            kw["hostname"] = self.hostname
        return RssFeedCollector(feed, self.slot, last_stored_sha=state["last_stored_sha"] if state else None,
                                last_content_digest=state["last_content_digest"] if state else None, **kw)

    # ---- one feed
    def process(self, feed: FeedRow, *, over_budget: bool) -> None:
        sp = self.state_dir / f"{feed.feed_id}.json"
        with _feed_lock(self.state_dir / f"{feed.feed_id}.lock", mono=self.mono, sleeper=self.sleep) as got:
            if not got:
                self.locked += 1
                self.log.warning("feed_locked", feed_id=feed.feed_id, reason="cycle already running")
                return
            state = read_state(sp, feed.feed_id, self.log)
            if state is not None and state["last_ok_slot"] == self.slot:
                self.not_due += 1
                self.finish_feed(feed, sp, state, None)
                return
            old_ok = (state["last_ok_slot"], state["last_ok_at"]) if state else (None, None)
            gap_ok = True
            if state is not None and state["last_ok_slot"] is not None and not over_budget:
                missed = _scheduled_between(state["last_ok_slot"], self.slot)
                if missed:
                    gap_ok = self.write_gap(feed.feed_id, "missed", missed[0], missed[-1], len(missed),
                                            f"no ok poll between {state['last_ok_slot']} and {self.slot}")
            if over_budget:
                outcome, reason = "not_attempted", "cycle budget"
            else:
                outcome, reason = self.poll(feed, state)
            new = self.apply_outcome(feed, state, outcome)
            if outcome in _FAIL_OUTCOMES:
                self.failed += 1
                self.write_gap(feed.feed_id, "failed", self.slot, self.slot, 1, reason)
            else:
                self.ok += 1
            if not gap_ok:                                         # the miss was not recorded: keep it detectable
                new["last_ok_slot"], new["last_ok_at"] = old_ok
            self.finish_feed(feed, sp, new, outcome)

    def poll(self, feed: FeedRow, state: dict | None) -> tuple[str, str]:
        key = f"{feed.feed_id}/{self.slot}"
        self.polled.append(feed.feed_id)
        try:
            col = self.collector(feed, state)
            res = col.run(RunRange(self.today, self.today))
        except Exception as e:                                     # one feed never stops the cycle
            self.log.error("feed_failed", feed_id=feed.feed_id, reason=type(e).__name__)
            self.outcomes[feed.feed_id] = "unreadable"
            return "unreadable", f"exception {type(e).__name__}"
        out = res.outcomes.get(key)
        outcome = out.value if out is not None else "not_attempted"
        self.outcomes[feed.feed_id] = outcome
        reason = ""
        if outcome in _FAIL_OUTCOMES:
            reason = self._sidecar_reason(res.sidecar_path, key)
        return outcome, reason

    @staticmethod
    def _sidecar_reason(path: Path, key: str) -> str:
        try:
            gaps = json.loads(Path(path).read_text(encoding="utf-8")).get("gaps", [])
            for g in gaps:
                if g.get("key") == key:
                    return str(g.get("reason") or "unspecified")
        except (OSError, ValueError):
            pass
        return "unspecified"

    def apply_outcome(self, feed: FeedRow, state: dict | None, outcome: str) -> dict:
        st = dict(state) if state is not None else _blank_state(feed.feed_id)
        st["alerted"] = dict(st["alerted"])
        if feed.feed_id not in self.outcomes:
            self.outcomes[feed.feed_id] = outcome                  # budget: recorded, never polled
        st["last_attempt_at"] = _fmt_ts(self.now)
        if outcome in _OK_OUTCOMES:
            st["last_ok_slot"] = self.slot
            st["last_ok_at"] = _fmt_ts(self.now)
            st["consecutive_failures"] = 0
            if outcome != "skipped":
                entry = self.manifest.get_by_key("rss", f"{feed.feed_id}/{self.slot}")
                if entry is not None:
                    body = self.manifest.read_bytes("rss", entry.sha256)
                    st["last_stored_sha"] = entry.sha256
                    st["last_content_digest"] = content_digest(body)
                    st["last_change_at"] = _fmt_ts(self.now)
                    st["newest_pub_ist"] = _newest_pub(body)
        else:
            st["consecutive_failures"] += 1
        return st

    def finish_feed(self, feed: FeedRow, sp: Path, st: dict, outcome: str | None) -> None:
        """Freshness verdict, alerts, then one state write (only when something changed or a poll happened)."""
        changed = outcome is not None
        verdict = judge_feed(feed, st, self.now, self.calendar() if feed.stale_scope == "trading_hours" else None)
        today = self.now.astimezone(IST).date().isoformat()
        for w in verdict.warnings:
            self.log.warning("freshness_note", feed_id=feed.feed_id, note=w)
        for kind, detail in verdict.alarms:
            if st["alerted"].get(kind) == today:
                continue
            self.log.error("feed_alarm", feed_id=feed.feed_id, kind=kind, detail=detail)
            try:
                self.notifier.notify("Invest OS: feed stale", f"{feed.feed_id} {kind}")
            except Exception as e:                                 # an alert never breaks the cycle
                self.log.warning("notify_failed", reason=type(e).__name__)
                continue
            st = dict(st)
            st["alerted"] = {**st["alerted"], kind: today}
            self.alerts.append(f"{feed.feed_id} {kind}")
            changed = True
        if changed:
            try:
                _write_state(sp, st)
            except OSError as e:
                self.log.error("state_write_failed", feed_id=feed.feed_id, reason=str(e)[:200])


def due_feeds(cfg: Config, feeds: Sequence[FeedRow], slot: str) -> list[FeedRow]:
    """Enabled feeds whose state does not already say ``last_ok_slot == slot`` (read-only; for ``--dry-run``)."""
    out = []
    for f in feeds:
        if not f.enabled:
            continue
        st = read_state(state_dir_of(cfg) / f"{f.feed_id}.json", f.feed_id)
        if st is None or st["last_ok_slot"] != slot:
            out.append(f)
    return out
