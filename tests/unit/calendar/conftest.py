"""Fixtures for S1-T4 calendar tests. No investos imports at module level."""
from __future__ import annotations

import pytest

from _cal_helpers import Tables, confirm_rows, D

ERA_DATES = [D(2026, 8, 2), D(2026, 8, 3), D(2026, 12, 6), D(2026, 12, 7), D(2027, 2, 7), D(2027, 2, 8)]


@pytest.fixture
def tables(tmp_path):
    return Tables(tmp_path)


@pytest.fixture
def era_tables(tables):
    """Shipped eras, years 2026 loaded, every boundary date confirmed trading (ALL, manual) on NSE and BSE."""
    tables.set_sessions(confirm_rows(ERA_DATES + [D(2010, 1, 1), D(2027, 6, 1)]))
    return tables


@pytest.fixture
def cal(era_tables):
    return era_tables.calendar()
