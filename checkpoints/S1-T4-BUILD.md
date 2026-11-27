# Checkpoint S1-T4 — Builder — 2026-11-27

## Done so far
- src/investos/calendar/{__init__,trading}.py (stdlib + core.config only); src/investos/check/calendar_check.py (check + plant), imported at the bottom of check/builtin.py (one line; the runner is unchanged)
- config/calendar/close_eras.csv (40 rows: 20 NSE press, 20 BSE assumed; BSE era_id = "BSE-<NSE id>"), sessions.csv and years.csv header only
- spec R4 wording amended (special_session may coexist with an ALL holiday, A7)
- calendar tests 337/337; full suite 1503 passed x3 as non-root (git safe.directory env needed since the worktree is root-owned)
- reports/S1-T4: tests_green.txt, sample_times.txt, check_calendar.txt

## Not done / decisions
- HOLIDAY DATA NOT SHIPPED: no 2026 NSE/BSE holiday list found in repo or specs; years.csv and sessions.csv are header only (no invented holidays). Mac probe step must load them. Until then every weekday reads UNKNOWN, `check calendar` WARNs that 2026 is not loaded.
- Planted check: zone copy under reports/calendar_plant; gap made by shortening E2-CM-cat1 (or two appended rows in a repo without tables). Repo with no config/calendar passes with a WARNING.
- special-session price_fields = ("close_price",); FO special close_method fo_session_end, CM vwap_last_30m.
- close_earliest = market_close in every row except E3/E4 CM cat1 (15:38); cas_applicable empty on FO rows.

## Next step
- Reviewer; macOS run is the Orchestrator's step
