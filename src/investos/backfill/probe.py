"""The PROBE stage (spec collect_backfill R4, R5): measures every endpoint before any bulk fetch.

For one endpoint it learns, with a bounded number of requests, the response shape, the row cap, whether `to` is
inclusive, the earliest date with data, whether rows carry a time of day and whether a window answers the same
twice. Nothing it computes is stored as data: every body goes to the manifest as raw bytes (source `backfill_probe`),
the findings go to `probe.json` and a human report. The probe never edits the endpoint table."""
from __future__ import annotations

import json
import os
import socket
import tempfile
from dataclasses import asdict, dataclass, replace
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Callable, Iterable, Mapping, Sequence
from zoneinfo import ZoneInfo

from investos.acquire.base import TransportError
from investos.backfill.endpoints import (Endpoint, anchor_full, build_url, has_time, parse_day)
from investos.backfill.ledger import ledger_dir
from investos.core.config import Config

__all__ = ["ProbeResult", "ProbeError", "ProbeBlocked", "load_probe", "load_probe_file", "save_probe_results",
           "mark_needs_review", "probe_endpoint", "probe_age_days", "unusable_reason", "DEFAULT_API_ORIGIN",
           "PROBE_MAX_AGE_DAYS", "MAX_REQUESTS_PER_ENDPOINT", "probe_json_path", "write_probe_report",
           "BLOCK_STATUSES", "parse_rows"]

IST = ZoneInfo("Asia/Kolkata")
DEFAULT_API_ORIGIN = "https://www.nseindia.in"
PROBE_MAX_AGE_DAYS = 30
MAX_REQUESTS_PER_ENDPOINT = 60
BLOCK_STATUSES = frozenset({401, 403, 429, 451})
_LADDER = (1, 3, 7, 14, 31)
_ROUND = frozenset({10, 20, 25, 50, 70, 100, 200, 250, 500, 1000, 2000, 2500, 5000, 10000})
_ONE_DAY = timedelta(days=1)
_DEFAULT_SOURCE = "backfill_probe"


class ProbeError(Exception):
    pass


class ProbeBlocked(Exception):
    def __init__(self, url: str, status: int) -> None:
        super().__init__(f"blocked http {status}")
        self.url, self.status = url, status


class _Stop(Exception):
    """This endpoint cannot be measured further: needs_review with the reason."""


@dataclass(frozen=True)
class ProbeResult:
    endpoint_id: str
    row_hash: str
    probed_at: str
    api_origin: str
    rows_path: str
    date_field: str
    date_format: str
    to_inclusive: bool | None
    row_cap: int | None
    max_safe_rows: int
    max_window_days: int
    earliest_date: date
    time_of_day: str
    undercount: bool
    mode: str
    status: str
    reasons: tuple[str, ...]
    rows_per_day: float | None = None
    depth: int | None = None
    symbol_source: str = ""

    def to_json(self) -> dict:
        d = asdict(self)
        d["earliest_date"] = self.earliest_date.isoformat()
        d["reasons"] = list(self.reasons)
        return d


def probe_json_path(cfg: Config) -> Path:
    return ledger_dir(cfg) / "probe.json"


def _from_json(d: Mapping) -> ProbeResult:
    try:
        return ProbeResult(
            endpoint_id=d["endpoint_id"], row_hash=d["row_hash"], probed_at=d["probed_at"], api_origin=d["api_origin"],
            rows_path=d["rows_path"], date_field=d["date_field"], date_format=d["date_format"],
            to_inclusive=d["to_inclusive"], row_cap=d["row_cap"], max_safe_rows=int(d["max_safe_rows"]),
            max_window_days=int(d["max_window_days"]), earliest_date=date.fromisoformat(d["earliest_date"]),
            time_of_day=d["time_of_day"], undercount=bool(d["undercount"]), mode=d["mode"], status=d["status"],
            reasons=tuple(d["reasons"]), rows_per_day=d.get("rows_per_day"), depth=d.get("depth"),
            symbol_source=d.get("symbol_source", ""))
    except (KeyError, TypeError, ValueError) as e:
        raise ProbeError(f"probe.json entry is malformed: {type(e).__name__}: {e}") from None


def load_probe_file(cfg: Config) -> dict[str, ProbeResult]:
    path = probe_json_path(cfg)
    try:
        obj = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return {}
    except (OSError, ValueError) as e:
        raise ProbeError(f"probe.json is unreadable: {e}") from None
    if not isinstance(obj, dict) or obj.get("schema") != 1 or not isinstance(obj.get("endpoints"), dict):
        raise ProbeError("probe.json: schema must be 1 with an `endpoints` object")
    return {eid: _from_json(ent) for eid, ent in obj["endpoints"].items()}


def load_probe(cfg: Config, ep: Endpoint) -> ProbeResult | None:
    """The stored probe of an endpoint, only when it was made for exactly this table row (R5)."""
    r = load_probe_file(cfg).get(ep.endpoint_id)
    return r if r is not None and r.row_hash == ep.row_hash() else None


def _write_json(path: Path, obj: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    text = json.dumps(obj, sort_keys=True, separators=(",", ":")) + "\n"
    fd, tmp = tempfile.mkstemp(prefix=".probe-", dir=str(path.parent))
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


def save_probe_results(cfg: Config, results: Iterable[ProbeResult]) -> None:
    """A re-probe replaces the endpoint's entry; the other entries stay."""
    path = probe_json_path(cfg)
    try:
        old = json.loads(path.read_text(encoding="utf-8"))["endpoints"]
    except FileNotFoundError:
        old = {}
    except (OSError, ValueError, KeyError, TypeError) as e:
        raise ProbeError(f"probe.json is unreadable: {e}") from None
    for r in results:
        old[r.endpoint_id] = r.to_json()
    _write_json(path, {"schema": 1, "endpoints": old})


def mark_needs_review(cfg: Config, endpoint_id: str, reason: str) -> None:
    """The runner found something a human must look at (an audit that lost rows): the endpoint is refused until re-probed."""
    r = load_probe_file(cfg).get(endpoint_id)
    if r is not None:
        save_probe_results(cfg, [replace(r, status="needs_review", reasons=r.reasons + (reason,))])


def _parse_ts(text: str) -> datetime:
    return datetime.strptime(text, "%Y-%m-%dT%H:%M:%S.%fZ").replace(tzinfo=timezone.utc)


def probe_age_days(probe: ProbeResult, now: datetime) -> float:
    return (now - _parse_ts(probe.probed_at)).total_seconds() / 86400.0


def unusable_reason(probe: ProbeResult | None, now: datetime) -> str:
    """"" when the probe allows a backfill; else why not (R3)."""
    if probe is None:
        return "refused: probe not confirmed (no probe for this table row)"
    if probe.status != "confirmed":
        return f"refused: probe not confirmed (status {probe.status})"
    try:
        if probe_age_days(probe, now) > PROBE_MAX_AGE_DAYS:
            return f"refused: probe not confirmed (older than {PROBE_MAX_AGE_DAYS} days)"
    except ValueError:
        return "refused: probe not confirmed (probed_at is unreadable)"
    return ""


# --------------------------------------------------------------------------- body analysis
def parse_rows(body: bytes, rows_path: str | None = None) -> tuple[str, list] | None:
    """(rows_path, rows) when the body is JSON that holds a row list, else None. With ``rows_path`` given the list
    must sit at that key; without it a top-level list is path "" and an object's first list value names the path."""
    try:
        obj = json.loads(body)
    except (ValueError, UnicodeError):
        return None
    if rows_path is not None:
        cur = obj
        for part in [p for p in rows_path.split(".") if p]:
            cur = cur.get(part) if isinstance(cur, dict) else None
        return (rows_path, cur) if isinstance(cur, list) else None
    if isinstance(obj, list):
        return "", obj
    if isinstance(obj, dict):
        for k, v in obj.items():
            if isinstance(v, list):
                return k, v
    return None


def _add_years(d: date, k: int) -> date:
    try:
        return d.replace(year=d.year + k)
    except ValueError:
        return d.replace(year=d.year + k, day=28)


def _month_chunks(lo: date, hi: date) -> list[tuple[date, date]]:
    out: list[tuple[date, date]] = []
    cur = lo
    while cur <= hi:
        nxt = (cur.replace(day=1) + timedelta(days=32)).replace(day=1)
        out.append((cur, min(nxt - _ONE_DAY, hi)))
        cur = nxt
    return out


def _prev_weekday(d: date) -> date:
    while d.weekday() >= 5:
        d -= _ONE_DAY
    return d


def _row_text(row) -> str:
    return json.dumps(row, sort_keys=True, ensure_ascii=False)


@dataclass
class _Got:
    n: int
    rows: list
    body: bytes
    key: str
    window: str


class _Probe:
    def __init__(self, ep: Endpoint, browser, *, clock, now_ist: date, store, report: list, symbols, hostname,
                 on_block, run_id: str, max_requests: int) -> None:
        self.ep, self.browser, self.clock, self.today = ep, browser, clock, now_ist
        self.store, self.out, self.symbols = store, report, list(symbols or [])
        self.hostname, self.on_block, self.run_id, self.limit = hostname, on_block, run_id, max_requests
        self.origin = getattr(browser, "api_origin", None) or DEFAULT_API_ORIGIN
        self.api_origin = self.origin
        self.n = 0
        self.cache: dict[tuple, _Got] = {}
        self.reasons: list[str] = []
        self.incl: bool | None = True
        self.rows_path = ""
        self.rows_path_known = False
        self.anchored = False
        self.date_field = ""
        self.max_n = 0
        self.req_lines: list[str] = []
        stamp = clock().astimezone(IST).strftime("%Y%m%dT%H%M%S")
        probe_id, k = stamp, 1
        while store.get_by_key(_DEFAULT_SOURCE, f"{ep.endpoint_id}/{probe_id}/1") is not None:
            probe_id, k = f"{stamp}-{k}", k + 1
        self.probe_id = probe_id
        self.findings: dict = {}

    # ---- one request
    def get(self, s: date | None, e: date | None, *, fresh: bool = False, symbol: str | None = None) -> _Got:
        ck = (s, e, symbol, self.incl)
        if not fresh and ck in self.cache:
            return self.cache[ck]
        if self.n >= self.limit:
            raise _Stop(f"request limit {self.limit} reached")
        start, end = (s, e) if symbol is None else (self.ep.start_date, self.today)
        url = build_url(self.ep, self.origin, start, end, to_inclusive=self.incl, symbol=symbol)
        self.n += 1
        pace = getattr(self.browser, "pace", None)
        if pace is not None:
            pace(url)
        try:
            resp = self.browser.fetch(url)
        except TransportError as te:
            raise _Stop(str(te)) from None
        if resp.status in BLOCK_STATUSES:
            if self.on_block is not None:
                self.on_block(url, resp.status)
            raise ProbeBlocked(url, resp.status)
        window = f"{s}..{e}" if symbol is None else f"symbol {symbol}"
        key = f"{self.ep.endpoint_id}/{self.probe_id}/{self.n}"
        if 200 <= resp.status <= 299:
            self.store.store(_DEFAULT_SOURCE, bytes(resp.body), url=url, source_version="backfill-v1",
                             logical_key=key, run_id=self.run_id, fetched_at=self.clock(), access_mode="real_browser",
                             request_headers=resp.request_headers, final_url=resp.final_url or url,
                             fetch_host=self.hostname())
        if not 200 <= resp.status <= 299:
            self.req_lines.append(f"- `{key}` {window} http {resp.status} (not stored)")
            raise _Stop(f"http {resp.status} for {window}")
        parsed = parse_rows(resp.body, self.rows_path if self.rows_path_known else None)
        if parsed is None:
            self.req_lines.append(f"- `{key}` {window} not json rows")
            raise _Stop("not json rows: the body is not JSON holding a row list")
        got = _Got(len(parsed[1]), parsed[1], bytes(resp.body), key, window)
        self.req_lines.append(f"- `{key}` {window} rows {got.n}")
        self.max_n = max(self.max_n, got.n)
        self.cache[ck] = got
        return got

    # ---- helpers on rows
    def day_count(self, got: _Got, d: date) -> int:
        return sum(1 for r in got.rows if isinstance(r, dict) and parse_day(r.get(self.date_field)) == d)

    def win(self, s: date, w: int) -> _Got:
        return self.get(s, s + timedelta(days=w - 1))

    def has_rows(self, s: date, w: int = 7) -> bool:
        return self.win(s, w).n > 0

    # ---- (a)+(b)+(d) anchor, shape, inclusivity
    def shape_and_inclusivity(self) -> date:
        self._anchor()
        d = _prev_weekday(self.today - timedelta(days=3))
        for attempt in range(3):
            self.incl = True
            w0 = self.get(d, d)
            if attempt == 0 and not self.rows_path_known:
                parsed = parse_rows(w0.body)
                self.rows_path, self.rows_path_known = parsed[0], True
                self._note_origin()
            a = self.get(d - _ONE_DAY, d)
            b = self.get(d, d + _ONE_DAY)
            first = next((g for g in (w0, b, a) if g.n), None)
            if first is None:
                d = _prev_weekday(d - _ONE_DAY)
                continue
            if not self.date_field:
                self._pick_date_field(first)
                if not self.date_field:
                    raise _Stop("no date field: no value of the first row looks like a date")
            c0, ad, bd = self.day_count(w0, d), self.day_count(a, d), self.day_count(b, d)
            if c0 == 0 and ad == 0 and bd == 0:
                d = _prev_weekday(d - _ONE_DAY)
                continue
            if c0 > 0 and ad == c0 and bd == c0:
                self.incl = True
            elif c0 == 0 and ad == 0 and bd > 0:
                self.incl = False
            else:
                raise _Stop(f"ambiguous inclusivity: day {d} has {c0} rows alone, {ad} in the window ending on it and "
                            f"{bd} in the window starting on it")
            self.findings["reference_day"] = d.isoformat()
            return d
        raise _Stop("no rows in the recent windows: no data to measure")

    def _anchor(self) -> None:
        if not self.anchored:
            self.anchored = True
            set_anchor = getattr(self.browser, "set_anchor", None)
            if set_anchor is not None:
                set_anchor(anchor_full(self.ep))

    def _note_origin(self) -> None:
        page = getattr(self.browser, "page_origin", None)
        origin = page() if page is not None else ""
        if origin:
            if origin != self.origin:
                self.out.append(f"- anchor landed on {origin}, not on {self.origin}: api_origin is {origin}")
            self.origin = self.api_origin = origin

    def _pick_date_field(self, got: _Got) -> None:
        row = got.rows[0] if got.rows and isinstance(got.rows[0], dict) else {}
        cands = [k for k, v in row.items() if parse_day(v) is not None]
        self.findings["fields"] = sorted(row)
        self.findings["date_candidates"] = cands
        self.date_field = self.ep.date_field if self.ep.date_field in cands else (cands[0] if cands else "")

    # ---- (c) ladder and cap
    def ladder(self, m0: date) -> None:
        counts = {w: self.win(m0, w).n for w in _LADDER}
        self.findings["ladder"] = counts
        ws = list(_LADDER)
        under = any(counts[b] < counts[a] for a in ws for b in ws if b > a)
        cap: int | None = None
        seen: dict[int, int] = {}
        for w in ([] if under else _LADDER):
            n = counts[w]
            if n > 0 and n in seen and seen[n] != w:
                cap = n
                break
            seen.setdefault(n, w)
        if cap is None and not under:
            for w in _LADDER[1:]:
                n = counts[w]
                if n in _ROUND:
                    h = (w + 1) // 2
                    ha, hb = self.win(m0, h).n, self.win(m0 + timedelta(days=h), w - h).n
                    if ha + hb > n:
                        cap = n
                        break
        self.findings["row_cap"] = cap
        self.findings["undercount"] = under
        if under:
            self.findings["max_window_days"] = self._complete_width(m0)
        else:
            limit = 0.8 * cap if cap else 0.5 * max(counts.values())
            self.findings["max_window_days"] = max([w for w in ws if counts[w] <= limit] or [1])
        usable = [counts[w] / w for w in ws if cap is None or counts[w] < cap]
        self.findings["rows_per_day"] = max(usable) if usable else None

    def _complete_width(self, m0: date) -> int:
        """Largest ladder width whose count equals the sum of its halves in three trials (R4c)."""
        for w in reversed(_LADDER[1:]):
            ok = True
            for i in range(3):
                t = m0 + timedelta(days=i * w)
                h = (w + 1) // 2
                if self.win(t, w).n != self.win(t, h).n + self.win(t + timedelta(days=h), w - h).n:
                    ok = False
                    break
            if ok:
                return w
        self.reasons.append("undercount: no window width above one day returns all its rows")
        return 1

    # ---- (e) earliest date
    def earliest(self, known: date) -> date:
        ep = self.ep
        s0 = ep.start_date
        bracket_lo: date | None = None
        first = s0
        if self.has_rows(s0):
            first = s0
            for k in range(1, 26):
                c = _add_years(s0, -k)
                if self.has_rows(c):
                    first = c
                else:
                    bracket_lo = c
                    break
        else:
            bracket_lo = s0
            found = None
            for y in range(s0.year + 1, self.today.year + 1):
                c = _add_years(s0, y - s0.year)
                if c > self.today:
                    break
                if self.has_rows(c):
                    found = c
                    break
                bracket_lo = c
            first = found if found is not None else known
        if bracket_lo is None:
            earliest = first
        else:
            earliest = self._refine(bracket_lo + timedelta(days=7), first)
        self._confirm_before(earliest)
        return earliest

    def _refine(self, lo: date, hi: date) -> date:
        chunks = _month_chunks(lo, hi)
        a, b = 0, len(chunks) - 1
        while a < b:
            mid = (a + b) // 2
            if self.get(*chunks[mid]).n > 0:
                b = mid
            else:
                a = mid + 1
        ca, cb = chunks[a]
        weeks, s = [], ca
        while s <= cb:
            weeks.append((s, min(s + timedelta(days=6), cb)))
            s += timedelta(days=7)
        a, b = 0, len(weeks) - 1
        while a < b:
            mid = (a + b) // 2
            if self.get(*weeks[mid]).n > 0:
                b = mid
            else:
                a = mid + 1
        return weeks[a][0]

    def _confirm_before(self, earliest: date) -> None:
        """One window a month before and the week right before the earliest date must both be empty."""
        for gap in (37, 7):
            s = earliest - timedelta(days=gap)
            if s < date(2000, 1, 1):
                return
            if self.get(s, s + timedelta(days=6)).n > 0:
                self.reasons.append(f"earliest date not confirmed: rows exist before {earliest}")
                return

    # ---- (f) time of day
    def _kind(self, got: _Got) -> str | None:
        vals = [r.get(self.date_field) for r in got.rows if isinstance(r, dict)]
        if not vals:
            return None
        t = sum(1 for v in vals if has_time(v))
        return "all" if t == len(vals) else "none" if t == 0 else "mixed"

    def time_of_day(self, earliest: date) -> str:
        bands, k = [], 0
        while True:
            s = _add_years(earliest, 5 * k)
            if s > self.today - timedelta(days=7):
                break
            bands.append(s)
            k += 1
        if not bands:
            bands = [earliest]
        kinds = [(s, self._kind(self.win(s, 7))) for s in bands]
        kinds = [(s, k) for s, k in kinds if k is not None]
        seq = [k for _, k in kinds]
        if not seq:
            return "none"
        if "mixed" in seq:
            return "mixed"
        if all(k == seq[0] for k in seq):
            return seq[0]
        i = seq.index("all")
        if "none" in seq[i:]:
            return "mixed"
        lo_year, hi_year = kinds[i - 1][0].year + 1, kinds[i][0].year
        while lo_year < hi_year:
            mid = (lo_year + hi_year) // 2
            if self._kind(self.win(date(mid, 1, 1), 7)) == "all":
                hi_year = mid
            else:
                lo_year = mid + 1
        return f"none_before:{date(hi_year, 1, 1).isoformat()}" if hi_year != kinds[i][0].year \
            else f"none_before:{kinds[i][0].isoformat()}"

    # ---- (h) determinism
    def determinism(self, m0: date) -> None:
        first = self.win(m0, 7)
        again = self.get(m0, m0 + timedelta(days=6), fresh=True)
        if first.body == again.body:
            return
        same = first.n == again.n and {_row_text(r) for r in first.rows} == {_row_text(r) for r in again.rows}
        if same:
            self.out.append("- the two bodies of the reference window differ only outside the rows (a wrapper field)")
        else:
            self.reasons.append("non-deterministic: the reference window returned different rows the second time")

    # ---- (g) shareholding
    def per_company(self) -> dict:
        syms = self.symbols
        if not syms:
            raise _Stop("no symbols supplied for per-company probing (config/symbols_backfill.csv)")
        pick = syms if len(syms) <= 3 else [syms[0], syms[len(syms) // 2], syms[-1]]
        depth, oldest, kinds, size = 0, None, [], 0
        for sym in pick:
            got = self.get(None, None, symbol=sym)
            if not self.rows_path_known:
                parsed = parse_rows(got.body)
                self.rows_path, self.rows_path_known = parsed[0], True
                self._note_origin()
            if got.n and not self.date_field:
                self._pick_date_field(got)
            depth, size = max(depth, got.n), max(size, len(got.body))
            for r in got.rows:
                dd = parse_day(r.get(self.date_field)) if isinstance(r, dict) else None
                if dd is not None and (oldest is None or dd < oldest):
                    oldest = dd
            k = self._kind(got)
            if k:
                kinds.append(k)
        if depth == 0 or oldest is None:
            raise _Stop("per-company probe returned no dated rows")
        tod = kinds[0] if kinds and all(x == kinds[0] for x in kinds) else "mixed"
        return {"depth": depth, "earliest": oldest, "time_of_day": tod, "size": size}

    # ---- the whole procedure
    def run(self) -> ProbeResult:
        ep, today = self.ep, self.today
        q_start = date(today.year, ((today.month - 1) // 3) * 3 + 1, 1)
        pm = q_start.month - 3
        m0 = date(q_start.year - (1 if pm < 1 else 0), pm + 12 if pm < 1 else pm, 1)
        mode, depth, symbol_source = "", None, ""
        res: dict = {}
        try:
            if ep.endpoint_id == "shareholding":
                self._anchor()
                listing = self.get(m0, m0 + timedelta(days=91))
                parsed = parse_rows(listing.body)
                self.rows_path, self.rows_path_known = parsed[0], True
                self._note_origin()
                mode = "per_quarter" if listing.n > 0 else "per_company"
                if mode == "per_company":
                    pc = self.per_company()
                    depth, symbol_source = pc["depth"], f"supplied list of {len(self.symbols)} symbols"
                    return self._result(mode, depth, symbol_source, earliest=pc["earliest"], tod=pc["time_of_day"],
                                        window=1, rpd=None, cap=None, under=False)
            d = self.shape_and_inclusivity()
            self.ladder(m0)
            earliest = self.earliest(d)
            tod = self.time_of_day(earliest)
            self.determinism(m0)
            f = self.findings
            return self._result(mode, depth, symbol_source, earliest=earliest, tod=tod, window=f["max_window_days"],
                                rpd=f["rows_per_day"], cap=f["row_cap"], under=f["undercount"])
        except _Stop as e:
            self.reasons.append(str(e))
            return self._result(mode, depth, symbol_source, earliest=ep.start_date, tod="", window=ep.max_window_days,
                                rpd=None, cap=self.findings.get("row_cap"), under=bool(self.findings.get("undercount")))

    def _result(self, mode, depth, symbol_source, *, earliest, tod, window, rpd, cap, under) -> ProbeResult:
        status = "needs_review" if self.reasons else "confirmed"
        return ProbeResult(
            endpoint_id=self.ep.endpoint_id, row_hash=self.ep.row_hash(),
            probed_at=self.clock().astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%fZ"),
            api_origin=self.api_origin, rows_path=self.rows_path, date_field=self.date_field,
            date_format=self.ep.date_format, to_inclusive=self.incl, row_cap=cap, max_safe_rows=self.max_n,
            max_window_days=int(window), earliest_date=earliest, time_of_day=tod, undercount=under, mode=mode,
            status=status, reasons=tuple(self.reasons), rows_per_day=rpd, depth=depth, symbol_source=symbol_source)


def probe_endpoint(ep: Endpoint, browser, *, clock: Callable[[], datetime], now_ist: date, store, report: list,
                   symbols: Sequence[str] | None = None, hostname: Callable[[], str] | None = None,
                   on_block: Callable[[str, int], None] | None = None, run_id: str = "probe",
                   max_requests: int = MAX_REQUESTS_PER_ENDPOINT) -> ProbeResult:
    """Measure one endpoint (R4). ``browser`` is the transport; ``report`` receives the human report lines.
    A block raises ``ProbeBlocked`` after ``on_block`` ran; the byte budget and window stops (BaseException) pass through."""
    p = _Probe(ep, browser, clock=clock, now_ist=now_ist, store=store, report=report, symbols=symbols,
               hostname=hostname or socket.gethostname, on_block=on_block, run_id=run_id, max_requests=max_requests)
    try:
        res = p.run()
    finally:
        report.extend(_section(ep, p))
    report.extend(_result_lines(res, p))
    return res


def _section(ep: Endpoint, p: _Probe) -> list[str]:
    lines = [f"## {ep.endpoint_id}", "", f"- page: {anchor_full(ep)}", f"- requests: {p.n}"]
    if p.findings.get("fields"):
        lines.append("- fields of the first row: " + ", ".join(p.findings["fields"]))
        lines.append("- date field candidates: " + (", ".join(p.findings.get("date_candidates", [])) or "none"))
    if p.findings.get("ladder"):
        lines.append("- ladder (window days: rows): " + ", ".join(f"{w}: {n}" for w, n in p.findings["ladder"].items()))
    return lines


def _result_lines(r: ProbeResult, p: _Probe) -> list[str]:
    rows = [("api_origin", r.api_origin), ("rows_path", repr(r.rows_path)), ("date_field", r.date_field),
            ("date_format", r.date_format), ("to_inclusive", r.to_inclusive), ("row_cap", r.row_cap),
            ("max_safe_rows", r.max_safe_rows), ("max_window_days", r.max_window_days),
            ("rows_per_day", r.rows_per_day), ("earliest_date", r.earliest_date), ("time_of_day", r.time_of_day),
            ("undercount", r.undercount), ("mode", r.mode or "-"), ("depth", r.depth), ("status", r.status)]
    out = ["", "| measurement | value |", "|---|---|"] + [f"| {k} | {v} |" for k, v in rows]
    out += ["", "decision: " + ("confirmed" if r.status == "confirmed" else "needs_review: " + "; ".join(r.reasons)),
            "", "raw manifest keys (source backfill_probe):"] + p.req_lines + [""]
    return out


def write_probe_report(cfg: Config, ist_day: date, now: datetime, lines: list[str]) -> Path:
    d = Path(cfg.zones["reports"]) / "backfill"
    d.mkdir(parents=True, exist_ok=True)
    path = d / f"PROBE_{ist_day.isoformat()}.md"
    head = [f"# Backfill probe {ist_day.isoformat()}", "",
            f"Made at {now.astimezone(timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ')}. History is what the endpoint serves today "
            "(restatements, corrections and delisted names may be missing). Facts below are measurements, not guarantees.",
            ""]
    path.write_text("\n".join(head + lines) + "\n", encoding="utf-8")
    return path
