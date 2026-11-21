# Checkpoint S0-T4 regressions — Test writer — 2026-11-21

## Done so far
- tests/unit/acquire/test_collector_regressions.py (23 test cases) → red run in reports/S0-T4/regressions_red.txt: 8 failed, 15 passed.
- Old suite unchanged: 535 passed, 2 skipped (regressions file deselected). No edits under src/. D4 not covered (policy pending).

## RED on 173f3db (fail now, pass after a correct fix)
| Test | Defect | Spec rule |
|---|---|---|
| test_D1_skip_only_rerun_loads_manifest_a_bounded_number_of_times | D1 | R9 (skip, zero HTTP); structural: manifest file reads over 200 skipped keys <= 3 (now 200) |
| test_D2a_safe_unzip_accepts_dot_slash_directory_entry | D2 | R12 "Directory entries are skipped" |
| test_D2b_run_stores_archive_with_dot_slash_directory_entry | D2 | R12, A19 |
| test_D2c[no_entries], [dir_only] | D2 | R12 silent (see open Q1); safer behaviour chosen: unreadable, nothing stored. [dot_slash_only] is green today, but only because of the D2 bug |
| test_D3_skipped_event_carries_real_url | D3 | R16 (event carries key, url) |
| test_D5_archive_run_and_skip_rerun_never_call_entries | D5 | A27 / R11 (public get_by_key and store only) |
| test_D6_base_does_not_import_private_names | D6 | no spec rule; hygiene from review (AST scan, dunders excluded) |

## GREEN guards (pass today; each kills a review mutation survivor)
| Test | Mutation killed | Spec |
|---|---|---|
| test_M08_any_5xx_is_retried [500,501,504,505,507,511,599] | M08 (5xx range 500-504 only) | R4 "HTTP 5xx" |
| test_M08b [499, 600] | range widened beyond 5xx | R4/R5 |
| test_M54_unreadable_after_retries_logged_at_error | M54 (per-key unreadable at INFO) | R4 "logged at ERROR" |
| test_N4_sidecar_is_written_via_temp_file_fsync_and_replace | N4 (direct write), M32 (no fsync) | Sidecar: temp file + fsync + os.replace |
| test_N4b_failed_replace_leaves_no_partial_sidecar | N4 | same |
| test_M57_sidecar_times_convert_to_utc | M57 (tzinfo overwrite) | Sidecar timestamps end in Z (UTC). The spec says the clock is "aware UTC", so a non-UTC clock is outside the contract; this is a defensive check |
| test_N13_outputs_sorted_by_key_for_unsorted_keys | N13 | "outputs ... sorted by key" |

## Open questions
1. R12 is silent on zip files with zero file members. I chose unreadable (nothing stored). The spec owner should confirm.
2. D1 vs D5 vs A27 conflict: the review's D1 fix (snapshot entries() once) breaks D5/A27. A correct fix needs a cache or index inside ManifestStore, or a new public method. The D5 test only forbids entries(). A27's existing test allows only get_by_key and store, so a new manifest method would also need the spec or A27 relaxed. D5 also needs a way to list archive members on skip without entries().
3. Not tested because the spec does not define it: zip-last store ordering (M49) and failing-manifest-mid-archive (D7); transport-error reason text (N12); `.tmp-*` leftovers and same-run_id attempt overwrite (D8); `./` handling beyond directories.
4. D4 (header policy) untouched, pending decision.

## State
- commit: see git log   branch: task/S0-T4
- files touched: tests/unit/acquire/test_collector_regressions.py, reports/S0-T4/regressions_red.txt, checkpoints/S0-T4-REGRESSIONS.md
