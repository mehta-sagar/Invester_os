"""S1-T5 R35: there is no config switch that disables the in-file date check."""
from __future__ import annotations

import pytest

from _core_helpers import VALID_TOML, make_repo, set_toml


@pytest.mark.planted
@pytest.mark.parametrize("line", ["date_check = false", 'date_check = "off"', "enforce_date_check = false",
                                  "skip_date_check = true"])
def test_R35_collect_section_has_no_date_check_switch(tmp_path, line):
    from investos.core.config import ConfigError, load_config
    repo = make_repo(tmp_path / "r")
    set_toml(repo, VALID_TOML + f'\n[collect]\ncontact = "a@b.c"\n{line}\n')
    with pytest.raises(ConfigError):
        load_config(repo / "config" / "investos.toml", env={}, repo_root=repo)


@pytest.mark.planted
@pytest.mark.parametrize("name", ["INVESTOS_COLLECT__DATE_CHECK", "INVESTOS_COLLECT__ENFORCE_DATE_CHECK"])
def test_R35_no_env_switch_for_the_date_check(tmp_path, name):
    from investos.core.config import ConfigError, load_config
    repo = make_repo(tmp_path / "r")
    with pytest.raises(ConfigError):
        load_config(repo / "config" / "investos.toml", env={name: "off"}, repo_root=repo)
