# Spec ops_roots_routine   (from brief S1-T3, task S1-SPEC1)

Module: `src/investos/routine/roots.py` (+ `src/investos/routine/__init__.py`), CLI `investos routine roots [--dry-run] [--branch NAME] [--remote NAME]` wired in `src/investos/cli.py`, example scheduler file `ops/launchd/com.investos.roots.plist.example`. Python 3.11+, stdlib only plus `investos.core.*` and `investos.registry.*`; git is run through `subprocess` with an argument list (never a shell string). POSIX only.

Implements audit condition C4: `write_roots` for closed IST days, then commit and push exactly the files that prove what was fetched and decided, so that a forged ledger plus a deleted roots file (audit demo 4, last line) is no longer undetectable. Because `data/ledger/decisions.jsonl` is tracked since S0-FIX (A4), this routine is also its off-machine backup.

## Inputs (schema)
- Config (`load_config`), the repo root `cfg.repo_root` (must contain `.git`), the injected `clock`, and a `Git` helper (`run(args: list[str], *, check=False) -> CompletedProcess`) that tests replace with a fake or point at a temporary repo with a local bare remote.
- CLI flags: `--dry-run` (no write, no git change), `--branch NAME` (default `main`), `--remote NAME` (default `origin`).
- Routine API (`investos.routine.roots`):
```python
class ExitCode(IntEnum):
    OK = 0                 # roots written/committed/pushed, or nothing to do
    USAGE = 1              # bad flags, no git repo, git not runnable, git identity missing
    VERIFY_FAILED = 2      # ledger chain or roots broken; nothing written
    REFUSED = 3            # unsafe repo state (R5); nothing written, nothing committed
    COMMIT_FAILED = 4      # git add or commit failed; roots may have been written (safe, see R9)
    PUSH_REJECTED = 5      # remote refused (non-fast-forward, protected branch); local commit kept
    PUSH_FAILED = 6        # remote not reachable, auth, DNS, timeout; local commit kept
    BUSY = 7               # another routine holds the lock

@dataclass(frozen=True)
class RoutineResult:
    exit_code: ExitCode
    roots_written: Mapping[str, list[str]]   # ledger kind -> IST dates newly rooted
    committed: str | None                    # commit hash, or None when nothing was committed
    pushed: bool
    added: list[str]                         # repo-relative paths passed to git add, sorted
    message: str

def run_routine(cfg: Config, *, git: Git, clock=None, branch: str = "main", remote: str = "origin",
                dry_run: bool = False, notifier: Notifier | None = None) -> RoutineResult   # Notifier: investos.core.notify (built in S1-T1)
```

## Outputs (schema)
- Ledger side effects only through `Ledger.write_roots()` of `trials` and `decisions` (the only code that writes roots).
- At most one git commit per run, on `branch`, with message `routine: roots and ledgers <IST date of clock> (run <run_id>)`, author and committer from the user's own git configuration (the routine never sets or overrides identity and adds no co-author line: it is a scheduled job, not an agent).
- One JSON line on stdout: `{"added":<int>,"branch":"main","committed":"<hash>|null","exit":<int>,"pushed":<bool>,"roots":{"decisions":["2026-11-20"],"trials":[]}}` (canonical key order), plus log events on task `routine.roots`.

### The add-list (R4): the only paths the routine may stage
Explicit files, enumerated by the routine, never directories, never globs handed to git, never `-A` or `.`:
1. `registry/trials.jsonl`, `registry/trials.roots.jsonl` (via `cfg.zones["registry"]`)
2. `data/ledger/decisions.jsonl`, `data/ledger/decisions.roots.jsonl` (via `cfg.zones["ledger"]`)
3. `data/ledger/access/<source>.jsonl` for each file whose stem matches `SOURCE_RE` (halt state) and `data/ledger/access/hosts/<host>.jsonl` for each file whose stem matches `^[a-z0-9][a-z0-9.-]{0,252}$` (block state), both of specs/collect_base.md R25 (kept in the add-list: block history is evidence and holds no secrets)
4. `data/manifests/<source>.jsonl` for each file whose stem matches `SOURCE_RE`
5. `data/manifests/<source>.runs/<start>_<end>.json` run sidecars, and their `.attempt-*` / `.corrupt-*` variants, for each such directory
A path counts only if it exists, is a regular file (not a symlink), lies inside the repo, and is not git-ignored. Anything else found under those folders (a file whose name fails the patterns, a `.tmp-*`, a lock) is ignored by the routine and never staged. Nothing under `data/raw`, `data/staged`, `data/canonical`, `data/features`, `data/models`, `data/quarantine`, `data/exports`, `data/ledger/.state`, `reports/` or any `credentials/` path can ever be in the list.

## Rules (numbered, each gets at least one test and one planted failure)

R1. Order of work: (1) take the routine lock (R10); (2) check the repo state (R5); (3) verify both ledgers with roots (`Ledger.verify(roots=True)`); (4) `write_roots()` on `trials` then `decisions`; (5) stage the add-list (R4, R6); (6) commit if the index changed (R7); (7) push (R8). A failure at a step stops the routine with the exit code of that step; earlier steps are not undone.

R2. Verify before write: if either ledger fails `verify(roots=True)`, nothing is written, staged or committed, the exit code is `VERIFY_FAILED` and the message names the ledger and `first_broken_seq`. `write_roots` itself re-verifies and may raise `LedgerCorruptError` or `RootMismatchError`; these also map to `VERIFY_FAILED`. A missing ledger file is not a failure (no rows, no roots).

R3. Closed days only: roots are written only for IST dates strictly before today's IST date (the `write_roots` rule, R9 of registry_chain), using the injected clock. A day with rows but no root yet (the routine did not run for a while) is rooted on the next run, in date order; the routine writes all missing closed days in one run.

R4. Exact add-list: the routine stages only the files of the add-list above, each with `git add -- <path>` for that single path; it never runs `git add -A`, `git add .`, `git add -u`, `git commit -a`, `git stash`, `git reset`, `git checkout`, `git clean`, `git rm`, or any command with `--force`, `-f` or a `+` refspec. It never stages a path that is not in the add-list, and it checks this before committing: the set from `git diff --cached --name-only -z` must be a subset of the add-list, else `REFUSED` (and nothing is committed).

R5. Safe repo state, checked before anything is written: the current branch (`git rev-parse --abbrev-ref HEAD`) must equal `--branch`, and the index must be empty (`git diff --cached --quiet` is clean), and no merge, rebase or cherry-pick is in progress (`.git/MERGE_HEAD`, `.git/rebase-merge`, `.git/rebase-apply`, `.git/CHERRY_PICK_HEAD` absent). Otherwise `REFUSED` with the reason, and no write at all (not even roots). Unstaged changes in other files are allowed and untouched (they are never added).

R6. Consistent snapshot: while a file is staged, the routine holds a shared `flock` on that file's lock (`<file>.lock`, the same lock writers take exclusively) so `git add` never captures a half-written line; locks are taken one file at a time in sorted path order and released after that file's `git add`. Sidecars and roots files are replaced or appended atomically and need no lock. After staging, the routine re-reads each staged jsonl file from the index (`git show :<path>`) and requires that it ends with `"\n"`; a file that does not is unstaged with `git restore --staged -- <path>` (the one allowed restore, only on that path) and the run ends `REFUSED` with the file named (a torn tail, runbook `manifest-torn-tail.md`).

R7. Commit: if `git diff --cached --quiet` shows no change, nothing is committed (idempotent no-op, exit `OK` unless R8 has something to push). Otherwise one commit with the message above. A `git commit` failure (for example missing `user.name`/`user.email`) maps to `USAGE` when the cause is a missing identity, else `COMMIT_FAILED`; the routine never sets an identity itself. Hooks are not bypassed (`--no-verify` is never used).

R8. Push: after the commit step, and also when R7 committed nothing but the local branch is ahead of `<remote>/<branch>` (`git rev-list --count <remote>/<branch>..HEAD > 0`, using the last known remote ref), the routine runs `git push <remote> <branch>` (a plain push, never forced, never `--force-with-lease`, never `--delete`, never a `+refspec`; hooks are not bypassed). If the remote rejects it (stderr contains `rejected`, `non-fast-forward` or `fetch first`) the exit code is `PUSH_REJECTED`: the routine does not pull, rebase, merge or reset, and tells the human to integrate. Any other failure (network, DNS, auth, timeout of 60 s) is `PUSH_FAILED`. In both cases the local commit stays, and the next run pushes it (R9). On success `pushed` is true.

R9. Offline and partial failure are safe and repeatable. The routine needs no network until R8. If it is offline: roots and the commit are written locally, the push fails with `PUSH_FAILED`, a WARNING `push_failed` is logged, and the next run (online) finds nothing new to commit, sees the branch ahead, and pushes. If it is interrupted after `write_roots` but before commit: the roots are already valid, the next run stages and commits them. Idempotent: running it twice in a row, with nothing new in between, writes no roots, makes no commit, pushes nothing (the second run exits `OK`); `--dry-run` never changes anything and prints what it would stage.

R10. Single instance: the routine takes an exclusive non-blocking `flock` on `<ledger zone>/.state/roots-routine.lock` (created if missing). If it is held, exit `BUSY` at once, writing nothing. Collectors and ledger writers do not take this lock; R6's shared file locks are what keep the routine consistent with them.

R11. Never destructive: the module has no code that deletes, truncates, renames or rewrites files in the ledger, registry, manifests or data zones (other than via `write_roots`, which only appends); a source scan of `routine/roots.py` finds no `os.remove`, `unlink`, `rmtree`, `truncate`, `rename`, `replace`, no `open(` with a write mode on repo paths, and no `shell=True`. The git command list is an allow-list (R4, R8, plus read-only `rev-parse`, `status`, `diff`, `show`, `rev-list`, `ls-files`, `check-ignore`, `config`, and `restore --staged` of R6).

R12. Logging and exit: events on task `routine.roots`: `start`, `verified`, `roots_written` (kind, dates), `staged` (count), `committed` (hash), `pushed`, `push_failed` (WARNING, first stderr line, no URLs with credentials), `refused` (ERROR), `verify_failed` (ERROR), `done` (exit code). The process exit status equals the `ExitCode`. A stack trace never replaces an exit code: an unexpected exception is logged and maps to `USAGE` if it happens before any root was written, and to `COMMIT_FAILED` if it happens after (the roots are safe on disk either way).

R13. Alerts. On exit codes `VERIFY_FAILED` (2), `REFUSED` (3), `PUSH_REJECTED` (5) and `PUSH_FAILED` (6) the routine calls `notifier.notify("Invest OS: roots routine", "exit <code> <NAME>: <message>")` exactly once, where `<message>` is the first line of `RoutineResult.message` with remote URLs and absolute paths removed. It does not notify on 0, 1, 4 or 7, and never in `--dry-run`. The default notifier is `NullNotifier` (so no test runs a command); the CLI passes an `OsascriptNotifier` on macOS (`investos.core.notify`, specs/collect_base.md R33, same escaping, 5 s timeout, never raises). A failing notifier is logged (WARNING `notify_failed`) and never changes the exit code.

R14. Last result file. At the end of every non-dry run the routine writes the stdout JSON plus `"ts"` (UTC, 6 digits, `Z`) to `<ledger zone>/.state/roots-routine.last.json` (temp file plus `os.replace`; ignored by git, under `.state/`), so Mission Control can show the last routine result. A failure to write it is a WARNING, not an exit code.

R15. Schedule example. `ops/launchd/com.investos.roots.plist.example` is a valid property list (`plistlib` loads it) with `Label` `com.investos.roots`, `ProgramArguments` running `investos routine roots` (path placeholders in `<...>`-free example values, documented in a comment key), `StartCalendarInterval` `{Hour: 23, Minute: 30}` and `RunAtLoad` true (catch-up after the Mac wakes), and a comment that launchd uses the Mac's local time zone, so the hour means 23:30 IST only while the Mac is set to Asia/Kolkata. Roots exist only for closed days, so a 23:30 run roots the days before today; the latest anchor is thus up to one day behind. Installing the job is a human step.

## Edge cases
- First ever run, no trials file, no decisions file: nothing to root, add-list contains only manifests/sidecars/access files that exist; may commit them.
- The same day run at 00:05 IST and again at 18:00 IST: the second run roots nothing new (today is open) and commits only new manifest lines and sidecars.
- A manifest with a torn tail: refused (R6), exit 3; the human follows the runbook.
- Branch behind the remote (someone else pushed): push is rejected (exit 5); the routine does nothing to resolve it.
- `git` or the remote is slow: the push timeout of 60 s gives `PUSH_FAILED`; other git calls use a 30 s timeout and map to `COMMIT_FAILED`.
- Clock before the last row's date: `write_roots` writes nothing for dates not yet closed (registry rule).
- Large manifests: staging is per file; `git add` of an appended file stores a new blob each time (storage growth, see open question 3).

## Acceptance tests (in words)
Fixtures: a temp git repo with a bare local remote (`file://` path) holding the layout (`registry/`, `data/ledger/`, `data/manifests/`, ignored `data/raw/` with a file in it), a config pointing at it, a fixed injected clock, ledgers filled with rows on three IST days, a fake `Git` helper for failure injection.

A1. (R1, R3) Given rows on IST days D1, D2 and D3 (today = D3), when the routine runs, then roots for D1 and D2 are appended in order to both ledgers that have rows on them, none for D3, one commit exists, the remote has it, the exit code is 0, and the stdout JSON lists the dates.
A2. (R2, planted) Given a ledger row edited after the fact (chain break) or a root with the wrong hash, then exit 2, no root line added, nothing staged, no commit, remote unchanged.
A3. (R4, planted) Given untracked files `data/raw/bse/ab/<sha>`, `data/staged/x`, `data/ledger/.state/ratelimit/h.last`, `data/manifests/a.jsonl.lock`, `data/manifests/a.runs/.tmp-1`, `reports/checks/x.json`, `credentials/k`, `NOTES.txt`, and a modified tracked source file `src/investos/cli.py`, then after a run `git show --name-only HEAD` lists only add-list files; none of those is staged or committed; `src/investos/cli.py` is still modified and unstaged.
A4. (R4, planted) Given a symlink named `data/manifests/evil.jsonl` pointing outside the repo, a manifest named `Bad-Name.jsonl`, and a sidecar directory `data/manifests/a.runs/` containing `notes.txt`, then none is staged. Given an already-staged unrelated file in the index, then exit 3 and the index is unchanged (R5). A fake `Git` records every command: none is `add -A`, `add .`, `commit -a`, `push --force`, `push -f`, `reset`, `checkout`, `clean`, `stash`, or contains a `+` refspec or `--no-verify`.
A5. (R5, planted) Given the current branch `task/S1-T1`, then exit 3, no root written, nothing committed. Given a `MERGE_HEAD` file, the same. Given `--branch` equal to the current branch other than main, it runs.
A6. (R6, planted) Given a manifest whose last line was cut (no final newline), then exit 3, the file is not in the commit or the index, the message names the file and the runbook, and no other file is committed either. Given a writer process holding the exclusive lock on a manifest for about 1 second while appending, then the routine waits and stages the file only after the writer's line is complete (the staged blob ends in a newline and has the writer's last line).
A7. (R7, R9) Given a run that commits and a second run immediately after, then the second run writes no roots, makes no commit (commit count unchanged), pushes nothing, exits 0. Given a new manifest line in between, then exactly one new commit containing that manifest file only.
A8. (R7) Given no git identity configured, then exit 1 with a message about `user.name`/`user.email`, and the commit count is unchanged. Given `--dry-run`, then no file changed (byte comparison of the whole repo tree), no commit, the stdout lists the planned add-list.
A9. (R8, R9, planted) Given the remote path made unreachable, when the routine runs, then roots are written, the commit exists locally, exit 6, a `push_failed` WARNING is logged, and no network error text with a URL appears in the stdout JSON. When the remote is back and the routine runs again, it writes nothing new, pushes the earlier commit, and exits 0 with `pushed == true`.
A10. (R8, planted) Given the remote has a commit the local branch lacks, then exit 5, the local commit is kept, no pull/fetch/rebase/merge/reset was run, and the remote is unchanged. Given a pre-receive hook on the remote that rejects, then exit 5 or 6 as the stderr says, never a forced retry.
A11. (R10) Given another process holding the routine lock, then exit 7 immediately, nothing written. Given two routines started together, exactly one does the work, the other exits 7 or finds nothing to do, and the history has no duplicate root lines (`verify` is clean).
A12. (R11, planted) Given the source text of `routine/roots.py`, then it contains none of the forbidden calls and no `shell=True`, and every git argument list in the code is in the allow-list (test collects the commands issued across the scenarios above and checks them).
A13. (R12) Given each scenario, then the process exit status equals the documented code, one `done` log event carries it, and an injected exception after `write_roots` ends with exit 4 and the roots are on disk.
A14. (R9, planted) Given the routine killed (SIGKILL in a test hook) right after `write_roots` and before staging, then a following run stages and commits the roots; the chain and roots verify clean; no root date is duplicated.
A15. (R3) Given the routine was not run for 5 days with rows on each, then one run writes all 5 missing roots in date order, one commit.
A16. (Edge) Given an empty repo layout (no ledgers, no manifests), then exit 0, no commit, no error.
A17. (R13, planted) Given scenarios ending in each exit code 0 to 7 and a `FakeNotifier`, then it received exactly one call for each of 2, 3, 5, 6 (title `Invest OS: roots routine`, text starting `exit <code> <NAME>`, containing no `file://` or absolute repo path) and none for 0, 1, 4, 7; with `--dry-run` none at all. A notifier raising `OSError` leaves the exit code unchanged and logs `notify_failed`.
A18. (R13) Given the routine API called without a notifier, then a `NullNotifier` is used and no subprocess is started (patched `subprocess.run` is never called for osascript); the CLI wiring selects `OsascriptNotifier` only when `sys.platform == "darwin"` (tested with a patched platform).
A19. (R14) Given a non-dry run, then `<ledger zone>/.state/roots-routine.last.json` exists with the stdout fields and a valid `ts`, is not staged or committed (A3 holds), and a dry run does not create or change it. Given an unwritable state dir, then the run still ends with the normal exit code.
A20. (R15, planted) Given `plistlib.load` on the example file, then it parses, `StartCalendarInterval == {"Hour": 23, "Minute": 30}`, `RunAtLoad is True`, the first `ProgramArguments` entries are the CLI and `routine roots`, and the file contains the time zone comment. Given the access-state files (host and source files), then both kinds are in the add-list and committed by a run (A3's list extended).

## Not in this module
- Task-branch merging: with the routine moving `main` nightly, tasks merge with `git merge --no-ff` when a fast-forward is impossible (decision 0001 amendment); the routine never rebases or merges.
- The scheduler itself: the routine is a command; `ops/launchd/com.investos.roots.plist.example` is only an example (a daily `StartCalendarInterval` shortly after 00:00 IST, with `RunAtLoad` so a Mac that was asleep catches up). Installing it is a human step.
- Any remote anchoring other than git (signing, timestamping services).
- Pull, rebase, merge or conflict resolution; opening pull requests.
- Backing up `data/raw` (bytes are re-fetchable and never go to GitHub).
- Rotating or pruning git history.

## Open questions for Sagar (as raised by the Spec writer; see Resolved below)
1. Branch: the spec commits to `main` (default `--branch main`). Every routine commit makes `main` move while task branches are merged `--ff-only`, so each task branch would need a rebase. Alternative: a dedicated branch (for example `ledger`, kept in a second worktree) that is only ever fast-forwarded by the routine. Which one?
2. Schedule: which time (IST) should launchd use, and is `RunAtLoad` catch-up after the Mac wakes acceptable?
3. Repo growth: manifests are committed in full every day (each run stores a new blob for each changed jsonl file). With polling this is megabytes per day (see core_manifest open question 3). Is a size or row-count limit wanted, with a rollover file per month?
4. Push rejected (exit 5) needs a human today. Do you want a notification (for example a macOS notification via `osascript`) on exit codes 2, 3, 5 and 6, or is the log enough?
5. The access state files (`data/ledger/access/*.jsonl`) are in the add-list, so block history is pushed. Say if you prefer them kept local.

Folded into the rules above: 1 -> default `--branch main` (R5), no rebase or force (R8, R11), merge note under Not in this module; 2 -> R15, A20; 3 -> none (no size limit in code); 4 -> R13, R14, A17-A19; 5 -> add-list item 3, A20.

## Resolved (21 Nov 2026; S = Sagar, O = Orchestrator default)
1. (S) Branch `main`. Consequence for the build process: once the routine runs, main moves nightly, so task branches merge with `git merge --no-ff` when a fast-forward is impossible. Never rebase, never force-push. Decision 0001's ff-only rule is amended accordingly.
2. (S) launchd daily at 23:30 IST, with RunAtLoad catch-up after wake.
3. (O) No limit yet. Revisit if the repo grows past 500 MB. Monthly rollover is a later option.
4. (O) A macOS notification (osascript) on exit codes 2, 3, 5 and 6, plus the log, and Mission Control shows the last routine result.
5. (O) Keep pushing the access state files. Block history is useful evidence, and it holds no secrets.
