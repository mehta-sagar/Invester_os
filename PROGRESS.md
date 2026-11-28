# PROGRESS — Invest OS build

Current stage: 1 (Raw acquisition), in progress. Stage 0 DONE 2026-11-21 (audit PASS WITH CONDITIONS, all defects fixed in S0-FIX)
Next task: first live RSS poll on the Mac (`investos collect rss --once`) and launchd job; spec for the real-browser backfill runner; remaining Mac probes (holiday lists, shareholding depth, results window, mcp.nseindia.in, TLS, BSE XBRL). Session process log: Plan Project claude/SESSION_LOG.md

## Log
- 2026-11-21 S0-T0 done: TASKS.md, PROGRESS.md, templates (briefs/_TEMPLATE.md, specs/_TEMPLATE.md, checkpoints/_TEMPLATE.md, reports/_TEMPLATE.md), briefs S0-T1..T6.
- 2026-11-21 S0-T1..T6 merged (see TASKS.md for commits).
- 2026-11-21 S0-AUD (Opus, fresh): PASS WITH CONDITIONS; 4/4 exit-gate demos; 41 rules traced; 36 mutations.
- 2026-11-21 S0-FIX: all audit defects A1-A17 fixed; 685 tests; demo ALL CLAIMS PASS.
- 2026-11-22..27 S1-SPEC1, S1-T2 (manifest v2, main 7f61fbd), S1-T1 (collector amendment, 284fa68), S1-SPEC2 (9449f0f), S1-T4 (trading calendar, b4ebfe8), S1-T3 (nightly roots routine, 7824395) merged. (Back-filled 27 Nov: this file was not updated for these tasks at the time.)
- 2026-11-27 S1-T5 merged (main d3b055c): in-file date check (outcome `rejected`, date_mismatch/date_missing) + `[duckdb]` caps in open_db. Review PASS WITH MINOR, 66/77 mutants killed, D1-D4 fixed. Suite 1912 passed non-root x3 (1909 + 3 skipped as root). One approved line outside the brief in check/runner.py. Open questions for Sagar in reports/S1-T5/REPORT.md.
- 2026-11-28 S1-T6 merged (main d5f7ec8): RSS poller for 25 feeds (22 NSE corporate + Circulars + RBI + SEBI, all confirmed by the 27 Nov Mac probe), dedup on a content digest that ignores <lastBuildDate> (Sagar), item extractor (first_seen_at = our fetch time, pubDate read as IST), adapter registry, `investos check rss`. Review PASS WITH MINOR, 90/102 mutants; D1-D5 fixed; D6-D10 Info open. Suite 2257 passed non-root x3. Mac suite on f42b672: 1912 passed (macOS 27.2.1, Python 3.14.7).
- 2026-11-28 S1-SPEC4 + S1-T7 merged: real-Chrome transport (Playwright on the installed Chrome, in-page fetch, no stealth, 2.5 s), source rss_nse, host-down rule; live Mac check 25/25; 23 NSE feeds enabled (quiet feeds: 10-year content window). Review PASS WITH MINOR, D1 (404 anchor) fixed. Non-root 2455 x3.
- 2026-11-28 S1-SPEC5 + S1-T8 merged: history backfill runner. Mac probe: 5/8 endpoints confirmed (announcements back to 2002); PIT/results need endpoint eras, corporate_actions an earlier start (next fix round). Non-root 2787 x3.
