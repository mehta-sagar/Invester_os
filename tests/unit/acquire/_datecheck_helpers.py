"""Helpers for S1-T5 (in-file date check, collect_base amendment 2). investos imports happen inside functions.

The demo adapter reads a `DATE1` column (dd-mmm-yyyy) from a CSV body; header-only bodies carry no date.
Test files pull fixtures in with `from _collector_helpers import env, logs  # noqa: F401`.
"""
from __future__ import annotations

from datetime import date, datetime

from _collector_helpers import SOURCE, demo_class, make_zip, resp, url_of  # noqa: F401

FRI, SAT, SUN, MON = "2026-11-20", "2026-11-21", "2026-11-22", "2026-11-23"
HEADER = b"DATE1,SYMBOL,CLOSE\n"
WAIVER = "source files carry no date at all"             # >= 20 characters


def d_text(iso: str) -> str:
    return datetime.strptime(iso, "%Y-%m-%d").strftime("%d-%b-%Y")      # 2026-11-20 -> 20-Nov-2026


def dated_body(iso: str, rows: int = 2) -> bytes:
    out = HEADER
    for i in range(rows):
        out += f"{d_text(iso)},SYM{i},{100 + i}\n".encode()
    return out


def blank_date_body(rows: int = 2) -> bytes:
    return HEADER + b"".join(f",SYM{i},{100 + i}\n".encode() for i in range(rows))


def date_verdict(self, key, r):
    """check_body for the DATE1 demo format (and plain zip bodies)."""
    from investos.acquire.base import BodyVerdict, Outcome
    b = r.body
    if b == b"NO FILE":
        return BodyVerdict(Outcome.MISSING_HOLIDAY, "no file for key")
    if b.startswith(b"PK"):
        return BodyVerdict(Outcome.FETCHED)
    if b.startswith(b"DATE1,"):
        if len(b.splitlines()) > 1:
            return BodyVerdict(Outcome.FETCHED)
        return BodyVerdict(Outcome.FETCHED_EMPTY, "header only")
    return BodyVerdict(Outcome.UNREADABLE, "check failed: unrecognised body")


def read_date1(self, key, member, body):
    """Adapter-style reader of the DATE1 column. Records every call in `self.calls`."""
    from investos.acquire.base import DateEvidence
    self.calls.append((key, member))
    lines = body.decode("utf-8").splitlines()
    if not lines or not lines[0].startswith("DATE1,"):
        return None                                                # README-like member: carries no date
    found, rows = set(), 0
    for line in lines[1:]:
        rows += 1
        cell = line.split(",")[0].strip()
        if cell:
            found.add(datetime.strptime(cell, "%d-%b-%Y").date())
    return DateEvidence(frozenset(found), "DATE1", rows)


def evidence(*isos: str, field: str = "DATE1", rows: int = 1):
    from investos.acquire.base import DateEvidence
    return DateEvidence(frozenset(date.fromisoformat(i) for i in isos), field, rows)


def date_class(**attrs):
    """DemoCollector with date_keyed = True and the DATE1 reader. `calls` is a per-class list of (key, member)."""
    ns = {"date_keyed": True, "internal_dates": read_date1, "verdict_fn": date_verdict, "calls": []}
    ns.update(attrs)
    cls = demo_class(**ns)
    return cls


def waived_class(**attrs):
    ns = {"date_keyed": True, "date_check_waiver": WAIVER, "verdict_fn": date_verdict, "calls": []}
    ns.update(attrs)
    return demo_class(**ns)


def scripted(env, key: str, body: bytes, status: int = 200, url: str | None = None):
    env.transport.set(url or url_of(key), resp(status, body))


def manifest_keys(env) -> list[str]:
    import json
    return sorted(json.loads(l)["logical_key"] for l in env.manifest_lines())
