"""S0-T4 regression tests from reports/S0-T4/review.md (defects D1, D2, D3, D5, D6 and mutation survivors).

Tests marked RED must fail on 173f3db and pass after a correct fix. Tests marked MUTANT already pass and exist
to kill review mutation survivors (spec-backed). D4 (header deny-list) is deliberately not covered.
"""
from __future__ import annotations

import ast
import io
import json
import os
import zipfile
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from _collector_helpers import (CSV, D17, D18, D19, SOURCE, SOURCE_VERSION, demo_class, env, events, logs,  # noqa: F401
                                make_zip, resp, rr, sha, url_of)

REPO = Path(__file__).resolve().parents[3]
BASE_PY = REPO / "src" / "investos" / "acquire" / "base.py"


def _arch(**kw):
    def ok(self, key, r):
        from investos.acquire.base import BodyVerdict, Outcome
        return BodyVerdict(Outcome.FETCHED)
    return demo_class(archive=True, verdict_fn=ok, **kw)


class _Spy:
    """Transparent proxy around a ManifestStore recording public method names called."""
    def __init__(self, inner):
        self._inner = inner
        self.calls: list[str] = []

    def __getattr__(self, name):
        attr = getattr(self._inner, name)
        if callable(attr):
            def wrapped(*a, **k):
                self.calls.append(name)
                return attr(*a, **k)
            return wrapped
        return attr


# ------------------------------------------------------------------ D1 (R9): skip path must not re-parse per key  [RED]
def test_D1_skip_only_rerun_loads_manifest_a_bounded_number_of_times(env, monkeypatch):
    n = 200
    keys = [f"k{i:04d}" for i in range(n)]
    for k in keys:                                               # pre-fetch via the writer, not via a run
        env.store.store(SOURCE, f"date,x\n{k},1\n".encode(), url=url_of(k), source_version=SOURCE_VERSION,
                        logical_key=k, run_id="seed", fetched_at=datetime(2026, 11, 21, tzinfo=timezone.utc))
    manifest_file = env.man / f"{SOURCE}.jsonl"
    assert manifest_file.exists()
    reads = []
    real_read_bytes, real_read_text = Path.read_bytes, Path.read_text

    def spy_bytes(self, *a, **k):
        if self == manifest_file:
            reads.append(1)
        return real_read_bytes(self, *a, **k)

    def spy_text(self, *a, **k):
        if self == manifest_file:
            reads.append(1)
        return real_read_text(self, *a, **k)
    monkeypatch.setattr(Path, "read_bytes", spy_bytes)
    monkeypatch.setattr(Path, "read_text", spy_text)
    opened = []
    real_open = open

    def spy_open(f, *a, **k):
        if str(f) == str(manifest_file):
            opened.append(1)
        return real_open(f, *a, **k)
    monkeypatch.setattr("builtins.open", spy_open)

    c = env.make(demo_class(fixed_keys=keys))
    r = c.run(rr(D17))
    monkeypatch.undo()
    assert set(r.outcomes.values()) == {__import__("investos.acquire.base", fromlist=["Outcome"]).Outcome.SKIPPED}
    assert len(r.outcomes) == n and r.http_calls == 0 and env.transport.n() == 0
    assert len(reads) + len(opened) <= 3, f"manifest file read {len(reads) + len(opened)} times for {n} skipped keys"


# ------------------------------------------------------------------ D2 (R12): "./" dir entry; empty archive  [RED]
def _zip_with_dot_slash_dir() -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
        z.writestr(zipfile.ZipInfo("./"), b"")
        z.writestr("a.csv", b"A,1\n")
    return buf.getvalue()


def test_D2a_safe_unzip_accepts_dot_slash_directory_entry():
    from investos.acquire.base import safe_unzip
    assert safe_unzip(_zip_with_dot_slash_dir()) == [("a.csv", b"A,1\n")]


def test_D2b_run_stores_archive_with_dot_slash_directory_entry(env):
    from investos.acquire.base import Outcome
    z = _zip_with_dot_slash_dir()
    env.transport.set(url_of(D17), resp(200, z))
    r = env.make(_arch()).run(rr(D17))
    assert r.outcomes == {D17: Outcome.FETCHED}
    stored = {json.loads(l)["logical_key"] for l in env.manifest_lines()}
    assert stored == {D17, f"{D17}/a.csv"}


@pytest.mark.parametrize("members", [[], [("d/", b"")], [("./", b"")]], ids=["no_entries", "dir_only", "dot_slash_only"])
def test_D2c_zip_with_zero_file_members_is_not_recorded_as_fetched(env, members):
    # Spec R12 is silent on empty archives. Safer choice taken: unreadable, nothing stored (see checkpoint).
    from investos.acquire.base import Outcome
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as z:
        for name, data in members:
            z.writestr(zipfile.ZipInfo(name) if name.endswith("/") else name, data)
    env.transport.set(url_of(D17), resp(200, buf.getvalue()))
    r = env.make(_arch()).run(rr(D17))
    assert r.outcomes == {D17: Outcome.UNREADABLE}
    assert env.manifest_lines() == [] and env.raw_files() == []
    assert D17 in env.gaps(r) and env.sidecar(r)["status"] == "incomplete"


# ------------------------------------------------------------------ D3 (R16): skipped event carries url  [RED]
def test_D3_skipped_event_carries_real_url(env, logs):
    env.script_ok(D17)
    env.make().run(rr(D17))
    env.fresh_transport()
    env.make(run_id="run-2").run(rr(D17))
    sk = events(logs(), "skipped")
    assert len(sk) == 1
    assert sk[0]["fields"]["key"] == D17 and sk[0]["fields"]["url"] == url_of(D17)
    assert env.transport.calls == []                             # computing the url made no HTTP call


# ------------------------------------------------------------------ D5 (A27): archive skip path avoids entries()  [RED]
def test_D5_archive_run_and_skip_rerun_never_call_entries(env):
    from investos.acquire.base import Outcome
    z = make_zip([("a.csv", b"A,1\n"), ("sub/b.csv", b"B,1\n")])
    env.transport.set(url_of(D17), resp(200, z))
    spy = _Spy(env.store)
    r1 = env.make(_arch(), manifest=spy).run(rr(D17))
    env.fresh_transport()
    r2 = env.make(_arch(), manifest=spy, run_id="run-2").run(rr(D17))
    assert r1.outcomes == {D17: Outcome.FETCHED} and r2.outcomes == {D17: Outcome.SKIPPED}
    assert env.transport.calls == []
    assert "entries" not in spy.calls, f"collector called entries(): {spy.calls}"
    # and the skipped run still reports the same outputs (zip + members)
    assert [o["key"] for o in env.sidecar(r2)["outputs"]] == [o["key"] for o in env.sidecar(r1)["outputs"]]
    assert len(env.sidecar(r2)["outputs"]) == 3


# ------------------------------------------------------------------ D6: no private imports from manifest  [RED]
def test_D6_base_does_not_import_private_names():
    tree = ast.parse(BASE_PY.read_text())
    bad = [f"{n.module}.{a.name}" for n in ast.walk(tree) if isinstance(n, ast.ImportFrom)
           and (n.module or "").startswith("investos") for a in n.names if a.name.startswith("_") and not a.name.startswith("__")]
    assert bad == []
    assert "_check_source" not in BASE_PY.read_text()


# ================================================================== mutation survivors (already green)
# M08, R4: every 5xx is retryable
@pytest.mark.parametrize("status", [500, 501, 504, 505, 507, 511, 599])
def test_M08_any_5xx_is_retried(env, status):
    from investos.acquire.base import Outcome
    env.transport.set(url_of(D17), resp(status, b"x"), resp(200, CSV))
    r = env.make().run(rr(D17))
    assert r.outcomes == {D17: Outcome.FETCHED}
    assert env.transport.n() == 2 and [s for s in env.sleeper.sleeps if s >= 1] == [1]


@pytest.mark.parametrize("status", [499, 600])
def test_M08b_non_5xx_boundaries_are_not_retried(env, status):
    env.transport.set(url_of(D17), resp(status, b"x"), resp(200, CSV))
    r = env.make().run(rr(D17))
    assert env.transport.n() == 1 and r.outcomes[D17].value == "unreadable"


# M54, R4/R8: retry exhaustion logged at ERROR for the key itself
def test_M54_unreadable_after_retries_logged_at_error(env, logs):
    env.transport.set(url_of(D17), resp(503, b"x"))
    env.make().run(rr(D17))
    un = events(logs(), "unreadable")
    assert [e["level"] for e in un] == ["ERROR"] and un[0]["fields"]["key"] == D17 and un[0]["fields"]["attempts"] == 4


# M32 / N4, R13: sidecar via temp file + fsync + os.replace
def test_N4_sidecar_is_written_via_temp_file_fsync_and_replace(env, monkeypatch):
    env.script_ok(D17)
    replaces, fsyncs = [], []
    real_replace, real_fsync = os.replace, os.fsync

    def spy_replace(src, dst, *a, **k):
        replaces.append((Path(src), Path(dst)))
        return real_replace(src, dst, *a, **k)

    def spy_fsync(fd):
        fsyncs.append(fd)
        return real_fsync(fd)
    monkeypatch.setattr(os, "replace", spy_replace)
    monkeypatch.setattr(os, "fsync", spy_fsync)
    r = env.make().run(rr(D17))
    monkeypatch.undo()
    final = Path(r.sidecar_path)
    hits = [(s, d) for s, d in replaces if d == final]
    assert len(hits) == 1 and hits[0][0] != final and hits[0][0].parent == final.parent
    assert fsyncs, "sidecar content was never fsynced"
    assert [p.name for p in final.parent.iterdir()] == [final.name]          # no temp leftovers


def test_N4b_failed_replace_leaves_no_partial_sidecar(env, monkeypatch):
    env.script_ok(D17)

    def boom(*a, **k):
        raise OSError("disk gone")
    monkeypatch.setattr(os, "replace", boom)
    with pytest.raises(OSError):
        env.make().run(rr(D17))
    monkeypatch.undo()
    assert not (env.sidecar_dir / f"{D17}_{D17}.json").exists()
    assert [p for p in env.sidecar_dir.iterdir()] == []


# M57, spec "started_at ... Z": sidecar times are real UTC even for a non-UTC aware clock
def test_M57_sidecar_times_convert_to_utc(env):
    ist = timezone(timedelta(hours=5, minutes=30))
    env.script_ok(D17)
    c = env.make()
    c._clock = lambda: datetime(2026, 11, 21, 15, 0, 0, tzinfo=ist)         # 09:30 UTC
    r = c.run(rr(D17))
    sc = env.sidecar(r)
    assert sc["started_at"] == "2026-11-21T09:30:00.000000Z" and sc["finished_at"] == "2026-11-21T09:30:00.000000Z"


# N13, spec: outputs sorted by key even when keys() is unsorted
def test_N13_outputs_sorted_by_key_for_unsorted_keys(env):
    env.script_ok(D19, D17, D18)
    r = env.make(demo_class(fixed_keys=[D19, D17, D18])).run(rr(D17, D19))
    assert [o["key"] for o in env.sidecar(r)["outputs"]] == [D17, D18, D19]
