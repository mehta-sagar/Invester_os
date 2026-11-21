"""S0-T3 acceptance tests A9, A10 (+A9b threads) for file-lock-safe concurrent appends (rules R6, R7)."""
from __future__ import annotations

import json
import multiprocessing as mp
import threading
import time

import pytest

from _acq_helpers import (manifest_path, put, raw_files, worker_hold_lock, worker_store,
                          worker_timed_store)

N_PROC, M = 4, 50


def test_A9_multiprocess_appends_no_loss_no_tear(mk, roots):
    raw, man = roots
    mk()                                             # fails here if module missing
    ctx = mp.get_context("spawn")
    procs = [ctx.Process(target=worker_store, args=(str(raw), str(man), "bse", i, M)) for i in range(N_PROC)]
    for p in procs:
        p.start()
    for p in procs:
        p.join(120)
    assert [p.exitcode for p in procs] == [0] * N_PROC
    data = manifest_path(man, "bse").read_bytes()
    assert data.endswith(b"\n")
    lines = data.decode("utf-8").split("\n")[:-1]
    assert len(lines) == N_PROC * M
    objs = [json.loads(l) for l in lines]             # every line parses (none torn/interleaved)
    shas = [o["sha256"] for o in objs]
    assert len(set(shas)) == N_PROC * M
    assert len(raw_files(raw)) == N_PROC * M
    st = mk()
    assert st.verify() == []
    assert len(st.entries("bse")) == N_PROC * M


def test_A9b_threads_share_one_store(mk, roots):
    raw, man = roots
    st = mk()
    errs = []

    def run(w):
        try:
            for i in range(25):
                put(st, "bse", f"t-{w}-{i}".encode(), run_id=f"t{w}", logical_key=f"k{w}-{i}")
        except BaseException as e:      # noqa: BLE001
            errs.append(e)
    ts = [threading.Thread(target=run, args=(w,)) for w in range(4)]
    for t in ts:
        t.start()
    for t in ts:
        t.join(60)
    assert not errs
    assert len(st.entries("bse")) == 100 and len(raw_files(raw)) == 100
    assert st.verify() == []


@pytest.mark.planted
def test_A10_lock_blocks_same_source_not_other(mk, roots):
    raw, man = roots
    mk()
    ctx = mp.get_context("spawn")
    ready, go = ctx.Event(), ctx.Event()
    released, results = ctx.Queue(), ctx.Queue()
    hold = 2.0
    lock_path = man / "bse.jsonl.lock"
    holder = ctx.Process(target=worker_hold_lock, args=(str(lock_path), ready, hold, released))
    same = ctx.Process(target=worker_timed_store, args=(str(raw), str(man), "bse", go, results))
    other = ctx.Process(target=worker_timed_store, args=(str(raw), str(man), "nse", go, results))
    for p in (same, other):         # started first so import time is not counted in the hold window
        p.start()
    holder.start()
    assert ready.wait(60), "lock holder never acquired the lock"
    go.set()
    time.sleep(hold / 3)
    mp_same, mp_other = manifest_path(man, "bse"), manifest_path(man, "nse")
    assert (not mp_same.exists()) or mp_same.read_bytes() == b"", "line appeared while lock was held"
    t_release = released.get(timeout=60)
    got = dict(results.get(timeout=60) for _ in range(2))
    for p in (holder, same, other):
        p.join(60)
        assert p.exitcode == 0
    assert got["nse"] < t_release, "different source was blocked by another source's lock"
    assert got["bse"] >= t_release, "same-source store returned before the lock was released"
    assert len(mp_same.read_bytes().splitlines()) == 1 and len(mp_other.read_bytes().splitlines()) == 1
