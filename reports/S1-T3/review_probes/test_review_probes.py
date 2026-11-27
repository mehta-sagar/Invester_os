"""Reviewer safety probes for S1-T3 (real git, temp repos). Copy next to tests/unit/routine/conftest.py to run."""
import os, subprocess, sys, time, threading, fcntl
from pathlib import Path
import pytest
from _routine_helpers import D1, D2, D3, sh

def committed(env):
    return env.g("ls-tree", "-r", "--name-only", "HEAD").stdout.split()

def test_P1_raw_env_creds_outside_symlink(env, tmp_path):
    env.setup_three_days()
    outside = tmp_path / "outside"; outside.mkdir()
    (outside / "secret.jsonl").write_text('{"s":1}\n')
    (outside / "1_2.json").write_text("{}\n")
    # raw/staged/credentials/.env
    env.write("data/raw/bse/ab/deadbeef", "RAW\n")
    env.write("data/raw/a.jsonl", "RAW\n")                     # raw file with manifest-like name
    env.write("data/staged/x.jsonl", "S\n")
    env.write(".env", "KEY=1\n"); env.write("credentials/k", "k\n")
    env.write("data/ledger/.state/x.jsonl", "S\n")
    env.write("data/manifests/.env", "KEY=1\n")
    env.write("data/manifests/secret.env", "KEY=1\n")
    env.write("data/manifests/credentials.env.jsonl", "x\n")   # name fails SOURCE_RE? (dots)
    env.write("data/manifests/a.runs/.env", "KEY=1\n")
    env.write("data/manifests/a.runs/1_2.json", "{}\n")        # legit sidecar
    env.write("data/manifests/a.runs/credentials.json", "{}\n")
    env.write("NOTES.txt")
    os.symlink(outside / "secret.jsonl", env.work / "data/manifests/evil.jsonl")
    os.symlink("/etc/passwd", env.work / "data/manifests/pw.jsonl")
    os.symlink(outside, env.work / "data/manifests/b.runs")
    os.symlink(outside, env.work / "data/ledger/access_link")
    (env.work / "data/ledger/access").mkdir(exist_ok=True)
    os.symlink(outside, env.work / "data/ledger/access/hosts")
    os.symlink(env.work / "data/raw/a.jsonl", env.work / "data/manifests/rawlink.jsonl")  # symlink inside repo
    os.symlink("../../.env", env.work / "data/manifests/envlink.jsonl")
    r = env.run()
    files = committed(env)
    print(r.exit_code, r.added, files)
    assert r.exit_code == 0
    for bad in ("data/raw", ".env", "credentials", "NOTES", "evil", "pw.jsonl", "b.runs", "rawlink", "envlink", "hosts", "secret.env", "data/staged", ".state"):
        assert not any(bad in f for f in files if f not in ("config/investos.toml",)), (bad, files)
    assert "data/manifests/a.runs/1_2.json" in files
    assert "data/manifests/a.runs/credentials.json" not in files

def test_P2_nonff_push_never_forces(env):
    env.setup_three_days()
    env.push_to_origin_from_other_clone()
    other_head = env.remote_head()
    # hostile config: force refspec configured for the remote
    env.g("config", "remote.origin.push", "+refs/heads/*:refs/heads/*")
    env.g("config", "push.default", "matching")
    env.g("config", "alias.push", "push --force")
    r = env.run()
    assert int(r.exit_code) == 5, r
    assert env.remote_head() == other_head
    pushes = [c for c in env.git.commands if c[:1] == ["push"] or c[:2] == ["git", "push"]]
    print(pushes)
    assert len(pushes) == 1
    # run again: still rejected, remote unchanged
    assert int(env.run().exit_code) == 5
    assert env.remote_head() == other_head

def test_P3_remote_option_injection(env, tmp_path):
    env.setup_three_days()
    r = env.run(remote="--force")
    print(r.exit_code, r.message, [c for c in env.git.commands if "push" in c or "rev-list" in c])
    # record only; failure expected (exit 6/5) and no force semantic
    pushes = [c for c in env.git.commands if "push" in c]
    print("PUSHCMD", pushes)

def test_P4_unsafe_states(env):
    env.setup_three_days()
    env.g("checkout", "-q", "--detach")
    assert int(env.run().exit_code) == 3
    env.g("checkout", "-q", "main")
    env.write("src/other.py", "x\n"); env.g("add", "src/other.py")
    assert int(env.run().exit_code) == 3
    assert env.staged() == ["src/other.py"]
    assert env.roots_dates("trials") == []
    env.g("reset", "-q"); (env.work / "src/other.py").unlink()
    (env.work / ".git/CHERRY_PICK_HEAD").write_text("x")
    assert int(env.run().exit_code) == 3
    (env.work / ".git/CHERRY_PICK_HEAD").unlink()
    assert int(env.run().exit_code) == 0

def test_P5_hang_when_writer_holds_lock(env):
    env.setup_three_days(); env.manifest("a")
    fd = os.open(env.work / "data/manifests/a.jsonl.lock", os.O_RDWR | os.O_CREAT)
    fcntl.flock(fd, fcntl.LOCK_EX)
    res = {}
    t = threading.Thread(target=lambda: res.setdefault("r", env.run()), daemon=True); t.start()
    t.join(4)
    print("STILL RUNNING after 4s:", t.is_alive())
    fcntl.flock(fd, fcntl.LOCK_UN); t.join(10)
    assert "r" in res

def test_P6_hook_adds_file_after_check(env):
    env.setup_three_days()
    hook = env.work / ".git/hooks/pre-commit"
    hook.write_text("#!/bin/sh\necho SECRET > leaked.txt\ngit add leaked.txt\n"); hook.chmod(0o755)
    r = env.run()
    print(r.exit_code, committed(env))
    print("LEAKED" if "leaked.txt" in committed(env) else "clean")

def test_P7_ignored_manifest_and_untracked_gitignored_decisions(env):
    env.setup_three_days()
    env.write("data/manifests/ignored.jsonl", "x\n")   # ignored by .gitignore
    r = env.run()
    assert "data/manifests/ignored.jsonl" not in committed(env)

def test_P8_repeat_and_state_not_staged(env):
    env.setup_three_days()
    assert int(env.run().exit_code) == 0
    n = env.count()
    assert int(env.run().exit_code) == 0 and env.count() == n
    assert not any(".state" in f or f.endswith(".lock") for f in committed(env))

def test_P9_empty_repo_unborn_head(tmp_path, logbuf):
    pass
