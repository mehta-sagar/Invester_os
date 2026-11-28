# S1-T8 tests checkpoint

Done: all of A1-A41 and R1-R25 have tests (10 files, see reports/S1-T8/RED_TESTS.md). Reference implementation passes the full suite (2781 passed, 7 skipped); it is outside the repo and not committed.

Remaining / weak spots:
- No mutation checks run (planted-failure strength of A15, A18, A30, A39 not verified by mutating the reference).
- A40 smoke is written but only runs on the Mac with INVESTOS_BROWSER_SMOKE=1.
- Open questions 1-4 in RED_TESTS.md.

Next step: Orchestrator answers the questions; implementer builds src/investos/backfill/*, check/backfill_check.py (registered in check/cli.py), backfill CLI, ops/launchd plist example, config/backfill_endpoints.csv, and the additive browser.py amendments (anchor_for, origin_aliases, profile_name, set_anchor), then runs the full suite.
