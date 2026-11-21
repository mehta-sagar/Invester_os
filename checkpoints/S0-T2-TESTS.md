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

## Update — review regressions (Test writer)
- Added tests/unit/core/test_review_regressions.py for D2 (NaN/Inf -> ValueError, nothing written), D3 (flush OSError propagates), D4 (injected env without PATH must refuse), D5 (empty INVESTOS_ROOT/INVESTOS_CONFIG -> ConfigError; repo_root not a dir -> ConfigError mentioning repo_root).
- Red run: 13 tests, 9 failed, 4 passed (controls/guards already satisfied: D2b finite float, D4 control, D4b empty PATH, D5c repo_root). Output: reports/S0-T2/regressions_red.txt
- Open: D6 (database-locked test) not added, not requested.

## Decision (Orchestrator, applied by Test writer)
- Strict D4 rule stands: the git lookup uses only the injected env's PATH, no fallback to the process PATH.
- Consequence: test_A10_raw_ignored_or_no_git_or_outside called _load with env={} in the git-repo "raw ignored" case, which now (correctly) refuses. Amended that case to pass env={"PATH": os.environ["PATH"]}. The no-.git case and the raw-outside-repo case (git not called) are unchanged.
