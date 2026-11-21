"""S0-T4 collector base: page limit (A16) and archive safety (A17-A19)."""
from __future__ import annotations

import json
import os

import pytest

from _collector_helpers import (CSV, D17, SOURCE, demo_class, env, make_encrypted_flag_zip, make_symlink_zip,  # noqa: F401
                                make_zip, resp, rr, sha, url_of)


def _lim(**kw):
    from investos.acquire.base import UnzipLimits
    return UnzipLimits(**kw)


# ------------------------------------------------------------------ page limit (R10)
@pytest.mark.planted
@pytest.mark.parametrize("count,expect", [(100, "incomplete"), (99, "fetched"), (101, "incomplete"), (None, "fetched")])
def test_A16_page_limit(env, count, expect):
    from investos.acquire.base import BodyVerdict, Outcome
    env.transport.set(url_of(D17), resp(200, CSV))
    c = env.make(demo_class(page_limit=100, verdict_fn=lambda s, k, r: BodyVerdict(Outcome.FETCHED, record_count=count)))
    r = c.run(rr(D17))
    assert r.outcomes[D17].value == expect
    sc = env.sidecar(r)
    if expect == "incomplete":
        assert env.manifest_lines() == [] and env.raw_files() == []
        assert sc["status"] == "incomplete" and sc["gaps"][0]["key"] == D17
        assert "page limit 100 reached" in sc["gaps"][0]["reason"]
        assert sc["received"] == 0
    else:
        assert len(env.manifest_lines()) == 1 and sc["status"] == "complete"


# ------------------------------------------------------------------ safe_unzip unit (R12)
BAD_NAMES = ["../evil.txt", "/etc/passwd", "\\evil.txt", "C:\\x.txt", "c:/x.txt", "a/../../b", "a\\..\\..\\b", "a/../b"]


@pytest.mark.planted
@pytest.mark.parametrize("name", BAD_NAMES)
def test_A17a_zip_slip_member_names_rejected(name):
    from investos.acquire.base import UnsafeArchiveError, safe_unzip
    with pytest.raises(UnsafeArchiveError):
        safe_unzip(make_zip([("ok.csv", b"date,x\n1,2\n"), (name, b"evil")]))


@pytest.mark.planted
def test_A17b_symlink_encrypted_duplicate_and_non_zip_rejected():
    from investos.acquire.base import UnsafeArchiveError, safe_unzip
    for data in (make_symlink_zip(), make_encrypted_flag_zip(),
                 make_zip([("a.csv", b"1"), ("a.csv", b"2")]),
                 make_zip([("x/y.csv", b"1"), ("x\\y.csv", b"2")]),          # same after \ -> /
                 b"this is not a zip", b"", b"PK\x03\x04truncated"):
        with pytest.raises(UnsafeArchiveError):
            safe_unzip(data)


@pytest.mark.planted
def test_A18a_zip_bomb_default_ratio_rejected():
    from investos.acquire.base import UnsafeArchiveError, safe_unzip
    bomb = make_zip({"zeros.csv": b"\0" * 50_000_000})
    assert len(bomb) < 200_000
    with pytest.raises(UnsafeArchiveError):
        safe_unzip(bomb)


@pytest.mark.planted
def test_A18b_total_bytes_counted_while_streaming():
    from investos.acquire.base import UnsafeArchiveError, safe_unzip
    data = make_zip({f"m{i}.bin": os.urandom(600) for i in range(3)}, compress=False)
    lim = _lim(max_total_bytes=1000, max_ratio=1e9, max_member_bytes=10_000)
    with pytest.raises(UnsafeArchiveError):
        safe_unzip(data, limits=lim)
    assert len(safe_unzip(data, limits=_lim(max_total_bytes=1800, max_ratio=1e9))) == 3   # boundary ok


@pytest.mark.planted
def test_A18c_member_size_and_count_limits():
    from investos.acquire.base import UnsafeArchiveError, safe_unzip
    with pytest.raises(UnsafeArchiveError):
        safe_unzip(make_zip({"big.bin": os.urandom(2000)}, compress=False),
                   limits=_lim(max_member_bytes=1000, max_ratio=1e9))
    four = make_zip({f"m{i}.csv": b"date,x\n1,2\n" for i in range(4)})
    with pytest.raises(UnsafeArchiveError):
        safe_unzip(four, limits=_lim(max_members=3, max_ratio=1e9))
    assert len(safe_unzip(four, limits=_lim(max_members=4, max_ratio=1e9))) == 4


def test_A19a_safe_unzip_returns_sorted_pairs_and_writes_nothing(tmp_path, monkeypatch):
    from investos.acquire.base import safe_unzip
    monkeypatch.chdir(tmp_path)
    before = sorted(os.listdir(tmp_path))
    z = make_zip([("sub/b.csv", b"B,1\n"), ("a.csv", b"A,1\n"), ("sub/", b"")])      # dir entry skipped
    out = safe_unzip(z)
    assert out == [("a.csv", b"A,1\n"), ("sub/b.csv", b"B,1\n")]
    assert sorted(os.listdir(tmp_path)) == before and list(tmp_path.rglob("*")) == []


def test_A19a2_unzip_limits_defaults():
    from investos.acquire.base import UnzipLimits
    d = UnzipLimits()
    assert (d.max_members, d.max_total_bytes, d.max_ratio, d.max_member_bytes) == (10_000, 1_073_741_824, 100.0, 536_870_912)


# ------------------------------------------------------------------ archives through run()
def _arch(**kw):
    def ok(self, key, r):
        from investos.acquire.base import BodyVerdict, Outcome
        return BodyVerdict(Outcome.FETCHED)
    return demo_class(archive=True, verdict_fn=ok, **kw)


@pytest.mark.planted
@pytest.mark.parametrize("name", BAD_NAMES)
def test_A17c_run_rejects_unsafe_archive_and_stores_nothing(env, name):
    from investos.acquire.base import Outcome
    env.transport.set(url_of(D17), resp(200, make_zip([("ok.csv", b"date,x\n1,2\n"), (name, b"evil")])))
    r = env.make(_arch()).run(rr(D17))
    assert r.outcomes == {D17: Outcome.UNREADABLE}
    assert "unsafe archive" in env.gaps(r)[D17]["reason"]
    assert env.raw_files() == [] and env.manifest_lines() == []        # neither zip nor members


@pytest.mark.planted
@pytest.mark.parametrize("data", [make_symlink_zip, make_encrypted_flag_zip, lambda: b"not a zip at all",
                                  lambda: make_zip({"z.bin": b"\0" * 20_000_000})])
def test_A17d_run_rejects_other_unsafe_archives(env, data):
    from investos.acquire.base import Outcome
    env.transport.set(url_of(D17), resp(200, data()))
    r = env.make(_arch()).run(rr(D17))
    assert r.outcomes == {D17: Outcome.UNREADABLE}
    assert "unsafe archive" in env.gaps(r)[D17]["reason"]
    assert env.raw_files() == [] and env.manifest_lines() == []


@pytest.mark.planted
def test_A17e_unsafe_archive_does_not_abort_run(env):
    from investos.acquire.base import Outcome
    env.transport.set(url_of(D17), resp(200, b"not a zip"))
    env.transport.set(url_of("2026-11-18"), resp(200, make_zip({"a.csv": b"date,x\n1,2\n"})))
    r = env.make(_arch()).run(rr(D17, "2026-11-18"))
    assert r.outcomes == {D17: Outcome.UNREADABLE, "2026-11-18": Outcome.FETCHED} and r.status == "incomplete"


def test_A19b_run_stores_zip_and_members(env):
    from investos.acquire.base import Outcome
    z = make_zip([("a.csv", b"A,1\n"), ("sub/b.csv", b"B,1\n")])
    env.transport.set(url_of(D17), resp(200, z))
    r = env.make(_arch()).run(rr(D17))
    assert r.outcomes == {D17: Outcome.FETCHED}
    keys = {json.loads(l)["logical_key"]: json.loads(l) for l in env.manifest_lines()}
    assert set(keys) == {D17, f"{D17}/a.csv", f"{D17}/sub/b.csv"}
    assert keys[D17]["sha256"] == sha(z) and keys[f"{D17}/a.csv"]["sha256"] == sha(b"A,1\n")
    assert keys[f"{D17}/sub/b.csv"]["sha256"] == sha(b"B,1\n")
    sc = env.sidecar(r)
    assert [o["key"] for o in sc["outputs"]] == sorted([D17, f"{D17}/a.csv", f"{D17}/sub/b.csv"])
    assert {o["key"]: o["sha256"] for o in sc["outputs"]}[D17] == sha(z)
    assert sc["expected"] == 1 and sc["status"] == "complete"
    # re-run: zip key already in manifest -> skipped, zero HTTP
    env.fresh_transport()
    r2 = env.make(_arch(), run_id="run-2").run(rr(D17))
    assert r2.outcomes == {D17: Outcome.SKIPPED} and env.transport.calls == []
    assert [o["key"] for o in env.sidecar(r2)["outputs"]] == [o["key"] for o in sc["outputs"]]
