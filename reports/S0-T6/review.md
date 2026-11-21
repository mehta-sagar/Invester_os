# Review S0-T6 (trial registry + decision log) - Reviewer, 21 Nov 2026

Branch task/S0-T6 @ ad3122f. Verdict: **PASS WITH MINOR** (no code defect against the spec; one process gap on committing roots).

Full suite: `python -m pytest tests -q` -> 402 passed, 2 skipped (reports/S0-T6/review_pytest.txt).

## Hard rules
- Tests untouched by Builder: `git log main..task/S0-T6 -- tests` shows only Invest-OS-Test-writer and Orchestrator (merge) authors. Builder checkpoint says it did not edit tests. OK.
- Stdlib only (hashlib, json, fcntl, zoneinfo, re, os, dataclasses) plus investos.core.config / investos.check.registry. OK.
- No update/delete API: `dir(Ledger)` public = {append, head, kind, path, roots, rows, verify, write_roots}. Only two `open()` on ledger files, both `"ab"` (chain.py:228, :316), plus the lock file `"ab"` (:328). No unlink/truncate/rename/replace. OK.

## Spec coverage (R1-R13)
All rules implemented in src/investos/registry/{canon,chain,ledgers,check}.py and covered by tests/unit/registry (A1-A23 incl. R7 amended canonical-line form, chain.py:~150 `body != canonical_json(obj)`). I read each rule against code; no gap found. Order of R7 checks matches the spec (types, seq, prev_hash, hash, ts, canonical form, trailing newline).

## Mutation probes (on /tmp copy, `pytest tests/unit/registry -x`): 18 run, 18 killed
skip prev_hash check; skip seq check; hash excludes ts; non-canonical JSON (unsorted, spaced separators) in hashing; floats allowed (type check removed); UTC day boundary instead of IST; roots written for today; append without re-verify (non-strict walk); duplicate trial_id allowed; root hash check skipped; truncated-ledger root check skipped; canonical-form check skipped; flock removed; ledger opened in "wb"; clock-backwards check removed; registered_before_run truthy accepted; root-not-last-of-day skipped; write_roots pre-verify skipped. Every one failed at least one test.

## Exit-gate demo 4: can an edit be forged undetected? (probe script, 6 rows over 4 IST days, roots written)
| Attacker action (write access to the files) | Result |
|---|---|
| Edit row 2 payload, hash untouched | caught at seq 2 (chain) |
| Edit row k and recompute ALL later hashes (chain self-consistent) | chain ok, but caught by roots: seq 2, "root mismatch" (any row on a closed day up to and including the day's last row is covered, since every closed day's last-row hash covers all earlier rows) |
| Edit the last row of a closed day + rehash | caught (root mismatch at that seq) |
| Same edit AND rewrite roots.jsonl consistently | **NOT detected** (verify ok) |
| Same edit AND delete roots.jsonl | **NOT detected** (absent roots file is ok, by spec) |
| Delete trailing root lines / truncate trailing roots | not detected for those days (missing roots are allowed by spec) |
| Edit/append rows on today's open IST day (no root yet), rehash chain | not detected until the day closes and a root is written; appended rows after the last row are not detectable by the chain either |
| Append a forged valid-looking row at the end | not detected (integrity, not authenticity) |

So: with write access to both ledger and roots files an attacker can rewrite history undetectably. Detection relies on an independent copy of the roots (git history). Demo 4 as worded in the brief (editing any past row of a closed day) holds ONLY while the roots file is intact or an external copy exists.

**Are roots committed to git by anything?** No. Nothing in src calls git; spec marks this "human/Orchestrator step". Worse, see D1.

## Concurrency
8 processes x 30 appends (2 KB payloads, non-ASCII) -> 240 rows, seq 1..240, verify ok. Mutation "no flock" is killed by A19. flock is on `<path>.lock`, held across re-verify + write; OK.

## Defects
- **D1 (Medium, process/config)** `.gitignore:9` `data/ledger/` ignores `data/ledger/decisions.roots.jsonl` and `decisions.jsonl` (`git check-ignore -v` confirms). The brief says roots are "to be committed to git"; the decisions roots cannot be committed without `git add -f` or a `!data/ledger/*.roots.jsonl` exception. `registry/trials.roots.jsonl` is not ignored. Fix: add `!data/ledger/` + ignore pattern change (e.g. `data/ledger/*` then `!data/ledger/*.roots.jsonl`), and have the Orchestrator define the commit step (nightly `write_roots` + commit + push). Until then the decisions log has no external anchor.
- **D2 (Low, src/investos/registry/chain.py:~255 `_read_roots`/`_verify_roots`)** Roots file is not held to canonical-line form (whitespace/key order edits pass if values valid; harmless to integrity), and a closed day with rows but no root is accepted by design, so deleting roots is silent. Fix (optional, spec change): `registry` check could warn when a closed IST day has no root, and/or require roots to be non-empty once rows span a closed day.
- **D3 (Low, chain.py `append`, by code reading, not run)** `append` does not consult roots: with a wrong clock that still satisfies `ts >= last ts`, a row can be appended on an IST date that already has a root; later `verify` reports "root not last of day" while the append itself succeeded. Spec R4 only requires verifying the chain, so this is spec-conformant; consider refusing append when `ts` IST date <= last root date.
- **D4 (Info, chain.py `_now_ist_date`)** calls the clock twice (once to validate awareness, once for the date); harmless but a stepping clock advances twice. 

## Untested / unprobed edges
Disk-full or crash between write and fsync (partial last line is then caught by the newline guard, not simulated); lock-file permissions and NFS flock semantics; very large ledgers (O(n) verify per append); roots file with CRLF; clock set far in the future (poisons ts monotonicity permanently, by design); concurrent `write_roots` vs `append` from separate processes (both take the same lock, not stress-tested); symlinked ledger path.
