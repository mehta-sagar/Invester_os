# S1-T4 — Trading calendar: report

- Spec: specs/trading_calendar.md (R2, R8 amended by Orchestrator after review: holiday/trading rows must not carry open/close; newest row of each group must be open-ended).
- Code: src/investos/calendar/trading.py, src/investos/check/calendar_check.py.
- Data: config/calendar/close_eras.csv — 40 rows (20 NSE `press`, 20 BSE `assumed`). sessions.csv and years.csv are header-only on purpose: no holidays are invented. The 2026 NSE/BSE holiday lists come from the official sites in the Mac probe step.
- Eras: VWAP close before 2026-08-03; CAS from 2026-08-03; from 2026-12-07 continuous trading to 15:30, CAS 15:30–15:40, F&O to 15:45, expiry on the 15:00–15:30 VWAP.
- Check output: reports/S1-T4/check_calendar.txt — pass, 26 warnings (BSE rows unverified, CAS flag unverified on non-F&O rows, 2026 holiday lists not loaded). A second run with a deliberate gap is caught.
- Review: fresh Reviewer findings D1–D9 fixed by regression tests and Builder fixes; the Builder made no test edits. Suite run 3×, all green.
- Open: load the official holiday lists; verify the BSE rows against BSE circulars.
