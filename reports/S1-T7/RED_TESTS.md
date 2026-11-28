# S1-T7 red tests (Test writer, 28 Nov 2026)

## Counts
| | passed | failed | errors | skipped | total |
|---|---|---|---|---|---|
| baseline before this task (main) | 2257 | 0 | 0 | 3 | 2260 |
| this branch, no implementation | 2273 | 170 | 7 | 4 | 2454 |
| reference implementation (scratch copy, never committed) | 2449 | 0 | 0 | 5 | 2454 |

Added: 194 tests in 6 new files plus 1 helper module (177 red, 16 inherently green because they pin what already holds:
A35 phase-A table, A37 base untouched and no-parser imports, A22 gitignore, A14 manifest refusal, A23 table column, A26 `rss` half,
A24/A33/A35 plant logic, A28 suite-import check, A34 smoke shape; 1 smoke test skipped by design). Skips on the reference: smoke,
A37 (the scratch copy has no `main` branch), 3 old ones. Run on Linux as root (uid 0); no Playwright, no Chrome, no network.

Files: `tests/unit/acquire/_browser_helpers.py` (fakes: `FakeDriver`/`FakeSession`, `PWWorld` = fake `sync_playwright` object that raises
`AssertionError` on `request`, `route`, `add_init_script`, `new_cdp_session`, `mouse`, `keyboard`..., `FakeProcTable`, `FakeReaper`,
`TimedBrowser`, `BrowserEnv`), `test_browser_transport.py`, `test_browser_driver.py`, `test_browser_rss.py`, `test_browser_hostdown.py`,
`test_browser_hygiene.py`, `test_browser_smoke.py` (R19 smoke test itself).

## Existing tests changed (3 files, spec-required)
R13 routes every URL on `nsearchives.nseindia.com` to the browser, so the old honest-mode tests that used that host as a
stand-in for "any feed" cannot pass unchanged (A37 "unchanged" is impossible read literally):
- `_rss_helpers.py`: constant `NSE` now `https://rss.example.test/content/RSS/` (an honest test host); new `NSE_REAL` keeps the real host.
- `test_rss_cycle.py`: two host literals (`host_events(...)`, the 24-feed limiter test) follow the constant.
- `test_rss_feedtable.py`: the shipped-URL assertion uses `NSE_REAL`.
No assertion was weakened; all 2257 old tests pass on the reference.

## Mutation evidence (reference implementation, 45 single-line mutations)
44 caught by a named test, 1 equivalent (length check removed in the driver only; the transport re-checks). Caught include: limiter
acquire skipped (first/second), content-encoding kept, text re-encode, reaper substring match, reaper without KILL, `headers`/`follow`
in the fetch init, extra launch kwarg, `no_viewport` dropped, pseudo headers kept, no header validation, blocked anchor ignored,
anchor origin unchecked, anchor every time, no relaunch after browser failure, relaunch after refusal, lock not released, profile 0755,
watchdog never fires, 2.5 s floor dropped, host allow-list dropped, no sweep at close, headless forced, 64 MiB cap dropped, RBI routed
to the browser, host-down on any unreadable / on 503, no host-down, no/over coalescing, no warnings, CLI without close / SIGTERM /
`--headless`, check reading one manifest / no ghost, plist without Aqua, missing extra / marker.

## A / R -> test (`-k` pattern, file)
| Spec | Tests |
|---|---|
| A1 (R1, R13) | `test_A1_*` transport |
| A2 (R3) | `test_A2_*` driver (source pin + 7 planted variants) |
| A3 (R2, R8d) | `test_A3_*` driver |
| A4 (R4) | `test_A4_*` transport |
| A5 (R4c,d) | `test_A5_*` transport (401/403/429/451, 404, left origin, host block through the adapter) |
| A6 (R4a, R14) | `test_A6_*` transport (spacing, planted no-limiter, counter, only first key pays) |
| A7 (R5) | `test_A7_*` transport (6 byte cases incl. BOM, cp1252, NUL, CRLF, 5 MiB; mismatch, bad base64, too large, cap) |
| A8 (R5 plant) | `test_A8_*` transport (round trip detector, byte-exact raw file) |
| A9 (R6a) | `test_A9_*` transport |
| A10 (R6b) | `test_A10_*` transport |
| A11 (R6c) | `test_A11_*` transport (observed, relative Location, unobserved) |
| A12 (R6d, R1) | `test_A12_*` transport |
| A13 / A14 (R7) | `test_A13_*`, `test_A14_*` transport (11 bad-record cases) |
| A15 (R8a) | `test_A15_*` hygiene (full string list, 13 planted lines, imports, one goto, one evaluate) |
| A16 (R8b,c) | `test_A16_*` driver (exact kwargs, 10 planted kwargs, 5 planted args) |
| A17 (R9) | `test_A17_*` transport and rss (CLI `--headless`) |
| A18 (R10) | `test_A18_*` transport (no browser needed, one launch, close twice, exceptions) and rss (CLI close, SIGTERM) |
| A19 (R10) | `test_A19_*` transport |
| A20 (R11) | `test_A20_*` transport (timeouts, watchdog thread) |
| A21 (R11) | `test_A21_*` transport (fake process table) |
| A22 (R12) | `test_A22_*` transport |
| A23 (R13) | `test_A23_*` rss |
| A24, A25 (R13) | `test_A24_*`, `test_A25_*` rss |
| A26 (R13) | `test_A26_*` rss |
| A27 (R14) | `test_A27_*` rss |
| A28 (R15) | `test_A28_*` driver, hygiene, rss (CLI hint) |
| A29, A30, A31 (R16) | `test_browser_hostdown.py` |
| A32 (R17) | `test_A32_*` rss |
| A33 (R18) | `test_A33_*` hygiene |
| A34 (R19) | `test_A34_*` hygiene + `test_browser_smoke.py` |
| A35 (R20) | `test_A35_*` hygiene |
| A36 (R10, R11) | `test_A36_*` transport |
| A37 | `test_A37_*` hygiene (base diff vs `main`, base imports) plus the whole old suite |

## Questions for Orchestrator
1. Names the tests need that the spec leaves open (builder may rename, with a note): `PsReaper(*, lister, killer, sleeper, monotonic)`
   (lister returns the `ps -axo pid=,command=` text); `PlaywrightDriver(playwright_factory=)` where the factory returns an object with
   `.start()` (returns the playwright object, `.stop()`); the in-page function returns `{kind,status,headers=[[name,value],...],url,
   redirected,base64,byteLength,error}` and receives the URL inside the `evaluate` argument; the anchor is `page.goto(url, wait_until="commit",
   timeout=<ms>)` and nothing else; `FetchResult` field names as in the spec.
2. A20 watchdog: tests expect a daemon thread that polls the INJECTED monotonic clock with a short real `Event.wait`, not the injected
   sleeper (a sleeper-driven loop would advance the fake clock and break every spacing test). A thread-less timer seam would need a
   constructor argument the spec does not list.
3. A15 as written cannot hold for imported modules: `base.py` and `manifest.py` contain `proxy-authorization` / `MappingProxyType`. The
   full R8(a) list is applied to `browser.py`; imported `acquire/` modules get the list minus `proxy`.
4. A33: an XML comment may not contain `--`, so the plist cannot literally say `--headless` in prose (plistlib then fails). The test asks
   for the word `headless`.
5. A22: the second-process check uses a second file descriptor in the same process (flock conflicts across descriptors), not `multiprocessing` spawn;
   same behaviour, no process cost.
6. A31: five feeds with 30 s attempts do not exhaust the 600 s budget (the fifth starts at 508 s). The planted pre-rule scenario uses 38 s attempts
   on five different hosts; the real scenario uses 38 s too.
7. Sagar's answer 4a ("at most one host-down alert per day") is tested as: second cycle the same IST day sends nothing (A32). A new day alerts again.
8. R17 forces notifications to be sent at the end of the cycle; the reference sets `alerted` when the alarm is raised and sends later, so a
   failing notifier no longer leaves the flag unset (old A19 test only checks the failure count and still passes). Say if you want the old retry-on-failed-notify kept.
9. Beyond the letter of the spec, tests also pin: a failed launch stops the Playwright driver; a profile-lock timeout is `browser unavailable: profile busy`;
   the CLI prints the install hint when an enabled feed routes to the browser and playwright is missing; host-down skips behave like the budget path.
10. A37 "git diff shows no change in base.py" is a test only where a `main` ref exists (skipped otherwise).
