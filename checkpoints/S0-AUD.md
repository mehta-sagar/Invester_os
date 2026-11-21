# Checkpoint S0-AUD (Independent audit of Stage 0)
Status: IN PROGRESS 2026-11-21. Branch audit/S0 (local only).
Done: read docs/specs/briefs/reports/reviews; read all src. Suite 3x root: 558 pass 2 skip; non-root: 560 pass 0 skip (reports/S0-AUD/suite_runs.txt).
Leads: symlink kind not mapped to any check (builtin.py _MANIFEST_KINDS/_RAW_KINDS); lookups read manifest without lock (partial-line race?); archive skip re-unzips stored zip every re-run; lock files / sidecars / reports in tracked dirs; TASKS.md stale; S0-T1 no report.
Next: demo script; probes; traceability; mutations.
