"""S0-T3 acceptance tests A11-A19 for lookups, strict reading, verify and path safety
(rules R7-R11, R13). Spec: specs/core_manifest.md."""
from __future__ import annotations

import json
import os
import re
import shutil
from datetime import datetime, timezone

import pytest

from _acq_helpers import (HELLO_SHA, canon, for_path, kinds, lines_of, manifest_path, put, raw_file,
                          sha, snap, tamper)

LOOKUPS = [
    ("get_by_key", lambda st: st.get_by_key("bse", "2026-11-20")),
    ("get_all_by_key", lambda st: st.get_all_by_key("bse", "2026-11-20")),
    ("get_by_sha", lambda st: st.get_by_sha("bse", HELLO_SHA)),
    ("entries", lambda st: st.entries("bse")),
]


@pytest.mark.planted
def test_A11_truncated_last_line(mk, roots):
    raw, man = roots
    st = mk()
    put(st, "bse", b"one", logical_key="k1")
    put(st, "bse", b"two", logical_key="k2")
    mp = manifest_path(man, "bse")
    mp.write_bytes(mp.read_bytes()[:-12])               # drop the "\n" and part of the JSON
    before = mp.read_bytes()
    assert not before.endswith(b"\n")
    from investos.acquire.manifest import ManifestCorruptError
    with pytest.raises(ManifestCorruptError):
        put(st, "bse", b"three", logical_key="k3")
    assert mp.read_bytes() == before
    probs = st.verify("bse")
    bad = [p for p in probs if p.kind == "bad_line"]
    assert [p.line for p in bad] == [2]


VARIANTS = ["invalid_json", "json_array", "missing_run_id", "extra_field", "size_as_string",
            "sha_uppercase", "fetched_at_no_Z", "blank_line"]


def _bad_variants(good: dict):
    def without(k):
        d = dict(good)
        d.pop(k)
        return canon(d)
    return {
        "invalid_json": "{not json",
        "json_array": "[1,2,3]",
        "missing_run_id": without("run_id"),
        "extra_field": canon({**good, "foo": "bar"}),
        "size_as_string": canon({**good, "size": "5"}),
        "sha_uppercase": canon({**good, "sha256": good["sha256"].upper()}),
        "fetched_at_no_Z": canon({**good, "fetched_at": good["fetched_at"].rstrip("Z")}),
        "blank_line": "",
    }


@pytest.mark.planted
@pytest.mark.parametrize("variant", VARIANTS)
def test_A12_strict_reading_names_line(mk, roots, variant):
    raw, man = roots
    st = mk()
    put(st, "bse", b"hello", logical_key="2026-11-20")
    put(st, "bse", b"world", logical_key="2026-11-21")
    mp = manifest_path(man, "bse")
    l1, l3 = lines_of(mp)
    bad = _bad_variants(json.loads(l1))[variant]
    mp.write_bytes((l1 + bad + "\n" + l3).encode("utf-8"))
    os.unlink(raw_file(raw, "bse", b"world"))              # line 3 has a problem verify must still find
    from investos.acquire.manifest import ManifestCorruptError
    for name, call in LOOKUPS:
        with pytest.raises(ManifestCorruptError) as ei:
            call(st)
        msg = str(ei.value)
        assert "bse.jsonl" in msg, f"{name}: file not named: {msg}"
        assert re.search(r"(?<![0-9a-f])2(?![0-9a-f])", msg), f"{name}: line number 2 not named: {msg}"
    probs = st.verify("bse")                                 # must not raise
    assert [(p.kind, p.line) for p in probs if p.kind == "bad_line"] == [("bad_line", 2)]
    assert any(p.kind == "missing_file" and p.line == 3 for p in probs), "lines after a bad line not checked"


def test_A13_no_manifest_is_empty_and_creates_nothing(mk, roots, tmp_path):
    raw, man = roots
    st = mk()
    before = snap(tmp_path)
    assert st.get_by_key("bse", "2026-11-20") is None
    assert st.get_all_by_key("bse", "2026-11-20") == []
    assert st.get_by_sha("bse", HELLO_SHA) == []
    assert st.entries("bse") == []
    assert snap(tmp_path) == before
    assert list(man.iterdir()) == []


def test_A14_lookup_latest_and_all(mk, roots):
    raw, man = roots
    st = mk()
    put(st, "bse", b"v1", logical_key="2026-11-20", run_id="r1")
    put(st, "bse", b"other", logical_key="2026-11-19", run_id="r2")
    put(st, "bse", b"v2", logical_key="2026-11-20", run_id="r3")
    assert st.get_by_key("bse", "2026-11-20").run_id == "r3"
    assert [e.run_id for e in st.get_all_by_key("bse", "2026-11-20")] == ["r1", "r3"]
    assert st.get_by_key("bse", "nope") is None
    assert [e.run_id for e in st.entries("bse")] == ["r1", "r2", "r3"]
    put(st, "nse", b"v1", logical_key="2026-11-20", run_id="n1")      # R9: source-scoped
    assert [e.run_id for e in st.get_by_sha("bse", sha(b"v1"))] == ["r1"]
    assert st.get_by_sha("bse", sha(b"zzz")) == []


def _healthy3(mk, raw):
    st = mk()
    datas = [b"alpha-0001", b"beta-0002", b"gamma-0003"]
    for i, d in enumerate(datas):
        put(st, "bse", d, logical_key=f"k{i}")
    return st, [raw_file(raw, "bse", d) for d in datas]


@pytest.mark.planted
def test_A15_verify_detects_tampered_and_truncated(mk, roots):
    raw, man = roots
    st, paths = _healthy3(mk, raw)
    assert st.verify() == []
    tamper(paths[0], b"X" * paths[0].stat().st_size)         # same size, mode restored to 0444
    probs = st.verify("bse")
    assert [p.kind for p in for_path(probs, paths[0])] == ["hash_mismatch"]
    assert [p for p in probs if Path_name(p) != paths[0].name] == []
    tamper(paths[1], paths[1].read_bytes()[:3])              # truncated
    probs = st.verify("bse")
    assert kinds(for_path(probs, paths[1])) == {"hash_mismatch", "size_mismatch"}
    assert kinds(for_path(probs, paths[2])) == set()


def Path_name(p):
    from pathlib import Path
    return Path(p.path).name


@pytest.mark.planted
def test_A16_verify_missing_writable_misplaced(mk, roots, tmp_path):
    # missing file: one problem per referencing manifest line
    raw, man = roots
    st = mk()
    put(st, "bse", b"hello", run_id="r1")
    put(st, "bse", b"hello", run_id="r2")
    os.unlink(raw_file(raw, "bse", b"hello"))
    miss = [p for p in st.verify("bse") if p.kind == "missing_file"]
    assert sorted(p.line for p in miss) == [1, 2]
    assert all(Path_name(p) == HELLO_SHA and p.source == "bse" for p in miss)

    # writable file
    st2 = mk(tmp_path / "w")
    put(st2, "bse", b"hello")
    f = tmp_path / "w" / "raw" / "bse" / "2c" / HELLO_SHA
    os.chmod(f, 0o644)
    pr = st2.verify("bse")
    assert kinds(for_path(pr, f)) == {"writable_file"}

    # misplaced: correct bytes in the wrong directory; and a stray name
    st3 = mk(tmp_path / "m")
    put(st3, "bse", b"hello")
    base = tmp_path / "m" / "raw" / "bse"
    (base / "zz").mkdir()
    wrong = base / "zz" / HELLO_SHA
    shutil.copyfile(base / "2c" / HELLO_SHA, wrong)
    os.chmod(wrong, 0o444)
    stray = base / "2c" / "foo.txt"
    stray.write_bytes(b"junk")
    os.chmod(stray, 0o444)
    pr = st3.verify("bse")
    assert "misplaced_file" in kinds([p for p in pr if p.path.endswith(f"zz/{HELLO_SHA}")])
    assert "misplaced_file" in kinds([p for p in pr if p.path.endswith("foo.txt")])
    assert kinds(for_path(pr, base / "2c" / HELLO_SHA)) == set()
    # each (kind, path, line) reported once
    keys = [(p.kind, p.path, p.line) for p in pr]
    assert len(keys) == len(set(keys))


def test_A17_verify_healthy_readonly_and_scoped(mk, roots, tmp_path):
    raw, man = roots
    st = mk()
    put(st, "a", b"aaa")
    put(st, "b", b"bbb")
    before = snap(tmp_path)
    assert st.verify() == [] and st.verify("a") == [] and st.verify("b") == []
    assert snap(tmp_path) == before                        # no mtime/mode change, nothing created
    tamper(raw_file(raw, "a", b"aaa"), b"xxx")
    tamper(raw_file(raw, "b", b"bbb"), b"yyy")
    only_a = st.verify("a")
    assert only_a and {p.source for p in only_a} == {"a"}
    both = st.verify()
    assert {p.source for p in both} == {"a", "b"}


@pytest.mark.planted
def test_A18_read_bytes_reverifies(mk, roots):
    raw, man = roots
    st = mk()
    put(st, "bse", b"hello")
    assert st.read_bytes("bse", HELLO_SHA) == b"hello"
    tamper(raw_file(raw, "bse", b"hello"), b"hellO")
    from investos.acquire.manifest import RawIntegrityError
    with pytest.raises(RawIntegrityError):
        st.read_bytes("bse", HELLO_SHA)


BAD_SOURCES = ["", "..", "a/b", "A", "x" * 65, "-a"]


@pytest.mark.planted
@pytest.mark.parametrize("source", BAD_SOURCES)
def test_A19_invalid_source_rejected_first(mk, roots, tmp_path, source):
    st = mk()
    before = snap(tmp_path)
    calls = [
        lambda: put(st, source, b"hello"),
        lambda: st.get_by_key(source, "k"),
        lambda: st.get_all_by_key(source, "k"),
        lambda: st.get_by_sha(source, HELLO_SHA),
        lambda: st.entries(source),
        lambda: st.verify(source),
        lambda: st.raw_path(source, HELLO_SHA),
        lambda: st.read_bytes(source, HELLO_SHA),
    ]
    for c in calls:
        with pytest.raises(ValueError):
            c()
    assert snap(tmp_path) == before


def test_A19b_valid_source_boundary(mk, roots):
    st = mk()
    put(st, "x" * 64, b"hello")                 # 64 chars allowed
    put(st, "0_a", b"hello")                    # digit start, underscore allowed
    assert st.entries("x" * 64) and st.entries("0_a")


@pytest.mark.planted
@pytest.mark.parametrize("bad_sha", ["../../etc", HELLO_SHA.upper(), HELLO_SHA[:63], HELLO_SHA + "0", "", "g" * 64])
def test_A19c_invalid_sha_rejected(mk, roots, tmp_path, bad_sha):
    st = mk()
    before = snap(tmp_path)
    for c in (lambda: st.raw_path("bse", bad_sha), lambda: st.read_bytes("bse", bad_sha),
              lambda: st.get_by_sha("bse", bad_sha)):
        with pytest.raises(ValueError):
            c()
    assert snap(tmp_path) == before
