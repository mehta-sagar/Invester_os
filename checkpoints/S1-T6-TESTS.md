# Checkpoint S1-T6 - Test writer - final

## Done
- 318 red tests for A1-A31, A10a, A22a and R1-R23 (tests/unit/acquire/test_rss_*.py, _rss_helpers.py; tests/unit/routine/test_rss_roots_addlist.py).
- Red run: 284 failed + 29 errors, 1914 passed (1909 baseline + 5 guards), 3 skipped. Reference impl (scratch, uncommitted): 2227 passed, 3 skipped.
- Evidence: reports/S1-T6/RED_TESTS.md (counts, A/R to test table, questions), reports/S1-T6/tests_red.txt.
- No existing test changed. Spec amendment (R7 body arg, R8a, R3a, A10a) incorporated.

## Next step
Builder: make the tests pass; read "Questions for Orchestrator" in RED_TESTS.md first (25 vs 26 rows, slots 4 vs 5, check passes with WARNING when config/rss_feeds.csv is absent).
