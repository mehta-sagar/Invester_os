"""Check `calendar`: loads and validates the trading-calendar tables read-only, plus its planted failure
(spec trading_calendar R5, R7, R12, R13)."""
from __future__ import annotations

import shutil
from collections import Counter
from datetime import date
from pathlib import Path

from investos.calendar.trading import ERAS_HEADER, SESSIONS_HEADER, YEARS_HEADER, CalendarError, TradingCalendar

from .registry import CheckContext, CheckResult, check, plant_for

# planted run: the tables copy lives in the temp `reports` zone, whose path keys this map
_TABLE_DIRS: dict[Path, Path] = {}
_NAMES = ("close_eras.csv", "sessions.csv", "years.csv")


def _table_dir(ctx: CheckContext) -> Path:
    return _TABLE_DIRS.get(ctx.cfg.zones["reports"], Path(ctx.cfg.repo_root) / "config" / "calendar")


@check("calendar")
def check_calendar(ctx: CheckContext) -> CheckResult:
    d = _table_dir(ctx)
    if not d.exists():
        return CheckResult(True, "no config/calendar directory; nothing to check",
                           ("WARNING config/calendar is missing, the calendar tables are not loaded",))
    try:
        cal = TradingCalendar(ctx.cfg, close_eras=d / _NAMES[0], sessions=d / _NAMES[1], years=d / _NAMES[2])
    except CalendarError as e:
        return CheckResult(False, str(e)[:180], (str(e),))
    eras = cal.all_eras()
    counts = Counter(e.verified for e in eras)
    details = [f"verified counts: " + ", ".join(f"{k}={counts[k]}" for k in sorted(counts))]
    warns = []
    for e in eras:
        if e.cas_applicable == "unverified":
            warns.append(f"WARNING {e.era_id}: cas_applicable unverified, treated as applicable (latest close)")
        if e.verified == "assumed":
            warns.append(f"WARNING {e.era_id}: verified=assumed, not checked against any source")
    today = date.today()
    loaded = set()
    for ex in ("NSE", "BSE"):
        years = cal.loaded_years(ex)
        loaded.update(years)
        if today.year not in years:
            warns.append(f"WARNING {ex}: holiday list for {today.year} (today's year) is not loaded")
        elif today.month == 12 and today.year + 1 not in years:
            warns.append(f"WARNING {ex}: December {today.year} and {today.year + 1} holiday list not loaded")
    newest = max(loaded) if loaded else None
    details.append(f"newest loaded year: {newest}")
    msg = "tables OK; verified " + " ".join(f"{k}={counts[k]}" for k in sorted(counts)) \
          + f"; {len(warns)} warning(s)"
    return CheckResult(True, msg, tuple(details + warns))


_HEADERS = {
    "close_eras.csv": ",".join(ERAS_HEADER),
    "sessions.csv": ",".join(SESSIONS_HEADER),
    "years.csv": ",".join(YEARS_HEADER),
}


def _setup(ctx: CheckContext) -> None:
    src = Path(ctx.cfg.repo_root) / "config" / "calendar"
    dst = ctx.cfg.zones["reports"] / "calendar_plant"
    dst.mkdir(parents=True, exist_ok=True)
    for n in _NAMES:
        if (src / n).is_file():
            shutil.copyfile(src / n, dst / n)
        else:                                   # repo without tables: a valid empty set to plant into
            (dst / n).write_text(_HEADERS[n] + "\n", encoding="utf-8")
    _TABLE_DIRS[ctx.cfg.zones["reports"]] = dst


def _era_line(era_id: str, frm: str, to: str) -> str:
    return (f"{era_id},{frm},{to},NSE,CM,non_fo,15:30,,,no,vwap_last_30m,15:30,15:30,,,,,30,assumed")


@plant_for("calendar", setup=_setup, zones={"reports"})
def _plant(ctx: CheckContext) -> None:
    """One era gap: shorten E2-CM-cat1 by a day when the shipped rows are there, else append two
    rows with a hole between them."""
    p = _table_dir(ctx) / "close_eras.csv"
    lines = p.read_text(encoding="utf-8").splitlines()
    if any(ln.startswith("E2-CM-cat1,") for ln in lines):
        lines = [ln.replace(",2026-12-06,", ",2026-12-05,", 1) if ln.startswith("E2-CM-cat1,") else ln
                 for ln in lines]
    else:
        lines += [_era_line("PLANT-1", "2999-01-01", "2999-01-10"), _era_line("PLANT-2", "2999-02-01", "")]
    p.write_text("\n".join(lines) + "\n", encoding="utf-8")
