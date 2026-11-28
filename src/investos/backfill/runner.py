"""The nightly runner and the browser transport of the backfill (spec collect_backfill R3, R10, R12-R15, R21, R23).

``BackfillTransport`` is the existing ``RealBrowserTransport`` with the backfill's own Chrome profile, both NSE ``www``
hosts and the ``.com`` to ``.in`` redirect declared. A ``RunGate`` sits between the transport and the browser session:
it counts EVERY request (anchors, retries, probe requests) against the night budget and checks the anchor's final
origin against the probe's. It only counts and stops; it changes nothing about how Chrome fetches.

``run_backfill`` is a thin loop over the unchanged base ``Collector``: per endpoint it plans, takes a batch of pending
windows, runs the collector and writes the window ledger AFTER the manifest write. ``run_probe`` runs the probe stage
under the same window, budget and block rules."""
from __future__ import annotations

import json
import os
import tempfile
import time
from collections import Counter
from dataclasses import dataclass, field
from datetime import date, datetime, time as dtime, timezone
from math import ceil
from pathlib import Path
from typing import Callable, Mapping, Sequence
from urllib.parse import urlsplit
from zoneinfo import ZoneInfo

from investos.acquire.access import AccessState, AccessStateError
from investos.acquire.base import Outcome, RunRange, TransportError
from investos.acquire.browser import AnchorResult, BrowserSession, PlaywrightDriver, RealBrowserTransport
from investos.acquire.manifest import ManifestError
from investos.backfill.collector import NOT_JSON, make_collector_class, rows_of
from investos.backfill.endpoints import (COM_ORIGIN, NSE_HOSTS, Endpoint, EndpointTableError, anchor_full,
                                         load_endpoints, load_symbols, symbols_path, table_path)
from investos.backfill.ledger import DONE_EVENTS, LedgerError, WindowKey, WindowLedger
from investos.backfill.planner import plan_windows, split, write_plan
from investos.backfill.probe import (MAX_REQUESTS_PER_ENDPOINT, ProbeBlocked, ProbeError, load_probe, mark_needs_review,
                                     probe_endpoint, save_probe_results, unusable_reason, write_probe_report)
from investos.core import log
from investos.core.config import Config
from investos.core.notify import NullNotifier

__all__ = ["RunStop", "RunGate", "BackfillTransport", "make_transport", "BackfillResult", "run_backfill", "run_probe",
           "survey", "Survey", "make_plan", "night_budget", "night_counter_path", "read_counter", "state_dir",
           "window_open", "market_hours", "DEFAULT_API_ORIGIN", "CORE_IDS", "ordered"]

IST = ZoneInfo("Asia/Kolkata")
CORE_IDS = ("announcements", "pit", "corporate_actions", "bulk_deals", "block_deals")
GAP_S = 120.0                 # R23: a longer silence between two requests means the Mac slept
MAX_FAILED = 5                # R17: failed lines after which a window needs a human
HTML_STREAK = 3               # R14e: consecutive non-JSON windows that stop an endpoint for the night
BLOCK_TITLE = "Invest OS: NSE backfill blocked"


class RunStop(BaseException):
    """Ends the run from deep inside a request. A BaseException, so the collector base (which retries a
    TransportError and catches Exception) can never swallow or retry it. ``status`` is the run status."""

    def __init__(self, status: str, reason: str = "") -> None:
        super().__init__(status, reason)
        self.status, self.reason = status, reason


DEFAULT_API_ORIGIN = "https://www.nseindia.in"
_BLOCK = frozenset({401, 403, 429, 451})


def state_dir(cfg: Config) -> Path:
    return Path(cfg.zones["ledger"]) / ".state" / "backfill"


def night_counter_path(cfg: Config, night: str) -> Path:
    return state_dir(cfg) / f"night.{night}.count"


def read_counter(path: Path) -> int:
    try:
        text = path.read_text(encoding="ascii").strip()
    except FileNotFoundError:
        return 0
    except (OSError, UnicodeError) as e:
        raise RunStop("error", f"night counter unreadable: {e}") from None
    if not text.isascii() or not text.isdigit():
        raise RunStop("error", f"night counter {path.name} is not a non-negative integer")
    return int(text)


def _origin(url: str) -> str:
    p = urlsplit(url)
    return f"{p.scheme}://{p.netloc}".lower() if p.scheme and p.netloc else ""


class RunGate:
    """Counts requests into ``night.<date>.count`` and stops the run (RunStop budget) before the one that would exceed it."""

    def __init__(self, cfg: Config) -> None:
        self._cfg = cfg
        self.night: str | None = None
        self.limit = 0
        self.max_requests: int | None = None
        self.count_run = 0
        self.expected_origin: str | None = None

    def begin(self, night: str, limit: int, max_requests: int | None = None) -> None:
        self.night, self.limit, self.max_requests, self.count_run = night, limit, max_requests, 0

    def used(self) -> int:
        return read_counter(night_counter_path(self._cfg, self.night)) if self.night else 0

    def remaining(self) -> int:
        if self.night is None:
            return 1 << 30
        left = self.limit - self.used()
        if self.max_requests is not None:
            left = min(left, self.max_requests - self.count_run)
        return left

    def hit(self) -> None:
        if self.night is None:
            return
        if self.remaining() <= 0:
            raise RunStop("budget", f"night budget {self.limit} reached" if self.used() >= self.limit
                          else f"--max-requests {self.max_requests} reached")
        path = night_counter_path(self._cfg, self.night)
        path.parent.mkdir(parents=True, exist_ok=True)
        fd, tmp = tempfile.mkstemp(prefix=".count-", dir=str(path.parent))
        try:
            with os.fdopen(fd, "w", encoding="ascii") as fh:
                fh.write(f"{self.used() + 1}\n")
            os.replace(tmp, path)
        except BaseException:
            try:
                os.unlink(tmp)
            except FileNotFoundError:
                pass
            raise
        self.count_run += 1


class _GatedSession:
    def __init__(self, inner: BrowserSession, gate: RunGate) -> None:
        self._inner, self._gate = inner, gate

    def goto_anchor(self, url: str, timeout_s: float) -> AnchorResult:
        self._gate.hit()
        res = self._inner.goto_anchor(url, timeout_s)
        want = self._gate.expected_origin
        if want and res.status not in _BLOCK and _origin(res.final_url) != want:
            raise RunStop("error", f"origin changed: {want} -> {_origin(res.final_url) or 'unknown'}")
        return res

    def page_origin(self) -> str:
        return self._inner.page_origin()

    def fetch(self, url: str, timeout_s: float, max_bytes: int):
        self._gate.hit()
        return self._inner.fetch(url, timeout_s, max_bytes)

    def sent_headers(self, url: str):
        return self._inner.sent_headers(url)

    def close(self, timeout_s: float) -> None:
        self._inner.close(timeout_s)


class _GatedDriver:
    def __init__(self, inner, gate: RunGate) -> None:
        self._inner, self._gate = inner, gate

    def launch(self, profile_dir, headless, timeout_s):
        return _GatedSession(self._inner.launch(profile_dir, headless, timeout_s), self._gate)


class BackfillTransport(RealBrowserTransport):
    def __init__(self, cfg: Config, *, api_origin: str, driver=None, **kw) -> None:
        self.gate = RunGate(cfg)
        self.api_origin = api_origin
        aliases = {COM_ORIGIN: api_origin} if api_origin != COM_ORIGIN else {}
        super().__init__(cfg, driver=_GatedDriver(driver if driver is not None else PlaywrightDriver(), self.gate),
                         allowed_hosts=frozenset(NSE_HOSTS), origin_aliases=aliases,
                         profile_name=cfg.backfill.profile_name, **kw)

    def pace(self, url: str) -> None:
        """Wait the host's spacing before a request the caller sends straight through ``fetch`` (the probe)."""
        host = (urlsplit(url).hostname or "").lower()
        self._limiter.acquire(host, self._interval(host))

    def page_origin(self) -> str:
        return self._session.page_origin() if self._session is not None else ""


def make_transport(cfg: Config, *, api_origin: str = DEFAULT_API_ORIGIN, driver=None, headless: bool = False,
                   reaper=None, clock=None, monotonic=None, wall_clock=None, sleeper=None,
                   notifier=None) -> BackfillTransport:
    return BackfillTransport(cfg, api_origin=api_origin, driver=driver, headless=headless, reaper=reaper, clock=clock,
                             monotonic=monotonic, wall_clock=wall_clock, sleeper=sleeper, notifier=notifier)


# --------------------------------------------------------------------------- time, window, budget
def _hm(text: str) -> dtime:
    h, m = text.split(":")
    return dtime(int(h), int(m))


def window_open(cfg: Config, now_ist: datetime) -> bool:
    return _hm(cfg.backfill.window_start) <= now_ist.time() < _hm(cfg.backfill.window_end)


def market_hours(now_ist: datetime) -> bool:
    """08:30 to 16:30 IST, Monday to Friday: the RSS poller keeps priority, no override (R13a)."""
    return now_ist.weekday() < 5 and dtime(8, 30) <= now_ist.time() < dtime(16, 30)


def _marker_dates(cfg: Config, prefix: str) -> list[str]:
    d = state_dir(cfg)
    return sorted(p.name[len(prefix) + 1:] for p in d.glob(prefix + ".*")) if d.is_dir() else []


def night_budget(cfg: Config, night: str) -> int:
    """The ramp (Sagar's answer 1): night 1 spends ramp[0]; every clean night after the last block lifts one step."""
    ramp = cfg.backfill.ramp
    blocks = _marker_dates(cfg, "block")
    last_block = blocks[-1] if blocks else ""
    clean = sum(1 for n in _marker_dates(cfg, "ran") if last_block < n < night)
    return ramp[min(clean, len(ramp) - 1)]


def _touch(path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.touch()


# --------------------------------------------------------------------------- the survey of one endpoint
@dataclass
class Survey:
    pending: list[WindowKey] = field(default_factory=list)
    unresolved: list[tuple[WindowKey, str, str]] = field(default_factory=list)      # (window, event, reason)
    total: int = 0
    done: int = 0
    rows: int = 0
    splits: int = 0
    unsplittable: int = 0
    failed: int = 0
    last_error: tuple[str, str] | None = None                                       # (ts, reason)


def make_plan(cfg: Config, ep: Endpoint, probe, ledger: WindowLedger, today: date, *, calendar=None,
              symbols: Sequence[str] | None = None, write: bool = True) -> list[WindowKey]:
    """The plan: the borders of every window the ledger has seen are kept; only uncovered dates are cut anew (R6)."""
    done = [k for k, s in ledger.state().items() if s.event != "planned"]
    kw = dict(calendar=calendar, done=done, max_window_days=ledger.shrink(), symbols=symbols)
    return write_plan(cfg, ep, probe, today, **kw) if write else plan_windows(ep, probe, today, **kw)


def survey(plan: Sequence[WindowKey], ledger: WindowLedger, night: str, hold: frozenset | set = frozenset()) -> Survey:
    """Walk the plan against the ledger. A split or superseded window is its halves; a failed window waits for a
    later night and is skipped after MAX_FAILED lines; an unsplittable window is a recorded gap."""
    st: dict[WindowKey, dict] = {}
    nfail: Counter = Counter()
    sv = Survey()
    for o in ledger.lines():
        if o["event"] == "audit_shrink":
            continue
        k = WindowKey(o["endpoint"], date.fromisoformat(o["start"]), date.fromisoformat(o["end"]), o["part"])
        st[k] = o
        if o["event"] == "split":
            sv.splits += 1
        if o["event"] == "failed":
            nfail[k] += 1
        if o["event"] in ("failed", "blocked") and (sv.last_error is None or o["ts"] >= sv.last_error[0]):
            sv.last_error = (o["ts"], o["reason"])

    def visit(k: WindowKey) -> None:
        o = st.get(k)
        ev = o["event"] if o else None
        if ev in DONE_EVENTS:
            sv.total += 1
            sv.done += 1
            sv.rows += o["rows"]
            return
        if ev in ("split", "superseded") and k.days >= 2:
            for c in split(k):
                visit(c)
            return
        sv.total += 1
        if ev == "unsplittable":
            sv.unsplittable += 1
            sv.unresolved.append((k, ev, o["reason"]))
        elif ev == "failed":
            sv.failed += 1
            if k in hold or o["date"] == night or nfail[k] >= MAX_FAILED:
                sv.unresolved.append((k, ev, o["reason"]))
            else:
                sv.pending.append(k)
        else:
            sv.pending.append(k)

    for w in plan:
        visit(w)
    return sv


# --------------------------------------------------------------------------- the browser the collector sees
class RunBrowser:
    """The one object the base collector fetches through. It notices a Mac that slept (R23) and turns a busy
    profile into a stop of the whole run; it never touches a request, a header or a timing."""

    def __init__(self, transport: BackfillTransport, *, wall: Callable[[], float], on_gap: Callable[[], None]) -> None:
        self._t, self._wall, self._on_gap = transport, wall, on_gap
        self._last: float | None = None
        self.anchor: str | None = None
        self._log = log.get_logger("backfill.run")

    def fetch(self, url: str):
        now = self._wall()
        if self._last is not None and now - self._last > GAP_S:
            self._log.warning("resume_after_sleep", gap_s=int(now - self._last))
            self._on_gap()                                   # the window may have ended while the Mac slept
            if self.anchor:
                self._t.set_anchor(self.anchor)              # the page may have lost its session
        self._last = now
        try:
            return self._t.fetch(url)
        except TransportError as e:
            if "profile busy" in str(e):
                raise RunStop("error", "profile busy: another process holds the backfill Chrome profile") from None
            raise

    def close(self) -> None:
        self._t.close()


# --------------------------------------------------------------------------- results
@dataclass
class BackfillResult:
    status: str
    exit_code: int
    reason: str = ""
    requests: int = 0
    windows_done: int = 0
    rows: int = 0
    splits: int = 0
    failed: int = 0
    per_endpoint: dict = field(default_factory=dict)


_EXIT = {"complete": 0, "incomplete": 0, "budget": 0, "window_closed": 0, "stopped": 0, "refused": 0, "error": 1,
         "blocked": 3}


def ordered(eps: Mapping[str, Endpoint]) -> list[Endpoint]:
    """Sagar's answer 2: the core endpoints first, then the rest in table order."""
    return [eps[i] for i in CORE_IDS if i in eps] + [e for i, e in eps.items() if i not in CORE_IDS]


def _short(text: str, n: int = 300) -> str:
    return " ".join(str(text).split())[:n]


class _Run:
    def __init__(self, cfg: Config, *, browser, manifest, clock, sleeper, notifier, monotonic, wall_clock, hostname,
                 endpoints, max_requests, outside_window, calendar, symbols) -> None:
        self.cfg, self.browser, self.manifest = cfg, browser, manifest
        self.clock = clock or (lambda: datetime.now(timezone.utc))
        self.sleeper, self.monotonic, self.wall_clock, self.hostname = sleeper, monotonic, wall_clock, hostname
        self.notifier = notifier if notifier is not None else NullNotifier()
        self.max_requests, self.outside_window, self.calendar = max_requests, outside_window, calendar
        self.symbols = list(symbols) if symbols is not None else None
        self.endpoints = endpoints
        self.log = log.get_logger("backfill.run")
        now = self.clock()
        self.ist = now.astimezone(IST)
        self.night = self.ist.date().isoformat()
        self.run_id = "backfill-" + now.astimezone(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
        self.res = BackfillResult("complete", 0)
        self.gate: RunGate = getattr(browser, "gate", None)
        if self.gate is None:
            raise TypeError("browser must be a BackfillTransport (make_transport)")
        if wall_clock is not None:
            wall = wall_clock
        elif monotonic is None:
            wall = time.time
        else:
            base = time.time() - monotonic()
            wall = lambda: base + monotonic()
        self.rb = RunBrowser(browser, wall=wall, on_gap=self._tick)
        self.current: str | None = None
        self.block_notified = False

    # ---- small helpers
    def _notify(self, title: str, message: str) -> None:
        try:
            self.notifier.notify(title, message)
        except Exception as e:                               # an alert never changes the outcome
            self.log.warning("notify_failed", reason=type(e).__name__)

    def _finish(self, status: str, reason: str = "") -> BackfillResult:
        self.res.status, self.res.exit_code, self.res.reason = status, _EXIT[status], reason
        self.res.requests = self.gate.count_run
        return self.res

    def _admit(self) -> BackfillResult | None:
        """Window, market hours and host block, before any Chrome is started (R13a, R15f)."""
        if not self.outside_window:
            if not window_open(self.cfg, self.ist):
                return self._finish("window_closed", f"outside the nightly window "
                                    f"{self.cfg.backfill.window_start}-{self.cfg.backfill.window_end} IST")
        elif market_hours(self.ist):
            return self._finish("error", "market hours (08:30-16:30 IST, Monday to Friday): no backfill runs")
        acc = AccessState(self.cfg, "backfill_probe", clock=self.clock)
        try:
            for host in sorted(NSE_HOSTS):
                st = acc.status(host)
                if st.blocked_today:
                    return self._finish("blocked", st.reason)
        except AccessStateError as e:
            return self._finish("error", f"access state unreadable: {e}")
        return None

    def _tick(self) -> None:
        """Checked before every batch and after a sleep: the window may be over (R13c, R23)."""
        ist = self.clock().astimezone(IST)
        if self.outside_window:
            if market_hours(ist):
                raise RunStop("window_closed", "market hours reached")
        elif not window_open(self.cfg, ist):
            raise RunStop("window_closed", "the nightly window has ended")

    def _begin_gate(self) -> None:
        self.gate.begin(self.night, night_budget(self.cfg, self.night), self.max_requests)

    # ---- blocks (R15)
    def _mirror_block(self, source: str, host: str, detail: str) -> None:
        acc = AccessState(self.cfg, source, clock=self.clock)
        for other in sorted(NSE_HOSTS - {host}):
            try:
                acc.record_block(host=other, detail=detail, run_id=self.run_id)
            except (AccessStateError, OSError) as e:
                self.log.error("block_mirror_failed", host=other, detail=str(e))
        _touch(state_dir(self.cfg) / f"block.{self.night}")

    def _block(self, ep: Endpoint, probe, ledger: WindowLedger, w: WindowKey, reason: str) -> None:
        host = urlsplit(probe.api_origin).hostname or ""
        detail = reason.removeprefix("blocked ")
        self._mirror_block(ep.source, host, detail)
        shown = detail.removeprefix("http ") if detail.startswith("http ") else "challenge page"
        if not self.block_notified:
            self.block_notified = True
            self._notify(BLOCK_TITLE, f"{host} {shown}; backfill stopped for the IST day")
        raise RunStop("blocked", f"{host} {detail}")

    # ---- the ledger writers
    def _done_line(self, ep: Endpoint, probe, ledger: WindowLedger, w: WindowKey) -> tuple[str, int]:
        key = w.logical_key()
        ent = self.manifest.get_by_key(ep.source, key)
        if ent is None:
            raise ManifestError(f"{key} is not in the manifest")
        n = len(rows_of(self.manifest.read_bytes(ep.source, ent.sha256), probe.rows_path))
        ev = "fetched" if n else "empty"
        ledger.append(ev, w, rows=n, sha256=ent.sha256, logical_key=key, run_id=self.run_id,
                      probe_hash=ep.row_hash())
        self.res.windows_done += 1
        self.res.rows += n
        return ev, n

    def _repair(self, ep: Endpoint, probe, ledger: WindowLedger, windows: Sequence[WindowKey]) -> None:
        """After an interrupt: a window stored in the manifest but missing from the ledger gets its line (R10c)."""
        try:
            st = ledger.state()
            for w in windows:
                s = st.get(w)
                if (s is None or s.event not in DONE_EVENTS) and self.manifest.get_by_key(ep.source, w.logical_key()):
                    self._done_line(ep, probe, ledger, w)
        except (OSError, LedgerError, ManifestError, ValueError) as e:
            self.log.error("ledger_repair_failed", endpoint=ep.endpoint_id, detail=str(e))

    def _record(self, ep: Endpoint, probe, ledger: WindowLedger, w: WindowKey, outcome: Outcome, reason: str,
                *, audit: bool = False) -> tuple[str, str]:
        """One window outcome into the ledger. Returns (kind, detail): done, split, unsplittable, blocked, failed,
        nonjson, storage."""
        common = dict(run_id=self.run_id, probe_hash=ep.row_hash())
        if outcome in (Outcome.FETCHED, Outcome.FETCHED_EMPTY, Outcome.SKIPPED):
            try:
                ev, n = self._done_line(ep, probe, ledger, w)
                return "done", f"{ev} {n}"
            except (ManifestError, ValueError, OSError) as e:
                if isinstance(e, OSError) and not isinstance(e, ManifestError):
                    raise
                outcome, reason = Outcome.UNREADABLE, f"stored window unreadable: {_short(e, 150)}"
        if outcome is Outcome.INCOMPLETE:
            if audit:
                return "incomplete", reason
            if w.days >= 2 and probe.mode != "per_company":
                ledger.append("split", w, reason=_short(reason), **common)
                self.res.splits += 1
                return "split", reason
            ledger.append("unsplittable", w, reason=_short(reason), **common)
            return "unsplittable", reason
        if reason.startswith(("blocked http", "challenge page")):
            ledger.append("blocked", w, reason=_short(reason), **common)
            return "blocked", reason
        if audit:
            return "failed", reason
        ledger.append("failed", w, reason=_short(reason or "unreadable"), **common)
        self.res.failed += 1
        kind = "storage" if reason.startswith("storage failure") else "nonjson" if reason.startswith(NOT_JSON) \
            else "failed"
        return kind, reason

    # ---- one collector run
    def _collect(self, ep: Endpoint, probe, ledger: WindowLedger, windows: Sequence[WindowKey]):
        cls = make_collector_class(ep, probe)
        coll = cls(windows=windows, origin=probe.api_origin, symbols=self._symbols(ep, probe), browser=self.rb,
                   manifest=self.manifest, cfg=self.cfg, run_id=self.run_id, clock=self.clock,
                   monotonic=self.monotonic, sleeper=self.sleeper, wall_clock=self.wall_clock,
                   notifier=self.notifier, hostname=self.hostname)
        rr = RunRange(min(w.start for w in windows), max(w.end for w in windows))
        try:
            res = coll.run(rr)
        except BaseException:
            self._repair(ep, probe, ledger, windows)
            raise
        reasons: dict[str, str] = {}
        try:
            gaps = json.loads(Path(res.sidecar_path).read_text(encoding="utf-8"))["gaps"]
            reasons = {g["key"]: g["reason"] for g in gaps}
        except (OSError, ValueError, KeyError):
            pass
        return res.outcomes, reasons

    def _symbols(self, ep: Endpoint, probe) -> list[str] | None:
        if probe.mode != "per_company":
            return None
        if self.symbols is None:
            self.symbols = load_symbols(symbols_path(self.cfg.repo_root))
        return self.symbols

    # ---- audit (R8b)
    def _audit(self, ep: Endpoint, probe, ledger: WindowLedger, w: WindowKey, n: int) -> str:
        """Re-fetch the two halves of a fetched window. Returns equal, more, less or inconclusive."""
        if self.gate.remaining() < 2:
            raise RunStop("budget", "no budget left for an audit")
        self._tick()
        a, b = split(w)
        outs, reasons = self._collect(ep, probe, ledger, [a, b])
        rows = []
        for h in (a, b):
            o = outs.get(h.logical_key())
            if o is None:
                return "inconclusive"
            kind, detail = self._record(ep, probe, ledger, h, o, reasons.get(h.logical_key(), ""), audit=True)
            if kind == "blocked":
                ledger.append("blocked", h, reason=_short(detail), run_id=self.run_id, probe_hash=ep.row_hash())
                self._block(ep, probe, ledger, h, detail)
            if kind != "done":
                return "inconclusive"
            rows.append(int(detail.split()[1]))
        total = sum(rows)
        if total == n:
            return "equal"
        if total > n:
            cur = probe.max_window_days if ledger.shrink() is None else min(probe.max_window_days, ledger.shrink())
            common = dict(run_id=self.run_id, probe_hash=ep.row_hash())
            ledger.append("superseded", w, reason=f"halves hold {total} rows, the whole {n}", **common)
            ledger.append("audit_shrink", w, reason=f"max_window_days={max(1, cur // 2)}", **common)
            return "more"
        return "less"

    # ---- one endpoint
    def _endpoint(self, ep: Endpoint, probe) -> None:
        eid = ep.endpoint_id
        per = self.res.per_endpoint[eid]
        ledger = WindowLedger(self.cfg, eid, clock=self.clock)
        anchored, streak, hold = False, 0, set()
        every = self.cfg.backfill.audit_every
        try:
            while True:
                plan = make_plan(self.cfg, ep, probe, ledger, self.ist.date(), calendar=self.calendar,
                                 symbols=self._symbols(ep, probe))
                sv = survey(plan, ledger, self.night, hold)
                if not sv.pending:
                    left = len(sv.unresolved)
                    per.update(status="incomplete" if left else "complete",
                               reason=f"{left} window(s) unsplittable, failed or needing review" if left else "")
                    return
                self._tick()
                room = self.gate.remaining() - (0 if anchored else 1)
                if room <= 0:
                    raise RunStop("budget", "the night budget is spent")
                n = min(self.cfg.backfill.batch_windows, room)
                if streak:
                    n = min(n, max(1, HTML_STREAK - streak))
                batch = sv.pending[:n]
                if not anchored:
                    url = anchor_full(ep)
                    self.browser.set_anchor(url)
                    self.rb.anchor, self.gate.expected_origin, anchored = url, probe.api_origin, True
                outs, reasons = self._collect(ep, probe, ledger, batch)
                if not outs:
                    per.update(status="error", reason="no request was made (access state or daily cap)")
                    return
                fetched, blocked, storage = [], None, None
                for w in batch:
                    o = outs.get(w.logical_key())
                    if o is None:
                        continue
                    kind, detail = self._record(ep, probe, ledger, w, o, reasons.get(w.logical_key(), ""))
                    if kind == "done":
                        streak = 0
                        if detail.startswith("fetched"):
                            fetched.append((w, int(detail.split()[1])))
                    elif kind == "blocked":
                        blocked = (w, detail)
                    elif kind in ("failed", "nonjson", "storage"):
                        hold.add(w)
                        streak = streak + 1 if kind == "nonjson" else 0
                        storage = detail if kind == "storage" else storage
                if blocked:
                    self._block(ep, probe, ledger, *blocked)
                if storage:
                    raise RunStop("error", storage)
                if streak >= HTML_STREAK:
                    self._notify("Invest OS: backfill needs review",
                                 f"{eid}: {streak} windows in a row returned non-JSON; stopped for the night")
                    per.update(status="stopped", reason="non-JSON responses")
                    return
                index = {k: i for i, k in enumerate(plan, 1)}
                for w, n_rows in fetched:
                    i = index.get(w)
                    if i is None or w.days < 2 or probe.mode == "per_company":
                        continue
                    if i % every and not (probe.row_cap is None and n_rows >= 0.5 * probe.max_safe_rows):
                        continue
                    if self._audit(ep, probe, ledger, w, n_rows) == "less":
                        mark_needs_review(self.cfg, eid, f"audit: halves of {w.logical_key()} hold fewer rows")
                        self._notify("Invest OS: backfill needs review",
                                     f"{eid}: rows disappear when a window narrows; stopped")
                        per.update(status="needs_review", reason="inconsistent counts in an audit")
                        return
                    if self.gate.remaining() <= 0:
                        break
        except (ValueError, ProbeError, EndpointTableError) as e:
            per.update(status="error", reason=_short(e))

    # ---- the whole run
    def run(self) -> BackfillResult:
        early = self._admit()
        if early is not None:
            return early
        try:
            eps = self.endpoints if self.endpoints is not None else load_endpoints(
                table_path(self.cfg.repo_root), today=self.ist.date())
            runnable = []
            now = self.clock()
            for ep in ordered(eps):
                probe = load_probe(self.cfg, ep)
                why = unusable_reason(probe, now)
                halted = AccessState(self.cfg, ep.source, clock=self.clock).status(sorted(NSE_HOSTS)[0]).halted \
                    if not why else False
                if why or halted:
                    self.res.per_endpoint[ep.endpoint_id] = {"status": "halted" if halted else "refused",
                                                             "reason": "adapter halted; needs a decision" if halted
                                                             else why}
                    self.log.info("endpoint_refused", endpoint=ep.endpoint_id, reason=why or "halted")
                else:
                    self.res.per_endpoint[ep.endpoint_id] = {"status": "pending", "reason": ""}
                    runnable.append((ep, probe))
        except (EndpointTableError, ProbeError, AccessStateError, OSError) as e:
            return self._finish("error", _short(e))
        if not runnable:
            return self._finish("refused", "no endpoint has a confirmed probe, nothing was requested")
        status, reason = "complete", ""
        try:
            self._begin_gate()
            for ep, probe in runnable:
                self.current = ep.endpoint_id
                self._endpoint(ep, probe)
                self.current = None
            if any(v["status"] != "complete" for v in self.res.per_endpoint.values() if v["status"] != "refused"
                   and v["status"] != "halted"):
                status = "incomplete"
        except RunStop as s:
            status, reason = s.status, s.reason
        except KeyboardInterrupt:
            status, reason = "stopped", "interrupted"
        except (OSError, LedgerError, ProbeError, ManifestError) as e:
            self.log.error("run_error", detail=str(e))
            status, reason = "error", _short(e)
        finally:
            if self.current is not None and self.current in self.res.per_endpoint:
                self.res.per_endpoint[self.current].update(status="incomplete", reason=reason or "stopped")
        self._finish(status, reason)
        for v in self.res.per_endpoint.values():
            if v["status"] == "pending":
                v.update(status="not_run", reason=reason)
        if self.res.requests:
            _touch(state_dir(self.cfg) / f"ran.{self.night}")
        if status != "blocked" and self.res.requests:
            r = self.res
            self._notify("Invest OS: backfill night", f"{r.requests} requests, {r.windows_done} windows, "
                         f"{r.rows} rows, {r.splits} splits, {r.failed} failed")
        marker = state_dir(self.cfg) / "complete.notified"
        if status == "complete" and len(runnable) == len(self.res.per_endpoint) and not marker.exists():
            self._notify("Invest OS: NSE backfill complete", f"{len(runnable)} endpoints complete")
            _touch(marker)
        return self.res


def run_backfill(cfg: Config, *, browser, manifest, clock=None, sleeper=None, notifier=None, monotonic=None,
                 wall_clock=None, hostname=None, endpoints: Mapping[str, Endpoint] | None = None,
                 max_requests: int | None = None, outside_window: bool = False, calendar=None,
                 symbols: Sequence[str] | None = None) -> BackfillResult:
    """One nightly run (R3, R10, R12-R15). There is no flag that skips the probe or the market-hours ban."""
    return _Run(cfg, browser=browser, manifest=manifest, clock=clock, sleeper=sleeper, notifier=notifier,
                monotonic=monotonic, wall_clock=wall_clock, hostname=hostname, endpoints=endpoints,
                max_requests=max_requests, outside_window=outside_window, calendar=calendar, symbols=symbols).run()


# --------------------------------------------------------------------------- the probe stage under the same rules
def run_probe(cfg: Config, *, browser, manifest, clock=None, sleeper=None, notifier=None, monotonic=None,
              wall_clock=None, hostname=None, endpoints: Mapping[str, Endpoint] | None = None,
              max_requests: int | None = None, outside_window: bool = False,
              symbols: Sequence[str] | None = None) -> BackfillResult:
    """Measure every endpoint (R4): same window, budget and block rules as the nightly run."""
    r = _Run(cfg, browser=browser, manifest=manifest, clock=clock, sleeper=sleeper, notifier=notifier,
             monotonic=monotonic, wall_clock=wall_clock, hostname=hostname, endpoints=endpoints,
             max_requests=max_requests, outside_window=outside_window, calendar=None, symbols=symbols)
    early = r._admit()
    if early is not None:
        return early
    lines: list[str] = []
    status, reason = "complete", ""
    try:
        eps = endpoints if endpoints is not None else load_endpoints(table_path(cfg.repo_root), today=r.ist.date())
        syms = r.symbols
        if syms is None and symbols_path(cfg.repo_root).exists():
            syms = load_symbols(symbols_path(cfg.repo_root))
        r._begin_gate()

        def on_block(url: str, code: int) -> None:
            host = urlsplit(url).hostname or ""
            acc = AccessState(cfg, "backfill_probe", clock=r.clock)
            for h in sorted(NSE_HOSTS):
                try:
                    acc.record_block(host=h, detail=f"http {code}", run_id=r.run_id)
                except (AccessStateError, OSError) as e:
                    r.log.error("block_mirror_failed", host=h, detail=str(e))
            _touch(state_dir(cfg) / f"block.{r.night}")
            r._notify(BLOCK_TITLE, f"{host} {code}; backfill stopped for the IST day")

        for ep in eps.values():
            r.current = ep.endpoint_id
            result = probe_endpoint(ep, browser, clock=r.clock, now_ist=r.ist.date(), store=manifest, report=lines,
                                    symbols=syms, hostname=hostname, on_block=on_block, run_id=r.run_id,
                                    max_requests=MAX_REQUESTS_PER_ENDPOINT)
            save_probe_results(cfg, [result])
            r.res.per_endpoint[ep.endpoint_id] = {"status": result.status, "reason": "; ".join(result.reasons)}
            r.current = None
    except RunStop as s:
        status, reason = s.status, s.reason
    except ProbeBlocked as b:
        status, reason = "blocked", f"http {b.status}"
    except KeyboardInterrupt:
        status, reason = "stopped", "interrupted"
    except (EndpointTableError, ProbeError, OSError, ValueError) as e:
        status, reason = "error", _short(e)
    finally:
        if r.current is not None:
            r.res.per_endpoint[r.current] = {"status": "incomplete", "reason": reason or "stopped"}
    try:
        write_probe_report(cfg, r.ist.date(), r.clock(), lines)
    except OSError as e:
        status, reason = "error", _short(e)
    if status != "blocked" and r.gate.count_run:
        _touch(state_dir(cfg) / f"probed.{r.night}")
    return r._finish(status, reason)
