"""Trading calendar: confirmed trading days and close-price timing by era (spec trading_calendar R1-R14).

Pure and deterministic: no clock, no network, never writes the CSV tables. Callers pass `datetime.date`.
"""
from __future__ import annotations

import csv
import re
from dataclasses import dataclass
from datetime import date, datetime, time, timedelta
from enum import Enum
from pathlib import Path
from zoneinfo import ZoneInfo

from investos.core.config import Config

IST = ZoneInfo("Asia/Kolkata")

SESSIONS_HEADER = ("date", "exchange", "segment", "status", "open_ist", "close_ist",
                   "evidence_kind", "evidence_ref", "recorded_at")
YEARS_HEADER = ("exchange", "year", "list_source", "loaded_at")
ERAS_HEADER = ("era_id", "effective_from", "effective_to", "exchange", "segment", "category",
               "continuous_end", "cas_start", "cas_end", "cas_applicable", "close_method",
               "market_close", "close_earliest", "post_close_start", "post_close_end",
               "cas_carry_forward", "settlement_window", "public_lag_min", "verified")

EXCHANGES = ("NSE", "BSE")
SEGMENTS = ("CM", "FO")
CATEGORIES = {"CM": ("cat1", "non_fo"), "FO": ("index_fo", "index_fo_expiring", "stock_fo")}
STATUSES = ("holiday", "special_session", "trading")
EVIDENCE = ("holiday_list", "circular", "bhavcopy", "manual")
CLOSE_METHODS = ("vwap_last_30m", "closing_auction", "fo_session_end")
VERIFIED = ("circular", "press", "assumed")
FIRST_SUPPORTED = date(2010, 1, 1)

_DATE_RE = re.compile(r"\d{4}-\d{2}-\d{2}", re.ASCII)
_TIME_RE = re.compile(r"([01]\d|2[0-3]):[0-5]\d", re.ASCII)
_WINDOW_RE = re.compile(r"(([01]\d|2[0-3]):[0-5]\d)-(([01]\d|2[0-3]):[0-5]\d)", re.ASCII)
_YEAR_RE = re.compile(r"\d{4}", re.ASCII)
_INT_RE = re.compile(r"\d{1,4}", re.ASCII)


class CalendarError(Exception):
    """Bad table, conflicting rows, or a date outside the tables."""


class NotATradingDay(CalendarError):
    """A close was asked for on a day that is not (confirmed) trading."""


class DayStatus(str, Enum):
    TRADING = "trading"
    CLOSED = "closed"
    PROVISIONAL = "provisional"
    UNKNOWN = "unknown"


@dataclass(frozen=True)
class EraRow:
    era_id: str
    effective_from: date
    effective_to: date | None
    exchange: str
    segment: str
    category: str
    continuous_end: time
    cas_start: time | None
    cas_end: time | None
    cas_applicable: str | None
    close_method: str
    market_close: time
    close_earliest: time
    post_close_start: time | None
    post_close_end: time | None
    cas_carry_forward: str | None
    settlement_window: str | None
    public_lag_min: int
    verified: str


@dataclass(frozen=True)
class SessionRow:
    date: date
    exchange: str
    segment: str
    status: str
    open_ist: time | None
    close_ist: time | None
    evidence_kind: str
    evidence_ref: str
    recorded_at: date
    line: int


@dataclass(frozen=True)
class SessionTimes:
    date: date
    close_era: str
    market_close: datetime
    close_earliest: datetime
    close_method: str
    price_fields: tuple[str, ...]


@dataclass(frozen=True)
class PriceFacts:
    """Two separate facts (R11): never derived from each other, never merged."""
    close_price: float | None = None
    settlement_price: float | None = None
    close_era: str | None = None


# ---- strict CSV parsing ------------------------------------------------------

def _fail(path: Path, line: int | None, field: str | None, msg: str) -> CalendarError:
    where = f"{path.name}" + (f" line {line}" if line is not None else "")
    return CalendarError(f"{where}" + (f" field {field}" if field else "") + f": {msg}")


def _read(path: Path, header: tuple[str, ...]):
    try:
        fh = open(path, newline="", encoding="utf-8")
    except OSError as e:
        raise CalendarError(f"{path.name}: cannot read {path}: {e}") from None
    with fh:
        rd = csv.reader(fh)
        try:
            first = next(rd)
        except StopIteration:
            raise _fail(path, 1, None, "empty file, header missing") from None
        except csv.Error as e:
            raise _fail(path, 1, None, f"unreadable csv: {e}") from None
        if tuple(first) != header:
            raise _fail(path, 1, "header", f"expected {','.join(header)}; got {','.join(first)}")
        out = []
        try:
            for rec in rd:
                if not rec:
                    continue
                if len(rec) != len(header):
                    raise _fail(path, rd.line_num, None, f"expected {len(header)} fields, got {len(rec)}")
                out.append((rd.line_num, dict(zip(header, rec))))
        except csv.Error as e:
            raise _fail(path, rd.line_num, None, f"unreadable csv: {e}") from None
    return out


def _date(path, line, field, v) -> date:
    if not _DATE_RE.fullmatch(v):
        raise _fail(path, line, field, f"not an ISO date: {v!r}")
    try:
        return date.fromisoformat(v)
    except ValueError:
        raise _fail(path, line, field, f"not a valid date: {v!r}") from None


def _time(path, line, field, v) -> time:
    if not _TIME_RE.fullmatch(v):
        raise _fail(path, line, field, f"not HH:MM: {v!r}")
    return time(int(v[:2]), int(v[3:]))


def _opt_time(path, line, field, v):
    return _time(path, line, field, v) if v != "" else None


def _enum(path, line, field, v, allowed, allow_empty=False):
    if v == "" and allow_empty:
        return None
    if v not in allowed:
        raise _fail(path, line, field, f"{v!r} not one of {', '.join(allowed)}")
    return v


def _load_sessions(path: Path) -> list[SessionRow]:
    rows: list[SessionRow] = []
    seen: dict[tuple, int] = {}
    for line, r in _read(path, SESSIONS_HEADER):
        d = _date(path, line, "date", r["date"])
        ex = _enum(path, line, "exchange", r["exchange"], EXCHANGES)
        seg = _enum(path, line, "segment", r["segment"], SEGMENTS + ("ALL",))
        st = _enum(path, line, "status", r["status"], STATUSES)
        ek = _enum(path, line, "evidence_kind", r["evidence_kind"], EVIDENCE)
        op = _opt_time(path, line, "open_ist", r["open_ist"])
        cl = _opt_time(path, line, "close_ist", r["close_ist"])
        if st == "special_session":
            if op is None:
                raise _fail(path, line, "open_ist", "mandatory for special_session")
            if cl is None:
                raise _fail(path, line, "close_ist", "mandatory for special_session")
            if cl <= op:
                raise _fail(path, line, "close_ist", "must be after open_ist")
        else:
            if op is not None:
                raise _fail(path, line, "open_ist", f"must be empty for {st}")
            if cl is not None:
                raise _fail(path, line, "close_ist", f"must be empty for {st}")
        if st == "trading" and ek == "holiday_list":
            raise _fail(path, line, "evidence_kind",
                        "a trading row cannot be confirmed by a holiday_list (it names closures only)")
        if not r["evidence_ref"].strip():
            raise _fail(path, line, "evidence_ref", "must not be empty")
        ra = _date(path, line, "recorded_at", r["recorded_at"])
        key = (d, ex, seg)
        if key in seen:
            raise _fail(path, line, "date",
                        f"duplicate (date, exchange, segment) = ({d}, {ex}, {seg}); first at line {seen[key]}")
        seen[key] = line
        rows.append(SessionRow(d, ex, seg, st, op, cl, ek, r["evidence_ref"], ra, line))
    # R4: a trading row and a holiday row that apply to the same segment contradict each other.
    # A special_session is a deliberate exception and may sit next to an ALL (or segment) holiday (spec A6/A7).
    by_day: dict[tuple, list[SessionRow]] = {}
    for s in rows:
        by_day.setdefault((s.date, s.exchange), []).append(s)
    for (d, ex), lst in by_day.items():
        for a in lst:
            for b in lst:
                if a.status == "trading" and b.status == "holiday" and \
                        (a.segment == b.segment or "ALL" in (a.segment, b.segment)):
                    raise _fail(path, max(a.line, b.line), "status",
                                f"conflict on {d} {ex}: trading ({a.segment}, line {a.line}) vs "
                                f"holiday ({b.segment}, line {b.line}); resolve by hand")
    return rows


def _load_years(path: Path) -> dict[str, dict[int, date]]:
    out: dict[str, dict[int, date]] = {e: {} for e in EXCHANGES}
    for line, r in _read(path, YEARS_HEADER):
        ex = _enum(path, line, "exchange", r["exchange"], EXCHANGES)
        if not _YEAR_RE.fullmatch(r["year"]):
            raise _fail(path, line, "year", f"not a 4-digit year: {r['year']!r}")
        y = int(r["year"])
        if not r["list_source"].strip():
            raise _fail(path, line, "list_source", "must not be empty")
        la = _date(path, line, "loaded_at", r["loaded_at"])
        if y in out[ex]:
            raise _fail(path, line, "year", f"duplicate ({ex}, {y})")
        out[ex][y] = la
    return out


def _load_eras(path: Path) -> list[EraRow]:
    rows: list[EraRow] = []
    ids: dict[str, int] = {}
    for line, r in _read(path, ERAS_HEADER):
        if not r["era_id"].strip():
            raise _fail(path, line, "era_id", "must not be empty")
        if r["era_id"] in ids:
            raise _fail(path, line, "era_id", f"duplicate era_id {r['era_id']!r} (first at line {ids[r['era_id']]})")
        ids[r["era_id"]] = line
        frm = _date(path, line, "effective_from", r["effective_from"])
        to = _date(path, line, "effective_to", r["effective_to"]) if r["effective_to"] != "" else None
        ex = _enum(path, line, "exchange", r["exchange"], EXCHANGES)
        seg = _enum(path, line, "segment", r["segment"], SEGMENTS)
        cat = _enum(path, line, "category", r["category"], CATEGORIES[seg])
        cont = _time(path, line, "continuous_end", r["continuous_end"])
        cs = _opt_time(path, line, "cas_start", r["cas_start"])
        ce = _opt_time(path, line, "cas_end", r["cas_end"])
        if (cs is None) != (ce is None):
            raise _fail(path, line, "cas_start", "cas_start and cas_end must both be set or both empty")
        app = _enum(path, line, "cas_applicable", r["cas_applicable"], ("yes", "no", "unverified"), True)
        meth = _enum(path, line, "close_method", r["close_method"], CLOSE_METHODS)
        mc = _time(path, line, "market_close", r["market_close"])
        ceo = _opt_time(path, line, "close_earliest", r["close_earliest"]) or mc
        pcs = _opt_time(path, line, "post_close_start", r["post_close_start"])
        pce = _opt_time(path, line, "post_close_end", r["post_close_end"])
        cf = _enum(path, line, "cas_carry_forward", r["cas_carry_forward"], ("yes", "no"), True)
        sw = r["settlement_window"]
        if sw != "":
            m = _WINDOW_RE.fullmatch(sw)
            if not m or m.group(1) >= m.group(3):
                raise _fail(path, line, "settlement_window", f"not HH:MM-HH:MM: {sw!r}")
        if not _INT_RE.fullmatch(r["public_lag_min"]):
            raise _fail(path, line, "public_lag_min", f"not a whole number of minutes >= 0: {r['public_lag_min']!r}")
        ver = _enum(path, line, "verified", r["verified"], VERIFIED)
        rows.append(EraRow(r["era_id"], frm, to, ex, seg, cat, cont, cs, ce, app, meth, mc, ceo, pcs, pce,
                           cf, sw or None, int(r["public_lag_min"]), ver))
    return rows


def _check_eras(path: Path, rows: list[EraRow]) -> dict[tuple, list[EraRow]]:
    """R8. Returns rows per group (exchange, segment, category), sorted by effective_from."""
    for e in rows:
        if e.effective_to is not None and e.effective_to < e.effective_from:
            raise CalendarError(f"{path.name}: {e.era_id}: effective_to {e.effective_to} before "
                                f"effective_from {e.effective_from}")
        if e.market_close < e.close_earliest:
            raise CalendarError(f"{path.name}: {e.era_id}: market_close before close_earliest")
        if e.market_close < e.continuous_end:
            raise CalendarError(f"{path.name}: {e.era_id}: market_close before continuous_end")
        if e.cas_end is not None and e.market_close < e.cas_end:
            raise CalendarError(f"{path.name}: {e.era_id}: market_close before cas_end")
        if e.post_close_start is not None and e.post_close_start < e.market_close:
            raise CalendarError(f"{path.name}: {e.era_id}: post_close_start before market_close")
    groups: dict[tuple, list[EraRow]] = {}
    for e in rows:
        groups.setdefault((e.exchange, e.segment, e.category), []).append(e)
    for g, lst in groups.items():
        lst.sort(key=lambda e: e.effective_from)
        for a, b in zip(lst, lst[1:]):
            if a.effective_to is None:
                raise CalendarError(f"{path.name}: {a.era_id} is open-ended but {b.era_id} follows it")
            nxt = a.effective_to + timedelta(days=1)
            if b.effective_from < nxt:
                raise CalendarError(f"{path.name}: {a.era_id} and {b.era_id} overlap")
            if b.effective_from > nxt:
                raise CalendarError(f"{path.name}: gap between {a.era_id} (to {a.effective_to}) and "
                                    f"{b.era_id} (from {b.effective_from})")
        if lst[-1].effective_to is not None:
            raise CalendarError(f"{path.name}: {lst[-1].era_id} is the last row of its group but has "
                                f"effective_to {lst[-1].effective_to}; the newest row must be open-ended")
    return groups


def _check_date(d) -> None:
    if isinstance(d, datetime) or not isinstance(d, date):
        raise TypeError(f"expected datetime.date, got {type(d).__name__}")


class TradingCalendar:
    def __init__(self, cfg: Config, *, close_eras: Path | None = None, sessions: Path | None = None,
                 years: Path | None = None):
        base = Path(cfg.repo_root) / "config" / "calendar"
        self._eras_path = Path(close_eras) if close_eras else base / "close_eras.csv"
        self._sessions_path = Path(sessions) if sessions else base / "sessions.csv"
        self._years_path = Path(years) if years else base / "years.csv"
        self._era_list = _load_eras(self._eras_path)
        self._groups = _check_eras(self._eras_path, self._era_list)
        self._session_list = _load_sessions(self._sessions_path)
        self._sessions = {(s.date, s.exchange, s.segment): s for s in self._session_list}
        self._years = _load_years(self._years_path)

    # ---- table access (read-only views, used by the check) -------------------
    def all_eras(self) -> tuple[EraRow, ...]:
        return tuple(self._era_list)

    def loaded_years(self, exchange: str = "NSE") -> tuple[int, ...]:
        return tuple(sorted(self._years[exchange]))

    # ---- argument validation -------------------------------------------------
    @staticmethod
    def _args(exchange, segment, category=None):
        if exchange not in EXCHANGES:
            raise ValueError(f"unknown exchange {exchange!r}")
        if segment not in SEGMENTS:
            raise ValueError(f"unknown segment {segment!r}")
        if category is not None and category not in CATEGORIES[segment]:
            raise ValueError(f"category {category!r} not valid for segment {segment}: "
                             f"{', '.join(CATEGORIES[segment])}")

    def _session_row(self, d, exchange, segment) -> SessionRow | None:
        return self._sessions.get((d, exchange, segment)) or self._sessions.get((d, exchange, "ALL"))

    # ---- status --------------------------------------------------------------
    def day_status(self, d: date, *, exchange: str = "NSE", segment: str = "CM") -> DayStatus:
        _check_date(d)
        self._args(exchange, segment)
        row = self._session_row(d, exchange, segment)
        if row is not None:
            return DayStatus.CLOSED if row.status == "holiday" else DayStatus.TRADING
        if d.year in self._years[exchange]:
            return DayStatus.CLOSED if d.weekday() >= 5 else DayStatus.PROVISIONAL
        return DayStatus.UNKNOWN

    def is_trading_day(self, d: date, *, exchange: str = "NSE", segment: str = "CM") -> bool:
        return self.day_status(d, exchange=exchange, segment=segment) is DayStatus.TRADING

    # ---- eras ----------------------------------------------------------------
    def era_for(self, d: date, segment: str, security_category: str, *, exchange: str = "NSE") -> EraRow:
        _check_date(d)
        self._args(exchange, segment, security_category)
        lst = self._groups.get((exchange, segment, security_category))
        if not lst:
            raise CalendarError(f"no era rows for {exchange}/{segment}/{security_category}")
        for e in lst:
            if e.effective_from <= d and (e.effective_to is None or d <= e.effective_to):
                return e
        raise CalendarError(f"{d} is outside the era table for {exchange}/{segment}/{security_category} "
                            f"(first effective_from {lst[0].effective_from})")

    def session_times(self, d: date, segment: str, security_category: str, *, exchange: str = "NSE",
                      allow_provisional: bool = False) -> SessionTimes:
        _check_date(d)
        self._args(exchange, segment, security_category)
        st = self.day_status(d, exchange=exchange, segment=segment)
        if st is not DayStatus.TRADING and not (st is DayStatus.PROVISIONAL and allow_provisional):
            raise NotATradingDay(f"{d} is {st.value} on {exchange}/{segment}")
        era = self.era_for(d, segment, security_category, exchange=exchange)
        row = self._session_row(d, exchange, segment)
        if row is not None and row.status == "special_session":
            close = datetime.combine(d, row.close_ist, tzinfo=IST)
            method = "fo_session_end" if segment == "FO" else "vwap_last_30m"
            return SessionTimes(d, f"special:{d.isoformat()}", close, close, method, ("close_price",))
        fields = ("close_price", "settlement_price") if era.settlement_window else ("close_price",)
        return SessionTimes(d, era.era_id, datetime.combine(d, era.market_close, tzinfo=IST),
                            datetime.combine(d, era.close_earliest, tzinfo=IST), era.close_method, fields)

    def market_close_ist(self, d: date, segment: str, security_category: str, *, exchange: str = "NSE",
                         allow_provisional: bool = False) -> datetime:
        return self.session_times(d, segment, security_category, exchange=exchange,
                                  allow_provisional=allow_provisional).market_close

    def after_close_public_at(self, d: date, segment: str, security_category: str, *, exchange: str = "NSE",
                              allow_provisional: bool = False) -> datetime:
        st = self.session_times(d, segment, security_category, exchange=exchange,
                                allow_provisional=allow_provisional)
        lag = self.era_for(d, segment, security_category, exchange=exchange).public_lag_min
        return st.market_close + timedelta(minutes=lag)

    def known_at(self, d: date, segment: str, security_category: str, fetched_at: datetime, *,
                 exchange: str = "NSE") -> datetime:
        if not isinstance(fetched_at, datetime) or fetched_at.tzinfo is None \
                or fetched_at.utcoffset() is None:
            raise ValueError("fetched_at must be a timezone-aware datetime")
        public = self.after_close_public_at(d, segment, security_category, exchange=exchange)
        return fetched_at if fetched_at > public else public
