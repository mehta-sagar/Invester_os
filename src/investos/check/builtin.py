"""Built-in checks manifest / raw / migrations, each with a registered plant (spec R10-R14)."""
from __future__ import annotations

import hashlib
import os
from datetime import datetime, timezone
from pathlib import Path

from investos.acquire.manifest import ManifestStore
from investos.core.config import Config
from investos.core.db import MigrationError, open_db
from investos.core.names import SOURCE_RE as _SOURCE_RE
from investos.core.db import migration_problems

from .registry import CheckContext, CheckResult, check, plant_for

_MANIFEST_KINDS = frozenset({"bad_line", "missing_file", "hash_mismatch", "size_mismatch"})
_RAW_KINDS = frozenset({"writable_file", "misplaced_file", "symlink"})
_FIXED_TIME = datetime(2026, 1, 1, tzinfo=timezone.utc)


def _sources(cfg: Config) -> list[str]:
    names: set[str] = set()
    man, raw = cfg.zones["manifests"], cfg.zones["raw"]
    if man.is_dir():
        names.update(p.name[:-6] for p in man.iterdir()
                     if p.name.endswith(".jsonl") and _SOURCE_RE.fullmatch(p.name[:-6]))
    if raw.is_dir():
        names.update(p.name for p in raw.iterdir() if p.is_dir() and _SOURCE_RE.fullmatch(p.name))
    return sorted(names)


def _count_lines(cfg: Config, sources: list[str]) -> int:
    n = 0
    for s in sources:
        try:
            data = (cfg.zones["manifests"] / f"{s}.jsonl").read_bytes()
        except OSError:
            continue
        n += data.count(b"\n") + (1 if data and not data.endswith(b"\n") else 0)
    return n


def _problems(cfg: Config, kinds: frozenset[str]):
    store = ManifestStore.from_config(cfg)
    sources = _sources(cfg)
    return sources, [p for p in store.verify() if p.kind in kinds]


def _summary(cfg: Config, probs) -> str:
    """'<n> problem(s): <kinds> [<first names>]', one line, within the 200 character message cap."""
    kinds = ", ".join(sorted({p.kind for p in probs}))
    roots = [cfg.zones["raw"], cfg.zones["manifests"]]
    names = []
    for p in probs[:3]:
        for r in roots:
            try:
                names.append(str(Path(p.path).relative_to(r)))
                break
            except ValueError:
                continue
        else:
            names.append(Path(p.path).name)
    more = ", ..." if len(probs) > 3 else ""
    return f"{len(probs)} problem(s): {kinds} [{', '.join(names)}{more}]"


def _fmt_problem(p) -> str:
    where = f" line {p.line}" if p.line is not None else ""
    return f"{p.kind} {p.path}{where}: {p.detail}"


@check("manifest")
def check_manifest(ctx: CheckContext) -> CheckResult:
    sources, probs = _problems(ctx.cfg, _MANIFEST_KINDS)
    if probs:
        return CheckResult(False, _summary(ctx.cfg, probs), tuple(_fmt_problem(p) for p in probs))
    return CheckResult(True, f"{len(sources)} sources, {_count_lines(ctx.cfg, sources)} lines OK")


@check("raw")
def check_raw(ctx: CheckContext) -> CheckResult:
    sources, probs = _problems(ctx.cfg, _RAW_KINDS)
    if probs:
        return CheckResult(False, _summary(ctx.cfg, probs), tuple(_fmt_problem(p) for p in probs))
    return CheckResult(True, f"{len(sources)} sources, raw files read-only and in place")


@check("migrations")
def check_migrations(ctx: CheckContext) -> CheckResult:
    cfg = ctx.cfg
    if not cfg.db_path.exists():
        if any(cfg.migrations_dir.glob("*.sql")):
            return CheckResult(False, "database missing", (str(cfg.db_path),))
        return CheckResult(True, "no migrations and no database")
    con = open_db(cfg, read_only=True)
    try:
        problems = migration_problems(con, cfg.migrations_dir)
    finally:
        con.close()
    if problems:
        return CheckResult(False, f"{len(problems)} migration problem(s)", tuple(problems))
    return CheckResult(True, "migrations consistent with database")


# ---- plants ---------------------------------------------------------------

_BLOBS = (b"planted-fixture-alpha", b"planted-fixture-beta")


def _store_blobs(ctx: CheckContext, source: str, blobs) -> ManifestStore:
    store = ManifestStore.from_config(ctx.cfg)
    for i, b in enumerate(blobs):
        store.store(source, b, url="planted://fixture", source_version="plant",
                    logical_key=f"plant-{i}", run_id=ctx.run_id, fetched_at=_FIXED_TIME,
                    access_mode="honest", request_headers={},
                    final_url="https://planted.invalid/fixture", fetch_host="planted-host")
    return store


def _manifest_setup(ctx: CheckContext) -> None:
    _store_blobs(ctx, "plant_manifest", _BLOBS)


@plant_for("manifest", setup=_manifest_setup, zones={"raw", "manifests"})
def _manifest_plant(ctx: CheckContext) -> None:
    store = ManifestStore.from_config(ctx.cfg)
    p = store.raw_path("plant_manifest", hashlib.sha256(_BLOBS[0]).hexdigest())
    os.chmod(p, 0o644)
    data = bytearray(p.read_bytes())
    data[0] ^= 0x01
    p.write_bytes(bytes(data))
    os.chmod(p, 0o444)


def _raw_setup(ctx: CheckContext) -> None:
    _store_blobs(ctx, "plant_raw", _BLOBS[:1])


@plant_for("raw", setup=_raw_setup, zones={"raw", "manifests"})
def _raw_plant(ctx: CheckContext) -> None:
    p = ManifestStore.from_config(ctx.cfg).raw_path("plant_raw", hashlib.sha256(_BLOBS[0]).hexdigest())
    os.chmod(p, 0o644)


def _migrations_setup(ctx: CheckContext) -> None:
    mdir = ctx.cfg.migrations_dir
    if not any(mdir.glob("*.sql")):
        (mdir / "0000_planted_fixture.sql").write_text("CREATE TABLE planted_fixture(x INTEGER);\n")
    open_db(ctx.cfg).close()


@plant_for("migrations", setup=_migrations_setup, zones={"canonical", "migrations"})
def _migrations_plant(ctx: CheckContext) -> None:
    target = sorted(ctx.cfg.migrations_dir.glob("*.sql"))[0]
    with open(target, "ab") as fh:
        fh.write(b"-- planted edit\n")


from . import calendar_check as _calendar_check  # noqa: E402,F401  registers check `calendar` and its plant

from . import backfill_check as _backfill_check  # noqa: E402,F401  registers check `backfill` and its plant
