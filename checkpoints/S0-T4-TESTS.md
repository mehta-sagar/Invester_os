# Checkpoint S0-T4 — Test writer — 2026-11-21T12:30:00Z

## Done so far
- tests/unit/acquire/_collector_helpers.py (fakes, DemoCollector factory, zip builders)
- test_collector_core.py (gates 1-3, A3,A4,A6,A7,A23-A27), test_collector_http.py (A8-A15), test_collector_traps.py (R1 T1,T2,T3,T6/T7), test_collector_archive_pages.py (A16-A19), test_collector_sidecar.py (A20-A22, schema)
- 133 new red (all ModuleNotFoundError investos.acquire.base; A25 fails on missing file); existing 402 green; reports/S0-T4/tests_red.txt
## Next step
- none (builder implements src/investos/acquire/base.py)
## Open questions
- see final report
## State
- commit: see git log   branch: task/S0-T4
- files touched: tests/unit/acquire/_collector_helpers.py, test_collector_{core,http,traps,archive_pages,sidecar}.py, reports/S0-T4/tests_red.txt, checkpoints/S0-T4-TESTS.md
- tokens used so far: n/a
