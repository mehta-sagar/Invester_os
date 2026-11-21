"""Fixtures for S0-T5 tests. No investos imports at module level."""
from __future__ import annotations

import io
import uuid
from pathlib import Path

import pytest

from _chk_helpers import make_repo, put


@pytest.fixture
def repo(tmp_path):
    return make_repo(tmp_path / "repo")


@pytest.fixture
def cfg(repo):
    from investos.core.config import load_config
    return load_config(repo / "config" / "investos.toml", env={}, repo_root=repo)


@pytest.fixture
def run_id():
    from investos.core.log import configure_logging
    rid = "t-" + uuid.uuid4().hex[:12]
    configure_logging(None, run_id=rid)
    return rid


@pytest.fixture
def reg():
    from investos.check.registry import CheckRegistry
    return CheckRegistry()


@pytest.fixture
def healthy(cfg):
    """cfg whose zones hold stored raw data (two sources, three files, 0444) and an applied DB."""
    from investos.core.db import open_db
    put(cfg, "bse", b"alpha-bytes")
    put(cfg, "bse", b"beta-bytes", logical_key="k2")
    put(cfg, "nse", b"gamma-bytes")
    open_db(cfg).close()
    return cfg


@pytest.fixture
def run(cfg, run_id):
    """run(argv, registry=None, cfg=cfg) -> (exit_code, stdout_text)."""
    def _run(argv, registry=None, config=None):
        from investos.check.cli import main
        buf = io.StringIO()
        kw = {"cfg": config or cfg, "stdout": buf}
        if registry is not None:
            kw["registry"] = registry
        code = main(list(argv), **kw)
        return code, buf.getvalue()
    return _run
