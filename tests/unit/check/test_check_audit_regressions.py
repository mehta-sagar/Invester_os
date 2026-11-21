"""S0-FIX A1: a raw file replaced by (or accompanied by) a symlink must fail `investos check raw`/all."""
from __future__ import annotations

import hashlib
import os

from _chk_helpers import put, raw_path


def test_A1_raw_file_replaced_by_symlink_to_writable_outside_file_fails(healthy, run, tmp_path):
    p = raw_path(healthy, "bse", b"alpha-bytes")
    outside = tmp_path / "outside-copy"
    outside.write_bytes(b"alpha-bytes")
    os.chmod(outside, 0o666)
    os.chmod(p, 0o644)
    os.unlink(p)
    os.symlink(outside, p)
    for argv in (["raw"], ["all"]):
        code, out = run(argv)
        assert code != 0, f"{argv}: symlinked raw file passed:\n{out}"
        assert "symlink" in out, out


def test_A1_extra_sha_named_symlink_in_raw_tree_fails(healthy, run, tmp_path):
    outside = tmp_path / "outside.txt"
    outside.write_bytes(b"evil")
    sha = hashlib.sha256(b"evil").hexdigest()
    d = healthy.zones["raw"] / "bse" / sha[:2]
    d.mkdir(exist_ok=True)
    os.symlink(outside, d / sha)
    code, out = run(["raw"])
    assert code != 0 and "symlink" in out, out


def test_A1_guard_clean_store_still_passes(healthy, run):
    code, out = run(["raw"])
    assert code == 0, out
