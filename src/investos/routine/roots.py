"""Nightly roots routine (spec ops_roots_routine, S1-T3): write roots for closed IST days, stage exactly the
ledgers, roots, access state files, manifests and run sidecars, commit, push. Never destructive, never forced.

Stdlib plus investos.core / investos.registry. Git is run through subprocess with an argument list only.
"""
from __future__ import annotations

import fcntl
import json
import os
import re
import subprocess
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from enum import IntEnum
from pathlib import Path
from typing import Callable, Mapping, Sequence

from investos.core import log
from investos.core.config import Config
from investos.core.names import SOURCE_RE
from investos.core.notify import Notifier, NullNotifier
from investos.registry import ledgers as _ledgers
from investos.registry.chain import LedgerError

__all__ = ["ExitCode", "Git", "RoutineResult", "run_routine", "build_add_list", "result_json"]

TASK = "routine.roots"
_IST = timezone(timedelta(hours=5, minutes=30))
HOST_RE = re.compile(r"[a-z0-9][a-z0-9.-]{0,252}", re.ASCII)
SIDECAR_RE = re.compile(
    r"\d[\w:+.\-]*_\d[\w:+.\-]*\.json(?:\.(?:attempt|corrupt)-[\w.\-]+)?"
    r"|\d[\w:+.\-]*_\d[\w:+.\-]*\.(?:attempt|corrupt)-[\w.\-]+\.json", re.ASCII)
_IDENTITY_HINTS = ("please tell me who you are", "unable to auto-detect", "empty ident", "user.useconfigonly",
                   "user.name", "user.email")
_NOTIFY_CODES = (2, 3, 5, 6)


class ExitCode(IntEnum):
    OK = 0
    USAGE = 1
    VERIFY_FAILED = 2
    REFUSED = 3
    COMMIT_FAILED = 4
    PUSH_REJECTED = 5
    PUSH_FAILED = 6
    BUSY = 7


@dataclass(frozen=True)
class RoutineResult:
    exit_code: ExitCode
    roots_written: Mapping[str, list[str]]
    committed: str | None
    pushed: bool
    added: list[str]
    message: str


class Git:
    """Thin subprocess wrapper. `args` may include or omit the leading "git". Never uses a shell."""

    def __init__(self, cwd: Path | str):
        self.cwd = Path(cwd)

    def run(self, args: Sequence[str], *, check: bool = False, timeout: float = 30,
            input: str | None = None) -> subprocess.CompletedProcess:
        argv = [str(a) for a in args]
        if not argv or argv[0] != "git":
            argv = ["git", *argv]
        env = dict(os.environ)
        env["GIT_TERMINAL_PROMPT"] = "0"
        try:
            r = subprocess.run(argv, cwd=str(self.cwd), capture_output=True, text=True, errors="replace",
                               env=env, timeout=timeout, input=input, check=False, shell=False)
        except subprocess.TimeoutExpired:
            r = subprocess.CompletedProcess(argv, 124, stdout="", stderr="timeout")
        if check and r.returncode != 0:
            raise subprocess.CalledProcessError(r.returncode, argv, r.stdout, r.stderr)
        return r


# ---------------------------------------------------------------- helpers

def _redact(text: str) -> str:
    text = re.sub(r"\S*://\S*", "[url]", text)
    text = re.sub(r"(?<![\w.\-])/[\w./\-~+@%]+", "[path]", text)
    return text


def _first_line(text: str) -> str:
    for ln in str(text).splitlines():
        if ln.strip():
            return ln.strip()
    return ""


def _is_plain_file(p: Path) -> bool:
    try:
        return p.is_file() and not p.is_symlink()
    except OSError:
        return False


def _under(root: Path, p: Path) -> str | None:
    """Repo-relative POSIX path of p if its real location lies inside root, else None."""
    try:
        real_root = Path(os.path.realpath(root))
        real_p = Path(os.path.realpath(p.parent)) / p.name
        real_p.relative_to(real_root)
        return p.relative_to(root).as_posix()
    except ValueError:
        return None


def build_add_list(cfg: Config, git: Git) -> list[str]:
    """The only paths the routine may stage (spec R4): explicit files, sorted, repo-relative."""
    root = Path(cfg.repo_root)
    reg, led, man = Path(cfg.zones["registry"]), Path(cfg.zones["ledger"]), Path(cfg.zones["manifests"])
    cands: list[Path] = [reg / "trials.jsonl", reg / "trials.roots.jsonl",
                         led / "decisions.jsonl", led / "decisions.roots.jsonl"]
    access = led / "access"
    if access.is_dir() and not access.is_symlink():
        cands += [p for p in access.glob("*.jsonl") if SOURCE_RE.fullmatch(p.stem)]
        hosts = access / "hosts"
        if hosts.is_dir() and not hosts.is_symlink():
            cands += [p for p in hosts.glob("*.jsonl") if HOST_RE.fullmatch(p.stem)]
    if man.is_dir() and not man.is_symlink():
        cands += [p for p in man.glob("*.jsonl") if SOURCE_RE.fullmatch(p.stem)]
        for d in man.glob("*.runs"):
            if d.is_dir() and not d.is_symlink() and SOURCE_RE.fullmatch(d.name[: -len(".runs")]):
                cands += [p for p in d.iterdir() if SIDECAR_RE.fullmatch(p.name)]
    rels = sorted({r for p in cands if _is_plain_file(p) and (r := _under(root, p)) is not None})
    if not rels:
        return []
    r = git.run(["check-ignore", "-z", "--stdin"], input="\0".join(rels) + "\0")
    if r.returncode not in (0, 1):
        raise RuntimeError(f"git check-ignore failed ({r.returncode})")
    ignored = {x for x in r.stdout.split("\0") if x}
    return [x for x in rels if x not in ignored]


class _Flock:
    def __init__(self, path: Path, op: int):
        self.path, self.op, self.fd = path, op, None

    def __enter__(self):
        self.fd = os.open(self.path, os.O_RDWR | os.O_CREAT, 0o644)
        try:
            fcntl.flock(self.fd, self.op)
        except BaseException:
            os.close(self.fd)
            self.fd = None
            raise
        return self

    def __exit__(self, *exc):
        if self.fd is not None:
            try:
                fcntl.flock(self.fd, fcntl.LOCK_UN)
            finally:
                os.close(self.fd)
                self.fd = None


def result_json(res: RoutineResult, branch: str) -> dict:
    return {"added": len(res.added), "branch": branch, "committed": res.committed, "exit": int(res.exit_code),
            "pushed": res.pushed, "roots": {k: list(v) for k, v in sorted(res.roots_written.items())}}


def _write_last(state_dir: Path, obj: dict) -> None:
    """Scratch file plus os.replace so readers never see a half-written file (R14)."""
    tmp = state_dir / ".tmp-roots-routine.last"
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o644)
    try:
        data = (json.dumps(obj, sort_keys=True, separators=(",", ":")) + "\n").encode("utf-8")
        view = memoryview(data)
        while view:
            view = view[os.write(fd, view):]
        os.fsync(fd)
    finally:
        os.close(fd)
    os.replace(tmp, state_dir / "roots-routine.last.json")


# ---------------------------------------------------------------- the routine

class _Stop(Exception):
    def __init__(self, code: ExitCode, message: str):
        super().__init__(message)
        self.code, self.message = code, message


def run_routine(cfg: Config, *, git: Git, clock: Callable[[], datetime] | None = None, branch: str = "main",
                remote: str = "origin", dry_run: bool = False, notifier: Notifier | None = None) -> RoutineResult:
    logger = log.get_logger(TASK)
    notifier = notifier if notifier is not None else NullNotifier()
    clock = clock or (lambda: datetime.now(timezone.utc))
    state = {"wrote": False, "roots": {"decisions": [], "trials": []}, "committed": None, "pushed": False,
             "added": []}
    state_dir = Path(cfg.zones["ledger"]) / ".state"
    logger.info("start", branch=branch, remote=remote, dry_run=dry_run)
    try:
        if dry_run:
            _body(cfg, git, clock, branch, remote, True, state, logger)
        else:
            os.makedirs(state_dir, exist_ok=True)
            try:
                lock = _Flock(state_dir / "roots-routine.lock", fcntl.LOCK_EX | fcntl.LOCK_NB).__enter__()
            except BlockingIOError:
                raise _Stop(ExitCode.BUSY, "another roots routine holds the lock")
            try:
                _body(cfg, git, clock, branch, remote, False, state, logger)
            finally:
                lock.__exit__(None, None, None)
        code, msg = ExitCode.OK, "ok"
    except _Stop as s:
        code, msg = s.code, s.message
    except Exception as e:                                       # a stack trace never replaces an exit code
        logger.error("unexpected", error=type(e).__name__, detail=_redact(_first_line(str(e)))[:200])
        code = ExitCode.COMMIT_FAILED if state["wrote"] else ExitCode.USAGE
        msg = f"unexpected error {type(e).__name__}: {_redact(_first_line(str(e)))[:200]}"
    msg = _redact(msg) if code == ExitCode.OK else msg
    res = RoutineResult(code, {k: list(v) for k, v in state["roots"].items()}, state["committed"],
                        state["pushed"], list(state["added"]), msg)
    logger.info("done", exit=int(code), name=code.name)
    if not dry_run:
        try:
            obj = result_json(res, branch)
            obj["ts"] = clock().astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%fZ")
            _write_last(state_dir, obj)
        except Exception as e:
            logger.warning("last_result_failed", error=type(e).__name__)
        if int(code) in _NOTIFY_CODES:
            try:
                notifier.notify("Invest OS: roots routine",
                                _redact(f"exit {int(code)} {code.name}: {_first_line(msg)}")[:300])
            except Exception as e:
                logger.warning("notify_failed", error=type(e).__name__)
    return res


def _body(cfg, git, clock, branch, remote, dry_run, state, logger) -> None:
    root = Path(cfg.repo_root)
    if not (root / ".git").exists():
        raise _Stop(ExitCode.USAGE, "not a git repository")

    # R5: safe repo state, before anything is written
    r = git.run(["rev-parse", "--abbrev-ref", "HEAD"])
    if r.returncode != 0:
        raise RuntimeError("git rev-parse failed")
    current = r.stdout.strip()
    if current != branch:
        _refuse(logger, f"on branch {current}, expected {branch}")
    r = git.run(["rev-parse", "--git-dir"])
    if r.returncode != 0:
        raise RuntimeError("git rev-parse --git-dir failed")
    gitdir = (root / r.stdout.strip())
    for marker in ("MERGE_HEAD", "rebase-merge", "rebase-apply", "CHERRY_PICK_HEAD"):
        if (gitdir / marker).exists():
            _refuse(logger, f"{marker} present: a merge, rebase or cherry-pick is in progress")
    r = git.run(["diff", "--cached", "--quiet"])
    if r.returncode == 1:
        _refuse(logger, "the index already holds staged changes")
    if r.returncode != 0:
        raise RuntimeError("git diff --cached failed")

    # R2: verify before write
    opened = {"trials": _ledgers.open_trials(cfg, clock=clock), "decisions": _ledgers.open_decisions(cfg, clock=clock)}
    for kind, led in opened.items():
        v = led.verify(roots=True)
        if not v.ok:
            _verify_failed(logger, kind, v.first_broken_seq, v.reason)
    logger.info("verified")

    if dry_run:
        state["added"] = build_add_list(cfg, git)
        logger.info("staged", count=len(state["added"]), dry_run=True)
        return

    # R3: roots for closed days only (Ledger.write_roots enforces it)
    state["wrote"] = True
    for kind in ("trials", "decisions"):
        try:
            new = opened[kind].write_roots()
        except LedgerError as e:
            _verify_failed(logger, kind, getattr(e, "seq", None), str(e))
        state["roots"][kind] = [x.date for x in new]
        logger.info("roots_written", kind=kind, dates=state["roots"][kind])

    # R4/R6: stage the add-list
    add_list = build_add_list(cfg, git)
    state["added"] = add_list
    staged: list[str] = []

    def unstage(paths: Sequence[str]) -> None:
        for p in paths:
            git.run(["restore", "--staged", "--", p])

    for rel in add_list:
        needs_lock = rel.endswith(".jsonl") and not rel.endswith(".roots.jsonl")
        if needs_lock:
            with _Flock(root / (rel + ".lock"), fcntl.LOCK_SH):
                r = git.run(["add", "--", rel])
        else:
            r = git.run(["add", "--", rel])
        if r.returncode != 0:
            unstage(staged)
            raise _Stop(ExitCode.COMMIT_FAILED, f"git add failed for {rel}: {_first_line(r.stderr)}")
        staged.append(rel)
    logger.info("staged", count=len(staged))
    for rel in add_list:
        if rel.endswith(".jsonl"):
            r = git.run(["show", f":{rel}"])
            if r.returncode != 0:
                raise RuntimeError(f"git show failed for {rel}")
            if r.stdout and not r.stdout.endswith("\n"):
                unstage(staged)
                _refuse(logger, f"{rel} does not end with a newline (torn tail); see runbook manifest-torn-tail")
    r = git.run(["diff", "--cached", "--name-only", "-z"])
    if r.returncode != 0:
        raise RuntimeError("git diff --cached --name-only failed")
    extra = sorted(x for x in r.stdout.split("\0") if x and x not in set(add_list))
    if extra:
        unstage(staged)
        _refuse(logger, f"staged path outside the add-list: {extra[0]}")

    # R7: commit
    r = git.run(["diff", "--cached", "--quiet"])
    if r.returncode not in (0, 1):
        raise RuntimeError("git diff --cached failed")
    if r.returncode == 1:
        ist_day = clock().astimezone(_IST).date().isoformat()
        r = git.run(["commit", "-m", f"routine: roots and ledgers {ist_day} (run {log.get_run_id()})"])
        if r.returncode != 0:
            unstage(staged)
            err = (r.stderr + r.stdout).lower()
            if any(h in err for h in _IDENTITY_HINTS):
                raise _Stop(ExitCode.USAGE, "git identity missing: set user.name and user.email")
            raise _Stop(ExitCode.COMMIT_FAILED, f"git commit failed: {_first_line(r.stderr or r.stdout)}")
        r = git.run(["rev-parse", "HEAD"])
        if r.returncode != 0:
            raise RuntimeError("git rev-parse HEAD failed")
        state["committed"] = r.stdout.strip()
        logger.info("committed", hash=state["committed"])

    # R8: push (also when an earlier run committed but could not push)
    if state["committed"] is None:
        r = git.run(["rev-list", "--count", f"{remote}/{branch}..HEAD"])
        ahead = True if r.returncode != 0 else int(r.stdout.strip() or "0") > 0
        if not ahead:
            return
    r = git.run(["push", remote, branch], timeout=60)
    if r.returncode == 0:
        state["pushed"] = True
        logger.info("pushed", remote=remote, branch=branch)
        return
    reason = _redact(_first_line(r.stderr))[:200]
    logger.warning("push_failed", reason=reason)
    low = r.stderr.lower()
    if "rejected" in low or "non-fast-forward" in low or "fetch first" in low:
        raise _Stop(ExitCode.PUSH_REJECTED,
                    f"push rejected by {remote}; integrate the remote changes by hand ({reason})")
    raise _Stop(ExitCode.PUSH_FAILED, f"push to {remote} failed ({reason}); the next run will retry")


def _refuse(logger, reason: str):
    logger.error("refused", reason=_redact(reason))
    raise _Stop(ExitCode.REFUSED, f"refused: {reason}")


def _verify_failed(logger, kind: str, seq, reason: str):
    logger.error("verify_failed", ledger=kind, first_broken_seq=seq)
    raise _Stop(ExitCode.VERIFY_FAILED, f"{kind} ledger failed verification: first_broken_seq={seq} ({reason})")
