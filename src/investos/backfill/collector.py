"""The per-endpoint collector class (spec collect_backfill R1, R8a, R11, R17).

A thin `Collector` subclass of the unchanged base: it stores the bytes of a window exactly as the transport returned
them. ``check_body`` looks inside a body only to confirm it is JSON of the probed shape, to count the rows at
``rows_path`` and to read each row's date field for the in-file date check; it never changes, drops or de-duplicates a row."""
from __future__ import annotations

import json
from typing import ClassVar, Iterable, Mapping, Sequence

from investos.acquire.base import AccessMode, BodyVerdict, Collector, Outcome, Response, RunRange
from investos.backfill.endpoints import Endpoint, build_url, parse_day
from investos.backfill.ledger import WindowKey
from investos.backfill.planner import symbol_parts

__all__ = ["BackfillCollector", "make_collector_class", "rows_of", "NOT_JSON"]

NOT_JSON = "not json rows"


def rows_of(body: bytes, rows_path: str) -> list:
    """The row list of a body at ``rows_path`` ("" is a top-level list). ValueError when the body has none."""
    try:
        obj = json.loads(body)
    except (ValueError, UnicodeError):
        raise ValueError(f"{NOT_JSON}: the body is not JSON") from None
    cur = obj
    for part in [p for p in rows_path.split(".") if p]:
        cur = cur.get(part) if isinstance(cur, dict) else None
    if not isinstance(cur, list):
        raise ValueError(f"{NOT_JSON}: no row list " + (f"at {rows_path!r}" if rows_path else "at the top level"))
    return cur


class BackfillCollector(Collector):
    _ep: ClassVar[Endpoint]
    _probe: ClassVar

    def __init__(self, *, windows: Sequence[WindowKey], origin: str | None = None,
                 symbols: Sequence[str] | None = None, **kw) -> None:
        super().__init__(**kw)
        self._wins: dict[str, WindowKey] = {w.logical_key(): w for w in windows}
        self._origin = origin or self._probe.api_origin
        self._by_part = symbol_parts(symbols) if symbols else {}
        if self._probe.mode == "per_company" and any(w.part not in self._by_part for w in windows):
            raise ValueError("per_company windows need the symbol list they were planned from")

    def keys(self, run_range: RunRange) -> Iterable[str]:
        return list(self._wins)

    def url(self, key: str) -> str:
        w, p = self._wins[key], self._probe
        sym = self._by_part[w.part] if p.mode == "per_company" else None
        return build_url(self._ep, self._origin, w.start, w.end, to_inclusive=p.to_inclusive,
                         date_format=p.date_format, symbol=sym)

    def check_body(self, key: str, resp: Response) -> BodyVerdict:
        w, p = self._wins[key], self._probe
        try:
            rows = rows_of(resp.body, p.rows_path)
        except ValueError as e:
            return BodyVerdict(Outcome.UNREADABLE, str(e))
        if not rows:
            return BodyVerdict(Outcome.FETCHED_EMPTY, record_count=0)
        if p.date_field and p.mode != "per_company":
            for row in rows:
                day = parse_day(row.get(p.date_field)) if isinstance(row, Mapping) else None
                if day is None:
                    return BodyVerdict(Outcome.UNREADABLE, f"row without a usable {p.date_field} value")
                if not w.start <= day <= w.end:
                    return BodyVerdict(Outcome.UNREADABLE,
                                       f"date outside window: {day} not in {w.start}..{w.end}")
        return BodyVerdict(Outcome.FETCHED, record_count=len(rows))


def make_collector_class(ep: Endpoint, probe) -> type[Collector]:
    """One class per endpoint: source `backfill_<id>`, real_browser, page_limit = the probed row cap (None when none)."""
    return type(f"Backfill_{ep.endpoint_id}", (BackfillCollector,), {
        "source": ep.source, "source_version": "backfill-v1", "access_mode": AccessMode.REAL_BROWSER,
        "page_limit": probe.row_cap, "archive": False, "date_keyed": False, "_ep": ep, "_probe": probe})
