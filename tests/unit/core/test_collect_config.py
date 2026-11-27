"""S1-T1: [collect] config section (spec collect_base "Config addition"; A28 last clause, A50, A51 load_config parts)."""
from __future__ import annotations

import dataclasses

import pytest

from _core_helpers import VALID_TOML, make_repo, set_toml


def load(repo, extra: str):
    from investos.core.config import load_config
    set_toml(repo, VALID_TOML + extra)
    return load_config(repo / "config" / "investos.toml", repo_root=repo, env={})


def bad(repo, extra: str, fragment: str):
    from investos.core.config import ConfigError
    with pytest.raises(ConfigError) as ei:
        load(repo, extra)
    assert fragment in str(ei.value), str(ei.value)


@pytest.fixture
def repo(tmp_path):
    return make_repo(tmp_path / "repo")


def test_section_absent_gives_defaults(repo):
    c = load(repo, "").collect
    assert c.contact is None and c.timeout_s == 30.0 and c.max_body_bytes == 536870912 and dict(c.access) == {}


def test_full_section_parses(repo):
    cfg = load(repo, '''
[collect]
contact = "ops@example.org"
timeout_s = 12
max_body_bytes = 1048576

[collect.access.nse_api]
mode = "real_browser"
decision = "0003a"
daily_cap = 300
challenge_signatures = ["Access Denied", "Pardon Our Interruption"]

[collect.access.arch]
mode = "honest"
''')
    c = cfg.collect
    assert (c.contact, c.timeout_s, c.max_body_bytes) == ("ops@example.org", 12.0, 1048576)
    a = c.access["nse_api"]
    assert (a.mode, a.decision, a.daily_cap) == ("real_browser", "0003a", 300)
    assert list(a.challenge_signatures) == ["Access Denied", "Pardon Our Interruption"]
    h = c.access["arch"]
    assert (h.mode, h.decision, h.daily_cap) == ("honest", None, None) and list(h.challenge_signatures) == []


def test_collect_config_is_frozen(repo):
    c = load(repo, '[collect]\ncontact = "a@b.co"\n').collect
    with pytest.raises(dataclasses.FrozenInstanceError):
        c.contact = "x"


def test_table_without_mode_defaults_to_honest(repo):
    assert load(repo, "[collect.access.x_src]\ndaily_cap = 5\n").collect.access["x_src"].mode == "honest"


@pytest.mark.planted
@pytest.mark.parametrize("extra,frag", [
    ('[collect]\nbogus = 1\n', "collect.bogus"),
    ('[collect]\ncontact = 5\n', "collect.contact"),
    ('[collect]\ntimeout_s = "30"\n', "collect.timeout_s"),
    ('[collect]\ntimeout_s = true\n', "collect.timeout_s"),
    ('[collect]\ntimeout_s = 0\n', "collect.timeout_s"),
    ('[collect]\ntimeout_s = -1\n', "collect.timeout_s"),
    ('[collect]\nmax_body_bytes = 0\n', "collect.max_body_bytes"),
    ('[collect]\nmax_body_bytes = true\n', "collect.max_body_bytes"),
    ('[collect]\nmax_body_bytes = 1.5\n', "collect.max_body_bytes"),
    ('[collect]\n[collect.nope]\nx = 1\n', "collect.nope"),
    ('[collect.access.x_src]\nmode = "stealth"\ndaily_cap = 5\n', "collect.access.x_src.mode"),
    ('[collect.access.x_src]\nmode = 3\n', "collect.access.x_src.mode"),
    ('[collect.access.Bad-Name]\nmode = "honest"\n', "Bad-Name"),
    ('[collect.access.x_src]\nmode = "scoped_exception"\ndaily_cap = 5\n', "collect.access.x_src.decision"),
    ('[collect.access.x_src]\nmode = "real_browser"\n', "collect.access.x_src.daily_cap"),
    ('[collect.access.x_src]\nmode = "scoped_exception"\ndecision = "0003"\n', "collect.access.x_src.daily_cap"),
    ('[collect.access.x_src]\nmode = "honest"\ndaily_cap = 0\n', "collect.access.x_src.daily_cap"),
    ('[collect.access.x_src]\nmode = "honest"\ndaily_cap = true\n', "collect.access.x_src.daily_cap"),
    ('[collect.access.x_src]\nmode = "honest"\ndaily_cap = 1.5\n', "collect.access.x_src.daily_cap"),
    ('[collect.access.x_src]\nmode = "honest"\ndecision = "3"\n', "collect.access.x_src.decision"),
    ('[collect.access.x_src]\nmode = "honest"\ndecision = 3\n', "collect.access.x_src.decision"),
    ('[collect.access.x_src]\nmode = "honest"\nextra = 1\n', "collect.access.x_src.extra"),
    ('[collect.access.x_src]\nmode = "honest"\nchallenge_signatures = "x"\n', "collect.access.x_src.challenge_signatures"),
    ('[collect.access.x_src]\nmode = "honest"\nchallenge_signatures = [""]\n', "collect.access.x_src.challenge_signatures"),
    ('[collect.access.x_src]\nmode = "honest"\nchallenge_signatures = [1]\n', "collect.access.x_src.challenge_signatures"),
])
def test_invalid_collect_config_names_the_dotted_key(repo, extra, frag):
    bad(repo, extra, frag)


@pytest.mark.planted
def test_signature_over_200_chars_rejected_200_accepted(repo):
    bad(repo, f'[collect.access.x_src]\nmode = "honest"\nchallenge_signatures = ["{"a" * 201}"]\n',
        "collect.access.x_src.challenge_signatures")
    c = load(repo, f'[collect.access.x_src]\nmode = "honest"\nchallenge_signatures = ["{"a" * 200}"]\n')
    assert len(c.collect.access["x_src"].challenge_signatures[0]) == 200


@pytest.mark.planted
def test_no_environment_override_for_collect_keys(repo):
    from investos.core.config import ConfigError, load_config
    set_toml(repo, VALID_TOML + '[collect]\ncontact = "a@b.co"\n')
    with pytest.raises(ConfigError):
        load_config(repo / "config" / "investos.toml", repo_root=repo, env={"INVESTOS_COLLECT__CONTACT": "x@y.zz"})


def test_decision_ids_accepted(repo):
    for d in ("0003", "0003a", "0002a"):
        load(repo, f'[collect.access.x_src]\nmode = "scoped_exception"\ndecision = "{d}"\ndaily_cap = 5\n')


def test_config_always_has_collect_attribute(repo):
    from investos.core.config import CollectConfig, AccessEntry  # noqa: F401
    assert isinstance(load(repo, "").collect, CollectConfig)
