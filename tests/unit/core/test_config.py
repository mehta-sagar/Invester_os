"""S0-T2 acceptance tests A1..A13 for investos.core.config (rules R1-R8)."""
from __future__ import annotations

import os
import subprocess
from pathlib import Path

import pytest

from _core_helpers import VALID_TOML, ZONES, make_repo, set_toml


def _cfgmod():
    from investos.core import config
    return config


def _load(repo, text=None, env=None, path="default"):
    config = _cfgmod()
    if text is not None:
        set_toml(repo, text)
    p = repo / "config" / "investos.toml" if path == "default" else path
    return config.load_config(p, env={} if env is None else env, repo_root=repo)


def _git_init(repo: Path, ignore: str | None):
    subprocess.run(["git", "init", "-q", str(repo)], check=True)
    if ignore is not None:
        (repo / ".gitignore").write_text(ignore)


# ---------------------------------------------------------------- R1
def test_A1_config_resolution_order(repo, tmp_path):
    config = _cfgmod()
    explicit = tmp_path / "explicit.toml"
    explicit.write_text(VALID_TOML.replace("0.35\n\n[rate_limits.hosts]", "0.1\n\n[rate_limits.hosts]"))
    envfile = tmp_path / "envfile.toml"
    envfile.write_text(VALID_TOML.replace("0.35\n\n[rate_limits.hosts]", "0.2\n\n[rate_limits.hosts]"))
    env = {"INVESTOS_CONFIG": str(envfile)}

    c1 = config.load_config(explicit, env=env, repo_root=repo)
    assert c1.rate_limits.default_interval_s == 0.1
    c2 = config.load_config(None, env=env, repo_root=repo)
    assert c2.rate_limits.default_interval_s == 0.2
    c3 = config.load_config(None, env={}, repo_root=repo)
    assert c3.rate_limits.default_interval_s == 0.35
    assert c3.repo_root == repo.resolve()


# ---------------------------------------------------------------- R1 planted
@pytest.mark.planted
def test_A2_no_config_or_no_root_raises(tmp_path):
    config = _cfgmod()
    empty = tmp_path / "emptyroot"
    empty.mkdir()
    with pytest.raises(config.ConfigError) as e:
        config.load_config(env={}, repo_root=empty)
    msg = str(e.value)
    assert "INVESTOS_CONFIG" in msg
    assert os.path.join("config", "investos.toml") in msg

    noroot = tmp_path / "noroot" / "sub"
    noroot.mkdir(parents=True)
    f = noroot / "c.toml"
    f.write_text(VALID_TOML)
    with pytest.raises(config.ConfigError):
        config.load_config(f, env={})


# ---------------------------------------------------------------- R2 planted
def _bad_variants():
    v = VALID_TOML
    return {
        "missing_ledger": (v.replace('ledger     = "data/ledger"\n', ""), "paths.ledger"),
        "extra_key": (v.replace('[db]', 'extra = "x"\n\n[db]'), "paths.extra"),
        "unknown_section": (v + '\n[foo]\nbar = 1\n', "foo"),
        "interval_string": (v.replace("default_interval_s = 0.35", 'default_interval_s = "0.35"'),
                            "rate_limits.default_interval_s"),
        "interval_bool": (v.replace("default_interval_s = 0.35", "default_interval_s = true"),
                          "rate_limits.default_interval_s"),
    }


@pytest.mark.planted
@pytest.mark.parametrize("name", list(_bad_variants()))
def test_A3_strict_schema(repo, name):
    config = _cfgmod()
    text, key = _bad_variants()[name]
    with pytest.raises(config.ConfigError) as e:
        _load(repo, text)
    assert key in str(e.value)


@pytest.mark.planted
def test_A3b_malformed_toml_mentions_path(repo):
    config = _cfgmod()
    p = set_toml(repo, "[general\ntimezone = = 'x'\n")
    with pytest.raises(config.ConfigError) as e:
        _load(repo, path=p)
    assert str(p) in str(e.value)


# ---------------------------------------------------------------- R2
def test_A4_example_toml_loads(repo, example_toml_text):
    set_toml(repo, example_toml_text)
    cfg = _load(repo)
    assert cfg.timezone == "Asia/Kolkata"
    assert cfg.rate_limits.interval_for("example.com") == 0.35
    assert set(cfg.zones) == {"raw", "manifests", "canonical", "ledger", "reports", "registry"}


# ---------------------------------------------------------------- R3
def test_A5_env_override(repo, tmp_path):
    other = tmp_path / "other" / "raw"
    other.mkdir(parents=True)
    env = {"INVESTOS_PATHS__RAW": str(other),
           "INVESTOS_RATE_LIMITS__DEFAULT_INTERVAL_S": "0.5"}
    cfg = _load(repo, env=env)
    assert cfg.zones["raw"] == other.resolve()
    assert cfg.rate_limits.default_interval_s == 0.5
    assert "default_interval_s = 0.35" in (repo / "config" / "investos.toml").read_text()


@pytest.mark.planted
@pytest.mark.parametrize("env", [
    {"INVESTOS_PATHS__RAWW": "/x"},
    {"INVESTOS_RATE_LIMITS__DEFAULT_INTERVAL_S": "abc"},
])
def test_A6_env_typo_and_non_numeric(repo, env):
    config = _cfgmod()
    with pytest.raises(config.ConfigError):
        _load(repo, env=env)


def test_A6b_non_override_env_vars_ignored(repo):
    cfg = _load(repo, env={"INVESTOS_CONFIG": str(repo / "config" / "investos.toml"),
                           "INVESTOS_ROOT": str(repo)})
    assert cfg.repo_root == repo.resolve()


# ---------------------------------------------------------------- R4
def test_A7_paths_absolute_resolved_symlink(repo, tmp_path):
    cfg = _load(repo)
    for k, p in cfg.zones.items():
        assert p.is_absolute() and p == p.resolve()
        assert str(p).startswith(str(repo.resolve()))
    assert cfg.migrations_dir == (repo / "db" / "migrations").resolve()
    assert cfg.db_path == cfg.zones["canonical"] / "investos.duckdb"

    target = tmp_path / "real_reports"
    target.mkdir()
    (repo / "reports").rmdir()
    (repo / "reports").symlink_to(target, target_is_directory=True)
    cfg2 = _load(repo)
    assert cfg2.zones["reports"] == target.resolve()


@pytest.mark.planted
def test_A8_duplicate_and_nested_zones(repo):
    config = _cfgmod()
    dup = VALID_TOML.replace('manifests  = "data/manifests"', 'manifests  = "data/raw"')
    with pytest.raises(config.ConfigError):
        _load(repo, dup)
    (repo / "data" / "raw" / "manifests").mkdir()
    nested = VALID_TOML.replace('manifests  = "data/manifests"', 'manifests  = "data/raw/manifests"')
    with pytest.raises(config.ConfigError):
        _load(repo, nested)


# ---------------------------------------------------------------- R5 planted
@pytest.mark.planted
def test_A9_missing_zone_dir(repo):
    config = _cfgmod()
    ledger = (repo / "data" / "ledger").resolve()
    ledger.rmdir()
    with pytest.raises(config.ConfigError) as e:
        _load(repo)
    assert "ledger" in str(e.value) and str(ledger) in str(e.value)
    assert not ledger.exists()


@pytest.mark.planted
def test_A9b_zone_is_file(repo):
    config = _cfgmod()
    (repo / "registry").rmdir()
    (repo / "registry").write_text("not a dir")
    with pytest.raises(config.ConfigError) as e:
        _load(repo)
    assert "registry" in str(e.value)


@pytest.mark.planted
def test_A9c_missing_migrations_dir(repo):
    config = _cfgmod()
    mig = (repo / "db" / "migrations").resolve()
    for f in mig.iterdir():
        f.unlink()
    mig.rmdir()
    with pytest.raises(config.ConfigError) as e:
        _load(repo)
    assert "migrations_dir" in str(e.value) and str(mig) in str(e.value)
    assert not mig.exists()


# ---------------------------------------------------------------- R6
def test_A10_raw_ignored_or_no_git_or_outside(repo, tmp_path):
    # no .git -> passes without git
    _load(repo)
    # git repo with raw ignored -> passes
    _git_init(repo, "data/raw/\ndata/canonical/*\n")   # S1-T5 R21: the duckdb temp dir (in canonical) must be ignored too
    cfg = _load(repo, env={"PATH": os.environ["PATH"]})  # git lookup uses only injected env PATH (D4)
    assert cfg.zones["raw"] == (repo / "data" / "raw").resolve()
    # raw outside the repo with .git present and nothing ignored -> passes
    (repo / ".gitignore").write_text("data/canonical/*\n")   # raw is not ignored here; only the duckdb temp dir (S1-T5 R21)
    outside = tmp_path / "outside_raw"
    outside.mkdir()
    cfg2 = _load(repo, env={"INVESTOS_PATHS__RAW": str(outside), "PATH": os.environ["PATH"]})   # S1-T5 R21: temp dir check runs git
    assert cfg2.zones["raw"] == outside.resolve()


@pytest.mark.planted
def test_A11_raw_not_ignored_refuses(repo):
    config = _cfgmod()
    _git_init(repo, None)
    with pytest.raises(config.ConfigError):
        _load(repo)


@pytest.mark.planted
def test_A11b_git_unrunnable_refuses(repo, monkeypatch):
    config = _cfgmod()
    _git_init(repo, "data/raw/\n")
    monkeypatch.setenv("PATH", "")
    with pytest.raises(config.ConfigError):
        _load(repo, env={"PATH": ""})


# ---------------------------------------------------------------- R7 planted
@pytest.mark.planted
@pytest.mark.parametrize("val", ["0", "-1", "nan", "inf"])
def test_A12_bad_default_interval(repo, val):
    config = _cfgmod()
    with pytest.raises(config.ConfigError):
        _load(repo, VALID_TOML.replace("default_interval_s = 0.35", f"default_interval_s = {val}"))


@pytest.mark.planted
def test_A12b_uppercase_host_key_rejected(repo):
    config = _cfgmod()
    with pytest.raises(config.ConfigError):
        _load(repo, VALID_TOML.replace('"www.bseindia.com" = 0.35', '"WWW.BSEINDIA.COM" = 0.35'))


@pytest.mark.planted
def test_A12c_bad_host_value(repo):
    config = _cfgmod()
    with pytest.raises(config.ConfigError):
        _load(repo, VALID_TOML.replace('"www.bseindia.com" = 0.35', '"www.bseindia.com" = 0'))


def test_A12d_interval_for(repo):
    cfg = _load(repo, VALID_TOML.replace('"www.bseindia.com" = 0.35', '"www.bseindia.com" = 1.0'))
    assert cfg.rate_limits.interval_for("WWW.BSEINDIA.COM") == 1.0
    assert cfg.rate_limits.interval_for("www.bseindia.com") == 1.0
    assert cfg.rate_limits.interval_for("other.example") == 0.35
    # integer default accepted and converted to float
    cfg2 = _load(repo, VALID_TOML.replace("default_interval_s = 0.35", "default_interval_s = 1"))
    assert cfg2.rate_limits.default_interval_s == 1.0
    assert isinstance(cfg2.rate_limits.default_interval_s, float)


# ---------------------------------------------------------------- R8 planted
@pytest.mark.planted
@pytest.mark.parametrize("tz", ["UTC", "Not/AZone"])
def test_A13_timezone_must_be_kolkata(repo, tz):
    config = _cfgmod()
    with pytest.raises(config.ConfigError):
        _load(repo, VALID_TOML.replace('timezone = "Asia/Kolkata"', f'timezone = "{tz}"'))


# ---------------------------------------------------------------- 28 Nov 2026 live-run regression
def test_R6_contents_only_ignore_pattern_with_gitkeep_is_accepted(repo):
    """The real repo ignores data/raw/* (contents) and keeps data/raw/.gitkeep tracked. The first live
    `investos collect rss` on the Mac refused to start because only the directory itself was checked."""
    _git_init(repo, "data/raw/*\n!data/raw/.gitkeep\ndata/canonical/*\n!data/canonical/.gitkeep\n")
    _commit_gitkeeps(repo)
    cfg = _load(repo, env={"PATH": os.environ["PATH"]})
    assert cfg.zones["raw"] == (repo / "data" / "raw").resolve()


def test_R6_the_repos_own_gitignore_is_accepted(repo):
    real = Path(__file__).resolve().parents[3] / ".gitignore"
    _git_init(repo, real.read_text())
    _commit_gitkeeps(repo)
    _load(repo, env={"PATH": os.environ["PATH"]})


@pytest.mark.planted
def test_R6_gitkeep_only_unignore_does_not_pass_when_contents_are_tracked(repo):
    config = _cfgmod()
    _git_init(repo, "data/raw/.gitkeep\ndata/canonical/*\n")   # only one file ignored: raw contents NOT ignored
    with pytest.raises(config.ConfigError):
        _load(repo, env={"PATH": os.environ["PATH"]})


def _commit_gitkeeps(repo):
    """Track data/raw/.gitkeep and data/canonical/.gitkeep, as the real repo does."""
    for z in ("raw", "canonical"):
        d = repo / "data" / z
        d.mkdir(parents=True, exist_ok=True)
        (d / ".gitkeep").write_text("")
    g = ["git", "-C", str(repo), "-c", "user.email=t@example.org", "-c", "user.name=t"]
    subprocess.run(g + ["add", "-f", "data/raw/.gitkeep", "data/canonical/.gitkeep"], check=True, capture_output=True)
    subprocess.run(g + ["commit", "-q", "-m", "keep"], check=True, capture_output=True)
