# TASKS — Invest OS build board

Status values: open · brief · spec · tests · build · review · sample-run · merged · blocked
Rule: one task, one branch (`task/<id>`), one PR. Steps per roadmap §5. Builder ≠ Test writer ≠ Reviewer.

## Stage 0 — Foundation

| ID | Task | Agent | Output | Status | Blocked by |
|---|---|---|---|---|---|
| S0-T0 | Templates, task board, briefs | Orchestrator | briefs/, specs/, checkpoints/, TASKS.md, PROGRESS.md | merged | — |
| S0-T1 | Repo from skeleton, folder layout from taxonomy | Builder | repo `investos`, first commit | merged (main; reports/S0-T1/REPORT.md; branch protection not possible, decision 0001) | — |
| S0-T2 | Config, logging, DuckDB setup | Builder | src/investos/core/ | merged (main 766aa26) | — |
| S0-T3 | Manifest writer: sha256, URL, fetch time, read-only raw files | Builder | src/investos/acquire/manifest.py | merged (main 2e84408) | — |
| S0-T4 | Collector base class: rate limit, retries, body check, 4 outcomes, run sidecar | Builder | src/investos/acquire/base.py | merged (main 12c834e) | — |
| S0-T5 | Check runner (`investos check`) + planted-failure harness | Builder | src/investos/check/ | merged (main 8070522) | — |
| S0-T6 | Trial registry + decision log, append-only, hash-chained | Builder | src/investos/registry/ | merged (main 788d3f1) | — |
| S0-AUD | Stage 0 audit against exit gate | Independent auditor | checkpoints/S0-AUD.md, defect list | merged (main d883bae): PASS WITH CONDITIONS | — |
| S0-FIX | Fix all stage-0 audit defects A1–A17 | Test writer → Builder | reports/S0-FIX/REPORT.md | merged | — |

### Stage 0 exit gate
1. A collector fetches one file (manifest row written, raw file read-only).
2. A deliberately broken file is rejected (body check, outcome = `rejected`).
3. A re-run fetches nothing (manifest hit → outcome = `skipped`).
4. Editing a past registry row breaks the hash chain (`investos check registry` fails).
Then: Independent auditor (strongest model, fresh context) → Sagar approves layout + start of stage 1.

## Stage 1 — Acquisition (audit condition C2 and C4 first)

| ID | Task | Agent | Output | Status | Blocked by |
|---|---|---|---|---|---|
| S1-SPEC1 | Spec change for decisions 0002/0002a: collect amendment, manifest schema v2, roots routine | Spec writer | specs/collect_base.md, specs/core_manifest.md, specs/ops_roots_routine.md, briefs/S1-T1..T3.md | merged (main e72fa38; questions resolved in each spec) | S0-FIX |
| S1-T1 | Collector amendment: access_mode, UrllibTransport, BrowserTransport interface, block policy, cross-process limiter, daily caps, per-host blocks, notifier | Test writer → Builder | src/investos/acquire/{base,access,transport}.py, core/config.py | merged (main 284fa68) | S1-SPEC1, S1-T2 |
| S1-T2 | Manifest schema v2: access_mode, request_headers, final_url, fetch_host, schema; version-aware reader | Test writer → Builder | src/investos/acquire/manifest.py | merged (main 7f61fbd) | S1-SPEC1 |
| S1-T3 | Daily roots routine: write roots, commit, push (`investos routine roots`) | Test writer → Builder | src/investos/routine/roots.py | merged (main 7824395) | S1-SPEC1, S1-T1 |
| S1-SPEC2 | Spec change: trading calendar (closing-price eras), in-file date check (outcome `rejected`), DuckDB caps for the 16 GB Mac | Spec writer | specs/trading_calendar.md, specs/collect_base.md (R34-R41), specs/core_config_log_db.md (R20-R25), briefs/S1-T4..T5.md | merged (main 9449f0f) | S1-SPEC1 |
| S1-T4 | Trading calendar: confirmed trading days, versioned closing eras, `market_close_ist`, `after_close_public_at`, `investos check calendar` | Test writer → Builder | src/investos/calendar/, config/calendar/*.csv | merged (main b4ebfe8) | S1-SPEC2 |
| S1-T5 | Date check in the collector base (`rejected`, `date_mismatch`) + `[duckdb]` config caps applied by `open_db` | Test writer → Builder | src/investos/acquire/base.py, core/config.py, core/db.py | merged (main d3b055c) | S1-SPEC2, S1-T1 |

Order: S1-T2 first (v2 reader before any v2 writer), then S1-T1 (also builds core/notify.py), then S1-T3. C3 done (reports/S0-AUD/mac_run.txt: 688 passed, ALL CLAIMS PASS). Still open: C5 (before raw passes a few GB).
