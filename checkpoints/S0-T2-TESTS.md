# Checkpoint S0-T2-TESTS — Test writer — 2026-11-21

## Done so far
- pyproject.toml (investos, src layout, duckdb, dev pytest, console script, `planted` marker, pythonpath=src)
- tests/unit/core/test_config.py (A1-A13), test_log.py (A14-A20), test_db.py (A21-A30), conftest.py, _core_helpers.py
- Red run: 62 tests collected, 51 failed + 11 errors (ModuleNotFoundError investos.core / missing example toml) -> reports/S0-T2/tests_red.txt
## Next step
- Hand to Implementer: make tests green (src/investos/core/{__init__,config,log,db}.py, config/investos.example.toml, db/migrations/0000_bootstrap.sql)
## Open questions
- A5/A10: INVESTOS_PATHS__RAW outside repo used with a .git present; assumed passes (R6 says outside repo passes).
- A11b: tests empty PATH both via env= and monkeypatch; impl must call git via PATH lookup (not absolute path).
- A15: `event=` cannot be passed as kwarg (clashes with positional); tested ts/level/task/run_id only.
- A16/A17: tests reload investos.core.log per test to reset process state; impl must keep state at module level.
- A21: also asserts repo's db/migrations/0000_bootstrap.sql exists and contains schema_family.
- A29: file mtime asserted unchanged while connection open (may be fragile if duckdb checkpoints; revisit if flaky).
- Not tested: concurrent-open "database locked" edge (same-process duckdb connections share an instance, untestable deterministically here); unreadable/dangling-symlink zone; ~ non-expansion.
- A25 assumes each migration is its own transaction so 0000 stays recorded.
## State
- commit: see git log   branch: task/S0-T2
- files touched: pyproject.toml, tests/unit/core/*, reports/S0-T2/tests_red.txt, checkpoints/S0-T2-TESTS.md
- tokens used so far: n/a
