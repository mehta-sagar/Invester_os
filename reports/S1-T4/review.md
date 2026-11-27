# Review S1-T4 (trading calendar, closing-price eras) - Reviewer, 27 Nov 2026

Branch task/S1-T4 @ 9aaa0c5 (main 284fa68). Verdict: **PASS WITH MINOR** (no critical or high defect; 1 Medium process defect D1, 4 Low test gaps/evidence gaps).

Full suite: root 1500 passed, 3 skipped; non-root (`nobody`, fresh git copy) 1503 passed, 0 skipped (reports/S1-T4/review_pytest.txt). Calendar tests 337/337. macOS run is the Orchestrator's step.

## Hard rules
- Builder edited no tests: `git log main..task/S1-T4 -- tests` shows only Invest-OS-Test-writer (2 commits); `git diff main..task/S1-T4 -- tests` has 0 removed lines; Builder commit 9aaa0c5 touches no tests/. OK.
- stdlib only: trading.py imports csv, re, dataclasses, datetime, enum, pathlib, zoneinfo, investos.core.config. calendar_check.py adds shutil, collections. No network name (grep urllib/socket/http/requests: none). OK.
- No clock in the calendar module: grep `today|now(|time.time` in trading.py only hits the docstring. `date.today()` is only in check/calendar_check.py (spec R5 asks for it there). OK.
- No file write in the calendar module: the only `open(` is read mode (trading.py:122). The check writes only into the temp reports-zone copy for the plant (`_setup`, `_plant`); config/calendar is never written (planted run left it untouched; tests cover). OK.
- No data/ in the diff. Brief scope: see D1 (specs/ and builtin.py touched).

## Spec coverage
| Rule | Code | Result |
|---|---|---|
| R1/R3 status resolution, ALL vs specific | trading.py day_status, _session_row | OK (M01-M04, M45 killed) |
| R2 strict load | _read, _load_* | OK (M05, M09, M34, M36, M41, M46 survived only for the open_ist-on-trading check) |
| R4 conflict | _load_sessions end | OK in code (verified by hand: ALL holiday + CM trading raises, line named); test gap D2 |
| R5/R13 check, years warnings | calendar_check.py | OK (M28, M29, M30 killed) |
| R6 era choice, special override | era_for, session_times | OK (M13, M14, M31, M32 killed) |
| R7 unverified uses latest close | CSV E3/E4 non_fo | OK (M24 killed); warning text test weak D3 |
| R8 integrity | _check_eras | OK (M07, M08, M43, M44 killed); cas_end rule untested (M42 survived, D2) |
| R9, R10 lag from era row, known_at max | after_close_public_at, known_at | OK (M10, M11, M12, M39 killed) |
| R11 two fields | PriceFacts, session_times | OK (M16, M17, M33 killed) |
| R12 verified counts | check | OK; all NSE press=20, BSE assumed=20 |
| R14 purity, TypeError on datetime | _check_date | OK (M18 killed) |
Extra (not in spec but required by tests): `session_times`, `all_eras`, `loaded_years` public methods. Fine.

## Data truth (config/calendar/close_eras.csv vs spec table and brief)
All 40 rows compared by eye against the spec table, plus programmatic check of market_close and public (=close+30) for 6 dates (2026-08-02/03, 12-06/07, 2027-02-07/08) x 5 categories x NSE/BSE: 0 mismatches, tz Asia/Kolkata, known_at with a pre-publication fetch equals public time.
| Era / group | Spec | CSV | Result |
|---|---|---|---|
| E1 CM cat1/non_fo, FO x3 (to 2026-08-02) | 15:30, none, VWAP / session end | same, lag 30 | OK; `press` is a stretch, see I1 |
| E2 CM cat1 (08-03..12-06) | 15:15, CAS 15:20-15:35, close 15:35 | 15:15 / 15:20 / 15:35 / 15:35 | OK |
| E2 CM non_fo | 15:30, no CAS | same, cas_applicable no | OK |
| E2 FO x3 | 15:40 (expiring also 15:40) | 15:40 | OK |
| E3 CM cat1 (from 12-07) | 15:30, CAS 15:30-15:40, close 15:40, earliest 15:38, post-close 15:50-16:00, carry no | all match | OK |
| E3 CM non_fo | same CAS, unverified, 15:40 | match | OK |
| E3 FO index_fo, stock_fo | 15:45, window 15:00-15:30 | match | OK |
| E3 FO index_fo_expiring | 15:30, window 15:00-15:30 | match | OK |
| E4 (2027-02-08 open) | same as E3, carry yes both CM | match; cat1 and non_fo carry yes | OK |
| BSE (20 rows) | copies, `assumed` | `BSE-<id>`, assumed, same times | OK; BSE E2/E3/E4 timings and CAS are an unverified copy of NSE: every one WARNs |
- Verified labels: every NSE row is `press`, none `circular`. No row claims more than press. I1: E1 rows (2010 to Aug 2026 history) are labelled press although the cited source (SEBI coverage Oct 2026) says nothing about 2010-2025; spec-mandated, so Info.
- Holidays: years.csv and sessions.csv are header only. No holiday or trading day invented. Shipped calendar: 2026-08-02/03, 12-06/07, 11-16 all read UNKNOWN; `market_close_ist` raises NotATradingDay (never open). The check WARNs that 2026 is not loaded.
- Sample file reports/S1-T4/sample_times.txt reproduces the spec numbers; it states the dates are fixture-confirmed.

## Point-in-time
after_close_public_at = market_close + public_lag_min (30) for every era, always at or after the true close (and after the E3/E4 cat1 post-close end 16:00: 16:10). `close_earliest` (15:38) is never used for publication. E3/E4 non_fo takes the latest 15:40 (conservative). Boundary dates: 08-02 16:00, 08-03 16:05 (cat1) / 16:00 (non_fo) / 16:10 (FO); 12-06 16:05; 12-07 16:10 CM, 16:15 FO, 16:00 expiring FO. Era chosen by trade date, not fetch time. known_at never earlier than public. Caller risk (Info I2): passing `index_fo_expiring` on a non-expiry day, or `cat1` for a non-cat1 stock, gives an earlier close than the truth; the module cannot know.

## Mutation probes (copy in /tmp/rv_t4, calendar tests, `-x`)
| # | Mutation | Result |
|---|---|---|
| M01 | weekday -> TRADING | killed |
| M02 | Sunday not closed | killed |
| M03 | unloaded year -> PROVISIONAL | killed |
| M04 | ALL row beats specific | killed |
| M05 | holiday_list may confirm trading | killed |
| M06 | R4 conflict check off | SURVIVED (D2) |
| M07 | era gap check off | killed |
| M08 | era overlap check off | killed |
| M09 | close_ist == open_ist allowed | killed |
| M10 | public lag ignored | killed |
| M11 | known_at min not max | killed |
| M12 | naive fetched_at accepted | killed |
| M13 | effective_to exclusive | killed |
| M14 | special session ignored | killed |
| M15 | provisional always allowed | killed |
| M16 | settlement field dropped | killed |
| M17 | PriceFacts default close_price 0.0 | killed |
| M18 | datetime accepted | killed |
| M19-M25 | CSV edits (E3 cat1 close, E2 FO, BSE press, settlement window, carry flag, non_fo unverified, lag 0) | all killed |
| M26 | check: unverified WARNING off | SURVIVED (D3) |
| M27 | check: assumed WARNING off | killed |
| M28 | check never fails | killed |
| M29 | December warning off | killed |
| M30 | plant made harmless | killed |
| M31-M34 | special era label, exchange ignored in era_for, close_earliest ignored, duplicate era_id | killed |
| M35 | newest era may have effective_to | SURVIVED (D4) |
| M36-M41 | duplicate year, years per exchange, lag fixed 30, special FO method, exchange validation | killed |
| M42 | market_close < cas_end check off | SURVIVED (D2) |
| M43-M45 | continuous_end, post_close, holiday status | killed |
| M46 | open_ist allowed on trading/holiday rows | SURVIVED (D4) |
| M47 | era rows not sorted before gap check | SURVIVED (D4) |
| M48 | BSE assumed warnings off | killed |
48 probes (M38 did not apply, not counted): 43 killed, 5 survived.

## Defects
- **D1 Medium (process) specs/trading_calendar.md:90, src/investos/check/builtin.py:158.** Brief says specs/ is out of scope and the check is "only a new registration module"; the Builder amended spec R4 (special_session may coexist with an ALL or other-segment holiday) and added an import line to builtin.py. The amendment is needed to reconcile R4 with A7 and is sound, but the Orchestrator must ratify it (and builtin.py import is the only way the registry sees the check). Fix: Orchestrator signs off the R4 text, or revert and amend via the Spec writer.
- **D2 Low (tests) tests/unit/calendar/test_calendar_status.py, test_calendar_eras.py.** No test for R4 with an `ALL` holiday plus a segment `trading` row (M06) and none for `market_close < cas_end` (M42). Code is correct (hand probe raised "conflict on 2026-11-16 NSE: trading (CM, line 3) vs holiday (ALL, line 2)"). Fix: add both planted failures.
- **D3 Low (tests) test_calendar_check.py.** The "unverified WARNING" assertion passes even with that warning removed (M26): it is satisfied by the BSE `assumed` lines that name the same era ids. Fix: assert the literal `cas_applicable unverified` for NSE and BSE non_fo.
- **D4 Low (tests, behaviour not in spec) trading.py:218, 163-164, 247.** Untested: newest era row with an `effective_to` is rejected by the code (spec R8 only says the open row must be last, so a closed final row is arguably legal; today it loads then fails at `era_for`) (M35); `open_ist`/`close_ist` must be empty for non-special rows (M46, spec says "empty"); unsorted CSV rows are sorted before the gap check (M47). Fix: add tests, or relax M35 to match the spec.
- **D5 Low (evidence) reports/S1-T4/check_calendar.txt.** Brief wants the `check calendar` output with the WARNINGs; the saved file has only the summary line (the WARNINGs live in the JSON report). Reviewer's run printed all 26: 2 NSE unverified, 20 BSE assumed, 2 BSE unverified, 2 year-not-loaded. Fix: save the details from the report.
- Info I1: `press` on E1 rows is spec-mandated though the source says nothing on 2010-2025. Info I2: category supplied by the caller. Info I3: `_TABLE_DIRS` in calendar_check.py is module-level state keyed by the reports zone path; harmless in tests, but leaks entries across runs.
