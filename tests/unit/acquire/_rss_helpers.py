"""Helpers for S1-T6 (RSS poller, item extractor, `rss` check) tests.

investos imports happen inside functions only, so every test fails individually (not at collection) while the
new modules (investos.acquire.rss, investos.acquire.registry, investos.parse.rss_items, investos.check.rss_check)
are absent. Real files in tmp dirs, fake clocks, no network, no real sleeps.
"""
from __future__ import annotations

import csv
import hashlib
import json
import shutil
from datetime import datetime, timedelta, timezone
from pathlib import Path
from xml.sax.saxutils import escape
from zoneinfo import ZoneInfo

from _collector_helpers import FakeTransport, resp  # noqa: F401
from _s1_helpers import CONTACT, HOSTNAME, FakeNotifier, S1Env  # noqa: F401

IST = ZoneInfo("Asia/Kolkata")
UTC = timezone.utc
REPO = Path(__file__).resolve().parents[3]
SHIPPED_CAL = REPO / "config" / "calendar"
SHIPPED_FEEDS = REPO / "config" / "rss_feeds.csv"
EXAMPLE_TOML = REPO / "config" / "investos.example.toml"
EXAMPLE_PLIST = REPO / "ops" / "launchd" / "com.investos.rss.plist.example"
SRC = REPO / "src" / "investos"
RSS_MODULES = [SRC / "acquire" / "rss.py", SRC / "acquire" / "registry.py", SRC / "parse" / "rss_items.py",
               SRC / "check" / "rss_check.py"]

FEED_HEADER = "feed_id,url,kind,publisher,enabled,stale_after_min,stale_scope,status,note"
FEED_COLS = FEED_HEADER.split(",")
NSE = "https://rss.example.test/content/RSS/"   # an honest-mode test host (S1-T7: real NSE-host feeds now route to the browser)
NSE_REAL = "https://nsearchives.nseindia.com/content/RSS/"
URL_ANN = NSE + "Online_announcements.xml"
URL_INS = NSE + "InsiderTrading.xml"
URL_RBI = "https://www.rbi.org.in/pressreleases_rss.xml"
URL_SEBI = "https://www.sebi.gov.in/sebirss.xml"
SLOT_RE = r"\d{8}T\d{4}"
TS_RE = r"^\d{4}-\d\d-\d\dT\d\d:\d\d:\d\d\.\d{6}Z$"


# ------------------------------------------------------------------ time
def ist(y, mo, d, h=0, mi=0, s=0) -> datetime:
    """An IST wall time as an aware UTC instant."""
    return datetime(y, mo, d, h, mi, s, tzinfo=IST).astimezone(UTC)


def ist_dt(y, mo, d, h=0, mi=0, s=0) -> datetime:
    return datetime(y, mo, d, h, mi, s, tzinfo=IST)


def fmt_ts(dt: datetime) -> str:
    return dt.astimezone(UTC).strftime("%Y-%m-%dT%H:%M:%S.%fZ")


def slot_dt(slot: str) -> datetime:
    """'20261127T0900' -> aware IST datetime."""
    return datetime(int(slot[:4]), int(slot[4:6]), int(slot[6:8]), int(slot[9:11]), int(slot[11:13]), tzinfo=IST)


def slot_str(dt: datetime) -> str:
    return dt.astimezone(IST).strftime("%Y%m%dT%H%M")


def scheduled_between(first: str, last: str) -> list[str]:
    """All scheduled slots (spec R9) from first to last inclusive, computed independently of the code under test."""
    out, t = [], slot_dt(first)
    end = slot_dt(last)
    while t <= end:
        hhmm = (t.hour, t.minute)
        if t.hour >= 7 or hhmm in ((1, 0), (3, 0), (5, 0)):
            out.append(slot_str(t))
        t += timedelta(minutes=15)
    return out


def sha(b: bytes) -> str:
    return hashlib.sha256(b).hexdigest()


def canon(obj) -> str:
    return json.dumps(obj, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


# ------------------------------------------------------------------ feed tables
def frow(feed_id="f1", url=None, kind="corporate", publisher="NSE", enabled="yes", stale="90",
         scope="trading_hours", status="confirmed", note="") -> dict:
    return {"feed_id": feed_id, "url": url or (NSE + f"{feed_id}.xml"), "kind": kind, "publisher": publisher,
            "enabled": enabled, "stale_after_min": stale, "stale_scope": scope, "status": status, "note": note}


def write_feeds(path: Path, rows: list[dict], header: str = FEED_HEADER) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    cols = header.split(",")
    with path.open("w", newline="", encoding="utf-8") as fh:
        fh.write(header + "\n")
        w = csv.writer(fh, lineterminator="\n")
        for r in rows:
            w.writerow([r.get(c, "") for c in cols])
    return path


def feed_row(**kw):
    """A FeedRow object."""
    from investos.acquire.rss import FeedRow
    d = frow(**{k: v for k, v in kw.items()})
    return FeedRow(feed_id=d["feed_id"], url=d["url"], kind=d["kind"], publisher=d["publisher"],
                   enabled=str(d["enabled"]).lower() in ("yes", "true"), stale_after_min=int(d["stale_after_min"]),
                   stale_scope=d["stale_scope"], status=d["status"], note=d["note"])


def default_rows() -> list[dict]:
    return [frow("f_nse", URL_ANN, "corporate", "NSE", stale="90", scope="trading_hours", status="to_confirm"),
            frow("f_news", URL_RBI, "news", "RBI", stale="5760", scope="always", status="confirmed")]


# ------------------------------------------------------------------ RSS bodies
def item(guid=None, link=None, title=None, pub=None, desc=None, dc_date=None, cdata=False) -> dict:
    return {"guid": guid, "link": link, "title": title, "pub": pub, "desc": desc, "dc_date": dc_date,
            "cdata": cdata}


def numbered(i: int, pub: str | None = "27-Nov-2026 10:00:00") -> dict:
    return item(guid=f"g{i}", link=f"https://nsearchives.nseindia.com/c/{i}.pdf", title=f"Item {i}", pub=pub)


def make_rss(items=(), *, last_build: str | None = None, channel_extra: str = "", pad: int = 0) -> bytes:
    out = ['<?xml version="1.0" encoding="UTF-8"?>',
           '<rss version="2.0" xmlns:dc="http://purl.org/dc/elements/1.1/"><channel>',
           '<title>Feed</title><link>https://example.test/</link><description>d</description>']
    if last_build:
        out.append(f"<lastBuildDate>{escape(last_build)}</lastBuildDate>")
    out.append(channel_extra)
    for it in items:
        out.append("<item>")
        if it.get("title") is not None:
            out.append(f"<title>{escape(it['title'])}</title>")
        if it.get("link") is not None:
            out.append(f"<link>{escape(it['link'])}</link>")
        if it.get("guid") is not None:
            out.append(f"<guid>{escape(it['guid'])}</guid>")
        if it.get("desc") is not None:
            if it.get("cdata"):
                out.append(f"<description><![CDATA[{it['desc']}]]></description>")
            else:
                out.append(f"<description>{escape(it['desc'])}</description>")
        if it.get("pub") is not None:
            out.append(f"<pubDate>{escape(it['pub'])}</pubDate>")
        if it.get("dc_date") is not None:
            out.append(f"<dc:date>{escape(it['dc_date'])}</dc:date>")
        out.append("</item>")
    out.append("</channel></rss>")
    body = "".join(out)
    if pad:
        body = body.replace("</channel>", "<!--" + "x" * pad + "--></channel>")
    return body.encode("utf-8")


def body_of(*ids: int, pub: str | None = "27-Nov-2026 10:00:00") -> bytes:
    return make_rss([numbered(i, pub) for i in ids])


def exact_size_body(size: int) -> bytes:
    """A valid RSS body of exactly `size` bytes (padding in an XML comment)."""
    pad = size - len(make_rss([numbered(1)], pad=1)) + 1
    b = make_rss([numbered(1)], pad=pad)
    assert len(b) == size, (len(b), size)
    return b


# ------------------------------------------------------------------ calendar tables (tmp repo)
def write_calendar(repo_root: Path, *, years=(2026,), trading_days=()) -> Path:
    """config/calendar tables: NSE/BSE holiday lists loaded for `years`; `trading_days` are ISO dates confirmed trading."""
    from investos.calendar.trading import SESSIONS_HEADER, YEARS_HEADER
    d = repo_root / "config" / "calendar"
    d.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(SHIPPED_CAL / "close_eras.csv", d / "close_eras.csv")
    with (d / "years.csv").open("w", newline="", encoding="utf-8") as fh:
        w = csv.writer(fh, lineterminator="\n")
        w.writerow(YEARS_HEADER)
        for y in years:
            for ex in ("NSE", "BSE"):
                w.writerow([ex, y, "fixture-list", "2026-01-02"])
    with (d / "sessions.csv").open("w", newline="", encoding="utf-8") as fh:
        w = csv.writer(fh, lineterminator="\n")
        w.writerow(SESSIONS_HEADER)
        for day in trading_days:
            for ex in ("NSE", "BSE"):
                row = {"date": day, "exchange": ex, "segment": "ALL", "status": "trading", "open_ist": "",
                       "close_ist": "", "evidence_kind": "manual", "evidence_ref": "fixture",
                       "recorded_at": "2026-11-20"}
                w.writerow([row[c] for c in SESSIONS_HEADER])
    return d


# ------------------------------------------------------------------ transport
class TrackingTransport(FakeTransport):
    """FakeTransport that records in-flight overlap and runs optional callbacks after each call."""

    def __init__(self, mono):
        super().__init__(mono)
        self.inflight = 0
        self.max_inflight = 0
        self.after: list = []                     # callables(call_index) run after each call returns

    def get(self, url, *, headers, timeout):
        self.inflight += 1
        self.max_inflight = max(self.max_inflight, self.inflight)
        try:
            return super().get(url, headers=headers, timeout=timeout)
        finally:
            self.inflight -= 1
            for fn in list(self.after):
                fn(len(self.calls))


# ------------------------------------------------------------------ environment
class RssEnv(S1Env):
    """S1Env plus: a feed table at <repo>/config/rss_feeds.csv, calendar tables, a tracking transport, notifier."""

    def __init__(self, tmp: Path, rows: list[dict] | None = None, *, hosts=None, interval=0.35, contact=CONTACT,
                 access=None, calendar="loaded", trading_days=()):
        super().__init__(tmp, hosts=hosts, interval=interval, contact=contact, access=access)
        self.rows = rows if rows is not None else default_rows()
        self.feeds_path = write_feeds(tmp / "config" / "rss_feeds.csv", self.rows)
        if calendar == "loaded":
            write_calendar(tmp, years=(2026,), trading_days=trading_days)
        elif calendar == "empty":
            write_calendar(tmp, years=(), trading_days=())
        self.transport = TrackingTransport(self.mono)
        self.man = Path(self.cfg.zones["manifests"])

    # ---- feeds
    def feeds_objs(self):
        from investos.acquire.rss import load_feeds
        return load_feeds(self.feeds_path)

    def feed(self, feed_id):
        return next(f for f in self.feeds_objs() if f.feed_id == feed_id)

    def set_rows(self, rows):
        self.rows = rows
        write_feeds(self.feeds_path, rows)

    def script(self, feed_id_or_url, *items):
        url = feed_id_or_url
        if not url.startswith("https://"):
            url = next(r["url"] for r in self.rows if r["feed_id"] == feed_id_or_url)
        self.transport.set(url, *items)

    def serve(self, feed_id, body: bytes | None = None, status=200, headers=None):
        self.script(feed_id, resp(status, body if body is not None else body_of(1), headers or {}))

    def serve_all(self, body: bytes | None = None):
        for r in self.rows:
            self.serve(r["feed_id"], body)

    # ---- run
    def cycle(self, now, **kw):
        from investos.acquire.rss import poll_cycle
        args = dict(now=now, transport=self.transport, manifest=self.store, notifier=self.notifier,
                    monotonic=self.mono, sleeper=self.sleeper, wall_clock=self.wall, hostname=lambda: HOSTNAME,
                    run_id=kw.pop("run_id", "cyc-1"))
        args.update(kw)
        return poll_cycle(self.cfg, **args)

    def collector(self, feed, slot, last_sha=None, **kw):
        from investos.acquire.rss import RssFeedCollector
        when = kw.pop("now", ist(2026, 11, 27, 9, 0))
        args = dict(transport=self.transport, manifest=self.store, cfg=self.cfg, run_id="run-1",
                    clock=lambda: when, monotonic=self.mono, sleeper=self.sleeper,
                    wall_clock=self.wall, hostname=lambda: HOSTNAME)
        args.update(kw)
        return RssFeedCollector(feed, slot, last_stored_sha=last_sha, **args)

    def run_collector(self, feed, slot, last_sha=None, now=None):
        from investos.acquire.base import RunRange
        c = self.collector(feed, slot, last_sha, now=now or slot_dt(slot).astimezone(UTC))
        d = slot_dt(slot).date()
        return c.run(RunRange(d, d))

    # ---- files
    @property
    def state_dir(self) -> Path:
        return self.ledger / ".state" / "rss"

    def state_path(self, feed_id) -> Path:
        return self.state_dir / f"{feed_id}.json"

    def state(self, feed_id) -> dict:
        return json.loads(self.state_path(feed_id).read_text())

    def write_state(self, feed_id, *, raw: str | None = None, **over) -> Path:
        p = self.state_path(feed_id)
        p.parent.mkdir(parents=True, exist_ok=True)
        if raw is not None:
            p.write_text(raw)
            return p
        obj = {"schema": 1, "feed_id": feed_id, "last_attempt_at": None, "last_ok_slot": None, "last_ok_at": None,
               "last_stored_sha": None, "last_content_digest": None, "last_change_at": None, "newest_pub_ist": None,
               "consecutive_failures": 0, "alerted": {}}
        obj.update(over)
        p.write_text(canon(obj) + "\n")
        return p

    def fresh_state(self, feed_id, now, *, newest=None, **over):
        """A healthy state as of `now`: last ok in the latest scheduled slot at least 10 minutes old."""
        t = now.astimezone(IST) - timedelta(minutes=10)
        t = t.replace(minute=t.minute - t.minute % 15, second=0, microsecond=0)
        while t.hour < 7 and (t.hour, t.minute) not in ((1, 0), (3, 0), (5, 0)):
            t -= timedelta(minutes=15)
        vals = dict(last_attempt_at=fmt_ts(now), last_ok_slot=slot_str(t), last_ok_at=fmt_ts(t.astimezone(UTC)),
                    last_change_at=fmt_ts(now - timedelta(hours=1)),
                    newest_pub_ist=(newest or (now.astimezone(IST) - timedelta(hours=2))).isoformat())
        vals.update(over)
        return self.write_state(feed_id, **vals)

    @property
    def gaps_path(self) -> Path:
        return self.ledger / "rss" / "gaps.jsonl"

    def gap_lines(self) -> list[dict]:
        p = self.gaps_path
        return [json.loads(l) for l in p.read_text().splitlines()] if p.exists() else []

    def rss_manifest(self) -> list[dict]:
        p = self.man / "rss.jsonl"
        return [json.loads(l) for l in p.read_text().splitlines()] if p.exists() else []

    def sidecar_for(self, feed_id, date_s="2026-11-27") -> dict:
        p = self.man / "rss.runs" / feed_id / f"{date_s}_{date_s}.json"
        return json.loads(p.read_text())

    def raw_count(self) -> int:
        return len([p for p in Path(self.cfg.zones["raw"]).rglob("*") if p.is_file()])

    def store_row(self, feed_id, slot, body, fetched_at):
        return self.store.store("rss", body, url=next(r["url"] for r in self.rows if r["feed_id"] == feed_id),
                                source_version="rss-v1", logical_key=f"{feed_id}/{slot}", run_id="seed",
                                fetched_at=fetched_at, access_mode="honest", request_headers={},
                                final_url="https://example.test/", fetch_host="h")


def tree(*roots: Path) -> dict:
    """path -> bytes (files) for every file under roots; used to prove 'nothing was written'."""
    out = {}
    for root in roots:
        if not Path(root).exists():
            continue
        for p in sorted(Path(root).rglob("*")):
            if p.is_file():
                out[str(p)] = p.read_bytes()
            else:
                out[str(p)] = None
    return out


def env_tree(e: RssEnv) -> dict:
    return tree(e.ledger, e.man, Path(e.cfg.zones["raw"]))


def log_text(records) -> str:
    return json.dumps(records, ensure_ascii=False)


def fields_of(records, event, level=None):
    return [r["fields"] for r in records if r["event"] == event and (level is None or r["level"] == level)]


# ------------------------------------------------------------------ extractor rows
def entry(feed_id, slot, body, fetched_at, run_id="r1"):
    from investos.acquire.manifest import ManifestEntry
    return ManifestEntry(sha256=sha(body), size=len(body), url=NSE + f"{feed_id}.xml", fetched_at=fetched_at,
                         source="rss", source_version="rss-v1", logical_key=f"{feed_id}/{slot}", run_id=run_id)


def row(feed_id, slot, body, fetched_at):
    return (entry(feed_id, slot, body, fetched_at), body)


# ------------------------------------------------------------------ check helpers
def set_check_clock(monkeypatch, now: datetime):
    """The check reads an injectable module-level clock (spec R20). Accept the first of a few plausible names."""
    import investos.check.rss_check as m
    for name in ("_clock", "clock", "_now", "now"):
        if callable(getattr(m, name, None)):
            monkeypatch.setattr(m, name, lambda: now)
            return name
    raise AssertionError("investos.check.rss_check has no injectable module-level clock "
                         "(expected a callable named _clock, clock, _now or now)")


def run_check(e: RssEnv, argv=("rss",)):
    """Run `investos check <argv>` against e.cfg through the default registry. -> (exit, stdout, report_text)."""
    import io
    from investos.check.cli import main
    buf = io.StringIO()
    code = main(list(argv), cfg=e.cfg, stdout=buf)
    rep = ""
    d = Path(e.cfg.zones["reports"]) / "checks"
    for p in sorted(d.glob("*.json")) if d.is_dir() else []:
        for r in json.loads(p.read_text()).get("results", []):
            rep += "\n".join([r.get("name", ""), r.get("message", "")] + list(r.get("details", []))) + "\n"
        p.unlink()
    return code, buf.getvalue(), rep


def warn_lines(text):
    return [l for l in text.splitlines() if "WARNING" in l]


def has_count(msg: str, word: str, n: int) -> bool:
    import re
    return bool(re.search(rf"\b{n}\W{{0,3}}{word}|{word}\W{{0,3}}{n}\b", msg, re.I))


# ------------------------------------------------------------------ spawn worker (concurrent gap appends)
def cycle_worker(spec: dict, out) -> None:
    from datetime import datetime as _dt
    from investos.acquire.base import Response
    from investos.acquire.manifest import ManifestStore
    from investos.acquire.rss import FeedRow, poll_cycle
    from investos.core.config import load_config
    tmp = Path(spec["tmp"])
    cfg = load_config(tmp / "config" / "investos.toml", repo_root=tmp, env={})
    store = ManifestStore(cfg.zones["raw"], cfg.zones["manifests"])
    f = spec["feed"]
    feed = FeedRow(f["feed_id"], f["url"], "corporate", "NSE", True, 90, "always", "confirmed", "")
    body = spec["body"].encode("utf-8")

    class T:
        def get(self, url, *, headers, timeout):
            return Response(200, {}, body, url)

    class N:
        def notify(self, t, m):
            pass

    spec["go"].wait(30)
    r = poll_cycle(cfg, now=_dt.fromisoformat(spec["now"]), transport=T(), manifest=store, notifier=N(),
                   force=True, feeds=[feed], run_id=spec["run_id"], hostname=lambda: "child")
    out.put(r.slot)
