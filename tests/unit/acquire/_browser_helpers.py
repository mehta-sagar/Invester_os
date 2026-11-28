"""Helpers for S1-T7 (real-Chrome transport, host-down rule, coalescing) tests.

investos imports happen inside functions only, so every test fails individually (not at collection) while
investos.acquire.browser is absent. No browser, no network, no system clock, no real sleeps: Chrome is a
`FakeDriver`/`FakeSession` (the BrowserDriver seam of spec R-seam) or a `PWWorld` (a fake of the object that
`sync_playwright()` yields, for the driver layer). Time is the FakeMono/FakeSleeper of the other helpers.
"""
from __future__ import annotations

import base64
import fcntl
import os
import threading
from dataclasses import replace
from pathlib import Path

from _collector_helpers import FakeMono, FakeTransport, resp, terr  # noqa: F401
from _rss_helpers import (NSE_REAL, RssEnv, TrackingTransport, body_of, frow, ist, make_rss, numbered)  # noqa: F401
from _s1_helpers import EPOCH0, FakeNotifier, bresp  # noqa: F401

HOST = "nsearchives.nseindia.com"
URL_N1 = NSE_REAL + "F01.xml"                    # the first row of nse_rows()
ANCHOR = f"https://{HOST}/content/RSS/Circulars.xml"   # review D1: robots.txt is a live 404 on nsearchives
ACCESS = {"rss_nse": {"mode": "real_browser", "daily_cap": 2000}}
HOSTS = {HOST: 1.0, "www.rbi.org.in": 1.0, "www.sebi.gov.in": 1.0}
CHROME_UA = ("Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) "
             "Chrome/142.0.0.0 Safari/537.36")
SENT_OK = {"user-agent": CHROME_UA, "accept": "*/*", "accept-language": "en-US,en;q=0.9",
           "sec-fetch-site": "same-origin", "sec-fetch-mode": "cors", "sec-fetch-dest": "empty",
           "sec-ch-ua": '"Chromium";v="142", "Google Chrome";v="142"', "referer": ANCHOR}
N0 = ist(2026, 11, 27, 9, 0, 30)
SLOT0 = "20261127T0900"
URL_SEBI = "https://www.sebi.gov.in/sebirss.xml"

# bodies that must survive byte for byte (R5)
NASTY = {
    "bom": b"\xef\xbb\xbf" + make_rss([numbered(1)]),
    "cp1252": make_rss([numbered(1)]).replace(b"<title>Feed", b"<title>Caf\xe9 Feed"),
    "nul": make_rss([numbered(1)]).replace(b"<title>Feed", b"<title>A\x00B Feed"),
    "crlf": make_rss([numbered(1)]).replace(b"><", b">\r\n<"),
    "random5mib": bytes((i * 2654435761 >> 7) & 0xFF for i in range(5 * 1024 * 1024)),
}


def nse_rows(n: int, *, prefix="n"):
    return [frow(f"{prefix}{i + 1:02d}", NSE_REAL + f"F{i + 1:02d}.xml", scope="always", stale="525600")
            for i in range(n)]


def b64(body: bytes) -> str:
    return base64.b64encode(body).decode("ascii")


def origin_of(url: str) -> str:
    from urllib.parse import urlsplit
    p = urlsplit(url)
    return f"{p.scheme}://{p.netloc}" if p.scheme and p.netloc else ""


# ------------------------------------------------------------------ seam fakes (FakeDriver / FakeSession)
def fetch_ok(body: bytes, *, url="", status=200, headers=None, byte_length=None, kind="ok", error="",
             redirected=False):
    from investos.acquire.browser import FetchResult
    return FetchResult(kind=kind, status=status,
                       headers={"content-type": "application/xml"} if headers is None else headers,
                       url=url, redirected=redirected, body=body,
                       byte_length=len(body) if byte_length is None else byte_length, error=error)


def fetch_err(error: str, url=""):
    return fetch_ok(b"", url=url, status=0, headers={}, kind="error", error=error)


class Cost:
    """A scripted item that first advances the fake monotonic clock (a slow timeout)."""

    def __init__(self, secs: float, item):
        self.secs, self.item = secs, item


class FakeProcTable:
    """A fake process table for PsReaper: lister() is `ps -axo pid=,command=`, killer(pid, sig) is os.kill."""

    def __init__(self):
        self.procs: dict[int, dict] = {}
        self.signals: list[tuple[int, int]] = []
        self.list_error: BaseException | None = None
        self.garbage = False
        self._pid = 100

    def add(self, cmd: str, *, ignore_term=False, pid=None) -> int:
        self._pid += 1
        pid = pid or self._pid
        self.procs[pid] = {"cmd": cmd, "ignore_term": ignore_term}
        return pid

    def lister(self) -> str:
        if self.list_error is not None:
            raise self.list_error
        if self.garbage:
            return "this is not ps output\n???\n\x00\x01\n"
        return "".join(f"{pid:>6} {p['cmd']}\n" for pid, p in self.procs.items())

    def killer(self, pid: int, sig: int) -> None:
        import signal
        self.signals.append((pid, int(sig)))
        if pid not in self.procs:
            raise ProcessLookupError(pid)
        if sig == signal.SIGKILL or (sig == signal.SIGTERM and not self.procs[pid]["ignore_term"]):
            del self.procs[pid]

    def matching(self, profile_dir) -> list[int]:
        tok = f"--user-data-dir={profile_dir}"
        return [pid for pid, p in self.procs.items() if tok in p["cmd"].split()]


class FakeReaper:
    def __init__(self, log: list, mono=None):
        self.log = log
        self.calls: list[tuple] = []
        self.on_reap = None

    def reap(self, profile_dir, *, grace_s):
        self.calls.append((str(profile_dir), grace_s, threading.get_ident()))
        self.log.append(("reap", str(profile_dir), threading.get_ident()))
        if self.on_reap is not None:
            self.on_reap()
        return 0


def lock_is_held(lock_path: Path) -> bool:
    """True when somebody else holds the exclusive flock on lock_path (probe through a fresh descriptor)."""
    if not Path(lock_path).exists():
        return False
    fd = os.open(lock_path, os.O_RDWR)
    try:
        try:
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            return True
        fcntl.flock(fd, fcntl.LOCK_UN)
        return False
    finally:
        os.close(fd)


class FakeSession:
    def __init__(self, drv: "FakeDriver", n: int):
        self.drv, self.n = drv, n
        self.origin = ""
        self.close_calls = 0
        self.thread_ids: set[int] = set()
        self.dead = False
        self.block: threading.Event | None = None

    def _rec(self, *ev):
        self.thread_ids.add(threading.get_ident())
        self.drv.log.append(ev)

    def goto_anchor(self, url, timeout_s):
        from investos.acquire.browser import AnchorResult
        self._rec("anchor", url, timeout_s, self.drv.mono.t)
        item = self.drv._next(self.drv.anchors, lambda: AnchorResult(200, url))
        if isinstance(item, BaseException):
            raise item
        self.origin = origin_of(item.final_url)
        return item

    def page_origin(self):
        return self.origin

    def fetch(self, url, timeout_s, max_bytes):
        self._rec("fetch", url, timeout_s, max_bytes, self.drv.mono.t)
        item = self.drv._next(self.drv.fetches.get(url), lambda: self.drv.default_fetch(url))
        if isinstance(item, Cost):
            self.drv.mono.t += item.secs
            item = item.item
        if callable(item) and not isinstance(item, BaseException):
            item = item(self)
        if isinstance(item, BaseException):
            raise item
        return item

    def sent_headers(self, url):
        self._rec("sent_headers", url)
        v = self.drv.sent.get(url, SENT_OK)
        if isinstance(v, BaseException):
            raise v
        return v

    def close(self, timeout_s):
        self._rec("session_close", timeout_s, self.n)
        self.close_calls += 1
        if self.drv.procs is not None and not self.dead:
            for pid in list(self.drv.session_pids.get(self.n, [])):
                self.drv.procs.procs.pop(pid, None)
        if self.drv.close_raises is not None:
            raise self.drv.close_raises


class FakeDriver:
    """BrowserDriver seam fake: records every call with arguments into `log` (shared with FakeReaper)."""

    def __init__(self, mono, log: list | None = None, procs: FakeProcTable | None = None):
        self.mono = mono
        self.log = log if log is not None else []
        self.procs = procs
        self.session_pids: dict[int, list[int]] = {}
        self.launches: list[dict] = []
        self.sessions: list[FakeSession] = []
        self.launch_errors: list = []        # consumed one per launch; None or an exception
        self.launch_cost = 0.0
        self.fetches: dict[str, list] = {}
        self.anchors: list = []
        self.sent: dict = {}
        self.close_raises: BaseException | None = None
        self.default_fetch = lambda url: fetch_ok(body_of(1), url=url)

    def set_fetch(self, url, *items):
        self.fetches[url] = list(items)

    @staticmethod
    def _next(items, default):
        if not items:
            return default()
        return items.pop(0) if len(items) > 1 else items[0]

    def launch(self, profile_dir, headless, timeout_s):
        lock = Path(profile_dir).parent / "chrome-profile.lock"
        self.log.append(("launch", str(profile_dir), headless, timeout_s))
        self.launches.append({"profile_dir": Path(profile_dir), "headless": headless, "timeout_s": timeout_s,
                              "lock_held": lock_is_held(lock), "profile_exists": Path(profile_dir).exists(),
                              "mode": (Path(profile_dir).stat().st_mode & 0o777) if Path(profile_dir).exists()
                              else None})
        self.mono.t += self.launch_cost
        err = self.launch_errors.pop(0) if self.launch_errors else None
        if err is not None:
            raise err
        s = FakeSession(self, len(self.sessions) + 1)
        self.sessions.append(s)
        if self.procs is not None:
            self.session_pids[s.n] = [self.procs.add(f"/Applications/Google Chrome --user-data-dir={profile_dir} "
                                                     f"--type={t}") for t in ("browser", "gpu", "renderer")]
        return s

    # convenience
    def kinds(self, *kinds):
        return [e for e in self.log if e[0] in kinds]

    def urls(self, kind="fetch"):
        return [e[1] for e in self.log if e[0] == kind]


# ------------------------------------------------------------------ fake Playwright (the driver layer)
FORBIDDEN_ATTRS = frozenset({
    "request", "route", "route_from_har", "unroute", "add_init_script", "new_cdp_session", "expose_function",
    "expose_binding", "set_extra_http_headers", "mouse", "keyboard", "touchscreen", "click", "dblclick", "fill", "type",
    "press", "hover", "tap", "dispatch_event", "check", "uncheck", "select_option", "set_input_files",
    "drag_and_drop", "focus", "bring_to_front", "emulate_media", "set_viewport_size", "set_content",
    "add_script_tag", "add_style_tag", "grant_permissions", "set_geolocation", "set_offline", "launch",
    "connect", "connect_over_cdp", "install"})


class _Forbidding:
    _world: "PWWorld"

    def __getattr__(self, name):
        if name in FORBIDDEN_ATTRS:
            self._world.touched.append(name)
            raise AssertionError(f"the driver touched forbidden Playwright API {name!r}")
        raise AttributeError(name)


class _FakeRequest:
    def __init__(self, url, headers):
        self.url, self._h = url, headers
        self.method = "GET"
        self.resource_type = "fetch"

    def all_headers(self):
        if isinstance(self._h, BaseException):
            raise self._h
        return dict(self._h)

    @property
    def headers(self):
        return self.all_headers()


class _FakeResponse:
    def __init__(self, url, status, headers):
        self.url, self.status, self._h = url, status, headers
        self.ok = 200 <= status < 300
        self.status_text = ""

    def all_headers(self):
        return dict(self._h)

    @property
    def headers(self):
        return dict(self._h)

    def header_value(self, name):
        return dict(self._h).get(name.lower())


class _Page(_Forbidding):
    def __init__(self, world):
        self._world = world
        self.url = "about:blank"
        self._listeners: dict[str, list] = {}
        self._id = world.next_page_id()

    def on(self, event, cb):
        self._listeners.setdefault(event, []).append(cb)

    once = on

    def remove_listener(self, event, cb):
        if cb in self._listeners.get(event, []):
            self._listeners[event].remove(cb)

    def set_default_timeout(self, ms):
        pass

    set_default_navigation_timeout = set_default_timeout

    def is_closed(self):
        return False

    def close(self, **kw):
        self._world.log.append(("page_close",))

    def _fire(self, event, obj):
        for cb in list(self._listeners.get(event, [])) + list(self._world.ctx_listeners.get(event, [])):
            cb(obj)

    def goto(self, url, **kw):
        w = self._world
        w.pages_used.add(self._id)
        w.gotos.append((url, dict(kw)))
        w.log.append(("goto", url))
        if w.goto_error is not None:
            raise w.goto_error
        status, final = w.goto_final.get(url, (200, url))
        self.url = final
        self._fire("request", _FakeRequest(url, SENT_OK))
        return _FakeResponse(final, status, {})

    def evaluate(self, expression, arg=None):
        w = self._world
        w.pages_used.add(self._id)
        w.evaluates.append((expression, arg))
        w.log.append(("evaluate",))
        if "fetch(" not in expression:
            raise AssertionError("the only page-side code allowed is the fixed fetch function (R8d)")
        url = _url_in(arg)
        w.fetch_urls.append(url)
        if url in w.sent and w.sent[url] is not False:
            self._fire("request", _FakeRequest(url, w.sent[url]))
        elif url not in w.sent:
            self._fire("request", _FakeRequest(url, SENT_OK))
        if url in w.redirect_events:
            st, hdrs = w.redirect_events[url]
            self._fire("response", _FakeResponse(url, st, hdrs))
        out = w.handler(url, arg)
        if isinstance(out, BaseException):
            raise out
        return out


def _url_in(arg):
    if isinstance(arg, str):
        return arg
    vals = list(arg.values()) if isinstance(arg, dict) else list(arg) if isinstance(arg, (list, tuple)) else []
    for v in vals:
        if isinstance(v, str) and v.startswith("https://"):
            return v
    raise AssertionError(f"cannot find the URL in the evaluate argument {arg!r}")


class _Context(_Forbidding):
    def __init__(self, world):
        self._world = world
        self.pages = [_Page(world)]

    def new_page(self):
        p = _Page(self._world)
        self.pages.append(p)
        return p

    def on(self, event, cb):
        self._world.ctx_listeners.setdefault(event, []).append(cb)

    def set_default_timeout(self, ms):
        pass

    set_default_navigation_timeout = set_default_timeout

    def close(self, **kw):
        self._world.log.append(("context_close",))
        self._world.closed.append("context")


class _Chromium(_Forbidding):
    def __init__(self, world):
        self._world = world

    def launch_persistent_context(self, *args, **kwargs):
        w = self._world
        assert not args, "launch_persistent_context must be called with keyword arguments only"
        w.launch_calls.append(dict(kwargs))
        w.log.append(("launch_persistent_context",))
        if w.launch_error is not None:
            raise w.launch_error
        w.context = _Context(w)
        return w.context


class _PW(_Forbidding):
    def __init__(self, world):
        self._world = world
        self.chromium = _Chromium(world)

    def stop(self):
        self._world.closed.append("playwright")


class _Manager:
    def __init__(self, world):
        self._world = world

    def start(self):
        return _PW(self._world)

    def __enter__(self):
        return self.start()

    def __exit__(self, *exc):
        self._world.closed.append("playwright")
        return False


class PWWorld:
    """State and scripting of the fake Playwright. `factory()` is what PlaywrightDriver(playwright_factory=...) gets."""

    def __init__(self, log: list | None = None):
        self.log = log if log is not None else []
        self.launch_calls: list[dict] = []
        self.launch_error: BaseException | None = None
        self.touched: list[str] = []
        self.evaluates: list[tuple] = []
        self.gotos: list[tuple] = []
        self.goto_final: dict = {}
        self.goto_error: BaseException | None = None
        self.fetch_urls: list[str] = []
        self.sent: dict = {}                 # url -> mapping | Exception | False (no request event)
        self.redirect_events: dict = {}      # url -> (status, headers)
        self.closed: list[str] = []
        self.pages_used: set[int] = set()
        self.ctx_listeners: dict[str, list] = {}
        self.context = None
        self._pid = 0
        self.bodies: dict[str, bytes] = {}
        self.handler = self._default_handler

    def next_page_id(self):
        self._pid += 1
        return self._pid

    def _default_handler(self, url, arg):
        return page_result(self.bodies.get(url, body_of(1)), url=url)

    def factory(self):
        return lambda: _Manager(self)

    def driver(self):
        from investos.acquire.browser import PlaywrightDriver
        return PlaywrightDriver(playwright_factory=self.factory())

    def js(self) -> str:
        return next(e for e, _ in self.evaluates if "fetch(" in e)


def page_result(body: bytes, *, url="", status=200, headers=None, kind="ok", redirected=False, byte_length=None,
                b64_text=None, error=""):
    """What the fixed in-page function returns (spec R5): headers as a list of [name, value] entries."""
    if headers is None:
        headers = [["Content-Type", "application/xml"]]
    elif isinstance(headers, dict):
        headers = [[k, v] for k, v in headers.items()]
    out = {"kind": kind, "status": status, "headers": headers, "url": url, "redirected": redirected,
           "base64": b64(body) if b64_text is None else b64_text,
           "byteLength": len(body) if byte_length is None else byte_length}
    if kind == "error":
        out["error"] = error
    return out


# ------------------------------------------------------------------ environments
class BrowserEnv(RssEnv):
    """RssEnv with the rss_nse access table, the NSE host in the rate table, a FakeDriver and a FakeReaper."""

    def __init__(self, tmp: Path, rows=None, *, access="default", interval=0.35, timeout_s=None,
                 max_body_bytes=None, launch_cost=0.0, with_procs=False, **kw):
        super().__init__(tmp, rows if rows is not None else nse_rows(3) + [frow("rbi_press", "https://www.rbi.org.in/pressreleases_rss.xml",
                                                                               kind="news", publisher="RBI")],
                         hosts=dict(HOSTS), interval=interval, access=ACCESS if access == "default" else access, **kw)
        coll = self.cfg.collect
        if timeout_s is not None:
            coll = replace(coll, timeout_s=float(timeout_s))
        if max_body_bytes is not None:
            coll = replace(coll, max_body_bytes=max_body_bytes)
        self.cfg = replace(self.cfg, collect=coll)
        self.log: list = []
        self.procs = FakeProcTable() if with_procs else None
        self.driver = FakeDriver(self.mono, self.log, self.procs)
        self.driver.launch_cost = launch_cost
        self.reaper = FakeReaper(self.log)
        self.profile_dir = self.ledger / ".state" / "chrome-profile"
        self.lock_path = self.ledger / ".state" / "chrome-profile.lock"

    def ps_reaper(self):
        from investos.acquire.browser import PsReaper
        return PsReaper(lister=self.procs.lister, killer=self.procs.killer, sleeper=self.sleeper,
                        monotonic=self.mono)

    def rb(self, *, driver=None, reaper=None, **kw):
        from investos.acquire.browser import RealBrowserTransport
        args = dict(driver=driver or self.driver, reaper=reaper or self.reaper, clock=self.clock,
                    monotonic=self.mono, wall_clock=self.wall, sleeper=self.sleeper)
        args.update(kw)
        return RealBrowserTransport(self.cfg, **args)

    def nse_collector(self, feed, slot, browser, **kw):
        from investos.acquire.rss import RssNseFeedCollector
        when = kw.pop("now", ist(2026, 11, 27, 9, 0))
        args = dict(browser=browser, manifest=self.store, cfg=self.cfg, run_id="run-1", clock=lambda: when,
                    monotonic=self.mono, sleeper=self.sleeper, wall_clock=self.wall, hostname=lambda: "test-host")
        args.update(kw)
        return RssNseFeedCollector(feed, slot, **args)

    def run_nse(self, feed, slot, browser):
        from datetime import date
        from investos.acquire.base import RunRange
        c = self.nse_collector(feed, slot, browser)
        return c.run(RunRange(date(2026, 11, 27), date(2026, 11, 27)))

    def nse_manifest(self) -> list[dict]:
        import json
        p = self.man / "rss_nse.jsonl"
        return [json.loads(l) for l in p.read_text().splitlines()] if p.exists() else []


class TimedBrowser:
    """A BrowserTransport-level fake (fetch(url) only) whose every call advances the fake clock by `cost`."""

    def __init__(self, mono, cost=0.0):
        self.mono, self.cost = mono, cost
        self.script: dict[str, list] = {}
        self.default: list = []
        self.urls: list[str] = []
        self.closed = 0

    def set(self, url, *items):
        self.script[url] = list(items)

    def fetch(self, url):
        self.urls.append(url)
        self.mono.t += self.cost
        items = self.script.get(url, self.default)
        if not items:
            return bresp(200, body_of(1), url=url)
        item = items.pop(0) if len(items) > 1 else items[0]
        if isinstance(item, BaseException):
            raise item
        return item

    def close(self):
        self.closed += 1

    def n(self, url=None):
        return len(self.urls if url is None else [u for u in self.urls if u == url])


class TimedTransport(TrackingTransport):
    def __init__(self, mono, cost=0.0):
        super().__init__(mono)
        self.cost = cost

    def get(self, url, *, headers, timeout):
        self._mono.t += self.cost
        return super().get(url, headers=headers, timeout=timeout)


def ok_resp(url, body=None):
    return bresp(200, body if body is not None else body_of(1), url=url)


def record_acquires(monkeypatch, log: list):
    """Wrap HostLimiter.acquire (every instance) so the order of limiter calls shows in `log`."""
    from investos.acquire import access
    orig = access.HostLimiter.acquire

    def rec(self, host, interval):
        log.append(("acquire", host, interval))
        return orig(self, host, interval)
    monkeypatch.setattr(access.HostLimiter, "acquire", rec)


def kinds_of(log, *kinds):
    return [e[0] for e in log if e[0] in kinds]


def dedupe(seq):
    """Collapse consecutive duplicates (a reaper sweep at construction and again before launch counts once)."""
    out = []
    for x in seq:
        if not out or out[-1] != x:
            out.append(x)
    return out
