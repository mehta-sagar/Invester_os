"""S0-FIX A17 (X9): zone nesting is rejected in BOTH directions."""
from __future__ import annotations

import pytest

from _core_helpers import VALID_TOML, set_toml


def _load(repo, text):
    from investos.core import config
    set_toml(repo, text)
    return config.load_config(repo / "config" / "investos.toml", env={}, repo_root=repo)


def test_X9_outer_zone_listed_first_inner_second(repo):
    from investos.core.config import ConfigError
    (repo / "data" / "raw" / "manifests").mkdir()
    with pytest.raises(ConfigError):
        _load(repo, VALID_TOML.replace('manifests  = "data/manifests"', 'manifests  = "data/raw/manifests"'))


def test_X9_inner_zone_listed_first_outer_second(repo):
    from investos.core.config import ConfigError
    (repo / "data" / "manifests" / "raw").mkdir()
    with pytest.raises(ConfigError):
        _load(repo, VALID_TOML.replace('raw        = "data/raw"', 'raw        = "data/manifests/raw"'))
