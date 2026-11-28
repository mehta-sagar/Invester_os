"""The endpoint table `config/backfill_endpoints.csv` (spec collect_backfill R2) and the small value helpers shared by
the probe, the collector and the planner: date formats, request URLs, the date and time-of-day patterns."""
from __future__ import annotations

import csv
import hashlib
import re
from dataclasses import dataclass
from datetime import date, timedelta
from pathlib import Path
from urllib.parse import quote

__all__ = ["Endpoint", "EndpointTableError", "load_endpoints", "load_symbols", "table_path", "symbols_path", "COLUMNS",
           "STATUSES", "COM_ORIGIN", "NSE_HOSTS", "parse_day", "has_time", "fmt_date", "build_url", "anchor_full"]

COLUMNS = ("endpoint_id", "page_url", "anchor_url", "api_path", "params", "date_format", "to_inclusive",
           "max_window_days", "row_cap", "rows_path", "date_field", "start_date", "time_of_day", "known_at_rule",
           "status", "notes")
STATUSES = ("to_probe", "probed", "confirmed", "needs_review", "disabled")
COM_ORIGIN = "https://www.nseindia.com"
NSE_HOSTS = frozenset({"www.nseindia.com", "www.nseindia.in"})
_ID_RE = re.compile(r"[a-z][a-z0-9_]{0,30}", re.ASCII)
_PLACEHOLDER_RE = re.compile(r"\{([^{}]*)\}")
_PLACEHOLDERS = frozenset({"from", "to", "symbol"})
_FORMATS = {"DD-MM-YYYY": "%d-%m-%Y", "YYYY-MM-DD": "%Y-%m-%d"}
_MIN_START = date(2000, 1, 1)
_SYMBOL_RE = re.compile(r"[A-Z0-9][A-Z0-9&_.-]{0,19}", re.ASCII)


class EndpointTableError(Exception):
    pass


@dataclass(frozen=True)
class Endpoint:
    endpoint_id: str
    page_url: str
    anchor_url: str
    api_path: str
    params: str
    date_format: str
    to_inclusive: bool | None
    max_window_days: int
    row_cap: int | None
    rows_path: str
    date_field: str
    start_date: date
    time_of_day: str
    known_at_rule: str
    status: str
    notes: str

    @property
    def source(self) -> str:
        return "backfill_" + self.endpoint_id

    def cells(self) -> list[str]:
        return [self.endpoint_id, self.page_url, self.anchor_url, self.api_path, self.params, self.date_format,
                {True: "yes", False: "no", None: ""}[self.to_inclusive], str(self.max_window_days),
                "" if self.row_cap is None else str(self.row_cap), self.rows_path, self.date_field,
                self.start_date.isoformat(), self.time_of_day, self.known_at_rule, self.status, self.notes]

    def row_hash(self) -> str:
        """sha256 of the canonical row text: any one-cell change gives another hash (R5)."""
        return hashlib.sha256("\x1f".join(self.cells()).encode("utf-8")).hexdigest()


def table_path(repo_root: Path) -> Path:
    return Path(repo_root) / "config" / "backfill_endpoints.csv"


def symbols_path(repo_root: Path) -> Path:
    return Path(repo_root) / "config" / "symbols_backfill.csv"


def _check_placeholders(text: str, where: str) -> None:
    for m in _PLACEHOLDER_RE.finditer(text):
        if m.group(1) not in _PLACEHOLDERS:
            raise EndpointTableError(f"{where}: unknown placeholder {{{m.group(1)}}}")
    if "{" in _PLACEHOLDER_RE.sub("", text) or "}" in _PLACEHOLDER_RE.sub("", text):
        raise EndpointTableError(f"{where}: unbalanced brace")


def _check_path(value: str, where: str, *, anchor: bool) -> None:
    if value.startswith("/") and not value.startswith("//"):
        if "://" in value or "\\" in value:
            raise EndpointTableError(f"{where}: must be a path without a scheme or host")
        return
    if anchor and value.startswith(COM_ORIGIN + "/"):
        return
    raise EndpointTableError(f"{where}: must start with '/' (a path on the page's own origin)"
                             + (" or be an https://www.nseindia.com/ page" if anchor else ""))


def _parse_row(cells: list[str], line: int, today: date | None) -> Endpoint:
    c = dict(zip(COLUMNS, (x.strip() for x in cells)))
    where = f"line {line} ({c['endpoint_id'] or '?'})"
    if not _ID_RE.fullmatch(c["endpoint_id"]):
        raise EndpointTableError(f"{where}: endpoint_id must match [a-z][a-z0-9_]{{0,30}}")
    _check_path(c["page_url"], f"{where} page_url", anchor=True)
    _check_path(c["anchor_url"], f"{where} anchor_url", anchor=True)
    _check_path(c["api_path"], f"{where} api_path", anchor=False)
    _check_placeholders(c["params"], f"{where} params")
    _check_placeholders(c["api_path"], f"{where} api_path")
    if c["date_format"] not in _FORMATS:
        raise EndpointTableError(f"{where}: date_format must be one of {', '.join(_FORMATS)}")
    incl = {"yes": True, "no": False, "": None}.get(c["to_inclusive"], "bad")
    if incl == "bad":
        raise EndpointTableError(f"{where}: to_inclusive must be yes, no or empty")
    if not re.fullmatch(r"[0-9]{1,5}", c["max_window_days"]) or int(c["max_window_days"]) < 1:
        raise EndpointTableError(f"{where}: max_window_days must be an integer >= 1")
    if c["row_cap"] == "":
        cap = None
    elif re.fullmatch(r"[0-9]{1,9}", c["row_cap"]) and int(c["row_cap"]) >= 1:
        cap = int(c["row_cap"])
    else:
        raise EndpointTableError(f"{where}: row_cap must be empty or a positive integer")
    try:
        start = date.fromisoformat(c["start_date"])
    except ValueError:
        raise EndpointTableError(f"{where}: start_date must be an ISO date") from None
    if start < _MIN_START:
        raise EndpointTableError(f"{where}: start_date is before {_MIN_START}")
    if today is not None and start > today:
        raise EndpointTableError(f"{where}: start_date is after the run date {today}")
    if c["status"] not in STATUSES:
        raise EndpointTableError(f"{where}: status must be one of {', '.join(STATUSES)}")
    return Endpoint(c["endpoint_id"], c["page_url"], c["anchor_url"], c["api_path"], c["params"], c["date_format"],
                    incl, int(c["max_window_days"]), cap, c["rows_path"], c["date_field"], start, c["time_of_day"],
                    c["known_at_rule"], c["status"], c["notes"])


def load_endpoints(path: Path, *, today: date | None = None) -> dict[str, Endpoint]:
    """Strict load; table order is kept. ``today`` (the run date) switches on the start_date upper bound."""
    try:
        with open(path, newline="", encoding="utf-8") as fh:
            reader = csv.reader(fh)
            rows = [(reader.line_num, r) for r in reader]
    except (OSError, UnicodeError, csv.Error) as e:
        raise EndpointTableError(f"cannot read {path}: {e}") from None
    if not rows or rows[0][1] != list(COLUMNS):
        raise EndpointTableError(f"{Path(path).name}: the header must be exactly {','.join(COLUMNS)}")
    out: dict[str, Endpoint] = {}
    for line, cells in rows[1:]:
        if len(cells) != len(COLUMNS):
            raise EndpointTableError(f"line {line}: expected {len(COLUMNS)} cells, got {len(cells)}")
        ep = _parse_row(cells, line, today)
        if ep.endpoint_id in out:
            raise EndpointTableError(f"line {line}: duplicate endpoint_id {ep.endpoint_id}")
        out[ep.endpoint_id] = ep
    return out


def load_symbols(path: Path) -> list[str]:
    """The per-company symbol list (R22): header `symbol`, one valid symbol per line, no duplicates."""
    try:
        with open(path, newline="", encoding="utf-8") as fh:
            rows = list(csv.reader(fh))
    except (OSError, UnicodeError, csv.Error) as e:
        raise EndpointTableError(f"cannot read {path}: {e}") from None
    if not rows or rows[0] != ["symbol"]:
        raise EndpointTableError(f"{Path(path).name}: the header must be exactly 'symbol'")
    out: list[str] = []
    for n, r in enumerate(rows[1:], 2):
        if len(r) != 1 or not _SYMBOL_RE.fullmatch(r[0]):
            raise EndpointTableError(f"line {n}: not a valid symbol")
        if r[0] in out:
            raise EndpointTableError(f"line {n}: duplicate symbol {r[0]}")
        out.append(r[0])
    if not out:
        raise EndpointTableError(f"{Path(path).name}: no symbols")
    return out


# --------------------------------------------------------------------------- value helpers
_MONTHS = {m: i for i, m in enumerate(("jan", "feb", "mar", "apr", "may", "jun", "jul", "aug", "sep", "oct", "nov",
                                       "dec"), 1)}
_DAY_PATTERNS = (
    (re.compile(r"\s*([0-9]{4})-([0-9]{2})-([0-9]{2})(?![0-9])"), "ymd"),
    (re.compile(r"\s*([0-9]{1,2})-([A-Za-z]{3})-([0-9]{4})(?![0-9])"), "dmy_text"),
    (re.compile(r"\s*([0-9]{1,2})[-/]([0-9]{1,2})[-/]([0-9]{4})(?![0-9])"), "dmy"),
)
_TIME_RE = re.compile(r"(?<![0-9])[0-9]{1,2}:[0-9]{2}")


def parse_day(value: object) -> date | None:
    """The calendar day a date or datetime text names; None when it names none (no parsing of anything else)."""
    if not isinstance(value, str):
        return None
    for rx, kind in _DAY_PATTERNS:
        m = rx.match(value)
        if m is None:
            continue
        try:
            if kind == "ymd":
                return date(int(m.group(1)), int(m.group(2)), int(m.group(3)))
            if kind == "dmy_text":
                return date(int(m.group(3)), _MONTHS[m.group(2).lower()], int(m.group(1)))
            return date(int(m.group(3)), int(m.group(2)), int(m.group(1)))
        except (ValueError, KeyError):
            return None
    return None


def has_time(value: object) -> bool:
    return isinstance(value, str) and _TIME_RE.search(value) is not None


def fmt_date(d: date, date_format: str) -> str:
    return d.strftime(_FORMATS[date_format])


def build_url(ep: Endpoint, origin: str, start: date, end: date, *, to_inclusive: bool | None = True,
              date_format: str | None = None, symbol: str | None = None) -> str:
    """origin + api_path with {from}/{to} formatted; an exclusive `to` is requested as end + 1 (R11)."""
    fmt = date_format or ep.date_format
    to = end if to_inclusive is not False else end + timedelta(days=1)
    path = ep.api_path.replace("{from}", fmt_date(start, fmt)).replace("{to}", fmt_date(to, fmt))
    if symbol is not None:
        quoted = quote(symbol, safe="")
        if "{symbol}" in path:
            path = path.replace("{symbol}", quoted)
        else:
            path += ("&" if "?" in path else "?") + "symbol=" + quoted
    return origin.rstrip("/") + path


def anchor_full(ep: Endpoint) -> str:
    return ep.anchor_url if ep.anchor_url.startswith("https://") else COM_ORIGIN + ep.anchor_url
