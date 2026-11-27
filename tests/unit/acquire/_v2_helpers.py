"""Helpers for S1-T2 (manifest schema v2) tests. investos imports happen inside functions only."""
from __future__ import annotations

import json
import os
import time
from pathlib import Path

from _acq_helpers import FETCHED, canon, put, sha

V2_FIELDS = {"sha256", "size", "url", "fetched_at", "source", "source_version", "logical_key", "run_id",
             "schema", "access_mode", "request_headers", "final_url", "fetch_host"}
V1_FIELDS = V2_FIELDS - {"schema", "access_mode", "request_headers", "final_url", "fetch_host"}
MODES = ("honest", "real_browser", "scoped_exception")


def v1_obj(data: bytes, *, source="bse", key="2026-11-20", url="https://example.test/f") -> dict:
    return {"sha256": sha(data), "size": len(data), "url": url, "fetched_at": "2026-11-21T09:30:00.000000Z",
            "source": source, "source_version": "v1", "logical_key": key, "run_id": "run-1"}


def v2_obj(data: bytes, *, source="bse", key="2026-11-20", **over) -> dict:
    o = v1_obj(data, source=source, key=key)
    o.update({"schema": 2, "access_mode": "honest",
              "request_headers": {"Accept-Encoding": "identity", "User-Agent": "u"},
              "final_url": "https://example.test/f", "fetch_host": "mac-1"})
    o.update(over)
    return o


def put_raw(raw_root: Path, source: str, data: bytes) -> None:
    """Create the raw file for hand-built manifest lines (0444, content-addressed)."""
    s = sha(data)
    d = raw_root / source / s[:2]
    d.mkdir(parents=True, exist_ok=True)
    p = d / s
    if not p.exists():
        p.write_bytes(data)
        os.chmod(p, 0o444)


def write_lines(man_root: Path, source: str, lines: list[str], *, raw_root: Path | None = None,
                payloads: list[bytes] | None = None) -> Path:
    """Write a manifest file from exact line texts (each gets a trailing newline)."""
    mp = man_root / f"{source}.jsonl"
    mp.write_bytes(("".join(l + "\n" for l in lines)).encode("utf-8"))
    for b in payloads or []:
        put_raw(raw_root, source, b)
    return mp


def put2(st, source, data, **over):
    """store() with explicit v2 arguments (same defaults as _acq_helpers.put)."""
    return put(st, source, data, **over)


def bad_line_numbers(problems) -> list[int | None]:
    return sorted(p.line for p in problems if p.kind == "bad_line")


# ---- multiprocessing workers (module level so spawn can import them) ----

def worker_v2_writer(raw: str, man: str, source: str, n: int, pad: int) -> None:
    from investos.acquire.manifest import ManifestStore
    st = ManifestStore(Path(raw), Path(man))
    hdr = {"User-Agent": "u", "X-Pad": "p" * pad}
    for i in range(n):
        st.store(source, f"v2-payload-{i}".encode(), url="https://x.test/f", source_version="v",
                 logical_key=f"k{i:04d}", run_id="w", fetched_at=FETCHED, access_mode="honest",
                 request_headers=hdr, final_url="https://x.test/f", fetch_host="h")


def worker_v2_reader(raw: str, man: str, source: str, stop, q) -> None:
    from investos.acquire.manifest import ManifestCorruptError, ManifestStore
    st = ManifestStore(Path(raw), Path(man))
    reads = errs = 0
    while not stop.is_set():
        for fn in (lambda: st.get_by_key(source, "k0000"), lambda: st.entries(source)):
            try:
                fn()
                reads += 1
            except ManifestCorruptError:
                errs += 1
    q.put((reads, errs))
