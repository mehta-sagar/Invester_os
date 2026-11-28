"""Check `rss`: feed table, manifest keys, gap ledger, freshness, waived adapters, plus its planted failure
(spec collect_rss R20, R21). Read-only; the clock is injectable through the module-level ``_clock``."""
from __future__ import annotations

import re
from datetime import datetime, timedelta, timezone
from pathlib import Path

from investos.acquire.manifest import ManifestError, ManifestStore
from investos.acquire.registry import waived_adapters
from investos.acquire.rss import (KEY_RE, CalendarView, FeedTableError, GapLedgerError, RssFeedCollector,
                                  RssNseFeedCollector, collector_class_for, feed_table_path, gaps_path_of,
                                  judge_feed, latest_due_slot, load_feeds, read_gaps, read_state, slot_start,
                                  state_dir_of)
from investos.core.config import Config

from .registry import CheckContext, CheckResult, check, plant_for

_MAX_MSG = 190


def _clock() -> datetime:
    return datetime.now(timezone.utc)


# planted run: the one-feed table lives in the temp `reports` zone, whose path keys this map
_PLANT_TABLES: dict[Path, Path] = {}


def _table_path(cfg: Config) -> Path:
    return _PLANT_TABLES.get(Path(cfg.zones["reports"]), feed_table_path(cfg))


def _key_problems(cfg: Config, feeds) -> list[str]:
    """Keys of both sources (``rss`` honest, ``rss_nse`` real browser); a feed has exactly one source, by routing."""
    expected = {f.feed_id: collector_class_for(f).source for f in feeds}
    out: list[str] = []
    for source in sorted({RssFeedCollector.source, RssNseFeedCollector.source}):
        try:
            entries = ManifestStore.from_config(cfg).entries(source)
        except (ManifestError, OSError) as e:
            out.append(f"{source} manifest unreadable: {e}")
            continue
        seen: set[str] = set()
        for e in entries:
            key = e.logical_key
            m = KEY_RE.fullmatch(key)
            if m is None:
                out.append(f"malformed key {key!r}: expected <feed_id>/<YYYYMMDDTHHMM>")
                continue
            try:
                slot_start(m.group(2))
            except ValueError:
                out.append(f"malformed key {key!r}: not a real slot time")
                continue
            want = expected.get(m.group(1))
            if want is None:
                out.append(f"unknown feed {m.group(1)!r} in key {key!r}")
            elif want != source:
                out.append(f"ghost feed {m.group(1)!r}: key {key!r} sits under source {source}, routing says {want}")
            if key in seen:
                out.append(f"duplicate key {key!r}")
            seen.add(key)
    return out


@check("rss")
def check_rss(ctx: CheckContext) -> CheckResult:
    cfg = ctx.cfg
    now = _clock()
    table = _table_path(cfg)
    if not table.exists():
        return CheckResult(True, "no config/rss_feeds.csv; nothing to check",
                           ("WARNING config/rss_feeds.csv (rss_feeds table) is missing, no feed is polled",))
    try:
        feeds = load_feeds(table)
    except FeedTableError as e:
        return CheckResult(False, str(e)[:_MAX_MSG], (str(e),))
    fails: list[str] = []
    warns: list[str] = []
    alarms: list[str] = []
    try:
        gaps = read_gaps(gaps_path_of(cfg))
    except GapLedgerError as e:
        gaps = []
        fails.append(f"gap ledger invalid: {e}")
    fails += _key_problems(cfg, feeds)
    gap_count: dict[str, int] = {}
    for g in gaps:
        gap_count[g["feed_id"]] = gap_count.get(g["feed_id"], 0) + 1
    calendar = CalendarView(cfg)
    enabled = [f for f in feeds if f.enabled]
    for f in feeds:
        if f.status == "to_confirm":
            warns.append(f"WARNING {f.feed_id}: status to_confirm, not yet confirmed by a probe")
        if gap_count.get(f.feed_id):
            warns.append(f"WARNING {f.feed_id}: {gap_count[f.feed_id]} open gap event(s) in rss/gaps.jsonl")
    for f in enabled:
        state = read_state(state_dir_of(cfg) / f"{f.feed_id}.json", f.feed_id)
        v = judge_feed(f, state, now, calendar if f.stale_scope == "trading_hours" else None)
        if state is None:
            warns.append(f"WARNING {f.feed_id}: unknown freshness (no readable state)")
        elif v.unknown and not v.alarms:
            warns.append(f"WARNING {f.feed_id}: unknown content freshness (no item date and no change seen yet)")
        warns += [f"WARNING {w}" for w in v.warnings]
        for kind, detail in v.alarms:
            alarms.append(f"{f.feed_id} {kind}")
            fails.append(f"ALARM {f.feed_id} {kind}: {detail}")
    for source, reason in waived_adapters():
        warns.append(f"WARNING waived date check: {source}: {reason}")
    counts = f"{len(feeds)} feeds, {len(enabled)} enabled, {len(alarms)} alarms, {len(warns)} warnings"
    details = tuple(fails + warns)
    if fails:
        head = "; ".join(alarms) if alarms else fails[0]
        return CheckResult(False, f"{counts}: {head}"[:_MAX_MSG], details)
    return CheckResult(True, counts, details)


# --------------------------------------------------------------------------- plant
_PLANT_FEED = ("feed_id,url,kind,publisher,enabled,stale_after_min,stale_scope,status,note\n"
               "plant_feed,https://plant.example.org/feed.xml,news,Plant,yes,5760,always,confirmed,\n")


def _fresh_state(feed_id: str, now: datetime) -> dict:
    from investos.acquire.rss import _blank_state, _fmt_ts
    st = _blank_state(feed_id)
    st.update(last_attempt_at=_fmt_ts(now), last_ok_slot=latest_due_slot(now), last_ok_at=_fmt_ts(now),
              last_change_at=_fmt_ts(now), newest_pub_ist=now.astimezone(timezone(timedelta(hours=5, minutes=30)))
              .isoformat())
    return st


def _setup(ctx: CheckContext) -> None:
    """A self-contained healthy copy: one feed, one fresh state, no gaps, whatever the real data holds."""
    from investos.acquire.rss import _write_state
    reports = Path(ctx.cfg.zones["reports"])
    d = reports / "rss_plant"
    d.mkdir(parents=True, exist_ok=True)
    (d / "rss_feeds.csv").write_text(_PLANT_FEED, encoding="utf-8")
    _PLANT_TABLES[reports] = d / "rss_feeds.csv"
    sd = state_dir_of(ctx.cfg)
    if sd.is_dir():
        for p in sd.iterdir():
            if p.is_file():
                p.unlink()
    gaps = gaps_path_of(ctx.cfg)
    if gaps.exists():
        gaps.unlink()
    _write_state(sd / "plant_feed.json", _fresh_state("plant_feed", _clock()))


@plant_for("rss", setup=_setup, zones={"reports", "ledger"})
def _plant(ctx: CheckContext) -> None:
    """A poll miss: the feed's last ok slot is two days old."""
    from investos.acquire.rss import _fmt_ts, _write_state, slot_of
    path = state_dir_of(ctx.cfg) / "plant_feed.json"
    st = read_state(path, "plant_feed")
    old = _clock() - timedelta(days=2)
    st["last_ok_slot"] = slot_of(old)
    st["last_ok_at"] = _fmt_ts(old)
    _write_state(path, st)
