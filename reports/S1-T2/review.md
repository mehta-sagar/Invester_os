# Review S1-T2 (manifest schema v2) - Reviewer, 22 Nov 2026

Branch task/S1-T2 @ 63c4991 (main 9449f0f). Verdict: **PASS WITH MINOR** (no critical or high defect; one Medium test gap at the collector call site, three Low).

Full suite: root 830 passed, 2 skipped; non-root (user `nobody`, repo copy) 832 passed, 0 skipped (reports/S1-T2/review_pytest.txt). macOS run is the Orchestrator's step (C3).

## Hard rules
- Builder did not edit tests: `git log main..task/S1-T2 --format='%an %s' -- tests` shows only Invest-OS-Test-writer (2 commits). The Builder commit touches only src/ and docs/. OK.
- stdlib only: manifest.py adds `types.MappingProxyType`, `typing.Mapping`; base.py adds `socket` (hostname only, no connection). No network anywhere in tests. OK.
- No new public names beyond tests: module-level additions are underscore-private; only the import names `Mapping`/`MappingProxyType` appear in `dir()` (Info). `ManifestEntry` gains the five spec fields with defaults. A6 pin passes. OK.
- Scope: `src/investos/check/builtin.py:114` was edited (plant call passes the four new arguments) although src/check/ is listed out of scope in the brief. It is a mechanical one-call-site change the Test writer flagged (open question 1) and is required for green. Not a defect, but the Orchestrator should record the exception.
- Edit of src/ or tests/ by Reviewer: none.

## Spec coverage
| Rule | Code | Tests | Result |
|---|---|---|---|
| R15 version-aware strict read | manifest.py `_entry_from_line` (type(...) is int, field set per version, mode, null headers) | A24, A25 (26 variants) | OK; schema 1/3/"2"/true/null/2.0 all rejected, v1 with extra field still corrupt |
| R16 writer v2 only, no defaults, validate before FS | `store` (validation precedes root/FS use) | A26, A27 | OK; TypeError for non-str mode is a reasonable reading |
| R17 headers (a)-(e) | `_check_headers` | A28, A29 | OK; caller dict not mutated; entry gets MappingProxyType copy; see D2 for the 16 KiB measure |
| R18 verify | same `_check_headers(redact=False)` + `_bad_text` in reader, so verify and lookups agree | A30, A33 | OK; sample run shows leak flagged at line 6, secret not echoed in stdout or report |
| R19 deployment + runbook | docs/runbooks/manifest-torn-tail.md paragraph mentions `schema`, v2, "update the code, do not truncate" | A32 | OK |
| R20 lock re-read, index signature | unchanged; v2 goes through same `_read_manifest` | A31, A32 | OK; sample run: 5128 reads, 0 corrupt, verify [] |
| R21 final_url, fetch_host | `_bad_text` | A34 | OK (2048/255 boundaries, http and https, fragment kept) |
Resolved items: cookie redaction (names kept, `<redacted>`, non-pair value -> whole `<redacted>`, key case kept) OK; final_url and fetch_host for every mode OK; v1 `access_mode is None`, never defaulted to honest (M10, M45 killed) OK; v1 `to_json_line` unchanged, v2 writes 13 fields OK.

## Mutation probes
Copy at /tmp/s1t2_probe; 48 single mutations (/tmp/s1t2_tools/mut.py, mut2.py), command `python -m pytest tests/unit/acquire tests/unit/check -x -q -p no:cacheprovider`. Killed 43, survived 5 (2 equivalent, 3 real). Score 43/48 (43/45 excluding equivalents is 96%; all manifest.py mutations killed except one equivalent).

| Mutation | Result |
|---|---|
| M01 store keeps raw cookie | killed (A29) |
| M02 verify/reader accepts raw cookie | killed (A30) |
| M03 writer writes v1 | killed (A26) |
| M04 drop fetch_host from line | killed (A7b, A26) |
| M05 reader accepts extra field on v2 | killed (A25) |
| M06 schema written as 3 | killed |
| M07 schema bool/float accepted | killed (A25) |
| M08 schema 1 accepted | killed (A25) |
| M09 header check skipped in reader | killed (A30) |
| M10 v1 default access_mode "honest" | killed (A24) |
| M11 null headers allowed for honest (reader) | killed |
| M12 null headers allowed for honest (store) | killed (A27) |
| M13 lock re-read removed | killed (S0-FIX A2 regression) |
| M14 index skips v2 lines | killed |
| M15-M17, M38-M40 Authorization/Proxy-Authorization/Cookie2/Set-Cookie not refused; dup check off; 65 headers | killed (A28) |
| M18 16 KiB limit off | killed |
| M19 NUL allowed in value | killed |
| M20, M47 final_url scheme check off / http rejected | killed |
| M21, M22 host edge-space / control chars allowed | killed |
| M23, M48 caller dict used / entry shows raw headers | killed (A29) |
| M24, M37 cookie key lowercased / cookie match case-sensitive | killed |
| M25 entry headers writable | killed |
| M26, M44 final_url 2049 / host 256 allowed | killed |
| M27 verify skips R21 | killed |
| M28, M29 mode unchecked in store / reader | killed |
| M34 validation moved after FS access | killed |
| M35 non-pair cookie kept raw | killed |
| M36 non-dict request_headers accepted by reader | killed |
| M41-M43 header name space; value 4097; name 129 | killed |
| M45 v1 line allowed access_mode | killed |
| **M30 base.py access_mode "honest" -> "real_browser"** | **SURVIVED (D1)** |
| **M31 base.py request_headers -> {}** | **SURVIVED (D1)** |
| **M32 base.py fetch_host -> constant** | **SURVIVED (D1)** |
| M33 builtin plant headers `{}` -> `{"Cookie":"a=b"}` | survived (equivalent: redacted, plant still caught) |
| M46 reader non-str header key/value pre-check removed | survived (equivalent: `_check_headers` raises the same error) |

## Defects
- **D1 (Medium, test gap) base.py:410-414.** The collector's `_store` call site is not asserted by any test: the collector tests use `_ManifestDouble` or ignore the new fields, so changing `access_mode`, `request_headers` or `fetch_host` goes unnoticed (M30-M32). The brief names this call site and audit C2 depends on it. Fix (Test writer, no src change): a collector test with a real `ManifestStore` that checks the stored v2 line has `access_mode == "honest"`, `request_headers` equal to the headers FakeTransport received, `fetch_host == socket.gethostname()` and `final_url` equal to the URL. Do before S1-T1 builds on it.
- **D2 (Low) manifest.py `_check_headers`, line with `_MAX_HBYTES`.** The 16 KiB limit is measured with `json.dumps(out, ensure_ascii=False)` (default separators `", "`, `": "`), not the canonical `(",", ":")` form of the stored line; up to 2 bytes per header (128) of difference, so a set that fits the spec's "serialised object" can be refused (probe: 16324 default vs 16317 canonical). Fix: add `separators=(",", ":")`. Reader and writer share the function, so no inconsistency, only strictness.
- **D3 (Low) base.py:412-414.** Brief says `final_url=resp.url`; the code passes the requested `url`. Equal today (redirects not followed), but a transport that follows a redirect would be recorded wrongly. Fix: pass the final response URL into `_store` (archive members use the same response URL).
- **D4 (Info) manifest.py `ManifestEntry`.** The public constructor accepts any combination (e.g. `schema=2` with None fields, or schema 3, which serialises as v1). Not reachable through the store or reader; no action needed unless the entry is built by other modules.
- **D5 (Info) check CLI stdout.** `investos check manifest` prints `bad_line [bse.jsonl]`; the line number appears only in the JSON report details (`line 6: cookie header value is not redacted`). The brief's "names the line" holds via the report. Pre-existing runner format; consider including the line in stdout later.

## Sample run
reports/S1-T2/sample_run.txt: mixed store (2 v1 + 3 v2 lines: honest, scoped_exception with redacted cookie, real_browser with null headers and fragment URL), reader view (v1 `access_mode None`), `verify()` -> `[]`, `investos check manifest` exit 0, then hand-added raw-cookie line -> verify `bad_line` line 6, check exit 1 (secret not echoed in stdout or report), and the 2-process A31 run (writer 200 v2 lines with 2 KiB headers; reader 5128 reads, 0 `ManifestCorruptError`, verify []). Hostname shows as the VM's.

## Test writer's open questions
1. builtin.py call site: accept; mechanical, one site, needed for green. Orchestrator to record the scope exception.
2. `to_json_line()` without trailing newline: accept. It matches stage 0 (`store` adds "\n"); "text exactly" means the line text.
3. Non-str header value: code raises TypeError (R16: not a `Mapping[str, str]`) while A28 says ValueError. Keep the code; fix the spec wording (A28 "ValueError or TypeError"). Reader/verify report it as `bad_line` either way.
4. `access_mode` None/non-str: TypeError in code; fine, tests accept both.
5. Cookie edge forms: behaviour is safe-fail (probed): `a=b;c=d` (no space), `a`, `=x`, `a=b; `, `a = b`, leading space -> whole `<redacted>`; `a=b=c` -> `a=<redacted>`; `a=b ; c=d` -> name `a`, `c`. Never leaks a value. Suggest one spec line pinning "no-space or malformed -> whole `<redacted>`".
6. A32 source scan as ast plus behaviour: adequate; a text scan would be brittle.
7. A33 line number only via report JSON: acceptable (D5).
8. Old A2 changed to 13 fields: correct reading of the supersede table ("a line written now has thirteen fields").
9. A31 uses spawn, about 5 s: acceptable; ran green here and in the sample run.
