# Checkpoint S1-T7 — Test writer — 2026-11-28

## Done
- Read brief, spec (R1-R20, A1-A37, Sagar's answers), rss.py, base.py, helpers.
- Existing tests changed (spec R13 makes honest feeds on the NSE host impossible): `_rss_helpers.py` NSE constant moved to
  `https://rss.example.test/content/RSS/` (+ `NSE_REAL`), `test_rss_cycle.py` (2 literal host names), `test_rss_feedtable.py` (shipped URL literal).

## Plan
- tests/unit/acquire/_browser_helpers.py, test_browser_{transport,driver,hygiene,rss,hostdown,smoke}.py
- reference impl in /home/claude/scratch/S1-T7-ref (copy of worktree, never committed)

## Next step
Write helpers, then tests file by file; commit after each group ("S1-T7 tests: ...").
