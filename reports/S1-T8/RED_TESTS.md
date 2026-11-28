# S1-T8 red tests

Worktree /home/claude/wt/S1-T8 (task/S1-T8 from main 9cd4fb7). Run: `PYTHONPATH=src python -m pytest -q -p no:cacheprovider`.

## Counts
- Without implementation: 131 failed, 170 errors (all in the 10 new `tests/unit/acquire/test_backfill_*.py` files), 2474 passed, 6 skipped. No existing test fails.
- Against the throwaway reference (/home/claude/scratch/S1-T8-ref, never committed): 2781 passed, 7 skipped, 0 failed.
- Existing tests changed: none.

## Files
`_backfill_helpers.py` (fake NSE World/EpData, BfDriver over a real RealBrowserTransport, BfEnv), `test_backfill_endpoints|ledger|planner|config|probe|collector|runner|runner_events|status_check|hygiene_cli.py`.

## A/R -> test files
- A1-A9, R1, R2: endpoints, config. A10-A13 (R4-R5): probe. A12 pace: probe, runner. A14 (R6): planner. A15, A16 (R7, R24): collector, runner_events. A17-A19 (R8-R9): collector, runner_events. A20-A22 (R10): ledger, runner. A23 (R11): collector/probe. A24 (R12): runner. A25-A26 (R13): runner, ramp S1. A27-A29 (R14): runner. A30-A31 (R15): runner_events. A32 (R16), R25: hygiene_cli. A33-A34 (R17): collector, runner. A35 (R18): hygiene_cli. A36 (R19), A37 (R20): status_check. A38 (R21): runner_events. A39 (R23): runner_events. A40 smoke: hygiene_cli (env-gated). A41: hygiene_cli.
- Sagar 1 (ramp 4000 then 8000, no catch-up, block keeps low step): runner (S1_*). Sagar 2 (core first, probe covers all 8): runner (S2_*), probe.

## Questions for Orchestrator
1. Spec A33/R-hosts: BROWSER_HOSTS in browser.py cannot change (browser A4/A15). Tests expect the backfill code to pass allowed_hosts itself. OK?
2. A36 spec figures (600 counter, 1.2 req/window, ETA 0.04 nights) do not agree if avg = counters / done windows. Tests plant counters totalling 144 for the ETA case and 600 separately for the footer. Confirm the definition of avg requests per window.
3. Plan end = last completed IST day (R6), so a night after midnight plans through yesterday; each new night adds one window. Tests assume this.
4. A21 manifest key conflicts: tests only assert skip and never overwrite.
