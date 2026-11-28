# S1-T7 phase A report (Builder, 28 Nov 2026)

## Suite
| run | result |
|---|---|
| root, Linux, no Chrome, no network | 2450 passed, 4 skipped, 0 failed |
| same, `sys.modules['playwright'] = None` for the whole run | 2450 passed, 4 skipped |
| non-root (`nobody`, copy of the tree, git safe.directory set) | 2453 passed, 1 skipped (smoke) |
| `git diff 4b939b8..HEAD -- tests` | empty |
| `git diff main -- src/investos/acquire/base.py` | empty (A37 passes with a `main` ref) |

Note: this container has Playwright 1.63.0 importable under python3.13 site-packages; the suite never imports it and passes with it blocked.

## R8(a) grep evidence on src/investos/acquire/browser.py
Every string of the R8(a) list and the R2 list, counted with `str.count` on the file text (comments and docstrings included): 0 hits for
stealth, webdriver, fake_useragent, AutomationControlled, undetected, patchright, rebrowser, camoufox, nodriver, curl_cffi, ja3, impersonat,
userAgent, user_agent, extra_http_headers, set_extra_http_headers, add_init_script, ignore_default_args, new_cdp_session,
evaluate_on_new_document, proxy, socks, jitter, `import random`, `random.`, `uniform(`, `.mouse`, `.keyboard`, `.click(`, `.dblclick(`,
`.fill(`, `.type(`, `.press(`, `.hover(`, `.tap(`, `.dispatch_event(`, APIRequestContext, page.request, context.request,
playwright.request, route.fetch, route.fulfill, page.route, context.route.
Also: one `.goto(` (the anchor), one `.evaluate(` (the fixed fetch function), no `/api/`, no `www.nseindia.com`, playwright imported only
inside `_default_playwright_factory`, launch kwargs exactly `user_data_dir, channel="chrome", headless, args, no_viewport=True, timeout`.

## Phase A state
The 23 NSE rows in config/rss_feeds.csv stay `enabled=no` (A35 green). pyproject extra `browser = ["playwright>=1.65"]` (1.65.2 is the
current release on the index at build time); `dependencies` unchanged.

## Deviations and readings (for the Orchestrator)
1. Order at first launch: profile lock, then reaper sweep, then launch (spec A4 text says sweep, lock, launch). Reason: sweeping before
   holding the lock could kill the Chrome of a concurrent live run. Tests cannot tell the two apart.
2. R17: `alerted` is set when the alarm is raised and the notification goes out at the end of the cycle (Orchestrator resolution 8).
3. The profile path matcher in PsReaper matches `--user-data-dir=<dir>` as a whole argument by regex (works for paths with spaces) for both
   the absolute and the symlink-resolved path.
4. `_check_headers` of manifest.py (private) is reused to validate the recorded request headers so the transport and the manifest cannot
   disagree.

## Orchestrator (28 Nov 2026)
- Live Mac check on 00c2dc9: smoke passed (fetch sha == navigation sha), 25/25 feeds, 63 s cycle, no Chrome left running.
- Review e744faf (rerun; the first reviewer was stopped by Sagar): PASS WITH MINOR, 40/43 mutants. D1 Medium (anchor robots.txt is a live 404): fixed by the Orchestrator, red test ba28088, then the fix. D2 Low / D3 Info open.
- Phase B: 23 NSE rows enabled; quiet feeds have a 10-year content window (Sagar). Tests updated red first.
- Suite non-root 3x: 2455, 2455, 2455 passed (1 skipped: the env-gated smoke test).
