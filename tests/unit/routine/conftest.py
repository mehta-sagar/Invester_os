"""Fixtures for S1-T3 (roots routine) tests. Real git, tmp dirs, a bare repo as origin. No network, no osascript."""
from __future__ import annotations

import io
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent))

from _routine_helpers import RoutineEnv  # noqa: E402


@pytest.fixture(autouse=True)
def _isolated_git_config(tmp_path, monkeypatch):
    empty = tmp_path / "_gitconfig_empty"
    empty.write_text("")
    monkeypatch.setenv("GIT_CONFIG_GLOBAL", str(empty))
    monkeypatch.setenv("GIT_CONFIG_NOSYSTEM", "1")
    monkeypatch.setenv("HOME", str(tmp_path))
    for k in ("GIT_AUTHOR_NAME", "GIT_AUTHOR_EMAIL", "GIT_COMMITTER_NAME", "GIT_COMMITTER_EMAIL"):
        monkeypatch.delenv(k, raising=False)


@pytest.fixture
def logbuf():
    from investos.core import log
    buf = io.StringIO()
    log.configure_logging(sink=buf, level="DEBUG", run_id="T3-RUN-1")
    yield buf
    log.configure_logging(sink=None, level="INFO")


@pytest.fixture
def env(tmp_path, logbuf):
    e = RoutineEnv(tmp_path / "w", logbuf)
    yield e
    e.assert_commands_allowed()
