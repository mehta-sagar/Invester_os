"""S0-FIX A1: a raw file replaced by (or accompanied by) a symlink must fail `investos check raw`/all."""
from __future__ import annotations

import hashlib
import os

import pytest

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


# ---------------------------------------------------------------- A10: unknown names are reported
def _mk_writable(p, data=b"x"):
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_bytes(data)
    os.chmod(p, 0o666)


def test_A10_stray_names_in_raw_are_reported_misplaced(healthy, run):
    raw = healthy.zones["raw"]
    _mk_writable(raw / "XYZUPPER" / "aa" / "junk")     # upper-case source dir (no case-fold clash with any fixture source)
    _mk_writable(raw / "stray.bin")                    # file directly in the raw zone
    _mk_writable(raw / "nse-x" / "f")                  # dir that is not a valid source name
    code, out = run(["raw"])
    assert code != 0, out
    for name in ("XYZUPPER", "stray.bin", "nse-x"):
        assert name in out, f"{name} not reported:\n{out}"
    assert "misplaced_file" in out


def test_A10_bad_manifest_name_is_reported_bad_line(healthy, run):
    (healthy.zones["manifests"] / "Bad-Name.jsonl").write_bytes(b"garbage no newline")
    code, out = run(["manifest"])
    assert code != 0 and "bad_line" in out and "Bad-Name.jsonl" in out, out


def test_A10_guard_legitimate_housekeeping_names_pass(healthy, run):
    man, raw = healthy.zones["manifests"], healthy.zones["raw"]
    (raw / ".gitkeep").write_bytes(b"")
    (man / ".gitkeep").write_bytes(b"")
    (man / "bse.jsonl.lock").write_bytes(b"")
    (man / "bse.roots.jsonl").write_bytes(b"")
    (man / "bse.runs").mkdir()
    (man / "bse.runs" / "2026-11-17_2026-11-17.json").write_bytes(b"{}\n")
    (man / "bse.runs" / ".tmp-abc").write_bytes(b"")
    for target in ("raw", "manifest"):
        code, out = run([target])
        assert code == 0, f"{target}: {out}"


# ---------------------------------------------------------------- A17 survivors X13, T12
def test_X13_writable_dotfile_in_raw_tree_is_not_hidden(healthy, run):
    _mk_writable(healthy.zones["raw"] / "bse" / ".x")
    code, out = run(["raw"])
    assert code != 0 and ".x" in out, out


def test_T12_size_mismatch_alone_fails_manifest_check(healthy, run):
    import json
    p = healthy.zones["manifests"] / "bse.jsonl"
    lines = p.read_bytes().splitlines()
    obj = json.loads(lines[0])
    obj["size"] += 1                                   # hash still right, only the recorded size is wrong
    lines[0] = json.dumps(obj, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode()
    p.write_bytes(b"\n".join(lines) + b"\n")
    code, out = run(["manifest"])
    assert code != 0 and "size_mismatch" in out, out


# ---------------------------------------------------------------- A9: plants copy only what the check needs
def _copy_spy(monkeypatch):
    import shutil
    srcs = []

    def wrap(real):
        def f(src, *a, **k):
            srcs.append(os.path.realpath(src))
            return real(src, *a, **k)
        return f
    monkeypatch.setattr(shutil, "copytree", wrap(shutil.copytree))
    monkeypatch.setattr(shutil, "copy2", wrap(shutil.copy2))
    monkeypatch.setattr(shutil, "copy", wrap(shutil.copy))
    real_link = os.link

    def link(src, *a, **k):
        srcs.append(os.path.realpath(src))
        return real_link(src, *a, **k)
    monkeypatch.setattr(os, "link", link)
    return srcs


def _touched(cfg, srcs):
    """names of zones (and 'migrations') that some copy source lies in."""
    areas = {k: os.path.realpath(v) for k, v in cfg.zones.items()}
    areas["migrations"] = os.path.realpath(cfg.migrations_dir)
    hit = set()
    for s in srcs:
        for name, a in areas.items():
            if s == a or s.startswith(a + os.sep):
                hit.add(name)
    return hit


@pytest.mark.parametrize("name,allowed", [
    ("raw", {"raw", "manifests"}),
    ("manifest", {"raw", "manifests"}),
    ("migrations", {"canonical", "migrations"}),
])
def test_A9_builtin_plant_copies_only_needed_zones(healthy, monkeypatch, name, allowed):
    import investos.check.builtin  # noqa: F401
    from investos.check.registry import DEFAULT_REGISTRY
    from investos.check.runner import run_planted
    for z in ("canonical", "ledger", "reports", "registry"):          # something to copy in every zone
        (healthy.zones[z] / "marker.txt").write_text("m")
    srcs = _copy_spy(monkeypatch)
    rep = run_planted(DEFAULT_REGISTRY, healthy, [name])
    assert [r.status for r in rep.results] == ["caught"], rep.results
    extra = _touched(healthy, srcs) - allowed
    assert not extra, f"plant for {name!r} copied unneeded zones: {sorted(extra)}"


def test_A9_registry_plant_copies_only_its_ledgers(healthy, monkeypatch):
    import investos.registry.check  # noqa: F401
    from investos.check.registry import DEFAULT_REGISTRY
    from investos.check.runner import run_planted
    srcs = _copy_spy(monkeypatch)
    rep = run_planted(DEFAULT_REGISTRY, healthy, ["registry"])
    assert [r.status for r in rep.results] == ["caught"], rep.results
    extra = _touched(healthy, srcs) - {"registry", "ledger"}
    assert not extra, f"registry plant copied unneeded zones: {sorted(extra)}"
