# Checkpoint S1-T1 — Builder — 2026-11-27
## State
- Built: core/notify.py, core/config.py [collect] (AccessEntry, CollectConfig, Config.collect), acquire/access.py,
  acquire/transport.py (http.client, A33), acquire/base.py amendment, acquire/access_cli.py (+ cli.py `access`),
  .gitignore, example config, torn-tail runbook line. Demo: reports/S1-T1/demo_s1t1.py -> demo_output.txt. A55 grep: a55_grep.txt.
- Full suite x3 (as root): 1131 passed, 2 skipped, 10 failed. Last run in reports/S1-T1/tests_green.txt.
## The 10 failures are test defects (tests/ is not mine to edit)
1. 8 tests: Env.__init__ in tests/unit/acquire/_collector_helpers.py does `self.raw.mkdir()` without parents=True, but the tests
   build `S1Env(tmp_path / "a")` (dir never created): A28 matching_pairs, A30 timeout, A32 placeholder, A41 case_sensitive,
   A45 window, A38 wiring, A52 sidecar_schema_2, A50 honest_interval. With a throwaway /tmp plugin making mkdir parents=True
   (not in the repo) all 8 pass. Fix: mkdir(parents=True) in the helper.
2. test_A45_two_blocks_on_one_day_count_once: blocks on days 1,1,4 then 4,5 give distinct days {1,4,5} within 7 -> R26 halts
   at day 5, but the test expects no halt until day 7. Test arithmetic is wrong.
3. test_collector_sidecar.py::test_sidecar_schema_canonical_bytes_and_no_temp_files: expects the old key set (no access_mode);
   R30 adds "access_mode" and schema 2. Old test not in the superseded table; needs updating.
## Deviation worth knowing
- Collector default wall_clock: time.time, except when `monotonic` is injected without `wall_clock`; then wall = anchor + monotonic()
  (keeps Stage 0 fake-clock tests exact: A8, A10b). Production (neither injected) uses time.time.
## Next step
- Orchestrator: fix the three test defects; macOS run (audit C3); suite was run as root here, not non-root.

## Review fixes (D1, D2, D3, D5) - after b92813f
- D1: _gate calls AccessState.verify_writable (lock host+source files) before every attempt; _record_block catches OSError and sets an in-process refusal.
- D2: limiter stamp taken after create+fsync; final write not fsynced. D3: connection/content-length/transfer-encoding denied.
- D5: whole-call 4x timeout via a timer that shuts the socket (covers header phase). Spec Resolved item 9 added.
- Suite x3: 1160 passed, 3 skipped, 3 failed. The 3 failures: test_D1_..._next_run_makes_no_call[401/403/429] assert the first run
  makes 1 request under the same unwritable-state setup where test_D1_unwritable_state_dir_refuses_even_before_any_block asserts 0.
  Contradictory; I followed the coordinator's rule (refuse before any fetch). Test needs n()==0 on the first run.
