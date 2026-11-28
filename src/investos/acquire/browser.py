"""Real-Chrome transport for the NSE archive host (spec: specs/collect_browser.md, brief S1-T7, decision 0002a).

``RealBrowserTransport`` satisfies ``base.BrowserTransport``: each request is one in-page ``fetch()`` issued from a
same-origin page of the installed Google Chrome, so Chrome's own network stack makes it. Everything above the
``BrowserDriver`` seam is testable without a browser; only ``PlaywrightDriver`` talks to Playwright, and it imports
the package lazily inside ``launch``. The module reads and sets nothing about Chrome's identity.
"""
from __future__ import annotations

import base64
import binascii
import errno
import fcntl
import json
import os
import re
import subprocess
import threading
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Mapping, Protocol
from urllib.parse import urljoin, urlsplit

from investos.acquire.access import HostLimiter
from investos.acquire.base import _FLOOR_INTERVAL_S, BrowserResponse, TransportError
from investos.acquire.manifest import _check_headers
from investos.core import log
from investos.core.config import Config
from investos.core.notify import Notifier

__all__ = ["BROWSER_HOSTS", "ANCHOR_PATH", "FetchResult", "AnchorResult", "BrowserSession", "BrowserDriver", "Reaper",
           "PsReaper", "PlaywrightDriver", "ANCHOR_PATHS", "anchor_url_for", "RealBrowserTransport", "LAUNCH_TIMEOUT_S", "CLOSE_TIMEOUT_S",
           "WATCHDOG_MARGIN_S", "PROFILE_LOCK_WAIT_S", "REAP_GRACE_S", "MAX_BODY_CAP"]

BROWSER_HOSTS: frozenset[str] = frozenset({"nsearchives.nseindia.com"})
ANCHOR_PATH = "/robots.txt"   # fallback for hosts without a known page (review D1)
# Review D1 (28 Nov live run): nsearchives has no robots.txt (HTTP 404); anchor on a page that exists, one of
# the shipped feed URLs. A non-200 anchor is logged (WARNING anchor_status) and the fetch proceeds unchanged.
ANCHOR_PATHS: dict[str, str] = {"nsearchives.nseindia.com": "/content/RSS/Circulars.xml"}


def anchor_url_for(host: str) -> str:
    return f"https://{host}{ANCHOR_PATHS.get(host, ANCHOR_PATH)}"
LAUNCH_TIMEOUT_S = 60
CLOSE_TIMEOUT_S = 15
WATCHDOG_MARGIN_S = 10
PROFILE_LOCK_WAIT_S = 5.0
REAP_GRACE_S = 5.0
MAX_BODY_CAP = 67108864
_WATCHDOG_POLL_S = 0.05
_BLOCK_STATUSES = frozenset({401, 403, 429, 451})
_DROPPED_HEADERS = frozenset({"content-encoding", "content-length"})
_LAUNCH_ARGS = ["--window-position=-2400,-2400", "--window-size=1280,800"]
_INSTALL_HINT = "playwright is not installed (pip install 'investos[browser]')"

# The one page-side function (R3). The URL, the timeout and the byte limit travel as the argument.
_FETCH_JS = """async (a) => {
  const out = (kind, status, headers, url, redirected, base64, byteLength, error) =>
    ({kind, status, headers, url, redirected, base64, byteLength, error});
  try {
    const r = await fetch(a.url, { cache: "no-store", redirect: "manual", signal: AbortSignal.timeout(a.timeoutMs) });
    const head = Array.from(r.headers.entries());
    if (r.type === "opaqueredirect") {
      return out("opaqueredirect", 0, head, r.url, false, "", 0, "");
    }
    const buf = await r.arrayBuffer();
    if (buf.byteLength > a.maxBytes) {
      return out("error", r.status, head, r.url, false, "", buf.byteLength, "too_large");
    }
    const bytes = new Uint8Array(buf);
    let text = "";
    for (let i = 0; i < bytes.length; i += 32766) {
      text += btoa(String.fromCharCode.apply(null, bytes.subarray(i, i + 32766)));
    }
    return out("ok", r.status, head, r.url, r.redirected, text, buf.byteLength, "");
  } catch (e) {
    const n = (e && e.name) || "";
    if (n === "TimeoutError" || n === "AbortError") {
      return out("error", 0, [], "", false, "", 0, "timeout");
    }
    if (e instanceof TypeError) {
      return out("error", 0, [], "", false, "", 0, "network");
    }
    return out("error", 0, [], "", false, "", 0, String(e).slice(0, 200));
  }
}"""


# --------------------------------------------------------------------------- the seam (R-seam)
@dataclass(frozen=True)
class FetchResult:
    kind: str                           # "ok" | "opaqueredirect" | "error"
    status: int
    headers: Mapping[str, str]
    url: str
    redirected: bool
    body: bytes
    byte_length: int                    # measured in the page; the body is decoded in Python (R5)
    error: str                          # "" unless kind == "error": "timeout" | "network" | "too_large" | "<text>"


@dataclass(frozen=True)
class AnchorResult:
    status: int
    final_url: str


class BrowserSession(Protocol):
    def goto_anchor(self, url: str, timeout_s: float) -> AnchorResult: ...
    def page_origin(self) -> str: ...
    def fetch(self, url: str, timeout_s: float, max_bytes: int) -> FetchResult: ...
    def sent_headers(self, url: str) -> Mapping[str, str] | None: ...
    def close(self, timeout_s: float) -> None: ...


class BrowserDriver(Protocol):
    def launch(self, profile_dir: Path, headless: bool, timeout_s: float) -> BrowserSession: ...


class Reaper(Protocol):
    def reap(self, profile_dir: Path, *, grace_s: float) -> int: ...


# --------------------------------------------------------------------------- helpers
def _origin_of(url: str) -> str:
    p = urlsplit(url)
    return f"{p.scheme}://{p.netloc}".lower() if p.scheme and p.netloc else ""


def _join_headers(entries) -> dict[str, str]:
    """Lowercase names, repeated names joined with ', '. Accepts a mapping or a list of (name, value) pairs."""
    items = entries.items() if isinstance(entries, Mapping) else entries
    out: dict[str, str] = {}
    for name, value in items:
        low = str(name).lower()
        out[low] = f"{out[low]}, {value}" if low in out else str(value)
    return out


def _clean_response_headers(headers: Mapping[str, str]) -> dict[str, str]:
    return {k: v for k, v in _join_headers(headers).items() if k not in _DROPPED_HEADERS}


def _short(exc: BaseException, n: int = 200) -> str:
    text = " ".join(str(exc).split())
    return (text or type(exc).__name__)[:n]


# --------------------------------------------------------------------------- reaper (R11)
class PsReaper:
    """Ends Chrome processes that carry exactly ``--user-data-dir=<profile>`` and nothing else."""

    def __init__(self, *, lister: Callable[[], str] | None = None, killer: Callable[[int, int], None] | None = None,
                 sleeper: Callable[[float], None] | None = None, monotonic: Callable[[], float] | None = None) -> None:
        self._lister = lister or self._ps
        self._killer = killer or os.kill
        self._sleep = sleeper or time.sleep
        self._mono = monotonic or time.monotonic
        self._log = log.get_logger("collect.browser")

    @staticmethod
    def _ps() -> str:
        out = subprocess.run(["ps", "-axo", "pid=,command="], capture_output=True, text=True, timeout=15, check=True)
        return out.stdout

    def _matching(self, profile_dir: Path) -> list[int]:
        text = self._lister()
        dirs = {os.path.abspath(profile_dir), os.path.realpath(profile_dir)}
        pats = [re.compile(r"(?:^|\s)--user-data-dir=" + re.escape(d) + r"(?=\s|$)") for d in dirs]
        found: list[int] = []
        parsed = 0
        for line in text.splitlines():
            m = re.match(r"\s*(\d+)\s+(.*\S)\s*$", line)
            if m is None:
                continue
            parsed += 1
            pid, cmd = int(m.group(1)), m.group(2)
            if pid != os.getpid() and any(p.search(cmd) for p in pats):
                found.append(pid)
        if text.strip() and parsed == 0:
            raise ValueError("process listing could not be parsed")
        return found

    def _send(self, pid: int, sig: int) -> None:
        try:
            self._killer(pid, sig)
        except ProcessLookupError:
            pass
        except PermissionError:
            self._log.warning("reaper_failed", reason="permission denied")

    def reap(self, profile_dir: Path, *, grace_s: float) -> int:
        import signal
        try:
            pids = self._matching(Path(profile_dir))
            if not pids:
                return 0
            for pid in pids:
                self._send(pid, signal.SIGTERM)
            start = self._mono()
            while True:
                alive = [p for p in self._matching(Path(profile_dir)) if p in pids]
                if not alive:
                    break
                if self._mono() - start >= grace_s:
                    for pid in alive:
                        self._send(pid, signal.SIGKILL)
                    break
                self._sleep(min(0.25, max(grace_s - (self._mono() - start), 0.01)))
            return len(pids)
        except Exception as e:                                       # never raises into a cycle
            self._log.warning("reaper_failed", reason=_short(e, 120))
            return 0


# --------------------------------------------------------------------------- the real driver (R2, R3, R8, R15)
class _PlaywrightSession:
    def __init__(self, playwright, context, page) -> None:
        self._pw, self._ctx, self._page = playwright, context, page
        self._requests: dict[str, object] = {}
        self._responses: dict[str, object] = {}
        page.on("request", lambda req: self._requests.__setitem__(req.url, req))
        page.on("response", lambda resp: self._responses.__setitem__(resp.url, resp))

    @staticmethod
    def _failure(exc: Exception, what: str) -> TransportError:
        if type(exc).__name__ == "TimeoutError":
            return TransportError("timeout")
        text = _short(exc)
        if what == "navigation" and "net::ERR_" in text:
            return TransportError("network error")
        return TransportError(f"browser failure: {text}")

    def goto_anchor(self, url: str, timeout_s: float) -> AnchorResult:
        try:
            resp = self._page.goto(url, wait_until="commit", timeout=int(timeout_s * 1000))
            status = int(resp.status) if resp is not None else 0
            return AnchorResult(status, str(self._page.url))
        except TransportError:
            raise
        except Exception as e:
            raise self._failure(e, "navigation") from None

    def page_origin(self) -> str:
        try:
            return _origin_of(str(self._page.url))
        except Exception:
            return ""

    def fetch(self, url: str, timeout_s: float, max_bytes: int) -> FetchResult:
        self._requests.pop(url, None)
        self._responses.pop(url, None)
        try:
            raw = self._page.evaluate(_FETCH_JS, {"url": url, "timeoutMs": int(timeout_s * 1000),
                                                  "maxBytes": int(max_bytes)})
        except Exception as e:
            raise self._failure(e, "evaluate") from None
        if not isinstance(raw, dict) or raw.get("kind") not in ("ok", "opaqueredirect", "error"):
            raise TransportError("browser failure: unexpected page result")
        kind = raw["kind"]
        headers = _join_headers([tuple(h) for h in raw.get("headers") or []])
        status = int(raw.get("status") or 0)
        if kind == "opaqueredirect":
            return self._redirect(url, headers)
        if kind == "error":
            return FetchResult("error", status, headers, str(raw.get("url") or ""), False, b"",
                               int(raw.get("byteLength") or 0), str(raw.get("error") or "unknown"))
        byte_length = int(raw.get("byteLength") or 0)
        if byte_length > max_bytes:
            raise TransportError("body too large")
        try:
            body = base64.b64decode(str(raw.get("base64") or ""), validate=True)
        except (binascii.Error, ValueError):
            raise TransportError("browser failure: invalid body encoding") from None
        if len(body) != byte_length:
            raise TransportError("body length mismatch")
        return FetchResult("ok", status, headers, str(raw.get("url") or url), bool(raw.get("redirected")), body,
                           byte_length, "")

    def _redirect(self, url: str, headers: dict[str, str]) -> FetchResult:
        resp = self._responses.get(url)
        status, location = 0, ""
        if resp is not None:
            try:
                status = int(resp.status)
                location = _join_headers(resp.all_headers()).get("location", "")
            except Exception:
                status, location = 0, ""
        if not 300 <= status <= 399 or not location:
            return FetchResult("opaqueredirect", 0, {}, "redirect:unobserved", False, b"", 0, "")
        return FetchResult("opaqueredirect", status, {"location": location}, urljoin(url, location), False, b"", 0, "")

    def sent_headers(self, url: str) -> Mapping[str, str] | None:
        req = self._requests.get(url)
        if req is None:
            return None
        try:
            return dict(req.all_headers())
        except Exception:
            return None

    def close(self, timeout_s: float) -> None:
        try:
            self._ctx.close()
        finally:
            self._pw.stop()


def _default_playwright_factory():
    from playwright.sync_api import sync_playwright
    return sync_playwright()


class PlaywrightDriver:
    """Drives the installed Google Chrome through Playwright. No browser is ever downloaded."""

    def __init__(self, *, playwright_factory=None) -> None:
        self._factory = playwright_factory

    def launch(self, profile_dir: Path, headless: bool, timeout_s: float) -> BrowserSession:
        factory = self._factory or _default_playwright_factory
        try:
            manager = factory()
        except ImportError:
            raise TransportError(f"browser unavailable: {_INSTALL_HINT}") from None
        pw = manager.start()
        try:
            ctx = pw.chromium.launch_persistent_context(
                user_data_dir=str(profile_dir), channel="chrome", headless=bool(headless),
                args=list(_LAUNCH_ARGS), no_viewport=True, timeout=int(timeout_s * 1000))
            page = ctx.pages[0] if ctx.pages else ctx.new_page()
        except Exception as e:
            try:
                pw.stop()
            except Exception:
                pass
            raise TransportError(f"browser unavailable: {_short(e)}") from None
        return _PlaywrightSession(pw, ctx, page)


# --------------------------------------------------------------------------- the transport (R1, R4-R7, R10-R13)
class _Watchdog:
    """Per-call daemon thread: if the call outlives its deadline on the injected clock it runs ``on_fire``.
    It never touches the session (the synchronous browser API is not thread safe)."""

    def __init__(self, mono: Callable[[], float], limit_s: float, on_fire: Callable[[], None]) -> None:
        self._mono, self._limit, self._on_fire = mono, limit_s, on_fire
        self._done = threading.Event()
        self.fired = False
        self._thread = threading.Thread(target=self._run, daemon=True, name="browser-watchdog")

    def _run(self) -> None:
        while not self._done.wait(_WATCHDOG_POLL_S):
            if self._mono() - self._start >= self._limit:
                if self._done.is_set():
                    return
                self.fired = True
                try:
                    self._on_fire()
                except Exception:
                    pass
                return

    def __enter__(self) -> "_Watchdog":
        self._start = self._mono()
        self._thread.start()
        return self

    def __exit__(self, *exc) -> None:
        self._done.set()
        self._thread.join(5.0)


class RealBrowserTransport:
    real_network = True

    def __init__(self, cfg: Config, *, driver: BrowserDriver | None = None,
                 allowed_hosts: frozenset[str] = BROWSER_HOSTS, headless: bool = False,
                 clock=None, monotonic=None, wall_clock=None, sleeper=None,
                 reaper: Reaper | None = None, notifier: Notifier | None = None) -> None:
        self._cfg = cfg
        self._driver = driver if driver is not None else PlaywrightDriver()
        self._allowed = frozenset(h.lower() for h in allowed_hosts)
        self._headless = bool(headless)
        self._mono = monotonic or time.monotonic
        self._sleep = sleeper or time.sleep
        wall = wall_clock or time.time
        self._reaper = reaper if reaper is not None else PsReaper(sleeper=sleeper, monotonic=monotonic)
        self._notifier = notifier
        self._clock = clock
        state_dir = Path(cfg.zones["ledger"]) / ".state"
        self._state_dir = state_dir
        self._profile = state_dir / "chrome-profile"
        self._lock_path = state_dir / "chrome-profile.lock"
        self._limiter = HostLimiter(state_dir, wall_clock=wall, monotonic=self._mono, sleeper=self._sleep)
        self._log = log.get_logger("collect.browser")
        self._session: BrowserSession | None = None
        self._dead = False
        self._unavailable: str | None = None
        self._lock_fd: int | None = None

    # ---- public
    def __enter__(self) -> "RealBrowserTransport":
        return self

    def __exit__(self, *exc) -> None:
        self.close()

    def fetch(self, url: str) -> BrowserResponse:
        parts = urlsplit(url)
        host = (parts.hostname or "").lower()
        if parts.scheme != "https" or host not in self._allowed or "@" in parts.netloc:
            raise ValueError(f"url is not an allowed https URL of {sorted(self._allowed)}")
        cfg = self._cfg
        timeout_s = float(cfg.collect.timeout_s)
        max_bytes = min(int(cfg.collect.max_body_bytes), MAX_BODY_CAP)
        session = self._ensure_session()
        origin = _origin_of(url)
        if session.page_origin() != origin:
            blocked = self._anchor(session, host, origin, timeout_s)
            if blocked is not None:
                return blocked
            self._limiter.acquire(host, self._interval(host))
        fr = self._guarded(timeout_s, lambda: session.fetch(url, timeout_s, max_bytes))
        resp = self._to_response(url, fr, max_bytes)
        return BrowserResponse(resp.status, resp.headers, resp.body, resp.final_url, self._sent(session, url))

    def close(self) -> None:
        session, self._session = self._session, None
        try:
            if session is not None:
                self._close_session(session)
                self._sweep()
        finally:
            self._dead = False
            self._release_lock()

    # ---- request building
    def _interval(self, host: str) -> float:
        return max(self._cfg.rate_limits.interval_for(host), _FLOOR_INTERVAL_S)

    def _guarded(self, timeout_s: float, call):
        """Run one session call under the watchdog; any failure of the browser marks the session dead."""
        limit = timeout_s + WATCHDOG_MARGIN_S
        wd = _Watchdog(self._mono, limit, lambda: self._reaper.reap(self._profile, grace_s=REAP_GRACE_S))
        try:
            with wd:
                try:
                    return call()
                except TransportError as e:
                    if wd.fired and not str(e).startswith("browser failure"):
                        raise TransportError(f"browser failure: killed after {limit:g} s") from None
                    raise
                except Exception as e:
                    raise TransportError(f"browser failure: {type(e).__name__}: {_short(e, 120)}") from None
        except TransportError as e:
            if str(e).startswith("browser failure") or wd.fired:
                self._dead = True
            raise
        finally:
            if wd.fired:
                self._dead = True

    def _anchor(self, session: BrowserSession, host: str, origin: str, timeout_s: float) -> BrowserResponse | None:
        anchor_url = f"{origin}{ANCHOR_PATHS.get(host, ANCHOR_PATH)}"
        interval = self._interval(host)
        self._limiter.acquire(host, interval)
        res = self._guarded(timeout_s, lambda: session.goto_anchor(anchor_url, timeout_s))
        if res.status in _BLOCK_STATUSES:
            return BrowserResponse(res.status, {}, b"", anchor_url, None)
        if res.status != 200:
            self._log.warning("anchor_status", host=host, status=res.status)
        final_origin = _origin_of(res.final_url)
        if final_origin != origin:
            raise TransportError(f"anchor left origin: {final_origin or 'unknown'}")
        return None

    def _to_response(self, url: str, fr: FetchResult, max_bytes: int) -> BrowserResponse:
        if fr.kind == "error":
            text = {"timeout": "timeout", "network": "network error", "too_large": "body too large"}.get(fr.error)
            raise TransportError(text or f"browser failure: {fr.error[:200]}")
        if fr.kind == "opaqueredirect":
            return BrowserResponse(fr.status, _clean_response_headers(fr.headers), b"",
                                   fr.url or "redirect:unobserved", None)
        if fr.kind != "ok":
            raise TransportError("browser failure: unexpected result kind")
        if fr.byte_length > max_bytes:
            raise TransportError("body too large")
        if len(fr.body) != fr.byte_length:
            raise TransportError("body length mismatch")
        return BrowserResponse(fr.status, _clean_response_headers(fr.headers), fr.body, fr.url or url, None)

    def _sent(self, session: BrowserSession, url: str) -> dict[str, str] | None:
        try:
            raw = session.sent_headers(url)
        except Exception:
            return self._drop("lookup failed")
        if raw is None:
            return self._drop("request not observed")
        try:
            kept = {k: v for k, v in dict(raw).items() if not (isinstance(k, str) and k.startswith(":"))}
            _check_headers(kept, redact=True)
        except (TypeError, ValueError) as e:
            return self._drop(_short(e, 100))
        return kept

    def _drop(self, reason: str) -> None:
        self._log.warning("request_headers_dropped", reason=reason)
        return None

    # ---- lifecycle (R10-R12)
    def _ensure_session(self) -> BrowserSession:
        if self._unavailable is not None:
            raise TransportError(self._unavailable)
        if self._session is not None and not self._dead:
            return self._session
        if self._session is not None:                                  # a dead session: end it, then launch once more
            old, self._session = self._session, None
            self._close_session(old)
            self._sweep()
            self._dead = False
        try:
            self._take_lock()
            self._profile.mkdir(parents=True, exist_ok=True)
            os.chmod(self._profile, 0o700)
            self._sweep()
            self._log.info("browser_launch", headless=self._headless)
            self._session = self._driver.launch(self._profile, self._headless, LAUNCH_TIMEOUT_S)
        except TransportError as e:
            self._unavailable = str(e)
        except Exception as e:
            self._unavailable = f"browser unavailable: {type(e).__name__}: {_short(e, 150)}"
        if self._unavailable is not None:
            self._release_lock()
            raise TransportError(self._unavailable)
        return self._session

    def _close_session(self, session: BrowserSession) -> None:
        wd = _Watchdog(self._mono, CLOSE_TIMEOUT_S, lambda: self._reaper.reap(self._profile, grace_s=REAP_GRACE_S))
        try:
            with wd:
                session.close(CLOSE_TIMEOUT_S)
        except Exception as e:
            self._log.warning("browser_close_failed", reason=_short(e, 120))

    def _sweep(self) -> None:
        try:
            self._reaper.reap(self._profile, grace_s=REAP_GRACE_S)
        except Exception as e:
            self._log.warning("reaper_failed", reason=_short(e, 120))

    def _take_lock(self) -> None:
        if self._lock_fd is not None:
            return
        self._state_dir.mkdir(parents=True, exist_ok=True)
        fd = os.open(self._lock_path, os.O_RDWR | os.O_CREAT, 0o644)
        start = self._mono()
        while True:
            try:
                fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
                self._lock_fd = fd
                return
            except OSError as e:
                if e.errno not in (errno.EAGAIN, errno.EACCES):
                    os.close(fd)
                    raise TransportError(f"browser unavailable: profile lock: {_short(e, 80)}") from None
            if self._mono() - start >= PROFILE_LOCK_WAIT_S:
                os.close(fd)
                raise TransportError("browser unavailable: profile busy")
            self._sleep(0.25)

    def _release_lock(self) -> None:
        fd, self._lock_fd = self._lock_fd, None
        if fd is not None:
            try:
                os.close(fd)
            except OSError:
                pass
