# Checkpoint S1-T1 — Test writer — 2026-11-27
## State
- branch task/S1-T1 (base f29f6dc). Tests written, red output in reports/S1-T1/tests_red.txt.
- New tests: 299 in 8 files. Red: 297 (287 failed + 10 fixture errors). Green on purpose (guards): A55 base.py imports, env-override typo guard (existing behaviour).
- Old suite: 842 pass + 2 skip. Baseline 847 minus 5 (see "Superseded" below).
## Next step
- Builder: make tests/unit/acquire/test_collect_s1_*.py, tests/unit/core/test_notify.py, test_collect_config.py green.

## Test -> rule map (files under tests/unit/)
- acquire/test_collect_s1_access.py: A28 (R17), A29 (R18), A30/A31 (R19), A32 (R20), A53 order of failures (R31)
- acquire/test_collect_s1_transport.py: A33-A35, A37 (R21, local 127.0.0.1 server), A36 (R22), A55 (R21/23/33 source guards), collector over UrllibTransport end to end
- acquire/test_collect_s1_block.py: A40, A41 (R24), A42 (R24/R31, midnight, re-read before retries), A43 (2 processes), A44 (R25 fail closed, torn tail, concurrent writer), A45 (R26 window/halt), A46 (R26/R33 notify, clear_halt), A54 (R32 logs), A56 (per-host scope)
- acquire/test_collect_s1_limiter.py: A47-A49 (R27 HostLimiter, 2 processes), A50 (R28 floors + example config), A51 (R29 caps, DailyCounter, 2 processes)
- acquire/test_collect_s1_browser_prov.py: A38, A39 (R23), A52 (R30 provenance + sidecar schema 2, schema 1/3 sidecars), A53 skipped-first (R31)
- acquire/test_collect_s1_cli.py: investos access status|clear (A46 CLI), A58 (R20)
- core/test_notify.py: A57 (R33)
- core/test_collect_config.py: [collect] config section (A28 last clause, A51 load_config part)
- helper: acquire/_s1_helpers.py (S1Env builds cfg through load_config; FakeBrowser, FakeNotifier, LocalServer, spawn workers); acquire/_collector_helpers.py Env now gives cfg.collect (contact test@example.invalid) when CollectConfig exists, and creates tmp/ledger.

## Superseded / edited old tests (removed, listed for the report)
- test_collector_http.py: test_A9_transport_error_then_429_then_ok (429 now a block -> A40), test_A13a_user_agent_is_the_configured_honest_one, test_A13b_declared_extra_headers_are_sent_and_nothing_else, test_A13d_headers_unchanged_across_retries (replaced by A30/A31). A13c kept (still valid).
- test_acquire_audit_regressions.py: "Accept-Encoding" removed from test_A6_guard_ordinary_headers_still_allowed (now denied, R19).

## Open questions / assumptions the Builder or Orchestrator must settle
1. HostLimiter/DailyCounter take state_dir = <ledger>/.state; files at .state/ratelimit/<host>.last|.lock and .state/requests/<source>.<IST date>.count (spec text is ambiguous whether state_dir already includes ratelimit/ or requests/). Tests assume the former.
2. A58 "CLI factory": spec does not give module or signature. Test assumes investos.acquire.access_cli.build_collector(cfg, cls) raising ValueError or SystemExit(1) with message "collect.contact is not configured". Confirm or adjust.
3. A33: with headers={} the spec says the server receives no Accept-Encoding, but http.client adds "Accept-Encoding: identity" unless skip_accept_encoding is used (not reachable from a plain urllib opener). Test follows the spec; Builder needs an HTTPConnection subclass/handler, or the spec should relax it.
4. AccessState.record_block on a torn tail raises AccessStateError (spec: "refuse to append"); bad host names raise ValueError or AccessStateError.
5. A31 "headers_for returns Host on 2nd call raises ValueError": test expects run() to propagate it (1 request made, none for key 2).
6. Brotli fake exposes both decompress() and Decompressor().process(); real API use is up to the Builder.
7. A42/R24: a key not attempted because its host is blocked mid-run is asserted as gap outcome not_attempted (retry case also accepts unreadable); reason text for later keys after a mid-run host block is not asserted.
8. A46: notifier exceptions are swallowed by the collector (spec: "a halt also writes this line even if the notifier fails").
9. CLI status output format is loose: source name, "halted", last-5 events by marker; exit 1 or 2 accepted on unreadable state.
10. Config: AccessEntry.mode compared as str or AccessMode (str enum) both fine; tests use plain strings via load_config.
