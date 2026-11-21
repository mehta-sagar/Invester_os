# Checkpoint S0-T2-BUILD — Builder — 2026-11-21

## Done so far
- core/config.py, core/log.py, core/db.py, example toml, 0000_bootstrap.sql: initial build, 62 tests green
- Review fixes D2 (non-finite floats raise ValueError), D3 (flush errors propagate), D4 (git uses injected env, strict PATH), D5 (empty INVESTOS_ROOT/CONFIG, repo_root must be dir): full suite 75 passed (reports/S0-T2/tests_green.txt)
- Sample run on /tmp copy with example toml, 0000+0001 applied, migration_problems == [] (reports/S0-T2/sample_run.txt)
## Next step
- Orchestrator: ff-only merge of task/S0-T2
## Open questions
- D1 (pyproject script entry to nonexistent investos.cli) and D6 (untested lock path) left as-is.
## State
- branch: task/S0-T2
