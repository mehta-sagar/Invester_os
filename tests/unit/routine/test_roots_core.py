"""S1-T3 A1, A2, A3, A4, A15, A16, A20(access part): roots written, add-list, commit format, plain push."""
from __future__ import annotations

import json
import re
import os

import pytest

from _routine_helpers import D1, D2, D3, TODAY, sh

IST_TODAY = "2026-11-20"


def _ec():
    from investos.routine.roots import ExitCode
    return ExitCode


# ---------- A1 / R1 / R3 ----------

def test_A1_roots_for_closed_days_only_one_commit_pushed(env):
    env.setup_three_days()
    before = env.count()
    r = env.run()
    assert r.exit_code == _ec().OK == 0
    assert env.roots_dates("trials") == [D1, D2]
    assert env.roots_dates("decisions") == [D1, D2]
    assert dict(r.roots_written) == {"trials": [D1, D2], "decisions": [D1, D2]}
    assert env.count() == before + 1
    assert r.committed == env.head()
    assert r.pushed is True
    assert env.remote_head() == env.head()
    t, d = env.ledgers()
    assert t.verify(roots=True).ok and d.verify(roots=True).ok


def test_A1_commit_message_format_and_author_from_user_config(env):
    env.setup_three_days()
    env.run()
    msg = env.g("log", "-1", "--format=%B").stdout.strip()
    assert msg == f"routine: roots and ledgers {IST_TODAY} (run T3-RUN-1)"
    assert env.g("log", "-1", "--format=%an <%ae>").stdout.strip() == "Sagar Test <sagar@example.invalid>"
    assert "Co-Authored-By" not in env.g("log", "-1", "--format=%B").stdout


def test_A1_ist_date_in_message_uses_clock_ist_not_utc(env):
    env.setup_three_days()
    env.clock.set("2026-11-20T19:00:00")            # IST 2026-11-21 00:30
    env.add_rows(D3)                                  # a row on D3 so D3 closes at this clock
    env.clock.set("2026-11-20T19:00:00")
    env.run()
    assert env.g("log", "-1", "--format=%s").stdout.startswith("routine: roots and ledgers 2026-11-21 (run ")
    assert env.roots_dates("trials") == [D1, D2, D3]


def test_A1_stdout_json_via_cli(env, capsys, monkeypatch):
    """Rows on past days so the real clock closes them; canonical key order."""
    for d in ("2020-01-01", "2020-01-02"):
        env.add_rows(d)
    monkeypatch.chdir(env.work)
    from investos import cli
    code = cli.main(["routine", "roots"])
    out = capsys.readouterr().out.strip().splitlines()
    line = [x for x in out if x.startswith("{")][-1]
    obj = json.loads(line)
    assert code == 0 and obj["exit"] == 0
    assert list(obj) == sorted(obj) == ["added", "branch", "committed", "exit", "pushed", "roots"]
    assert obj["branch"] == "main" and obj["pushed"] is True and obj["committed"] == env.head()
    assert obj["roots"] == {"decisions": ["2020-01-01", "2020-01-02"], "trials": ["2020-01-01", "2020-01-02"]}
    assert obj["added"] == 4
    assert line == json.dumps(obj, sort_keys=True, separators=(",", ":"))


# ---------- A15 / R3 ----------

def test_A15_five_missing_days_rooted_in_order_one_commit(env):
    days = ["2026-11-10", "2026-11-11", "2026-11-12", "2026-11-13", "2026-11-14"]
    for d in days:
        env.add_rows(d)
    env.clock.set("2026-11-16T10:00:00")
    before = env.count()
    r = env.run()
    assert r.exit_code == 0
    assert env.roots_dates("trials") == days and env.roots_dates("decisions") == days
    assert env.count() == before + 1


def test_A15_open_day_rooted_next_run_after_close(env):
    env.setup_three_days()
    env.run()
    assert D3 not in env.roots_dates("trials")
    env.clock.set("2026-11-21T10:00:00")
    r = env.run()
    assert r.exit_code == 0 and env.roots_dates("trials")[-1] == D3 and dict(r.roots_written)["trials"] == [D3]


# ---------- A16 ----------

def test_A16_empty_layout_ok_no_commit(env):
    before = env.count()
    r = env.run()
    assert r.exit_code == 0 and r.committed is None and r.pushed is False
    assert env.count() == before
    assert not (env.work / "registry" / "trials.jsonl").exists()
    assert not (env.work / "registry" / "trials.roots.jsonl").exists()


def test_A16_first_run_manifests_only_committed(env):
    env.manifest("a")
    r = env.run()
    assert r.exit_code == 0 and env.files_in() == ["data/manifests/a.jsonl"]


# ---------- A2 / R2 ----------

def _edit_first_line(path, fn):
    lines = path.read_bytes().split(b"\n")
    obj = json.loads(lines[0])
    fn(obj)
    lines[0] = json.dumps(obj, sort_keys=True, separators=(",", ":")).encode()
    path.write_bytes(b"\n".join(lines))


@pytest.mark.parametrize("which", ["trials", "decisions"])
def test_A2_chain_break_nothing_written_staged_or_committed(env, which):
    env.setup_three_days()
    env.run()
    env.add_rows("2026-11-21")
    env.clock.set("2026-11-23T10:00:00")
    p = env.work / ("registry/trials.jsonl" if which == "trials" else "data/ledger/decisions.jsonl")
    _edit_first_line(p, lambda o: o["payload"].update({"hypothesis": "forged"} if which == "trials"
                                                       else {"security_id": "FORGED"}))
    roots_before = {k: env.roots_dates(k) for k in ("trials", "decisions")}
    head, rhead = env.head(), env.remote_head()
    r = env.run()
    assert r.exit_code == _ec().VERIFY_FAILED == 2
    assert {k: env.roots_dates(k) for k in ("trials", "decisions")} == roots_before
    assert env.staged() == [] and env.head() == head and env.remote_head() == rhead
    assert which in r.message and ("seq" in r.message or "first_broken" in r.message)


def test_A2_wrong_root_hash_exit_2(env):
    env.setup_three_days()
    env.run()
    env.clock.set("2026-11-23T10:00:00")
    env.add_rows("2026-11-21")
    env.clock.set("2026-11-23T10:00:00")
    rp = env.work / "registry" / "trials.roots.jsonl"
    _edit_first_line(rp, lambda o: o.update(hash="0" * 64))
    head = env.head()
    r = env.run()
    assert r.exit_code == 2 and env.head() == head and env.staged() == []
    assert env.roots_dates("trials") == [D1, D2]


def test_A2_missing_ledger_is_not_a_failure(env):
    env.add_rows(D1, decisions=0)
    env.clock.set(TODAY)
    r = env.run()
    assert r.exit_code == 0 and env.roots_dates("trials") == [D1] and env.roots_dates("decisions") == []


# ---------- A3 / A4 / A20: the add-list ----------

def _plant(env):
    """Everything that must (good) and must not (bad) be staged. Returns the expected add-list."""
    env.setup_three_days()
    good = {
        "registry/trials.jsonl", "registry/trials.roots.jsonl",
        "data/ledger/decisions.jsonl", "data/ledger/decisions.roots.jsonl",
    }
    env.write("data/ledger/access/bse.jsonl", '{"state":"halt"}\n'); good.add("data/ledger/access/bse.jsonl")
    env.write("data/ledger/access/hosts/www.bseindia.com.jsonl", '{"state":"block"}\n')
    good.add("data/ledger/access/hosts/www.bseindia.com.jsonl")
    env.manifest("a"); good.add("data/manifests/a.jsonl")
    env.manifest("bse"); good.add("data/manifests/bse.jsonl")
    for name in ("2026-11-01_2026-11-02.json", "2026-11-01_2026-11-02.json.attempt-1",
                 "2026-11-03_2026-11-04.json.corrupt-1"):
        env.write(f"data/manifests/a.runs/{name}", "{}\n"); good.add(f"data/manifests/a.runs/{name}")
    bad = [
        "data/raw/bse/ab/" + "a" * 64, "data/staged/x", "data/ledger/.state/ratelimit/h.last",
        "data/manifests/a.jsonl.lock", "data/manifests/a.runs/.tmp-1", "reports/checks/x.json", "credentials/k",
        "NOTES.txt", "data/manifests/Bad-Name.jsonl", "data/manifests/a.runs/notes.txt",
        "data/ledger/access/Bad-Host.jsonl", "data/ledger/access/.tmp-1", "data/ledger/access/hosts/Bad_Host.jsonl",
        "data/manifests/ignored.jsonl", "data/canonical/c.parquet", "data/quarantine/q.json",
        "data/exports/e.csv", "data/features/f.json", "data/models/m.bin",
    ]
    for b in bad:
        env.write(b)
    outside = env.tmp / "outside.jsonl"
    outside.write_text("secret\n")
    os.symlink(outside, env.work / "data" / "manifests" / "evil.jsonl")
    os.symlink(outside, env.work / "data" / "manifests" / "b.runs-link.jsonl")
    (env.work / "src" / "investos" / "cli.py").write_text("# modified\n")
    return sorted(good), bad


def test_A3_A4_A20_commit_holds_exactly_the_add_list(env):
    good, bad = _plant(env)
    r = env.run()
    assert r.exit_code == 0, r.message
    committed = env.files_in()
    assert committed == good
    assert r.added == good
    assert env.g("status", "--porcelain", "--", "src/investos/cli.py").stdout.strip() == "M src/investos/cli.py"
    assert env.staged() == []
    for b in bad:
        assert b not in committed
    assert "data/manifests/evil.jsonl" not in committed
    # only the add-list is tracked beyond the initial commit
    tracked = set(env.g("ls-files").stdout.split())
    assert tracked - set(good) == {".gitignore", "pyproject.toml", "config/investos.toml",
                                   "db/migrations/0000_bootstrap.sql", "src/investos/cli.py"}


def test_A4_each_add_is_one_explicit_path_and_push_is_plain(env):
    good, _ = _plant(env)
    env.run()
    adds = [c for c in env.git.commands if c and c[0] == "add"]
    assert sorted(a[-1] for a in adds) == good
    assert all(a[:2] == ["add", "--"] and len(a) == 3 for a in adds)
    pushes = [c for c in env.git.commands if c[0] == "push"]
    assert pushes == [["push", "origin", "main"]]
    flat = [x for c in env.git.commands for x in c]
    for token in ("--force", "-f", "--force-with-lease", "--no-verify", "-A", "-a", "--delete"):
        assert token not in flat
    assert not any(x.startswith("+") for x in flat)
    assert not any(c[0] in ("reset", "checkout", "clean", "stash", "pull", "fetch", "rebase", "merge", "rm")
                   for c in env.git.commands)


def test_A4_staged_unrelated_file_refused_index_unchanged(env):
    env.setup_three_days()
    env.write("NOTES.txt", "n\n")
    env.g("add", "NOTES.txt")
    head = env.head()
    r = env.run()
    assert r.exit_code == 3
    assert env.staged() == ["NOTES.txt"] and env.head() == head
    assert env.roots_dates("trials") == []


def test_A4_cached_set_not_subset_of_add_list_refuses(env):
    """Defence in depth: a stray path appearing in `git diff --cached --name-only -z` just before commit."""
    from investos.routine.roots import ExitCode
    import subprocess
    env.setup_three_days()
    env.manifest("a")

    def pred(a):
        return a[:2] == ["diff", "--cached"] and "--name-only" in a and "-z" in a

    out = subprocess.CompletedProcess(["git"], 0, stdout="NOTES.txt\0data/manifests/a.jsonl\0", stderr="")
    env.git.inject.append((pred, out))
    r = env.run()
    assert r.exit_code == ExitCode.REFUSED and r.committed is None
    assert not any(c[0] == "commit" for c in env.git.commands)


def test_A4_ignored_manifest_never_added(env):
    env.write("data/manifests/ignored.jsonl", '{"n":1}\n')
    r = env.run()
    assert r.exit_code == 0 and "data/manifests/ignored.jsonl" not in r.added
    assert not any(c[-1] == "data/manifests/ignored.jsonl" for c in env.git.commands if c[0] == "add")
