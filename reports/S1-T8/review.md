# Review S1-T8 (real-browser history backfill) - fresh reviewer, 28 Nov 2026

Build 9e6e1f9, tests base 876d12e, plus bf0294e (live probe findings, read and folded in). src/ and tests/ unchanged by this review.

## Verdict: PASS WITH MINOR
No critical defect. The built scope matches R1-R25 and Sagar's answers. Three Medium items (D1-D3) are real-world gaps that the live probe already exposed or that a fake cannot show; D1 and D2 must be fixed before PIT, results and corporate_actions run. Night 1 on the five confirmed endpoints (announcements, bulk, block, shareholding, board_meetings) is not blocked by them.

## Suites
- Root: `2784 passed, 5 skipped` (87 s).
- Non-root (claude): `1 failed, 2786 passed, 2 skipped`. Failure: D7 (test-only, see below).
- `git diff 876d12e..9e6e1f9 -- tests` empty. base.py, access.py, manifest.py, transport.py, rss.py untouched. Browser tests A1-A37 pass unchanged (ran again under the browser.py mutations).

## Rule trace R1-R25 (brief)
R1/R8a collector.py (check_body reads, never edits). R2 endpoints.py (loader, row_hash). R3 runner.py:654-665 (unusable_reason). R4/R5 probe.py (ladder, earliest, tod, determinism, 60 cap line 32). R6 planner.py:79. R7 planner.py:25 + runner.py:488-496. R8b runner.py:537. R9 ledger.py. R10 runner.py:464 (_repair), survey. R11 endpoints.build_url. R12 browser `_interval` + RunGate. R13 runner.py:203-224, 400-428. R14 BackfillTransport, browser.py C2/C3. R15 runner.py:431-448 (mirror to both hosts). R16 hygiene tests plus REPORT.md grep. R17 collector.make_collector_class. R18 plist. R19 status.py. R20 check/backfill_check.py. R21 notify calls. R22 inputs (human). R23 RunBrowser.fetch (gap 120 s). R24 unsplittable. R25 hygiene/no-parse. Every rule has a test; the mutation table shows which ones bite.

## Defects
- **D1 Medium: no endpoint eras (end_date).** endpoints.py, planner.py:83-84 (`last = end - 1`), probe.py `earliest`. Scenario: `pit` (old) and `results` plan windows up to yesterday although data stops in 2025; thousands of empty windows stored as `empty`, and the probe cannot mark a last date. Fix: optional `end_date` column (in row_hash), loader validation, `plan_windows` uses `min(last, end_date)`, probe measures `last_date_with_data` (mirror of earliest; bisect, about 12 requests), status shows it. Add rows `pit_gg` (starts at the switch date) and `results_integrated` (empty api_path, status to_probe, excluded from planning: the loader currently demands api_path start with "/", so it must accept an empty path for `to_probe`/`disabled` rows). Tests: planner never plans past end_date; row with empty api_path loads and is skipped by run/status; changing end_date invalidates the probe; probe finds the last date on a fake with an era switch.
- **D2 Medium: start-date floor too high for corporate_actions.** Loader rejects `start_date` before 2000-01-01 (R2); findings say rows exist before 2005-01-15 (perhaps 1995). probe `earliest` walks back at most 25 yearly steps from the table start. Fix: allow 1990-01-01 floor (R2 edit), start the table at 1995-01-01 for corporate_actions, and test that the walk-back stops at the first empty year. Test: fake with data from 1996 gives earliest 1996.
- **D3 Medium (design risk): one anchor per endpoint per night, no session refresh.** runner.py:592-595 anchors once; only a sleep gap > 120 s re-anchors. announcements needs about 570-7,700 requests (5+ h) on one page load; Akamai cookies (`_abck`, `bm_sv`, `nsit`) can expire in minutes to hours. Any 401/403 is a block (R15) and stops all endpoints and keeps the ramp low. Fix only if the Mac probe shows expiry: config `reanchor_every` (requests), via `set_anchor`, counted and spaced like any request; a 401/403 straight after a re-anchor stays a block. Test: re-anchor after N requests, counted in the budget.
- **D4 Medium/Low: silent truncation when the probe saw no cap.** With `row_cap = none`, `page_limit` is None (collector.py:238). One-day windows cannot be audited (`w.days < 2`, runner.py:628). A window that silently stops at a round number (1000, 2000, 5000) on a peak day is stored as complete. announcements showed no cap (16,981 rows in 31 days), bulk has 70, so exposure is small. Suggested: treat n in the probe's round-number set (R4c list) with n >= 100 and cap none as `unsplittable` (a recorded gap), and show the maximum n per endpoint in status. Test: cap-none endpoint, 1-day window of exactly 1000 rows is recorded unsplittable.
- **D5 Minor: CLI lacks spec flags.** cli.py:68-76 accepts only `--outside-window`, `--max-requests` (and `--json`). Spec lists `--endpoint ID ...`, `--headless`, `--dry-run`; `backfill run --endpoint x` exits 2. `--endpoint` matters for the planned re-probe after row edits (a full probe is about 320 requests). Test: `probe --endpoint pit` probes only pit.
- **D6 Minor: unclean nights count as clean for the ramp.** runner.py:695 touches `ran.<date>` on any night with a request, including stops by a non-JSON streak, `origin changed` or error. Sagar's rule is "no block and no challenge", so a challenge page that is only detected as non-JSON would still lift night 2 to 8000. Suggest: write the `ran` marker only when status is complete/incomplete/budget/window_closed/stopped, or treat a non-JSON stop as block-like for the ramp. Test: HTML-streak night then next night budget 4000.
- **D7 Minor (test, non-root): A37 tamper test.** tests/unit/acquire/test_backfill_status_check.py:201 `p.write_bytes(...)` on the manifest raw file (mode 0444, manifest.py:416) works as root only. Fix: `os.chmod(p, 0o644)` first. Root masks it.
- **D8 Minor: no hang guard.** `_endpoint` `while True` (runner.py:575) has no progress check, and the suite has no per-test timeout: mutations M03b, M05 and M06 hang instead of failing. A pending window that is skipped by the base (zero requests) could spin without the budget gate stopping it. Suggest: stop the endpoint if a loop iteration neither makes a request nor changes the ledger; add `pytest-timeout`.
- **D9 Minor: test gaps (survivors).** (a) interrupted run must not append duplicate done lines (M07); (b) `_done_line` with no manifest row must refuse (M41, no test reaches it); (c) probe 60-request cap per endpoint untested (N05); (d) audit "less" must mark needs_review (N06); (e) ledger flock (N07, concurrency test). M14 is equivalent (the gate still stops).
- **D10 Minor: ETA average includes probe requests** (status.py `_total_requests` sums every night counter, including probe nights); conservative, matches the Orchestrator's A36 contract only loosely. Note, no change needed.
- **D11 Minor: window vs market hours.** config accepts a window overlapping 08:30-16:30 Monday to Friday; the ban applies only with `--outside-window` (runner.py:406). Validate in config or apply the ban always.
- **D12 Low: calendar tables empty** (config/calendar/years.csv and sessions.csv hold only headers), so `CalendarView` returns "unknown" and the bulk_deals trading-day cut (planner.py:92) does nothing: weekend windows are requested (about 30 percent extra on 6,175 windows). Load the calendar first or accept.
- Karpathy lens: no unrequested features found; `audit_shrink` and the HTML streak are in the spec. runner.py (774 lines) holds transport, gate, ramp and run loop together; acceptable. per_company mode is built but no test runs it end to end (REPORT.md says so): treat as untested until shareholding probes as per_company (the probe says 285 windows, so per_quarter).

## Mutation table (52 probes; each run against the 10 backfill test files, plus browser tests for browser.py)
| id | mutation | result |
|---|---|---|
| M01 | cap-hit trusted (page_limit None) | caught A33 |
| M02 | split not halving | caught A15 |
| M03 | split allowed on a one-day window | caught R7 |
| M03b | one-day window split attempted by the runner | HANG (D8), not a clean catch |
| M04 | unsplittable not recorded | caught A16 |
| M05 | resume refetches done windows | HANG (D8) |
| M06 | ledger truncated instead of appended | HANG (D8) |
| M07 | repair appends duplicate done lines | survived (D9a) |
| M08 | probe gate bypass | caught |
| M09 | row_hash binding ignored | caught R5 |
| M10 | ramp ignores the block | caught S1 |
| M11 | nightly window check removed | caught |
| M12 | market-hours ban removed | caught |
| M12b | ban start 09:30 | caught A25 |
| M12c | ban includes weekends wrongly | caught A25 |
| M13 | anchor not counted in budget | caught |
| M14 | audit budget precheck removed | survived (equivalent, gate stops) |
| M15 | spacing floor 1.0 s | caught A24 |
| M16 | block does not stop the runner | caught |
| M17 | block not mirrored to the other host | caught A30 |
| M18 | .com to .in alias broken | caught C2 |
| M19 | RSS profile reused | caught C2 |
| M20 | stealth string | caught A32 |
| M20b | import random | caught A32 |
| M21 | core-first order lost | caught S2 |
| M22 | ETA 1.0 s per request | caught A36 |
| M23 | in-file date check removed | caught A17 |
| M24 | blocked ledger line missing | caught A30 |
| M25 | RunStop swallowable (Exception) | caught |
| M26 | sleep gap detection off | caught A39 |
| M27 | --max-requests ignored | caught |
| M28 | window re-check between batches off | caught A26 |
| M29 | audit never runs | caught A18 |
| M30 | audit "more" does not supersede | caught A18 |
| M31 | per-endpoint anchor removed | caught |
| M32 | HTML streak never stops | caught A29 |
| M33 | failed windows retried forever | caught A34 |
| M34 | probe age unlimited | caught A2 |
| M35 | ledger reader skips bad lines | caught A19 |
| M36 | plan includes today | caught |
| M37 | second half overlaps | caught A15 |
| M38 | budget off by one | caught |
| M39 | origin change not checked | caught A28 |
| M40 | to_inclusive ignored | caught A23 |
| M41 | no manifest row, ledger line still written | survived (D9b) |
| M42 | complete notified every time | caught A38 |
| M43 | extra ran marker (poor mutation) | survived, ignore |
| N01 | same-day block ignored at start | caught A31 |
| N02 | oldest-first lost | caught R10 |
| N03 | check overlap removed | caught A37 |
| N04 | probe block becomes a plain stop | caught |
| N05 | probe 60-request cap off | survived (D9c) |
| N06 | audit "less" no needs_review | survived (D9d) |
| N07 | ledger flock removed | survived (D9e) |
| N08 | re-anchor after sleep removed | caught A39 |
| N09 | block marker not written | caught S1 |
Not mutated (covered by tests that already name them): page.request use (hygiene list contains `page.request`, test_browser_hygiene.py:28) and "manifest duplicate on re-run" (base R9 skip, A21; only M05's hang touches it).

## Mac-probe checklist (things a fake cannot show)
1. Session lifetime: after one anchor, how many requests / how long until the first 401/403 or HTML (D3)? Run 500 requests (`--max-requests 500`) and look at the first failure.
2. Cookie warm-up: does the in-page fetch work straight after the anchor `goto`, or does it need a wait or a scroll-free settle time? Record the first data response status.
3. API JSON shapes per endpoint: `rows_path` (`data`, top-level list), date field names and formats (DD-MM-YYYY in, ISO or `dd-MMM-yyyy HH:mm:ss` out), time of day present or not; PIT old vs `pit-gg`; results `period=Quarterly` only (annual or half-yearly filings missing?).
4. Caps: bulk 70 confirmed in probe; check no silent cap for announcements 1-day peak days (results season) (D4).
5. Redirect origin: `.com` anchor lands on `.in` every time; HostLimiter pacing on both hosts; the profile `chrome-profile-backfill` is separate and the RSS poll keeps working during a night.
6. Akamai behaviour across a 5 h run: any gradual slowdown, interstitial pages with HTTP 200 (non-JSON streak) rather than 403 (see D6).
7. Mac sleep: does `caffeinate -i` hold with the lid closed or on battery? Test `pmset repeat wakeorpoweron MTWRFSU 00:25:00` and that launchd fires at 00:35 after a wake; check the `resume_after_sleep` log and re-anchor.
8. launchd: Aqua session needed (Chrome headful off-screen); the Mac zone is Asia/Kolkata; RunAtLoad at login in the daytime exits 0 with `window_closed`.
9. Real request time: 8000 requests at 2.5 s is 5.6 h of the 6 h window; if each request adds 0.5-1 s of latency the night ends at the window with fewer requests (budget not the limiter). Check the limiter spaces start-to-start.
10. Calendar tables are empty (D12); bulk deals will include weekend windows.
11. Era switch dates for PIT and results (D1) and corporate_actions depth (D2) before the plan is written.
