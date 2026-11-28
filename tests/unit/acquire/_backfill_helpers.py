"""Helpers for S1-T8 (real-browser history backfill) tests.

investos imports happen inside functions only, so every test fails individually (not at collection) while the
`investos.backfill` package is absent. No network, no browser, no system clock, no real sleeps: Chrome is a
`BfDriver`/`BfSession` (the BrowserDriver seam of collect_browser) behind a REAL `RealBrowserTransport`; the NSE site
is a `World` that serves rows from a per-endpoint model (density per day, cap, earliest date, inclusivity, ...).
Time is the fake monotonic clock: `env.now` = base instant + fake seconds, so sleeps advance the IST clock.
"""
from __future__ import annotations

import csv
import json
import re
from dataclasses import replace
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from urllib.parse import parse_qs, urlsplit

from _browser_helpers import CHROME_UA, FakeReaper, fetch_ok, origin_of  # noqa: F401
from _collector_helpers import FakeMono, FakeSleeper  # noqa: F401
from _rss_helpers import IST, UTC, ist, write_calendar  # noqa: F401
from _s1_helpers import EPOCH0, FakeNotifier, S1Env, append_line, event_line  # noqa: F401

REPO = Path(__file__).resolve().parents[3]
SRC = REPO / "src" / "investos"
COM = "https://www.nseindia.com"
IN = "https://www.nseindia.in"
THIRD = "https://www.nseindia.example"
IDS = ["announcements", "pit", "corporate_actions", "shareholding", "bulk_deals", "block_deals", "board_meetings",
       "results"]
CORE_IDS = ["announcements", "pit", "corporate_actions", "bulk_deals", "block_deals"]
RUN_ORDER = CORE_IDS + ["shareholding", "board_meetings", "results"]       # Sagar's answer 2: core first
HEADER = ("endpoint_id,page_url,anchor_url,api_path,params,date_format,to_inclusive,max_window_days,row_cap,"
          "rows_path,date_field,start_date,time_of_day,known_at_rule,status,notes")
COLS = HEADER.split(",")
SHIPPED_TABLE = REPO / "config" / "backfill_endpoints.csv"
EXAMPLE_TOML = REPO / "config" / "investos.example.toml"
EXAMPLE_PLIST = REPO / "ops" / "launchd" / "com.investos.backfill.plist.example"
BF_SRC = SRC / "backfill"
SPEC = REPO / "specs" / "collect_backfill.md"

# 2026-11-29 is a Sunday; the nightly window is 00:30-06:30 IST.
NIGHT1 = ist(2026, 11, 29, 0, 35)
TUESDAY_NOON = ist(2026, 12, 1, 12, 0)
JSON_HDR = {"content-type": "application/json"}


# ------------------------------------------------------------------ the endpoint table
def table_row(eid: str, **over) -> dict:
    d = {"endpoint_id": eid, "page_url": f"/page/{eid}", "anchor_url": f"/page/{eid}",
         "api_path": f"/api/{eid}?from_date={{from}}&to_date={{to}}", "params": "from_date={from}&to_date={to}",
         "date_format": "DD-MM-YYYY", "to_inclusive": "", "max_window_days": "1", "row_cap": "", "rows_path": "",
         "date_field": "an_dt", "start_date": "2026-11-01", "time_of_day": "", "known_at_rule": "end of IST day",
         "status": "to_probe", "notes": ""}
    d.update({k: str(v) for k, v in over.items()})
    return d


def write_table(path: Path, rows: list[dict], *, header: str = HEADER) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    cols = header.split(",")
    with path.open("w", newline="", encoding="utf-8") as fh:
        w = csv.writer(fh, lineterminator="\n")
        w.writerow(cols)
        for r in rows:
            w.writerow([r.get(c, "") for c in cols])
    return path


def default_rows(ids=IDS, **over) -> list[dict]:
    return [table_row(i, **over) for i in ids]


# ------------------------------------------------------------------ the site model
_MON = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"]


def day_text(d: date, with_time: bool) -> str:
    s = f"{d.day:02d}-{_MON[d.month - 1]}-{d.year}"
    return s + " 10:15:00" if with_time else s


class EpData:
    """What one endpoint of the fake NSE site serves. Every attribute may be changed by a test."""

    def __init__(self, eid: str):
        self.eid = eid
        self.density = lambda d: 5                  # rows on day d (true rows)
        self.cap: int | None = None                 # a call never returns more than this
        self.earliest = date(2005, 1, 1)
        self.to_inclusive = True
        self.from_inclusive = True
        self.wrapper = ""                           # "" = bare list, else {"<wrapper>": [...], "meta": {...}}
        self.time_from = date(1990, 1, 1)           # rows carry a time of day from this date on
        self.mixed = False                          # every second row carries a time
        self.date_field = "an_dt"
        self.undercount = None                      # fn(width_days, n_true, from_date) -> n served
        self.row_hook = None                        # fn(rows, s, e) -> rows
        self.mode = "ok"                            # ok | html | empty_obj | error_obj | not_json
        self.market_wide = True
        self.symbol_rows = 23
        self.calls = 0
        self.windows: list[tuple[date, date]] = []  # (from, to) of every call, as requested

    def true_rows(self, s: date, e_req: date, symbol=None) -> list[dict]:
        if symbol is not None:
            return [{"symbol": symbol, "qtr": f"q{i}", self.date_field: day_text(date(2026, 9, 30) - timedelta(days=91 * i), False)}
                    for i in range(self.symbol_rows)]
        if not self.market_wide:
            return []
        e = e_req if self.to_inclusive else e_req - timedelta(days=1)
        d = max(s if self.from_inclusive else s + timedelta(days=1), self.earliest)
        out = []
        while d <= e:
            for i in range(self.density(d)):
                out.append({"symbol": f"S{i}", self.date_field: day_text(d, d >= self.time_from or (self.mixed and i % 2)),
                            "desc": "x"})
            d += timedelta(days=1)
        return out

    def body(self, s: date | None, e_req: date | None, symbol=None) -> bytes:
        self.calls += 1
        if self.mode == "html":
            return b"<html><body>Access Denied</body></html>"
        if self.mode == "empty_obj":
            return b"{}"
        if self.mode == "error_obj":
            return b'{"status":"error"}'
        if self.mode == "not_json":
            return b"\xff\xfe not json"
        if symbol is not None:
            rows = self.true_rows(None, None, symbol)
        else:
            rows = self.true_rows(s, e_req)
            if self.row_hook is not None:
                rows = self.row_hook(rows, s, e_req)
            n = len(rows)
            if self.undercount is not None:
                n = min(n, self.undercount((e_req - s).days + 1, n, s))
            if self.cap is not None:
                n = min(n, self.cap)
            rows = rows[:n]
        obj = rows if not self.wrapper else {self.wrapper: rows, "meta": {"call": self.calls}}
        return json.dumps(obj).encode("utf-8")


def _parse_q(v: str) -> date:
    v = v.strip()
    if re.fullmatch(r"\d{2}-\d{2}-\d{4}", v):
        return date(int(v[6:]), int(v[3:5]), int(v[:2]))
    return date.fromisoformat(v)


class World:
    def __init__(self):
        self.eps = {i: EpData(i) for i in IDS}
        self.anchor_final: dict[str, str] = {}      # anchor url -> final url (default: same path on the .in origin)
        self.anchor_status = 200
        self.anchor_status_for: dict[str, int] = {}
        self.fixed_final_origin: str | None = None

    def anchor(self, url: str):
        from investos.acquire.browser import AnchorResult
        p = urlsplit(url)
        origin = self.fixed_final_origin or (IN if p.hostname == "www.nseindia.com" else f"{p.scheme}://{p.netloc}")
        final = self.anchor_final.get(url, origin + p.path)
        return AnchorResult(self.anchor_status_for.get(url, self.anchor_status), final)

    def data_ep(self, url: str) -> EpData | None:
        p = urlsplit(url)
        m = re.fullmatch(r"/api/([a-z_]+)", p.path)
        return self.eps.get(m.group(1)) if m else None

    def handle(self, url: str) -> bytes:
        ep = self.data_ep(url)
        if ep is None:
            raise AssertionError(f"the fake site has no endpoint for {url}")
        q = parse_qs(urlsplit(url).query)
        if "symbol" in q:
            return ep.body(None, None, q["symbol"][0])
        s = _parse_q((q.get("from_date") or q["from"])[0])
        e = _parse_q((q.get("to_date") or q["to"])[0])
        ep.windows.append((s, e))
        return ep.body(s, e)


class BfSession:
    def __init__(self, drv: "BfDriver", n: int):
        self.drv, self.n = drv, n
        self.origin = ""
        self.closed = False

    def goto_anchor(self, url, timeout_s):
        self.drv.log.append(("anchor", url, self.drv.mono.t))
        self.drv.anchors.append(url)
        if self.drv.anchor_hook is not None:
            r = self.drv.anchor_hook(len(self.drv.anchors), url)
            if isinstance(r, BaseException):
                raise r
            if r is not None:
                self.origin = origin_of(r.final_url)
                return r
        r = self.drv.world.anchor(url)
        self.origin = origin_of(r.final_url)
        return r

    def page_origin(self):
        return self.origin

    def fetch(self, url, timeout_s, max_bytes):
        d = self.drv
        d.n_fetch += 1
        d.log.append(("fetch", url, d.mono.t))
        d.fetched.append(url)
        if d.fetch_hook is not None:
            r = d.fetch_hook(d.n_fetch, url)
            if isinstance(r, BaseException):
                raise r
            if r is not None:
                return r
        return fetch_ok(d.world.handle(url), url=url, headers=JSON_HDR)

    def sent_headers(self, url):
        return {"user-agent": CHROME_UA, "accept": "*/*", "referer": COM + "/page/x"}

    def close(self, timeout_s):
        self.closed = True


class BfDriver:
    """BrowserDriver seam fake. fetch_hook(n, url) may return a FetchResult, raise, or return None (serve normally);
    n counts data fetches over the whole test. anchor_hook(n, url) does the same for anchors."""

    def __init__(self, mono, world: World):
        self.mono, self.world = mono, world
        self.log: list[tuple] = []
        self.anchors: list[str] = []
        self.fetched: list[str] = []
        self.n_fetch = 0
        self.launches: list[dict] = []
        self.fetch_hook = None
        self.anchor_hook = None

    def launch(self, profile_dir, headless, timeout_s):
        self.log.append(("launch", str(profile_dir), self.mono.t))
        self.launches.append({"profile_dir": Path(profile_dir), "headless": headless})
        return BfSession(self, len(self.launches))

    # ---- inspection
    def starts(self) -> list[float]:
        return [e[2] for e in self.log if e[0] in ("anchor", "fetch")]

    def n_requests(self) -> int:
        return len(self.starts())

    def anchor_pages(self) -> list[str]:
        return [urlsplit(u).path for u in self.anchors]


def html_result(url: str = "", status: int = 200):
    return fetch_ok(b"<html><body>challenge</body></html>", url=url, status=status, headers={"content-type": "text/html"})


def status_result(status: int, url: str = ""):
    return fetch_ok(b"denied", url=url, status=status, headers={"content-type": "text/plain"})


class DirectBrowser:
    """A BrowserTransport-level fake (fetch(url) only) served by the World: for collector-level tests."""

    def __init__(self, world: World, mono, cost=0.0):
        self.world, self.mono, self.cost = world, mono, cost
        self.urls: list[str] = []
        self.override: dict[str, list] = {}
        self.bodies: dict[str, bytes] = {}

    def fetch(self, url):
        from investos.acquire.base import BrowserResponse
        self.urls.append(url)
        self.mono.t += self.cost
        items = self.override.get(url)
        if items:
            item = items.pop(0) if len(items) > 1 else items[0]
            if isinstance(item, BaseException):
                raise item
            return item
        body = self.bodies.get(url)
        if body is None:
            body = self.world.handle(url)
        return BrowserResponse(200, JSON_HDR, body, url, {"user-agent": CHROME_UA})

    def close(self):
        pass


# ------------------------------------------------------------------ probe artifacts
def ts_text(dt: datetime) -> str:
    return dt.astimezone(UTC).strftime("%Y-%m-%dT%H:%M:%S.%fZ")


def probe_entry(ep, *, probed_at: str, **over) -> dict:
    d = {"endpoint_id": ep.endpoint_id, "row_hash": ep.row_hash(), "probed_at": probed_at, "api_origin": IN,
         "rows_path": "", "date_field": "an_dt", "date_format": "DD-MM-YYYY", "to_inclusive": True, "row_cap": None,
         "max_safe_rows": 10, "max_window_days": 1, "earliest_date": "2026-11-20", "time_of_day": "all",
         "undercount": False, "mode": "", "status": "confirmed", "reasons": [], "rows_per_day": 5.0, "depth": None,
         "symbol_source": ""}
    d.update(over)
    return d


def make_probe(ep, **over):
    """A ProbeResult built directly (planner tests)."""
    from investos.backfill.probe import ProbeResult
    d = probe_entry(ep, probed_at="2026-11-28T00:00:00.000000Z", **over)
    d["earliest_date"] = date.fromisoformat(d["earliest_date"]) if isinstance(d["earliest_date"], str) \
        else d["earliest_date"]
    d["reasons"] = tuple(d["reasons"])
    return ProbeResult(**d)


# ------------------------------------------------------------------ the environment
def rendered_access() -> dict:
    out = {f"backfill_{i}": {"mode": "real_browser", "daily_cap": 9000} for i in IDS}
    out["backfill_probe"] = {"mode": "real_browser", "daily_cap": 9000}
    return out


class BfEnv(S1Env):
    def __init__(self, tmp: Path, *, rows=None, bf: dict | None = None, ids=IDS, **row_over):
        super().__init__(tmp, access=rendered_access(), hosts={"www.nseindia.com": 2.5, "www.nseindia.in": 2.5},
                         interval=0.35)
        from investos.core.config import load_config
        b = {"ramp": [100000, 100000], "window_start": "00:30", "window_end": "06:30", "batch_windows": 25,
             "audit_every": 50, "profile_name": "chrome-profile-backfill"}
        b.update(bf or {})
        self.bf = b
        with self.toml_path.open("a", encoding="utf-8") as fh:
            fh.write("\n[backfill]\n")
            for k, v in b.items():
                fh.write(f"{k} = {json.dumps(v)}\n")
        self.cfg = load_config(self.toml_path, repo_root=tmp, env={})
        self.table_path = tmp / "config" / "backfill_endpoints.csv"
        self.rows = rows if rows is not None else default_rows(ids, **row_over)
        write_table(self.table_path, self.rows)
        self.t0 = NIGHT1
        self.clock = lambda: self.t0 + timedelta(seconds=self.mono.t)
        self.world = World()
        self.driver = BfDriver(self.mono, self.world)
        self.reaper = FakeReaper([])
        self._tr = None
        self.hostname = lambda: "test-host"
        write_calendar(tmp, years=(2026,), trading_days=())

    # ---- time
    @property
    def now(self):
        return self.clock()

    @now.setter
    def now(self, v):
        self.t0 = v - timedelta(seconds=self.mono.t)

    def at(self, when: datetime) -> None:
        self.now = when

    def advance(self, seconds: float) -> None:
        self.mono.t += seconds

    def set_bf(self, **kw) -> None:
        from investos.core.config import BackfillConfig
        cur = self.cfg.backfill
        kw = {k: (tuple(v) if k == "ramp" else v) for k, v in kw.items()}
        self.cfg = replace(self.cfg, backfill=replace(cur, **kw))
        self._tr = None

    # ---- table, probes, transport
    def eps(self) -> dict:
        from investos.backfill.endpoints import load_endpoints
        return load_endpoints(self.table_path)

    def ep(self, eid: str):
        return self.eps()[eid]

    def write_probes(self, ids=None, **over) -> None:
        """probe.json with a confirmed probe for each endpoint; per-endpoint overrides via over[eid] = {...}."""
        eps = self.eps()
        out = {}
        for eid, ep in eps.items():
            if ids is not None and eid not in ids:
                continue
            o = dict(over.get("*", {}))
            o.update(over.get(eid, {}))
            o.setdefault("probed_at", ts_text(self.now))
            out[eid] = probe_entry(ep, **o)
        p = Path(self.cfg.zones["ledger"]) / "backfill" / "probe.json"
        p.parent.mkdir(parents=True, exist_ok=True)
        old = json.loads(p.read_text())["endpoints"] if p.exists() else {}
        old.update(out)
        p.write_text(json.dumps({"schema": 1, "endpoints": old}, sort_keys=True, separators=(",", ":")) + "\n")

    def probe(self, eid: str):
        from investos.backfill.probe import load_probe
        return load_probe(self.cfg, self.ep(eid))

    def tr(self, **kw):
        if self._tr is None or kw:
            from investos.backfill.runner import make_transport
            args = dict(api_origin=IN, driver=self.driver, reaper=self.reaper, clock=self.clock,
                        monotonic=self.mono, wall_clock=self.wall, sleeper=self.sleeper)
            args.update(kw)
            self._tr = make_transport(self.cfg, **args)
        return self._tr

    def fresh(self):
        """A new transport object (a new Chrome on first use): what a new process would have."""
        if self._tr is not None:
            self._tr.close()
        self._tr = None
        return self.tr()

    def assert_counter(self, night: str = "2026-11-29") -> None:
        assert self.counter(night) == self.driver.n_requests(), (self.counter(night), self.driver.n_requests())

    # ---- runs
    def run(self, **kw):
        from investos.backfill.runner import run_backfill
        args = dict(browser=self.tr(), manifest=self.store, clock=self.clock, sleeper=self.sleeper,
                    notifier=self.notifier, monotonic=self.mono, wall_clock=self.wall, hostname=self.hostname,
                    endpoints=self.eps())
        args.update(kw)
        return run_backfill(self.cfg, **args)

    def probe_run(self, **kw):
        from investos.backfill.runner import run_probe
        args = dict(browser=self.tr(), manifest=self.store, clock=self.clock, sleeper=self.sleeper,
                    notifier=self.notifier, monotonic=self.mono, wall_clock=self.wall, hostname=self.hostname,
                    endpoints=self.eps())
        args.update(kw)
        return run_probe(self.cfg, **args)

    def direct_probe(self, eid: str, *, browser=None, report=None, **kw):
        """probe_endpoint through the real transport (default) or a given browser."""
        from investos.backfill.probe import probe_endpoint
        rep = report if report is not None else []
        args = dict(clock=self.clock, now_ist=self.now.astimezone(IST).date(), store=self.store, report=rep)
        args.update(kw)
        return probe_endpoint(self.ep(eid), browser if browser is not None else self.tr(), **args)

    def next_night(self, n: int = 1) -> None:
        self.at(NIGHT1 + timedelta(days=n))

    def cli(self, *argv):
        from investos.cli import main
        import io
        import contextlib
        buf, err = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(buf), contextlib.redirect_stderr(err):
            code = main(list(argv))
        return code, buf.getvalue(), err.getvalue()

    # ---- ledger and manifest inspection
    def wl(self, eid: str):
        from investos.backfill.ledger import WindowLedger
        return WindowLedger(self.cfg, eid, clock=self.clock)

    def ledger_path(self, eid: str) -> Path:
        return self.ledger_dir / "backfill" / f"{eid}.windows.jsonl"

    @property
    def ledger_dir(self) -> Path:
        return Path(self.cfg.zones["ledger"])

    def lines(self, eid: str) -> list[dict]:
        p = self.ledger_path(eid)
        return [json.loads(l) for l in p.read_text().splitlines()] if p.exists() else []

    def events(self, eid: str, event: str | None = None) -> list[dict]:
        return [l for l in self.lines(eid) if event is None or l["event"] == event]

    def states(self, eid: str) -> dict:
        """window logical_key -> event (latest line wins; audit_shrink lines ignored)."""
        out = {}
        for l in self.lines(eid):
            if l["event"] == "audit_shrink":
                continue
            out[l["logical_key"] or key_of(l)] = l["event"]
        return out

    def mkeys(self, eid: str) -> list[str]:
        return sorted(e.logical_key for e in self.store.entries(f"backfill_{eid}"))

    def counter(self, night: str) -> int:
        p = self.ledger_dir / ".state" / "backfill" / f"night.{night}.count"
        return int(p.read_text()) if p.exists() else 0

    def put_lines(self, eid: str, *lines: dict) -> None:
        p = self.ledger_path(eid)
        p.parent.mkdir(parents=True, exist_ok=True)
        with p.open("ab") as fh:
            for l in lines:
                fh.write((json.dumps(l, sort_keys=True, separators=(",", ":")) + "\n").encode())

    def block_hosts(self) -> dict:
        out = {}
        for h in ("www.nseindia.com", "www.nseindia.in"):
            out[h] = self.host_events(h)
        return out


def key_of(l: dict) -> str:
    base = f"{l['endpoint']}/{l['start']}_{l['end']}"
    return f"{base}/{l['part']}" if l["part"] else base


def ledger_line(eid="announcements", start="2026-11-20", end="2026-11-20", event="fetched", *, part="",
                ts="2026-11-29T01:00:00.000000Z", rows=3, sha="a" * 64, **over) -> dict:
    t = datetime.strptime(ts, "%Y-%m-%dT%H:%M:%S.%fZ").replace(tzinfo=UTC)
    base = f"{eid}/{start}_{end}"
    d = {"schema": 1, "ts": ts, "date": t.astimezone(IST).date().isoformat(), "endpoint": eid, "start": start,
         "end": end, "part": part, "event": event, "rows": rows if event in ("fetched", "empty") else None,
         "sha256": sha if event in ("fetched", "empty") else "",
         "logical_key": (f"{base}/{part}" if part else base) if event in ("fetched", "empty") else "",
         "run_id": "r1", "probe_hash": "", "reason": ""}
    if event == "empty":
        d["rows"] = 0
    d.update(over)
    return d


def day(s: str) -> date:
    return date.fromisoformat(s)


def window_urls(env: BfEnv, eid: str) -> list[str]:
    return [u for u in env.driver.fetched if u.startswith(f"{IN}/api/{eid}?")]


def utc_text(s: str) -> str:
    return s


def live_windows(e: BfEnv, eid: str) -> list[tuple]:
    """(start, end) of the live done windows of an endpoint: done, not superseded/split, not audit evidence."""
    st = {}
    for l in e.lines(eid):
        if l["event"] == "audit_shrink":
            continue
        st[(l["start"], l["end"], l["part"])] = l["event"]
    live = []
    for (s, t, part), ev in st.items():
        if ev not in ("fetched", "empty"):
            continue
        if part and st.get((s, t, part[:-1])) in ("fetched", "empty"):
            continue                                   # audit evidence of a parent that stays
        live.append((day(s), day(t)))
    return sorted(live)


def tiles(ws) -> bool:
    return all(a[1] + timedelta(days=1) == b[0] for a, b in zip(ws, ws[1:]))


def one(e: BfEnv, eid="announcements", earliest="2026-11-19", ids=None, over=None, probe=None, **kw):
    """Probe artifacts for one endpoint (windows earliest..2026-11-28 at the first night)."""
    e.write_probes([eid], **{eid: {"earliest_date": earliest, **(probe or {})}})
    return e
