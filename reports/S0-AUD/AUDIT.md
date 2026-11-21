# S0-AUD — Independent audit of Stage 0 (Foundation)

Auditor: independent auditor (fresh context, run 2 finishing an earlier cut-off run). Date: 2026-11-21.
Audited: `main` @ 12c834e (branch `audit/S0`; audit files only, src/ and tests/ untouched).
Method: read docs, specs, briefs, task reports, reviews and all of `src/investos/`; re-ran suite and exit-gate demos; ran my own probes (`reports/S0-AUD/probes/`) and 36 single mutations (`probes/mutations.py`, results in `mutation_results.json`) on copies under /tmp.

## Overall verdict: **PASS WITH CONDITIONS**

All four exit-gate demos pass, and I reproduced them myself. The suite is green: 558 pass and 2 skip as root, 560 pass as non-root. I found no blocker for stage 0 itself.

I did find seven defects that must be fixed before stage-1 work starts:
- A1: a raw file swapped for a symlink passes every check.
- A2: unlocked cross-process reads produce false corruption errors.
- A3: a fresh clone cannot start.
- A4: runtime files are written into git-tracked folders.
- A5: the test suite fails on macOS.
- A6: the header deny-list is open.
- A8: there is no repair path for a torn manifest tail.

There is also a paperwork gap (A15).

## 1. Exit gate (evidence: `reports/S0-AUD/demo_output.txt`, re-run in this session from /tmp/aud with identical PASS lines)

| # | Gate claim | Result | Evidence |
|---|---|---|---|
| 1 | A collector fetches one file (manifest row, raw read-only) | PASS | demo_output.txt l.4-20: 1 HTTP call, 1 row with 8 fields, `<raw>/<src>/<sha[:2]>/<sha>`, mode 0444, name = sha256, sidecar complete, honest headers, raw git-ignored |
| 2 | A broken file is rejected | PASS | l.21-28: 200 + error page → `unreadable` (the spec's name for `rejected`), no row, nothing in raw tree, key in sidecar gaps |
| 3 | A re-run fetches nothing | PASS | l.29-36: all `skipped`, 0 HTTP, 0 sleeps, manifest unchanged, also from a separate process |
| 4 | Editing a past registry row breaks the chain | PASS | l.37-79: edit seq 2 → `investos check registry` exit 1 "broken at seq 2"; a re-hashed forgery is caught by the roots at seq 4. Known limit: forgery plus a deleted roots file passes (l.79), so the roots must be anchored in git |
| — | Planted harness | PASS | all 4 plants caught (manifest, raw, migrations, registry) |

## 2. Suite results (`reports/S0-AUD/suite_runs.txt` + this session)
- Run 1: 3 runs as root, each 558 pass and 2 skip. The 2 skips are root-only directory-mode tests.
- Run 1: 1 run as non-root user, 560 pass and 0 skip.
- Run 2 (this session, /tmp/aud copy): 558 pass and 2 skip.
- Not yet run on macOS. That run **will fail** (A5).

## 3. Traceability (41 rules across all 5 specs)

"Kill" names the mutation that proved the test would go red. X and T mutations are mine; M mutations are the reviewers'. "partial" means one mutation in the same rule survived.

| Spec / rule | Code | Test that fails if broken | Evidence |
|---|---|---|---|
| config R1 resolution order | core/config.py:162-177 | test_config.py::test_A1_config_resolution_order | T1 killed |
| config R3 env typo guard | config.py:62-69 | test_A6_env_typo_and_non_numeric | M4 killed |
| config R4 zone nesting | config.py:208-213 | test_A8_duplicate_and_nested_zones | M8 killed; **partial**: X9 (one direction only) survived |
| config R6 raw not in git | config.py:139-153, 215-217 | test_A11_raw_not_ignored_refuses | M3 killed |
| config R8 timezone | config.py:125-131 | test_A13_timezone_must_be_kolkata | M11 killed |
| log R10 run_id once per process | core/log.py:373-377 | test_log.py::test_A16 | M17 killed |
| log R11 UTC conversion | log.py:395-400 | test_A17_clock_converted_to_utc | T2 killed |
| log R12 never raise | log.py:403-422 | test_A19 | deviation: NaN raises (A11) |
| db R13 read-only open | core/db.py:332-337 | test_db.py::test_A22_read_only | T3 killed |
| db R14 gap / duplicate | db.py:273-285 | test_A23_discovery_errors_before_apply | X10, X11 killed |
| db R16 tamper refusal | db.py:266-271 | test_A26_tamper_refusal | M1, M14 killed |
| manifest R2 atomic write | acquire/manifest.py:275-296 | test_A7, test_A8, test_D3a/b | M1, M4 killed |
| manifest R4 no overwrite | manifest.py:264-267, 298-313 | test_A5_tampered_existing_file_not_overwritten | M5 killed |
| manifest R6 flock | manifest.py:228-257 | test_manifest_concurrency.py::test_A9, A10 | M7, M25 killed |
| manifest R7 torn-tail guard | manifest.py:238-242 | test_A11_truncated_last_line | T14 killed |
| manifest R9 latest wins | manifest.py:379-380 | test_A14_lookup_latest_and_all | M19 killed |
| manifest R10 verify kinds | manifest.py:405-488 | test_A15, test_A16, test_D1b | M14-M18 killed; `symlink` not used by any check (A1) |
| manifest R14 non-bytes TypeError | manifest.py:204-205 | test_T7_non_bytes_data_is_typeerror | T4 survived (equivalent for str: hashlib raises TypeError; bytearray is accepted) |
| manifest index cache (D1 fix) | manifest.py:323-368 | none | **X1, X2 survived** (A7) |
| collect R2 honest headers | acquire/base.py:41, 248-251 | test_A13c_spoofing_headers_rejected | reviewer kills; list is open (A6) |
| collect R3 rate limit | base.py:350-356 | test_A14a_spacing_on_one_host | X3 killed |
| collect R4 retries | base.py:324-345 | test_A8, test_A9_transport_error_then_429_then_ok | X15 killed |
| collect R5 blocks final | base.py:40, 334-336 | test_A11_block_is_final_and_aborts[403] | T5 killed |
| collect R6 non-2xx downgrade | base.py:372-373 | test_A4a_non_2xx_with_acceptable_body_is_unreadable | T15 killed |
| collect R9 skip on hit | base.py:308-312 | test_GATE3_rerun_fetches_nothing | reviewer killed |
| collect R10 page limit | base.py:374-376 | test_A16_page_limit[100-incomplete] | T6 killed |
| collect R13 key de-dup | base.py:282 | test_A23_duplicate_and_empty_keys | X20 killed |
| collect R14 sidecar protection | base.py:458-470 | test_A20_gap_run_never_overwrites_complete_sidecar | T7 killed |
| collect run_id guard | base.py:255-257 | none | **X4 survived** |
| registry R1 int bound | registry/canon.py:27-30 | test_A2_forbidden_values_rejected[int_2_53] | X7 killed |
| registry R3 genesis | chain.py:222-223 | test_A3_row_hash_matches_published_vector | T8 killed |
| registry R4 clock backwards | chain.py:220-221 | test_A5b_clock_moved_backwards | X19 killed |
| registry R9 IST day | chain.py:87-89 | test_A13_one_root_for_closed_ist_day | T9 killed |
| registry R10 root date | chain.py:277-278 | test_A15c_root_date_not_matching_ist_date_of_row | X6 killed |
| registry R10 roots file format | chain.py:242-243 | none | **X5 survived** (missing final newline accepted) |
| registry R6 decision date canonical | ledgers.py:97-103 | none | **X8 survived** (3.11 `fromisoformat` accepts `20261121` and week dates; the equality guard is untested) |
| registry R12 concurrency | chain.py:322-336 | test_A19_four_processes_25_appends_each | X14 (LOCK_SH) killed |
| registry R13 check uses roots | registry/check.py:155 | none | **X12 survived** (A7). Only the audit demo catches it |
| check R4 exit code | check/cli.py:327 | test_A17_noop_plant_for_builtin_is_missed | T10 killed |
| check R7(g) missed plant | check/runner.py:231-232 | test_A17_noop_plant_for_builtin_is_missed | T16 killed |
| check R8 real data untouched | runner.py:256-261 | test_A11_defective_plant_writing_real_path_is_error | T11 killed |
| check R11 manifest kinds | check/builtin.py:18 | test_A14_* | **T12 survived** (size_mismatch alone is never tested) |
| check R12 raw kinds | builtin.py:19 | test_A15_raw_writable_file_fails_manifest_still_passes | X18 killed |
| check R13 database missing | builtin.py:80-83 | test_A16_migrations_database_missing | T13 killed |

## 4. Mutation table (my own; areas the reviewers did not probe)

Results are in `mutation_results.json` (X) and the T-batch above. Each mutation is one change on a fresh /tmp copy, and the subset of tests named in mutations.py is run.

| ID | Mutation | Result |
|---|---|---|
| X1 | `_note_append` extends the cached index without checking the pre-write signature | **SURVIVED** |
| X2 | Index signature = inode only (size/mtime/ctime ignored) | **SURVIVED** (no test has another store or process append, then look up again) |
| X3 | `_pace` records a start time one interval early | killed (test_A14a) |
| X4 | run_id format check removed (sidecar names are built from run_id) | **SURVIVED** |
| X5 | Roots file without final newline accepted | **SURVIVED** |
| X6 | Root-date-vs-row-date check removed | killed (test_A15c) |
| X7 | Canonical int bound off by one | killed (test_A2[int_2_53]) |
| X8 | Decision date: `isoformat()==d` guard removed | **SURVIVED** |
| X9 | Zone nesting checked in one direction only | **SURVIVED** |
| X10 | Migration contiguity check removed | killed (test_A23[files0]) |
| X11 | Duplicate migration version check removed | killed (test_A23[files1]) |
| X12 | `registry` check calls `verify(roots=False)` | **SURVIVED** (exit-gate demo 4's forgery case depends on this) |
| X13 | Raw walk skips all dotfiles instead of `.tmp-*` | **SURVIVED** (a writable `.x` file in raw would be hidden) |
| X14 | Ledger lock LOCK_SH instead of LOCK_EX | killed (test_A19) |
| X15 | TransportError treated as final, not retried | killed (test_A9) |
| X16 | Harness copytree `symlinks=True` (the reviewers' M14 survivor) | killed now (test_RM14) |
| X17 | Index cached even if file size changed between stat and read | survived (not observable without a race; equivalent in tests) |
| X18 | `writable_file` dropped from raw kinds | killed |
| X19 | Clock-backwards check removed | killed |
| X20 | Duplicate keys not collapsed | killed |
| T1-T16 | 16 traceability mutations (table above) | 14 killed; T4 (equivalent) and T12 survived |

Totals: 36 mutations (20 X + 16 T), 25 killed, 11 survived. Of the survivors, 2 are equivalent or unobservable (X17, T4) and 9 are real test gaps (X1, X2, X4, X5, X8, X9, X12, X13, T12). X12, X1 and X2 matter for stage 1 (A7).

## 5. Leads from the checkpoint

| Lead | Verdict | Evidence |
|---|---|---|
| `symlink` kind not mapped to any check | **CONFIRMED** (A1) | probes/p_symlink.py: (a) an extra sha-named symlink to an outside file and (b) the manifest-referenced raw file replaced by a symlink to a 0666 outside file. In both cases `verify` reports `symlink`, but `investos check all` exits 0 (all pass). The S0-T5 regression tests RM14 only cover harness safety, not detection |
| Unlocked manifest lookups vs a concurrent writer in another process | **CONFIRMED** (A2) | probes/p_race_low.py: ext4 readers saw a torn tail in 3 of 15 reads (300-byte lines). p_race3.py: `get_by_key` in a reader process raised `ManifestCorruptError: ... line 19: last line not newline-terminated` 4 times in 66,925 lookups during 4-process appends. p_race2.py: `verify` gave a false `bad_line` once, so `investos check manifest` would fail falsely. Wrong-index caching: not reproduced. A corrupt read is never cached, and the cache matched a fresh parse in every run (p_race.py) |
| Archive skip re-unzips the stored zip each re-run | CONFIRMED, cheap now (A12) | p_archive.py: 30 zips of 2 MB each re-run skip in 0.1 s (re-hash + re-unzip). Side effect: a tampered stored zip raises `RawIntegrityError` out of `run()`, so the whole range aborts and no sidecar is written |
| Lock files / sidecars / reports in tracked dirs | **CONFIRMED** (A4) | `git check-ignore` reports these as TRACKED: data/manifests/*.jsonl.lock, data/manifests/*.runs/*.json and .tmp-*, registry/trials.jsonl and .lock, reports/checks/*.json and .tmp, config/investos.toml |
| TASKS.md stale | CONFIRMED (A15) | TASKS.md shows S0-T1 `blocked` and T2-T6 `open`; all are merged. PROGRESS.md says "Next task: S0-T1" |
| S0-T1 has no report | CONFIRMED (A15) | reports/S0-T1 holds only ignore_test.txt and skeleton_hashes.txt. Brief done-means not met: no branch-protection evidence (decision 0001 waives it), and 7 skeleton files are absent from git (A3) |

## 6. Open items

| Item | Verdict |
|---|---|
| Registry roots anchoring | Acceptable for stage 0. Nothing commits or pushes roots (no git call in src). Absent roots = ok, per spec. Risk: forgery plus roots deletion is undetectable (demo l.79). The stage-1 daily routine `write_roots → git add *.roots.jsonl → commit → push` is a condition (C4). Also, `data/ledger/decisions.jsonl` is git-ignored, so the decision log has no off-machine copy at all, only its roots. Needs a backup decision |
| Check runner copies all zones per check | CONFIRMED (A9). runner.py:182-195 copies all 6 zones and migrations for every planted check, and `_digest` (runner.py:129-155) hashes all zones twice. 200 MB raw → 1.5 s (probes/p_cost.py). Cost is linear, and it needs free temp space equal to the zone sizes. Fine now; fix before the backfill grows raw past a few GB |
| Linux-only fsync spy (S0-T3 tests) | CONFIRMED, worse than "unportable" (A5). tests/unit/acquire/test_manifest_review_regressions.py:131-138 reads `/proc/self/fd/<fd>`. On macOS this falls back to `fd:N`, so test_D3a and test_D3b **fail** rather than skip |
| Torn manifest tail: documented repair path? | **No** (A8). docs/runbooks/ is empty, and no spec or runbook describes recovery. spec core_manifest R7 says "does not repair". One torn tail (power loss / ENOSPC, S0-T3 review P5) blocks every store and lookup for that source |
| S0-T4 D4 header deny-list | CONFIRMED and wider (A6). probes/p_hdr.py: Authorization, Host, Forwarded, X-Client-IP, True-Client-IP, Via, X-Forwarded-Host, Cookie2, `"Referer "` (trailing space), `" referer"`, `User_Agent`, and a CRLF-injected value `a\r\nCookie: nsit=1` are all accepted. Only the 6 exact names are refused |
| S0-T4 D7 mid-archive manifest failure | Run (probes/p_d7.py). ENOSPC injected on the 2nd store: `run()` raises, no sidecar, and one orphan member line stays. The re-run re-fetches and completes, `verify` is clean, and the duplicate member line is harmless. Acceptable, because the zip line is written last. The missing sidecar on store failure is A13 |
| Index stat signature (inode, size, mtime_ns, ctime_ns) | Adequate (probes/p_sig.py). 200 same-size in-place rewrites right after a cached lookup left 0 stale entries. A rewrite plus `utime` restore was also detected (ctime). Another process appending is detected (p_race.py cache==fresh). Caveat: on coarse-timestamp filesystems (HFS+/FAT/network) a same-size rewrite within one tick could be stale. APFS has ns timestamps. Not covered by tests (X1/X2, A7) |
| base.py duplicated source regex | Identical today, but there are 3 copies, not 2: manifest.py:22, base.py:43, check/builtin.py:17. No test ties them together (A16) |
| .gitignore keeps data out except manifests, roots, .gitkeep | Data is kept out: raw, staged, canonical, features, models, quarantine, exports, ledger and credentials are all ignored. **But** the directory patterns (`data/raw/`) stop `!**/.gitkeep` from re-including, so the 7 zone .gitkeep files are not in git (A3). Runtime artefacts are tracked (A4) |
| No secrets committed | PASS. git grep for keys/tokens/private keys and an all-history file-name scan found nothing. All commit identities are `noreply@anthropic.com`. Remote is a private GitHub repo |

## 7. Defects

| ID | Severity | Where | Problem | Fix |
|---|---|---|---|---|
| A1 | must-fix-before-stage-1 | src/investos/check/builtin.py:18-19 | `symlink` problems from `ManifestStore.verify` are in neither `_MANIFEST_KINDS` nor `_RAW_KINDS`. A raw file replaced by a symlink to a writable outside file passes `investos check` (README rule 2 "raw is immutable" is not checked) | Add `"symlink"` to `_RAW_KINDS`. Regression test: replace a stored raw file with a symlink, then `raw` fails. Optionally add a `symlink` plant |
| A2 | must-fix-before-stage-1 | src/investos/acquire/manifest.py:337-368 (`_index`), 417-432 (`_verify_source`) | Lookups and verify read the manifest without the lock. A concurrent append in another process can be seen half-written, so `get_by_key` raises `ManifestCorruptError` (the collector run dies with no sidecar) and `check manifest` reports a false `bad_line` | When the tail is unterminated, take `flock(LOCK_SH)` on `<source>.jsonl.lock` and re-read before raising. Or take LOCK_SH around every read (writers hold LOCK_EX). Add a 2-process test |
| A3 | must-fix-before-stage-1 | .gitignore:2-8 | A fresh clone has no data/raw, staged, canonical, features, models, quarantine or exports dirs, because `dir/` patterns drop their .gitkeep. `load_config` refuses "zone directory does not exist" (verified on a clone of 12c834e). Also, `load_config()` with no env finds no config, so the docs must say to set INVESTOS_ROOT | Use `data/raw/*` + `!data/raw/.gitkeep` style patterns (commit the 7 .gitkeep files) or add `investos init`. Test: `git ls-files` contains each zone's .gitkeep. Document INVESTOS_ROOT and config/investos.toml (and git-ignore or commit the latter deliberately) |
| A4 | must-fix-before-stage-1 | .gitignore | Tracked: `data/manifests/*.jsonl.lock`, `data/manifests/*.runs/.tmp-*`, `*.attempt-*` / `*.corrupt-*` sidecars, `registry/trials.jsonl.lock`, `reports/checks/*.json` and `.tmp`. A `git add -A` from a stage-1 routine would commit them | Ignore `*.lock`, `**/.tmp-*`, `reports/checks/`. Decide explicitly whether sidecars and `registry/trials.jsonl` are tracked (probably yes) and write it in a decision |
| A5 | must-fix-before-stage-1 | tests/unit/acquire/test_manifest_review_regressions.py:131-138 | FsyncSpy depends on `/proc`, so the suite is red on macOS, where stage 1 runs | Use `fcntl.F_GETPATH` on darwin (or `os.fstat` inode matching), else skip with a reason. Then run the full suite on the Mac as non-root. Later: macOS `fsync` is not `F_FULLFSYNC` |
| A6 | must-fix-before-stage-1 (with the decision-0002 spec change) | src/investos/acquire/base.py:41, 248-251 | The deny-list is closed and exact-match. Identity, auth, proxy and Host headers, whitespace-padded names and CR/LF values are accepted | `honest` mode: allow-list (for example Accept, Accept-Language), names must be RFC 7230 tokens, values must have no CR/LF/NUL. UA/Referer/Cookie only in `scoped_exception`/`real_browser` adapters on the config allowlist. Record every header sent in the manifest row |
| A7 | must-fix-before-stage-1 (tests only) | tests | X12: no test fails if the `registry` check ignores roots (demo 4's forgery case is only in the audit demo). X1/X2: no test of index invalidation after another store or process appends | Add: tail forgery + roots → `check registry` fails. Store A caches, store B appends, then A's `get_by_key` sees B's line (in-process and 2-process) |
| A8 | must-fix-before-stage-1 | docs/runbooks/ (empty); spec core_manifest R7 | No documented repair for a torn manifest tail (or ledger tail). It blocks the source indefinitely, and an unattended RSS poller will eventually hit it | Runbook: stop collectors, take the lock, copy the file aside, truncate only the unterminated fragment, `investos check`, and record a decision row. Or a guarded `investos repair tail <source>` that only removes an unterminated final fragment under LOCK_EX and logs it |
| A9 | later (before raw > a few GB) | src/investos/check/runner.py:129-155, 182-195 | Each planted check copies all zones, and the real zones are hashed twice | Copy per check only what it needs (or hardlink/reflink raw); limit the digest to the zones a plant may touch, plus a cheaper stat-based digest |
| A10 | later | check/builtin.py:23-31; manifest.py:391-399 | Names in raw/manifests that don't match the source regex (`raw/BSE/…`, `raw/stray.bin` 0666, `manifests/Bad-Name.jsonl`) are ignored, and checks pass (probes/p_stray.py) | Report unknown entries as `misplaced_file` / `bad_line` |
| A11 | later | src/investos/core/log.py:403-417 (pinned by tests/unit/core/test_review_regressions.py:24-34) | A NaN/Inf field makes the log call raise ValueError, against spec R12 ("logging never raises … replaced by repr") | repr fallback (change the D2 test) or amend R12 |
| A12 | later | src/investos/acquire/base.py:398-407 | The skip path re-reads and re-unzips every stored zip. A tampered or now-over-limit stored zip raises out of `run()`: the whole range aborts with no sidecar | Catch → key `unreadable` ("stored archive failed integrity"), with a gap in the sidecar; continue |
| A13 | later | base.py:386-390 | OSError from `store` (ENOSPC) propagates: no sidecar, orphan member lines (D7) | Abort the run with a sidecar (`aborted`, reason) |
| A14 | later | src/investos/registry/chain.py:228-230, 316-318 | Single `fh.write` on an unbuffered FileIO, with no short-write loop (the manifest has one) | Loop until all bytes are written |
| A15 | must-fix-before-stage-1 (paperwork) | TASKS.md, PROGRESS.md, reports/S0-T1/ | The board is stale. S0-T1 has no REPORT.md, its done-means are unmet (A3), and the branch-protection waiver is recorded only in decision 0001 | Update the board. Write reports/S0-T1/REPORT.md citing decision 0001 and A3 |
| A16 | later | manifest.py:22, base.py:43, builtin.py:17 | 3 copies of the source regex | One public validator (relax test A6 to allow `validate_source`) or a test asserting the patterns are equal |
| A17 | later | tests | Surviving mutants X4, X5, X8, X9, X13, T12 (see section 4) | Add one test each |

No blockers.

## 8. Stage-1 fitness (NSE archives, NSE RSS poller, real-browser backfill runner, `access_mode`)

The Project doc `research/DECISION_NSE_HEADERS.md` and the amended decision 0002 (with `real_browser`) are not reachable from this session. The repo copy of 0002 lists only `honest | scoped_exception`. The following is judged against the repo text plus the task's description of the amendment.

What stage 0 lacks:
1. **access_mode.** No per-adapter mode, no config allowlist, and nothing logged per manifest row. The config schema is strict (an unknown section raises ConfigError), so `core_config` needs a spec and code change for an `[access]` section.
2. **Manifest schema v2.** Decision 0002 §4 requires every header sent to be in the manifest row, plus `access_mode`. The parser rejects any extra field as corrupt (spec R8), so a version-aware parser must ship before any v2 line is written. Otherwise old code reports the new lines as `bad_line`.
3. **Block policy.** 0002 says 401/403/429 stop the adapter *for the day*. Stage 0 retries 429 three times and aborts only the current run. Needs persistent per-adapter, per-day stop state and a daily volume cap.
4. **Rate limit across processes.** `_last_start` is per Collector instance (base.py:264). A poller plus a backfill running in parallel can each send 1 request per 2 s to the same host. Needs a cross-process limiter, for example an flock'd per-host timestamp file.
5. **Production Transport.** None exists. It must not follow redirects, must send only the given headers (urllib adds Host, Accept-Encoding, Connection), and needs a rule for Content-Encoding (store the bytes as received vs decoded; brotli is not in stdlib).
6. **Real-browser runner.** A browser follows redirects, sets its own headers and cookies, and may decode bodies. This conflicts with the Transport contract ("must NOT follow redirects") and R2 ("exactly" these headers). `real_browser` needs its own contract: record the headers actually sent, the final URL and the redirect chain. "Mac residential IP only" can only be recorded (hostname in the row), not enforced.
7. **RSS poller.** `keys(RunRange)` is date-based. A poller needs timestamped snapshot keys (`<feed>/<UTC minute>`). Item URLs come from parsing the feed, which acquire/ may not do (README rule 1, collect R1). Needs a two-step design: poll and store the feed → parse/ extracts links into a queue → an item collector fetches with keys from the queue. Sidecars keyed by date range are overwritten on every poll of the day (complete replaces complete), so poll history lives only in the manifest.
8. **NSE archives** fit the current base: `archive=True`, safe_unzip limits, `missing_holiday` via check_body on 404. A10/A12 should be fixed before the multi-year backfill. A9 matters once raw grows.
9. **Things in stage 0 that would block stage 1 in practice:** A2 (concurrent poller + backfill gives false corruption), A3 (fresh Mac clone does not start), A5 (red suite on Mac), A4 (runtime files committed by the roots routine), and the strict manifest/config schemas (deliberate, but they force spec changes first).

## 9. Conditions for Sagar's approval
- **C1. Before any stage-1 build task starts:** fix A1, A3, A4, A5, A7 and A15. All are small, and each needs a regression test seen red first.
- **C2. In the stage-1 collect spec change (decision 0002), before the first poller or exception adapter runs:**
  - fix A2, A6 and A8;
  - define manifest schema v2 (version-aware parser first), access_mode config, the per-day block policy and a cross-process rate limiter.
- **C3. On the Mac:** run the full suite as non-root (expect 560 pass) and the audit demo `python3 reports/S0-AUD/demo_script.py`, and record the output.
- **C4. Daily roots routine:** stage 1 adds `write_roots → git commit roots → push`. Decide how `data/ledger/decisions.jsonl` is backed up, since it is git-ignored.
- **C5. Before raw passes a few GB:** fix A9 (planted-harness copy cost).
- All other "later" items go on the stage-1 board.
