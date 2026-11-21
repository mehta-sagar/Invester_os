"""Shared constants/helpers for S0-T2 tests."""
from __future__ import annotations

from pathlib import Path

ZONES = ["data/raw", "data/manifests", "data/canonical", "data/ledger", "reports", "registry"]

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


def make_repo(root: Path, toml: str = VALID_TOML, *, bootstrap: bool = True) -> Path:
    """Create a tmp repo root with pyproject.toml, zone dirs, migrations dir, config/investos.toml."""
    root.mkdir(parents=True, exist_ok=True)
    (root / "pyproject.toml").write_text("[project]\nname='x'\n")
    for z in ZONES:
        (root / z).mkdir(parents=True, exist_ok=True)
    mig = root / "db" / "migrations"
    mig.mkdir(parents=True, exist_ok=True)
    if bootstrap:
        (mig / "0000_bootstrap.sql").write_text(BOOTSTRAP_SQL)
    (root / "config").mkdir(exist_ok=True)
    (root / "config" / "investos.toml").write_text(toml)
    return root


def set_toml(repo: Path, text: str) -> Path:
    p = repo / "config" / "investos.toml"
    p.write_text(text)
    return p
