"""DuckDB open + forward-only, hash-pinned SQL migrations (spec R13-R19)."""
from __future__ import annotations

import hashlib
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable

import duckdb

from . import log
from .config import Config, parse_size_text

__all__ = ["MigrationError", "open_db", "apply_migrations", "migration_problems", "parse_size_text"]

_NAME_RE = re.compile(r"^(\d{4})_[a-z0-9_]+\.sql$")


class MigrationError(Exception):
    pass


def _sha(p: Path) -> str:
    return hashlib.sha256(p.read_bytes()).hexdigest()


def _has_table(con) -> bool:
    return con.execute("select count(*) from information_schema.tables "
                       "where table_name='schema_migrations'").fetchone()[0] > 0


def _ensure_table(con) -> None:
    con.execute("""CREATE TABLE IF NOT EXISTS schema_migrations(
        version VARCHAR PRIMARY KEY, name VARCHAR NOT NULL,
        sha256 VARCHAR NOT NULL, applied_at TIMESTAMP NOT NULL)""")


def _audit(con, migrations_dir: Path) -> tuple[list[str], list[Path]]:
    """Return (problems, pending files in order). Writes nothing."""
    problems: list[str] = []
    if not Path(migrations_dir).is_dir():
        return [f"migrations dir missing: {migrations_dir}"], []
    files = {p.name: p for p in sorted(Path(migrations_dir).glob("*.sql")) if p.is_file()}
    applied = (con.execute("select version, name, sha256 from schema_migrations order by version").fetchall()
               if _has_table(con) else [])
    # R16 tamper
    for _v, name, sha in applied:
        if name not in files:
            problems.append(f"applied migration {name} is missing from {migrations_dir}")
        elif _sha(files[name]) != sha:
            problems.append(f"applied migration {name} has changed (sha256 mismatch)")
    # R14 discovery
    by_ver: dict[str, list[str]] = {}
    for name in files:
        m = _NAME_RE.match(name)
        if not m:
            problems.append(f"migration file {name} does not match NNNN_slug.sql")
            continue
        by_ver.setdefault(m.group(1), []).append(name)
    for v, names in by_ver.items():
        if len(names) > 1:
            problems.append(f"duplicate migration version {v}: {', '.join(names)}")
    expected = [f"{i:04d}" for i in range(len(by_ver))]
    if sorted(by_ver) != expected:
        problems.append(f"migration versions not contiguous from 0000: found {sorted(by_ver)}")
    # R17 ordering
    applied_names = {name for _v, name, _s in applied}
    max_applied = max((v for v, _n, _s in applied), default=None)
    pending: list[Path] = []
    for name, p in files.items():
        m = _NAME_RE.match(name)
        if not m or name in applied_names:
            continue
        if max_applied is not None and m.group(1) < max_applied:
            problems.append(f"migration {name} is older than applied version {max_applied} (no back-filling)")
        pending.append(p)
    return problems, pending


def migration_problems(con, migrations_dir: Path) -> list[str]:
    return _audit(con, migrations_dir)[0]


def apply_migrations(con, migrations_dir: Path, *,
                     clock: Callable[[], datetime] | None = None) -> list[str]:
    problems, pending = _audit(con, migrations_dir)
    if problems:
        raise MigrationError("; ".join(problems))
    _ensure_table(con)
    applied_now: list[str] = []
    for p in pending:
        sql = p.read_bytes()
        now = (clock() if clock else datetime.now(timezone.utc))
        if now.tzinfo is not None:
            now = now.astimezone(timezone.utc).replace(tzinfo=None)
        con.execute("BEGIN")
        try:
            con.execute(sql.decode("utf-8"))
            con.execute("INSERT INTO schema_migrations VALUES (?,?,?,?)",
                        [p.name[:4], p.name, hashlib.sha256(sql).hexdigest(), now])
            con.execute("COMMIT")
        except Exception as e:
            try:
                con.execute("ROLLBACK")
            except duckdb.Error:
                pass
            raise MigrationError(f"migration {p.name} failed and was rolled back: {e}") from e
        applied_now.append(p.name)
    return applied_now


def _sql_str(text: str) -> str:
    return "'" + text.replace("'", "''") + "'"


def _apply_caps(con, cfg: Config) -> None:
    """R22: create the temp directory, then SET the four caps in the fixed order. Raises MigrationError."""
    d = cfg.duckdb
    try:
        Path(d.temp_directory).mkdir(parents=False, exist_ok=True)
    except OSError as e:
        raise MigrationError(f"cannot create duckdb temp directory {d.temp_directory}: {e}") from e
    for name, value in (("memory_limit", _sql_str(d.memory_limit)), ("threads", str(int(d.threads))),
                        ("preserve_insertion_order", "false"),
                        ("temp_directory", _sql_str(str(d.temp_directory)))):
        try:
            con.execute(f"SET {name} = {value}")
        except Exception as e:
            raise MigrationError(f"cannot apply duckdb setting {name}: {e}") from e


def _log_caps(con) -> None:
    vals = {n: con.execute("select current_setting(?)", [n]).fetchone()[0]
            for n in ("memory_limit", "threads", "temp_directory", "preserve_insertion_order")}
    log.get_logger("core.db").info("duckdb_settings", **vals)


def open_db(cfg: Config, *, read_only: bool = False, apply: bool = True) -> duckdb.DuckDBPyConnection:
    path = cfg.db_path
    if read_only:
        if not path.exists():
            raise MigrationError(f"database file does not exist: {path}")
        apply = False
    try:
        con = duckdb.connect(str(path), read_only=read_only)
    except duckdb.IOException as e:
        if "lock" in str(e).lower():
            raise MigrationError("database locked") from e
        raise MigrationError(f"cannot open database {path}: {e}") from e
    try:
        _apply_caps(con, cfg)
        if apply:
            apply_migrations(con, cfg.migrations_dir)
        _log_caps(con)
    except BaseException:
        con.close()
        raise
    return con
