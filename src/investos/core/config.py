"""Strict TOML config loader with env overrides (spec core_config_log_db R1-R8)."""
from __future__ import annotations

import math
import os
import re
import shutil
import subprocess
import tomllib
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Mapping
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

ZONE_KEYS = ("raw", "manifests", "canonical", "ledger", "reports", "registry")
_ENV_RE = re.compile(r"^INVESTOS_[A-Z0-9_]+__[A-Z0-9_]+$")
_OVERRIDABLE = {
    ("general", "timezone"): str,
    **{("paths", k): str for k in ZONE_KEYS},
    ("db", "migrations_dir"): str,
    ("rate_limits", "default_interval_s"): float,
}


class ConfigError(Exception):
    pass


@dataclass(frozen=True)
class RateLimits:
    default_interval_s: float
    hosts: Mapping[str, float]

    def interval_for(self, host: str) -> float:
        return self.hosts.get(host.lower(), self.default_interval_s)


@dataclass(frozen=True)
class Config:
    repo_root: Path
    timezone: str
    zones: Mapping[str, Path]
    migrations_dir: Path
    rate_limits: RateLimits

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
        if kind is float:
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
        if sec not in schema:
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


def _resolve(root: Path, s: str) -> Path:
    p = Path(s)  # no ~ expansion
    return (p if p.is_absolute() else root / p).resolve()


def _git_check_ignored(root: Path, raw: Path, env: Mapping[str, str]) -> None:
    path_var = env["PATH"] if "PATH" in env else os.environ.get("PATH", "")
    git = shutil.which("git", path=path_var)
    if git is None:
        raise ConfigError(f"refuse to start: raw path {raw} is inside a git repo but git is not runnable")
    try:
        r = subprocess.run([git, "-C", str(root), "check-ignore", "-q", "--", str(raw) + "/"],
                           capture_output=True, check=False)
    except OSError as e:
        raise ConfigError(f"refuse to start: cannot run git: {e}") from None
    if r.returncode == 0:
        return
    if r.returncode == 1:
        raise ConfigError(f"refuse to start: raw path {raw} is inside the git tree and not git-ignored")
    raise ConfigError(f"refuse to start: git check-ignore failed ({r.returncode}): "
                      f"{r.stderr.decode(errors='replace').strip()}")


def load_config(path: Path | None = None, *, env: Mapping[str, str] | None = None,
                repo_root: Path | None = None) -> Config:
    env = os.environ if env is None else env
    root = Path(repo_root) if repo_root is not None else (Path(env["INVESTOS_ROOT"]) if env.get("INVESTOS_ROOT") else None)
    if path is not None:
        cfg_path: Path | None = Path(path)
    elif env.get("INVESTOS_CONFIG"):
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

    rl = data["rate_limits"]
    rate_limits = RateLimits(float(rl["default_interval_s"]),
                             {h: float(v) for h, v in rl.get("hosts", {}).items()})
    return Config(root, data["general"]["timezone"], zones, migrations_dir, rate_limits)
