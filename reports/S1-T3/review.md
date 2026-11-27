# Review S1-T3 (nightly roots routine) - Reviewer, 27 Nov 2026

Branch task/S1-T3 @ 39e22d1 (main 284fa68). Verdict: **PASS WITH CONDITIONS**. No critical defect. One High defect (D1: a user git config can turn the routine's push into a force push) must be fixed before the Orchestrator installs the schedule. D2 to D4 are worth fixing in the same pass; the rest are Low/Info.

Full suite: root 1251 passed, 3 skipped; non-root (user `nobody`, repo copy) 1254 passed, 0 skipped (reports/S1-T3/review_pytest.txt). macOS run is the Orchestrator's step (C3).

## 1. Hard rules
- Builder edited no tests: `git log main..task/S1-T3 --format='%h %an' -- tests` lists only e166fc6 (Invest-OS-Test-writer); Builder commit 39e22d1 touches src/, ops/, checkpoints, reports only. OK.
- stdlib only: roots.py imports fcntl, json, os, re, subprocess, dataclasses, datetime, enum, pathlib plus investos.core/registry. No third-party names. OK.
- git via argument lists: `Git.run` -> `subprocess.run(argv, shell=False)`; grep for `shell=True`, `--force`, `-f`, `rebase` (as a command), `--no-verify`, `os.remove/unlink/rmtree/rename/truncate`, `open(` in roots.py and cli.py: only hits are the word "rebase" in the in-progress marker check, comments, and `os.replace` for last.json (allowed by R14). OK. See D1/D2 for the cases where an argument still ends up as force.
- Reviewer touched no src/ or tests/; no data/ in the diff; no push/merge; /home/claude/wt_t4 untouched. Real repo and remote never used (all probes in /tmp).
- Scope: cli.py, routine/, ops/launchd example only. registry/, acquire/, core/ untouched (diff empty).

## 2. Spec coverage (R1-R15)
| Rule | Code | Result |
|---|---|---|
| R1 order | `_body` lock (run_routine), R5 checks, verify, write_roots, stage, commit, push | OK |
| R2 verify before write | roots.py:269-274; write_roots errors mapped at 286 | OK; D7 (decisions-ledger-broken-after-trials-written ordering untested) |
| R3 closed days | delegated to `Ledger.write_roots`; A1/A15 pass | OK |
| R4 add-list | `build_add_list` 116-140: explicit files, SOURCE_RE/HOST_RE/SIDECAR_RE, regular non-symlink, realpath inside repo, `check-ignore`; per-path `git add --`; cached-set subset check 320-326 | OK (probes P1) |
| R5 safe state | 249-266: branch, MERGE_HEAD/rebase-*/CHERRY_PICK_HEAD via `rev-parse --git-dir`, clean index; detached HEAD refused | OK |
| R6 snapshot | shared flock on `<rel>.lock` (same names writers use: manifest.py:358, access.py `_lock_of`, chain.py:177) one file at a time; torn tail via `git show :path` | OK; D3 (no timeout) |
| R7 commit | message with IST date + run id; identity hints -> USAGE; no `-a`, no `--no-verify`, no identity set | OK (D4 locale) |
| R8 push | `push <remote> <branch>`, ahead check with `rev-list --count`, rejected -> 5, else 6, 60 s timeout | **D1**, D2 |
| R9 offline/idempotent | demo below: exit 6 then catch-up exit 0 pushed true; second run no commit | OK |
| R10 single instance | EX|NB flock on `.state/roots-routine.lock`, BUSY 7 | OK |
| R11 never destructive | scan clean | OK |
| R12 logging/exit | start/verified/roots_written/staged/committed/pushed/push_failed/refused/verify_failed/done; unexpected -> 1 or 4 | OK |
| R13 notifier | codes 2,3,5,6 once, never dry-run, failing notifier logged; CLI Osascript only on darwin; message passed through `_redact` | OK; D8 (path with spaces) |
| R14 last.json | tmp + os.replace, failure is WARNING | OK; D5 |
| R15 plist | plistlib-valid, 23:30, RunAtLoad, TZ comment, `routine roots` args | OK |

Demo (reports/S1-T3/review_probes/cli_demo_output.txt, real CLI as subprocess on a temp repo with bare origin): dry-run lists 3 would-stage paths and changes nothing; first run roots 3 days for both ledgers, one commit with exactly the 5 add-list files, pushed; second run exit 0, no commit; manifest edited with origin moved away -> exit 6, commit kept, 1 ahead; origin back -> exit 0, `pushed:true`, no new commit; `--bogus` -> exit 1; `--branch other` -> exit 3.

## 3. Safety probes (real git, /tmp; reports/S1-T3/review_probes/test_review_probes.py)
| Probe | Result |
|---|---|
| P1 raw file (`data/raw/...`, `data/raw/a.jsonl`), `data/staged/x.jsonl`, `.env`, `credentials/k`, `data/manifests/.env`, `secret.env`, `a.runs/.env`, `a.runs/credentials.json`, `.state/x.jsonl`, NOTES.txt | none committed. Only the 5 legit files plus `a.runs/1_2.json` |
| P1 symlinks: `evil.jsonl` -> outside file, `pw.jsonl` -> /etc/passwd, `b.runs` -> outside dir, `access/hosts` -> outside dir, in-repo symlink to a raw file, symlink to `.env` | none committed |
| P2 non-fast-forward: other clone pushed first | exit 5, remote head unchanged, exactly one push command, second run still 5; no fetch/pull/reset |
| P2b same, but user config `remote.origin.push=+refs/heads/*:refs/heads/*` | **exit 0, remote history overwritten ("forced update")**. D1. Reproduced with plain git in /tmp/p2x.py: `git push origin main` is forced under that config; `git push origin refs/heads/main:refs/heads/main` is rejected |
| P3 `--remote=--force` | argv became `git push --force main` (failed only because no repo named `main`). D2 |
| P4 detached HEAD, unrelated staged file (index left untouched, no root written), CHERRY_PICK_HEAD | exit 3 each; after removing, run succeeds |
| P5 writer holds exclusive manifest lock forever | routine still blocked after 4 s (waits for ever while holding the routine lock). D3 |
| P6 pre-commit hook that `git add`s a new file | the file is committed (hook runs after the subset check). D6 |
| P7 git-ignored manifest, repeat run, `.state`/`*.lock` | not committed; repeat run creates no commit |

## 4. Mutation probes
Copy /tmp/s1t3_probe, runner reports/S1-T3/review_probes/mut.py, command `python -m pytest tests/unit/routine -x -q -p no:cacheprovider` (88 tests, about 12 s). 45 single mutations of roots.py. **Killed 37, survived 8: 37/45 = 82%.** (Raw log: review_probes/mut_output.txt.)

Killed: M01 branch check off, M02 index check off, M03 merge markers off, M04 no verify before write, M06 symlinks allowed, M07 ignore filter off, M09/M10/M11 host, sidecar, source regexes loosened, M12 shared lock off, M13/M14 torn tail check/unstage off, M15 subset check off, M16 `--no-verify`, M17 `push --force`, M18/M19 push-when-ahead wrong both ways, M20 rejected misclassified, M21 identity not mapped, M22 lock not exclusive, M23/M24 notify codes 4 added / 6 dropped, M25 notify in dry-run, M26 notifier exception propagates, M27/M28 post-write error code, M30 last.json not written, M31 UTC date in message, M32 dry-run writes, M33 empty commit, M34 push timeout, M36 path redaction, M38 hosts dropped, M39 `git add <dir>`, M40 truncated ignore list, M42 `shell=True`, M43 BUSY code.

| Survivor | Mutation | Verdict |
|---|---|---|
| M05 | `verify(roots=False)` | real gap (D7): write_roots re-verifies, so the wrong-root test passes; no test shows "nothing written" when only the decisions root is bad |
| M45 | LedgerError from write_roots not mapped | real gap (D7): the 286 path is untested; unmapped it gives exit 4 not 2 |
| M08 | no inside-repo (realpath) check | real gap, defence in depth (D7): git itself refuses paths beyond a symlink, and symlinked dirs are skipped earlier |
| M44 | symlinked `*.runs` dir followed | same as M08 |
| M37 | `.state/*` added to the add-list | real gap (D7): only the ignore filter stops it, and the fixture ignores `.state/`; no test with `.state` un-ignored |
| M35 | URL not redacted | real gap (D7): A17's "no file://" check does not bite because the remote-path redaction masks it |
| M29 | roots written decisions before trials | equivalent in effect (only log order) |
| M41 | GIT_TERMINAL_PROMPT removed | not testable here; equivalent |

## 5. Builder deviations
- Unstaging own paths (`restore --staged -- <own path>`) on add failure, stray path and commit failure: ACCEPT. Only paths this run staged, never the worktree, matches the test allow-list, and prevents R5 from blocking every later run. Spec text only names the torn-tail case; suggest amending R6.
- Dry-run skips the lock: ACCEPT. Dry-run is read-only (A8 byte-compare passes) and creating the lock file would change the tree.
- BUSY writes last.json: ACCEPT with D5 (R14 says every non-dry run, but a BUSY loser overwrites the winner's file or races on the shared temp name).
- `pip install -e . --no-deps`: ACCEPT as an env step, but A14 (`python -I`) fails in any clean env without it; D9 asks to document it. In my runs the editable install points at /home/claude/invester_os, so the /tmp copy's A14 subprocess runs unmutated code (the other 87 tests still kill).

## 6. Defects
| ID | Sev | Where | Problem | Fix |
|---|---|---|---|---|
| D1 | High | roots.py:353 | `git push origin main` is forced when the user's git config has a `+` push refspec (`remote.<r>.push=+refs/heads/*...`); remote history is overwritten, defeating the off-machine anchor. Tests isolate git config so they miss it | Push the explicit refspec `refs/heads/<b>:refs/heads/<b>` (verified rejected as non-ff under the hostile config; update the test allow-list to accept it) or refuse when `git config --get-all remote.<r>.push` contains `+`. Add a test with that config |
| D2 | Medium | roots.py:349,353; cli.py `--remote` | `--remote=--force` (or any value starting with `-`) is passed as an option: `git push --force main`. Needs a remote named like the branch to do harm, so practical risk is low, but A4/A12 intent is broken | Validate remote against `^[A-Za-z0-9][A-Za-z0-9._-]*$` and require it to be in `git remote`; exit 1 otherwise |
| D3 | Medium | roots.py:303 | Shared flock blocks without limit; a stuck collector hangs the routine while it holds the routine lock; later runs get BUSY (7), which never alerts | Poll `LOCK_SH|LOCK_NB` for about 30 s, then REFUSED/COMMIT_FAILED with the file named (alerts) |
| D4 | Low | roots.py:71, 338, 361 | Rejected/identity detection reads English git stderr; under another locale a rejected push is reported as 6 instead of 5 (still alerts) | Set `LC_ALL=C` in the git env |
| D5 | Low | roots.py:173, 227 | BUSY loser overwrites last.json and both use one fixed temp name with O_TRUNC; can interleave | Skip last.json on BUSY or use a unique temp name |
| D6 | Low/Info | roots.py:328-334 | A pre-commit hook that stages other files is committed; the subset check ran before hooks | Run `git show --name-only HEAD` after commit and alert (exit 3) if outside the add-list; or document that hooks are trusted |
| D7 | Low | tests/unit/routine | Survivors M05, M45, M08, M44, M37, M35 | Tests: bad decisions root with trials rootable (nothing written); LedgerError injected into write_roots -> 2; `.state` file with no ignore rule; URL in message -> no `file://`; hostile push config (D1) |
| D8 | Info | roots.py:85-88 | `_redact` leaves fragments of paths with spaces (`/Users/A B/x` -> `[path] B/x`) | Redact repo root and home by prefix first |
| D9 | Info | env | A14 needs `pip install -e .`; the field `added` counts the whole add-list, not new paths | Note both in the runbook |
