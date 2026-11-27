# Checkpoint S1-SPEC3 — Spec writer — 2026-11-27 (final)

## Done so far
- specs/collect_rss.md (new): R1-R23, A1-A31, 4 open questions; feed list of 22 NOT confirmed (directory fetch failed, not worked around; 2 NSE names + RBI + SEBI shipped, all `to_confirm`)
- briefs/S1-T6.md; TASKS.md rows S1-SPEC3 and S1-T6
- Design: key `<feed>/<IST 15-min slot>`, date_keyed False; base hook `unchanged_body` (R7); state file + tracked gap ledger `ledger/rss/gaps.jsonl`; extractor in parse/rss_items.py; check `rss` lists waived adapters (R21 registry)
## Next step
- none; Orchestrator merges task/S1-SPEC3, Sagar answers 4 questions, then Test writer starts S1-T6 (after T1,T2,T4,T5 which are merged)
## Open questions
- overnight slots; extra news feeds; stale windows; hook vs new outcome `unchanged`
- note: roots routine add-list needs one path (in S1-T6 scope)
## State
- commit: see git log   branch: task/S1-SPEC3 (not pushed)
- files touched: specs/collect_rss.md, briefs/S1-T6.md, TASKS.md, checkpoints/S1-SPEC3.md
