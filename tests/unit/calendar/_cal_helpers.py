"""Shared helpers for S1-T4 (trading calendar) tests. investos imports happen inside functions only,
so every test fails individually (not at collection) while investos.calendar is absent."""
from __future__ import annotations

import csv
import shutil
from datetime import date, datetime, time
from pathlib import Path

REPO = Path(__file__).resolve().parents[3]
SHIPPED = REPO / "config" / "calendar"

SESSIONS_HEADER = ["date", "exchange", "segment", "status", "open_ist", "close_ist",
                   "evidence_kind", "evidence_ref", "recorded_at"]
YEARS_HEADER = ["exchange", "year", "list_source", "loaded_at"]
ERAS_HEADER = ["era_id", "effective_from", "effective_to", "exchange", "segment", "category",
               "continuous_end", "cas_start", "cas_end", "cas_applicable", "close_method",
               "market_close", "close_earliest", "post_close_start", "post_close_end",
               "cas_carry_forward", "settlement_window", "public_lag_min", "verified"]

ZONE_DIRS = ["data/raw", "data/manifests", "data/canonical", "data/ledger", "reports", "registry"]
TOML = """\
[general]
timezone = "Asia/Kolkata"

[paths]
raw        = "data/raw"
manifests  = "data/manifests"
canonical  = "data/canonical"
ledger     = "data/ledger"
reports    = "reports"
registry   = "registry"

[db]
migrations_dir = "db/migrations"

[rate_limits]
default_interval_s = 0.35
"""

D = date  # short alias


def make_repo(root: Path) -> Path:
    root.mkdir(parents=True, exist_ok=True)
    (root / "pyproject.toml").write_text("[project]\nname='x'\n")
    for z in ZONE_DIRS:
        (root / z).mkdir(parents=True, exist_ok=True)
    (root / "db" / "migrations").mkdir(parents=True, exist_ok=True)
    (root / "config").mkdir(exist_ok=True)
    (root / "config" / "investos.toml").write_text(TOML)
    return root


def load_cfg(repo: Path):
    from investos.core.config import load_config
    return load_config(repo / "config" / "investos.toml", env={}, repo_root=repo)


# ---- csv helpers -----------------------------------------------------------

def write_csv(path: Path, header: list[str], rows: list[dict]) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="", encoding="utf-8") as fh:
        w = csv.writer(fh, lineterminator="\n")
        w.writerow(header)
        for r in rows:
            w.writerow([r.get(h, "") for h in header])
    return path


def read_csv(path: Path) -> tuple[list[str], list[dict]]:
    with Path(path).open(newline="", encoding="utf-8") as fh:
        rd = csv.DictReader(fh)
        return list(rd.fieldnames or []), list(rd)


def write_raw(path: Path, text: str) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")
    return path


def srow(d, status="trading", *, exchange="NSE", segment="CM", open_ist="", close_ist="",
         evidence_kind="manual", evidence_ref="fixture", recorded_at="2026-11-20") -> dict:
    return {"date": d.isoformat() if hasattr(d, "isoformat") else d, "exchange": exchange,
            "segment": segment, "status": status, "open_ist": open_ist, "close_ist": close_ist,
            "evidence_kind": evidence_kind, "evidence_ref": evidence_ref, "recorded_at": recorded_at}


def yrow(year, exchange="NSE", source="fixture-list", loaded_at="2026-01-02") -> dict:
    return {"exchange": exchange, "year": str(year), "list_source": source, "loaded_at": loaded_at}


def confirm_rows(dates, exchanges=("NSE", "BSE")) -> list[dict]:
    """ALL-segment `trading` rows (manual evidence) for each date, so era dates are confirmed."""
    return [srow(d, "trading", exchange=e, segment="ALL") for d in dates for e in exchanges]


def era_rows() -> tuple[list[str], list[dict]]:
    return read_csv(SHIPPED / "close_eras.csv")


def find_era(rows: list[dict], era_id: str) -> dict:
    for r in rows:
        if r["era_id"] == era_id:
            return r
    raise KeyError(era_id)


class Tables:
    """A tmp repo with config/calendar/{close_eras,sessions,years}.csv; copies of the shipped eras."""

    def __init__(self, tmp: Path):
        self.repo = make_repo(tmp / "repo")
        self.dir = self.repo / "config" / "calendar"
        self.dir.mkdir(parents=True, exist_ok=True)
        self.eras_path = self.dir / "close_eras.csv"
        self.sessions_path = self.dir / "sessions.csv"
        self.years_path = self.dir / "years.csv"
        shutil.copyfile(SHIPPED / "close_eras.csv", self.eras_path)
        self.set_sessions([])
        self.set_years([yrow(2026), yrow(2026, "BSE")])

    def set_sessions(self, rows):
        write_csv(self.sessions_path, SESSIONS_HEADER, rows)
        return self

    def set_years(self, rows):
        write_csv(self.years_path, YEARS_HEADER, rows)
        return self

    def edit_eras(self, fn):
        """fn(header, rows) mutates in place."""
        header, rows = read_csv(self.eras_path)
        fn(header, rows)
        write_csv(self.eras_path, header, rows)
        return self

    def set_era(self, era_id, **kw):
        def f(h, rows):
            find_era(rows, era_id).update(kw)
        return self.edit_eras(f)

    def calendar(self):
        from investos.calendar.trading import TradingCalendar
        return TradingCalendar(load_cfg(self.repo), close_eras=self.eras_path,
                               sessions=self.sessions_path, years=self.years_path)


def hhmm(h, m=0) -> time:
    return time(h, m)


def ist(y, mo, d, h, mi=0) -> datetime:
    from zoneinfo import ZoneInfo
    return datetime(y, mo, d, h, mi, tzinfo=ZoneInfo("Asia/Kolkata"))


def as_window(w):
    """Normalise a settlement_window value: '15:00-15:30' or (time, time) -> '15:00-15:30'."""
    if isinstance(w, str):
        return w
    a, b = w
    return f"{a:%H:%M}-{b:%H:%M}"
