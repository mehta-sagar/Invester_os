"""S1-T5 review regressions (reports/S1-T5/review.md D3): pins for details the first test pass left unasserted."""
from __future__ import annotations

import pytest

from test_duckdb_caps import SETTINGS_ORDER, _db, _parse, _repo_cfg, recording  # noqa: F401


@pytest.mark.parametrize("text,expected", [("70.0000006MB", 70_000_000), ("64.0000009MB", 64_000_000),
                                           ("70.9999999KB", 70_999), ("1.9999999KiB", 2047)])
def test_D3a_memory_limit_bytes_rounds_down_never_to_nearest(text, expected):
    assert _parse(text) == expected


@pytest.mark.parametrize("name", SETTINGS_ORDER)
def test_D3b_rejected_set_error_names_the_setting_in_our_own_wording(repo, recording, name):
    recording.reject = name
    db = _db()
    with pytest.raises(db.MigrationError) as e:
        db.open_db(_repo_cfg(repo, None))
    assert str(e.value).startswith(f"cannot apply duckdb setting {name}:")


def test_D3c_temp_directory_error_names_the_path_in_our_own_wording(repo):
    cfg = _repo_cfg(repo, None)
    cfg.duckdb.temp_directory.write_text("i am a file")
    db = _db()
    with pytest.raises(db.MigrationError) as e:
        db.open_db(cfg)
    assert str(e.value).startswith(f"cannot create duckdb temp directory {cfg.duckdb.temp_directory}:")
