# Checkpoint S1-T2 — Test writer — 2026-11-22

## Done so far
- baseline on main e7df0dc: 686 passed, 2 skipped
- commit 1: existing store() callers updated (tests/unit/acquire/_acq_helpers.put, tests/unit/check/_chk_helpers.put, test_acquire_audit_regressions A2 child process, test_collector_regressions seed, test_collector_core _ManifestDouble.store, test_manifest_write A2/A21/A23). A2 now asserts the 13-field v2 set (spec: "A2 applies to reading a v1 fixture; a line written now has thirteen fields").
- new: tests/unit/acquire/test_manifest_v2.py (+ _v2_helpers.py), tests/unit/check/test_check_manifest_v2.py: 144 tests
- red on old code: 142 (88 failed + 54 errors); 2 pass on purpose (v1 line with extra field stays corrupt; A6 public-API pin unchanged)
- validation: a throwaway reference v2 implementation in /tmp (not committed) turns the full suite green: 830 passed, 2 skipped (686 old + 144 new). Needs also base.py call site and check/builtin.py plant call (see open questions).
- full suite on the branch with OLD src: 157 failed, 107 errors, 566 passed (142 new red + 122 old tests red only; all TypeError from the four new required store() arguments (callers are updated, src is not). Old suite on main was 686 green.

## Coverage map (test -> spec)
- A24/R15: test_A24_* (v1 read: schema 1, None fields, to_json_line byte-exact, verify []; 10,000 v1 + 1 v2)
- A25/R15 planted: test_A25_bad_v2_line_rejected_by_every_lookup_and_verify (26 variants: schema 1/3/"2"/true/null/2.0, each missing field, int/empty final_url/fetch_host, extra field, v1-shaped with access_mode/request_headers, stealth/Honest/null mode, headers list/str/int value/null value, null headers with honest/scoped_exception), test_A25_unsupported_schema_message, test_A25_stage0_rules_still_hold_on_v2_lines, test_R15_valid_edges_accepted, test_R15_cookie_redaction_form_in_a_line
- A26/R16: test_A26_store_writes_thirteen_field_v2_line, mixed v1/v2/v1 order + latest wins, cache extension after v2 append, test_R16_* (v2 only, entry fields, read-only copy, each mode round-trips, validation before filesystem, source validated first)
- A27/R16 planted: signature (keyword-only, no default), each missing arg TypeError, invalid mode/headers/None-with-mode, nothing written; None with real_browser -> null; {} allowed for all modes
- A28/R17 planted: 19 bad header sets + boundaries (64 headers, 128 name, 4096 value, token chars, quotes/backslash/non-ASCII)
- A29/R17 planted: SECRET1/2 absent from every file and entry, caller dict unchanged, garbage cookie -> <redacted>, key case kept, quoted/unicode cookie
- A30/R18 planted: 12 bad hand-written headers (raw cookie, no-space form, partly redacted, empty cookie, Authorization, Set-Cookie, 65 headers, CRLF, dup, bad name, long value) -> bad_line exactly that line; healthy mixed store [] and no file/mtime change; verify never raises
- A31/R20: two spawned processes, 200 v2 lines with 2 KiB headers, reader zero ManifestCorruptError, final verify []
- A32/R19/R20 planted: torn v2 tail (store refuses, bytes unchanged, verify bad_line 3, runbook truncate under lock -> clean, store works again); reader waits for held lock on unterminated v2 tail; runbook mentions schema and v2; source-scan (store does not call json.dumps; to_json_line does) + behavioural v1/v2 serialisation
- A33/R18: tests/unit/check/test_check_manifest_v2.py (exit 0 on mixed; exit 1 with raw cookie, report details name file and line, secret not echoed)
- A34/R21 planted: 9 bad final_url, 9 bad fetch_host, boundaries 2048/255, non-ASCII round trip, query secrets verbatim, fragment kept for real_browser, 7 hand-written R21 violations -> bad_line
- A6 pin: spec adds no public names, pin in test_manifest_write.py unchanged; test_A6_public_api_unchanged_by_v2 re-asserts it.

## Next step
- Builder: implement manifest.py v2, base.py call site, and fix src/investos/check/builtin.py _store_blobs plant (see below); run full suite.

## Open questions
1. src/investos/check/builtin.py:114 (_store_blobs, used by the check plants) calls store() without the four new args; it will raise TypeError. The brief lists src/investos/check/ as out of scope. Builder/Orchestrator must allow that one call-site change (reference run needed access_mode="honest", request_headers={}, final_url, fetch_host).
2. to_json_line(): current code returns the line WITHOUT trailing newline; spec text ("equals the original line text exactly") is ambiguous. Tests accept either (rstrip("\n")).
3. Non-str header value: A28 says ValueError, R16 says TypeError for non-Mapping[str,str]; tests accept either for that case only.
4. access_mode None / non-str: tests accept ValueError or TypeError.
5. Cookie "name=value" pair parsing for edge forms (a pair without "=", empty name) is not fully specced; tests cover only clear cases (garbage -> <redacted>).
6. A32 "source scan" is implemented as ast check (store has no json.dumps, to_json_line does) plus behaviour; a stricter scan would be brittle.
7. A33 line number is asserted from the check report JSON details (stdout only shows file name).
8. Old test_A2 (test_manifest_write.py) was changed to the 13-field set; Reviewer should confirm that is the intended reading of the spec's supersede table.
9. Reviewer note on A31: uses spawn context; ~5 s.
## State
- branch: task/S1-T2

## Review regressions (after review 4290a87)
- tests/unit/acquire/test_s1t2_regressions.py (17 tests). Red on 63c4991: D2 (16 KiB measured on compact JSON; 16384-byte compact set refused) and D3 (final_url must be resp.url). 2 red.
- D1 collector test with real ManifestStore passes on current code by design; mutation check by hand: M30 (access_mode), M31 (request_headers), M32 (fetch_host) each now fail it; src restored.
- Non-str header value pinned to TypeError (test_manifest_v2 A28 value_int/value_none tightened); malformed/no-space cookie -> whole <redacted> pinned (7 forms). Both already pass.
- Note for Builder: after D3, existing tests use resp(url="") (default in _collector_helpers.resp); final_url must then fall back to the requested url when resp.url is empty, or those tests need a non-empty url. Test writer chose not to edit those tests; open question for Orchestrator.
- Full suite: 2 failed (the two above), rest pass.
