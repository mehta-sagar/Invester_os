# S1-T6 RSS poller — Orchestrator report (28 Nov 2026)

- Spec: specs/collect_rss.md + R3a (25 feeds on, confirmed by the probe), R8a (dedup ignores <lastBuildDate>; Sagar), R15a (live pubDate spellings), Orchestrator resolutions Q1-Q8.
- Probe: reports/S1-T6/PROBE_2026-11-27.md (Mac built-in browser, 22 NSE corporate feeds + Circulars + RBI + SEBI, all 200; 13 feeds churn lastBuildDate only).
- Tests: 318 (7a196c9, red 284 failed + 29 errors; reference impl 2227 green) + 26 fix tests (346bf7c) + 1 Orchestrator test (1842db6).
- Build: 9e1d9e7, cfd929c, 4c8aa0f, 8549d4a; fixes 02f1f7a; CLI clock 4d5df26. Builder never touched tests (diffs 7a196c9..8549d4a and 346bf7c..02f1f7a on tests/ are empty).
- Review 3f5fce2: PASS WITH MINOR, 90/102 mutants killed. D1 High (per-feed clock), D2/D3 Medium, D4/D5 Low: fixed. D6-D10 Info (guid .in rewrite, DOCTYPE inside CDATA, lookback cap, helper imports, an S0 zip test flake) left open.
- DECISION (Orchestrator): Builder fix of D1 left the CLI not passing the clock; I added test 1842db6 (red) and the one-line fix 4d5df26.
- Suite as non-root 3x: 2257, 2257, 2257 passed.
- Next (Orchestrator, Mac): pull, `investos collect rss --once`, check sidecars/manifest rows and the honest UA is accepted by nsearchives; then load the launchd plist.
