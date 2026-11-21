"""S0-T4 collector base: run sidecar format and protection (spec A20-A22, R13, R14)."""
from __future__ import annotations

import json
import re
from pathlib import Path

import pytest

from _collector_helpers import (CSV, D17, D18, D19, SOURCE, SOURCE_VERSION, demo_class, env, resp, rr, sha,  # noqa: F401
                                url_of)

TS = re.compile(r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}\.\d{6}Z$")


def _no_floats(o):
    if isinstance(o, float):
        raise AssertionError("float in sidecar")
    if isinstance(o, dict):
        [_no_floats(v) for v in o.values()]
    if isinstance(o, list):
        [_no_floats(v) for v in o]


def test_sidecar_schema_canonical_bytes_and_no_temp_files(env):  # R13
    env.script_ok(D17)
    env.transport.set(url_of(D18), resp(200, b"junk"))
    r = env.make(run_id="run-9").run(rr(D17, D18))
    raw = Path(r.sidecar_path).read_bytes()
    obj = json.loads(raw)
    assert raw == (json.dumps(obj, sort_keys=True, separators=(",", ":"), ensure_ascii=False) + "\n").encode()
    assert set(obj) == {"schema", "source", "source_version", "run_id", "range", "started_at", "finished_at",
                        "status", "expected", "received", "outcomes", "gaps", "outputs"}
    assert obj["schema"] == 1 and obj["source"] == SOURCE and obj["source_version"] == SOURCE_VERSION
    assert obj["run_id"] == "run-9" and obj["range"] == {"start": D17, "end": D18}
    assert TS.match(obj["started_at"]) and TS.match(obj["finished_at"])
    assert set(obj["outcomes"]) == {"fetched", "fetched_empty", "missing_holiday", "unreadable", "skipped",
                                    "incomplete", "not_attempted"}
    assert obj["gaps"] == [{"key": D18, "outcome": "unreadable", "reason": obj["gaps"][0]["reason"]}]
    assert obj["gaps"][0]["reason"]
    assert obj["outputs"] == [{"key": D17, "sha256": sha(CSV)}]
    _no_floats(obj)
    assert [p.name for p in env.sidecar_dir.iterdir()] == [f"{D17}_{D18}.json"]      # no tmp leftovers
    assert Path(r.sidecar_path) == env.sidecar_dir / f"{D17}_{D18}.json"


def test_gaps_and_outputs_sorted_by_key(env):
    ks = ["2026-11-19", "2026-11-17", "2026-11-18"]
    for k in ks:
        env.transport.set(url_of(k), resp(200, b"junk" if k != D18 else CSV))
    r = env.make(demo_class(fixed_keys=ks)).run(rr(D17, D19))
    sc = env.sidecar(r)
    assert [g["key"] for g in sc["gaps"]] == [D17, D19]
    assert list(r.outcomes) == ks                      # keys() order in RunResult


def _first_complete(env, run_id="run-1"):
    env.script_ok(D17, D18)
    r = env.make(run_id=run_id).run(rr(D17, D18))
    assert env.sidecar(r)["status"] == "complete"
    return r


@pytest.mark.planted
def test_A20_gap_run_never_overwrites_complete_sidecar(env):  # R14
    r1 = _first_complete(env)
    original = Path(r1.sidecar_path).read_bytes()
    env.fresh_transport()
    env.transport.set(url_of(D19), resp(503, b"down"))
    c = env.make(demo_class(fixed_keys=[D17, D18, D19]), run_id="run-2")
    r2 = c.run(rr(D17, D18))
    assert r2.status == "aborted" and r2.sidecar_replaced_previous is False
    assert Path(r1.sidecar_path).read_bytes() == original
    attempt = env.sidecar_dir / f"{D17}_{D18}.attempt-run-2.json"
    assert attempt.exists() and Path(r2.sidecar_path) == attempt
    obj = json.loads(attempt.read_text())
    assert obj["status"] == "aborted" and obj["run_id"] == "run-2"
    assert obj["gaps"][0]["key"] == D19


@pytest.mark.planted
def test_A20b_incomplete_run_also_protected(env):
    r1 = _first_complete(env)
    original = Path(r1.sidecar_path).read_bytes()
    env.fresh_transport()
    env.transport.set(url_of(D19), resp(200, b"junk"))
    r2 = env.make(demo_class(fixed_keys=[D17, D18, D19]), run_id="run-2").run(rr(D17, D18))
    assert r2.status == "incomplete" and not r2.sidecar_replaced_previous
    assert Path(r1.sidecar_path).read_bytes() == original
    assert (env.sidecar_dir / f"{D17}_{D18}.attempt-run-2.json").exists()


def test_A21a_incomplete_then_complete_replaces(env):
    from investos.acquire.base import Outcome
    env.script_ok(D17)
    env.transport.set(url_of(D18), resp(200, b"junk"))
    r1 = env.make(run_id="run-1").run(rr(D17, D18))
    assert env.sidecar(r1)["status"] == "incomplete"
    env.fresh_transport()
    env.script_ok(D18)
    r2 = env.make(run_id="run-2").run(rr(D17, D18))
    assert r2.outcomes == {D17: Outcome.SKIPPED, D18: Outcome.FETCHED}
    assert r2.sidecar_replaced_previous is True
    assert json.loads((env.sidecar_dir / f"{D17}_{D18}.json").read_text())["run_id"] == "run-2"
    assert not list(env.sidecar_dir.glob("*.attempt-*"))


def test_A21b_incomplete_then_aborted_replaces(env):
    env.script_ok(D17)
    env.transport.set(url_of(D18), resp(200, b"junk"))
    env.make(run_id="run-1").run(rr(D17, D18))
    env.fresh_transport()
    env.transport.set(url_of(D18), resp(503, b"down"))
    r2 = env.make(run_id="run-2").run(rr(D17, D18))
    assert r2.status == "aborted" and r2.sidecar_replaced_previous is True
    assert json.loads((env.sidecar_dir / f"{D17}_{D18}.json").read_text())["status"] == "aborted"


def test_A21c_complete_then_complete_replaces(env):
    _first_complete(env)
    env.fresh_transport()
    r2 = env.make(run_id="run-2").run(rr(D17, D18))
    assert r2.status == "complete" and r2.sidecar_replaced_previous is True
    assert json.loads((env.sidecar_dir / f"{D17}_{D18}.json").read_text())["run_id"] == "run-2"


@pytest.mark.planted
@pytest.mark.parametrize("junk", [b"{not json", b"", b"\xff\xfe\x00garbage"])
def test_A22_corrupt_existing_sidecar_is_renamed_not_lost(env, junk):
    env.sidecar_dir.mkdir(parents=True)
    target = env.sidecar_dir / f"{D17}_{D17}.json"
    target.write_bytes(junk)
    env.script_ok(D17)
    r = env.make(run_id="run-5").run(rr(D17))                # no exception escapes
    kept = env.sidecar_dir / f"{D17}_{D17}.corrupt-run-5.json"
    assert kept.read_bytes() == junk
    assert json.loads(target.read_text())["status"] == "complete" and Path(r.sidecar_path) == target


@pytest.mark.planted
def test_sidecar_written_for_aborted_run_with_not_attempted_gaps(env):
    env.transport.set(url_of(D17), resp(403, b"no"))
    r = env.make().run(rr(D17, D19))
    sc = env.sidecar(r)
    assert sc["status"] == "aborted" and sc["expected"] == 3 and sc["received"] == 0
    assert [g["key"] for g in sc["gaps"]] == [D17, D18, D19]
    assert [g["outcome"] for g in sc["gaps"]] == ["unreadable", "not_attempted", "not_attempted"]
