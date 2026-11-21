# Checkpoint S0-T6 — Test writer — 2026-11-21T11:05:29Z

## Done so far
- helpers+conftest+test_registry_canon.py (A1,A2,A3,A22 canon part) written
- test_registry_chain.py (A3-A5,A10-A12,A17-A19,A22,A23) and test_registry_roots.py (A13-A16 incl. IST boundary) written
- test_registry_ledgers.py (A6-A9) and test_registry_check.py (A20,A21) written
- suite run: 143 new red (85 failed + 58 errors, all ModuleNotFoundError); existing 147 green
- regression tests added: A10b/A10c canonical-form (chain), A20h (registry check); fixed order-dependence of check A18 usage-error test
- fixed reordered_keys cases (non-sorted order, assert bytes differ); full suite green

## Next step
- none
## Open questions
- Interface assumptions are listed in the final report (module paths investos.check.registry / investos.check.cli per specs/check_runner.md; src/ not read).
## State
- commit: a8d0a14 (base; test commit pending)   branch: task/S0-T6
- files touched: tests/unit/registry/*, checkpoints/S0-T6-TESTS.md
- tokens used so far: n/a
