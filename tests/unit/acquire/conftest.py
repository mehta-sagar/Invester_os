"""Shared fixtures for S0-T3 (manifest writer) tests. No investos imports at module level,
so every test fails individually (not at collection) while investos.acquire.manifest is absent."""
from __future__ import annotations

import pytest


@pytest.fixture
def roots(tmp_path):
    """(raw_root, manifests_root), both existing and empty."""
    raw = tmp_path / "raw"
    man = tmp_path / "manifests"
    raw.mkdir()
    man.mkdir()
    return raw, man


@pytest.fixture
def mk(roots):
    """Factory: mk() -> ManifestStore on the default roots; mk(base=Path) -> fresh store on base/raw, base/manifests."""
    def _mk(base=None, clock=None):
        from investos.acquire.manifest import ManifestStore
        if base is None:
            raw, man = roots
        else:
            raw, man = base / "raw", base / "manifests"
            raw.mkdir(parents=True, exist_ok=True)
            man.mkdir(parents=True, exist_ok=True)
        return ManifestStore(raw, man, clock=clock) if clock else ManifestStore(raw, man)
    return _mk
