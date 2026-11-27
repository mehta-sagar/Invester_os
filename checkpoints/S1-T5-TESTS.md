# Checkpoint S1-T5 — Test writer — 2026-11-27

## Done so far
- Baseline main 7824395: 1638 passed, 3 skipped.
- New tests: 257 = tests/unit/acquire/test_collect_s1_datecheck.py (91; A59-A71, R34-R41) + tests/unit/core/test_duckdb_caps.py (160; A31-A41, R20-R25) + tests/unit/core/test_collect_date_check_config.py (6; R35 no config/env switch). Helper: tests/unit/acquire/_datecheck_helpers.py.
- Planted case A61 (Sunday URL returns Friday's identical bytes) is test_A61_sunday_url_returns_fridays_identical_file; A70 (base.py imports no parser, no strptime) is test_A70_*; A25 itself is untouched.
- Red evidence (reports/S1-T5/tests_red.txt): `pytest tests/unit/acquire tests/unit/core` = 236 failed, 869 passed, 1 skipped. Failures: 75 datecheck, 160 duckdb caps, 1 sidecar key-set test. 16 datecheck + 28 caps + 6 config tests pass at red on purpose: they are guards (behaviour that must not change: "date never read" for html/404/skipped/page-limit/blocked, Saturday passes, unknown config keys still ConfigError, etc.); the config error tests first assert the valid form loads so they are not vacuous.
- Satisfiable: throwaway reference impl in /tmp/claude-0/-home-claude-invester-os/3c0a4dda-0547-5da4-be82-80bcc4eec090/scratchpad/s1t5_ref (copy of worktree, own throwaway git, never committed): full suite 1895 passed, 3 skipped (= 1638 + 257).
- Spill test runs in ~5 s (3,000,000 rows, not the spec's 5,000,000; 5M took 7.8 s here). Uses a sampling thread on os.scandir(temp dir). Unwritable-temp test replaces the dir with a regular file (chmod does not stop root). DuckDB used for tests: 2.0.1, Python 3.13.

## Existing tests changed (list)
Sidecar key set (R40):
- tests/unit/acquire/test_collector_sidecar.py::test_sidecar_schema_canonical_bytes_and_no_temp_files: top-level key set gains `date_check`, `outcomes` key set gains `rejected`, new assert date_check == "not_date_keyed". No other assertion changed. (test_collector_core::test_A7 only checks named keys, unchanged; `schema` stays 2, the S1-T2 value.)
Git-ignore rule R21 (duckdb temp dir must be git-ignored when inside a repo with .git; the existing tests' .gitignore only ignored data/raw):
- tests/unit/core/test_config.py::test_A10_raw_ignored_or_no_git_or_outside: .gitignore now also has `data/canonical/*` (both git steps); third step also passes PATH in env because the temp-dir ignore check now runs git even when raw is outside the repo.
- tests/unit/core/test_review_regressions.py::_git_repo helper (used by test_D4_control_injected_path_with_git_passes and test_D4_injected_env_without_path_refuses): .gitignore adds `data/canonical/*`.
No assertion was weakened; each only adds the ignore line the new rule needs.

## Interface guesses the Builder must meet (spec silent or tests rely on)
1. `Outcome.REJECTED = "rejected"` (7th member, last); `DateEvidence(dates, field, rows_examined)` importable from investos.acquire.base (frozen dataclass, positional order as spec); `Collector.date_keyed`, `date_check_waiver`, `logical_date(key)`, `internal_dates(key, member, body)`.
2. A bare set or any non-DateEvidence return, rows_examined < 0, datetime inside dates -> `unreadable`, reason starts "date check error". `None` for a plain file -> `rejected`, reason starts "date_missing".
3. Exact reasons asserted: `date_missing: no trade date found in DATE1 (N rows)` and `date_mismatch: key <key> but file has <iso,iso> in <field>` (dates joined by "," optionally followed by one space; test regex tolerant). Archive reasons only need the member name (b.csv) and the token; the first failing member (name order) decides. All-None archive -> date_missing.
4. Logs: WARNING `rejected` fields key,url,reason,file_dates(list),field,rows_examined; INFO fetched/fetched_empty carry `trade_date`; WARNING `date_check_waived` once per run before the first key, reason in fields; run_end fields include `rejected`. Log fields are read from record["fields"], task from record["task"] == "collect.<source>".
5. Sidecar `date_check` in {enforced, waived, not_date_keyed}; `rejected` keys appear in `gaps`. Waived class: base's own `Collector.internal_dates` must never be called (test monkeypatches it to raise).
6. R41: key validation is done in run() before any HTTP call and before the sidecar; the default logical_date path must reject `20261120`, `2026-W47-5` (Python 3.13 date.fromisoformat accepts both) and padded/T-suffixed keys by an exact YYYY-MM-DD regex. A `logical_date` override that raises, or returns a non-date or a datetime, -> ValueError. Two strings mapping to one date -> ValueError.
7. `DuckDBConfig(memory_limit, memory_limit_bytes, threads, temp_directory)` field order as spec, frozen, importable from investos.core.config; `Config.duckdb` has a default so Config(...) built without it (S0/S1 fixtures do this) still works; equality `cfg.duckdb == DuckDBConfig("5GB", 5_000_000_000, 4, <path>)`.
8. DEFAULT temp_directory = `<canonical zone>/.duckdb_tmp` (not the literal string "data/canonical/.duckdb_tmp"). Reason: S1 fixtures use zones like tmp/canonical; a literal default would be outside the zone and break ~140 existing tests (found with the reference impl). Under the standard layout both are identical, so A31 holds. Tested by test_A31_default_temp_directory_follows_a_relocated_canonical_zone. Note: spec open question 3 reads as if the default is literal; flag to Sagar.
9. memory_limit_bytes must use exact decimal arithmetic (tests include 0.1GiB, 70.1MB, 1.1GB); a trailing newline ("5GB\n") is invalid. Env THREADS must be digits only.
10. `parse_size_text` importable from investos.core.db (may live in config and be imported there); raises ValueError on garbage.
11. open_db: `Path.mkdir(parents=False, exist_ok=True)`; failure -> MigrationError containing the path and "temp directory"; rejected SET -> MigrationError starting "cannot apply duckdb setting <name>". Tests wrap duckdb.connect with a proxy that intercepts `.execute`/`.sql`/`.query` only, so the SETs must go through one of those, as `SET name = value` text (statement order memory_limit, threads, preserve_insertion_order, temp_directory, all before any schema_migrations statement); the connection must be closed on every failure (checked from a second process).
12. open_db logs one INFO `duckdb_settings` (task core.db) with exactly the four fields, values read back from `current_setting(...)` (threads int, preserve_insertion_order bool, memory_limit as DuckDB prints it); none when open_db fails.
13. Temp dir git-ignore failures are ConfigError naming `duckdb.temp_directory`; git not runnable with the temp dir inside a repo that has .git -> ConfigError.

## BUILDER MUST READ: change outside the brief's file list
- src/investos/check/runner.py line ~203 (`_zone_copy`: `dataclasses.replace(cfg, zones=zones, migrations_dir=mig)`) builds the planted-run config copy but not the duckdb temp dir. After the change, a plant's `open_db(ctx.cfg)` (check/builtin.py `_migrations_setup`) creates `.duckdb_tmp` in the REAL canonical zone, so `run_planted` reports "real data modified" and existing test tests/unit/check/test_checkrunner_builtin.py::test_A17_planted_run_on_empty_real_data_also_works fails (seen in the reference impl before the fix). The reference fix is one line: also replace `duckdb` with `dataclasses.replace(cfg.duckdb, temp_directory=zones["canonical"] / ".duckdb_tmp")`. check/ is "out of scope" in the brief; the Orchestrator should allow this one-line edit (or the Builder finds another way). The existing A17 test is the guard; no new test was added.

## Open questions
- (above) default temp_directory literal vs zone-relative; check/runner.py edit.
- R35 says `investos check` lists every waived adapter as a WARNING; no adapter registry exists and no acceptance test names it, so it is untested here (deferred to the first concrete adapter task).
- Spec says sidecar `schema` stays 1; the code is already at 2 (S1-T2). Tests do not assert a schema change.
- Spec A38 says 5 million rows; tests use 3 million for speed (brief: < ~10 s).
- Whether DuckDB accepts `SET temp_directory` on a read-only connection: tests (A35 read-only) assume yes; the Builder must verify and report. In the reference impl on DuckDB 2.0.1 it is accepted.

## Next step
- Builder (src/investos/acquire/base.py, core/config.py, core/db.py, config/investos.example.toml, plus the one-line check/runner.py fix above). Run `PYTHONPATH=src python -m pytest -q tests`; expect 1895 passed, 3 skipped.

## State
- branch: task/S1-T5 (base main 7824395)
- files touched: tests/unit/acquire/{_datecheck_helpers,test_collect_s1_datecheck,test_collector_sidecar}.py, tests/unit/core/{test_duckdb_caps,test_collect_date_check_config,test_config,test_review_regressions}.py, reports/S1-T5/tests_red.txt, checkpoints/S1-T5-TESTS.md

## Review regressions (D1-D4, reports/S1-T5/review.md)
- D1: the `log` fixtures in tests/unit/core/test_log.py and test_review_regressions.py now yield and reload the log module on teardown, so `_BadFlush` no longer leaks into later tests. Before: `acquire core check` gave 33 setup errors (reports/S1-T5/d1_order_before.txt); after: 1221 passed in both `acquire core check` and `check core acquire` order (d1_order_after.txt).
- D2: tests/unit/acquire/test_s1t5_datecheck_regressions.py (waived adapter: 4 bad keys, two keys naming one date; ValueError before any HTTP, no sidecar). Kills N03.
- D3: tests/unit/core/test_s1t5_caps_regressions.py (rounding down, exact "cannot apply duckdb setting <name>:" prefix, exact "cannot create duckdb temp directory <path>:" prefix; the old assertions passed because DuckDB/OSError text repeated the name/path). Kills M43, N16, N17.
- D4: spill test renamed test_A38_sort_over_3m_wide_rows_spills_with_64mib.
- Mutant evidence: reports/S1-T5/regressions_red.txt (copy in scratchpad s1t5_fix). New files use distinct basenames (pytest basename clash between core/ and acquire/).
