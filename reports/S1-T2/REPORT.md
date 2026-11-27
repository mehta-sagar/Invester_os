# Task report S1-T2 — manifest schema v2

- Built: manifest v2 (13 fields: schema 2, access_mode, request_headers with cookie values redacted, final_url, fetch_host); writer writes v2 only; reader reads v1 (access_mode None) and v2 mixed; verify flags bad v2 lines and unredacted cookies; A2 shared-lock re-read, torn-tail rules and the in-memory index kept. Collector records honest headers, final_url = response url (fallback: requested url), fetch_host = hostname. Check plants updated (one call in check/builtin.py, Orchestrator-approved scope exception).
- Tests: 144 v2 tests + 7 regression tests, written red first by the Test writer; Builder edited no tests. 847 passed, 2 skipped (Linux root), Orchestrator reran 3x. Reviewer also ran as non-root: all pass.
- Review: PASS WITH MINOR (review.md); 48 mutations, 43 killed, 2 equivalent, 3 real survivors (collector call site) now killed by the D1 test. D2 (compact-JSON 16 KiB limit) and D3 (final_url from response) fixed in 4104ebd.
- Sample run: reports/S1-T2/sample_run.txt (mixed v1+v2 store, verify clean, check manifest exit 0, planted raw cookie flagged at line 6 without echoing the secret, 2-process A31: 5,128 reads, 0 corrupt errors).
- Open: spec wording — A28 to say TypeError for a non-str header value; one line that malformed cookies become `<redacted>`. Mac run of the suite at the next Mac check.
