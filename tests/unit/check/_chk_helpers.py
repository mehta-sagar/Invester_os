"""Shared helpers for S0-T5 (check runner) tests. investos imports happen inside functions only,
so every test fails individually (not at collection) while investos.check is absent."""
from __future__ import annotations

import hashlib
import io
import json
import os
import re
from datetime import datetime, timedelta, timezone
from pathlib import Path

ZONE_KEYS = ["raw", "manifests", "canonical", "ledger", "reports", "registry"]
ZONE_DIRS = ["data/raw", "data/manifests", "data/canonical", "data/ledger", "reports", "registry"]

VALID_TOML = """\
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

[rate_limits.hosts]
"www.bseindia.com" = 0.35
"""

BOOTSTRAP_SQL = (
    "CREATE TABLE meta(key VARCHAR PRIMARY KEY, value VARCHAR NOT NULL);\n"
    "INSERT INTO meta VALUES ('schema_family','investos');\n"
)
SECOND_SQL = "CREATE TABLE t1(x INTEGER);\n"

IST = timezone(timedelta(hours=5, minutes=30))
FETCHED = datetime(2026, 11, 21, 15, 0, 0, tzinfo=IST)


def make_repo(root: Path, *, bootstrap: bool = True) -> Path:
    root.mkdir(parents=True, exist_ok=True)
    (root / "pyproject.toml").write_text("[project]\nname='x'\n")
    for z in ZONE_DIRS:
        (root / z).mkdir(parents=True, exist_ok=True)
    mig = root / "db" / "migrations"
    mig.mkdir(parents=True, exist_ok=True)
    if bootstrap:
        (mig / "0000_bootstrap.sql").write_text(BOOTSTRAP_SQL)
        (mig / "0001_second.sql").write_text(SECOND_SQL)
    (root / "config").mkdir(exist_ok=True)
    (root / "config" / "investos.toml").write_text(VALID_TOML)
    return root


def put(cfg, source: str, data: bytes, **over):
    from investos.acquire.manifest import ManifestStore
    kw = dict(url="https://example.test/f", source_version="v1", logical_key="k-" + data.decode("latin1")[:20],
              run_id="run-1", fetched_at=FETCHED)
    kw.update(over)
    return ManifestStore.from_config(cfg).store(source, data, **kw)


def raw_path(cfg, source: str, data: bytes) -> Path:
    s = hashlib.sha256(data).hexdigest()
    return cfg.zones["raw"] / source / s[:2] / s


def tamper(path: Path, new: bytes, mode: int = 0o444) -> None:
    os.chmod(path, 0o644)
    path.write_bytes(new)
    os.chmod(path, mode)


def digest(*roots: Path) -> dict:
    """relpath-keyed (kind, mode, size, sha256) for every file/dir under each root."""
    out = {}
    for root in roots:
        for p in [root, *sorted(root.rglob("*"))]:
            st = p.lstat()
            h = None
            if p.is_file() and not p.is_symlink():
                h = hashlib.sha256(p.read_bytes()).hexdigest()
            out[str(p)] = (st.st_mode, st.st_size, h)
    return out


def snap(*roots: Path) -> dict:
    """path -> (mode, mtime_ns, size) for every file and directory under roots (incl. roots)."""
    out = {}
    for root in roots:
        for p in [root, *sorted(root.rglob("*"))]:
            st = p.lstat()
            out[str(p)] = (st.st_mode, st.st_mtime_ns, st.st_size)
    return out


def canonical_dump(obj) -> str:
    return json.dumps(obj, sort_keys=True, separators=(",", ":"), ensure_ascii=False) + "\n"


def report_path(cfg, run_id: str) -> Path:
    return cfg.zones["reports"] / "checks" / f"{run_id}.json"


def read_report(cfg, run_id: str) -> dict:
    return json.loads(report_path(cfg, run_id).read_text(encoding="utf-8"))


def rows(out: str) -> dict:
    """Parse the stdout table -> {name: (status, line)} (header and OK/FAILED lines excluded)."""
    res = {}
    for ln in out.splitlines():
        parts = ln.split()
        if not parts or parts[0] == "NAME" or parts[0] == "OK" or ln.startswith("FAILED"):
            continue
        if len(parts) >= 2:
            res[parts[0]] = (parts[1], ln)
    return res


def last_line(out: str) -> str:
    return [ln for ln in out.splitlines() if ln.strip()][-1].strip()


def by_name(report: dict) -> dict:
    return {r["name"]: r for r in report["results"]}


def text_of(result) -> str:
    return result.message + "\n" + "\n".join(result.details)


# ---- fake checks -------------------------------------------------------

def ok_check(reg, name: str, counter: list | None = None, message: str = "fine"):
    from investos.check.registry import CheckResult

    def fn(ctx):
        if counter is not None:
            counter.append(name)
        return CheckResult(True, message)
    fn.__name__ = f"check_{name}"
    return reg.check(name)(fn)


def fail_check(reg, name: str, counter: list | None = None, message: str = "broken", details=()):
    from investos.check.registry import CheckResult

    def fn(ctx):
        if counter is not None:
            counter.append(name)
        return CheckResult(False, message, tuple(details))
    fn.__name__ = f"check_{name}"
    return reg.check(name)(fn)


def flag_check(reg, name: str = "c", counter: list | None = None):
    """Passes iff canonical/flag.txt exists and does not contain BAD (reads ctx.cfg only)."""
    from investos.check.registry import CheckResult

    def fn(ctx):
        if counter is not None:
            counter.append(name)
        p = ctx.cfg.zones["canonical"] / "flag.txt"
        if not p.exists():
            return CheckResult(False, "flag missing")
        return CheckResult("BAD" not in p.read_text(), "flag checked")
    fn.__name__ = f"check_{name}"
    return reg.check(name)(fn)


def flag_setup(ctx):
    (ctx.cfg.zones["canonical"] / "flag.txt").write_text("OK")


def flag_plant(ctx):
    (ctx.cfg.zones["canonical"] / "flag.txt").write_text("BAD")


def with_flag_plant(reg, name: str = "c", plant=flag_plant, setup=flag_setup):
    reg.plant_for(name, setup=setup)(plant)
