"""S0-T3 acceptance tests for the write path of investos.acquire.manifest
(A1-A8, A20-A23 + extras A3b, A24). Spec: specs/core_manifest.md (rules R1-R5, R12, R13)."""
from __future__ import annotations

import json
import os
import shutil
import stat
from datetime import datetime, timezone
from pathlib import Path

import pytest

from _acq_helpers import (EMPTY_SHA, FETCHED, FIELDS, HELLO_SHA, canon, lines_of, manifest_path,
                          put, raw_file, raw_files, sha, snap, tamper)


def test_A1_store_content_addressed_readonly(mk, roots):
    raw, man = roots
    st = mk()
    put(st, "bse", b"hello")
    p = raw / "bse" / "2c" / HELLO_SHA
    assert p.is_file() and p.read_bytes() == b"hello"
    assert stat.S_IMODE(p.stat().st_mode) == 0o444
    import hashlib
    assert hashlib.sha256(st.read_bytes("bse", HELLO_SHA)).hexdigest() == p.name
    assert st.raw_path("bse", HELLO_SHA) == p
    assert [x for x in raw_files(raw)] == [p]


def test_A2_manifest_line_format_and_utc(mk, roots):
    raw, man = roots
    st = mk()
    e = put(st, "bse", b"hello")
    mp = manifest_path(man, "bse")
    text = mp.read_bytes().decode("utf-8")
    assert text.endswith("\n") and text.count("\n") == 1
    obj = json.loads(text)
    assert sorted(obj) == sorted(FIELDS)
    assert obj["fetched_at"] == "2026-11-21T09:30:00.000000Z"
    assert obj["size"] == 5 and isinstance(obj["size"], int) and not isinstance(obj["size"], bool)
    assert obj["sha256"] == HELLO_SHA and obj["source"] == "bse"
    assert obj["url"] == "https://example.test/f" and obj["source_version"] == "v1"
    assert obj["logical_key"] == "2026-11-20" and obj["run_id"] == "run-1"
    assert all(isinstance(obj[k], str) for k in FIELDS if k != "size")
    assert text == json.dumps(obj, sort_keys=True, separators=(",", ":"), ensure_ascii=False) + "\n"
    # returned entry: aware UTC datetime; to_json_line matches the file
    assert e.fetched_at.utcoffset().total_seconds() == 0
    assert e.fetched_at == FETCHED
    assert e.to_json_line() in (text, text.rstrip("\n"))
    # R12: same instant, different offset, microseconds -> canonical 6-digit UTC string
    put(st, "bse", b"hello", fetched_at=datetime(2026, 11, 21, 9, 30, 0, 5, tzinfo=timezone.utc))
    second = json.loads(lines_of(mp)[1])
    assert second["fetched_at"] == "2026-11-21T09:30:00.000005Z"
    back = st.entries("bse")
    assert back[0].fetched_at == FETCHED and back[0].fetched_at.tzinfo is not None
    assert back[1].fetched_at == datetime(2026, 11, 21, 9, 30, 0, 5, tzinfo=timezone.utc)


@pytest.mark.planted
def test_A3_naive_datetime_rejected(mk, roots):
    raw, man = roots
    st = mk()
    with pytest.raises(ValueError):
        put(st, "bse", b"hello", fetched_at=datetime(2026, 11, 21, 15, 0, 0))
    assert raw_files(raw) == []
    mp = manifest_path(man, "bse")
    assert (not mp.exists()) or mp.read_bytes() == b""


@pytest.mark.planted
@pytest.mark.parametrize("field,value", [
    ("url", ""), ("source_version", ""), ("run_id", ""), ("logical_key", ""), ("logical_key", "k" * 513),
])
def test_A3b_argument_constraints(mk, roots, field, value):
    raw, man = roots
    st = mk()
    with pytest.raises(ValueError):
        put(st, "bse", b"hello", **{field: value})
    assert raw_files(raw) == []
    mp = manifest_path(man, "bse")
    assert (not mp.exists()) or mp.read_bytes() == b""


def test_A3c_logical_key_boundary_512_ok(mk, roots):
    st = mk()
    put(st, "bse", b"hello", logical_key="k" * 512)
    assert st.get_by_key("bse", "k" * 512).logical_key == "k" * 512


def test_A4_same_bytes_dedup_two_lines(mk, roots):
    raw, man = roots
    st = mk()
    put(st, "bse", b"hello", run_id="r1", fetched_at=FETCHED)
    p = raw_file(raw, "bse", b"hello")
    before = p.stat()
    put(st, "bse", b"hello", run_id="r2", fetched_at=datetime(2026, 11, 22, 1, 0, tzinfo=timezone.utc))
    after = p.stat()
    assert raw_files(raw) == [p]
    assert after.st_mtime_ns == before.st_mtime_ns and after.st_ino == before.st_ino
    assert stat.S_IMODE(after.st_mode) == 0o444                      # R3: pre-existing mode untouched
    lines = lines_of(manifest_path(man, "bse"))
    assert len(lines) == 2
    objs = [json.loads(l) for l in lines]
    assert objs[0]["sha256"] == objs[1]["sha256"] == HELLO_SHA
    assert [o["run_id"] for o in objs] == ["r1", "r2"]
    assert [e.run_id for e in st.get_by_sha("bse", HELLO_SHA)] == ["r1", "r2"]


@pytest.mark.planted
def test_A5_tampered_existing_file_not_overwritten(mk, roots):
    raw, man = roots
    st = mk()
    put(st, "bse", b"AAA")
    p = raw_file(raw, "bse", b"AAA")
    tamper(p, b"BBB")
    mp = manifest_path(man, "bse")
    manifest_before = mp.read_bytes()
    with pytest.raises(Exception) as ei:
        put(st, "bse", b"AAA", run_id="r2")
    from investos.acquire.manifest import RawIntegrityError
    assert isinstance(ei.value, RawIntegrityError)
    assert p.read_bytes() == b"BBB"
    assert mp.read_bytes() == manifest_before
    assert len(lines_of(mp)) == 1
    assert not [f for f in p.parent.iterdir() if f.name.startswith(".tmp-")]


def test_A6_no_destructive_public_api():
    import investos.acquire.manifest as m
    forbidden = {"delete", "remove", "unlink", "overwrite", "update", "truncate", "rewrite"}
    allowed_module = {"ManifestError", "ManifestCorruptError", "RawIntegrityError",
                      "ManifestStore", "ManifestEntry", "Problem"}
    allowed_methods = {"from_config", "store", "get_by_key", "get_all_by_key", "get_by_sha",
                       "entries", "raw_path", "read_bytes", "verify"}
    own = {n for n in dir(m) if not n.startswith("_")
           and getattr(getattr(m, n), "__module__", None) == m.__name__
           and (callable(getattr(m, n)))}
    assert own == allowed_module, f"unexpected/missing public names: {own ^ allowed_module}"
    assert not (forbidden & {n for n in dir(m) if not n.startswith("_")})
    methods = {n for n in dir(m.ManifestStore) if not n.startswith("_")}
    assert methods == allowed_methods, f"unexpected/missing methods: {methods ^ allowed_methods}"
    assert issubclass(m.ManifestCorruptError, m.ManifestError) and issubclass(m.RawIntegrityError, m.ManifestError)


@pytest.mark.planted
@pytest.mark.parametrize("target", ["fsync", "link"])
def test_A7_failure_midstore_leaves_nothing(mk, roots, monkeypatch, target):
    raw, man = roots
    st = mk()

    def boom(*a, **k):
        raise OSError(5, "injected")
    monkeypatch.setattr(os, target, boom)
    with pytest.raises(OSError):
        put(st, "bse", b"hello")
    monkeypatch.undo()
    assert not [p for p in raw.rglob("*") if p.is_file()], "temp or final file left behind"
    assert not raw_file(raw, "bse", b"hello").exists()
    mp = manifest_path(man, "bse")
    assert (not mp.exists()) or mp.read_bytes() == b""


def test_A8_link_race_identical_bytes_is_dedup(mk, roots, monkeypatch):
    raw, man = roots
    st = mk()
    dest = raw_file(raw, "bse", b"hello")

    def racing_link(src, dst, *a, **k):
        # a concurrent writer wins the race with identical bytes
        Path(dst).parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(src, dst)
        os.chmod(dst, 0o444)
        raise FileExistsError(17, "File exists", str(dst))
    monkeypatch.setattr(os, "link", racing_link)
    put(st, "bse", b"hello")
    monkeypatch.undo()
    assert dest.read_bytes() == b"hello" and stat.S_IMODE(dest.stat().st_mode) == 0o444
    assert [p for p in raw_files(raw)] == [dest], "temp file not cleaned up"
    assert len(lines_of(manifest_path(man, "bse"))) == 1


def test_A20_empty_payload(mk, roots):
    raw, man = roots
    st = mk()
    put(st, "bse", b"")
    p = raw / "bse" / "e3" / EMPTY_SHA
    assert p.is_file() and p.read_bytes() == b"" and stat.S_IMODE(p.stat().st_mode) == 0o444
    obj = json.loads(lines_of(manifest_path(man, "bse"))[0])
    assert obj["size"] == 0 and obj["sha256"] == EMPTY_SHA


def test_A21_special_characters_roundtrip(mk, roots):
    raw, man = roots
    st = mk()
    url = 'https://x.test/a?q="1"\nline2&é'
    key = 'k"1\n2é'
    put(st, "bse", b"one", url=url, logical_key=key)
    put(st, "bse", b"two", url=url, logical_key=key, run_id="r2")
    mp = manifest_path(man, "bse")
    data = mp.read_bytes()
    assert data.count(b"\n") == 2 and len(data.decode("utf-8").splitlines()) == 2
    assert "é" in data.decode("utf-8")                       # ensure_ascii=False
    es = st.entries("bse")
    assert [e.url for e in es] == [url, url] and [e.logical_key for e in es] == [key, key]
    assert st.get_by_key("bse", key).run_id == "r2"


def test_A22_stale_tmp_ignored(mk, roots):
    raw, man = roots
    st = mk()
    d = raw / "bse" / sha(b"other")[:2]
    d.mkdir(parents=True)
    stale = d / ".tmp-abc"
    stale.write_bytes(b"half written")
    before = (stale.read_bytes(), stale.stat().st_mtime_ns)
    put(st, "bse", b"other")
    assert (stale.read_bytes(), stale.stat().st_mtime_ns) == before
    assert st.verify("bse") == []
    assert raw_file(raw, "bse", b"other").read_bytes() == b"other"


@pytest.mark.parametrize("missing", ["raw", "manifests"])
def test_A23_missing_root_raises_manifest_error(tmp_path, missing):
    from investos.acquire.manifest import ManifestError, ManifestStore
    base = tmp_path / "gone"
    raw = base / "raw" if missing == "raw" else tmp_path / "raw"
    man = base / "man" if missing == "manifests" else tmp_path / "man"
    for p in (raw, man):
        if p.parent == tmp_path:
            p.mkdir()
    with pytest.raises(ManifestError):
        ManifestStore(raw, man).store("bse", b"x", url="u", source_version="v", logical_key="k",
                                      run_id="r", fetched_at=FETCHED)
    assert not base.exists(), "directories created above a missing root"


def test_A24_from_config(tmp_path):
    from investos.core.config import Config, RateLimits
    from investos.acquire.manifest import ManifestStore
    raw, man = tmp_path / "r", tmp_path / "m"
    raw.mkdir()
    man.mkdir()
    cfg = Config(repo_root=tmp_path, timezone="Asia/Kolkata", zones={"raw": raw, "manifests": man},
                 migrations_dir=tmp_path / "mig", rate_limits=RateLimits(0.35, {}))
    st = ManifestStore.from_config(cfg)
    put(st, "bse", b"hello")
    assert (raw / "bse" / "2c" / HELLO_SHA).read_bytes() == b"hello"
    assert (man / "bse.jsonl").is_file()
