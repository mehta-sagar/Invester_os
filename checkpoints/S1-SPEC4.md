# Checkpoint S1-SPEC4 — Spec writer — 2026-11-28

## Done so far
- specs/collect_browser.md: 20 rules (R1-R20), 37 acceptance tests (A1-A37), edge cases, not-in-module, 6 open questions → specs/collect_browser.md
- briefs/S1-T7.md (two phases: A build, B re-enable NSE rows after the Orchestrator's Mac check)
- TASKS.md rows: S1-SPEC4 merged-pending, S1-T7 open
## Decisions taken (spec writer)
- In-page fetch (not navigation, not APIRequestContext) for byte-exactness; same-origin anchor `robots.txt` governed by the limiter.
- New adapter `rss_nse` (REAL_BROWSER) beside honest `rss`, because base R17 forbids mode change by config; routing = code constant `BROWSER_HOSTS`.
- Base untouched. Interval is the base 2.5 s floor (decision 0002a), NOT the 1.0 s the brief said (open question 2).
- Host-down rule keyed on `transport error: ... after 4 attempts` only.
## Next step
- Orchestrator: review, answer open questions, merge; then S1-T7 Test writer.
## Open questions
- see specs/collect_browser.md "Open questions for Sagar" (1 headful, 2 spacing, 3 lifetime, 4 alert coalescing, 5 cap, 6 source name)
- Items marked (verify) in the spec (CDP body decoding, all_headers, --user-data-dir token, Playwright wheel for Python 3.14.7) are for the Mac smoke run.
## State
- commit: see git log   branch: task/S1-SPEC4
- files touched: specs/collect_browser.md, briefs/S1-T7.md, TASKS.md, checkpoints/S1-SPEC4.md
