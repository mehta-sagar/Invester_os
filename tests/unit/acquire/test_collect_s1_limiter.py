"""S1-T1: cross-process rate limiter, interval floors, daily caps (R27-R29; A47-A51)."""
from __future__ import annotations

import threading
import time
from datetime import timedelta
from pathlib import Path

import pytest

from _s1_helpers import (CSV, D17, D18, D19, EPOCH0, HOST, SOURCE, TODAY, S1Env, bresp, cfg_access, demo_class,
                         events, finish, get_result, limiter_worker, logs, mode_cls, resp, rr, scoped_env, spawn_collect,
                         url_of, CTX)

KEYS5 = ["2026-11-17", "2026-11-18", "2026-11-19", "2026-11-20", "2026-11-21"]


class FW:
    """Fake wall + monotonic clock that advance only through the sleeper."""
    def __init__(self, t=EPOCH0):
        self.t = t
        self.m = 0.0
        self.sleeps: list[float] = []

    def wall(self):
        return self.t

    def mono(self):
        return self.m

    def sleep(self, s):
        self.sleeps.append(s)
        self.t += s
        self.m += s


def limiter(tmp_path, fw):
    from investos.acquire.access import HostLimiter
    return HostLimiter(tmp_path / ".state", wall_clock=fw.wall, monotonic=fw.mono, sleeper=fw.sleep)


def last_file(tmp_path, host="h") -> Path:
    return tmp_path / ".state" / "ratelimit" / f"{host}.last"


def write_stamp(tmp_path, t, host="h", text=None):
    p = last_file(tmp_path, host)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(text if text is not None else str(int(round(t * 1e9))))


# ------------------------------------------------------------------ A47 (R27)
def test_A47_waits_for_the_remainder_of_the_interval(tmp_path):
    fw = FW(EPOCH0 + 0.4)
    write_stamp(tmp_path, EPOCH0)
    limiter(tmp_path, fw).acquire("h", 1.0)
    assert fw.sleeps == pytest.approx([0.6], abs=1e-6)
    assert int(last_file(tmp_path).read_text()) / 1e9 == pytest.approx(fw.t, abs=1e-6)      # stamp = wall after sleep


def test_A47_first_request_does_not_wait_and_stamps(tmp_path):
    fw = FW()
    limiter(tmp_path, fw).acquire("h", 1.0)
    assert fw.sleeps == []
    assert int(last_file(tmp_path).read_text()) / 1e9 == pytest.approx(EPOCH0, abs=1e-6)


@pytest.mark.planted
def test_A47_consecutive_acquires_are_spaced_by_the_interval(tmp_path):
    fw = FW()
    lim = limiter(tmp_path, fw)
    starts = []
    for _ in range(4):
        lim.acquire("h", 0.5)
        starts.append(fw.t)
    assert all(b - a >= 0.5 - 1e-9 for a, b in zip(starts, starts[1:]))


def test_A47_no_wait_when_interval_already_elapsed(tmp_path):
    fw = FW(EPOCH0 + 10)
    write_stamp(tmp_path, EPOCH0)
    limiter(tmp_path, fw).acquire("h", 1.0)
    assert fw.sleeps == []


@pytest.mark.planted
def test_A48_hosts_are_independent(tmp_path):
    fw = FW()
    lim = limiter(tmp_path, fw)
    lim.acquire("h1", 2.5)
    lim.acquire("h2", 2.5)
    assert fw.sleeps == []


@pytest.mark.planted
def test_A48_two_processes_never_start_closer_than_the_interval(tmp_path):
    """Spacing is asserted on the limiter's own stamps (exact), real return times only against a loose floor (D2)."""
    interval = 0.2
    q = CTX.Queue()
    ps = [CTX.Process(target=limiter_worker, args=(str(tmp_path / ".state"), "h", 5, interval, q)) for _ in range(2)]
    for p in ps:
        p.start()
    res = [get_result(q, ps) for _ in ps]
    for p in ps:
        p.join(30)
        assert p.exitcode == 0
    stamps = sorted(t for _, st in res for t in st)
    assert len(stamps) == 10
    gaps = [b - a for a, b in zip(stamps, stamps[1:])]
    assert min(gaps) >= interval - 0.002, gaps                                      # exact: scheduling cannot shrink it
    starts = sorted(t for st, _ in res for t in st)
    assert min(b - a for a, b in zip(starts, starts[1:])) >= interval * 0.5          # loose: CPU load only adds jitter


@pytest.mark.planted
def test_D2_slow_fsync_cannot_shrink_real_spacing(tmp_path, monkeypatch):
    """The stamp must be taken when the request may start (after the stamp file is durable), otherwise a slow
    write/fsync of the previous holder eats into the next gap."""
    import os
    interval = 0.3
    from investos.acquire.access import HostLimiter
    lim = HostLimiter(tmp_path / ".state", wall_clock=time.time, monotonic=time.monotonic, sleeper=time.sleep)
    real = os.fsync
    delays = iter([0.2, 0.0, 0.0])

    def slow_fsync(fd):
        time.sleep(next(delays, 0.0))
        return real(fd)
    monkeypatch.setattr(os, "fsync", slow_fsync)
    starts = []
    for _ in range(3):
        lim.acquire("h", interval)
        starts.append(time.time())
    gaps = [b - a for a, b in zip(starts, starts[1:])]
    assert min(gaps) >= interval - 0.03, gaps


@pytest.mark.planted
def test_A48_lock_released_when_the_holder_raises(tmp_path):
    fw = FW(EPOCH0 + 0.1)
    write_stamp(tmp_path, EPOCH0)
    from investos.acquire.access import HostLimiter

    def boom(s):
        raise RuntimeError("sleeper died")
    bad = HostLimiter(tmp_path / ".state", wall_clock=fw.wall, monotonic=fw.mono, sleeper=boom)
    with pytest.raises(RuntimeError):
        bad.acquire("h", 1.0)
    done = []

    def later():
        limiter(tmp_path, FW(EPOCH0 + 5)).acquire("h", 1.0)
        done.append(True)
    t = threading.Thread(target=later, daemon=True)
    t.start()
    t.join(5)
    assert done == [True], "a later acquire is stuck: lock leaked"


# ------------------------------------------------------------------ A49 (R27)
@pytest.mark.planted
def test_A49_stamp_in_the_future_never_waits_more_than_the_interval(tmp_path):
    fw = FW(EPOCH0)
    write_stamp(tmp_path, EPOCH0 + 3600)
    limiter(tmp_path, fw).acquire("h", 2.5)
    assert len(fw.sleeps) <= 1 and all(0 <= s <= 2.5 + 1e-9 for s in fw.sleeps)


@pytest.mark.planted
@pytest.mark.parametrize("text", ["", "abc", "1.5e3", "-", "12 34"])
def test_A49_unreadable_stamp_waits_the_full_interval(tmp_path, text):
    fw = FW(EPOCH0)
    write_stamp(tmp_path, 0, text=text)
    limiter(tmp_path, fw).acquire("h", 2.5)
    assert fw.sleeps == pytest.approx([2.5])


@pytest.mark.planted
def test_A49_wall_clock_stepping_backwards_still_spaces_by_monotonic(tmp_path):
    fw = FW(EPOCH0)
    lim = limiter(tmp_path, fw)
    lim.acquire("h", 2.5)
    m1 = fw.m
    fw.t -= 100                                                                     # wall steps backwards
    lim.acquire("h", 2.5)
    assert fw.m - m1 >= 2.5 - 1e-9                                                  # monotonic start gap >= interval
    assert fw.sleeps and max(fw.sleeps) <= 2.5 + 1e-9


@pytest.mark.planted
def test_A49_larger_of_wall_and_monotonic_wait_is_used(tmp_path):
    fw = FW(EPOCH0)
    lim = limiter(tmp_path, fw)
    lim.acquire("h", 2.5)
    fw.m += 1.0                                                                     # 1 s elapsed monotonic, 0 wall (clock stalled)
    lim.acquire("h", 2.5)
    assert fw.sleeps == pytest.approx([2.5])                                        # wall rule says 2.5, mono says 1.5


# ------------------------------------------------------------------ collector wiring (R27, R28)
@pytest.mark.planted
def test_collector_uses_the_shared_limiter_across_instances(tmp_path):
    e = S1Env(tmp_path)
    e.script_ok(D17, D18)
    e.make().run(rr(D17))
    assert (e.ledger / ".state" / "ratelimit" / f"{HOST}.last").exists()
    e.sleeper.sleeps.clear()
    e.make(run_id="r2").run(rr(D18))                                                # new instance, no in-process memory
    assert e.sleeper.sleeps == pytest.approx([0.35])


def _spacing(e, cls, keys=(D17, D18), browser=False):
    if browser:
        e.browser.set(bresp(200, CSV, url=url_of(D17)))
        for k in keys:
            e.browser.set(bresp(200, CSV, url=url_of(k)), url=url_of(k))
    else:
        e.script_ok(*keys)
    e.make(cls).run(rr(keys[0], keys[-1]))
    return e.sleeper.sleeps


@pytest.mark.planted
def test_A50_real_browser_floor_is_2_5_seconds(tmp_path):
    e = S1Env(tmp_path, hosts={HOST: 0.35}, access=cfg_access("real_browser"))
    assert _spacing(e, mode_cls("real_browser"), browser=True) == pytest.approx([2.5])


@pytest.mark.planted
def test_A50_scoped_exception_floor_is_2_5_seconds(tmp_path):
    e = scoped_env(tmp_path, hosts={HOST: 0.35})
    assert _spacing(e, mode_cls("scoped_exception")) == pytest.approx([2.5])


def test_A50_floor_never_lowers_a_larger_configured_interval(tmp_path):
    e = S1Env(tmp_path, hosts={HOST: 5.0}, access=cfg_access("real_browser"))
    assert _spacing(e, mode_cls("real_browser"), browser=True) == pytest.approx([5.0])


def test_A50_honest_interval_unchanged(tmp_path):
    e = S1Env(tmp_path, hosts={HOST: 0.35})
    assert _spacing(e, demo_class()) == pytest.approx([0.35])
    e2 = S1Env(tmp_path / "b", hosts={HOST: 1.0})
    assert _spacing(e2, demo_class()) == pytest.approx([1.0])


def test_A50_example_config_loads_with_nse_bse_intervals(tmp_path):
    from investos.core.config import load_config
    ex = Path(__file__).resolve().parents[3] / "config" / "investos.example.toml"
    root = tmp_path / "repo"
    for d in ("data/raw", "data/manifests", "data/canonical", "data/ledger", "reports", "registry", "db/migrations",
              "config"):
        (root / d).mkdir(parents=True)
    (root / "pyproject.toml").write_text("[project]\nname='x'\n")
    (root / "config" / "investos.toml").write_text(ex.read_text())
    cfg = load_config(root / "config" / "investos.toml", repo_root=root, env={})
    rl = cfg.rate_limits
    assert rl.interval_for("nsearchives.nseindia.com") == 1.0
    for h in ("www.nseindia.com", "www.nseindia.in", "api.bseindia.com"):
        assert rl.interval_for(h) == 2.5
    assert rl.interval_for("www.bseindia.com") == 0.35
    assert cfg.collect.contact == "CHANGE-ME@example.invalid" and dict(cfg.collect.access) == {}


# ------------------------------------------------------------------ A51 (R29)
def cap_env(tmp_path, cap=3, **kw):
    return S1Env(tmp_path, access={SOURCE: {"mode": "honest", "daily_cap": cap}}, **kw)


def keyed(keys):
    return demo_class(fixed_keys=list(keys))


@pytest.mark.planted
def test_A51_cap_stops_the_run_without_a_block(tmp_path, logs):
    from investos.acquire.base import Outcome
    e = cap_env(tmp_path)
    e.script_ok(*KEYS5)
    r = e.make(keyed(KEYS5)).run(rr(KEYS5[0], KEYS5[-1]))
    assert e.transport.n() == 3 and r.http_calls == 3 and r.status == "aborted"
    assert [r.outcomes[k] for k in KEYS5[:3]] == [Outcome.FETCHED] * 3
    for k in KEYS5[3:]:
        assert e.gaps(r)[k]["outcome"] == "not_attempted" and e.reasons(r)[k] == "daily cap 3 reached"
    assert not (e.ledger / "access").exists() or not list((e.ledger / "access").rglob("*.jsonl"))
    assert e.count_file().read_text().strip() == "3"
    w = events(logs(), "cap_reached", "WARNING")
    assert w and w[0]["fields"]["cap"] == 3 and w[0]["fields"]["date"] == TODAY


@pytest.mark.planted
def test_A51_a_retry_uses_two_of_the_cap(tmp_path):
    e = cap_env(tmp_path)
    e.transport.set(url_of(D17), resp(503, b"busy"), resp(200, CSV))
    e.script_ok(D18, D19)
    r = e.make().run(rr(D17, D19))
    assert e.transport.n() == 3 and r.status == "aborted"
    assert e.reasons(r)[D19] == "daily cap 3 reached"


@pytest.mark.planted
def test_A51_skipped_keys_do_not_count(tmp_path):
    e = cap_env(tmp_path, cap=2)
    e.script_ok(D17, D18)
    e.make().run(rr(D17, D18))
    e.fresh_transport()
    r = e.make(run_id="r2").run(rr(D17, D18))
    assert r.http_calls == 0 and r.status == "complete" and e.count_file().read_text().strip() == "2"


@pytest.mark.planted
def test_A51_second_process_same_day_makes_zero_attempts_next_day_proceeds(tmp_path):
    e = cap_env(tmp_path, interval=0.01)
    e.script_ok(*KEYS5[:3])
    e.make(keyed(KEYS5[:3])).run(rr(KEYS5[0], KEYS5[2]))                              # uses all 3
    new_keys = ["2026-12-01", "2026-12-02"]
    p, q = spawn_collect({"tmp": str(tmp_path), "keys": new_keys, "now": e.now.isoformat(), "run_id": "c1",
                          "range": (new_keys[0], new_keys[-1])})
    res = finish(p, q)
    assert res["calls"] == [] and res["status"] == "aborted"
    assert set(res["reasons"].values()) == {"daily cap 3 reached"}
    e.at_ist("2026-11-22")
    p, q = spawn_collect({"tmp": str(tmp_path), "keys": new_keys, "now": e.now.isoformat(), "run_id": "c2",
                          "range": (new_keys[0], new_keys[-1])})
    res = finish(p, q)
    assert len(res["calls"]) == 2 and res["status"] in ("complete", "incomplete")


@pytest.mark.planted
def test_A51_two_processes_together_never_exceed_the_cap(tmp_path):
    cap = 4
    e = cap_env(tmp_path, cap=cap, interval=0.01)
    ka = [f"2026-12-{i:02d}" for i in range(1, 7)]
    kb = [f"2027-01-{i:02d}" for i in range(1, 7)]
    ps = [spawn_collect({"tmp": str(tmp_path), "keys": ks, "now": e.now.isoformat(), "run_id": rid,
                         "range": (ks[0], ks[-1])}) for rid, ks in (("ca", ka), ("cb", kb))]
    res = [finish(p, q) for p, q in ps]
    assert sum(len(r["calls"]) for r in res) == cap
    assert e.count_file().read_text().strip() == str(cap)


@pytest.mark.planted
@pytest.mark.parametrize("mode", ["real_browser", "scoped_exception"])
def test_A51_non_honest_adapter_without_daily_cap_fails_in_load_config(tmp_path, mode):
    from investos.core.config import ConfigError
    acc = {SOURCE: {"mode": mode, "decision": "0003"}}
    with pytest.raises(ConfigError) as ei:
        S1Env(tmp_path, access=acc)
    assert f"collect.access.{SOURCE}.daily_cap" in str(ei.value)


@pytest.mark.planted
@pytest.mark.parametrize("content", ["abc", "-1", "", "1.5", "3 3", " "])
def test_A51_corrupt_count_file_fails_closed(tmp_path, logs, content):
    e = cap_env(tmp_path)
    p = e.count_file()
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(content)
    e.script_ok(D17)
    r = e.make().run(rr(D17))
    assert e.transport.n() == 0 and r.status == "aborted"
    assert events(logs(), level="ERROR")


@pytest.mark.planted
def test_A51_cap_applies_to_browser_fetches(tmp_path):
    e = S1Env(tmp_path, access={SOURCE: {"mode": "real_browser", "daily_cap": 2}})
    for k in (D17, D18, D19):
        e.browser.set(bresp(200, CSV, url=url_of(k)), url=url_of(k))
    r = e.make(mode_cls("real_browser")).run(rr(D17, D19))
    assert len(e.browser.urls) == 2 and e.reasons(r)[D19] == "daily cap 2 reached"


def test_A51_cap_optional_for_honest_means_unbounded(tmp_path):
    e = S1Env(tmp_path)
    e.script_ok(*KEYS5)
    r = e.make(keyed(KEYS5)).run(rr(KEYS5[0], KEYS5[-1]))
    assert r.status == "complete" and e.transport.n() == 5


# ------------------------------------------------------------------ DailyCounter direct (R29)
def counter(tmp_path, e, source=SOURCE):
    from investos.acquire.access import DailyCounter
    return DailyCounter(tmp_path / "ledger" / ".state", source, clock=e.clock)


def test_R29_reserve_counts_until_cap_then_refuses(tmp_path):
    e = S1Env(tmp_path)
    c = counter(tmp_path, e)
    assert [c.reserve(2), c.reserve(2), c.reserve(2)] == [True, True, False]
    assert e.count_file().read_text().strip() == "2"
    assert c.reserve(None) is True


def test_R29_new_ist_day_starts_at_zero_and_sources_are_independent(tmp_path):
    e = S1Env(tmp_path)
    c = counter(tmp_path, e)
    assert c.reserve(1) and not c.reserve(1)
    assert counter(tmp_path, e, "src_b").reserve(1)
    e.at_ist("2026-11-22", "00:00:00")
    assert c.reserve(1)
    assert e.count_file(date_s="2026-11-22").exists()


def test_R29_ist_day_boundary_not_utc(tmp_path):
    e = S1Env(tmp_path)
    c = counter(tmp_path, e)
    e.at_ist(TODAY, "23:59:59")                                                       # 18:29:59 UTC, still TODAY in IST
    assert c.reserve(1)
    e.at_ist("2026-11-22", "00:00:01")                                                # 18:30:01 UTC
    assert c.reserve(1)
