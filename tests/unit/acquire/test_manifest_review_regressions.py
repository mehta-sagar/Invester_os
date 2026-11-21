"""S0-T3 review regressions (reports/S0-T3/review.md): D1 symlinks, D2 non-regular dest, D3 directory
fsync, T1-T6 test gaps. Added after the first green run; the new ones are expected red until fixed."""
from __future__ import annotations

import json
import fcntl
import os
import stat
from pathlib import Path

import pytest

from _acq_helpers import (HELLO_SHA, kinds, lines_of, manifest_path, put, raw_file, sha, snap, tamper)

LOOKUPS = [
    lambda st: st.get_by_key("bse", "2026-11-20"),
    lambda st: st.get_all_by_key("bse", "2026-11-20"),
    lambda st: st.get_by_sha("bse", HELLO_SHA),
    lambda st: st.entries("bse"),
]


def _no_new_manifest_line(man, before_bytes):
    mp = manifest_path(man, "bse")
    got = mp.read_bytes() if mp.exists() else b""
    assert got == before_bytes


# ---------------------------------------------------------------- D1 symlinks

@pytest.mark.planted
def test_D1a_symlink_at_dest_with_correct_bytes_rejected(mk, roots, tmp_path):
    raw, man = roots
    st = mk()
    put(st, "bse", b"other")                     # so manifest exists
    before = manifest_path(man, "bse").read_bytes()
    target = tmp_path / "elsewhere"
    target.write_bytes(b"hello")                 # correct bytes, but outside the store
    dest = raw_file(raw, "bse", b"hello")
    dest.parent.mkdir(parents=True)
    dest.symlink_to(target)
    from investos.acquire.manifest import RawIntegrityError
    with pytest.raises(RawIntegrityError):
        put(st, "bse", b"hello")
    assert dest.is_symlink() and target.read_bytes() == b"hello"
    _no_new_manifest_line(man, before)


@pytest.mark.planted
def test_D1b_verify_reports_symlinked_raw_file_and_tampering_behind_it(mk, roots, tmp_path):
    raw, man = roots
    st = mk()
    put(st, "bse", b"hello")
    f = raw_file(raw, "bse", b"hello")
    evil = tmp_path / "evil"
    evil.write_bytes(b"tampered!")
    os.chmod(f, 0o644)
    f.unlink()
    f.symlink_to(evil)
    probs = st.verify("bse")                     # must not raise
    mine = [p for p in probs if Path(p.path).name == HELLO_SHA]
    assert "symlink" in kinds(mine)
    assert "hash_mismatch" in kinds(mine), "manifest-referenced file behind the link was not hashed"


@pytest.mark.planted
def test_D1c_verify_reports_symlinked_xx_directory(mk, roots, tmp_path):
    raw, man = roots
    st = mk()
    put(st, "bse", b"hello")
    xx = raw / "bse" / HELLO_SHA[:2]
    moved = tmp_path / "moved_xx"
    xx.rename(moved)
    xx.symlink_to(moved, target_is_directory=True)
    tamper(moved / HELLO_SHA, b"tampered!")
    probs = st.verify("bse")
    assert "symlink" in kinds(probs)
    assert "hash_mismatch" in kinds(probs), "file behind a symlinked directory was never hashed"


@pytest.mark.planted
def test_D1d_verify_hashes_manifest_referenced_files(mk, roots):
    # healthy raw tree walk finds the problem too, but removing the file from the walk's view
    # (symlinked dir) must not hide a size/hash problem in a manifest-referenced file
    raw, man = roots
    st = mk()
    put(st, "bse", b"hello")
    f = raw_file(raw, "bse", b"hello")
    tamper(f, b"hellO")                           # same size, different hash
    probs = st.verify("bse")
    assert any(p.kind == "hash_mismatch" and p.line == 1 for p in probs), \
        "hash_mismatch should be attributed to the manifest line that references the file"


# ---------------------------------------------------------------- D2 non-regular dest

@pytest.mark.planted
def test_D2a_dangling_symlink_at_dest(mk, roots, tmp_path):
    raw, man = roots
    st = mk()
    dest = raw_file(raw, "bse", b"hello")
    dest.parent.mkdir(parents=True)
    dest.symlink_to(tmp_path / "does-not-exist")
    from investos.acquire.manifest import RawIntegrityError
    with pytest.raises(RawIntegrityError):
        put(st, "bse", b"hello")
    assert dest.is_symlink()
    _no_new_manifest_line(man, b"")


@pytest.mark.planted
def test_D2b_directory_at_dest(mk, roots):
    raw, man = roots
    st = mk()
    dest = raw_file(raw, "bse", b"hello")
    dest.mkdir(parents=True)
    from investos.acquire.manifest import RawIntegrityError
    with pytest.raises(RawIntegrityError):
        put(st, "bse", b"hello")
    assert dest.is_dir()
    _no_new_manifest_line(man, b"")


# ---------------------------------------------------------------- D3 / T4 fsync spying

HAVE_FD_PATH = hasattr(fcntl, "F_GETPATH") or os.path.isdir("/proc/self/fd")
needs_fd_path = pytest.mark.skipif(
    not HAVE_FD_PATH, reason="FsyncSpy needs fcntl.F_GETPATH (macOS) or /proc/self/fd (Linux) to map fds to paths")


def fd_path(fd: int) -> str:
    """fd -> path: fcntl.F_GETPATH (macOS), else /proc/self/fd (Linux), else 'fd:N'."""
    if hasattr(fcntl, "F_GETPATH"):
        try:
            return os.fsdecode(fcntl.fcntl(fd, fcntl.F_GETPATH, b"\0" * 1024).split(b"\0", 1)[0])
        except OSError:
            pass
    try:
        return os.readlink(f"/proc/self/fd/{fd}")
    except OSError:
        return f"fd:{fd}"


class FsyncSpy:
    def __init__(self, monkeypatch):
        self.real_fsync, self.real_link = os.fsync, os.link
        self.events = []                         # ("fsync", path) / ("link", dst)
        monkeypatch.setattr(os, "fsync", self._fsync)
        monkeypatch.setattr(os, "link", self._link)

    def _fsync(self, fd):
        self.events.append(("fsync", fd_path(fd)))
        return self.real_fsync(fd)

    def _link(self, src, dst, *a, **k):
        self.events.append(("link", str(dst)))
        return self.real_link(src, dst, *a, **k)

    def fsynced(self):
        return [p for k, p in self.events if k == "fsync"]


def _real(p: Path) -> str:
    return os.path.realpath(p)


@pytest.mark.planted
@needs_fd_path
def test_D3a_new_entries_fsync_parent_dirs(mk, roots, monkeypatch):
    raw, man = roots
    st = mk()
    spy = FsyncSpy(monkeypatch)
    put(st, "bse", b"hello")
    monkeypatch.undo()
    synced = {_real(Path(p)) for p in spy.fsynced() if p.startswith("/")}
    assert _real(raw / "bse") in synced, "raw/<source>/ not fsynced after creating the xx dir"
    assert _real(raw / "bse" / HELLO_SHA[:2]) in synced, "xx dir not fsynced after link"
    assert _real(man) in synced, "manifests dir not fsynced after creating the manifest file"
    assert _real(manifest_path(man, "bse")) in synced, "manifest append not fsynced"


@pytest.mark.planted
@needs_fd_path
def test_D3b_dir_fsync_follows_link_and_manifest_fsync_last(mk, roots, monkeypatch):
    raw, man = roots
    st = mk()
    spy = FsyncSpy(monkeypatch)
    put(st, "bse", b"hello")
    monkeypatch.undo()
    xx = _real(raw / "bse" / HELLO_SHA[:2])
    ev = [(k, _real(Path(p)) if p.startswith("/") else p) for k, p in spy.events]
    link_i = next(i for i, (k, _) in enumerate(ev) if k == "link")
    assert ("fsync", xx) in ev[link_i:], "xx dir must be fsynced after os.link"
    mf = _real(manifest_path(man, "bse"))
    assert ev.index(("fsync", mf)) > link_i, "manifest fsync must come after the raw file is in place"


@pytest.mark.planted
@needs_fd_path
def test_D3c_dedup_path_fsyncs_dir_and_manifest(mk, roots, monkeypatch):
    raw, man = roots
    st = mk()
    put(st, "bse", b"hello")
    spy = FsyncSpy(monkeypatch)
    put(st, "bse", b"hello", run_id="r2")
    monkeypatch.undo()
    synced = {_real(Path(p)) for p in spy.fsynced() if p.startswith("/")}
    assert not [e for e in spy.events if e[0] == "link"], "dedup must not link"
    assert _real(raw / "bse" / HELLO_SHA[:2]) in synced, "dedup path did not fsync the xx dir"
    assert _real(manifest_path(man, "bse")) in synced


# ---------------------------------------------------------------- T1 verify never mutates

@pytest.mark.planted
def test_T1_verify_does_not_mutate_bad_tree(mk, roots, tmp_path):
    raw, man = roots
    st = mk()
    put(st, "bse", b"hello")
    put(st, "bse", b"world")
    f1, f2 = raw_file(raw, "bse", b"hello"), raw_file(raw, "bse", b"world")
    os.chmod(f1, 0o666)                           # writable
    tamper(f2, b"WORLD", mode=0o644)              # tampered and writable
    (raw / "bse" / "ab").mkdir()
    (raw / "bse" / "ab" / "foo.txt").write_bytes(b"x")
    mp = manifest_path(man, "bse")
    mp.write_bytes(mp.read_bytes() + b"{truncated")
    before = snap(tmp_path)
    assert st.verify("bse") and st.verify()
    assert snap(tmp_path) == before
    assert stat.S_IMODE(f1.stat().st_mode) == 0o666 and stat.S_IMODE(f2.stat().st_mode) == 0o644


# ---------------------------------------------------------------- T2 / T3 dedup & race

@pytest.mark.planted
def test_T2_dedup_on_writable_existing_file_raises_and_does_not_chmod(mk, roots):
    raw, man = roots
    st = mk()
    put(st, "bse", b"hello")
    f = raw_file(raw, "bse", b"hello")
    os.chmod(f, 0o644)
    before = manifest_path(man, "bse").read_bytes()
    mtime = f.stat().st_mtime_ns
    from investos.acquire.manifest import RawIntegrityError
    with pytest.raises(RawIntegrityError):
        put(st, "bse", b"hello", run_id="r2")
    assert stat.S_IMODE(f.stat().st_mode) == 0o644, "store changed the mode of a pre-existing file"
    assert f.stat().st_mtime_ns == mtime and f.read_bytes() == b"hello"
    _no_new_manifest_line(man, before)


@pytest.mark.planted
def test_T3_link_race_with_different_bytes_raises(mk, roots, monkeypatch):
    raw, man = roots
    st = mk()
    dest = raw_file(raw, "bse", b"hello")

    def racing_link(src, dst, *a, **k):
        Path(dst).parent.mkdir(parents=True, exist_ok=True)
        Path(dst).write_bytes(b"HELLO-different")
        os.chmod(dst, 0o444)
        raise FileExistsError(17, "File exists", str(dst))
    monkeypatch.setattr(os, "link", racing_link)
    from investos.acquire.manifest import RawIntegrityError
    with pytest.raises(RawIntegrityError):
        put(st, "bse", b"hello")
    monkeypatch.undo()
    assert dest.read_bytes() == b"HELLO-different"
    assert [p for p in dest.parent.iterdir() if p.name.startswith(".tmp-")] == []
    _no_new_manifest_line(man, b"")


# ---------------------------------------------------------------- T5 / T6 strict reading

@pytest.mark.planted
def test_T5_valid_json_last_line_without_newline(mk, roots):
    raw, man = roots
    st = mk()
    put(st, "bse", b"hello", logical_key="2026-11-20")
    mp = manifest_path(man, "bse")
    mp.write_bytes(mp.read_bytes().rstrip(b"\n"))        # complete JSON object, no "\n"
    from investos.acquire.manifest import ManifestCorruptError
    for call in LOOKUPS:
        with pytest.raises(ManifestCorruptError):
            call(st)
    assert [(p.kind, p.line) for p in st.verify("bse") if p.kind == "bad_line"] == [("bad_line", 1)]
    with pytest.raises(ManifestCorruptError):
        put(st, "bse", b"again")


@pytest.mark.planted
@pytest.mark.parametrize("field,value", [("size", True), ("size", 5.0), ("size", -1)])
def test_T6_bad_size_types_rejected(mk, roots, field, value):
    raw, man = roots
    st = mk()
    put(st, "bse", b"hello")
    mp = manifest_path(man, "bse")
    obj = json.loads(lines_of(mp)[0])
    obj[field] = value
    mp.write_bytes((json.dumps(obj, sort_keys=True, separators=(",", ":")) + "\n").encode())
    from investos.acquire.manifest import ManifestCorruptError
    for call in LOOKUPS:
        with pytest.raises(ManifestCorruptError):
            call(st)
    assert [p.line for p in st.verify("bse") if p.kind == "bad_line"] == [1]


@pytest.mark.planted
@pytest.mark.parametrize("data", ["hello", None, 5, ["h"]])
def test_T7_non_bytes_data_is_typeerror(mk, roots, data):
    raw, man = roots
    st = mk()
    before = snap(roots[0].parent)
    with pytest.raises(TypeError):
        put(st, "bse", data)
    assert snap(roots[0].parent) == before


def _spy_on_pipe_fsync(monkeypatch):
    spy = FsyncSpy(monkeypatch)
    r, w = os.pipe()
    try:
        try:
            spy._fsync(w)
        except OSError:
            pass                                    # a pipe cannot be fsynced; only the recorded event matters
    finally:
        monkeypatch.undo()
        os.close(r)
        os.close(w)
    return spy, w


def test_A5_fsync_spy_falls_back_when_both_mechanisms_missing(monkeypatch):
    """Guard: without F_GETPATH and /proc the spy records 'fd:N' and never raises; the D3 tests skip then."""
    monkeypatch.delattr(fcntl, "F_GETPATH", raising=False)
    real = os.readlink
    monkeypatch.setattr(os, "readlink", lambda p, *a, **k: (_ for _ in ()).throw(OSError("no /proc"))
                        if str(p).startswith("/proc/") else real(p, *a, **k))
    spy, w = _spy_on_pipe_fsync(monkeypatch)
    assert spy.fsynced() == [f"fd:{w}"]


def test_A5_fsync_spy_uses_f_getpath_when_available(monkeypatch):
    """Simulates macOS: F_GETPATH present, /proc absent."""
    monkeypatch.setattr(fcntl, "F_GETPATH", 50, raising=False)
    real_fcntl = fcntl.fcntl
    monkeypatch.setattr(fcntl, "fcntl", lambda fd, cmd, arg=0: b"/mac/path/file\0\0\0" if cmd == 50
                        else real_fcntl(fd, cmd, arg))
    real = os.readlink
    monkeypatch.setattr(os, "readlink", lambda p, *a, **k: (_ for _ in ()).throw(OSError("no /proc"))
                        if str(p).startswith("/proc/") else real(p, *a, **k))
    spy, _ = _spy_on_pipe_fsync(monkeypatch)
    assert spy.fsynced() == ["/mac/path/file"]
