"""S1-T3 A9, A12-A14, A17-A20: offline catch-up, logging/exit codes, kill safety, notifier, last file, plist, source."""
from __future__ import annotations

import fcntl
import json
import os
import plistlib
import re
import subprocess
import sys
import textwrap
from pathlib import Path

import pytest

from _routine_helpers import D1, D2, D3, FakeNotifier, TODAY, sh

REPO = Path(__file__).resolve().parents[3]


# ---------- A9 / R8 / R9 offline ----------

def test_A9_offline_commit_locally_exit_6_then_catch_up(env, capsys, monkeypatch):
    env.setup_three_days()
    env.take_origin_offline()
    n = env.count()
    r = env.run()
    assert r.exit_code == 6 and r.pushed is False
    assert env.count() == n + 1 and r.committed == env.head()
    assert env.roots_dates("trials") == [D1, D2]
    w = [e for e in env.events_named("push_failed")]
    assert len(w) == 1 and w[0]["level"] == "WARNING"
    assert "file://" not in json.dumps(w[0]["fields"]) and str(env.origin) not in json.dumps(w[0]["fields"])
    last = (env.work / "data/ledger/.state/roots-routine.last.json").read_text()
    assert "file://" not in last and str(env.origin) not in last and json.loads(last)["exit"] == 6
    env.bring_origin_back()
    r2 = env.run()
    assert r2.exit_code == 0 and r2.pushed is True
    assert dict(r2.roots_written) in ({}, {"trials": [], "decisions": []})
    assert r2.committed is None and env.count() == n + 1
    assert env.remote_head() == env.head()


def test_A9_cli_stdout_has_no_url_when_offline(env, capsys, monkeypatch):
    env.add_rows("2020-01-01")
    env.take_origin_offline()
    monkeypatch.chdir(env.work)
    from investos import cli
    code = cli.main(["routine", "roots"])
    out = capsys.readouterr().out
    assert code == 6 and "file://" not in out and str(env.origin) not in out
    obj = json.loads([x for x in out.splitlines() if x.startswith("{")][-1])
    assert obj["exit"] == 6 and obj["pushed"] is False and obj["committed"] == env.head()


# ---------- A12 / R11 source scan ----------

def _src() -> str:
    import investos.routine.roots as m
    return Path(m.__file__).read_text()


@pytest.mark.parametrize("pat", [r"os\.remove", r"\.unlink\(", r"rmtree", r"\.truncate\(", r"os\.rename", r"\.rename\(",
                                 r"shell\s*=\s*True", r"os\.system", r"--force", r"--no-verify", r"force-with-lease",
                                 r"\"-A\"|'-A'", r"\"reset\"|'reset'", r"\"checkout\"|'checkout'", r"\"clean\"|'clean'",
                                 r"\"stash\"|'stash'", r"\"pull\"|'pull'", r"\"fetch\"|'fetch'", r"\"rebase\"|'rebase'"])
def test_A12_source_has_no_forbidden_call(pat):
    assert re.search(pat, _src()) is None, pat


def test_A12_no_open_in_write_mode_and_no_os_replace_outside_state_file():
    src = _src()
    assert re.search(r"""\bopen\([^)]*,\s*['"][wax]b?\+?['"]""", src) is None
    assert re.search(r"""mode\s*=\s*['"][wax]""", src) is None


def test_A12_commands_issued_are_all_allow_listed(env):
    """The `env` fixture also checks this at teardown for every test; here an explicit full scenario."""
    from _routine_helpers import violations
    env.setup_three_days()
    env.manifest("a")
    env.run()
    env.run()
    assert violations(env.git.commands) == []


# ---------- A13 / R12 ----------

def _done(env):
    d = env.events_named("done")
    assert len(d) == 1, d
    return d[0]


def test_A13_happy_path_event_sequence(env):
    env.setup_three_days()
    env.run()
    names = [e["event"] for e in env.events() if e["task"] == "routine.roots"]
    for ev in ("start", "verified", "roots_written", "staged", "committed", "pushed", "done"):
        assert ev in names
    assert names.index("start") < names.index("verified") < names.index("roots_written") < names.index("staged") \
        < names.index("committed") < names.index("pushed") < names.index("done")
    assert 0 in _done(env)["fields"].values()


def test_A13_refused_and_verify_failed_log_error(env):
    env.g("checkout", "-q", "-b", "zzz")
    env.run()
    assert [e["level"] for e in env.events_named("refused")] == ["ERROR"]
    assert 3 in _done(env)["fields"].values()


def test_A13_verify_failed_logs_error(env):
    env.setup_three_days()
    env.run()
    env.clock.set("2026-11-23T10:00:00")
    p = env.work / "registry/trials.jsonl"
    p.write_bytes(p.read_bytes().replace(b'"hypothesis":"h"', b'"hypothesis":"X"', 1))
    env.run()
    assert [e["level"] for e in env.events_named("verify_failed")] == ["ERROR"]


def test_A13_exception_after_write_roots_exit_4_roots_on_disk(env):
    env.setup_three_days()
    env.git.inject.append((lambda a: a[:1] == ["add"], RuntimeError("boom")))
    r = env.run()
    assert r.exit_code == 4
    assert env.roots_dates("trials") == [D1, D2]
    assert 4 in _done(env)["fields"].values()


def test_A13_exception_before_any_root_exit_1(env):
    env.setup_three_days()
    env.git.inject.append((lambda a: a[:1] == ["rev-parse"], RuntimeError("boom")))
    r = env.run()
    assert r.exit_code == 1 and env.roots_dates("trials") == []


def test_A13_process_exit_status_equals_code(env, monkeypatch, capsys):
    env.g("checkout", "-q", "-b", "zzz")
    monkeypatch.chdir(env.work)
    from investos import cli
    assert cli.main(["routine", "roots"]) == 3


def test_A13_bad_flags_exit_1(env, monkeypatch, capsys):
    monkeypatch.chdir(env.work)
    from investos import cli
    try:
        code = cli.main(["routine", "roots", "--bogus"])
    except SystemExit as e:
        code = e.code
    assert code == 1


# ---------- A14 / R9: SIGKILL right after write_roots ----------

KILL_SCRIPT = textwrap.dedent("""
    import os, signal, sys
    from datetime import datetime, timezone
    from pathlib import Path
    from investos.core import config
    from investos.routine.roots import Git, run_routine
    work = Path(sys.argv[1])
    cfg = config.load_config(work / "config" / "investos.toml", env={"PATH": os.environ["PATH"]}, repo_root=work)
    class KillGit(Git):
        def run(self, args, *, check=False, **kw):
            a = [x for x in args if x != "git"]
            if a[:1] == ["add"]:
                os.kill(os.getpid(), signal.SIGKILL)
            return super().run(args, check=check, **kw)
    run_routine(cfg, git=KillGit(work), clock=lambda: datetime(2026, 11, 20, 10, 0, tzinfo=timezone.utc))
""")


def test_A14_sigkill_after_write_roots_then_next_run_commits(env):
    env.setup_three_days()
    n = env.count()
    # D9: no dependence on an editable install: PYTHONPATH=src, cwd outside the repo
    p = subprocess.run([sys.executable, "-c", KILL_SCRIPT, str(env.work)], capture_output=True, text=True,
                       cwd=str(env.tmp), env={**os.environ, "PYTHONPATH": str(REPO / "src")})
    assert p.returncode == -9, (p.returncode, p.stderr[-500:])
    assert env.roots_dates("trials") == [D1, D2] and env.count() == n
    r = env.run()
    assert r.exit_code == 0 and env.count() == n + 1
    assert "registry/trials.roots.jsonl" in env.files_in()
    assert env.roots_dates("trials") == [D1, D2] and env.roots_dates("decisions") == [D1, D2]
    for led in env.ledgers():
        assert led.verify(roots=True).ok


# ---------- A17 / R13 notifier ----------

def _s0(env):
    env.setup_three_days()


def _s1(env):
    env.g("config", "--unset", "user.name")
    env.g("config", "--unset", "user.email")
    env.g("config", "user.useConfigOnly", "true")
    env.setup_three_days()


def _s2(env):
    env.setup_three_days()
    env.run()
    env.clock.set("2026-11-23T10:00:00")
    p = env.work / "registry/trials.jsonl"
    p.write_bytes(p.read_bytes().replace(b'"hypothesis":"h"', b'"hypothesis":"X"', 1))


def _s3(env):
    env.setup_three_days()
    env.g("checkout", "-q", "-b", "zzz")


def _s4(env):
    env.setup_three_days()
    h = env.work / ".git" / "hooks" / "pre-commit"
    h.write_text("#!/bin/sh\nexit 1\n")
    h.chmod(0o755)


def _s5(env):
    env.push_to_origin_from_other_clone()
    env.setup_three_days()


def _s6(env):
    env.setup_three_days()
    env.take_origin_offline()


_LOCK = []


def _s7(env):
    env.setup_three_days()
    st = env.work / "data/ledger/.state"
    st.mkdir(parents=True, exist_ok=True)
    f = open(st / "roots-routine.lock", "a+")
    fcntl.flock(f, fcntl.LOCK_EX | fcntl.LOCK_NB)
    _LOCK.append(f)


SCEN = {0: _s0, 1: _s1, 2: _s2, 3: _s3, 4: _s4, 5: _s5, 6: _s6, 7: _s7}
NAMES = {0: "OK", 1: "USAGE", 2: "VERIFY_FAILED", 3: "REFUSED", 4: "COMMIT_FAILED", 5: "PUSH_REJECTED",
         6: "PUSH_FAILED", 7: "BUSY"}


@pytest.fixture(autouse=True)
def _release_locks():
    yield
    while _LOCK:
        _LOCK.pop().close()


@pytest.mark.parametrize("code", range(8))
def test_A17_notifier_called_once_on_2_3_5_6_only(env, code):
    SCEN[code](env)
    fn = FakeNotifier()
    r = env.run(notifier=fn)
    assert int(r.exit_code) == code
    if code in (2, 3, 5, 6):
        assert len(fn.calls) == 1
        title, text = fn.calls[0]
        assert title == "Invest OS: roots routine"
        assert text.startswith(f"exit {code} {NAMES[code]}")
        assert "file://" not in text and str(env.tmp) not in text and "\n" not in text
    else:
        assert fn.calls == []


@pytest.mark.parametrize("code", [2, 3, 5, 6])
def test_A17_dry_run_never_notifies(env, code):
    SCEN[code](env)
    fn = FakeNotifier()
    env.run(notifier=fn, dry_run=True)
    assert fn.calls == []


def test_A17_failing_notifier_keeps_exit_code_and_logs(env):
    _s3(env)
    r = env.run(notifier=FakeNotifier(raises=OSError("no osascript")))
    assert r.exit_code == 3
    w = env.events_named("notify_failed")
    assert len(w) == 1 and w[0]["level"] == "WARNING"


# ---------- A18 ----------

def test_A18_default_notifier_runs_no_osascript(env, monkeypatch):
    real = subprocess.run
    seen = []

    def spy(args, *a, **kw):
        seen.append(list(args) if not isinstance(args, str) else [args])
        return real(args, *a, **kw)

    monkeypatch.setattr(subprocess, "run", spy)
    _s3(env)
    r = env.run()                     # no notifier argument
    assert r.exit_code == 3
    assert not any("osascript" in " ".join(c) for c in seen)


@pytest.mark.parametrize("platform,expect_osa", [("darwin", True), ("linux", False)])
def test_A18_cli_selects_osascript_notifier_only_on_darwin(env, monkeypatch, platform, expect_osa):
    from investos.core.notify import OsascriptNotifier
    import investos.routine.roots as roots
    captured = {}

    def fake_run(cfg, **kw):
        captured.update(kw)
        return roots.RoutineResult(roots.ExitCode.OK, {}, None, False, [], "ok")

    monkeypatch.setattr(roots, "run_routine", fake_run)
    import investos.cli as cli
    monkeypatch.setattr(cli, "run_routine", fake_run, raising=False)
    monkeypatch.setattr(sys, "platform", platform)
    monkeypatch.chdir(env.work)
    cli.main(["routine", "roots", "--dry-run"])
    n = captured.get("notifier")
    assert isinstance(n, OsascriptNotifier) is expect_osa


# ---------- A19 / R14 ----------

def test_A19_last_result_file(env):
    env.setup_three_days()
    r = env.run()
    p = env.work / "data/ledger/.state/roots-routine.last.json"
    obj = json.loads(p.read_text())
    assert set(obj) == {"added", "branch", "committed", "exit", "pushed", "roots", "ts"}
    assert re.fullmatch(r"\d{4}-\d\d-\d\dT\d\d:\d\d:\d\d\.\d{6}Z", obj["ts"])
    assert obj["exit"] == 0 and obj["committed"] == r.committed and obj["pushed"] is True
    assert obj["roots"] == {"decisions": [D1, D2], "trials": [D1, D2]}
    assert "data/ledger/.state/roots-routine.last.json" not in env.g("ls-files").stdout
    assert "roots-routine.last.json" not in " ".join(env.files_in())


def test_A19_written_also_for_failed_runs_and_dry_run_leaves_it(env):
    _s3(env)
    env.run()
    p = env.work / "data/ledger/.state/roots-routine.last.json"
    assert json.loads(p.read_text())["exit"] == 3
    before = p.read_bytes()
    env.run(dry_run=True)
    assert p.read_bytes() == before


def test_A19_unwritable_result_file_does_not_change_exit(env):
    env.setup_three_days()
    st = env.work / "data/ledger/.state"
    st.mkdir(parents=True, exist_ok=True)
    (st / "roots-routine.last.json").mkdir()          # os.replace onto a directory fails
    r = env.run()
    assert r.exit_code == 0


# ---------- A20 / R15 ----------

PLIST = REPO / "ops" / "launchd" / "com.investos.roots.plist.example"


def test_A20_plist_content():
    with open(PLIST, "rb") as f:
        d = plistlib.load(f)
    assert d["Label"] == "com.investos.roots"
    assert d["StartCalendarInterval"] == {"Hour": 23, "Minute": 30}
    assert d["RunAtLoad"] is True
    args = d["ProgramArguments"]
    i = args.index("routine")
    assert args[i + 1] == "roots" and Path(args[0]).name == "investos" or "investos" in args[0]
    assert all("<" not in a and ">" not in a for a in args)
    text = PLIST.read_text()
    assert "Asia/Kolkata" in text and re.search(r"local\s+time\s*zone", text, re.I)
    assert "--force" not in text
