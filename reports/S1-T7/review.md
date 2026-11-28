# Review S1-T7 (real-Chrome transport for NSE RSS), fresh Reviewer, 28 Nov 2026

Verdict: PASS WITH MINOR (one Medium defect D1 from the live Mac run, to fix before phase B; no critical defect).

Scope: tests base 4b939b8, build 5cdf7a0 and 00c2dc9. Live Mac result accepted as given (smoke passed, 25/25 cycle ok).

## Rule trace R1-R20 (code -> tests)
R1 interface/mode: RealBrowserTransport.fetch(url), real_network, RssNseFeedCollector (A1). R2/R3 in-page fetch, fixed JS with three init keys (browser.py _FETCH_JS; A2, A3). R4 anchor, limiter before anchor and data, block statuses, origin check (A4-A6). R5 base64 chunks, length check, max_bytes (A7, A8). R6 status/headers/redirect/errors (A9-A12). R7 sent headers sanitised, `_check_headers` reuse (A13, A14). R8/R9 no-evasion strings, exact launch kwargs, headful default (A15-A17). R10 lazy launch, one-per-cycle, dead session relaunch once, `_unavailable` sticky (A18, A19). R11 watchdog, PsReaper exact `--user-data-dir=` match, TERM then KILL (A20, A21). R12 profile 0700, flock (A22). R13 routing by host, missing table, browser=None (A23-A26). R14 floor 2.5 s (A27). R15 lazy playwright import (A28). R16 host-down (A29-A31). R17 coalescing (A32). R18 plist Aqua (A33). R19 smoke (A34). R20 rows still `no` (A35). All have code and at least one test; each mutation below was caught except the equivalents noted.

## Defects
D1 (Medium) src/investos/acquire/browser.py:37 and :459. Anchor URL is `https://nsearchives.nseindia.com/robots.txt`, which the live Mac run showed answers HTTP 404 ("The file you are trying to access doesn't appear to exist"); Sagar saw the 404 page in the Chrome window. Fetches still work (same origin) but the anchor should be an existing resource, and a 404 anchor is silently accepted (only 401/403/429/451 are looked at, :463). Suggested fix: default anchor = an existing NSE RSS URL such as https://nsearchives.nseindia.com/content/RSS/Circulars.xml, configurable; log WARNING `anchor_status` when the anchor status is not 200; never retry or switch identity. The spec text (R4) names robots.txt, so the spec needs the same edit. Regression tests: (a) the default anchor is not /robots.txt and equals one of the shipped feed URLs on that host (read config/rss_feeds.csv); (b) a fake anchor answering 404 logs WARNING `anchor_status` and the data fetch still proceeds. Existing tests pin robots.txt (tests/unit/acquire/_browser_helpers.py:23, test_browser_transport.py:92), so the Test writer must update them.
D2 (Low) src/investos/acquire/rss.py (_Cycle alert path, `alerted` set at raise time, notify at cycle end via send_alerts). If the cycle is interrupted (KeyboardInterrupt/SIGTERM) or the notifier raises, the alarm is marked alerted for the IST day but never delivered; before S1-T7 a failed notify left the mark unset so it retried. Accepted by Orchestrator resolution 8, but note the lost-alert window. Suggested test: notifier raising on first call, assert behaviour is deliberate (documented) or mark only after a successful send.
D3 (Info) browser.py:269 and :481 duplicate the body-length check; mutation of the driver-side one survives (equivalent). base64 `validate=True` mutation also survives because a stripped character changes the length and trips the mismatch check. No action.

## Mutation table (43 probes, one at a time, reverted; suite tests/unit/acquire + tests/unit/check, -x)
| # | Mutation | Result |
|---|---|---|
| 1 | page.request instead of in-page fetch | KILLED |
| 2 | redirect manual dropped | KILLED |
| 3 | body as text roundtrip | KILLED |
| 4 | content-encoding not stripped | KILLED |
| 5 | pseudo-headers kept | KILLED |
| 6 | interval 1.0 (floor dropped) | KILLED |
| 7 | anchor limiter skipped | KILLED |
| 8 | second acquire after anchor skipped | KILLED |
| 9 | host-down on 5xx too | KILLED |
| 10 | host-down on block/any unreadable | KILLED |
| 11 | host-down not applied to later feeds | KILLED |
| 12 | host-down applied to other hosts | KILLED |
| 13 | alerted not set (alert twice) | KILLED |
| 14 | coalescing off | KILLED |
| 15 | close not in finally (cli) | KILLED |
| 16 | transport close skips sweep | KILLED |
| 17 | reaper matches any chrome | KILLED |
| 18 | reaper prefix match (other profile) | KILLED |
| 19 | reaper KILL without TERM wait/killall | KILLED |
| 20 | stealth arg | KILLED |
| 21 | headless default cli/transport | KILLED |
| 22 | playwright imported at module import | KILLED |
| 23 | NSE routed to honest | KILLED |
| 24 | RBI routed to browser | KILLED |
| 25 | extra launch kwarg user_agent-ish | KILLED |
| 26 | viewport emulated (no_viewport False) | KILLED |
| 27 | headless flag ignored in launch | KILLED |
| 28 | lock not taken | KILLED |
| 29 | lock leaked on close | KILLED |
| 30 | second launch after refusal | KILLED |
| 31 | anchor block status ignored | KILLED |
| 32 | anchor repeated each fetch | KILLED |
| 33 | allowed host check dropped | KILLED |
| 34 | length mismatch unchecked | SURVIVED (equivalent: _to_response repeats the check) |
| 35 | base64 non-strict | SURVIVED (equivalent in practice: dropped chars change length, caught by mismatch) |
| 36 | watchdog never fires | KILLED |
| 37 | daily cap counts via browser (cap check dropped in cycle-> n/a) request_headers always None | KILLED |
| 38 | invalid headers let through | KILLED |
| 39 | reaper not run at start | KILLED |
| 40 | profile perms 0755 | KILLED |
| 41 | rss_check ghost off | KILLED |
| 42 | host-down probe feed also skipped (setdefault->first) | SURVIVED (my mutation was flawed, equivalent) |
| 43 | sigterm handler not raising | KILLED |

Required list covered: page.request (1), redirect manual dropped (2), body as text (3), content-encoding kept (4), pseudo-headers kept (5), spacing below 2.5 s (6) and limiter bypass (7, 8), host-down on 5xx/block (9, 10), not applied to later feeds (11), alert twice a day (13), Chrome not closed in finally (15), reaper killing another profile (17, 18), stealth flag (20), headless default (21), playwright imported at module import (22), NSE routed honest (23). "Daily cap not counting retries" lives in base.py (not changed, out of scope; base tests cover it, not mutated here). 40 killed, 3 survivors, all equivalent or flawed probes (rows 34, 35, 42).

## Suites
| run | result |
|---|---|
| root, Linux | 2453 passed, 1 skipped (smoke) |
| non-root (user claude) | 2453 passed, 1 skipped |
| `git status` after probes | clean (src and tests unchanged) |
