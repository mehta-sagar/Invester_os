# Checkpoint S1-SPEC2 — Spec writer — 2026-11-22

## Done so far
- specs/trading_calendar.md (new): 14 rules R1-R14, 23 tests A1-A23, 7 open questions
- specs/collect_base.md "Stage 1 amendment 2": R34-R41 (8 rules), A59-A71 (13 tests), 4 open questions; new outcome `rejected`
- specs/core_config_log_db.md "Stage 1 amendment": R20-R25 (6 rules), A31-A41 (11 tests), 3 open questions
- briefs/S1-T4.md, briefs/S1-T5.md; TASKS.md rows S1-SPEC2 (spec), S1-T4, S1-T5 (open)
## Next step
- none; Orchestrator merges task/S1-SPEC2, then Test writers start S1-T4 (now) and S1-T5 (after S1-T1)
## Open questions
- `rejected` is a new Outcome (the S0 exit-gate text used the word informally for unreadable bodies); sidecar schema kept at 1 (collect_base OQ1)
- FETCHED_EMPTY without in-file date is rejected (OQ2); old files not audited (OQ3)
- calendar: late special sessions, pre-2010, public_lag_min=30 assumed, CAS for non-F&O treated applicable, who writes trading rows from bhavcopy
- duckdb: threads default 4, temp dir inside data/canonical, SET on read-only to be verified by Builder
## State
- commit: see git log   branch: task/S1-SPEC2 (not pushed)
- files touched: specs/*.md (3), briefs/S1-T4.md, briefs/S1-T5.md, TASKS.md, checkpoints/S1-SPEC2.md
