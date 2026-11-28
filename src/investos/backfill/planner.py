"""The window planner and the adaptive split (spec collect_backfill R6, R7).

``plan_windows`` cuts ``[max(start_date, earliest), last completed day]`` into ordered, disjoint, gap-free inclusive
windows; ``split`` halves one window by calendar days and never trusts a window that came back at the row cap."""
from __future__ import annotations

import json
import os
import tempfile
from datetime import date, timedelta
from pathlib import Path
from typing import Iterable, Sequence

from investos.backfill.endpoints import Endpoint
from investos.backfill.ledger import WindowKey, ledger_dir
from investos.core.config import Config

__all__ = ["plan_windows", "split", "write_plan", "read_plan", "symbol_parts", "TRADING_DAY_ENDPOINTS"]

TRADING_DAY_ENDPOINTS = frozenset({"bulk_deals"})
_ONE_DAY = timedelta(days=1)
_FILL = 0.8                                   # expected rows stay at or under 80 percent of the cap


def split(key: WindowKey) -> tuple[WindowKey, WindowKey]:
    """Halve by calendar days, the first half gets the extra day; parts get `a` and `b` appended (R7)."""
    if key.days < 2:
        raise ValueError("a one-day window cannot be split")
    first_days = (key.days + 1) // 2
    mid = key.start + timedelta(days=first_days - 1)
    return (WindowKey(key.endpoint_id, key.start, mid, key.part + "a"),
            WindowKey(key.endpoint_id, mid + _ONE_DAY, key.end, key.part + "b"))


def symbol_parts(symbols: Sequence[str]) -> dict[str, str]:
    """part -> symbol: the part letters name the index into the symbol list (parts are [a-z]* in the ledger)."""
    out: dict[str, str] = {}
    for i, sym in enumerate(symbols):
        n, part = i, ""
        while True:
            part = chr(97 + n % 26) + part
            n = n // 26 - 1
            if n < 0:
                break
        out[part] = sym
    return out


def _width(ep: Endpoint, probe, max_window_days: int | None) -> int:
    w = int(probe.max_window_days)
    if max_window_days is not None:
        w = min(w, int(max_window_days))
    cap, rpd = probe.row_cap, getattr(probe, "rows_per_day", None)
    if cap and rpd and rpd > 0:
        w = min(w, max(1, int(_FILL * cap / rpd + 1e-9)))
    return max(1, w)


def _runs(a: date, b: date, calendar, trading_only: bool) -> list[tuple[date, date]]:
    """The stretches of a..b that may carry a window: all of it, or between calendar-closed days."""
    if not trading_only:
        return [(a, b)]
    out: list[tuple[date, date]] = []
    cur: date | None = None
    d = a
    while d <= b:
        if calendar.day_status(d) == "closed":
            if cur is not None:
                out.append((cur, d - _ONE_DAY))
                cur = None
        elif cur is None:
            cur = d
        d += _ONE_DAY
    if cur is not None:
        out.append((cur, b))
    return out


def plan_windows(ep: Endpoint, probe, end: date, *, calendar=None, done: Iterable[WindowKey] = (),
                 max_window_days: int | None = None, symbols: Sequence[str] | None = None) -> list[WindowKey]:
    """``end`` is today: the plan stops at the last completed IST day (end - 1). Windows in ``done`` keep their borders;
    only the uncovered dates get new windows (R6)."""
    first = max(ep.start_date, probe.earliest_date)
    last = end - _ONE_DAY
    if first > last:
        return []
    if getattr(probe, "mode", "") == "per_company":
        if not symbols:
            raise ValueError("per_company mode needs the symbol list")
        return [WindowKey(ep.endpoint_id, first, last, part) for part in symbol_parts(symbols)]
    w = _width(ep, probe, max_window_days)
    trading_only = calendar is not None and ep.endpoint_id in TRADING_DAY_ENDPOINTS
    out: list[WindowKey] = []

    def fill(a: date, b: date) -> None:
        for ra, rb in _runs(a, b, calendar, trading_only):
            s = ra
            while s <= rb:
                e = min(s + timedelta(days=w - 1), rb)
                out.append(WindowKey(ep.endpoint_id, s, e))
                s = e + _ONE_DAY

    cursor = first
    for d in sorted((k for k in done if k.part == "" and k.endpoint_id == ep.endpoint_id and k.end >= first
                     and k.start <= last), key=lambda k: (k.start, k.end)):
        if d.end < cursor:
            continue
        if d.start > cursor:
            fill(cursor, d.start - _ONE_DAY)
        out.append(d)
        cursor = max(cursor, d.end + _ONE_DAY)
    if cursor <= last:
        fill(cursor, last)
    return out


# --------------------------------------------------------------------------- the plan file (tracked)
def _plan_path(cfg: Config, endpoint_id: str) -> Path:
    return ledger_dir(cfg) / "plan" / f"{endpoint_id}.json"


def write_plan(cfg: Config, ep: Endpoint, probe, end: date, **kw) -> list[WindowKey]:
    ws = plan_windows(ep, probe, end, **kw)
    obj = {"schema": 1, "endpoint_id": ep.endpoint_id, "row_hash": ep.row_hash(),
           "inputs": {"end": end.isoformat(), "earliest_date": probe.earliest_date.isoformat(),
                      "max_window_days": probe.max_window_days, "row_cap": probe.row_cap,
                      "rows_per_day": getattr(probe, "rows_per_day", None),
                      "narrowed_to": kw.get("max_window_days")},
           "windows": [{"start": w.start.isoformat(), "end": w.end.isoformat(), "part": w.part} for w in ws]}
    text = json.dumps(obj, sort_keys=True, separators=(",", ":")) + "\n"
    path = _plan_path(cfg, ep.endpoint_id)
    if path.exists() and path.read_text(encoding="utf-8") == text:
        return ws
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(prefix=".plan-", dir=str(path.parent))
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            fh.write(text)
            fh.flush()
            os.fsync(fh.fileno())
        os.replace(tmp, path)
    except BaseException:
        try:
            os.unlink(tmp)
        except FileNotFoundError:
            pass
        raise
    return ws


def read_plan(cfg: Config, endpoint_id: str) -> list[WindowKey] | None:
    try:
        obj = json.loads(_plan_path(cfg, endpoint_id).read_text(encoding="utf-8"))
    except FileNotFoundError:
        return None
    except (OSError, ValueError) as e:
        raise ValueError(f"plan file for {endpoint_id} is unreadable: {e}") from None
    return [WindowKey(endpoint_id, date.fromisoformat(w["start"]), date.fromisoformat(w["end"]), w["part"])
            for w in obj["windows"]]
