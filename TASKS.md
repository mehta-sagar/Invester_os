# TASKS — Invest OS build board

Status values: open · brief · spec · tests · build · review · sample-run · merged · blocked
Rule: one task, one branch (`task/<id>`), one PR. Steps per roadmap §5. Builder ≠ Test writer ≠ Reviewer.

## Stage 0 — Foundation

| ID | Task | Agent | Output | Status | Blocked by |
|---|---|---|---|---|---|
| S0-T0 | Templates, task board, briefs | Orchestrator | briefs/, specs/, checkpoints/, TASKS.md, PROGRESS.md | merged | — |
| S0-T1 | Repo from skeleton, branch protection, folder layout from taxonomy | Builder | repo `investos`, first commit | blocked | GitHub repo + ~/Invest_OS access |
| S0-T2 | Config, logging, DuckDB setup | Builder | src/investos/core/ | open | S0-T1 |
| S0-T3 | Manifest writer: sha256, URL, fetch time, read-only raw files | Builder | src/investos/core/manifest.py | open | S0-T2 |
| S0-T4 | Collector base class: rate limit, retries, body check, 4 outcomes, run sidecar | Builder | src/investos/collect/base.py | open | S0-T3 |
| S0-T5 | Check runner (`investos check`) + planted-failure harness | Builder | src/investos/check/ | open | S0-T2 |
| S0-T6 | Trial registry + decision log, append-only, hash-chained | Builder | src/investos/registry/ | open | S0-T2 |
| S0-AUD | Stage 0 audit against exit gate | Independent auditor | checkpoints/S0-AUD.md, defect list | open | S0-T1..T6 |

### Stage 0 exit gate
1. A collector fetches one file (manifest row written, raw file read-only).
2. A deliberately broken file is rejected (body check, outcome = `rejected`).
3. A re-run fetches nothing (manifest hit → outcome = `skipped`).
4. Editing a past registry row breaks the hash chain (`investos check registry` fails).
Then: Independent auditor (strongest model, fresh context) → Sagar approves layout + start of stage 1.
