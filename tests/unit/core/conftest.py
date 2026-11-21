"""Shared fixtures for S0-T2 (config/log/db) tests. No investos imports at module level."""
from __future__ import annotations

from pathlib import Path

import pytest

from _core_helpers import make_repo

REPO = Path(__file__).resolve().parents[3]
EXAMPLE_TOML = REPO / "config" / "investos.example.toml"

@pytest.fixture
def repo(tmp_path):
    return make_repo(tmp_path / "repo")


@pytest.fixture
def cfg_file(repo):
    return repo / "config" / "investos.toml"


@pytest.fixture
def clean_env(monkeypatch):
    """Remove every INVESTOS_* variable from the process environment."""
    import os
    for k in list(os.environ):
        if k.startswith("INVESTOS_"):
            monkeypatch.delenv(k, raising=False)


@pytest.fixture
def example_toml_text():
    assert EXAMPLE_TOML.exists(), f"missing {EXAMPLE_TOML}"
    return EXAMPLE_TOML.read_text()
