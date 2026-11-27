"""Builds the S1-T6 sample output with fakes only (no network, no system clock in the code under test).

Run:  PYTHONPATH=src python reports/S1-T6/samples/make_sample.py
Writes into reports/S1-T6/samples/out/: sidecars, state files, gaps.jsonl, manifest rows, item rows, log.
"""
from __future__ import annotations

import io
import json
import shutil
import sys
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

from investos.acquire.base import Response
from investos.acquire.manifest import ManifestStore
from investos.acquire.rss import load_feeds, poll_cycle
from investos.core import log
from investos.core.config import load_config
from investos.parse.rss_items import extract_items

IST = ZoneInfo("Asia/Kolkata")
OUT = Path(__file__).resolve().parent / "out"
NSE, RBI = "https://nsearchives.nseindia.com/content/RSS/Online_announcements.xml", "https://www.rbi.org.in/pressreleases_rss.xml"


def ist(h, m, s=0):
    return datetime(2026, 11, 27, h, m, s, tzinfo=IST).astimezone(timezone.utc)


def rss(*items, last_build=""):
    body = ['<?xml version="1.0" encoding="UTF-8"?><rss version="2.0"><channel><title>Feed</title>']
    if last_build:
        body.append(f"<lastBuildDate>{last_build}</lastBuildDate>")
    for i, pub in items:
        body.append(f"<item><title>Item {i}</title><guid>g{i}</guid><link>https://nsearchives.nseindia.com/c/{i}.pdf</link>"
                    f"<description>&lt;b&gt;Filing {i}&lt;/b&gt;</description><pubDate>{pub}</pubDate></item>")
    body.append("</channel></rss>")
    return "".join(body).encode()


class Mono:
    t = 0.0

    def __call__(self):
        return self.t


class Transport:
    def __init__(self, mono):
        self.script, self.mono = {}, mono

    def get(self, url, *, headers, timeout):
        r = self.script[url]
        return r() if callable(r) else r


def main():
    tmp = Path(tempfile.mkdtemp(prefix="s1t6-sample-"))
    for d in ("data/raw", "data/manifests", "data/canonical", "data/ledger", "reports", "registry", "db/migrations", "config"):
        (tmp / d).mkdir(parents=True)
    (tmp / "pyproject.toml").write_text("[project]\nname='x'\n")
    (tmp / "config" / "investos.toml").write_text(
        '[general]\ntimezone = "Asia/Kolkata"\n[paths]\nraw="data/raw"\nmanifests="data/manifests"\ncanonical="data/canonical"\n'
        'ledger="data/ledger"\nreports="reports"\nregistry="registry"\n[db]\nmigrations_dir="db/migrations"\n'
        '[rate_limits]\ndefault_interval_s=0.35\n[rate_limits.hosts]\n"nsearchives.nseindia.com"=1.0\n"www.rbi.org.in"=1.0\n'
        '[collect]\ncontact="sample@example.org"\n')
    (tmp / "config" / "rss_feeds.csv").write_text(
        "feed_id,url,kind,publisher,enabled,stale_after_min,stale_scope,status,note\n"
        f"online_announcements,{NSE},corporate,NSE,yes,90,trading_hours,confirmed,\n"
        f"rbi_press,{RBI},news,RBI,yes,5760,always,confirmed,\n")
    cfg = load_config(tmp / "config" / "investos.toml", repo_root=tmp, env={})
    store = ManifestStore(cfg.zones["raw"], cfg.zones["manifests"])
    logbuf = io.StringIO()
    log.configure_logging(logbuf, run_id="sample-run")
    mono = Mono()
    tr = Transport(mono)
    sleeps = []

    def sleeper(s):
        sleeps.append(s)
        mono.t += s

    class Notifier:
        def notify(self, title, message):
            print("NOTIFY:", title, message)

    def cycle(now, run_id):
        return poll_cycle(cfg, now=now, transport=tr, manifest=store, notifier=Notifier(), run_id=run_id,
                          monotonic=mono, sleeper=sleeper, hostname=lambda: "sample-host")

    a = rss((1, "27-Nov-2026 09:00:10"), (2, "27-Nov-2026 09:00:40"), last_build="Fri, 27 Nov 2026 09:00:00 +0530")
    a_noise = rss((1, "27-Nov-2026 09:00:10"), (2, "27-Nov-2026 09:00:40"), last_build="Fri, 27 Nov 2026 09:10:00 +0530")
    b = rss((1, "27-Nov-2026 09:00:10"), (2, "27-Nov-2026 09:00:40"), (3, "27-Nov-2026 09:12:00"))
    c = rss((2, "27-Nov-2026 09:00:40"), (3, "27-Nov-2026 09:12:00"), (4, "27-Nov-2026 10:14:00"))
    rbi = rss((11, "Fri, 27 Nov 2026 08:30:00"))
    tr.script[RBI] = Response(200, {}, rbi, RBI)
    tr.script[NSE] = Response(200, {}, a, NSE)
    results = [cycle(ist(9, 0, 30), "sample-c1")]
    tr.script[NSE] = Response(200, {}, a_noise, NSE)             # only <lastBuildDate> differs: skipped, no row
    results.append(cycle(ist(9, 15, 30), "sample-c2"))
    tr.script[NSE] = Response(200, {}, b, NSE)                   # a new item: stored
    results.append(cycle(ist(9, 30, 30), "sample-c3"))
    tr.script[NSE] = Response(200, {}, c, NSE)                   # Mac asleep 09:45 to 10:00; rbi now fails
    tr.script[RBI] = Response(503, {}, b"busy", RBI)
    results.append(cycle(ist(10, 20, 30), "sample-c4"))

    if OUT.exists():
        shutil.rmtree(OUT)
    OUT.mkdir(parents=True)
    man, led = Path(cfg.zones["manifests"]), Path(cfg.zones["ledger"])
    for p in sorted((man / "rss.runs").rglob("*.json")):
        dest = OUT / "sidecars" / p.relative_to(man / "rss.runs")
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_text(json.dumps(json.loads(p.read_text()), indent=2, sort_keys=True) + "\n")
    for p in sorted((led / ".state" / "rss").glob("*.json")):
        (OUT / "state").mkdir(exist_ok=True)
        (OUT / "state" / p.name).write_text(json.dumps(json.loads(p.read_text()), indent=2, sort_keys=True) + "\n")
    (OUT / "gaps.jsonl").write_text((led / "rss" / "gaps.jsonl").read_text())
    (OUT / "manifest_rss.jsonl").write_text((man / "rss.jsonl").read_text())
    entries = [e for e in store.entries("rss") if e.logical_key.startswith("online_announcements/")]
    rows = [(e, store.read_bytes("rss", e.sha256)) for e in entries]
    with (OUT / "items.jsonl").open("w") as fh:
        for it in extract_items(rows):
            d = {k: (v.isoformat() if isinstance(v, datetime) else v) for k, v in it.__dict__.items()}
            fh.write(json.dumps(d, sort_keys=True) + "\n")
    (OUT / "cycle_results.txt").write_text("\n".join(repr(r) for r in results) + "\n")
    (OUT / "log.jsonl").write_text(logbuf.getvalue())
    print("wrote", OUT, "from", tmp)
    shutil.rmtree(tmp)


if __name__ == "__main__":
    sys.exit(main())
