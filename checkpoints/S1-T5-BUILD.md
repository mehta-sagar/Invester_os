# Checkpoint S1-T5 — Builder — 2026-11-27

## Done so far
- Commit 1 62ee28d: src/investos/acquire/base.py (R34-R41): Outcome.REJECTED, DateEvidence, date_keyed / date_check_waiver / logical_date / internal_dates, declaration check in __init__, key validation in run(), date check in _judge after page limit and safe_unzip, sidecar `date_check` + `rejected` count and gaps, WARNING `rejected`, `trade_date` on fetched lines, WARNING `date_check_waived`. No parser import, no strptime.
- Commit 2 9957cbb: core/config.py ([duckdb] section, DuckDBConfig, parse_size_text, env overrides, temp-dir zone and git-ignore rule), core/db.py (open_db mkdir + 4 SETs before migrations, MigrationError paths, INFO duckdb_settings), config/investos.example.toml, check/runner.py one-liner (planted-run copy re-roots duckdb.temp_directory; approved by Orchestrator).
- Commit 3: reports/S1-T5/{sample_datecheck,sample_duckdb,tests_green}.txt + this checkpoint.
- Full suite: root 1895 passed, 3 skipped; non-root `claude` 1898 passed, 0 skipped (the 3 root-only skips run as non-root).

## Deviations / notes
- parse_size_text lives in core/config.py and is re-exported from core/db.py.
- Config.duckdb defaults to None and is filled in __post_init__ (zone-relative default `<canonical>/.duckdb_tmp`, falling back to repo_root/data/canonical when zones lack "canonical"), so Config(...) built without it still works.
- _git_check_ignored gained an optional label argument; raw messages are unchanged.
- Sidecar schema stays 2 (the code value after S1-T2); spec says 1.
- Archive reasons append " (member NAME)" after the plain-file text.
- R35 "investos check lists waived adapters" not built (no adapter registry; untested by design).

## DuckDB facts (2.0.1, Python 3.13.18)
- `SET temp_directory` is ACCEPTED on a read-only connection (open_db(read_only=True) applies all four settings; direct SET also accepted). No spec change needed.
- 64 MiB spill test: 3M rows, correct, ~4.8 s, up to 6 spill files / ~398 MiB seen.

## Test defects
- None found. (Note: spill test uses 3M rows not 5M; spec open question 3 vs literal default temp path, see TESTS checkpoint.)

## Open questions
- Sidecar schema number (spec 1 vs code 2). Literal vs zone-relative default temp_directory (Sagar).

## Next step
- Reviewer (fresh). macOS run is the Orchestrator's.

## State
- branch: task/S1-T5
- files touched: src/investos/acquire/base.py, src/investos/core/{config,db}.py, src/investos/check/runner.py, config/investos.example.toml, reports/S1-T5/*, checkpoints/S1-T5-BUILD.md
