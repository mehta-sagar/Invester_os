# Checkpoint S1-T8-BUILD - Builder - 2026-11-28 (start)

State: red baseline 131 failed + 170 errors. Read spec, red-test notes, browser/base/rss, all backfill tests.
Design decisions (traceable to tests):
- Backfill passes allowed_hosts itself (make_transport); BROWSER_HOSTS unchanged (browser A4/A15, Orchestrator resolution 1).
- One BackfillTransport (subclass of RealBrowserTransport) with a RunGate wrapping the driver session: counts EVERY request
  (anchors, retries, probe), enforces night budget / --max-requests via a BaseException RunStop (not retried by the base),
  checks the anchor's final origin against the probe's api_origin ("origin changed: a -> b").
- Runner wraps the transport in RunBrowser (block note, sleep-gap detection R23, re-anchor via set_anchor).
- Probe talks to the transport directly (own pacing via transport.pace, own storage as source backfill_probe, block callback).
- run_backfill takes calendar=None (CLI wires CalendarView); tests pass none so weekends are not skipped for bulk_deals.
- Ramp: BackfillConfig.ramp=(4000,8000); night 1 = ramp[0]; later clean nights ramp[1]; a block marker file keeps the low step.

## Test disputes
(none yet)

## Next step
Implement in order: config, endpoints, ledger, planner, browser C2/C3, collector, probe, runner, status, cli, check, files.

---
# Checkpoint S1-T8-BUILD - Builder - 2026-11-28 (end)

State: green. `PYTHONPATH=src python -m pytest -q -p no:cacheprovider` as root: 2784 passed, 5 skipped.
`git diff 876d12e..HEAD -- tests` is empty. base.py, access.py, manifest.py, transport.py, rss.py untouched.

Done: config `[backfill]`, endpoint table + loader, ledger, planner, probe, collector, runner (gate, run browser, ramp,
audit, blocks, sleep, notifications), status, CLI (`investos backfill probe|plan|run|status`), `check backfill` with
its plant, plist example, report with grep evidence (reports/S1-T8/REPORT.md).

Design points that went beyond the first sketch:
- The runner does not close the transport it is given (A27 expects the profile lock to stay held after `run`); the CLI
  closes it in a `finally`. stop.py and transport.py were folded into runner.py to stay inside the brief's file list.
- Probe: `_confirm_before` also fetches the week right before the earliest date (A13 needs more than 60 bodies for 3 endpoints).
- Ramp markers: `ran.<date>` and `block.<date>` under `.state/backfill`; a block resets the step.
- `check/builtin.py` got one import line (registers check `backfill`); `cli.py` got the dispatch.

## Test disputes
None.

## Next step
Orchestrator review. Mac steps: probe run, then supervised night, then install the plist and the pmset line (see handback).
