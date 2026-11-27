"""S1-T6 R12: the gap ledger (and only it) joins the roots routine's add-list."""
from __future__ import annotations

import os

from _routine_helpers import GITIGNORE  # noqa: F401


def add_list(env):
    from investos.routine import roots
    return roots.build_add_list(env.cfg, roots.Git(env.work))


def test_R12_gaps_file_is_in_the_add_list_state_and_stray_files_are_not(env):
    env.write("data/ledger/rss/gaps.jsonl", '{"schema":1}\n')
    env.write("data/ledger/.state/rss/f1.json", "{}\n")
    env.write("data/ledger/rss/other.jsonl", "x\n")
    env.write("data/ledger/rss/gaps.jsonl.tmp", "x\n")
    env.write("data/ledger/rss/gaps.jsonl.lock", "x\n")
    got = add_list(env)
    assert "data/ledger/rss/gaps.jsonl" in got
    assert not any(".state" in g or "other.jsonl" in g or g.endswith((".tmp", ".lock")) for g in got)


def test_R12_gaps_symlink_is_never_staged(env, tmp_path):
    out = tmp_path / "outside.jsonl"
    out.write_text("secret\n")
    (env.work / "data/ledger/rss").mkdir(parents=True)
    os.symlink(out, env.work / "data/ledger/rss/gaps.jsonl")
    assert "data/ledger/rss/gaps.jsonl" not in add_list(env)


def test_R12_missing_gaps_file_is_fine(env):
    assert "data/ledger/rss/gaps.jsonl" not in add_list(env)
