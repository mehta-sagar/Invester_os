"""Helpers for S1-T3 tests. investos imports are lazy so every test fails individually while routine/ is absent."""
from __future__ import annotations

import hashlib
import json
import os
import subprocess
from datetime import datetime, timedelta, timezone
from pathlib import Path

TOML = """\
[general]
timezone = "Asia/Kolkata"

[paths]
raw        = "data/raw"
manifests  = "data/manifests"
canonical  = "data/canonical"
ledger     = "data/ledger"
reports    = "reports"
registry   = "registry"

[db]
migrations_dir = "db/migrations"

[rate_limits]
default_interval_s = 0.35

[rate_limits.hosts]
"www.bseindia.com" = 0.35
"""

GITIGNORE = """\
data/raw/*
!data/raw/.gitkeep
data/staged/*
data/canonical/*
data/features/*
data/models/*
data/quarantine/*
data/exports/*
*.lock
.tmp-*
*.tmp
reports/checks/*
credentials/
__pycache__/
data/ledger/.state/
data/manifests/ignored.jsonl
"""

# days used by most scenarios (IST); today = D3
D1, D2, D3 = "2026-11-18", "2026-11-19", "2026-11-20"
TODAY = "2026-11-20T10:00:00"

READ_ONLY = {"rev-parse", "status", "diff", "show", "rev-list", "ls-files", "check-ignore", "config"}
FORBIDDEN_ARGS = {"--force", "-f", "--force-with-lease", "--delete", "-d", "--no-verify", "-A", "--all", "-a",
                  "-u", "-am", "-n", "--author", "--amend"}


def utc(s: str) -> datetime:
    return datetime.fromisoformat(s.rstrip("Z") + "+00:00")


class FakeClock:
    def __init__(self, start: str = TODAY):
        self.now = utc(start)

    def __call__(self) -> datetime:
        return self.now

    def set(self, s: str) -> "FakeClock":
        self.now = utc(s)
        return self


class FakeNotifier:
    def __init__(self, raises: BaseException | None = None):
        self.calls: list[tuple[str, str]] = []
        self.raises = raises

    def notify(self, title, message):
        self.calls.append((title, message))
        if self.raises:
            raise self.raises


def sh(cwd, *args, check=True, env_extra=None):
    env = dict(os.environ)
    env.update({"GIT_AUTHOR_NAME": "t", "GIT_AUTHOR_EMAIL": "t@example.invalid",
                "GIT_COMMITTER_NAME": "t", "GIT_COMMITTER_EMAIL": "t@example.invalid"})
    env.update(env_extra or {})
    r = subprocess.run(["git", *args], cwd=str(cwd), capture_output=True, text=True, env=env)
    if check and r.returncode != 0:
        raise AssertionError(f"git {args} failed: {r.stderr}")
    return r


def canon(obj) -> str:
    return json.dumps(obj, sort_keys=True, separators=(",", ":"), ensure_ascii=False, allow_nan=False)


def make_git_classes():
    """Build RecordingGit lazily (needs investos.routine.roots.Git)."""
    from investos.routine.roots import Git

    class RecordingGit(Git):
        def __init__(self, cwd, *a, **kw):
            super().__init__(cwd, *a, **kw)
            self.commands: list[list[str]] = []
            self.inject: list = []          # (predicate(args) -> bool, CompletedProcess | BaseException)

        def run(self, args, *, check=False, **kw):
            args = [str(a) for a in args]
            self.commands.append(list(args))
            norm = _norm(args)
            for pred, what in self.inject:
                if pred(norm):
                    if isinstance(what, BaseException):
                        raise what
                    return what
            return super().run(args, check=check, **kw)

    return RecordingGit


def _norm(args):
    a = list(args)
    if a and a[0] == "git":
        a = a[1:]
    return a


def violations(cmds, remote="origin", branch="main") -> list[str]:
    out = []
    for raw in cmds:
        a = _norm(raw)
        if not a:
            out.append(f"empty command {raw}")
            continue
        sub = a[0]
        bad = [x for x in a if x in FORBIDDEN_ARGS or x.startswith("--force") or x.startswith("+")]
        if bad:
            out.append(f"{a}: forbidden arg {bad}")
        if sub in READ_ONLY:
            if sub == "config" and any(x in a for x in ("--unset", "--add", "--replace-all", "--edit", "--global",
                                                         "--system")):
                out.append(f"{a}: config write")
            if sub == "config" and len([x for x in a[1:] if not x.startswith("-")]) > 1:
                out.append(f"{a}: config set")
        elif sub == "add":
            if not (len(a) == 3 and a[1] == "--" and a[2] not in (".", "") and not a[2].startswith(("/", ".."))
                    and "*" not in a[2]):
                out.append(f"{a}: add must be `add -- <single repo-relative path>`")
        elif sub == "commit":
            pass
        elif sub == "push":
            if a != ["push", remote, branch]:
                out.append(f"{a}: push must be exactly `push {remote} {branch}`")
        elif sub == "restore":
            if not (len(a) == 4 and a[1:3] == ["--staged", "--"]):
                out.append(f"{a}: restore only `--staged -- <path>`")
        else:
            out.append(f"{a}: command not in allow-list")
    return out


class RoutineEnv:
    def __init__(self, work: Path, logbuf):
        from investos.core import config
        self.tmp = work.parent
        self.work = work
        self.origin = self.tmp / "origin.git"
        self.logbuf = logbuf
        self.clock = FakeClock()
        self.remote = "origin"
        self.branch = "main"
        sh(self.tmp, "init", "-q", "--bare", "-b", "main", str(self.origin))
        work.mkdir(parents=True)
        (work / "pyproject.toml").write_text("[project]\nname='x'\n")
        for z in ("data/raw", "data/manifests", "data/canonical", "data/ledger", "reports", "registry",
                  "data/staged"):
            (work / z).mkdir(parents=True, exist_ok=True)
        (work / "db" / "migrations").mkdir(parents=True)
        (work / "db" / "migrations" / "0000_bootstrap.sql").write_text(
            "CREATE TABLE meta(key VARCHAR PRIMARY KEY, value VARCHAR NOT NULL);\n"
            "INSERT INTO meta VALUES ('schema_family','investos');\n")
        (work / "config").mkdir()
        (work / "config" / "investos.toml").write_text(TOML)
        (work / ".gitignore").write_text(GITIGNORE)
        (work / "src" / "investos").mkdir(parents=True)
        (work / "src" / "investos" / "cli.py").write_text("# stand-in\n")
        sh(work, "init", "-q", "-b", "main")
        sh(work, "config", "user.name", "Sagar Test")
        sh(work, "config", "user.email", "sagar@example.invalid")
        sh(work, "remote", "add", "origin", self.url)
        sh(work, "add", ".")
        sh(work, "commit", "-q", "-m", "init")
        sh(work, "push", "-q", "-u", "origin", "main")
        self.cfg = config.load_config(work / "config" / "investos.toml", env={"PATH": os.environ["PATH"]}, repo_root=work)
        self.git = make_git_classes()(work)
        self.notifier = None

    # ---- plumbing
    @property
    def url(self) -> str:
        return "file://" + str(self.origin)

    def g(self, *args, **kw):
        return sh(self.work, *args, **kw)

    def run(self, **kw):
        from investos.routine.roots import run_routine
        kw.setdefault("git", self.git)
        kw.setdefault("clock", self.clock)
        kw.setdefault("branch", self.branch)
        kw.setdefault("remote", self.remote)
        if self.notifier is not None:
            kw.setdefault("notifier", self.notifier)
        return run_routine(self.cfg, **kw)

    def assert_commands_allowed(self):
        v = violations(self.git.commands, self.remote, self.branch)
        assert not v, v

    def reset_commands(self):
        self.git.commands.clear()

    def count(self) -> int:
        return int(self.g("rev-list", "--count", "HEAD").stdout.strip())

    def head(self) -> str:
        return self.g("rev-parse", "HEAD").stdout.strip()

    def remote_head(self, branch="main") -> str:
        return sh(self.tmp, "--git-dir", str(self.origin), "rev-parse", branch).stdout.strip()

    def files_in(self, rev="HEAD") -> list[str]:
        return sorted(self.g("show", "--name-only", "--format=", rev).stdout.split())

    def staged(self) -> list[str]:
        return self.g("diff", "--cached", "--name-only").stdout.split()

    def events(self) -> list[dict]:
        return [json.loads(x) for x in self.logbuf.getvalue().splitlines() if x.strip()]

    def events_named(self, name) -> list[dict]:
        return [e for e in self.events() if e["task"] == "routine.roots" and e["event"] == name]

    # ---- data
    def add_rows(self, day: str, *, trials=1, decisions=1, hour="06:00:00"):
        from investos.registry import ledgers
        keep = self.clock.now
        self.clock.set(f"{day}T{hour}")
        t = ledgers.open_trials(self.cfg, clock=self.clock)
        d = ledgers.open_decisions(self.cfg, clock=self.clock)
        for i in range(trials):
            ledgers.register_trial(t, trial_id=f"t-{day}-{i}-{len(t.rows())}", family="f", feature_id="x",
                                   params={}, hypothesis="h")
        for i in range(decisions):
            ledgers.log_decision(d, date=day, security_id="S1", action="no_trade", reasons=["r"],
                                 model_versions={})
        self.clock.now = keep

    def ledgers(self):
        from investos.registry import ledgers
        return (ledgers.open_trials(self.cfg, clock=self.clock), ledgers.open_decisions(self.cfg, clock=self.clock))

    def roots_dates(self, kind: str) -> list[str]:
        p = self.work / ("registry/trials.roots.jsonl" if kind == "trials" else "data/ledger/decisions.roots.jsonl")
        if not p.exists():
            return []
        return [json.loads(x)["date"] for x in p.read_text().splitlines()]

    def write(self, rel: str, text: str = "x\n") -> Path:
        p = self.work / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(text)
        return p

    def manifest(self, source="a", lines=1) -> Path:
        return self.write(f"data/manifests/{source}.jsonl", "".join(f'{{"n":{i}}}\n' for i in range(lines)))

    def tree_snapshot(self) -> dict[str, str]:
        out = {}
        for p in sorted(self.work.rglob("*")):
            rel = p.relative_to(self.work).as_posix()
            if rel.startswith(".git/") or rel == ".git" or rel.endswith("roots-routine.lock"):
                continue
            if p.is_file() and not p.is_symlink():
                out[rel] = hashlib.sha256(p.read_bytes()).hexdigest()
            else:
                out[rel] = "dir" if p.is_dir() else "link"
        return out

    def push_to_origin_from_other_clone(self, name="x.txt"):
        other = self.tmp / "other"
        if not other.exists():
            sh(self.tmp, "clone", "-q", self.url, str(other))
        (other / name).write_text("other\n")
        sh(other, "add", name)
        sh(other, "commit", "-q", "-m", "other")
        sh(other, "push", "-q", "origin", "main")

    def setup_three_days(self):
        for d in (D1, D2, D3):
            self.add_rows(d)
        self.clock.set(TODAY)

    def take_origin_offline(self):
        self.origin.rename(self.tmp / "origin.away")

    def bring_origin_back(self):
        (self.tmp / "origin.away").rename(self.origin)
