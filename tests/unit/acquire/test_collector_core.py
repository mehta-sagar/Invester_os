"""S0-T4 collector base: outcomes, skip, sidecar basics, validation (spec collect_base A1-A7, A23-A27, gates 1-3)."""
from __future__ import annotations

import ast
import builtins
import json
import os
import re
from pathlib import Path

import pytest

from _collector_helpers import (CSV, CSV_HEADER_ONLY, D17, D18, D19, NOW, SOURCE, SOURCE_VERSION, demo_class,
                                env, events, logs, resp, rr, sha, url_of)  # noqa: F401

REPO = Path(__file__).resolve().parents[3]


# ------------------------------------------------------------------ exit gates
def test_GATE1_fetches_one_file(env, logs):  # A1; R6, R11
    from investos.acquire.base import Outcome
    from investos.core import log
    env.script_ok(D17)
    c = env.make(run_id=None)                       # default run id and default sidecar dir
    r = c.run(rr(D17))
    assert r.outcomes == {D17: Outcome.FETCHED}
    assert r.http_calls == 1 and env.transport.n() == 1
    h = sha(CSV)
    raw = env.store.raw_path(SOURCE, h)
    assert raw.read_bytes() == CSV
    assert raw.stat().st_mode & 0o777 == 0o444      # read-only raw
    lines = env.manifest_lines()
    assert len(lines) == 1
    e = json.loads(lines[0])
    assert e["logical_key"] == D17 and e["source_version"] == SOURCE_VERSION
    assert e["run_id"] == log.get_run_id() and e["sha256"] == h and e["size"] == len(CSV)
    assert e["url"] == url_of(D17)
    assert e["fetched_at"] == "2026-11-21T09:30:00.000000Z" == NOW.strftime("%Y-%m-%dT%H:%M:%S.%fZ")
    assert Path(r.sidecar_path).parent == env.man / f"{SOURCE}.runs"
    assert Path(r.sidecar_path).name == "2026-11-17_2026-11-17.json"
    sc = env.sidecar(r)
    assert sc["status"] == "complete" and sc["expected"] == sc["received"] == 1
    assert sc["outputs"] == [{"key": D17, "sha256": h}] and sc["gaps"] == []
    assert r.status == "complete" and r.sidecar_replaced_previous is False


@pytest.mark.planted
def test_GATE2_broken_body_rejected(env):  # A2; R6 (BSE-style 200 error page)
    from investos.acquire.base import Outcome
    env.transport.set(url_of(D17), resp(200, b"<html>Error: data not available</html>"))
    r = env.make().run(rr(D17))
    assert r.outcomes == {D17: Outcome.UNREADABLE}
    assert env.raw_files() == [] and env.manifest_lines() == []
    sc = env.sidecar(r)
    assert sc["status"] == "incomplete" and r.status == "incomplete"
    assert [g["key"] for g in sc["gaps"]] == [D17]
    assert sc["gaps"][0]["outcome"] == "unreadable" and "check" in sc["gaps"][0]["reason"].lower()
    assert sc["outputs"] == [] and sc["received"] == 0


def test_GATE3_rerun_fetches_nothing(env):  # A5; R9
    from investos.acquire.base import Outcome
    keys = [D17, D18, D19]
    env.script_ok(*keys)
    r1 = env.make(run_id="run-1").run(rr(D17, D19))
    assert set(r1.outcomes.values()) == {Outcome.FETCHED}
    n_lines, sc1 = len(env.manifest_lines()), env.sidecar(r1)
    env.fresh_transport()                           # nothing scripted: any call fails the test
    r2 = env.make(run_id="run-2").run(rr(D17, D19))
    assert list(r2.outcomes) == keys and set(r2.outcomes.values()) == {Outcome.SKIPPED}
    assert r2.http_calls == 0 and env.transport.calls == []
    assert env.sleeper.sleeps == []
    assert len(env.manifest_lines()) == n_lines
    sc2 = env.sidecar(r2)
    assert sc2["status"] == "complete" and sc2["outputs"] == sc1["outputs"]
    assert sc2["outcomes"]["skipped"] == 3 and sc2["received"] == 3 and sc2["run_id"] == "run-2"


# ------------------------------------------------------------------ outcomes
@pytest.mark.planted
@pytest.mark.parametrize("mode", ["raises", "none", "incomplete_verdict", "skipped_verdict", "non_verdict"])
def test_A3_bad_check_body_is_unreadable_and_run_continues(env, mode):  # R6
    from investos.acquire.base import BodyVerdict, Outcome

    def verdict(self, key, r):
        if key != D17:
            return BodyVerdict(Outcome.FETCHED)
        if mode == "raises":
            raise RuntimeError("boom")
        return {"none": None, "incomplete_verdict": BodyVerdict(Outcome.INCOMPLETE, "x"),
                "skipped_verdict": BodyVerdict(Outcome.SKIPPED, "x"), "non_verdict": "fetched"}[mode]
    env.script_ok(D17, D18)
    r = env.make(demo_class(verdict_fn=verdict)).run(rr(D17, D18))
    assert r.outcomes == {D17: Outcome.UNREADABLE, D18: Outcome.FETCHED}
    assert env.transport.n(url_of(D18)) == 1        # not aborted
    assert [m["logical_key"] for m in map(json.loads, env.manifest_lines())] == [D18]
    gap = env.gaps(r)[D17]["reason"]
    assert gap.startswith("check_body error") if mode == "raises" else "invalid verdict" in gap
    assert r.status == "incomplete"


@pytest.mark.planted
@pytest.mark.parametrize("status", [404, 301, 400, 410])
def test_A4a_non_2xx_with_acceptable_body_is_unreadable(env, status):  # R6 downgrade
    from investos.acquire.base import Outcome
    env.transport.set(url_of(D17), resp(status, CSV))
    r = env.make().run(rr(D17))
    assert r.outcomes == {D17: Outcome.UNREADABLE}
    assert f"non-2xx status {status}" in env.gaps(r)[D17]["reason"]
    assert env.raw_files() == [] and env.manifest_lines() == []
    assert env.transport.n() == 1                   # 3xx/4xx not retried


def test_A4b_404_no_file_is_missing_holiday_via_subclass(env):  # R6, R7
    from investos.acquire.base import Outcome
    env.transport.set(url_of(D17), resp(404, b"NO FILE"))
    r = env.make().run(rr(D17))
    assert r.outcomes == {D17: Outcome.MISSING_HOLIDAY}
    assert env.raw_files() == [] and env.manifest_lines() == []
    sc = env.sidecar(r)
    assert sc["gaps"] == [] and sc["received"] == 1 and sc["outcomes"]["missing_holiday"] == 1
    assert sc["status"] == "complete"


def test_A6_missing_holiday_is_requested_again(env):  # R9
    from investos.acquire.base import Outcome
    env.script_ok(D17)
    env.transport.set(url_of(D18), resp(404, b"NO FILE"))
    r1 = env.make(run_id="run-1").run(rr(D17, D18))
    assert r1.outcomes == {D17: Outcome.FETCHED, D18: Outcome.MISSING_HOLIDAY}
    env.fresh_transport()
    env.transport.set(url_of(D18), resp(404, b"NO FILE"))
    r2 = env.make(run_id="run-2").run(rr(D17, D18))
    assert r2.outcomes == {D17: Outcome.SKIPPED, D18: Outcome.MISSING_HOLIDAY}
    assert env.transport.n() == 1 and env.transport.n(url_of(D18)) == 1


@pytest.mark.planted
@pytest.mark.parametrize("unstored", ["unreadable", "incomplete"])
def test_A6b_unstored_outcomes_are_requested_again(env, unstored):  # R9
    from investos.acquire.base import BodyVerdict, Outcome
    bad = resp(200, b"<html>x</html>")
    env.transport.set(url_of(D17), bad)
    r1 = env.make(demo_class(page_limit=1, verdict_fn=lambda s, k, r: BodyVerdict(Outcome.FETCHED, record_count=1)
                             if unstored == "incomplete" else BodyVerdict(Outcome.UNREADABLE, "bad"))).run(rr(D17))
    assert r1.outcomes[D17] in (Outcome.INCOMPLETE, Outcome.UNREADABLE)
    assert env.manifest_lines() == []
    env.fresh_transport()
    env.script_ok(D17)
    r2 = env.make(run_id="run-2").run(rr(D17))
    assert r2.outcomes == {D17: Outcome.FETCHED} and env.transport.n() == 1


def test_A7_four_outcomes_kept_separate(env, logs):  # R7, R16
    from investos.acquire.base import Outcome
    ks = [D17, D18, D19, "2026-11-20"]
    env.transport.set(url_of(D17), resp(200, CSV))
    env.transport.set(url_of(D18), resp(200, CSV_HEADER_ONLY))
    env.transport.set(url_of(D19), resp(404, b"NO FILE"))
    env.transport.set(url_of("2026-11-20"), resp(200, b"garbage"))
    r = env.make().run(rr(D17, "2026-11-20"))
    assert r.outcomes == {D17: Outcome.FETCHED, D18: Outcome.FETCHED_EMPTY, D19: Outcome.MISSING_HOLIDAY,
                          "2026-11-20": Outcome.UNREADABLE}
    sc = env.sidecar(r)
    c = sc["outcomes"]
    assert (c["fetched"], c["fetched_empty"], c["missing_holiday"], c["unreadable"]) == (1, 1, 1, 1)
    assert c["skipped"] == c["incomplete"] == c["not_attempted"] == 0
    assert sc["expected"] == 4 and sc["received"] == 3 and sum(c.values()) == 4
    assert [g["key"] for g in sc["gaps"]] == ["2026-11-20"]
    assert [o["key"] for o in sc["outputs"]] == [D17, D18]          # holiday/unreadable have no output
    assert env.store.get_by_key(SOURCE, D18) is not None            # empty body stored
    assert env.store.get_by_key(SOURCE, D19) is None
    names = {e["event"] for e in logs()}
    assert {"fetched", "fetched_empty", "missing_holiday", "unreadable", "run_end"} <= names


# ------------------------------------------------------------------ keys / range validation
@pytest.mark.planted
def test_A23_duplicate_and_empty_keys(env):  # R13
    from investos.acquire.base import Outcome
    env.script_ok(D17, D18)
    c = env.make(demo_class(fixed_keys=[D17, D17, D18]))
    r = c.run(rr(D17, D18))
    assert env.transport.n(url_of(D17)) == 1 and r.http_calls == 2
    assert list(r.outcomes) == [D17, D18]
    sc = env.sidecar(r)
    assert sc["expected"] == 2 and sum(sc["outcomes"].values()) == 2

    e2 = demo_class(fixed_keys=[])
    env.fresh_transport()
    r2 = env.make(e2, run_id="run-2").run(rr(D17, D18))
    sc2 = env.sidecar(r2)
    assert env.transport.calls == [] and r2.http_calls == 0
    assert sc2["expected"] == 0 and sc2["gaps"] == [] and sc2["status"] == "incomplete"
    assert r2.status == "incomplete"


@pytest.mark.planted
def test_A24_bad_range_and_keys_error(env):  # R15
    from investos.acquire.base import RunRange
    from datetime import date
    c = env.make()
    with pytest.raises(ValueError):
        c.run(RunRange(date(2026, 11, 19), date(2026, 11, 17)))
    assert env.transport.calls == []
    assert not env.sidecar_dir.exists() or list(env.sidecar_dir.iterdir()) == []

    class Boom(demo_class()):
        def keys(self, run_range):
            raise KeyError("keys failed")
    with pytest.raises(KeyError):
        env.make(Boom, run_id="run-2").run(rr(D17))
    assert env.transport.calls == []
    assert not env.sidecar_dir.exists() or list(env.sidecar_dir.iterdir()) == []


@pytest.mark.planted
def test_A24b_non_transport_exception_propagates_no_sidecar(env):  # edge: interrupted run
    from investos.acquire.base import Outcome
    env.script_ok(D17)
    env.transport.set(url_of(D18), KeyboardInterrupt())
    c = env.make()
    with pytest.raises(KeyboardInterrupt):
        c.run(rr(D17, D18))
    assert not env.sidecar_dir.exists() or list(env.sidecar_dir.glob("*")) == []
    env.fresh_transport()
    env.script_ok(D18)
    r = env.make(run_id="run-2").run(rr(D17, D18))   # D17 stored earlier: skippable
    assert r.outcomes == {D17: Outcome.SKIPPED, D18: Outcome.FETCHED}


@pytest.mark.planted
def test_A24c_instantiation_validation(env):
    with pytest.raises(ValueError):
        env.make(demo_class(source="Bad-Source"))
    with pytest.raises(ValueError):
        env.make(demo_class(page_limit=0))
    with pytest.raises(ValueError):
        env.make(demo_class(page_limit=-5))


# ------------------------------------------------------------------ R1, R11, R16
def test_A25_base_imports_no_content_parsers():  # R1
    p = REPO / "src" / "investos" / "acquire" / "base.py"
    assert p.exists(), "src/investos/acquire/base.py missing"
    tree = ast.parse(p.read_text())
    mods = set()
    for n in ast.walk(tree):
        if isinstance(n, ast.Import):
            mods |= {a.name for a in n.names}
        elif isinstance(n, ast.ImportFrom) and n.module:
            mods.add(n.module)
    forbidden = {"csv", "xml", "html", "bs4", "lxml", "openpyxl", "pandas", "xlrd", "yaml", "tomllib",
                 "pyarrow", "polars", "numpy", "html5lib"}
    bad = {m for m in mods if m.split(".")[0] in forbidden}
    assert not bad, f"parser imports in base.py: {bad}"
    # json is for the sidecar only: the body of a response must never be decoded
    src = p.read_text()
    assert not re.search(r"json\.loads?\(\s*(resp|response|r)\.body", src)


class _ManifestDouble:
    """Exposes ONLY get_by_key and store; any other attribute access raises AttributeError. Pure in-memory."""
    __slots__ = ("calls", "_entries")

    def __init__(self):
        self.calls, self._entries = [], {}

    def get_by_key(self, source, logical_key):
        self.calls.append(("get_by_key", source, logical_key))
        return self._entries.get((source, logical_key))

    def store(self, source, data, *, url, source_version, logical_key, run_id, fetched_at):
        from investos.acquire.manifest import ManifestEntry
        self.calls.append(("store", source, logical_key, dict(url=url, source_version=source_version,
                                                            run_id=run_id, fetched_at=fetched_at, data=data)))
        e = ManifestEntry(sha(data), len(data), url, fetched_at, source, source_version, logical_key, run_id)
        self._entries[(source, logical_key)] = e
        return e


@pytest.mark.planted
def test_A27_collector_uses_only_manifest_api_and_opens_no_raw_paths(env, monkeypatch):  # R11
    dbl = _ManifestDouble()
    env.script_ok(D17, D18)
    opened = []
    real_open, real_osopen = builtins.open, os.open

    def spy_open(f, *a, **k):
        opened.append(str(f))
        return real_open(f, *a, **k)

    def spy_osopen(f, *a, **k):
        opened.append(str(f))
        return real_osopen(f, *a, **k)
    c = env.make(manifest=dbl)
    monkeypatch.setattr(builtins, "open", spy_open)
    monkeypatch.setattr(os, "open", spy_osopen)
    r = c.run(rr(D17, D18))
    monkeypatch.undo()
    assert {x[0] for x in dbl.calls} <= {"get_by_key", "store"}
    stores = [x for x in dbl.calls if x[0] == "store"]
    assert [s[2] for s in stores] == [D17, D18] and all(s[1] == SOURCE for s in stores)
    kw = stores[0][3]
    assert kw["data"] == CSV and kw["fetched_at"] == NOW and kw["run_id"] == "run-1"
    assert kw["source_version"] == SOURCE_VERSION and kw["url"] == url_of(D17)
    assert [x[2] for x in dbl.calls if x[0] == "get_by_key"][:2] == [D17, D18]
    assert r.status == "complete"
    for p in opened:
        assert not p.startswith(str(env.raw)), f"collector touched raw tree: {p}"
        if p.startswith(str(env.man)):
            assert str(env.sidecar_dir) in p, f"collector touched manifests tree outside sidecar dir: {p}"


def test_A26_log_events(env, logs):  # R16
    env.script_ok(D17)
    env.transport.set(url_of(D18), resp(200, b"junk"))
    env.make().run(rr(D17, D18))
    recs = logs()
    f = events(recs, "fetched")
    u = events(recs, "unreadable")
    assert len(f) == 1 and len(u) == 1 and len(events(recs, "run_end")) == 1
    assert f[0]["task"] == u[0]["task"] == f"collect.{SOURCE}"
    assert f[0]["fields"]["key"] == D17 and u[0]["fields"]["key"] == D18
    assert f[0]["level"] == "INFO" and u[0]["level"] in ("INFO", "ERROR")
    assert f[0]["fields"]["url"] == url_of(D17) and f[0]["fields"]["attempts"] == 1
    assert u[0]["fields"]["reason"]
    assert events(recs, "run_end")[0]["task"] == f"collect.{SOURCE}"
