"""Shared fixtures for S0-T6 (trial registry / decision log) tests. No investos imports at module level."""
from __future__ import annotations

import pytest

from _registry_helpers import FakeClock, fill, make_repo, new_ledger


@pytest.fixture
def clock():
    return FakeClock("2026-11-20T10:00:00")


@pytest.fixture
def lpath(tmp_path):
    return tmp_path / "test.jsonl"


@pytest.fixture
def ledger(lpath, clock):
    """Generic (kind='test', accept-anything validator) empty ledger on a fake clock."""
    return new_ledger(lpath, clock)


@pytest.fixture
def five(lpath, clock):
    """Generic ledger with 5 rows at 2026-11-20T10:00:00Z + 0..4 s (all on IST day 2026-11-20)."""
    led = new_ledger(lpath, clock)
    fill(led, clock, 5)
    assert lpath.read_bytes().count(b"\n") == 5
    return led


@pytest.fixture
def repo(tmp_path):
    return make_repo(tmp_path / "repo")


@pytest.fixture
def cfg(repo):
    from investos.core import config
    return config.load_config(repo / "config" / "investos.toml", env={}, repo_root=repo)
