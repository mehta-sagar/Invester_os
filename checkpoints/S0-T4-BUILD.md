# Checkpoint S0-T4 — Builder — 2026-11-21

## Done so far
- src/investos/acquire/base.py written (all spec rules R1-R16). With 2 test-helper bugs worked around locally (not committed): 534 pass, 1 fails (see below); as committed: 501 pass, 34 fail.
## BLOCKED: test bugs (tests not edited, per instructions)
1. tests/unit/acquire/_collector_helpers.py demo_class(): `verdict_fn` is set as a plain function class attribute, so `getattr(self, "verdict_fn")` returns a bound method and `fn(self, key, r)` passes 4 args (33 failures: TypeError in check_body). Fix: line `ns[k] = staticmethod(v) if k == "url_fn" else v` -> `... if k in ("url_fn", "verdict_fn") else v`.
2. test_collector_traps.py::test_T7c second half builds `env2 = env.make(...)` BEFORE `env.fresh_transport()`, so the collector holds the old transport which has no script for D18 (unscripted request AssertionError). Fix: call env.fresh_transport() and set D18 before env.make(...).
## Next step
- Test writer fixes the two items; then Builder: run suite 3x, sample run (reports/S0-T4/sample_run.txt), tests_green.txt, push.
## State
- branch: task/S0-T4
