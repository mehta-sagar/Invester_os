# Checkpoint S0-FIX — Test writer — 2026-11-21
## Done so far
- branch task/S0-FIX created; audit read; baseline suite 558 pass 2 skip
## Next step
- write tests A1 (check), A3/A4 (regression), A5 (skipif), A7 (registry + manifest); run red; commit
## Open questions
- A4: audit says "decide" whether registry/trials.jsonl and run sidecars are tracked; tests assert only locks/.tmp/reports/checks ignored
## State
- branch: task/S0-FIX (base d883bae)
## Update: tests written and committed
- red (16): A1 x2 (check/test_check_audit_regressions.py), A3 x8, A4 x6 (regression/test_repo_hygiene.py)
- green guards: A5 skipif + no-/proc spy test, A7a (mutant X12 killed), A7b x4 (X1/X2 mutants killed)
- full suite: 576 pass, 2 skip, 16 red (all new)
- open: trials.jsonl itself and run sidecar *.json not asserted ignored (audit leaves it to a decision)
