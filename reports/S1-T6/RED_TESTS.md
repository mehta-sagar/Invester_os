# S1-T6 red tests (Test writer), 27 Nov 2026

Spec: specs/collect_rss.md at bfc00cf (amended: R7 hook gets `body`, R8a content digest, R3a shipped table, A10a).

## (a) Red run (root, Linux, worktree without any production code)
`PYTHONPATH=src python -m pytest -q -p no:cacheprovider` on task/S1-T6: **284 failed, 29 errors, 1914 passed, 3 skipped**
(baseline 1909 passed, 3 skipped; no existing test changed). 318 tests added; 313 are red (29 errors are fixtures that need
`investos.check.rss_check`), 5 pass at red on purpose as guards: `test_A9_hook_not_called_for_unreadable_bodies`,
`test_A25_collect_with_no_adapter_is_usage`, `test_R1_example_config_has_no_collect_access_rss_table`,
`test_R12_gaps_symlink_is_never_staged`, `test_R12_missing_gaps_file_is_fine` (behaviour that must not change).
Full failing id list: reports/S1-T6/tests_red.txt. Red count by A-number (every A1-A31 and A22a/A10a has red tests):
A1 10, A2 10, A3 3, A4 2, A5 29, A6 6, A7 12, A8 11, A9 11, A10 4, A10a 9, A11 14, A12 2, A13 6, A14 6, A15 4, A16 4, A17 3,
A18 14, A19 8, A20 7, A21 9, A22 21, A22a 14, A23 15, A24 2, A25 9, A26 6, A27 18, A28 3, A29 6, A30 2, A31 1;
rule-named: R1 1, R3 2, R5 13, R6 4, R10 2, R11 2, R12 2, R17 1, R19 1, R20 4.

## (b) Satisfiability: throwaway reference implementation
Scratch copy /home/claude/scratch/S1-T6-ref (own throwaway git, never committed here): base hook, registry, rss_items, rss.py,
rss_check.py (+ plant), CLI `collect`, roots add-list, 25-row feed table, example toml hosts, plist.
Full suite there: **2227 passed, 3 skipped** (= 1909 + 318), 0 failed, as root. Mutation spot checks on the reference
(pub time read as UTC, pubDate used as first_seen_at, raw sha instead of digest, same-slot dedup removed) each turn
tests red (20, 3, 3, 2 failures).
Note: the reference needed one design choice forced by EXISTING tests: with no `config/rss_feeds.csv` the `rss` check must pass
with a WARNING and its plant must still be caught (tests/unit/check/test_checkrunner_builtin.py runs `check all` and
`--planted` on temp repos without the table). Pinned by `test_R20_missing_feed_table_file_passes...` and `test_A28_..._without_a_feed_table`.

## (c) Test files
tests/unit/acquire/: `_rss_helpers.py`, `test_rss_parse_items.py` (P), `test_rss_base_hook.py` (H), `test_rss_feedtable.py` (T),
`test_rss_collector.py` (K), `test_rss_cycle.py` (C), `test_rss_cli.py` (L), `test_rss_check.py` (K2), `test_rss_hygiene.py` (Y);
tests/unit/routine/test_rss_roots_addlist.py (RT). "planted" = marked `@pytest.mark.planted`.

| A / R | tests (function names, file) |
|---|---|
| A1, R1 | K: test_A1_class_attributes, test_A1_planted_wrong_attributes_are_caught_by_the_guard, test_A1_config_entry_for_rss_that_disagrees_is_refused, test_A1_empty_contact_refuses_the_collector, test_R1_example_config_has_no_collect_access_rss_table, test_A1_collector_sets_no_header...; L: test_A1_empty_contact_exits_1_before_touching_data_or_building_a_transport, test_A1_placeholder_contact_is_refused_with_a_real_transport |
| A2, R1, R23 | K: test_A2_exactly_user_agent_and_accept_encoding, test_A2_manifest_row_records_honest_mode...; Y: test_A2_module_has_no_forbidden_strings, test_A2_module_imports_no_browser_or_third_party_http, test_R1_rss_py_sets_no_header_of_its_own |
| A3, A4, R2 | K: test_A3_slot_of_floors_to_15_minutes_in_ist, test_A3_slot_uses_ist_not_the_host_zone, test_A3_sidecar_says_not_date_keyed..., test_A4_date_keyed_subclass_is_caught_by_the_guard, test_A4_key_built_from_the_utc_date... |
| A5, R3, R3a | T: test_A5_* (header, 21 table cases, NUL/non-UTF-8, zero enabled, shipped table ids/urls/windows), test_R3_loader_never_writes_the_file; C: test_R3_zero_enabled_rows_does_nothing_with_a_warning |
| A6, R4 | K: test_A6_redirect_is_unreadable_one_call_nothing_stored, test_A6_location_is_cut_at_200_characters... |
| A7, R5 | K: test_A7_valid_body_is_stored_byte_exact, test_A7_record_count..., test_A7_unusable_200_bodies_are_unreadable..., test_A7_atom_and_rdf_reason..., test_A7_nine_mib_body..., test_R5_non_2xx_with_valid_body_is_not_stored; P: test_R5_* |
| A8, R6 | P: test_A8_* (DOCTYPE/ENTITY spellings, billion laughs, late DOCTYPE, NUL), test_R6_body_over_8_mib..., test_R6_parse_module_uses_only_stdlib_etree...; K: test_A8_doctype_nul_and_billion_laughs_bodies_are_unreadable_via_the_collector; H: test_A8_base_still_imports_no_xml_parser...; Y: test_R6_rss_py_imports_no_xml_module_itself |
| A9, R7 | H: test_A9_default_hook_returns_none, ..._hook_string_makes_the_key_skipped..., ..._hook_none_stores_the_body, ..._bad_hook_is_ignored..., ..._manifest_hit_comes_first..., ..._hook_not_called_for_unreadable_bodies, ..._header_only_body_is_hooked_too, ..._skipped_by_hook_keeps_run_complete... |
| A10, R8 | C: test_A10_identical_bodies_second_poll_is_skipped_without_a_row, test_A10_one_changed_byte_stores_a_second_row_and_A_B_A..., test_A10_lost_or_garbage_state_never_skips_a_body |
| A10a, R8a | C: test_A10a_content_digest_ignores_only_lastbuilddate, ..._digest_is_exact_case_and_bounded, ..._lastbuilddate_only_difference_is_skipped, ..._any_other_difference_stores_a_new_row..., ..._state_without_a_digest..., ..._state_holds_digest... |
| A11, R9 | K: test_A11_is_poll_slot, test_A11_exactly_71_scheduled_slots_a_day; C: test_A11_unscheduled_slot_does_nothing, test_A11_force_polls..., test_A11_overnight_scheduled_slot_polls |
| A12, R10 | C: test_A12_each_feed_gets_its_own_run..., test_A12_a_403_makes_same_host_feeds_not_attempted..., test_R10_feeds_are_polled_in_table_order..., test_R10_invalid_table_aborts_before_any_fetch |
| A13, R10 | C: test_A13_second_cycle_in_the_same_slot..., ..._only_the_failed_feed_is_polled_again..., ..._cycle_budget_of_600_seconds..., ..._not_hit_just_below_600_seconds, ..._unknown_exception_is_logged..., ..._keyboard_interrupt_propagates |
| A14, R11 | C: test_A14_state_after_an_ok_poll..., ..._newest_pub_is_null..., ..._ok_unchanged_poll_moves_only..., ..._failure_moves_only..., ..._state_write_is_atomic..., ..._a_held_lock_means_the_cycle_gives_up...; L: test_R11_cycle_already_running_exits_0 |
| A15, R11 | C: test_A15_unreadable_state_is_treated_as_absent... (schema2, invalid JSON, missing field, unknown field) |
| A16, R12 | C: test_A16_missed_slots_are_one_line_per_feed, ..._overnight_gap_counts_only_scheduled_slots, ..._no_gap_when_the_previous_scheduled_slot_was_ok, ..._failed_current_slot... |
| A17, R12 | C: test_A17_first_run_has_no_state..., ..._a_failing_gap_writer_fails_the_cycle..., ..._two_processes_appending_concurrently...; L: test_R12_a_failing_gap_writer_exits_1...; K2: test_A27_bad_gaps_file_fails (strict reader: torn, bad JSON, unknown field, schema 2, bad kind); RT: test_R12_* (add-list) |
| A18, R13 | C: test_A18_trading_hours_age_is_measured_from_0915..., ..._not_evaluated_off_hours, ..._provisional_day..., ..._always_feed_with_5760_minute_window..., ..._missing_pub_dates_fall_back_to_last_change_at; K2: test_A18_trading_day_content_stale_is_found_by_the_check, test_A18_feed_without_state_is_unknown... |
| A19, R13 | C: test_A19_future_dated_newest_item..., ..._alarm_text_is_feed_and_kind_only_once_per_day_and_again_next_day, ..._cycle_result_alerts..., ..._notifier_failure_never_breaks_the_cycle, ..._unknown_calendar_year_is_judged_as_always; K2: test_A19_poll_missed_boundary..., ..._three_consecutive_failures..., ..._unloaded_calendar_year... |
| A20, R14 | P: test_A20_* (first sighting, order, fields, per-feed, parse failure warning, no clock/no file, iterators) |
| A21, R14 | P: test_A21_* (first_seen is fetch time, pub_after_seen boundaries 3h/4min/5min/5min1s/past, repeats never move it) |
| A22, R15 | P: test_A22_no_zone_is_ist..., ..._accepted_spellings (14), ..._date_only..., ..._unparseable..., ..._missing_element..., ..._dc_date..., ..._never_falls_back_to_the_system_clock |
| A22a, R15a | P: test_A22a_live_spellings (12), ..._upper_and_lower_case_months..., ..._items_with_no_pubdate... |
| A23, R16 | P: test_A23_* (guid, strip, link fallback, h: hash, .in/.com id, dup ids, order, summary markup, caps, control chars) |
| A24, R17 | C: test_A24_example_config_gives_the_hosts_their_intervals, test_A24_24_nse_feeds_are_at_least_one_second_apart...; Y: test_R17_example_config_lists_rbi_and_sebi_hosts |
| A25, R18 | L: test_A25_without_once_prints_usage_and_exits_2, ..._once_exits_0_even_when_a_feed_failed..., ..._bad_table_exits_2_with_no_http, ..._dry_run_prints_slot_and_due_feeds..., ..._unreadable_access_state_at_start_exits_1, ..._collect_with_no_adapter_is_usage; Y: test_R19_cli_registers_collect_in_the_usage_text |
| A26, R19 | L: test_A26_example_plist_is_valid, test_A26_a_broken_plist_is_caught_by_the_validator (4 mutations), test_A26_plist_never_commits_or_pushes |
| A27, R20 | K2: test_A27_* (healthy, to_confirm warnings, counts in message, read-only twice, stale, missed, ghost key, malformed keys, valid key, invalid table, bad gaps file, valid gap warning); Y: test_R20_check_cli_imports_the_rss_check; test_R20_missing_feed_table_file_passes... |
| A28, R20 | K2: test_A28_planted_run_is_caught_and_real_data_untouched, ..._on_empty_real_data, ..._even_without_a_feed_table |
| A29, R21 | K2: test_A29_rss_collector_is_registered_and_not_waived, ..._waived_adapter_is_listed..., ..._duplicate_source_from_another_class_is_refused_and_same_class_twice_is_fine, ..._check_prints_a_warning_per_waived_adapter... |
| A30, R22 | C: test_A30_cycle_log_lines_and_no_secrets, test_A30_feed_alarm_is_logged_at_error |
| A31, R10, R12 | C: test_A31_three_day_outage_is_one_missed_line_per_feed_and_one_alarm_per_feed_and_kind |
| R23 | Y: test_A2_module_has_no_forbidden_strings / ..._imports_no_browser_or_third_party_http (4 modules each) |

## Questions for Orchestrator (readings chosen; nothing invented)
1. **Row count**: R3a and A5 say 26 rows, but the named ids are 23 NSE (22 corporate + circulars, the probe table has 23 names) + rbi_press + sebi_press = **25**. Tests assert the enumerated ids exactly (25) and flag this; fix the spec number or name the 26th.
2. **A16 `slots`**: spec says from 07:15 to 08:00 is `slots` 5; at 15-minute slots that is 4. Test asserts 4 (the count of scheduled slots in [from_slot, to_slot]).
3. **poll_cycle failure signalling**: no field for "gap writer failed". Tests accept either a raise or a normal return from `poll_cycle` (they check ERROR `gap_write_failed`, other feeds still polled, file unchanged) and pin exit code 1 at the CLI. `CycleResult.outcomes` is assumed keyed by feed_id with values `fetched|fetched_empty|skipped|unreadable|incomplete|not_attempted`.
4. **poll_cycle extra kwargs** (the `...` in the spec): tests pass `run_id, monotonic, sleeper, wall_clock, hostname` (as the Collector). Feed table assumed at `cfg.repo_root/config/rss_feeds.csv`, calendar tables at `cfg.repo_root/config/calendar/` (a missing or empty calendar means UNKNOWN, judged `always`).
5. **RssFeedCollector ctor after R8a**: the API block still shows only `last_stored_sha`. Tests construct it with `last_stored_sha=` only and test the digest dedup through the cycle (state `last_content_digest`).
6. **Names the spec leaves open** (tests assume): check clock = module-level callable `_clock` (also accepts `clock`, `_now`, `now`); log fields `cycle_start{slot,feeds}`, `cycle_end{ok,failed,not_due,gaps_written}`; duplicate adapter registration raises `ValueError`; lock-busy log text contains `cycle already running`; alert message exactly `"<feed_id> <kind>"`, title `Invest OS: feed stale`; `--dry-run` is used together with `--once`, prints `slot <YYYYMMDDTHHMM>` and the feed ids.
7. **Lock wait**: the "waits at most 5 s" test uses the injected sleeper/monotonic if the builder does; with real time it costs 5 s once or twice.
8. **Existing-test constraint** (see (b)): missing feed table must be a WARNING pass, plant must still work.
9. Existing tests changed: **none**.

## Fix round (review 3f5fce2: D1-D5)
File: `tests/unit/acquire/test_rss_fixes.py` (26 tests). No existing test changed. Satisfiability: a throwaway src patch (not committed: `clock` param on `poll_cycle`, miss range measured from the last failed attempt, `poll_missed` for a never-ok feed with failures) gives the full suite 2253 passed, 3 skipped (2227 + 26), so no existing test contradicts a fix.

Red run against current src (HEAD 3f5fce2 plus the new file): **8 failed, 18 passed** (full suite: 2245 passed, 8 failed, 3 skipped).

| Defect | Tests | Against current src |
|---|---|---|
| D1 | `test_D1_each_feed_is_stamped_with_its_own_fetch_time_not_the_cycle_start`, `..._fetched_at_is_never_earlier_than_the_transport_call`, `..._slot_and_state_stay_on_the_cycle_start_while_the_clock_moves` (TypeError: no `clock`); `..._without_a_clock_the_cycle_start_is_still_the_stamp` (guard) | 3 red, 1 green |
| D2 | `test_D2_eight_failing_cycles_record_each_scheduled_slot_at_most_once` (7 slots repeated), `..._failures_then_recovery_never_cover_a_slot_twice`; guard `..._a_single_outage_still_reports_each_missed_slot_once` | 2 red, 1 green |
| D4 | `test_D4_a_feed_that_never_succeeded_raises_poll_missed_without_waiting_for_three_failures` (failures 1 and 2), `..._the_cycle_alerts_for_a_new_feed_that_failed_its_first_polls`, `..._the_check_flags_a_never_succeeded_feed_after_one_failure`; guard `..._a_feed_with_no_state_at_all_is_still_only_unknown` | 3 red, 1 green |
| D5 / M56 | `test_D5_after_a_gap_write_failure_last_ok_slot_is_not_advanced`, `..._the_held_back_miss_is_written_once_the_ledger_works_again`, `..._while_the_writer_stays_broken_poll_missed_is_raised` | green (pins current behaviour) |
| D3 | M22 `test_D3_negative_utc_offsets_are_converted_to_ist` (4 cases); M33 `..._every_lastbuilddate_element_is_ignored_by_the_digest`, `..._two_lastbuilddate_elements_changing_alone_is_skipped_as_unchanged`; M45 `..._a_cycle_budget_of_exactly_600_seconds_is_spent` (+ just-under guard); M53 `..._the_lock_wait_is_at_most_five_seconds`; M90 `..._an_adapter_with_a_waiver_but_without_date_keyed_is_not_listed`; M96 `..._a_duplicate_manifest_key_fails_the_check`; M65 `..._future_dated_newest_item_gives_no_early_alarm_and_alarms_after_date_plus_window` | green (mutant killers) |

D3/D5 tests are green by design (the code is right, the tests were missing), so their red evidence is mutation: each mutant re-applied in a throwaway copy of src and the new file run. Killed: M22 (4 tests), M33 (2), M45 (1), M53 (1), M56 (3, the D5 tests), M90 (1), M96 (1). **M65 is equivalent, not killed**: `min(newest, now)` and no cap give the same alarms for every input, because a negative age and a zero age both stay below any window >= 15 min and `ref = max(newest, 09:15)` is also in the future. The test only pins the reading (no early alarm, alarm once stated date plus window has passed). If the review wants the cap to matter, the spec must say what it caps against (for example `last_change_at`); the spec is silent, so no such test was written.

Readings pinned where the spec is silent:
- D1: `clock` is a wall-clock callable on `poll_cycle`; slot, state timestamps and gap `detected_at` stay on `now`; manifest `fetched_at` comes from `clock`.
- D2: "at most once" is checked on scheduled slots expanded from `from_slot..to_slot` of every line; every failed current slot must still be recorded.
- D4: with a state present, `last_ok_slot` null and at least one failure, the check at the end of the first failed cycle already raises `poll_missed` (the latest slot at least 10 minutes old, 08:45 at a 09:00 cycle, has no ok poll). A missing state stays `unknown`.
- D5: after a gap-write failure `last_ok_slot` and `last_ok_at` keep their old values while the other fields record the ok poll; once the ledger is writable again the truly missed slots (0715-0800 in the test) are written and no slot is written twice.
