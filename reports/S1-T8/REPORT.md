# S1-T8 build report: real-browser history backfill

Branch task/S1-T8. Suite as root: `PYTHONPATH=src python -m pytest -q -p no:cacheprovider` gives 2784 passed, 5 skipped.
Tests directory unchanged: `git diff 876d12e..HEAD -- tests` is empty.

## Decision 0002a evidence (R16)

Command (every forbidden string of the test list, comments and docstrings included):

```
grep -nE 'stealth|webdriver|fake_useragent|AutomationControlled|undetected|patchright|rebrowser|camoufox|nodriver|curl_cffi|ja3|impersonat|userAgent|user_agent|extra_http_headers|add_init_script|ignore_default_args|new_cdp_session|evaluate_on_new_document|proxy|socks|jitter|import random|random\.|uniform\(|\.mouse|\.keyboard|\.click\(|\.dblclick\(|\.fill\(|\.type\(|\.press\(|\.hover\(|\.tap\(|dispatch_event|captcha|CAPTCHA|apify|scrapingbee|brightdata' src/investos/backfill/*.py src/investos/acquire/browser.py
```

Output: none (exit status 1). `known_at` appears in the backfill modules only as `known_at_rule`:
`grep -n known_at src/investos/backfill/*.py | grep -v known_at_rule` prints nothing. No backfill module imports
playwright (A41); the Playwright import stays lazy inside the existing `PlaywrightDriver`.

The only Chrome calls are the transport's fixed `goto` anchor and the one in-page `fetch`. The recording-Playwright
test (A32) sees nothing else. The run gate counts and stops; it never changes a request, header or timing.

## Files untouched (A33)

```
970fc645ac5fcc459b82ee0866206f65be14ad7291c1a6b86003824256998474  src/investos/acquire/base.py
0120d6d94ec0c19195177c0387710fd4d5fa680fc94a6221e86ffd71c4c27057  src/investos/acquire/access.py
38be5615e204bd4b2e4b5cdec0a52c9d883255e064595cbe1fa9a720da1789a8  src/investos/acquire/manifest.py
1ec1de61954395ad729991b63fae70d253e607838487f903918d4e8e3868aada  src/investos/acquire/transport.py
355beee15586b4de39463b17cee6ad60b7b5efd17a3bf478f74e61fbed2e8799  src/investos/acquire/rss.py
```

`git diff --stat` on those five files is empty.

## Files touched

New: `src/investos/backfill/{__init__,endpoints,ledger,planner,probe,collector,runner,status,cli}.py`,
`src/investos/check/backfill_check.py`, `config/backfill_endpoints.csv`,
`ops/launchd/com.investos.backfill.plist.example`, this report and `checkpoints/S1-T8-BUILD.md`.
Modified: `src/investos/core/config.py` (`[backfill]` section), `src/investos/acquire/browser.py` (C2, C3 only),
`config/investos.example.toml` (commented blocks), `src/investos/cli.py` (dispatch and usage line),
`src/investos/check/builtin.py` (one import line that registers check `backfill`; scope-adjacent).

## Sample output from the fakes (no network, no Chrome)

Two endpoints against the test site model (`BfEnv`), probe on night 0, one run the next night:

```
$ investos backfill probe   (fake NSE site, no network)
probe: complete; 31 requests
  announcements  confirmed
  pit            confirmed
{'announcements': ('2026-11-15', 31, None, True, 'confirmed'), 'pit': ('2026-11-22', 31, None, True, 'confirmed')}
$ investos backfill run
run: complete; 8 requests, 6 windows, 180 rows
  announcements  complete
  pit            complete
notifications: [('Invest OS: backfill night', '8 requests, 6 windows, 180 rows, 0 splits, 0 failed'), ('Invest OS: NSE backfill complete', '2 endpoints complete')]
```

## Notes for the Orchestrator

- The probe spends 20 to 21 requests per endpoint on a quiet site (about 45 on the default test world). Spec says about 40.
- The runner never closes the transport it is given; the CLI closes it in a `finally` (SIGTERM becomes KeyboardInterrupt).
- Ramp: night budget = ramp[min(clean nights since the last block, last step)]; a clean night is one with a
  `ran.<date>` marker under `.state/backfill`, a block writes `block.<date>`. Probe nights do not lift the ramp.
- Per-company shareholding is planned and run as one window per symbol (no splitting); no test drives it end to end.
