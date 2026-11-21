# Review S0-T4 (collector base) - Reviewer, 21 Nov 2026

Branch task/S0-T4 @ 0f31542 (main 788d3f1). Verdict: **PASS WITH MINOR** (no spec violation found; one Medium performance defect, several Low/Info).

Full suite: `python -m pytest tests -q` -> 535 passed, 2 skipped (reports/S0-T4/review_pytest.txt). Collector tests: 205 passed.

## Hard rules
- Tests untouched by Builder: `git log main..task/S0-T4 --format='%an %s' -- tests` shows only Invest-OS-Test-writer (2 commits: "collector base (red)", "fix verdict_fn binding and T7c transport order"). OK.
- Dependencies: base.py imports only stdlib (io, json, os, re, stat, time, uuid, zipfile, abc, dataclasses, datetime, enum, pathlib, typing, urllib.parse) plus investos.core.* and investos.acquire.manifest. `json` only in the sidecar. No csv/xml/html/bs4/lxml/openpyxl/pandas, no socket/urllib.request/http.client/requests/httpx anywhere in src/investos/acquire or tests/unit/acquire. pyproject deps unchanged (duckdb). OK.
- No network in tests: all HTTP through FakeTransport; grep for socket/requests/httpx clean. OK.
- Module path is `investos/acquire/base.py`, not spec's `investos.collect.base`: sanctioned by docs/decisions/0001. OK.

## Spec coverage (R1-R16)
| Rule | Code | Tests | Gap |
|---|---|---|---|
| R1 never parse | imports above | A25, T1-T3 | none |
| R2 honest headers | base.py:240-243 | A13a-c | closed deny-list only (see header facts, D4) |
| R3 rate limit | `_pace` :342 | A14, A15 | host lowercasing untested (urlparse already lowercases, equivalent) |
| R4 retries | :316-337 | A8-A10, A9b | only 500/502/503 tested; whole 5xx range not (M08) |
| R5 blocks | :326 | A11 (401/403/451) | none |
| R6 body check/downgrade | `_judge` :350 | A2-A4 | none |
| R7 four outcomes | `_finish`, sidecar | A7 | none |
| R8 stop and log | :283-288 | A11, A12 | ERROR level of per-key `unreadable` on abort not pinned (M54; run_aborted ERROR is) |
| R9 skip | :300 | A5, A6, GATE3 | none |
| R10 page limit | :366 | A16, T7c | none |
| R11 manifest only | `_store` | A1, A27 | archive skip path calls `manifest.entries()` (outside A27's "only get_by_key/store"; D5) |
| R12 archive safety | `safe_unzip` :141 | A17-A19 | "./" dir entry false-rejected (D2); empty zip accepted silently |
| R13 sidecar counts | :413 | schema test, A23 | `outputs` sort for unsorted keys() untested (N13) |
| R14 protection | :444 | A20-A22 | atomic replace/fsync not behaviourally tested (M32, N4) |
| R15 RunRange | :90, :272 | A24 | see open question (b) |
| R16 logging | `_finish` | A26 | skipped event logs `url=""` (D3) |
Edge cases (empty keys, duplicates, 3xx as-is, Retry-After ignored, page_limit<1, bad source): implemented; Retry-After ignoring and 3xx are covered by probe/tests.

## Mutation probes (on /tmp/s0t4_probe copy, `pytest tests/unit/acquire -x -q`)
72 single mutations (scripts /tmp/s0t4_tools/mut.py, mut2.py). Killed 57, survived 15. Of the 15, 6 are equivalent/redundant (M23 encrypted flag: zipfile itself refuses encrypted open and base wraps it as unsafe archive; M27 total-bytes check: `budget=min(max_total, ratio*len)` already covers; M28 / M29 each alone: the other check covers, both off together is killed (N1); M52 host lower: urlparse lowercases; N15 no-op). Real survivors (9): M08, M32, M49, M54, M57, N3, N4, N12, N13.

| Mutation | Result |
|---|---|
| skip block check / drop 451 / 400 treated as block | killed (A11[401], A11[451], A4a[400]) |
| max attempts 5 / 3; linear backoff; 2**attempts; no backoff sleep | killed (A8) |
| retry 404; retry 3xx; no retry on 429 | killed |
| 5xx range 500-504 only (505-599 not retried) | **SURVIVED** (M08) |
| drop rate-limit sleep; wait=interval; first request waits; global host key | killed (A14, A8) |
| host not lowercased | survived (equivalent) |
| re-fetch already fetched (no skip) | killed |
| skipped archive drops member outputs | killed (A19b) |
| skip non-2xx downgrade | killed (A4a) |
| page limit `>` only / `==` only; fetched_empty subject to limit; validation off | killed |
| check_body exception propagates; accept INCOMPLETE verdict | killed (A3) |
| store missing_holiday body; skip FETCHED_EMPTY store | killed |
| skip safe_unzip; `..` allowed; backslash not converted; drive letter; symlink; duplicates; empty name | killed except empty name (N3 survived, see D2) |
| encrypted allowed | survived (equivalent, zipfile refuses) |
| member count limit off; ratio off | killed |
| total-bytes limit off; member streamed limit off; member declared limit off (each alone) | survived (redundant); both member checks off killed |
| sidecar protection off; no corrupt rename; attempt name wrong; replaced flag false | killed |
| sidecar no fsync (M32); direct write instead of os.replace (N4) | **SURVIVED** (not behaviourally testable without fault injection) |
| sort_keys off; no trailing newline | killed |
| aborted status off; abort does not stop run; abort on check_body failure | killed |
| complete when keys empty; missing_holiday as gap; skipped not in received | killed |
| no key de-dup | killed |
| Referer added; cookie / x-real-ip dropped from deny-list; deny-list case-sensitive | killed |
| fetched_at wrong; source_version wrong; timeout not passed | killed |
| zip stored before members (M49) | **SURVIVED** (crash-safety ordering untested) |
| per-key unreadable-on-abort logged INFO instead of ERROR (M54) | **SURVIVED** (run_aborted ERROR remains) |
| UTC conversion replaced by tzinfo overwrite (M57) | **SURVIVED** (tests use a UTC clock only) |
| transport-error cause text lost (N12) | **SURVIVED** (reason text for transport errors not asserted) |
| `outputs` not sorted (N13) | **SURVIVED** (gaps sort is tested, outputs sort is not) |
| not_attempted reason wrong; clock before request | killed / no-op |

## Open questions from the Test writer
(a) **brotli / Accept-Encoding.** Correct per spec: R2 sends exactly `{"User-Agent": ..., **extra_headers}`; the base adds no Accept-Encoding, and `accept-encoding` is not on the deny-list, so `extra_headers = {"Accept-Encoding": "br"}` is the only route (test_collector_http.py:141 already uses gzip,deflate). Not a defect in S0-T4, but the contract is incomplete: the spec does not say whether `Response.body` is content-decoded. stdlib has no brotli, so a production Transport that cannot decode `br` would hand compressed bytes to `check_body` (fails safe as `unreadable`, never silently stored as data). Conversely httpx/requests add Accept-Encoding, Accept, Connection themselves, which would break "no other headers" on the wire. Recommend a line in the real-Transport spec: it sends only the headers given, and decodes gzip/deflate (or requests identity). Raise when the production Transport task is written.
(b) **RunRange ValueError** is raised in `RunRange.__post_init__` (base.py:90-92) at construction, so `RunRange(end<start)` never exists; `run()` re-checks (:272) as a second guard, reachable only if someone mutates the frozen object (probe confirmed both messages). Location is right for "before any call"; A24 should construct inside `pytest.raises`, not assume `run()` raises. Spec text is ambiguous, both satisfy it.
(c) **Blocked responses and check_body.** 401/403/451 are rejected at base.py:326 before `check_body`; probe: check_body not called, 1 HTTP call, reason `blocked http 403`, run aborted, a scripted following 200 is never requested. Any Akamai/HTML challenge served with HTTP 403 therefore never reaches `check_body`. A challenge page served with HTTP 200 (or 3xx, 404, other 4xx) does reach `check_body` and must be caught by the subclass. 429 and 5xx never reach `check_body` either (retried, then `unreadable` and abort after 4 attempts; probe confirmed 4 calls). Consequence: the subclass cannot classify a 403 body as `MISSING_HOLIDAY`; if a source returns 403 for absent files it will abort the run (by spec).

## Header facts (no policy judgement)
- Default request headers: exactly `{"User-Agent": "investos/0.0.0"}` (`investos.__version__`) plus `extra_headers`. Timeout 30.0 s default. Nothing else (probe printed the recorded headers).
- Referer, Origin, Cookie, X-Forwarded-For, X-Real-IP, User-Agent: never sent by the base; a subclass setting any of them (any case) gets ValueError at instantiation. The base never reads `Set-Cookie`, keeps no cookie jar, and never changes headers between attempts or after a block.
- An adapter CAN set, via class attribute `extra_headers`, anything not on that six-name list: probe showed allowed Accept, Accept-Language, Accept-Encoding, Sec-Fetch-Site, Sec-Ch-Ua, Authorization, Forwarded, X-Client-IP, True-Client-IP, Via, Host, and a value containing CRLF. So browser-like fingerprint headers other than UA/Referer/Origin/Cookie are possible, but a browser User-Agent is not (no code path). Headers are fixed per class, identical for every key and attempt.
- Cookies/UA beyond these would have to come from the (not yet written) production Transport, which the base cannot see.

## Defects
- **D1 (Medium, performance) base.py:300 and :395 with manifest.py:318**: `get_by_key` re-reads and re-parses the whole manifest per key, so a skip-only re-run is O(n^2): measured 500 keys 2.3 s, 2000 keys 38 s, 0 HTTP. Archive skips add a second full load via `entries()`. A multi-year daily range will take minutes. Fix: snapshot `manifest.entries(source)` once at start of `run()` into a dict (latest per key), or add an index/cache in ManifestStore (S0-T3 owner). A27's "only get_by_key/store" would need relaxing or the cache put in the store.
- **D2 (Low) base.py:133**: a directory entry named `./` (or `.`) makes `_normalise_member` raise "empty member name", so an otherwise safe zip is `unreadable`. Fix: check `zi.is_dir()`/trailing slash before normalising, skip dirs whose name normalises to empty. Related: a zip with zero members is accepted as `fetched` (spec silent; consider UNREADABLE or at least a log).
- **D3 (Low) base.py:303**: `skipped` event logs `url=""`; R16 lists `url` as a field. Fix: compute `self.url(key)` for the log (no HTTP) or document.
- **D4 (Low, policy pending) base.py:240**: deny-list is closed to six names; Authorization, Forwarded, X-Client-IP, True-Client-IP, Via, Host are allowed; header values not checked for CR/LF; case-variant duplicate names (`Accept` and `accept`) are not rejected. Fix after the header policy decision: allow-list or extend list; reject control characters in names and values.
- **D5 (Low) base.py:395**: archive skip path calls `manifest.entries()` and prefix-matches `key + "/"`, so a different top-level key that starts with `<key>/` would be pulled into outputs (spec already notes the `/` caveat), and it goes beyond A27's get_by_key/store contract. Fix: index API in manifest.
- **D6 (Low) base.py:30**: imports private `_check_source` from manifest. Make it public or duplicate the regex.
- **D7 (Info) base.py:381 (by code reading, not run)**: if `store()` raises midway through an archive, member lines exist without the zip line, so the re-run re-stores members (zip-last ordering is deliberate, M49 shows it is untested). Add a test with a failing manifest double.
- **D8 (Info) sidecar**: `.tmp-*` leftovers after a crash are never cleaned; two runs with the same run_id and an existing `attempt-<run_id>` file silently overwrite it; a directory/symlink at the target path gets renamed to `.corrupt-` (OSError branch). Harmless.

## Untested edges
Non-UTC aware clock (M57); transport-error reason text; 5xx outside 500/502/503 (e.g. 599); `outputs` order with unsorted keys(); manifest failure mid-archive; sidecar crash between tmp write and replace; non-dict JSON in existing sidecar (treated as non-complete, replaced, no rename, by code); keys with `/` or very long names (member name cap 400 only); huge responses held fully in memory (no body size cap on non-archive fetches); `keys()` yielding non-str raises TypeError after the range check but before any call (no sidecar); concurrent runs on the same range (no lock, last replace wins with R14 protection); IPv6/`urlparse` host edge cases and URLs without host (ValueError raised mid-run after earlier keys were stored, so no sidecar is written for that run).
