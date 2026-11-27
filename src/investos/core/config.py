"""Strict TOML config loader with env overrides (spec core_config_log_db R1-R8)."""
from __future__ import annotations

import math
import os
import re
import shutil
import subprocess
import tomllib
from dataclasses import dataclass, field
from decimal import Decimal
from pathlib import Path
from typing import Any, Mapping
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from investos.core.names import SOURCE_RE

ZONE_KEYS = ("raw", "manifests", "canonical", "ledger", "reports", "registry")
_ENV_RE = re.compile(r"^INVESTOS_[A-Z0-9_]+__[A-Z0-9_]+$")
_OVERRIDABLE = {
    ("general", "timezone"): str,
    **{("paths", k): str for k in ZONE_KEYS},
    ("db", "migrations_dir"): str,
    ("rate_limits", "default_interval_s"): float,
    ("duckdb", "memory_limit"): str,
    ("duckdb", "threads"): int,
    ("duckdb", "temp_directory"): str,
}
_SIZE_RE = re.compile(r"([0-9]+(?:\.[0-9]+)?) ?(KB|MB|GB|TB|KiB|MiB|GiB|TiB)", re.ASCII | re.IGNORECASE)
_UNITS = {"kb": 10**3, "mb": 10**6, "gb": 10**9, "tb": 10**12,
          "kib": 2**10, "mib": 2**20, "gib": 2**30, "tib": 2**40}
_MIN_MEMORY_BYTES = 64 * 2**20
DEFAULT_MEMORY_LIMIT = "5GB"
DEFAULT_THREADS = 4
DUCKDB_TEMP_NAME = ".duckdb_tmp"


def parse_size_text(text: str) -> int:
    """DuckDB size text ("5GB", "4.6 GiB") to bytes, rounded down. Decimal units for KB..TB, binary for KiB..TiB."""
    m = _SIZE_RE.fullmatch(text) if isinstance(text, str) else None
    if m is None:
        raise ValueError(f"not a size text: {text!r}")
    return int(Decimal(m.group(1)) * _UNITS[m.group(2).lower()])


class ConfigError(Exception):
    pass


@dataclass(frozen=True)
class RateLimits:
    default_interval_s: float
    hosts: Mapping[str, float]

    def interval_for(self, host: str) -> float:
        return self.hosts.get(host.lower(), self.default_interval_s)


ACCESS_MODES = ("honest", "real_browser", "scoped_exception")
_DECISION_RE = re.compile(r"[0-9]{4}a?", re.ASCII)


@dataclass(frozen=True)
class AccessEntry:
    mode: str = "honest"
    decision: str | None = None
    daily_cap: int | None = None
    challenge_signatures: tuple[str, ...] = ()


@dataclass(frozen=True)
class CollectConfig:
    contact: str | None = None
    timeout_s: float = 30.0
    max_body_bytes: int = 536870912
    access: Mapping[str, AccessEntry] = field(default_factory=dict)


@dataclass(frozen=True)
class DuckDBConfig:
    memory_limit: str
    memory_limit_bytes: int
    threads: int
    temp_directory: Path


@dataclass(frozen=True)
class Config:
    repo_root: Path
    timezone: str
    zones: Mapping[str, Path]
    migrations_dir: Path
    rate_limits: RateLimits
    collect: CollectConfig = field(default_factory=CollectConfig)
    duckdb: DuckDBConfig | None = None             # None: defaults, temp directory inside the canonical zone

    def __post_init__(self) -> None:
        if self.duckdb is None:
            object.__setattr__(self, "duckdb", DuckDBConfig(
                DEFAULT_MEMORY_LIMIT, parse_size_text(DEFAULT_MEMORY_LIMIT), DEFAULT_THREADS,
                Path(self.zones.get("canonical", Path(self.repo_root) / "data" / "canonical")) / DUCKDB_TEMP_NAME))

    @property
    def db_path(self) -> Path:
        return self.zones["canonical"] / "investos.duckdb"


def _is_num(v: Any) -> bool:
    return isinstance(v, (int, float)) and not isinstance(v, bool)


def _find_root(start: Path) -> Path | None:
    for d in [start, *start.parents]:
        if (d / "pyproject.toml").is_file():
            return d
    return None


def _apply_env(data: dict, env: Mapping[str, str]) -> None:
    for name, value in env.items():
        if not _ENV_RE.match(name):
            continue
        section, key = name[len("INVESTOS_"):].lower().split("__", 1)
        kind = _OVERRIDABLE.get((section, key))
        if kind is None:
            raise ConfigError(f"unrecognised config override {name} (typo?)")
        if kind is int:
            if not value.isascii() or not value.isdigit():
                raise ConfigError(f"{section}.{key}: env {name}={value!r} is not an integer")
            parsed = int(value)
        elif kind is float:
            try:
                parsed: Any = float(value)
            except ValueError:
                raise ConfigError(f"{section}.{key}: env {name}={value!r} is not a number") from None
        else:
            parsed = value
        data.setdefault(section, {})
        if not isinstance(data[section], dict):
            raise ConfigError(f"{section}: must be a table")
        data[section][key] = parsed


def _validate(data: dict) -> None:
    schema = {
        "general": {"timezone"},
        "paths": set(ZONE_KEYS),
        "db": {"migrations_dir"},
        "rate_limits": {"default_interval_s", "hosts"},
    }
    for sec in data:
        if sec not in schema and sec not in ("collect", "duckdb"):
            raise ConfigError(f"unknown config section: {sec}")
    for sec, keys in schema.items():
        tbl = data.get(sec)
        if tbl is None:
            raise ConfigError(f"missing config section: {sec}")
        if not isinstance(tbl, dict):
            raise ConfigError(f"{sec}: must be a table")
        for k in tbl:
            if k not in keys:
                raise ConfigError(f"unknown config key: {sec}.{k}")
        required = keys - {"hosts"}
        for k in sorted(required):
            if k not in tbl:
                raise ConfigError(f"missing config key: {sec}.{k}")
            if k == "default_interval_s":
                continue
            if not isinstance(tbl[k], str) or not tbl[k]:
                raise ConfigError(f"{sec}.{k}: must be a non-empty string")
    rl = data["rate_limits"]
    if not _is_num(rl["default_interval_s"]):
        raise ConfigError("rate_limits.default_interval_s: must be a number")
    hosts = rl.get("hosts", {})
    if not isinstance(hosts, dict):
        raise ConfigError("rate_limits.hosts: must be a table")
    for h, v in hosts.items():
        if h != h.lower():
            raise ConfigError(f"rate_limits.hosts.{h}: host key must be lowercase")
        if not _is_num(v):
            raise ConfigError(f"rate_limits.hosts.{h}: must be a number")
    for key, v in [("default_interval_s", rl["default_interval_s"]),
                   *[(f"hosts.{h}", v) for h, v in hosts.items()]]:
        if not math.isfinite(v) or v <= 0:
            raise ConfigError(f"rate_limits.{key}: must be finite and > 0, got {v!r}")
    tz = data["general"]["timezone"]
    if tz != "Asia/Kolkata":
        raise ConfigError(f"general.timezone: must be 'Asia/Kolkata', got {tz!r}")
    try:
        ZoneInfo(tz)
    except (ZoneInfoNotFoundError, ValueError, OSError) as e:
        raise ConfigError(f"general.timezone: cannot load {tz!r}: {e}") from None


def _is_int(v: Any) -> bool:
    return isinstance(v, int) and not isinstance(v, bool)


def _parse_collect(data: dict) -> CollectConfig:
    tbl = data.get("collect")
    if tbl is None:
        return CollectConfig()
    if not isinstance(tbl, dict):
        raise ConfigError("collect: must be a table")
    for k in tbl:
        if k not in ("contact", "timeout_s", "max_body_bytes", "access"):
            raise ConfigError(f"unknown config key: collect.{k}")
    contact = tbl.get("contact")
    if contact is not None and not isinstance(contact, str):
        raise ConfigError("collect.contact: must be a string")
    timeout_s = tbl.get("timeout_s", 30.0)
    if not _is_num(timeout_s) or not math.isfinite(timeout_s) or timeout_s <= 0:
        raise ConfigError(f"collect.timeout_s: must be a finite number > 0, got {timeout_s!r}")
    max_body = tbl.get("max_body_bytes", 536870912)
    if not _is_int(max_body) or max_body < 1:
        raise ConfigError(f"collect.max_body_bytes: must be an int >= 1, got {max_body!r}")
    acc = tbl.get("access", {})
    if not isinstance(acc, dict):
        raise ConfigError("collect.access: must be a table")
    access: dict[str, AccessEntry] = {}
    for name, ent in acc.items():
        where = f"collect.access.{name}"
        if not SOURCE_RE.fullmatch(name):
            raise ConfigError(f"{where}: table name is not a valid source name")
        if not isinstance(ent, dict):
            raise ConfigError(f"{where}: must be a table")
        for k in ent:
            if k not in ("mode", "decision", "daily_cap", "challenge_signatures"):
                raise ConfigError(f"unknown config key: {where}.{k}")
        mode = ent.get("mode", "honest")
        if not isinstance(mode, str) or mode not in ACCESS_MODES:
            raise ConfigError(f"{where}.mode: must be one of {', '.join(ACCESS_MODES)}, got {mode!r}")
        decision = ent.get("decision")
        if decision is not None and (not isinstance(decision, str) or not _DECISION_RE.fullmatch(decision)):
            raise ConfigError(f"{where}.decision: must be a string like '0003' or '0003a', got {decision!r}")
        if mode == "scoped_exception" and decision is None:
            raise ConfigError(f"{where}.decision: required for mode scoped_exception")
        cap = ent.get("daily_cap")
        if cap is not None and (not _is_int(cap) or cap < 1):
            raise ConfigError(f"{where}.daily_cap: must be an int >= 1, got {cap!r}")
        if mode != "honest" and cap is None:
            raise ConfigError(f"{where}.daily_cap: required for mode {mode}")
        sigs = ent.get("challenge_signatures", [])
        if not isinstance(sigs, list) or any(not isinstance(x, str) or not x or len(x) > 200 for x in sigs):
            raise ConfigError(f"{where}.challenge_signatures: must be a list of non-empty strings, each <= 200 chars")
        access[name] = AccessEntry(mode, decision, cap, tuple(sigs))
    return CollectConfig(contact, float(timeout_s), max_body, access)


def _parse_duckdb(data: dict, root: Path, zones: Mapping[str, Path], env: Mapping[str, str]) -> DuckDBConfig:
    tbl = data.get("duckdb", {})
    if not isinstance(tbl, dict):
        raise ConfigError("duckdb: must be a table")
    for k in tbl:
        if k not in ("memory_limit", "threads", "temp_directory"):
            raise ConfigError(f"unknown config key: duckdb.{k}")
    mem = tbl.get("memory_limit", DEFAULT_MEMORY_LIMIT)
    try:
        nbytes = parse_size_text(mem)
    except ValueError:
        raise ConfigError(f"duckdb.memory_limit: must be a size like '5GB' or '4 GiB' "
                          f"(units KB MB GB TB KiB MiB GiB TiB), got {mem!r}") from None
    if nbytes < _MIN_MEMORY_BYTES:
        raise ConfigError(f"duckdb.memory_limit: must be at least 64 MiB, got {mem!r}")
    threads = tbl.get("threads", DEFAULT_THREADS)
    if not _is_int(threads) or not 1 <= threads <= 64:
        raise ConfigError(f"duckdb.threads: must be an integer from 1 to 64, got {threads!r}")
    td = tbl.get("temp_directory")
    if td is None:
        temp = zones["canonical"] / DUCKDB_TEMP_NAME
    else:
        if not isinstance(td, str) or not td:
            raise ConfigError("duckdb.temp_directory: must be a non-empty string")
        temp = _resolve(root, td)
    if not any(zones[z] in temp.parents for z in ("raw", "manifests", "canonical", "ledger")):
        raise ConfigError(f"duckdb.temp_directory: must be strictly inside the raw, manifests, canonical or "
                          f"ledger zone, got {temp}")
    if (temp == root or root in temp.parents) and (root / ".git").exists():
        try:
            _git_check_ignored(root, temp, env, "duckdb.temp_directory")
        except ConfigError as e:
            raise ConfigError(f"duckdb.temp_directory: {e}") from None
    return DuckDBConfig(mem, nbytes, threads, temp)


def _resolve(root: Path, s: str) -> Path:
    p = Path(s)  # no ~ expansion
    return (p if p.is_absolute() else root / p).resolve()


def _git_check_ignored(root: Path, raw: Path, env: Mapping[str, str], label: str = "raw path") -> None:
    git = shutil.which("git", path=env.get("PATH", ""))
    if git is None:
        raise ConfigError(f"refuse to start: {label} {raw} is inside a git repo but git is not runnable")
    try:
        r = subprocess.run([git, "-C", str(root), "check-ignore", "-q", "--", str(raw) + "/"],
                           capture_output=True, check=False, env=dict(env))
    except OSError as e:
        raise ConfigError(f"refuse to start: cannot run git: {e}") from None
    if r.returncode == 0:
        return
    if r.returncode == 1:
        raise ConfigError(f"refuse to start: {label} {raw} is inside the git tree and not git-ignored")
    raise ConfigError(f"refuse to start: git check-ignore failed ({r.returncode}): "
                      f"{r.stderr.decode(errors='replace').strip()}")


def load_config(path: Path | None = None, *, env: Mapping[str, str] | None = None,
                repo_root: Path | None = None) -> Config:
    env = os.environ if env is None else env
    for var in ("INVESTOS_ROOT", "INVESTOS_CONFIG"):
        if var in env and not env[var].strip():
            raise ConfigError(f"{var} is set but empty")
    if repo_root is not None:
        root: Path | None = Path(repo_root)
    elif "INVESTOS_ROOT" in env:
        root = Path(env["INVESTOS_ROOT"])
    else:
        root = None
    if root is not None and not root.is_dir():
        raise ConfigError(f"repo_root is not a directory: {root}")
    if path is not None:
        cfg_path: Path | None = Path(path)
    elif "INVESTOS_CONFIG" in env:
        cfg_path = Path(env["INVESTOS_CONFIG"])
    elif root is not None:
        cfg_path = root / "config" / "investos.toml"
    else:
        cfg_path = None
    if cfg_path is None or not cfg_path.is_file():
        raise ConfigError(
            f"no config file found; tried: explicit path argument ({path}), "
            f"INVESTOS_CONFIG ({env.get('INVESTOS_CONFIG')}), "
            f"{os.path.join('<repo_root>', 'config', 'investos.toml')} (repo_root={root}); resolved: {cfg_path}")
    if root is None:
        root = _find_root(cfg_path.resolve().parent)
    if root is None:
        raise ConfigError(f"cannot determine repo root for {cfg_path}: no repo_root argument, "
                          "no INVESTOS_ROOT, and no ancestor directory contains pyproject.toml")
    root = root.resolve()

    try:
        with open(cfg_path, "rb") as f:
            data = tomllib.load(f)
    except tomllib.TOMLDecodeError as e:
        raise ConfigError(f"malformed TOML in {cfg_path}: {e}") from None
    except OSError as e:
        raise ConfigError(f"cannot read config {cfg_path}: {e}") from None

    _apply_env(data, env)
    _validate(data)

    zones = {k: _resolve(root, data["paths"][k]) for k in ZONE_KEYS}
    migrations_dir = _resolve(root, data["db"]["migrations_dir"])
    for k, p in zones.items():
        if not p.is_dir():
            raise ConfigError(f"paths.{k}: zone directory does not exist or is not a directory: {p}")
    if not migrations_dir.is_dir():
        raise ConfigError(f"db.migrations_dir: does not exist or is not a directory: {migrations_dir}")
    keys = list(zones)
    for i, a in enumerate(keys):
        for b in keys[i + 1:]:
            pa, pb = zones[a], zones[b]
            if pa == pb or pa in pb.parents or pb in pa.parents:
                raise ConfigError(f"paths.{a} and paths.{b} must be distinct and not nested: {pa} / {pb}")

    raw = zones["raw"]
    if (raw == root or root in raw.parents) and (root / ".git").exists():
        _git_check_ignored(root, raw, env)

    collect = _parse_collect(data)
    duck = _parse_duckdb(data, root, zones, env)
    rl = data["rate_limits"]
    rate_limits = RateLimits(float(rl["default_interval_s"]),
                             {h: float(v) for h, v in rl.get("hosts", {}).items()})
    return Config(root, data["general"]["timezone"], zones, migrations_dir, rate_limits, collect, duck)
