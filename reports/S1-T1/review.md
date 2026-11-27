# Review S1-T1 (collector amendment) - Reviewer, 27 Nov 2026

Branch task/S1-T1 @ a6ea923 (main f29f6dc). Verdict: **PASS WITH MINOR** (no critical or high defect; one Medium src defect D1 and one Medium test flake D2; both should be fixed before the first real adapter ships).

Full suite: root 1141 passed, 2 skipped; non-root (user `nobody`, repo copy) 1143 passed, 0 skipped (reports/S1-T1/review_pytest.txt). macOS run is the Orchestrator's step (C3).

## Hard rules
- Builder did not edit tests: `git log main..task/S1-T1 --format='%an %s' -- tests` lists only Invest-OS-Test-writer (2 commits: red tests, round-2 fixes). Builder commit fb91557 touches src/, docs/, config, .gitignore, reports, checkpoints only. OK.
- stdlib only: imports of src/investos/acquire and core/notify.py are fcntl, http.client, ssl, socket, zlib, subprocess, etc. Grep for requests/httpx/aiohttp/selenium/playwright/socks/webdriver/stealth/fake_useragent over src/investos/acquire and notify.py: no hits (one false hit on the word "requests" in a directory name). OK.
- No network in tests: all test URLs are 127.0.0.1/localhost servers or fake transports (other URLs are inert Referer strings or fake final_url values); `subprocess` is replaced in notify tests and a guard test asserts the default collector never calls it. OK.
- No edits to src/ or tests/ by Reviewer. No data/ file in the diff. `.gitignore` covers `data/ledger/.state/` (git check-ignore confirmed). OK.
- Scope: cli.py, config.py, notify.py, access_cli.py within brief. Nothing under manifest.py, registry/, check/ touched.

## Security-relevant rules (checked by reading, by mutation and on the wire)
| Rule | Result |
|---|---|
| Only scoped_exception may set UA/Referer/Cookie | OK. `_DENIED_HONEST` adds user-agent/referer/cookie; `_DENIED_ALWAYS` applies to scoped_exception. M01 killed. Validated on every `headers_for` call (M09 killed). |
| scoped_exception needs allowlist entry + decision file with `Adapter:` and `Access-mode:` lines, exactly one file, re-checked at every instantiation | OK (`check_decision_file`, no cache). M02, M03, M04 killed. Base-level "decision is None" re-check (M05) is not exercised because tests go through load_config (see D4). |
| Honest UA format `InvestOS/<ver> (personal research; <contact>)`, contact rules, `.invalid` refused for real transport | OK. M10-M13 killed. |
| real_browser never uses the HTTP transport | OK. Construction refuses transport with real_browser, browser with other modes (M14, M15 killed); `_do_key_inner` uses `self._browser` only; no header argument; headers only recorded. |
| Per-host block persists, fails closed | OK for unreadable/torn/invalid state (M24, M62, M63, M64 killed). Fails OPEN when the state dir is unwritable: D1. |
| Halt after 3 distinct blocked IST days in 7, per source; only `clear_halt` with a `Clears-halt:` decision file ends it | OK. M28-M33, M36, M37, M78, M80, M86 killed. |
| Daily caps | OK in one process (M39, M40, M48 killed, corrupt count fails closed M41). Cross-process atomicity not proven by a test (M42 survived, D4). |
| Header deny-list incl. A6 widening | OK for the spec's list (M07, M08, M79 killed, `accept-encoding` owned by base). Framing headers (Connection, Content-Length, Transfer-Encoding) are not on the list: D3. |
| UrllibTransport sends exactly the given headers, never follows redirects, ignores env proxies | VERIFIED on the wire with a raw-socket server (reports/S1-T1/review_probes/wire.py): with `{}` the server got `GET /a?x=1 HTTP/1.1`, `Host`, `Connection: close` and nothing else (no User-Agent, no Accept, no Accept-Encoding); with UA + AE it got those two plus the same Host/Connection; a 302 was returned with `location` and the server saw one request; HTTP_PROXY set to a dead port ignored (M84 killed); `file://` and `https://` under default schemes raise ValueError. TLS context is `create_default_context()` (certificate verification on), but untested (M22 survived, D4). |
| Notifier | Default `NullNotifier`; `OsascriptNotifier` uses an argument list, 5 s timeout, escapes `\` then `"`, strips control chars, darwin only, never raises. M34, M35, M58-M61, M71, M72 killed. |

## Spec coverage
| Rule | Code | Tests | Result |
|---|---|---|---|
| R17 mode vs config both directions | base.py:331 | A28 | OK |
| R18 decision file | access.py `check_decision_file`, base.py:337 | A29 | OK |
| R19 headers by mode | base.py:345-346, 551-560 | A30, A31 | OK (D3 for extra names) |
| R20 contact | base.py `_check_contact` | A32, A58 | OK |
| R21 transport | transport.py (http.client, not urllib.request: see Info I1) | A33-A35, A37 | OK; gzip/deflate/raw deflate, size cap on decoded body, 4x timeout; truncated body check untested (M82 survived) |
| R22 undecoded encodings | base.py:542 | A36 | OK |
| R23 real_browser | base.py:340-344, 539 | A38, A39 | OK |
| R24 block policy, per host, persisted, state re-read before every attempt incl. retries | base.py `_gate`, `_block_reason`, access.py | A40-A43, A56 | OK (M23, M25, M26, M27, M50, M51, M52 killed); D1 |
| R25 state files, strict reader, stat-signature cache, torn tail | access.py `_parse_file`, `_append`, `_Cache` | A44 | OK; host names restricted by regex (path traversal M75 killed); IPv6 / underscore hosts fail closed |
| R26 halt, notify, clear | access.py `_maybe_halt`, `clear_halt`; base.py `_record_block` | A45, A46 | OK |
| R27 cross-process limiter | access.py `HostLimiter` | A47-A49 | OK; D2 (stamp slack), M76 equivalent |
| R28 floors | base.py:496 | A50 | OK (M47 killed) |
| R29 caps | access.py `DailyCounter` | A51 | OK, D4 for M42 |
| R30 provenance, sidecar schema 2 | base.py `_store`, `_build_sidecar` | A52 | OK |
| R31 order of work | base.py `_do_key_inner`, `__init__` | A53 | OK (skip first: M49 killed) |
| R32 logging | base.py | A54 | OK; the retry log path is not covered (M74 survived) |
| R33 alert | notify.py, base.py:582 | A57 | OK |
| Config `[collect]` | config.py `_parse_collect` | test_collect_config | OK (M66-M68 killed); I2 |
| CLI | access_cli.py | A46 CLI, A58 | OK (M69, M70 killed) |

Demo: reports/S1-T1/demo_s1t1.py re-run (reports/S1-T1/review_demo_rerun.txt): honest headers on the wire as above; a 403 writes one line to `access/hosts/127.0.0.1.jsonl` and aborts; a second process makes 0 calls (server saw 0); the cap of 2 stops the 3rd key; `investos access status` shows the block; `investos check all` exits 0 (manifest, raw, migrations, registry pass) on the demo data. Output matches the saved demo_output.txt apart from ports, run ids and timestamps.

## Mutation probes
Copy at /tmp/s1t1_probe, tool reports/S1-T1/review_probes/mut.py, command `python -m pytest tests/unit/acquire tests/unit/core -x -q -p no:cacheprovider` (826 tests, 16 s). 86 single mutations over access.py, transport.py, base.py, access_cli.py, notify.py, config.py. **Killed 77, survived 9 (3 equivalent, 6 real). Score 77/86 = 90%; 77/83 = 93% excluding equivalents.**

Killed (77): M01-M04, M06-M19, M21, M23-M37, M39-M41, M43-M73, M75, M77-M81, M83, M84, M86. Short names: honest cookie/referer/UA allowed; decision check skipped or weakened (Adapter-only, >1 file); config upgrade of honest class; origin/authorization/host dropped from deny-list; headers_for checked once; UA format; empty contact; `;` in contact; `.invalid` allowed; browser+transport accepted; follow redirects (M16); default User-Agent added (M17); Accept-Encoding identity added (M18); gzip size cap off; whole-call deadline off or 40x; block not persisted (M23); fail-open on unreadable state (M24); 429/451 not blocks; block scope per source; same-day blocks counted twice (M28); window 8 or 6 days (M29, M30); threshold 4; other sources' blocks counted; cleared does not reset; notifier not called (M34); notifier exception propagates; halted not enforced; clear_halt skips decision file; cap not enforced / off by one / corrupt counter fails open (M39-M41); limiter per-process lock (M43); limiter ignores stamp, unclamped future stamp, bad stamp no wait; floor 2.5 dropped; skip-first order; state not re-read on retries (M50); 429 retried; signature scan whole body; undecoded encoding accepted; browser redirect ignored; wrong access_mode/fetch_host/sidecar schema recorded; osascript guard, escaping, timeout, notify_failed log removed; torn tail append/read; wrong date accepted; UTC day instead of IST (M65, M83); unknown config key; daily_cap and decision not required in config; CLI empty contact; clear exit code; default notifier = osascript (M71, M72); br always advertised; host regex with slash; halt appended twice; deny-list drops host; env proxy honoured (M84); clear_halt appends nothing.

| Survivor | Mutation | Verdict |
|---|---|---|
| M05 | base: scoped_exception with `decision is None` allowed (hand-built Config bypassing load_config) | real gap (D4); load_config rejects it so only defence in depth |
| M20 | raw (identity) body cap `max_body_bytes` not enforced; only the gzip bomb is tested | real gap (D4) |
| M22 | HTTPS certificate verification off | real gap (D4) |
| M38 | `clear_halt` initial "not halted" check removed | equivalent: the guard under the lock refuses the same case |
| M42 | DailyCounter lock per pid (no cross-process mutual exclusion) | real gap (D4): the 2-process cap test is not racy enough |
| M74 | cookie/headers added to the `retry` log line | real gap (D4): A54 covers the success path only |
| M76 | limiter drops the monotonic rule | equivalent in practice: a backwards wall clock leaves a future stamp, clamped to a full interval wait |
| M82 | `Content-Length` mismatch (truncated body) check removed | real gap (D4): http.client does not raise, so a truncated 200 would be stored as data (verified: the check raises "got 3 of 100 bytes") |
| M85 | real_browser class's extra_headers denied set emptied | equivalent: those headers are never sent |

Note: under 4 parallel suites on 2 cores, test A48 killed unrelated mutations (M05, M82 in the first pass); those two were re-run alone and their true result is shown above. See D2.

## Defects
- **D1 (Medium, fail-open) base.py:573-588, access.py:193-214.** If the block event cannot be written (OSError: unwritable `data/ledger/access`, ENOSPC), `record_block` raises OSError, `run()` turns it into "storage failure" and aborts, but nothing is persisted; the next run sees an empty state and calls the host again. Verified as user `nobody` with `access/` mode 0555 (reports/S1-T1/review_probes/probe2.py): run 0 and run 1 each contacted the server (server saw 2 requests). R25 says the collector fails closed. Fix: in `_gate`, before every attempt, open/create the host and source lock files (`_flock(..., LOCK_SH)`) so an unwritable state directory raises and becomes "access state unreadable"; and in `_record_block` catch OSError, log ERROR and set the in-process deny reason so the rest of the process stays blocked. Add a planted test (read-only access dir, 0 calls).
- **D2 (Medium test flake, Low src) tests/unit/acquire/test_collect_s1_limiter.py::test_A48 and access.py:382-391.** Fails about 1 in 8 when four suites run in parallel on two cores (min gap 0.177 s vs 0.18 required). Cause: the stamp is taken after the sleep but before the temp-file write, fsync and replace, and the test logs start times after `acquire` returns, so spacing of real request starts can be shorter than the interval by the write/fsync/scheduling time (macOS APFS fsync can be slower). Fix for the test: measure the stamp content, or require `>= 0.5 * interval`. Fix for src (optional): take `wall_now` after the write and only then release, or document the slack (negligible at 1 to 2.5 s intervals).
- **D3 (Low) base.py:49-51, transport.py:67-72.** `Connection`, `Content-Length`, `Transfer-Encoding`, `Expect`, `TE`, `Upgrade` are not denied; a scoped_exception `headers_for` returning `Connection: keep-alive` produces two `Connection` headers on the wire (verified), and `Content-Length: 5` is sent on a GET. Spec list does not include them either. Fix: deny them in `_DENIED_ALWAYS` (spec amendment) or have the transport raise ValueError for them.
- **D4 (Low, test gaps) tests/.** Add tests that kill M05, M20, M22 (assert the SSL context verifies, e.g. patch `ssl.create_default_context` and check it was used), M42 (many processes racing a cap), M74 (cookie not in any log line on the retry path, with a 5xx first), M82 (server sends fewer bytes than Content-Length, expect TransportError).
- **D5 (Low, unverified, by reading) transport.py:68-77.** The 4x timeout deadline is checked only in the body loop; connect and header reading are each bounded per socket operation but not by the whole-call deadline, so a server dripping header lines could exceed 4x timeout. Fix: check the deadline in a socket wrapper or use `settimeout` before each phase.
- I1 (Info) R21 says "built on urllib.request"; the Builder used `http.client` (layer under urllib). Justified: urllib.request cannot suppress `Accept-Encoding`/`User-Agent` defaults the way A33 requires. Amend the spec text and A55's "imports urllib only" accordingly. Tests pass.
- I2 (Info) `CollectConfig.access` is a plain `dict` inside a frozen dataclass, so runtime mutation could change a mode. Wrap in `MappingProxyType`.

## Deviation: wall time derived from an injected monotonic clock (base.py:374-380)
When `monotonic` is injected and `wall_clock` is not, `wall = anchor + monotonic()` with `anchor = time.time() - monotonic()` taken once at construction. **Recommend ACCEPT.** In production both default (`monotonic is None`) so `time.time` is used directly; the derived path is reachable only when a caller injects a monotonic clock without a wall clock, which the CLI factory never does. Even then the stamps are real epoch time plus elapsed injected time, so cross-process comparison stays meaningful, and a stamp that is too far in the future is clamped to one interval (R27), so the worst case is a wait of one interval, never a skipped wait. It keeps the Stage 0 fake-clock tests (A8, A10b) exact. Conditions: record it in the spec (R27/Inputs) as the defined behaviour, and prefer that the Stage 0 test Env injects `wall_clock` later; the Test writer's concern that the stamp is "meaningless" in this path is not correct, it is anchored to real time.

## Checklist against the brief "Done means"
Acceptance tests A28-A58 pass (sample red-first output in reports/S1-T1/tests_red.txt, 297 red); planted failures caught; Stage 0 tests pass or are in the superseded table; demo saved and re-run; `investos check all` passes on demo data; no data/ file committed; A58 and A57 pass; A55 grep clean; no open critical defect; non-root Linux run done. Remaining: macOS run (C3), D1 and D2 fixes.
