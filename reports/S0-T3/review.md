# Review S0-T3 (manifest writer) - Reviewer, 21 Nov 2026

Branch task/S0-T3. Module: src/investos/acquire/manifest.py (placement per decision 0001; the brief's `core` path is superseded).
Full suite: `python -m pytest tests -q` -> 127 passed (reports/S0-T3/review_pytest.txt).

## Verdict: PASS WITH MINOR

All 13 rules are implemented and the tests that exist kill 35 of 46 mutants. There are no blocking defects. The minor items are: symlink handling in the raw tree, raw OSErrors that are not ManifestErrors, missing parent-directory fsyncs, and 8 behaviours (R2, R3, R4, R5, R8, R10) that no test pins down. None of these breaks Stage 0 acceptance (A1..A23). I recommend merging and fixing D1-D3 and T1-T6 as a follow-up.

## Hard rules
- No data/ writes: the src diff touches only src/investos/acquire/. The sample run used a mkdtemp directory. `git diff main...task/S0-T3 --name-only | grep ^data/` is empty.
- No network: no socket, urllib or http imports. Imports are stdlib only (fcntl, hashlib, json, os, re, stat, uuid, dataclasses, datetime, pathlib, typing).
- Tests not modified by the Builder: `git log --format='%an %s' main..task/S0-T3 -- tests` shows 3 commits, all by Invest-OS-Test-writer.
- No public name in the module is delete/remove/overwrite/etc. (R4 API surface). The only unlink is of the `.tmp-` file.

## Rule coverage (implementation / test that fails when broken)
| Rule | Impl | Killing test (probe) |
|---|---|---|
| R1 content address | l.171, 228 | A1; M20, M26 |
| R2 atomic write | l.234-257 | A7 kills no-file-fsync M1 and replace-instead-of-link M4; A8 kills M28. Dir fsync M6: SURVIVED |
| R3 0444 | l.245 | M2, M3 killed (via A1/verify). "Never changes mode of pre-existing file" M37: SURVIVED |
| R4 no overwrite | l.230, 260 | A5 kills M5. Race-path hash compare M36: SURVIVED |
| R5 one line per call, fsync | l.213-217 | M27, M20 killed. Manifest fsync M34: SURVIVED |
| R6 lock | l.202-204 | A9/A10 kill M7, M25, M35 |
| R7 truncation guard | l.209-212 | A11 kills M8 |
| R8 strict reading | l.93-123 | A12 kills M11, M31; M24 (blank line) is an equivalent mutant (json.loads("") fails anyway). Newline-less last line M23: SURVIVED (see T5) |
| R9 lookups | l.293-303 | A14 kills M19 |
| R10 verify | l.306-384 | M14-M18, M32, M46 killed. "Never modifies" M43: SURVIVED |
| R11 read_bytes | l.266 | A18 kills M21 |
| R12 time | l.41-48 | A2 kills M12, M13, M47 |
| R13 source safety | l.81 | A19 kills M9, M42 |

## Mutation probes (on a /tmp copy; repo untouched). 46 run: 35 killed, 11 survived (8 real gaps, 2 equivalent, 1 unobservable).
Killed (test that caught it in brackets):
- M1 no file fsync [A7 fsync]
- M2 no chmod [A9 verify writable]
- M3 chmod 0644 [A9]
- M4 os.replace instead of link [A7 link]
- M5 collision hash check disabled [A5]
- M7 no flock [A10]
- M8 no truncation guard [A11]
- M9 source regex allows `..` and `/` [A19]
- M10 tmp not cleaned [A9]
- M11 extra field accepted [A12]
- M12 sort_keys off [A2]
- M13 3-digit microseconds [A2]
- M14 verify hash_mismatch removed [A15]
- M15 verify size_mismatch removed [A15]
- M16 writable_file removed [A16]
- M17 missing_file removed [A12]
- M18 misplaced_file removed [A16]
- M19 get_by_key returns first [A14]
- M20 manifest written before raw [A9]
- M21 read_bytes not verified [A18]
- M22 naive datetime accepted [A3]
- M25 lock and O_APPEND removed [A10]
- M26 wrong size [A9]
- M27 dedup skips the manifest line [A16]
- M28 FileExistsError not tolerated [A8]
- M29 logical_key length unchecked [A3b]
- M31 upper-case sha accepted [A12]
- M32 `.tmp-` not ignored in verify [A22]
- M35 global lock file [A10]
- M38 ensure_ascii=True [A21]
- M39 mkdir exist_ok removed [A9]
- M41 empty url accepted [A3b]
- M42 get_by_sha unvalidated [A19c]
- M46 raw walk removed [A15]
- M47 no UTC conversion [A2]

Survived (real test gaps):
- M6 no directory fsync after link
- M34 no fsync of the manifest append
- M36 on the link FileExistsError path, the hash compare is skipped. A racing writer with different bytes would be treated as dedup.
- M37 dedup chmods the pre-existing file (violates R3's last sentence)
- M43 `verify` chmods writable files (violates R10 "never modifies")
- M23 newline-less last line that is otherwise valid JSON is accepted by lookups
- M30 `size: true` (bool) accepted in a manifest line
- M33 non-bytes `data` accepted (spec says bytes; low value)

Equivalent: M24 (blank line is also caught by the JSON parser), M44 (unlink after link equals the original).
M45 (chmod after link instead of before) is not observable without a crash, so no test could kill it. It does matter: the implementation correctly chmods the temp file before linking, so the final name never appears writable.

## Behavioural probes (scripts in /tmp, run as root and as uid 65534)
- P1: manifest with the last "\n" removed -> lookups and `store` raise ManifestCorruptError; verify gives `bad_line`. OK
- P2: a symlink at the dest path pointing to a file with the right bytes -> `store` dedups through it, verify returns [] (D1).
- P3: dangling symlink at dest -> raw FileNotFoundError, not ManifestError; no manifest line (D2).
- P4: directory at dest -> raw IsADirectoryError (D2).
- P5: ENOSPC raised after a partial 10-byte manifest write -> OSError propagates, a torn line stays, the next `store` raises ManifestCorruptError and `verify` reports `bad_line` at line 2. This matches R7 (no repair) but means one failed write blocks the source until a human repairs it (D4).
- P6 (unprivileged): raw_root read-only -> PermissionError, nothing created, no manifest. Clean.
- P7 (unprivileged): manifests_root read-only -> PermissionError; one orphan raw file stays (allowed by R5); no tmp left.
- P8: raw file replaced by a symlink to tampered bytes -> `verify` returns [] (symlinks are skipped, and the manifest-referenced path is only lstat-ed). `read_bytes` does catch it.
- P9: the `xx` directory replaced by a symlink to a directory holding tampered bytes -> `verify` returns [] (os.walk does not follow it; manifest lines only get an lstat and a size check) (D1).

## Defects
| id | sev | location | rule | evidence | fix |
|---|---|---|---|---|---|
| D1 | minor | manifest.py:230-231, 351-356, 360-370 | R4/R10 | P2, P8, P9: a symlinked dest is accepted as dedup; `verify` ignores symlinked files and symlinked dirs, so tampering through a link is invisible. The manifest-line loop never hashes the file. | Use `lstat`, and reject non-regular dest in `_require_same` with RawIntegrityError. In `verify`, report any symlink or non-regular entry under the raw tree (`misplaced_file`), and hash the manifest-referenced file. Open with O_NOFOLLOW. |
| D2 | minor | manifest.py:231, 262 | R4 | P3/P4: FileNotFoundError and IsADirectoryError escape instead of RawIntegrityError. | Check `S_ISREG(lstat)` first and raise RawIntegrityError. |
| D3 | minor | manifest.py:230-233, 202, 205 | R2 | Only `d` is fsynced after link. A newly created `raw/<source>/`, the `xx` dir's entry in its parent, the new `<source>.jsonl` and the `.lock` file are never directory-fsynced, so a crash can lose the file name or the manifest file even though `store` returned. The dedup early-return path does no dir fsync either. | fsync parent dirs after mkdir. fsync `manifests_root` when the manifest file is created (size==0 before append). fsync `d` on the dedup path too. |
| D4 | info | manifest.py:213-217 | R5/R7 | P5: a failed or partial append leaves a torn tail that blocks the source. No best-effort rollback exists; this is by spec (no truncate), but a torn tail after ENOSPC is foreseeable. | Document it for the S0-T5 check. Optionally record `size` before the write and ftruncate on failure inside the lock (needs a spec change). |
| D5 | info | manifest.py:202-215, 227-258 | - | OS errors (PermissionError, ENOSPC) propagate as raw OSError, not ManifestError. Dead `finally: pass` at l.251-252. Duplicate JSON keys in a manifest line are silently last-wins. | Wrap in ManifestError or document; remove dead code. |
| T1 | minor | tests | R10 | M43 survived: no test shows `verify` leaves a writable or tampered tree unchanged. A17 only covers a healthy store. | Snapshot the tree after verify on a tampered store. |
| T2 | minor | tests | R3 | M37 survived: no test that a pre-existing 0644 file keeps its mode after a dedup `store`. | Add one. |
| T3 | minor | tests | R4/R2 | M36 survived: A8 covers only identical bytes on a link race; no test for different bytes on the race path. | Patch link to create a different-bytes dest, then raise FileExistsError, and expect RawIntegrityError. |
| T4 | minor | tests | R2/R5 | M6 and M34 survived: neither the directory fsync after link nor the fsync of the manifest append is asserted. | Spy on `os.fsync` and check the fds (directory and manifest). |
| T5 | minor | tests | R8 | M23 survived: A11 truncates into the JSON, so a complete JSON line missing only its "\n" is not tested for lookups. | Add a case. |
| T6 | info | tests | R8 | M30 survived: bool `size` in a line is not tested. | Add to the A12 parameters. |

## Untested edge cases
- symlinks anywhere in the raw tree (D1); dangling symlink; dest is a directory (D2)
- ENOSPC or short write on the raw file and on the manifest line (P5 reproduced manually)
- crash points (no kill -9 test): after link before the manifest append; stale `.tmp-` files; the lock file is not removed
- `store` called with a raw_root or manifests_root that is a symlink
- logical_key at exactly 512 characters, and non-BMP characters; a lone surrogate in url -> UnicodeEncodeError (a ValueError) before any write (behaves correctly, but is untested)
- duplicate JSON keys in a manifest line; U+2028 or `\r` inside a value
- `from_config` with a real Config
- two threads (not processes) calling `store` in one process: flock is per open-file-description, so it works, but this is untested
- the source named `lock` or similar does not clash: files are `<source>.jsonl` and `<source>.jsonl.lock`, and a `verify()` scan of `.jsonl.lock` names is skipped by the suffix check (verified by reading)
