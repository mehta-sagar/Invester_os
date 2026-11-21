# Checkpoint S0-T3-TESTS — Test writer — 2026-11-21

## Done so far
- tests/unit/acquire/conftest.py, _acq_helpers.py (fixtures/helpers/mp workers)
- tests/unit/acquire/test_manifest_write.py: A1-A8, A20-A23, plus extras A3b/A3c (arg constraints), A24 (from_config) -> 21 tests, all red
## Next step
- test_manifest_concurrency.py (A9, A10, A9b threads), test_manifest_read_verify.py (A11-A19), then red run -> reports/S0-T3/tests_red.txt, commit, push
## Open questions
- Module path: spec header says core.manifest; ADR 0001 says investos.acquire.manifest (used).
## State
- commit: (pending)   branch: task/S0-T3
- files touched: tests/unit/acquire/*
- tokens used so far: n/a
