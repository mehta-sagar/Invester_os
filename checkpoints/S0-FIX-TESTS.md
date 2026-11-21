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

## Round 2 (all audit flags) — tests written, red in reports/S0-FIX/red2.txt
- Orchestrator decision applied: trials/decisions ledgers and run sidecar *.json stay tracked; *.lock, .tmp-*, reports/checks ignored (test_repo_hygiene.py; decisions.jsonl trackable is red). Note: real decisions path is data/ledger/decisions.jsonl (registry/decisions.jsonl also asserted trackable).
- A2: lock-held half-line tests (red) + torn-tail guard + 4-writer stress (nondeterministic, green today) in acquire/test_acquire_audit_regressions.py
- A6: denied names/case/padding/underscore, CR/LF/NUL, malformed names (red); ordinary headers guard
- A8: docs/runbooks/manifest-torn-tail.md content test only (spec R7 "does not repair", no spec backs a repair command)
- A9: copy-spy per plant (check/test_check_audit_regressions.py); allowed zones raw/manifest -> {raw,manifests}; migrations -> {canonical, migrations_dir}; registry -> {registry, ledger}. Digest cost not tested.
- A10: kinds `misplaced_file` (raw check) and `bad_line` (manifest check); guard that .gitkeep/.lock/roots/runs names pass
- A11: existing D2 test rewritten (was pinning ValueError): non-finite floats never raise, strict JSON, top-level repr string
- A12/A13: collector tests (sidecar written, key unreadable w/ "integrity"; storage failure -> status aborted, gap with reason, rest in gaps)
- A14: short-write spy (open wrapper + os.write by inode) for append and write_roots
- A16: AST tests in tests/regression/test_audit_docs_and_structure.py (one definition in src/investos/core, three modules import it)
- A17: X4, X5, X8, X9, X13, T12 each have a test; all verified to kill their mutant (guards, green now)
- A5/A7 unchanged
