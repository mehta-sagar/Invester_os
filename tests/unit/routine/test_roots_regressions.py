"""S1-T3 review regressions D1-D9 (written red first against 39e22d1)."""
from __future__ import annotations

import fcntl
import json
import os
import subprocess
import threading
import time

import pytest

from _routine_helpers import D1, D2, D3, FakeNotifier, TODAY

REFSPEC = ["push", "origin", "refs/heads/main:refs/heads/main"]


# ---------- D1: hostile git push config must not turn the push into a force push ----------

HOSTILE = [
    ("local", "remote.origin.push", "+refs/heads/*:refs/heads/*"),
    ("local", "remote.origin.push", "+refs/heads/main:refs/heads/main"),
    ("local", "remote.origin.push", "+main:main"),
    ("global", "remote.origin.push", "+refs/heads/*:refs/heads/*"),
]


@pytest.mark.parametrize("scope,key,val", HOSTILE)
def test_D1_diverged_remote_not_overwritten_under_plus_refspec_config(env, scope, key, val, tmp_path):
    if scope == "global":
        with open(os.environ["GIT_CONFIG_GLOBAL"], "a") as f:
            f.write(f'[remote "origin"]\n\tpush = {val}\n')
    else:
        env.g("config", "--add", key, val)
    env.push_to_origin_from_other_clone()
    env.setup_three_days()
    rhead = env.remote_head()
    n = env.count()
    r = env.run()
    assert r.exit_code == 5 and r.pushed is False
    assert env.remote_head() == rhead
    assert env.count() == n + 1
    assert [c for c in env.git.commands if c[0] == "push"] == [REFSPEC]


def test_D1_push_default_variants_still_explicit_refspec(env):
    env.g("config", "push.default", "matching")
    env.g("config", "push.followTags", "true")
    env.setup_three_days()
    assert env.run().exit_code == 0
    assert [c for c in env.git.commands if c[0] == "push"] == [REFSPEC]
    assert env.remote_head() == env.head()


# ---------- D2: remote name validation, before any git call ----------

BAD_REMOTES = ["--force", "-f", "origin --force", "-origin", "a b", "o\tx", "origin;rm", "origin\n--force",
               "+origin", "", "--delete", "../x"]


@pytest.mark.parametrize("remote", BAD_REMOTES)
def test_D2_odd_remote_refused_before_any_git_call(env, remote):
    env.setup_three_days()
    r = env.run(remote=remote)
    assert r.exit_code == 1
    assert env.git.commands == []
    assert env.roots_dates("trials") == [] and env.staged() == []


def test_D2_unknown_remote_name_exit_1_no_push(env):
    env.setup_three_days()
    r = env.run(remote="nope")
    assert r.exit_code == 1 and not any(c[0] == "push" for c in env.git.commands)


def test_D2_cli_remote_equals_force(env, monkeypatch, capsys):
    monkeypatch.chdir(env.work)
    from investos import cli
    try:
        code = cli.main(["routine", "roots", "--remote=--force"])
    except SystemExit as e:
        code = e.code
    assert code == 1


# ---------- D3: bounded shared-lock wait ----------

def test_D3_stuck_writer_lock_times_out_with_alert(env, monkeypatch):
    import investos.routine.roots as roots
    for name in ("LOCK_WAIT_S", "LOCK_TIMEOUT_S", "SHARED_LOCK_TIMEOUT_S", "FILE_LOCK_TIMEOUT_S"):
        monkeypatch.setattr(roots, name, 1.0, raising=False)
    env.setup_three_days()
    mp = env.write("data/manifests/a.jsonl", '{"n":1}\n')
    lockp = env.work / "data/manifests/a.jsonl.lock"
    held, release = threading.Event(), threading.Event()

    def writer():
        with open(lockp, "a+") as lk:
            fcntl.flock(lk, fcntl.LOCK_EX)
            held.set()
            release.wait(60)

    w = threading.Thread(target=writer)
    w.start()
    assert held.wait(5)
    fn = FakeNotifier()
    out = []
    t = threading.Thread(target=lambda: out.append(env.run(notifier=fn)))
    t0 = time.monotonic()
    t.start()
    t.join(45)
    alive = t.is_alive()
    release.set()
    w.join(10)
    t.join(10)
    assert not alive, "routine waits for ever on a stuck writer lock"
    assert time.monotonic() - t0 < 45
    r = out[0]
    assert r.exit_code == 3, r          # REFUSED: alerts (BUSY 7 never alerts per R13)
    assert "a.jsonl" in r.message
    assert len(fn.calls) == 1 and fn.calls[0][1].startswith("exit 3 REFUSED")
    assert env.staged() == []
    last = json.loads((env.work / "data/ledger/.state/roots-routine.last.json").read_text())
    assert last["exit"] == 3


# ---------- D4: LC_ALL=C for every git call ----------

def test_D4_git_runs_with_lc_all_c(env, monkeypatch):
    real = subprocess.run
    envs = []

    def spy(args, *a, **kw):
        if isinstance(args, (list, tuple)) and args and args[0] == "git":
            envs.append(kw.get("env"))
        return real(args, *a, **kw)

    monkeypatch.setattr(subprocess, "run", spy)
    monkeypatch.setenv("LC_ALL", "de_DE.UTF-8")
    monkeypatch.setenv("LANG", "de_DE.UTF-8")
    env.setup_three_days()
    env.run()
    assert envs and all(e is not None and e.get("LC_ALL") == "C" for e in envs)


# ---------- D5: BUSY must not clobber the previous result ----------

def test_D5_busy_run_keeps_previous_last_result(env):
    env.setup_three_days()
    assert env.run().exit_code == 0
    p = env.work / "data/ledger/.state/roots-routine.last.json"
    before = p.read_bytes()
    with open(env.work / "data/ledger/.state/roots-routine.lock", "a+") as lk:
        fcntl.flock(lk, fcntl.LOCK_EX | fcntl.LOCK_NB)
        r = env.run()
    assert r.exit_code == 7
    assert p.read_bytes() == before
    assert sorted(x.name for x in p.parent.iterdir() if x.name.startswith(".tmp")) == []


# ---------- D6: files added by a hook after the safety check ----------

def test_D6_hook_added_file_detected_after_commit(env):
    h = env.work / ".git" / "hooks" / "pre-commit"
    h.write_text("#!/bin/sh\necho x > NOTES.txt\ngit add NOTES.txt\nexit 0\n")
    h.chmod(0o755)
    env.setup_three_days()
    rhead = env.remote_head()
    fn = FakeNotifier()
    r = env.run(notifier=fn)
    assert r.exit_code == 3 and r.pushed is False
    assert "NOTES.txt" in r.message
    assert env.remote_head() == rhead
    assert len(fn.calls) == 1


# ---------- D7: mutation survivors ----------

def test_D7_M05_bad_decisions_root_blocks_trials_roots_too(env):
    env.setup_three_days()
    env.run()
    env.add_rows("2026-11-21", decisions=0)                     # trials can be rooted, decisions cannot
    env.clock.set("2026-11-23T10:00:00")
    rp = env.work / "data/ledger/decisions.roots.jsonl"
    lines = rp.read_text().splitlines()
    o = json.loads(lines[0]); o["hash"] = "0" * 64
    lines[0] = json.dumps(o, sort_keys=True, separators=(",", ":"))
    rp.write_text("\n".join(lines) + "\n")
    before = env.roots_dates("trials")
    r = env.run()
    assert r.exit_code == 2
    assert env.roots_dates("trials") == before            # nothing written when ANY ledger fails verify
    assert "decisions" in r.message


def test_D7_M45_ledger_error_from_write_roots_maps_to_2(env, monkeypatch):
    from investos.registry.chain import Ledger, LedgerCorruptError
    env.setup_three_days()

    def boom(self):
        raise LedgerCorruptError("planted", 3)

    monkeypatch.setattr(Ledger, "write_roots", boom)
    r = env.run()
    assert r.exit_code == 2 and env.staged() == []


def test_D7_M08_M44_symlinked_dirs_pointing_outside_never_listed(env):
    env.setup_three_days()
    out = env.tmp / "outside"
    (out / "hosts").mkdir(parents=True)
    (out / "bse.jsonl").write_text('{"s":1}\n')
    (out / "hosts" / "h.jsonl").write_text('{"s":1}\n')
    (out / "r.runs").mkdir()
    (out / "r.runs" / "2026-11-01_2026-11-02.json").write_text("{}\n")
    os.symlink(out, env.work / "data/ledger/access")
    os.symlink(out / "r.runs", env.work / "data/manifests/r.runs")
    env.manifest("a")
    r = env.run()
    assert r.exit_code == 0, r.message
    assert not any(x.startswith("data/ledger/access") or "r.runs" in x for x in r.added)
    assert not any(x.startswith("data/ledger/access") or "r.runs" in x for x in env.files_in())


def test_D7_M37_state_dir_never_staged_even_when_not_ignored(env):
    gi = env.work / ".gitignore"
    gi.write_text(gi.read_text().replace("data/ledger/.state/\n", ""))
    env.setup_three_days()
    env.write("data/ledger/.state/ratelimit/h.jsonl", "{}\n")
    env.write("data/ledger/.state/x.jsonl", "{}\n")
    assert env.run().exit_code == 0
    assert env.run().exit_code == 0
    committed = env.g("ls-files").stdout.split()
    assert not any(".state" in x for x in committed)
    assert not any(".state" in x for x in env.git.commands[-1])
    assert not any(c[0] == "add" and ".state" in c[-1] for c in env.git.commands)


def _push_fail(stderr):
    return (lambda a: a[:1] == ["push"], subprocess.CompletedProcess(["git", "push"], 128, stdout="", stderr=stderr))


def test_D7_M35_url_with_credentials_never_in_message_log_or_alert(env):
    env.git.inject.append(_push_fail("fatal: unable to access 'https://user:tok3n@example.invalid/repo.git/': "
                                     "Could not resolve host: example.invalid\n"))
    env.setup_three_days()
    fn = FakeNotifier()
    r = env.run(notifier=fn)
    assert r.exit_code == 6
    blob = r.message + json.dumps(env.events()) + json.dumps(fn.calls) + \
        (env.work / "data/ledger/.state/roots-routine.last.json").read_text()
    for bad in ("https://", "tok3n", "user:", "example.invalid/repo"):
        assert bad not in blob


# ---------- D8: paths with spaces ----------

def test_D8_path_with_spaces_fully_redacted(env):
    env.git.inject.append(_push_fail("fatal: '/Users/Ann Lee/My Repo/origin.git' does not appear to be a git "
                                     "repository\n"))
    env.setup_three_days()
    fn = FakeNotifier()
    r = env.run(notifier=fn)
    assert r.exit_code == 6
    blob = r.message + json.dumps(env.events()) + json.dumps(fn.calls) + \
        (env.work / "data/ledger/.state/roots-routine.last.json").read_text()
    for bad in ("Ann", "Lee", "My Repo", "Users", "origin.git"):
        assert bad not in blob


def test_D8_repo_root_with_spaces_redacted_from_alert(env, tmp_path):
    spaced = "/tmp/Some Where/with space/repo"
    env.git.inject.append(_push_fail(f"fatal: '{spaced}' does not appear to be a git repository\n"))
    env.setup_three_days()
    fn = FakeNotifier()
    env.run(notifier=fn)
    assert "Some" not in fn.calls[0][1] and "space" not in fn.calls[0][1]
