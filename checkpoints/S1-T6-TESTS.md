# Checkpoint S1-T6 - Test writer - fix round

## Done (fix round, review 3f5fce2)
- tests/unit/acquire/test_rss_fixes.py: 26 tests for D1 (per-feed `clock`), D2 (no repeated gap lines), D3 (mutants M22, M33, M45, M53, M56, M90, M96 killed; M65 equivalent, pinned only), D4 (never-succeeded feed alarms at once), D5 (last_ok_slot held back).
- Red against current src: 8 failed (D1 3, D2 2, D4 3), 18 green (guards and mutant killers). Throwaway fix (uncommitted): full suite 2253 passed, 3 skipped.
- No existing test changed, no src change committed. Evidence: "Fix round" section of reports/S1-T6/RED_TESTS.md.

## Next step
Builder: add `clock` to `poll_cycle` (CLI passes a real UTC clock), measure misses from the last failed attempt (without losing misses after a gap-write failure), alarm `poll_missed` for a never-ok feed with failures. Orchestrator: decide M65 (equivalent) and the D4 first-failure alert.

---
# Checkpoint S1-T6 - Test writer - final

## Done
- 318 red tests for A1-A31, A10a, A22a and R1-R23 (tests/unit/acquire/test_rss_*.py, _rss_helpers.py; tests/unit/routine/test_rss_roots_addlist.py).
- Red run: 284 failed + 29 errors, 1914 passed (1909 baseline + 5 guards), 3 skipped. Reference impl (scratch, uncommitted): 2227 passed, 3 skipped.
- Evidence: reports/S1-T6/RED_TESTS.md (counts, A/R to test table, questions), reports/S1-T6/tests_red.txt.
- No existing test changed. Spec amendment (R7 body arg, R8a, R3a, A10a) incorporated.

## Next step
Builder: make the tests pass; read "Questions for Orchestrator" in RED_TESTS.md first (25 vs 26 rows, slots 4 vs 5, check passes with WARNING when config/rss_feeds.csv is absent).
