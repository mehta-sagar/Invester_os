"""S0-T4 collector base: retries, blocks, abort, headers, rate limits (spec A8-A15)."""
from __future__ import annotations

import pytest

from _collector_helpers import (CSV, D17, D18, D19, SOURCE, demo_class, env, events, logs, resp, rr,  # noqa: F401
                                terr, url_of)


@pytest.mark.planted
def test_A8_fourth_retry_never_attempted(env, logs):  # R4
    from investos.acquire.base import Outcome
    env.transport.set(url_of(D17), resp(503, b"busy"))
    r = env.make().run(rr(D17))
    assert env.transport.n() == 4 and r.http_calls == 4          # 1 try + 3 retries, never a 5th
    assert env.sleeper.sleeps == [1, 2, 4]
    assert r.outcomes == {D17: Outcome.UNREADABLE}
    assert "after 4 attempts" in env.gaps(r)[D17]["reason"]
    assert events(logs(), level="ERROR")
    assert len(events(logs(), "retry", "WARNING")) == 3
    assert env.manifest_lines() == []


@pytest.mark.planted
def test_A9b_transport_error_forever_is_unreadable_not_holiday(env):  # R4, R8
    from investos.acquire.base import Outcome
    env.transport.set(url_of(D17), terr("timeout"))
    env.transport.set(url_of(D18), resp(200, CSV))
    r = env.make().run(rr(D17, D18))
    assert env.transport.n(url_of(D17)) == 4
    assert r.outcomes == {D17: Outcome.UNREADABLE}
    assert "after 4 attempts" in env.gaps(r)[D17]["reason"]
    assert env.transport.n(url_of(D18)) == 0 and r.status == "aborted"


@pytest.mark.planted
@pytest.mark.parametrize("status", [400, 404, 410, 418])
def test_A10a_other_4xx_not_retried(env, status):  # R4, R5
    env.transport.set(url_of(D17), resp(status, b"nope"))
    env.make().run(rr(D17))
    assert env.transport.n() == 1 and env.sleeper.sleeps == []


def test_A10b_502_then_200_retries_once(env):  # R4
    from investos.acquire.base import Outcome
    env.transport.set(url_of(D17), resp(502, b"bad gw"), resp(200, CSV))
    r = env.make().run(rr(D17))
    assert r.outcomes == {D17: Outcome.FETCHED} and env.transport.n() == 2 and env.sleeper.sleeps == [1]


@pytest.mark.planted
@pytest.mark.parametrize("status", [401, 403, 451])
def test_A11_block_is_final_and_aborts(env, status):  # R5, R8
    from investos.acquire.base import Outcome
    for k in (D17, D18, D19):
        env.transport.set(url_of(k), resp(status, b"Access Denied"))
    r = env.make().run(rr(D17, D19))
    assert env.transport.n() == 1                                  # never retried
    assert r.outcomes == {D17: Outcome.UNREADABLE}
    gaps = env.gaps(r)
    assert gaps[D17]["reason"] == f"blocked http {status}" and gaps[D17]["outcome"] == "unreadable"
    for k in (D18, D19):
        assert gaps[k]["outcome"] == "not_attempted" and gaps[k]["reason"] == "run aborted after " + D17
    sc = env.sidecar(r)
    assert sc["status"] == "aborted" and r.status == "aborted" and sc["outcomes"]["not_attempted"] == 2
    assert sc["expected"] == 3 and sum(sc["outcomes"].values()) == 3
    assert env.sleeper.sleeps == []
    assert len({tuple(sorted(c["headers"].items())) for c in env.transport.calls}) == 1


@pytest.mark.planted
def test_A11b_block_mid_run_headers_never_change(env, logs):  # R2, R5
    env.script_ok(D17)
    env.transport.set(url_of(D18), resp(403, b"denied"))
    env.script_ok(D19)
    r = env.make().run(rr(D17, D19))
    assert env.transport.n(url_of(D19)) == 0
    assert len({tuple(sorted(c["headers"].items())) for c in env.transport.calls}) == 1
    assert events(logs(), level="ERROR")


@pytest.mark.planted
def test_A12a_retry_exhaustion_aborts_run(env):  # R8
    from investos.acquire.base import Outcome
    env.script_ok(D17)
    env.transport.set(url_of(D18), resp(500, b"err"))
    env.script_ok(D19)
    r = env.make().run(rr(D17, D19))
    assert env.transport.n(url_of(D19)) == 0
    assert r.outcomes == {D17: Outcome.FETCHED, D18: Outcome.UNREADABLE}
    g = env.gaps(r)
    assert g[D19]["reason"] == f"run aborted after {D18}" and g[D19]["outcome"] == "not_attempted"
    assert env.sidecar(r)["status"] == "aborted"


@pytest.mark.planted
def test_A12b_check_body_unreadable_does_not_abort(env):  # R8
    from investos.acquire.base import Outcome
    env.script_ok(D17)
    env.transport.set(url_of(D18), resp(200, b"garbage"))
    env.script_ok(D19)
    r = env.make().run(rr(D17, D19))
    assert env.transport.n(url_of(D19)) == 1
    assert r.outcomes == {D17: Outcome.FETCHED, D18: Outcome.UNREADABLE, D19: Outcome.FETCHED}
    assert r.status == "incomplete" and env.sidecar(r)["status"] == "incomplete"


@pytest.mark.planted
def test_A12c_non_2xx_downgrade_does_not_abort(env):  # R8
    env.transport.set(url_of(D17), resp(404, CSV))
    env.script_ok(D18)
    r = env.make().run(rr(D17, D18))
    assert env.transport.n(url_of(D18)) == 1 and r.status == "incomplete"


# ------------------------------------------------------------------ headers (R2)
# S1-T1: A9 (429 retried), A13a/b/d (UA investos/<v>, headers exactly one) removed; superseded by A30, A31, A40 (spec table).
@pytest.mark.planted
@pytest.mark.parametrize("name", ["Referer", "referer", "COOKIE", "User-Agent", "user-agent", "Origin",
                                  "X-Forwarded-For", "x-real-ip"])
def test_A13c_spoofing_headers_rejected_at_instantiation(env, name):
    with pytest.raises(ValueError):
        env.make(demo_class(extra_headers={name: "https://x"}))


# ------------------------------------------------------------------ rate limits (R3)
def _starts(env):
    return [c["t"] for c in env.transport.calls]


def test_A14a_spacing_on_one_host(env):
    env.script_ok(D17, D18, D19)
    env.make().run(rr(D17, D19))
    t = _starts(env)
    assert t[0] == 0 and env.sleeper.sleeps == pytest.approx([0.35, 0.35])
    assert all(b - a >= 0.35 - 1e-9 for a, b in zip(t, t[1:]))


def test_A14b_hosts_are_independent(env):
    hosts = {D17: "h1.test", D18: "h2.test", D19: "h1.test"}
    for k, h in hosts.items():
        env.transport.set(url_of(k, h), resp(200, CSV))
    c = env.make(demo_class(url_fn=lambda k: url_of(k, hosts[k])))
    c.run(rr(D17, D19))
    assert _starts(env)[:2] == [0, 0]                       # first request per host never waits
    assert env.sleeper.sleeps == pytest.approx([0.35])      # only the 2nd h1 request waits


def test_A14c_host_override_and_case(tmp_path):
    from _collector_helpers import Env
    e = Env(tmp_path, hosts={"example.test": 1.0})
    for k in (D17, D18):
        e.transport.set(f"https://EXAMPLE.test/f/{k}.csv", resp(200, CSV))
    e.make(demo_class(url_fn=lambda k: f"https://EXAMPLE.test/f/{k}.csv")).run(rr(D17, D18))
    assert e.sleeper.sleeps == pytest.approx([1.0])         # hostname lowercased before lookup


@pytest.mark.planted
def test_A15_retry_spacing_is_max_of_interval_and_backoff(env):
    env.transport.set(url_of(D17), resp(503, b"x"), resp(503, b"x"), resp(200, CSV))
    env.make().run(rr(D17))
    t = _starts(env)
    assert t[1] - t[0] >= 1.0 and t[2] - t[1] >= 2.0


@pytest.mark.planted
def test_A15b_retry_obeys_larger_rate_interval(tmp_path):
    from _collector_helpers import Env
    e = Env(tmp_path, hosts={"example.test": 3.0})
    e.transport.set(url_of(D17), resp(503, b"x"), resp(200, CSV))
    e.make().run(rr(D17))
    t = _starts(e)
    assert t[1] - t[0] >= 3.0
