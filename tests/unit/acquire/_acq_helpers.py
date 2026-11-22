"""Shared constants/helpers for S0-T3 tests. investos imports happen inside functions only."""
from __future__ import annotations

import hashlib
import json
import os
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

IST = timezone(timedelta(hours=5, minutes=30))
FETCHED = datetime(2026, 11, 21, 15, 0, 0, tzinfo=IST)          # == 2026-11-21T09:30:00Z
HELLO_SHA = "2cf24dba5fb0a30e26e83b2ac5b9e29e1b161e5c1fa7425e73043362938b9824"
EMPTY_SHA = "e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855"
FIELDS = ["sha256", "size", "url", "fetched_at", "source", "source_version", "logical_key", "run_id"]


def sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def put(st, source: str, data: bytes, **over):
    kw = dict(url="https://example.test/f", source_version="v1", logical_key="2026-11-20",
              run_id="run-1", fetched_at=FETCHED, access_mode="honest",
              request_headers={"User-Agent": "InvestOS/test", "Accept-Encoding": "identity"},
              final_url=over.get("url", "https://example.test/f"), fetch_host="test-host")
    kw.update(over)
    return st.store(source, data, **kw)


def raw_file(raw_root: Path, source: str, data_or_sha) -> Path:
    s = data_or_sha if isinstance(data_or_sha, str) else sha(data_or_sha)
    return raw_root / source / s[:2] / s


def manifest_path(man_root: Path, source: str) -> Path:
    return man_root / f"{source}.jsonl"


def canon(obj: dict) -> str:
    return json.dumps(obj, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


def lines_of(path: Path) -> list[str]:
    return path.read_bytes().decode("utf-8").splitlines(keepends=True)


def snap(root: Path) -> dict:
    """relpath -> (mode, mtime_ns, size) for every file and directory under root (root excluded)."""
    out = {}
    for p in sorted(root.rglob("*")):
        st = p.lstat()
        out[str(p.relative_to(root))] = (st.st_mode, st.st_mtime_ns, st.st_size)
    return out


def raw_files(raw_root: Path) -> list[Path]:
    return sorted(p for p in raw_root.rglob("*") if p.is_file())


def tamper(path: Path, new: bytes, mode: int = 0o444) -> None:
    os.chmod(path, 0o644)
    path.write_bytes(new)
    os.chmod(path, mode)


def for_path(problems, path: Path):
    return [p for p in problems if Path(p.path).name == path.name]


def kinds(problems) -> set:
    return {p.kind for p in problems}


# ---- multiprocessing workers (module level so spawn can import them) ----

def worker_store(raw: str, man: str, source: str, worker_id: int, n: int) -> None:
    from investos.acquire.manifest import ManifestStore
    st = ManifestStore(Path(raw), Path(man))
    for i in range(n):
        put(st, source, f"payload-{worker_id}-{i}".encode(), run_id=f"w{worker_id}", logical_key=f"k{worker_id}-{i}")


def worker_hold_lock(lock_path: str, ready, hold_s: float, released_q) -> None:
    import fcntl
    with open(lock_path, "a+") as fh:
        fcntl.flock(fh, fcntl.LOCK_EX)
        ready.set()
        time.sleep(hold_s)
        released_q.put(time.time())      # recorded before the lock is released
        fcntl.flock(fh, fcntl.LOCK_UN)


def worker_timed_store(raw: str, man: str, source: str, go, out_q) -> None:
    from investos.acquire.manifest import ManifestStore
    st = ManifestStore(Path(raw), Path(man))
    go.wait(30)
    put(st, source, f"timed-{source}".encode())
    out_q.put((source, time.time()))
