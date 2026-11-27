"""S1-T3 A5-A8, A10, A11: repo-state refusal, torn tail, idempotence, identity, dry-run, locks."""
from __future__ import annotations

import subprocess
import threading
import time
import fcntl

import pytest

from _routine_helpers import D1, D2, D3, sh


# ---------- A5 / R5 ----------

def test_A5_wrong_branch_refused_nothing_written(env):
    env.setup_three_days()
    env.g("checkout", "-q", "-b", "task/S1-T1")
    before = env.count()
    r = env.run()
    assert r.exit_code == 3
    assert "task/S1-T1" in r.message or "branch" in r.message
    assert env.count() == before
    assert not (env.work / "registry" / "trials.roots.jsonl").exists()
    assert not (env.work / "data" / "ledger" / "decisions.roots.jsonl").exists()
    assert env.staged() == []


def test_A5_merge_in_progress_refused(env):
    env.setup_three_days()
    (env.work / ".git" / "MERGE_HEAD").write_text(env.head() + "\n")
    r = env.run()
    assert r.exit_code == 3 and env.roots_dates("trials") == []


@pytest.mark.parametrize("marker,is_dir", [("rebase-merge", True), ("rebase-apply", True), ("CHERRY_PICK_HEAD", False)])
def test_A5_rebase_or_cherry_pick_in_progress_refused(env, marker, is_dir):
    env.setup_three_days()
    p = env.work / ".git" / marker
    p.mkdir() if is_dir else p.write_text(env.head() + "\n")
    r = env.run()
    assert r.exit_code == 3 and env.roots_dates("trials") == []


def test_A5_other_branch_allowed_when_asked(env):
    env.g("checkout", "-q", "-b", "other")
    env.g("push", "-q", "-u", "origin", "other")
    env.branch = "other"
    env.setup_three_days()
    r = env.run(branch="other")
    assert r.exit_code == 0 and env.remote_head("other") == env.head()
    assert env.remote_head("main") != env.head()


def test_A5_unstaged_changes_elsewhere_are_allowed_and_untouched(env):
    env.setup_three_days()
    (env.work / "src" / "investos" / "cli.py").write_text("# changed\n")
    r = env.run()
    assert r.exit_code == 0
    assert (env.work / "src" / "investos" / "cli.py").read_text() == "# changed\n"
    assert "src/investos/cli.py" not in env.files_in()


# ---------- A6 / R6 ----------

def test_A6_torn_tail_manifest_refused_and_nothing_committed(env):
    env.setup_three_days()
    env.write("data/manifests/a.jsonl", '{"n":1}\n{"n":2')
    env.manifest("b")
    before = env.count()
    r = env.run()
    assert r.exit_code == 3
    assert "a.jsonl" in r.message and "manifest-torn-tail" in r.message
    assert env.count() == before and env.staged() == []
    assert env.remote_head() == env.head()
    assert "data/manifests/a.jsonl" not in env.g("ls-files").stdout.split()
    assert "data/manifests/b.jsonl" not in env.g("ls-files").stdout.split()


def test_A6_waits_for_writer_holding_exclusive_lock(env):
    env.setup_three_days()
    mp = env.write("data/manifests/a.jsonl", '{"n":1}\n')
    lockp = env.work / "data/manifests/a.jsonl.lock"
    held, done = threading.Event(), threading.Event()

    def writer():
        with open(lockp, "a+") as lk:
            fcntl.flock(lk, fcntl.LOCK_EX)
            with open(mp, "ab") as f:
                f.write(b'{"n":2')
                f.flush()
                held.set()
                time.sleep(1.0)
                f.write(b"}\n")
            fcntl.flock(lk, fcntl.LOCK_UN)
        done.set()

    th = threading.Thread(target=writer)
    th.start()
    assert held.wait(5)
    t0 = time.monotonic()
    r = env.run()
    th.join()
    assert r.exit_code == 0, r.message
    assert time.monotonic() - t0 >= 0.8
    blob = env.g("show", "HEAD:data/manifests/a.jsonl").stdout
    assert blob.endswith("\n") and blob.splitlines()[-1] == '{"n":2}'


# ---------- A7 / R7 / R9 ----------

def test_A7_second_run_is_a_noop(env):
    env.setup_three_days()
    env.manifest("a")
    assert env.run().exit_code == 0
    n, head, roots = env.count(), env.head(), env.roots_dates("trials")
    env.reset_commands()
    r = env.run()
    assert r.exit_code == 0 and r.committed is None and r.pushed is False
    assert dict(r.roots_written) in ({}, {"trials": [], "decisions": []})
    assert env.count() == n and env.head() == head and env.roots_dates("trials") == roots
    assert not any(c[0] in ("commit", "push") for c in env.git.commands)


def test_A7_new_manifest_line_gives_one_commit_with_that_file_only(env):
    env.setup_three_days()
    env.manifest("a")
    env.run()
    n = env.count()
    with open(env.work / "data/manifests/a.jsonl", "a") as f:
        f.write('{"n":99}\n')
    r = env.run()
    assert r.exit_code == 0 and env.count() == n + 1
    assert env.files_in() == ["data/manifests/a.jsonl"]
    assert env.remote_head() == env.head()


def test_A7_hooks_are_not_bypassed(env):
    hooks = env.work / ".git" / "hooks"
    for name in ("pre-commit", "pre-push"):
        h = hooks / name
        h.write_text(f"#!/bin/sh\ntouch '{env.tmp}/{name}.ran'\nexit 0\n")
        h.chmod(0o755)
    env.setup_three_days()
    assert env.run().exit_code == 0
    assert (env.tmp / "pre-commit.ran").exists() and (env.tmp / "pre-push.ran").exists()


def test_A7_commit_failure_from_hook_is_exit_4_not_usage(env):
    h = env.work / ".git" / "hooks" / "pre-commit"
    h.write_text("#!/bin/sh\necho veto >&2\nexit 1\n")
    h.chmod(0o755)
    env.setup_three_days()
    before = env.count()
    r = env.run()
    assert r.exit_code == 4 and env.count() == before
    assert env.roots_dates("trials") == [D1, D2]       # roots stay on disk (safe)


# ---------- A8 / R7 identity, dry-run ----------

def test_A8_no_identity_exit_1(env, monkeypatch):
    env.g("config", "--unset", "user.name")
    env.g("config", "--unset", "user.email")
    env.g("config", "user.useConfigOnly", "true")
    env.setup_three_days()
    before = env.count()
    r = env.run()
    assert r.exit_code == 1
    assert "user.name" in r.message or "user.email" in r.message
    assert env.count() == before
    assert not any(c[0] == "config" and ("--unset" in c or len([x for x in c if not x.startswith("-")]) > 2)
                   for c in env.git.commands), "routine must never set identity"


def test_A8_dry_run_changes_nothing_and_lists_plan(env, capsys, monkeypatch):
    for d in ("2020-01-01", "2020-01-02"):
        env.add_rows(d)
    env.manifest("a")
    snap, head = env.tree_snapshot(), env.head()
    monkeypatch.chdir(env.work)
    from investos import cli
    code = cli.main(["routine", "roots", "--dry-run"])
    out = capsys.readouterr().out
    assert code == 0
    assert env.tree_snapshot() == snap and env.head() == head and env.staged() == []
    for p in ("registry/trials.jsonl", "data/ledger/decisions.jsonl", "data/manifests/a.jsonl"):
        assert p in out
    assert "data/raw" not in out


def test_A8_dry_run_api_no_git_change_no_last_file(env):
    env.setup_three_days()
    env.manifest("a")
    snap = env.tree_snapshot()
    r = env.run(dry_run=True)
    assert r.exit_code == 0 and r.committed is None and r.pushed is False
    assert env.tree_snapshot() == snap and env.staged() == []
    assert "data/manifests/a.jsonl" in r.added or any("a.jsonl" in x for x in r.added)
    assert not any(c[0] in ("add", "commit", "push", "restore") for c in env.git.commands)
    assert not (env.work / "data/ledger/.state/roots-routine.last.json").exists()


# ---------- A10 / R8 ----------

def test_A10_remote_ahead_exit_5_local_kept_no_integration(env):
    env.push_to_origin_from_other_clone()
    env.setup_three_days()
    rhead = env.remote_head()
    n = env.count()
    r = env.run()
    assert r.exit_code == 5 and r.pushed is False
    assert env.count() == n + 1 and r.committed == env.head()
    assert env.remote_head() == rhead
    assert not any(c[0] in ("pull", "fetch", "rebase", "merge", "reset", "checkout") for c in env.git.commands)
    assert [c for c in env.git.commands if c[0] == "push"] == [["push", "origin", "refs/heads/main:refs/heads/main"]]


def test_A10_pre_receive_hook_rejects_no_forced_retry(env):
    h = env.origin / "hooks" / "pre-receive"
    h.write_text("#!/bin/sh\necho 'rejected by policy' >&2\nexit 1\n")
    h.chmod(0o755)
    env.setup_three_days()
    r = env.run()
    assert r.exit_code in (5, 6) and r.pushed is False
    assert len([c for c in env.git.commands if c[0] == "push"]) == 1


# ---------- A11 / R10 ----------

def test_A11_lock_held_exit_7_nothing_written(env):
    env.setup_three_days()
    st = env.work / "data" / "ledger" / ".state"
    st.mkdir(parents=True, exist_ok=True)
    with open(st / "roots-routine.lock", "a+") as lk:
        fcntl.flock(lk, fcntl.LOCK_EX | fcntl.LOCK_NB)
        t0 = time.monotonic()
        r = env.run()
        assert time.monotonic() - t0 < 5
    assert r.exit_code == 7 and env.roots_dates("trials") == [] and env.staged() == []


def test_A11_two_routines_together_no_duplicate_roots(env):
    from investos.routine.roots import run_routine
    from _routine_helpers import make_git_classes
    env.setup_three_days()
    for i in range(3):
        env.manifest(f"s{i}", lines=200)
    G = make_git_classes()
    res = []

    def go():
        res.append(run_routine(env.cfg, git=G(env.work), clock=env.clock, branch="main", remote="origin"))

    ths = [threading.Thread(target=go) for _ in range(2)]
    [t.start() for t in ths]
    [t.join() for t in ths]
    codes = sorted(int(r.exit_code) for r in res)
    assert codes in ([0, 0], [0, 7])
    for led in env.ledgers():
        assert led.verify(roots=True).ok
    assert env.roots_dates("trials") == [D1, D2] and env.roots_dates("decisions") == [D1, D2]
