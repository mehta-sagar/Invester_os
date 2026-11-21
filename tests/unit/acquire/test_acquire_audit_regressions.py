"""S0-FIX A7(b): ManifestStore index must see appends made by ANOTHER store instance / process."""
from __future__ import annotations

import subprocess
import sys
import textwrap
from pathlib import Path

from _acq_helpers import FETCHED, put

SRC = str(Path(__file__).resolve().parents[3] / "src")


def test_A7b_other_store_instance_append_is_seen(mk, tmp_path):
    a = mk()
    put(a, "bse", b"one", logical_key="k1")
    assert a.get_by_key("bse", "k1") is not None        # populates A's cached index
    assert a.get_by_key("bse", "k2") is None
    b = mk()                                             # same roots, different instance
    e2 = put(b, "bse", b"two", logical_key="k2")
    got = a.get_by_key("bse", "k2")
    assert got is not None and got.sha256 == e2.sha256, "A's cached index went stale after B appended"
    # A's own later append after B's still keeps the index consistent
    put(a, "bse", b"three", logical_key="k3")
    assert b.get_by_key("bse", "k3") is not None
    assert a.get_by_key("bse", "k2") is not None


def test_A7b_other_store_same_size_rewrite_of_latest_wins(mk):
    a = mk()
    put(a, "bse", b"one", logical_key="k1")
    first = a.get_by_key("bse", "k1")
    b = mk()
    put(b, "bse", b"uno", logical_key="k1")              # same key, new bytes: latest wins (R9)
    now = a.get_by_key("bse", "k1")
    assert now.sha256 != first.sha256


def test_A7b_other_process_append_is_seen(mk, roots):
    raw, man = roots
    a = mk()
    put(a, "bse", b"one", logical_key="k1")
    assert a.get_by_key("bse", "k1") is not None
    assert a.get_by_key("bse", "from-child") is None
    code = textwrap.dedent(f"""
        import sys; sys.path.insert(0, {SRC!r})
        from datetime import datetime, timezone
        from pathlib import Path
        from investos.acquire.manifest import ManifestStore
        st = ManifestStore(Path({str(raw)!r}), Path({str(man)!r}))
        st.store("bse", b"child-bytes", url="u", source_version="v1", logical_key="from-child",
                 run_id="run-2", fetched_at=datetime(2026, 11, 21, tzinfo=timezone.utc))
    """)
    subprocess.run([sys.executable, "-I", "-c", code], check=True)
    assert a.get_by_key("bse", "from-child") is not None, "index stale after another process appended"


def test_A7b_own_append_after_foreign_append_does_not_hide_foreign_entry(mk):
    """Audit mutant X1: A extends its cached index on its own append even though B appended in between."""
    a = mk()
    put(a, "bse", b"one", logical_key="k1")
    assert a.get_by_key("bse", "k1") is not None            # A caches the index
    b = mk()
    put(b, "bse", b"two", logical_key="k2")                 # B appends; A not told
    put(a, "bse", b"three", logical_key="k3")               # A appends without looking up first
    assert a.get_by_key("bse", "k2") is not None, "A's cache missed B's entry (own append extended a stale index)"
    assert a.get_by_key("bse", "k3") is not None


# ================================================================== round 2
import errno
import json
import multiprocessing as mp
import os
import threading
import time
from datetime import datetime, timezone

import pytest

from _collector_helpers import (CSV, D17, D18, D19, SOURCE, demo_class, env, make_zip, resp, rr,  # noqa: F401
                                sha, url_of)


# ---------------------------------------------------------------- A2: readers vs a writer mid-append
def _split_line(mk, roots):
    """Return (store, line_bytes_of_k1, size_before_k1). The manifest ends up holding only k0."""
    raw, man = roots
    st = mk()
    put(st, "bse", b"zero", logical_key="k0")
    mp_ = man / "bse.jsonl"
    before = mp_.stat().st_size
    put(st, "bse", b"one", logical_key="k1")
    data = mp_.read_bytes()
    line = data[before:]
    mp_.write_bytes(data[:before])
    return st, line, before, mp_


def _hold_lock_with_half_line(man, mp_, line, ready, cut=None):
    import fcntl
    fd = os.open(man / "bse.jsonl.lock", os.O_RDWR | os.O_CREAT, 0o644)
    fcntl.flock(fd, fcntl.LOCK_EX)
    try:
        cut = cut or len(line) // 2
        with open(mp_, "ab", buffering=0) as fh:
            fh.write(line[:cut])
            ready.set()
            time.sleep(0.4)
            fh.write(line[cut:])
    finally:
        fcntl.flock(fd, fcntl.LOCK_UN)
        os.close(fd)


def test_A2_get_by_key_during_foreign_append_waits_instead_of_raising(mk, roots):
    _, line, _, mp_ = _split_line(mk, roots)
    ready = threading.Event()
    t = threading.Thread(target=_hold_lock_with_half_line, args=(roots[1], mp_, line, ready))
    t.start()
    assert ready.wait(5)
    try:
        reader = mk()                                  # fresh store, nothing cached
        got = reader.get_by_key("bse", "k1")           # must not raise ManifestCorruptError
        assert got is not None, "reader saw a stale file instead of the completed append"
    finally:
        t.join()


def test_A2_verify_during_foreign_append_reports_no_bad_line(mk, roots):
    _, line, _, mp_ = _split_line(mk, roots)
    ready = threading.Event()
    t = threading.Thread(target=_hold_lock_with_half_line, args=(roots[1], mp_, line, ready))
    t.start()
    assert ready.wait(5)
    try:
        probs = mk().verify("bse")
    finally:
        t.join()
    assert not [p for p in probs if p.kind == "bad_line"], probs


def test_A2_guard_genuinely_torn_tail_without_writer_still_raises(mk, roots):
    from investos.acquire.manifest import ManifestCorruptError
    _, line, _, mp_ = _split_line(mk, roots)
    with open(mp_, "ab") as fh:
        fh.write(line[: len(line) // 2])               # nobody holds the lock: a real torn tail
    with pytest.raises(ManifestCorruptError):
        mk().get_by_key("bse", "k1")
    assert [p.kind for p in mk().verify("bse")].count("bad_line") == 1


def _a2_writer(raw, man, wid, n):
    from investos.acquire.manifest import ManifestStore
    st = ManifestStore(raw, man)
    for i in range(n):
        st.store("bse", f"p-{wid}-{i}".encode(), url="https://x.test/" + "u" * 300, source_version="v",
                 logical_key=f"k{wid}-{i}-" + "x" * 300, run_id="w",
                 fetched_at=datetime(2026, 11, 21, tzinfo=timezone.utc))


def _a2_reader(raw, man, stop, q):
    from investos.acquire.manifest import ManifestCorruptError, ManifestStore
    st = ManifestStore(raw, man)
    errs = bad = reads = 0
    while not stop.is_set():
        try:
            st.get_by_key("bse", "nope")
            reads += 1
        except ManifestCorruptError:
            errs += 1
        if reads % 20 == 0:
            bad += sum(1 for p in ManifestStore(raw, man).verify("bse") if p.kind == "bad_line")
    q.put((reads, errs, bad))


def test_A2_stress_four_writers_one_reader_no_false_corruption(roots):
    raw, man = roots
    ctx = mp.get_context("fork")
    stop, q = ctx.Event(), ctx.Queue()
    r = ctx.Process(target=_a2_reader, args=(raw, man, stop, q))
    r.start()
    ws = [ctx.Process(target=_a2_writer, args=(raw, man, w, 80)) for w in range(4)]
    for w in ws:
        w.start()
    for w in ws:
        w.join()
    stop.set()
    reads, errs, bad = q.get(timeout=30)
    r.join()
    assert reads > 0
    assert (errs, bad) == (0, 0), f"{errs} ManifestCorruptError and {bad} false bad_line in {reads} lookups"


# ---------------------------------------------------------------- A6: header deny rules
DENIED_HEADERS = ["Authorization", "Proxy-Authorization", "Host", "Forwarded", "X-Client-IP", "True-Client-IP",
                  "X-Real-IP", "X-Forwarded-For", "X-Forwarded-Host", "Via", "Cookie", "Cookie2", "Referer",
                  "Origin", "User-Agent"]


@pytest.mark.parametrize("name", DENIED_HEADERS)
def test_A6_denied_names_rejected(env, name):
    with pytest.raises(ValueError):
        env.make(demo_class(extra_headers={name: "x"}))


@pytest.mark.parametrize("name", [n for n in DENIED_HEADERS])
def test_A6_denied_names_rejected_in_any_case_and_padding(env, name):
    for variant in (name.upper(), name.lower(), name.swapcase(), f" {name}", f"{name} ", f"\t{name}", f"{name}\t",
                    name.replace("-", "_")):
        with pytest.raises(ValueError):
            env.make(demo_class(extra_headers={variant: "x"}))


@pytest.mark.parametrize("name,value", [
    ("X-Ok", "a\r\nCookie: nsit=1"), ("X-Ok", "a\nb"), ("X-Ok", "a\rb"), ("X-Ok", "a\0b"),
    ("X-Ok\r\nCookie", "1"), ("X\nOk", "1"), ("X\0Ok", "1"), ("X Ok", "1"), ("", "1"), ("X-Ok:", "1"),
])
def test_A6_control_characters_and_malformed_names_rejected(env, name, value):
    with pytest.raises(ValueError):
        env.make(demo_class(extra_headers={name: value}))


@pytest.mark.parametrize("name", ["Accept", "Accept-Language", "Accept-Encoding", "X-Api-Version"])
def test_A6_guard_ordinary_headers_still_allowed(env, name):
    env.make(demo_class(extra_headers={name: "gzip"}))


# ---------------------------------------------------------------- A17 X4: run_id format guard
@pytest.mark.parametrize("bad", ["../evil", "a/b", "", " x", "x y", "x\n", ".hidden", "-x", "a" * 129])
def test_X4_bad_run_id_rejected(env, bad):
    with pytest.raises(ValueError):
        env.make(run_id=bad)


# ---------------------------------------------------------------- A12 / A13
def _arch(**kw):
    def ok(self, key, r):
        from investos.acquire.base import BodyVerdict, Outcome
        return BodyVerdict(Outcome.FETCHED)
    return demo_class(archive=True, verdict_fn=ok, **kw)


def _zip_for(key):
    return make_zip({"a.csv": f"date,x\n{key},1\n".encode()})


def test_A12_tampered_stored_zip_marks_key_and_run_continues(env):
    from investos.acquire.base import Outcome
    for k in (D17, D18):
        env.transport.set(url_of(k), resp(200, _zip_for(k)))
    env.make(_arch(), run_id="run-1").run(rr(D17, D18))
    z = _zip_for(D17)
    p = env.raw / SOURCE / sha(z)[:2] / sha(z)
    os.chmod(p, 0o644)
    p.write_bytes(bytes([z[0] ^ 1]) + z[1:])
    os.chmod(p, 0o444)
    env.fresh_transport().set(url_of(D19), resp(200, _zip_for(D19)))
    r = env.make(_arch(), run_id="run-2").run(rr(D17, D19))          # must not raise RawIntegrityError
    assert r.outcomes[D17] is Outcome.UNREADABLE
    assert r.outcomes[D18] is Outcome.SKIPPED
    assert r.outcomes[D19] is Outcome.FETCHED, "one bad stored zip must not abort the range"
    assert D17 in env.gaps(r) and "integrity" in env.gaps(r)[D17]["reason"].lower()
    assert r.sidecar_path.exists() and env.sidecar(r)["status"] == "incomplete"


def test_A13_storage_failure_mid_run_leaves_sidecar_with_failure(env, monkeypatch):
    for k in (D17, D18, D19):
        env.transport.set(url_of(k), resp(200, CSV))
    real, n = env.store.store, {"n": 0}

    def flaky(*a, **k):
        n["n"] += 1
        if n["n"] == 2:
            raise OSError(errno.ENOSPC, "No space left on device")
        return real(*a, **k)
    monkeypatch.setattr(env.store, "store", flaky)
    try:
        env.make().run(rr(D17, D19))
    except OSError:
        pass                                             # re-raising after writing the sidecar is acceptable
    files = [p for p in env.sidecar_dir.glob("*.json")] if env.sidecar_dir.exists() else []
    assert len(files) == 1, "no sidecar written after a storage failure"
    sc = json.loads(files[0].read_text())
    assert sc["status"] == "aborted"
    gaps = {g["key"]: g for g in sc["gaps"]}
    assert D18 in gaps and any(w in gaps[D18]["reason"].lower() for w in ("space", "storage", "enospc", "errno 28"))
    assert D19 in gaps, "remaining key must be recorded as not attempted"
    assert [o["key"] for o in sc["outputs"]] == [D17]
