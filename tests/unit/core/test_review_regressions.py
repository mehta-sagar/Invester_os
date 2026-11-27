"""S0-T2 review regressions D2-D5 (reports/S0-T2/review.md)."""
from __future__ import annotations

import io
import json
import os
import subprocess
from datetime import datetime, timezone

import pytest

from _core_helpers import VALID_TOML, set_toml

FIXED = datetime(2026, 11, 21, 9, 30, 0, tzinfo=timezone.utc)


@pytest.fixture
def log():
    import importlib
    from investos.core import log as m
    return importlib.reload(m)


# ---- D2: strictly valid JSON ------------------------------------------------
@pytest.mark.planted
@pytest.mark.parametrize("bad", [float("nan"), float("inf"), float("-inf"),
                                 [1.0, float("nan")], {"k": float("inf")}],
                         ids=["nan", "inf", "-inf", "nested_list", "nested_dict"])
def test_D2_non_finite_float_never_raises_and_line_is_strict_json(log, bad):
    """S0-FIX A11: spec R12 (logging never raises) wins over the old ValueError; output stays strict JSON."""
    sink = io.StringIO()
    log.configure_logging(sink, clock=lambda: FIXED)
    log.get_logger("t").info("e", v=bad, other="kept")           # must not raise
    lines = sink.getvalue().splitlines()
    assert len(lines) == 1 and sink.getvalue().endswith("\n")

    def refuse(c):
        raise AssertionError(f"non-strict JSON constant {c}")
    rec = json.loads(lines[0], parse_constant=refuse)
    assert rec["fields"]["other"] == "kept" and rec["event"] == "e"


@pytest.mark.parametrize("bad", [float("nan"), float("inf"), float("-inf")])
def test_A11_top_level_non_finite_is_rendered_as_repr_string(log, bad):
    sink = io.StringIO()
    log.configure_logging(sink, clock=lambda: FIXED)
    log.get_logger("t").info("e", v=bad)
    assert json.loads(sink.getvalue())["fields"]["v"] == repr(bad)


def test_D2b_finite_floats_still_strict_json(log):
    sink = io.StringIO()
    log.configure_logging(sink, clock=lambda: FIXED)
    log.get_logger("t").info("e", v=1.5)
    json.loads(sink.getvalue(), parse_constant=lambda c: pytest.fail(f"non-strict constant {c}"))


# ---- D3: flush errors propagate ---------------------------------------------
class _BadFlush(io.StringIO):
    def flush(self):
        raise OSError("flush failed")


@pytest.mark.planted
def test_D3_flush_error_propagates(log):
    log.configure_logging(_BadFlush(), clock=lambda: FIXED)
    with pytest.raises(OSError):
        log.get_logger("t").info("e")


# ---- D4: git check uses injected env ----------------------------------------
def _git_repo(repo):
    subprocess.run(["git", "init", "-q", str(repo)], check=True)
    (repo / ".gitignore").write_text("data/raw/\ndata/canonical/*\n")   # S1-T5 R21


def test_D4_control_injected_path_with_git_passes(repo):
    from investos.core import config
    _git_repo(repo)
    cfg = config.load_config(repo / "config" / "investos.toml",
                             env={"PATH": os.environ["PATH"]}, repo_root=repo)
    assert cfg.zones["raw"] == (repo / "data" / "raw").resolve()


@pytest.mark.planted
def test_D4_injected_env_without_path_refuses(repo):
    from investos.core import config
    _git_repo(repo)  # process PATH still has git; injected env must win
    assert subprocess.run(["git", "--version"], capture_output=True).returncode == 0
    with pytest.raises(config.ConfigError):
        config.load_config(repo / "config" / "investos.toml", env={}, repo_root=repo)


@pytest.mark.planted
def test_D4b_injected_empty_path_refuses_despite_process_path(repo):
    from investos.core import config
    _git_repo(repo)
    with pytest.raises(config.ConfigError):
        config.load_config(repo / "config" / "investos.toml", env={"PATH": ""}, repo_root=repo)


# ---- D5: empty env roots / bad repo_root ------------------------------------
@pytest.mark.planted
def test_D5_empty_investos_root_raises(repo):
    from investos.core import config
    with pytest.raises(config.ConfigError):
        config.load_config(repo / "config" / "investos.toml", env={"INVESTOS_ROOT": ""})


@pytest.mark.planted
def test_D5b_empty_investos_config_raises(repo):
    from investos.core import config
    with pytest.raises(config.ConfigError):
        config.load_config(None, env={"INVESTOS_CONFIG": ""}, repo_root=repo)


@pytest.mark.planted
def test_D5c_repo_root_not_a_directory_raises(repo, tmp_path):
    from investos.core import config
    f = tmp_path / "afile"
    f.write_text("x")
    for bad in (f, tmp_path / "does_not_exist"):
        with pytest.raises(config.ConfigError) as e:
            config.load_config(repo / "config" / "investos.toml", env={}, repo_root=bad)
        assert "repo_root" in str(e.value)
