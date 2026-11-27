# Review S1-T5 (in-file date check + DuckDB caps) - Reviewer, 27 Nov 2026

Branch task/S1-T5 @ 06e8183 (main 7824395). Verdict: **PASS WITH MINOR** (no critical or high defect; 1 Medium order-dependence defect D1; 3 Low test gaps D2-D4). No src defect was found; every defect is a test or test-hygiene gap.

Suite (reports/S1-T5/review_pytest.txt): root 1895 passed, 3 skipped; non-root `claude` 1898 passed, 0 skipped. DuckDB 2.0.1, Python 3.13. macOS run is the Orchestrator's step.

## Hard rules
- Builder commits (62ee28d, 9957cbb, 06e8183 "S1-T5 build") touch no tests/. Test commits touch only tests/, one checkpoint and reports/. OK.
- Changed existing tests (test_collector_sidecar, test_config A10, test_review_regressions `_git_repo`): the diff only adds key names / `.gitignore` lines / a PATH entry; 0 assertions removed or loosened. The A10 step 3 `.gitignore` replaced `""` by `data/canonical/*` (needed by R21), raw is still not ignored there, so the case it tests is unchanged. OK.
- base.py imports no parser, no `strptime`; only `datetime.date` compared (A70 test + grep). OK.
- No network, no data/ in the diff. src/investos/check/runner.py: exactly one line (planted-run copy re-roots `duckdb.temp_directory` to the temp canonical zone). Mutant M57 (revert it) is killed by the A17 planted test. OK.
- Scope: only base.py, config.py, db.py, example toml, the one runner line. specs/, briefs/, manifest.py untouched. OK.

## Spec coverage
| Rule | Code | Tests | Result |
|---|---|---|---|
| R34 rejected outcome, not received, not abort, retried, invalid verdict | base.py Outcome.REJECTED, _judge, gaps | A61, A66, A69, R34 | OK (M16, M17, M26 killed) |
| R35 declaration (5 error cases, waiver >=20 after strip, mode in sidecar, WARNING once, no config switch) | `_declare_date_check`, run() | A59, A67, test_collect_date_check_config | OK (M01-M05, M21, N11, N12 killed). "investos check lists waived adapters": not built, see Q3 |
| R36 file decides only; every record | adapter side; base passes key, member, body | R36 tests | OK |
| R37 comparison, error path, FETCHED_EMPTY checked, no tolerance | `_date_check_one` | A60-A63, A71 | OK (M06-M13, N01, N13, N14 killed) |
| R38 order (never on unreadable/missing/blocked/incomplete/skipped) | `_judge` placement | A64, R38 | OK (M15 killed) |
| R39 archives (name order, None ignored, none-has-evidence, first failure, nothing stored) | `_date_check_files` | A65 | OK (M13, M14, M27-M29 killed; order comes from safe_unzip sort) |
| R40 sidecar keys, gaps, `date_check`, WARNING fields, `trade_date`, run_end | `_build_sidecar`, `_finish` | A60, A66, R40 | OK (M17-M20, M26 killed) |
| R41 keys (exact ISO, override, duplicates, before HTTP, no sidecar) | `_validate_keys`, `logical_date` | A68 | OK for enforced; waived untested, D2 (N03) |
| R20 validation (regex, >=64 MiB, threads 1..64, bool, unknown key) | config `_parse_duckdb`, `parse_size_text` | A31, A32, A39 | OK (M31-M35, M41, N05-N08 killed); rounding D3 (M43) |
| R20 env overrides | `_OVERRIDABLE`, `_apply_env` | A33 | OK (M44 killed); non-ASCII digits M40 survived, Info |
| R21 temp dir strictly inside raw/manifests/canonical/ledger, git-ignored, load creates nothing | `_parse_duckdb` | A34 | OK (M36-M39, N09 killed) |
| R22 four SETs in order, every connection (write, read-only, apply=False), before migrations, mkdir parents=False, quoting, close on failure | `_apply_caps`, open_db | A35, A36, R22 | OK (M45-M51, M54-M56, N04 killed); message content D3 (N16, N17) |
| R23 settings real, follow config | db.py | A35, A37 | OK (M58 killed) |
| R24 spill, unwritable dir fails loudly, limit never raised | no code; test | A38, R24 | OK (3M rows, see Q4) |
| R25 one INFO `duckdb_settings`, DuckDB-reported values | `_log_caps` | A40 | OK (M52, M53, N20 killed); see D1 |
| Example toml | config/investos.example.toml | A41 | OK |

Edge cases checked in code and tests: same bytes two URLs (date, not hash, decides), two dates in one file, Saturday special session, archive None members, no clock, waiver text trimmed, key not a date, bool `threads`, env typo, `memory_limit` never raised (recording-connection test), read-only connection settings, connection closed on failure (second-process check), `parse_size_text` re-exported from db.py.

## Defects
- **D1 (Medium) open_db now depends on global log state; test-order dependence.** db.py:155 `_log_caps(con)` writes to the process log sink. `test_review_regressions.py::test_D3_flush_error_propagates` leaves a `_BadFlush` sink configured (the `log` fixture does not reset it). Scenario: `pytest tests/unit/acquire tests/unit/core tests/unit/check` (core before check) gives 33 setup errors in tests/unit/check ("OSError: flush failed" inside `open_db`); on main the same command passes (964). The default full run passes only because `check` is collected before `core`. Any random-order or `-p xdist` run will fail intermittently. Fix: reset the log sink in the `log` fixture teardown (or an autouse conftest fixture calling `log.configure_logging(None)` / restoring `_state`); the src behaviour (log failures are loud, S0 D3) is deliberate and stays. Regression test: run `pytest tests/unit/core tests/unit/check` in that order in a CI-style check, or a test that configures a bad sink, then asserts the next test's `open_db` works.
- **D2 (Low, test gap) R41 is not tested for waived adapters.** base.py:479-480 validates keys for every date_keyed mode; mutant N03 (validate only when enforced) survived. A waived adapter with key `"20261120"` or two keys naming one date should raise ValueError before any HTTP call, as for enforced. Add `test_A68_waived_adapter_bad_key_fails_before_http` and a duplicate-date variant.
- **D3 (Low, test gap) three spec-stated details have no assertion.** (a) config.py:43 "rounded down": mutant M43 (`round`) survived; add a case such as `"64.0000000009MB"` expecting `int(...)` floor of the exact decimal product. (b) db.py:130 the MigrationError for a rejected SET must name the setting (`cannot apply duckdb setting <name>`): N16 survived; assert the name in A36's rejected-SET test. (c) db.py:123 the temp-dir error must name the path: N17 survived; assert `str(temp_directory)` in the A36 file-in-the-way test.
- **D4 (Info) naming.** The spill test is called `..._5m_...` but runs 3,000,000 rows; rename or add a comment. Evidence of spill (files seen, bytes > 0) is asserted, so R24 is covered. Non-ASCII digit env values (M40) are accepted as integers by `int()` when the `isascii` guard is removed; the guard exists and is untested but the spec only says "integer text"; no action needed.

## Open questions weighed
1. Default `temp_directory` follows the canonical zone (`<canonical>/.duckdb_tmp`) instead of the literal `data/canonical/.duckdb_tmp`: not a defect. A literal default breaks every fixture and any relocated `[paths]`; under the standard layout both are identical (A31 holds). Question for Sagar only to confirm spec open question 3 reading.
2. Sidecar `schema` stays 2: not a defect. S1-T2 already moved it to 2; the spec's "stays 1" is stale. Spec text should be corrected.
3. R35 "`investos check` lists waived adapters as WARNING": not built. Not a defect for this task (no adapter registry exists, brief forbids src/investos/check/); carry it as a follow-up item to the first concrete adapter or the check task, so the rule is not lost.
4. Spill test 3M rows vs 5M: not a defect (spill observed, ~5 s). Wording only (D4).
5. Read-only `SET temp_directory`: Builder reports DuckDB 2.0.1 accepts it; test A35 read-only covers it. No spec change.

## Mutation probes (copy in scratchpad s1t5_mut, `-x`; first batch ran acquire+core+check, survivors and the order-fragile ones re-run on the whole `tests` tree)
Total 77 mutants (M30 and a placeholder dropped): 66 killed, 11 survived. Survivors: 4 are real test gaps (M43, N03, N16, N17 = D2, D3), 7 are equivalent or non-spec-required (M24, M40, N02, N10, N15, N18, N19).

| ID | Mutation | Result |
|---|---|---|
| M01 | waiver min length 19 | killed |
| M02 | waiver length without strip | killed |
| M03 | both internal_dates and waiver allowed | killed |
| M04 | not-date-keyed with override allowed | killed |
| M05 | not-date-keyed with waiver allowed | killed |
| M06 | `target in dates` instead of equality | killed |
| M07 | empty dates not rejected | killed |
| M08 | file dates unsorted in reason | killed |
| M09 | None for plain file passes | killed |
| M10 | datetime in dates accepted | killed |
| M11 | negative rows_examined accepted | killed |
| M12 | adapter exception not caught | killed |
| M13 | None accepted for plain file via archive flag | killed |
| M14 | archive with no evidence passes | killed |
| M15 | date check also runs for waived/not keyed | killed |
| M16 | check_body may return REJECTED | killed |
| M17 | rejected not in gaps | killed |
| M18 | sidecar date_check constant | killed |
| M19 | no trade_date in INFO | killed |
| M20 | rejected WARNING loses date fields | killed |
| M21 | waiver WARNING on enforced runs | killed |
| M22 | no key validation | killed |
| M23 | duplicate-date keys allowed | killed |
| M24 | key regex `match` instead of `fullmatch` | survived (equivalent: `date.fromisoformat` rejects the trailing text) |
| M25 | logical_date datetime allowed | killed |
| M26 | rejected counted as received | killed |
| M27 | archive date_missing token dropped | killed |
| M28 | member name dropped from detail | killed |
| M29 | first failing member skipped (continue) | killed |
| M31 | min memory 64 MB instead of 64 MiB | killed |
| M32 | GB = 2**30 | killed |
| M33 | TiB = 2**30 | killed |
| M34 | threads max 65 | killed |
| M35 | threads min 0 | killed |
| M36 | reports zone allowed for temp dir | killed |
| M37 | ledger zone dropped | killed |
| M38 | temp dir equal to a zone allowed | killed |
| M39 | git-ignore check skipped | killed |
| M40 | env threads without isascii | survived (Info, D4) |
| M41 | `preserve_insertion_order` accepted in [duckdb] | killed |
| M42 | default threads 8 | killed |
| M43 | memory bytes `round` not floor | survived (D3a) |
| M44 | threads env kept as str | killed |
| M45 | mkdir parents=True | killed |
| M46 | preserve_insertion_order true | killed |
| M47 | no quote doubling | killed |
| M48 | caps not applied with apply=False | killed |
| M49 | connection not closed on failure | killed |
| M50 | rejected SET swallowed | killed |
| M51 | temp dir mkdir failure swallowed | killed |
| M52 | no duckdb_settings event | killed |
| M53 | event misses preserve_insertion_order | killed |
| M54 | SET order changed | killed |
| M55 | wrong temp path set | killed |
| M56 | caps after migrations | killed |
| M57 | runner.py re-root reverted | killed |
| M58 | threads + 1 | killed |
| N01 | date check skipped for FETCHED_EMPTY | killed |
| N02 | date_keyed non-bool check removed | survived (extra Builder guard, not in spec) |
| N03 | keys validated only when enforced | survived (D2) |
| N04 | no caps on read-only | killed |
| N05 | 64 MiB exactly rejected | killed |
| N06 | bool accepted for threads | killed |
| N07 | case-sensitive units | killed |
| N08 | `\s*` between number and unit | killed |
| N09 | temp dir git check skipped when inside repo | killed |
| N10 | empty temp_directory string check removed | survived (equivalent: "" resolves to root, rejected by zone rule) |
| N11 | waiver min 21 | killed |
| N12 | waiver reason dropped from WARNING | killed |
| N13 | invalid-evidence reason prefix changed | killed |
| N14 | datetime from logical_date allowed | killed |
| N15 | plain `set` for dates rejected | survived (non-spec leniency) |
| N16 | SET error drops setting name | survived (D3b) |
| N17 | mkdir error drops path | survived (D3c) |
| N18 | git-ignore error not re-labelled | survived (equivalent, message already names it) |
| N19 | archive no-evidence rows count 0 -> 1 | survived (cosmetic) |
| N20 | event not logged on read-only | killed |
