"""Helpers for S1-T1 (collector amendment) tests. investos imports happen inside functions only,
so every test fails individually while the new modules/config are absent.

Real files, fake clocks. Network: a 127.0.0.1 http.server only (see local_server)."""
from __future__ import annotations

import gzip
import json
import multiprocessing
import os
import threading
import time
import zlib
from dataclasses import replace
from datetime import datetime, timedelta, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from zoneinfo import ZoneInfo

import pytest

from _collector_helpers import (CSV, D17, D18, D19, NOW, SOURCE, SOURCE_VERSION, Env, FakeMono,  # noqa: F401
                                default_check, demo_class, env, events, logs, resp, rr, terr, url_of)

IST = ZoneInfo("Asia/Kolkata")
EPOCH0 = 1_795_000_000.0          # fake wall clock origin (epoch seconds)
HOSTNAME = "test-host"
CONTACT = "test@example.invalid"
HOST = "example.test"
TODAY = "2026-11-21"              # IST date of NOW (09:30 UTC = 15:00 IST)


# ------------------------------------------------------------------ config rendering
def render_toml(*, contact=CONTACT, access=None, hosts=None, interval=0.35, timeout_s=None,
                max_body_bytes=None, collect_extra="") -> str:
    out = ['[general]', 'timezone = "Asia/Kolkata"', '', '[paths]']
    for k, v in [("raw", "raw"), ("manifests", "manifests"), ("canonical", "canonical"), ("ledger", "ledger"),
                 ("reports", "reports"), ("registry", "registry")]:
        out.append(f'{k} = "{v}"')
    out += ['', '[db]', 'migrations_dir = "mig"', '', '[rate_limits]', f'default_interval_s = {interval}', '',
            '[rate_limits.hosts]']
    for h, v in (hosts or {}).items():
        out.append(f'"{h}" = {v}')
    coll = []
    if contact is not None:
        coll.append(f'contact = {json.dumps(contact)}')
    if timeout_s is not None:
        coll.append(f'timeout_s = {timeout_s}')
    if max_body_bytes is not None:
        coll.append(f'max_body_bytes = {max_body_bytes}')
    if coll or access or collect_extra:
        out += ['', '[collect]', *coll]
        if collect_extra:
            out.append(collect_extra)
    for src, a in (access or {}).items():
        out += ['', f'[collect.access.{src}]']
        for k, v in a.items():
            out.append(f'{k} = {json.dumps(v)}')
    return "\n".join(out) + "\n"


def write_decision(root: Path, name: str, *lines: str) -> Path:
    d = root / "docs" / "decisions"
    d.mkdir(parents=True, exist_ok=True)
    p = d / name
    p.write_text("# decision\n\n" + "\n".join(lines) + "\n")
    return p


class S1Env(Env):
    """Env whose cfg is built by load_config from a rendered TOML (so [collect] is real) and whose
    clock, wall clock and hostname are injectable. All state lives under tmp."""

    def __init__(self, tmp: Path, *, access=None, hosts=None, interval=0.35, contact=CONTACT,
                 timeout_s=None, max_body_bytes=None, collect_extra=""):
        super().__init__(tmp, hosts=hosts, interval=interval)
        from investos.core.config import load_config
        for d in ("canonical", "ledger", "reports", "registry", "mig", "config"):
            (tmp / d).mkdir(exist_ok=True)
        self.toml_path = tmp / "config" / "investos.toml"
        self.toml_path.write_text(render_toml(contact=contact, access=access, hosts=hosts, interval=interval,
                                              timeout_s=timeout_s, max_body_bytes=max_body_bytes,
                                              collect_extra=collect_extra))
        self.cfg = load_config(self.toml_path, repo_root=tmp, env={})
        self.now = NOW
        self.clock = lambda: self.now
        self.wall = lambda: EPOCH0 + self.mono.t
        self.ledger = tmp / "ledger"
        self.browser = FakeBrowser(self.mono)
        self.notifier = FakeNotifier()

    # ---- time
    def at_ist(self, date_s: str, hms: str = "12:00:00") -> None:
        d = datetime.fromisoformat(f"{date_s}T{hms}").replace(tzinfo=IST)
        self.now = d.astimezone(timezone.utc)

    # ---- construction
    def cfg_contact(self, contact):
        return replace(self.cfg, collect=replace(self.cfg.collect, contact=contact))

    def make(self, cls=None, *, run_id="run-1", transport="default", browser=None, hostname=lambda: HOSTNAME,
             notifier=None, manifest=None, cfg=None, wall=True, sleeper=None, **kw):
        cls = cls or demo_class()
        mode = getattr(cls, "access_mode", None)
        if browser is None and getattr(mode, "value", mode) == "real_browser" and transport == "default":
            browser = self.browser
        if transport == "default":
            transport = None if browser is not None else self.transport
        args = dict(transport=transport, browser=browser, manifest=manifest or self.store, cfg=cfg or self.cfg,
                    run_id=run_id, clock=self.clock, monotonic=self.mono, sleeper=sleeper or self.sleeper, hostname=hostname)
        if wall:
            args["wall_clock"] = self.wall
        if notifier is not None:
            args["notifier"] = notifier
        return cls(**args, **kw)

    # ---- state files
    def host_file(self, host=HOST) -> Path:
        return self.ledger / "access" / "hosts" / f"{host}.jsonl"

    def source_file(self, source=SOURCE) -> Path:
        return self.ledger / "access" / f"{source}.jsonl"

    def count_file(self, source=SOURCE, date_s=TODAY) -> Path:
        return self.ledger / ".state" / "requests" / f"{source}.{date_s}.count"

    def host_events(self, host=HOST) -> list[dict]:
        p = self.host_file(host)
        return [json.loads(l) for l in p.read_text().splitlines()] if p.exists() else []

    def source_events(self, source=SOURCE) -> list[dict]:
        p = self.source_file(source)
        return [json.loads(l) for l in p.read_text().splitlines()] if p.exists() else []

    def plant_block(self, host=HOST, source=SOURCE, ts=None, **kw) -> None:
        ts = ts or self.now
        append_line(self.host_file(host), event_line("blocked", source, host, ts, **kw))

    def plant_halt(self, source=SOURCE, ts=None) -> None:
        append_line(self.source_file(source), event_line("halted", source, None, ts or self.now,
                                                         detail="3 blocked days in 7"))

    def reasons(self, result) -> dict:
        return {g["key"]: g["reason"] for g in self.sidecar(result)["gaps"]}


def ts_of(t) -> str:
    if isinstance(t, str):
        return t
    return t.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%fZ")


def event_line(event, source, host, ts, *, detail="x", run_id="r0", decision=None, **over) -> str:
    t = ts_of(ts)
    dt = datetime.fromisoformat(t.replace("Z", "+00:00"))
    obj = {"schema": 1, "ts": t, "date": dt.astimezone(IST).date().isoformat(), "event": event,
           "source": source, "host": host, "detail": detail, "run_id": run_id, "decision": decision}
    obj.update(over)
    return json.dumps(obj, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


def append_line(path: Path, line: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "ab") as f:
        f.write(line.encode() + b"\n")


def mode_cls(mode: str, **attrs):
    from investos.acquire.base import AccessMode
    return demo_class(access_mode=AccessMode(mode), **attrs)


def cfg_access(mode="honest", **kw) -> dict:
    d = {"mode": mode}
    if mode != "honest":
        d.setdefault("daily_cap", 300)
    d.update(kw)
    return {SOURCE: d}


def scoped_env(tmp, **kw) -> "S1Env":
    """Env with a valid scoped_exception setup for SOURCE (decision file 0003 present)."""
    e = S1Env(tmp, access=cfg_access("scoped_exception", decision="0003"), **kw)
    write_decision(tmp, "0003-test.md", f"Adapter: {SOURCE}", "Access-mode: scoped_exception")
    return e


# ------------------------------------------------------------------ fakes
class FakeBrowser:
    """fetch(url) only. Scripted list of BrowserResponse | BaseException; last item repeats."""
    def __init__(self, mono=None):
        self.script: list = []
        self.urls: list[str] = []
        self.by_url: dict[str, list] = {}

    def set(self, *items, url=None):
        if url is None:
            self.script = list(items)
        else:
            self.by_url[url] = list(items)

    def fetch(self, url):
        self.urls.append(url)
        items = self.by_url.get(url, self.script)
        if not items:
            raise AssertionError(f"unscripted browser fetch {url}")
        item = items.pop(0) if len(items) > 1 else items[0]
        if isinstance(item, BaseException):
            raise item
        return item


def bresp(status=200, body=CSV, *, url, final_url=None, headers=None, request_headers=None):
    from investos.acquire.base import BrowserResponse
    return BrowserResponse(status=status, headers=headers or {}, body=body,
                           final_url=final_url if final_url is not None else url, request_headers=request_headers)


class FakeNotifier:
    def __init__(self, raises: BaseException | None = None):
        self.calls: list[tuple[str, str]] = []
        self.raises = raises

    def notify(self, title, message):
        self.calls.append((title, message))
        if self.raises:
            raise self.raises


# ------------------------------------------------------------------ local HTTP server (127.0.0.1 only)
def gzip_bytes(b: bytes) -> bytes:
    return gzip.compress(b, 9)


def make_routes() -> dict:
    big = b"\0" * (4 * 1024 * 1024)
    plain = b"hello,world\n" * 50

    def send(h, status=200, body=b"", headers=None, length=True):
        h.send_response(status)
        for k, v in (headers or {}).items():
            if isinstance(v, list):
                for x in v:
                    h.send_header(k, x)
            else:
                h.send_header(k, v)
        if length:
            h.send_header("Content-Length", str(len(body)))
        h.end_headers()
        h.wfile.write(body)

    def drip(h):
        h.send_response(200)
        h.send_header("Content-Length", "100000")
        h.end_headers()
        for _ in range(60):
            try:
                h.wfile.write(b"x")
                h.wfile.flush()
            except OSError:
                return
            time.sleep(0.2)

    def slow(h):
        time.sleep(1.0)
        try:
            send(h, 200, b"late")
        except OSError:
            pass

    return {
        "/ok": lambda h: send(h, 200, plain, {"X-Multi": ["a", "b"], "X-Mixed-Case": "v"}),
        "/redir": lambda h: send(h, 302, b"moved", {"Location": "/ok"}),
        "/s404": lambda h: send(h, 404, b"nope"),
        "/s503": lambda h: send(h, 503, b"busy"),
        "/gzip": lambda h: send(h, 200, gzip_bytes(plain), {"Content-Encoding": "gzip"}),
        "/deflate": lambda h: send(h, 200, zlib.compress(plain), {"Content-Encoding": "deflate"}),
        "/deflate-raw": lambda h: send(h, 200, _raw_deflate(plain), {"Content-Encoding": "deflate"}),
        "/bomb": lambda h: send(h, 200, gzip_bytes(big), {"Content-Encoding": "gzip"}),
        "/br": lambda h: send(h, 200, b"BR:" + plain, {"Content-Encoding": "br"}),
        "/slow": slow,
        "/drip": drip,
        "plain": plain,
    }


def _raw_deflate(b: bytes) -> bytes:
    c = zlib.compressobj(9, zlib.DEFLATED, -15)
    return c.compress(b) + c.flush()


class LocalServer:
    def __init__(self):
        routes = make_routes()
        self.plain = routes.pop("plain")
        self.seen: list[dict] = []
        outer = self

        class H(BaseHTTPRequestHandler):
            def log_message(self, *a):
                pass

            def do_GET(self):
                outer.seen.append({"path": self.path,
                                   "names": [k.lower() for k in self.headers.keys()],
                                   "headers": {k.lower(): v for k, v in self.headers.items()}})
                fn = routes.get(self.path.split("?")[0])
                if fn is None:
                    self.send_response(500)
                    self.end_headers()
                    return
                try:
                    fn(self)
                except OSError:
                    pass

        class S(ThreadingHTTPServer):
            daemon_threads = True

            def handle_error(self, request, client_address):
                pass

        self.httpd = S(("127.0.0.1", 0), H)
        self.port = self.httpd.server_address[1]
        self.thread = threading.Thread(target=lambda: self.httpd.serve_forever(poll_interval=0.02), daemon=True)
        self.thread.start()

    def url(self, path: str) -> str:
        return f"http://127.0.0.1:{self.port}{path}"

    def close(self):
        self.httpd.shutdown()
        self.httpd.server_close()


@pytest.fixture
def server():
    s = LocalServer()
    yield s
    s.close()


def http_transport(**kw):
    from investos.acquire.transport import UrllibTransport
    kw.setdefault("allowed_schemes", ("http",))
    return UrllibTransport(**kw)


# ------------------------------------------------------------------ child processes (spawn)
CTX = multiprocessing.get_context("spawn")


def collect_worker(spec: dict, out) -> None:
    """Run one collector in a fresh process. Real monotonic/sleep/wall, injected UTC clock (spec['now'])."""
    from investos.acquire.base import Collector, Response, RunRange
    from investos.acquire.manifest import ManifestStore
    from investos.core.config import load_config
    from datetime import date
    tmp = Path(spec["tmp"])
    cfg = load_config(tmp / "config" / "investos.toml", repo_root=tmp, env={})
    store = ManifestStore(cfg.zones["raw"], cfg.zones["manifests"])
    calls: list[str] = []
    hold, reached, go = spec.get("hold_key"), spec.get("reached"), spec.get("go")
    status_map = spec.get("status", {})

    class T:
        def get(self, url, *, headers, timeout):
            calls.append(url)
            key = url.rsplit("/", 1)[1][:-4]
            st = status_map.get(key, 200)
            return Response(st, {}, CSV if st == 200 else b"denied", url)

    def verdict(self, key, r):
        if key == hold and reached is not None:
            reached.set()
            go.wait(30)
        return default_check(self, key, r)

    cls = demo_class(fixed_keys=spec["keys"], verdict_fn=verdict)
    now = datetime.fromisoformat(spec["now"])
    if spec.get("pre_barrier") is not None:
        spec["pre_barrier"].wait(30)
    c = cls(transport=T(), manifest=store, cfg=cfg, run_id=spec["run_id"], clock=lambda: now,
            hostname=lambda: "child")
    a, b = spec["range"]
    r = c.run(RunRange(date.fromisoformat(a), date.fromisoformat(b)))
    sc = json.loads(Path(r.sidecar_path).read_text())
    out.put({"status": r.status, "calls": calls, "outcomes": {k: v.value for k, v in r.outcomes.items()},
             "reasons": {g["key"]: g["reason"] for g in sc["gaps"]}})


def spawn_collect(spec: dict):
    q = CTX.Queue()
    p = CTX.Process(target=collect_worker, args=(spec, q))
    p.start()
    return p, q


def get_result(q, procs, timeout=60):
    """q.get that fails fast when a child died without answering."""
    import queue
    end = time.monotonic() + timeout
    while time.monotonic() < end:
        try:
            return q.get(timeout=0.2)
        except queue.Empty:
            dead = [p for p in procs if p.exitcode not in (None, 0)]
            if dead:
                try:
                    return q.get(timeout=0.2)
                except queue.Empty:
                    pytest.fail(f"child process died with exit code {dead[0].exitcode} before answering")
    pytest.fail("child process timed out")


def finish(p, q, timeout=60):
    res = get_result(q, [p], timeout)
    p.join(timeout)
    assert p.exitcode == 0
    return res


def limiter_worker(state_dir: str, host: str, n: int, interval: float, out) -> None:
    from investos.acquire.access import HostLimiter
    lim = HostLimiter(Path(state_dir), wall_clock=time.time, monotonic=time.monotonic, sleeper=time.sleep)
    starts = []
    for _ in range(n):
        lim.acquire(host, interval)
        starts.append(time.time())
    out.put(starts)


def torn_writer(path: str, line: str, ready, hold: float) -> None:
    """Hold the writer lock, leave a half line in the file, finish it after `hold` seconds."""
    import fcntl
    with open(path + ".lock", "a+") as lk:
        fcntl.flock(lk, fcntl.LOCK_EX)
        with open(path, "ab") as f:
            f.write(line[:20].encode())
            f.flush()
            ready.set()
            time.sleep(hold)
            f.write(line[20:].encode() + b"\n")
            f.flush()
        fcntl.flock(lk, fcntl.LOCK_UN)
