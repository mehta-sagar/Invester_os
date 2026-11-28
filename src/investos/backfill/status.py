"""`investos backfill status` (spec collect_backfill R19): one line per endpoint, read from the ledger, the probe file,
the plan inputs and the night counters only. No network call, no browser."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from math import ceil
from typing import Mapping
from zoneinfo import ZoneInfo

from investos.acquire.access import AccessState, AccessStateError
from investos.backfill.endpoints import NSE_HOSTS, Endpoint
from investos.backfill.ledger import WindowLedger
from investos.backfill.probe import PROBE_MAX_AGE_DAYS, load_probe, probe_age_days
from investos.backfill.runner import make_plan, night_budget, read_counter, state_dir, survey
from investos.core.config import Config

__all__ = ["build_status", "format_status"]

IST = ZoneInfo("Asia/Kolkata")
SECONDS_PER_REQUEST = 2.5


def _ist_day(ts: str) -> str:
    return datetime.strptime(ts, "%Y-%m-%dT%H:%M:%S.%fZ").replace(tzinfo=timezone.utc).astimezone(IST).date().isoformat()


def _total_requests(cfg: Config) -> int:
    d = state_dir(cfg)
    return sum(read_counter(p) for p in d.glob("night.*.count")) if d.is_dir() else 0


def _probe_label(probe, now: datetime) -> str:
    if probe is None:
        return "to_probe"
    if probe.status == "confirmed" and probe_age_days(probe, now) > PROBE_MAX_AGE_DAYS:
        return "stale"
    return probe.status


def _blocked_until(cfg: Config, now: datetime) -> str | None:
    acc = AccessState(cfg, "backfill_probe", clock=lambda: now)
    try:
        if any(acc.status(h).blocked_today for h in sorted(NSE_HOSTS)):
            return (now.astimezone(IST).date() + timedelta(days=1)).isoformat()
    except AccessStateError:
        return None
    return None


def build_status(cfg: Config, endpoints: Mapping[str, Endpoint], now: datetime, *, calendar=None,
                 symbols=None) -> dict:
    ist = now.astimezone(IST)
    night = ist.date().isoformat()
    budget = night_budget(cfg, night)
    blocked = _blocked_until(cfg, now)
    out: dict[str, dict] = {}
    pending: dict[str, int] = {}
    for eid, ep in endpoints.items():
        probe = load_probe(cfg, ep)
        ledger = WindowLedger(cfg, eid, clock=lambda: now)
        try:
            plan = make_plan(cfg, ep, probe, ledger, ist.date(), calendar=calendar, symbols=symbols,
                             write=False) if probe is not None else []
        except ValueError:
            plan = []
        sv = survey(plan, ledger, night)
        pending[eid] = len(sv.pending)
        out[eid] = {"probe": _probe_label(probe, now), "done": sv.done, "total": sv.total, "rows": sv.rows,
                    "splits": sv.splits, "unsplittable": sv.unsplittable, "failed": sv.failed,
                    "last_error": None if sv.last_error is None else
                    {"date": _ist_day(sv.last_error[0]), "ts": sv.last_error[0], "reason": sv.last_error[1]},
                    "pending": len(sv.pending)}
    total_requests = _total_requests(cfg)
    done_all = sum(v["done"] for v in out.values())
    avg = total_requests / done_all if total_requests and done_all else None
    for eid, v in out.items():
        if blocked is not None:
            v["eta"] = f"blocked until {blocked}"
        elif not v["pending"]:
            v["eta"] = "done"
        elif avg is None:
            v["eta"] = "unknown"
        else:
            need = v["pending"] * avg
            nights = max(1, ceil(need / budget))
            v["eta"] = f"{nights} night{'s' if nights != 1 else ''} (~{need * SECONDS_PER_REQUEST / 3600:.1f} h)"
    opens = ist.date() if ist.strftime("%H:%M") < cfg.backfill.window_start else ist.date() + timedelta(days=1)
    return {"night": night, "endpoints": out, "windows_done": done_all,
            "windows_total": sum(v["total"] for v in out.values()), "rows": sum(v["rows"] for v in out.values()),
            "night_counter": read_counter(state_dir(cfg) / f"night.{night}.count"), "night_budget": budget,
            "avg_requests_per_window": avg, "next_window": f"{opens.isoformat()} {cfg.backfill.window_start} IST"}


def format_status(st: dict) -> str:
    lines = []
    for eid, v in st["endpoints"].items():
        err = "-" if v["last_error"] is None else f"{v['last_error']['date']} {v['last_error']['reason']}"
        lines.append(f"{eid:<18} {v['probe']:<12} {v['done']}/{v['total']} windows  rows {v['rows']}  "
                     f"splits {v['splits']}  unsplittable {v['unsplittable']}  failed {v['failed']}  "
                     f"last error {err}  ETA {v['eta']}")
    lines.append(f"sum: {st['windows_done']}/{st['windows_total']} windows, {st['rows']} rows; night {st['night']} "
                 f"counter {st['night_counter']}/{st['night_budget']}; next window {st['next_window']}")
    return "\n".join(lines) + "\n"
