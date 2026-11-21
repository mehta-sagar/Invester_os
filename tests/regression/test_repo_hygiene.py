"""S0-FIX A3 / A4: a fresh checkout starts, and runtime artefacts are git-ignored (audit AUDIT.md section 7)."""
from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[2]
ZONE_DIRS = ["data/raw", "data/staged", "data/canonical", "data/features", "data/models",
             "data/quarantine", "data/exports"]


def _git(*args: str, check: bool = True) -> subprocess.CompletedProcess:
    return subprocess.run(["git", *args], cwd=REPO, capture_output=True, text=True, check=check)


def _export_committable_tree(dest: Path) -> None:
    """Copy exactly what a commit of the working tree would contain (tracked + untracked-not-ignored)."""
    names = _git("ls-files", "-z", "--cached", "--others", "--exclude-standard").stdout.split("\0")
    for n in filter(None, names):
        src = REPO / n
        if not src.is_file() and not src.is_symlink():
            continue
        out = dest / n
        out.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(src, out, follow_symlinks=False)


# ---------------------------------------------------------------- A3

@pytest.mark.parametrize("zone", ZONE_DIRS)
def test_A3_every_zone_has_committable_gitkeep(zone):
    names = set(_git("ls-files", "-z", "--cached", "--others", "--exclude-standard").stdout.split("\0"))
    assert f"{zone}/.gitkeep" in names, f"{zone}/.gitkeep would not be in a fresh clone"
    assert _git("check-ignore", "-q", "--no-index", f"{zone}/.gitkeep", check=False).returncode == 1, \
        f"{zone}/.gitkeep is git-ignored"


def test_A3_fresh_export_passes_zone_startup_check(tmp_path):
    from investos.core.config import load_config
    root = tmp_path / "clone"
    root.mkdir()
    _export_committable_tree(root)
    shutil.copy(root / "config" / "investos.example.toml", root / "config" / "investos.toml")
    cfg = load_config(root / "config" / "investos.toml", env={}, repo_root=root)   # must not raise ConfigError
    for z, p in cfg.zones.items():
        assert p.is_dir(), f"zone {z} missing in fresh export"
    for z in ZONE_DIRS:
        assert (root / z).is_dir(), z


def test_A3_data_stays_ignored():
    for p in ["data/raw/bse/ab/abcdef", "data/staged/x", "data/canonical/x.parquet", "data/features/x",
              "data/models/x", "data/quarantine/x", "data/exports/x", "data/ledger/decisions.jsonl"]:
        assert _git("check-ignore", "-q", "--no-index", p, check=False).returncode == 0, f"{p} must be ignored"


# ---------------------------------------------------------------- A4

IGNORED = [
    "data/manifests/bse.jsonl.lock",
    "data/manifests/bse.runs/.tmp-0123456789abcdef",
    "reports/checks/t-abc123.json",
    "reports/checks/.tmp-abc123",
    "reports/checks/t-abc123.json.tmp",
    "registry/trials.jsonl.lock",
    "data/ledger/decisions.jsonl.lock",
]
TRACKED_OK = [
    "data/manifests/bse.jsonl",
    "data/manifests/bse.roots.jsonl",
    "data/manifests/.gitkeep",
    "data/ledger/decisions.roots.jsonl",
    "data/ledger/.gitkeep",
    "registry/trials.roots.jsonl",
    "registry/.gitkeep",
    "registry/features.csv",
]


@pytest.mark.parametrize("path", IGNORED)
def test_A4_runtime_artefact_is_ignored(path):
    r = _git("check-ignore", "-q", "--no-index", path, check=False)
    assert r.returncode == 0, f"{path} is not git-ignored"


@pytest.mark.parametrize("path", TRACKED_OK)
def test_A4_durable_files_stay_trackable(path):
    r = _git("check-ignore", "-q", "--no-index", path, check=False)
    assert r.returncode == 1, f"{path} must NOT be git-ignored"


def test_A4_no_runtime_artefacts_currently_tracked():
    tracked = _git("ls-files").stdout.split("\n")
    bad = [t for t in tracked if t.endswith(".lock") or "/.tmp-" in t or t.startswith("reports/checks/")]
    assert not bad, bad
