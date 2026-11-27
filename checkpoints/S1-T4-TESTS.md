# Checkpoint S1-T4 — Test writer — 2026-11-27

## Done so far
- baseline main 284fa68: 1163 passed, 3 skipped (old suite unchanged with new tests ignored)
- tests/unit/calendar: _cal_helpers.py, conftest.py, test_calendar_{status,eras,pure,check,shipped}.py = 337 tests, A1-A23 + R1-R14 + shipped-table-vs-spec rows
- red on current src: 337 (13 failed + 324 errors; no investos.calendar, no shipped CSVs)
- validated against a throwaway reference impl in /tmp/wt_ref (not committed): 337 of 337 green, so tests are satisfiable
- tables for tests = copies of shipped config/calendar/close_eras.csv mutated via csv; guarded by test_calendar_shipped.py (row-by-row vs spec)

## Interface guesses the Builder must meet (spec silent)
1. `TradingCalendar.session_times(d, segment, category, *, exchange="NSE", allow_provisional=False) -> SessionTimes` (spec A12/A17 use SessionTimes but list no method)
2. EraRow fields = CSV column names (times datetime.time, dates date/None, public_lag_min int; settlement_window str "15:00-15:30" or (time,time); cas_* None when empty)
3. check module reads `date.today()` via a module-level `date` name (or `_today()` callable) so tests can inject (A20); check reads tables from cfg.repo_root/config/calendar; warnings/counts appear in CheckResult.message/details (tests read report JSON + stdout)
4. planted run must leave real config/calendar untouched; plant tables must live in a zone copy (config/calendar is not a zone)
5. weekday error when exchange/segment arg invalid: ValueError

## Open questions
- R4 says holiday conflicts with trading for 'specific or ALL' but A7 allows ALL holiday + FO special_session; tests follow A7 (specific wins); same-key conflict is covered by R2 duplicate.
- A9 says E3 non_fo 15:40 (unverified); E3 FO cas_applicable and E1/E2 carry-forward values unstated: not asserted.
- Defaults tested as stated: public_lag_min 30, table start 2010-01-01, E3 non_fo CAS applicable (unverified, 15:40), special session methods vwap_last_30m / fo_session_end, FO expiring E2 15:40.

## Next step
- Builder
