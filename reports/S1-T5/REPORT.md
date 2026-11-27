# S1-T5 report: in-file date check + DuckDB resource caps (Orchestrator, 27 Nov 2026)

Branch task/S1-T5, from main 7824395. Agents (all Sonnet):
- Test writer: ab0b5b91d26c82146
- Builder: a9a461c72a28ccac7
- fresh Reviewer: a2ef3547f7aef7c81
- Test writer for the fixes: a60ffcc0255cf4e4d

## Commits
| Role | Commits |
|---|---|
| Tests (red) | 1c72e10, 240dda2, f563924 |
| Build | 62ee28d (date check, base.py only), 9957cbb (DuckDB caps), 06e8183 (samples) |
| Review | 5571cc6 (PASS WITH MINOR) |
| Review regressions | 333e97c (D1–D4) |

## Done means (brief)
- **A59–A71 and A31–A41 pass, and each was seen failing first.**
  - Red: tests_red.txt shows 236 failed.
  - Green: the full suite as non-root gives 1912 passed, run 3 times by the Orchestrator.
- **Planted failure per rule (R34–R41, R20–R25):** covered by test_collect_s1_datecheck.py, test_duckdb_caps.py, test_collect_date_check_config.py and the two s1t5 regression files.
  - Mutation run: 66 of 77 mutants killed.
  - The 4 real survivors were killed by the regressions (regressions_red.txt).
- **A61, a Sunday key returning Friday's identical bytes:** see sample_datecheck.txt.
  - Friday 2026-11-20 is `fetched`.
  - Sunday 2026-11-22 is `rejected`, with reason `date_mismatch: key 2026-11-22 but file has 2026-11-20 in DATE1`.
  - The manifest has 1 line and 1 raw file.
  - The sidecar shows `rejected: 1` and status `incomplete`.
- **A25/A70:** base.py imports no parser module and uses no strptime.
- **Existing tests changed** (no assertion weakened):
  - test_collector_sidecar.py::test_sidecar_schema_canonical_bytes_and_no_temp_files gains the `date_check` and `rejected` keys.
  - test_config.py::test_A10_raw_ignored_or_no_git_or_outside and the `_git_repo` helper in test_review_regressions.py now also ignore `data/canonical/*`, because of the new temp-directory git-ignore rule.
  - The two `log` fixtures reset the log module on teardown (review D1).
- **DuckDB settings** (sample_duckdb.txt):
  - Default memory_limit `5GB` shows as `4.6 GiB`. DuckDB's GB is 10^9 bytes.
  - threads 4, preserve_insertion_order false, temp_directory `<data root>/data/canonical/.duckdb_tmp`.
  - The 64 MiB spill test passes: 5M rows sort in about 8.6 s (3M in the first build; raised to the spec's 5M per Sagar) and spill files appear.
- **Read-only connection:** DuckDB **2.0.1** (Python 3.13) accepts `SET temp_directory` on a read-only connection.
- **Reviewer note:** no open critical defect. D1 (Medium, test-order leak) and D2–D4 (Low/Info) are fixed in 333e97c.
- **Suite:** run as non-root on Linux. The macOS run is still open; it needs the Mac.

## Outside the brief (Orchestrator DECISION)
- src/investos/check/runner.py gets one line: the planted-run config copy re-roots `duckdb.temp_directory` into the plant's canonical zone.
- Without it, a plant's `open_db` writes `.duckdb_tmp` into the real data root, and the existing check A17 test fails.
- Sagar was told on 27 Nov.

## Open for Sagar (ASK-SAGAR)
1. Default temp_directory follows the canonical zone. The alternative is the literal path `data/canonical/.duckdb_tmp`.
2. Sidecar schema stays 2. The spec line saying "stays 1" is stale.
3. The spill test uses 3M rows; the spec says 5M.
4. R35's "investos check lists waived adapters" is deferred to the first real adapter.

## Answers (Sagar, 27 Nov 2026, 22:00 IST): 1a 2a 3b 4a
1. a: the temp directory follows the canonical zone (kept; spec line updated).
2. a: the schema stays 2 (stale spec line fixed).
3. b: the spill test now uses 5M rows, as the spec says (about 8.6 s here).
4. a: R35's waived-adapter listing is built with the first real adapter, the RSS poller (spec note added).
