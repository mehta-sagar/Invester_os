# Checkpoint S0-T2-BUILD — Builder — 2026-11-21

## Done so far
- core/config.py + example toml + 0000_bootstrap.sql -> test_config.py 30 passed
- core/log.py -> test_log.py 10 passed
- core/db.py -> test_db.py 22 passed; full `pytest tests` 62 passed (reports/S0-T2/tests_green.txt)
## Next step
- Orchestrator: review, sample run, ff-only merge of task/S0-T2
## Open questions
- No test judged wrong. Migration numbering OK (0000 added, 0001_core.sql untouched, contiguous).
- Design note: tamper check runs before discovery checks so a deleted applied file is named even when it also creates a gap (A26).
## State
- commit: see git log   branch: task/S0-T2
