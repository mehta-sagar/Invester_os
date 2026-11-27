"""S1-T1: block policy, access state files, halt, per-host scope (R24-R26, R32; A40-A46, A54, A56)."""
from __future__ import annotations

import json

import pytest

from _s1_helpers import (CSV, D17, D18, D19, HOST, NOW, SOURCE, TODAY, S1Env, bresp, cfg_access, demo_class,  # noqa: F401
                         event_line, events, finish, logs, mode_cls, resp, rr, scoped_env, spawn_collect,
                         torn_writer, url_of, write_decision, append_line, CTX)

CANON_KEYS = {"schema", "ts", "date", "event", "source", "host", "detail", "run_id", "decision"}
SIG = "Pardon Our Interruption"


def blocked_run(e, status=403, cls=None, body=b"denied", **kw):
    e.transport.set(url_of(D17), resp(status, body))
    e.script_ok(D18, D19)
    return e.make(cls, **kw).run(rr(D17, D19))


def day(n: int) -> str:
    return f"2026-11-{n:02d}"


# ------------------------------------------------------------------ A40 (R24)
@pytest.mark.planted
@pytest.mark.parametrize("status", [401, 403, 429, 451])
def test_A40_block_statuses_stop_the_run_and_persist(tmp_path, status):
    from investos.acquire.base import Outcome
    e = S1Env(tmp_path)
    r = blocked_run(e, status)
    assert e.transport.n() == 1 and r.http_calls == 1 and e.sleeper.sleeps == []     # no retry, 429 included
    assert r.outcomes == {D17: Outcome.UNREADABLE}
    assert e.reasons(r)[D17] == f"blocked http {status}"
    gaps = e.gaps(r)
    assert gaps[D18]["outcome"] == gaps[D19]["outcome"] == "not_attempted"
    assert r.status == "aborted" and e.sidecar(r)["status"] == "aborted"
    evs = e.host_events()
    assert len(evs) == 1
    ev = evs[0]
    assert set(ev) == CANON_KEYS
    assert (ev["event"], ev["host"], ev["source"], ev["run_id"], ev["date"], ev["schema"], ev["decision"]) == \
        ("blocked", HOST, SOURCE, "run-1", TODAY, 1, None)
    assert ev["ts"].endswith("Z") and len(ev["ts"].split(".")[1]) == 7 and ev["ts"].startswith("2026-11-21T09:30:00")
    line = e.host_file().read_text().splitlines()[0]
    assert line == json.dumps(ev, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    assert e.host_file().read_bytes().endswith(b"\n")
    assert e.manifest_lines() == [] and e.raw_files() == []


@pytest.mark.planted
def test_A40_nothing_else_is_a_block_status(tmp_path):
    """404 / 410 / 503 are not blocks: no state file, run continues."""
    e = S1Env(tmp_path)
    e.transport.set(url_of(D17), resp(404, b"nope"))
    e.script_ok(D18)
    e.make().run(rr(D17, D18))
    assert e.transport.n(url_of(D18)) == 1 and not e.host_file().exists()


@pytest.mark.planted
def test_A40_blocked_event_written_before_anything_else_and_no_identity_change(tmp_path):
    e = S1Env(tmp_path)
    blocked_run(e, 403)
    assert len({tuple(sorted(c["headers"].items())) for c in e.transport.calls}) == 1


# ------------------------------------------------------------------ A41 (R24 signatures)
def sig_env(tmp_path, sigs=(SIG,)):
    return S1Env(tmp_path, access={SOURCE: {"mode": "honest", "challenge_signatures": list(sigs)}})


def counting_cls():
    seen = []

    def v(self, key, r):
        seen.append(key)
        from _collector_helpers import default_check
        return default_check(self, key, r)
    return demo_class(verdict_fn=v), seen


@pytest.mark.planted
def test_A41_challenge_page_on_200_blocks(tmp_path):
    from investos.acquire.base import Outcome
    e = sig_env(tmp_path)
    cls, seen = counting_cls()
    e.transport.set(url_of(D17), resp(200, f"<html>{SIG}</html>".encode()))
    e.script_ok(D18)
    r = e.make(cls).run(rr(D17, D18))
    assert r.outcomes == {D17: Outcome.UNREADABLE} and e.reasons(r)[D17] == f"challenge page: {SIG}"
    assert r.status == "aborted" and seen == [] and e.transport.n() == 1
    assert [x["event"] for x in e.host_events()] == ["blocked"]
    assert e.gaps(r)[D18]["outcome"] == "not_attempted"


@pytest.mark.planted
def test_A41_only_first_256_kib_examined(tmp_path):
    from investos.acquire.base import Outcome
    e = sig_env(tmp_path)
    pad = b"x" * (262144 - len(CSV))
    e.transport.set(url_of(D17), resp(200, CSV + pad + SIG.encode()))                # starts at byte 262144
    r = e.make().run(rr(D17))
    assert r.outcomes == {D17: Outcome.FETCHED} and not e.host_file().exists()


@pytest.mark.planted
def test_A41_signature_inside_first_256_kib_blocks(tmp_path):
    e = sig_env(tmp_path)
    pad = b"x" * (262144 - len(CSV) - len(SIG))
    e.transport.set(url_of(D17), resp(200, CSV + pad + SIG.encode()))                # ends exactly at byte 262144
    r = e.make().run(rr(D17))
    assert e.reasons(r)[D17] == f"challenge page: {SIG}"


def test_A41_adapter_without_signatures_never_blocks_on_200(tmp_path):
    from investos.acquire.base import Outcome
    e = S1Env(tmp_path)
    e.transport.set(url_of(D17), resp(200, CSV + SIG.encode()))
    assert e.make().run(rr(D17)).outcomes == {D17: Outcome.FETCHED}
    assert not e.host_file().exists()


@pytest.mark.planted
def test_A41_signature_on_404_blocks(tmp_path):
    e = sig_env(tmp_path)
    e.transport.set(url_of(D17), resp(404, SIG.encode()))
    r = e.make().run(rr(D17))
    assert e.reasons(r)[D17] == f"challenge page: {SIG}" and r.status == "aborted" and len(e.host_events()) == 1


@pytest.mark.planted
def test_A41_case_sensitive_and_second_signature_matches(tmp_path):
    from investos.acquire.base import Outcome
    e = sig_env(tmp_path, ["Access Denied", SIG])
    e.transport.set(url_of(D17), resp(200, CSV + SIG.lower().encode()))
    assert e.make().run(rr(D17)).outcomes == {D17: Outcome.FETCHED}
    e2 = sig_env(tmp_path / "b", ["Access Denied", SIG])
    e2.transport.set(url_of(D17), resp(200, SIG.encode()))
    assert e2.reasons(e2.make().run(rr(D17)))[D17] == f"challenge page: {SIG}"


@pytest.mark.planted
def test_A41_signature_checked_before_undecoded_encoding(tmp_path):
    e = sig_env(tmp_path)
    e.transport.set(url_of(D17), resp(200, SIG.encode(), headers={"content-encoding": "br"}))
    assert e.reasons(e.make().run(rr(D17)))[D17] == f"challenge page: {SIG}"


# ------------------------------------------------------------------ A42 (R24, R31)
@pytest.mark.planted
def test_A42_block_holds_for_the_ist_day_for_new_instances(tmp_path):
    from investos.acquire.base import Outcome
    e = S1Env(tmp_path)
    e.script_ok(D17)
    e.make().run(rr(D17))                                                            # D17 stored
    e.fresh_transport()
    e.transport.set(url_of(D18), resp(403, b"denied"))
    e.make(run_id="run-2").run(rr(D17, D19))                                         # D18 blocks
    n_events = len(e.host_events())
    e.fresh_transport()
    r = e.make(run_id="run-3").run(rr(D17, D19))
    assert e.transport.n() == 0 and r.http_calls == 0
    assert r.outcomes[D17] is Outcome.SKIPPED
    assert e.gaps(r)[D18]["outcome"] == e.gaps(r)[D19]["outcome"] == "not_attempted"
    assert e.reasons(r)[D18] == f"host {HOST} blocked for IST day {TODAY}" == e.reasons(r)[D19]
    assert r.status == "aborted" and e.sidecar(r)["status"] == "aborted"
    assert len(e.host_events()) == n_events


@pytest.mark.planted
def test_A42_block_ends_at_ist_midnight_and_success_writes_nothing(tmp_path):
    e = S1Env(tmp_path)
    e.plant_block()                                                                  # today (15:00 IST)
    e.at_ist(TODAY, "23:59:59")
    r = e.make().run(rr(D17))
    assert r.http_calls == 0 and r.status == "aborted"
    before = e.host_file().read_bytes()
    e.fresh_transport()
    e.script_ok(D17)
    e.at_ist("2026-11-22", "00:00:00")
    r = e.make(run_id="run-2").run(rr(D17))
    assert r.outcomes[D17].value == "fetched" and e.transport.n() == 1
    assert e.host_file().read_bytes() == before                                      # success writes no event


@pytest.mark.planted
def test_A42_block_recorded_just_after_midnight_belongs_to_new_day(tmp_path):
    e = S1Env(tmp_path)
    e.at_ist("2026-11-22", "00:00:01")
    blocked_run(e, 403)
    assert e.host_events()[0]["date"] == "2026-11-22"
    e.fresh_transport()
    e.at_ist("2026-11-22", "23:59:59")
    assert e.make(run_id="r2").run(rr(D17)).http_calls == 0


@pytest.mark.planted
def test_A42_skipped_keys_stay_skipped_when_blocked_halted_or_capped(tmp_path):
    """R31: manifest hit comes before the access check."""
    e = S1Env(tmp_path, access={SOURCE: {"mode": "honest", "daily_cap": 1}})
    e.script_ok(D17, D18)
    e.make().run(rr(D17))                                                            # stores D17, uses the cap
    e.plant_block()
    e.plant_halt()
    e.fresh_transport()
    r = e.make(run_id="r2").run(rr(D17))
    assert r.http_calls == 0 and r.outcomes == {D17: __import__("investos.acquire.base", fromlist=["Outcome"]).Outcome.SKIPPED}
    assert r.status == "complete"


@pytest.mark.planted
def test_A42_state_reread_before_every_attempt_including_retries(tmp_path):
    e = S1Env(tmp_path)
    e.transport.set(url_of(D17), resp(503, b"busy"), resp(200, CSV))

    def hook(secs):
        e.sleeper(secs)
        if secs == 1:
            e.plant_block(source="src_other")                      # another process blocks the host during backoff
    r = e.make(sleeper=hook).run(rr(D17))
    assert e.transport.n() == 1 and r.http_calls == 1               # the retry never went out
    assert r.status == "aborted" and e.gaps(r)[D17]["outcome"] in ("not_attempted", "unreadable")


# ------------------------------------------------------------------ A43 (R24, R25 cross-process)
def test_A43_second_process_after_a_block_makes_zero_calls(tmp_path):
    e = S1Env(tmp_path, interval=0.01)
    blocked_run(e, 403)
    p, q = spawn_collect({"tmp": str(tmp_path), "keys": [D18, D19], "now": e.now.isoformat(), "run_id": "child-1",
                          "range": (D18, D19)})
    res = finish(p, q)
    assert res["calls"] == [] and res["status"] == "aborted"
    assert all(v.startswith(f"host {HOST} blocked for IST day {TODAY}") for v in res["reasons"].values())


@pytest.mark.planted
def test_A43_process_mid_run_sees_a_block_written_by_another_process(tmp_path):
    e = S1Env(tmp_path, interval=0.01)
    keys = ["2026-12-01", "2026-12-02", "2026-12-03", "2026-12-04", "2026-12-05"]
    reached, go = CTX.Event(), CTX.Event()
    p, q = spawn_collect({"tmp": str(tmp_path), "keys": keys, "now": e.now.isoformat(), "run_id": "child-2",
                          "range": (keys[0], keys[-1]), "hold_key": keys[1], "reached": reached, "go": go})
    assert reached.wait(60), "child never reached key 2"
    e.transport.set(url_of("2026-11-17"), resp(403, b"denied"))                      # process 1: receives a 403
    e.make(run_id="parent").run(rr("2026-11-17"))
    assert e.host_events()
    go.set()
    res = finish(p, q)
    assert [u.rsplit("/", 1)[1][:-4] for u in res["calls"]] == keys[:2]              # never requested key 3
    assert res["status"] == "aborted"
    assert all(res["outcomes"].get(k) is None for k in keys[2:]) and set(res["reasons"]) >= set(keys[2:])


# ------------------------------------------------------------------ A44 (R25)
def good_line(kind: str) -> dict:
    if kind == "host":
        return json.loads(event_line("blocked", SOURCE, HOST, NOW))
    return json.loads(event_line("halted", SOURCE, None, NOW, detail="3 blocked days in 7"))


def dumps(o) -> bytes:
    return (json.dumps(o, sort_keys=True, separators=(",", ":"), ensure_ascii=False) + "\n").encode()


def _mut(kind):
    g = good_line(kind)
    nodetail = {k: v for k, v in g.items() if k != "detail"}
    other = good_line("source" if kind == "host" else "host")
    cases = {
        "invalid_json": b"{not json}\n",
        "missing_field": dumps(nodetail),
        "extra_field": dumps({**g, "extra": 1}),
        "unknown_event": dumps({**g, "event": "weird"}),
        "schema_2": dumps({**g, "schema": 2}),
        "wrong_date": dumps({**g, "date": "2020-01-01"}),
        "wrong_kind_for_file": dumps(other),
        "blank_line": dumps(g) + b"\n" + dumps(g),
        "torn_tail": dumps(g)[:-1],
        "not_an_object": b"[1,2]\n",
    }
    if kind == "host":
        cases["host_null_in_host_file"] = dumps({**g, "host": None})
    else:
        cases["host_set_in_source_file"] = dumps({**g, "host": HOST})
    return cases


BAD_CASES = [(k, n) for k in ("host", "source") for n in _mut(k)]


@pytest.mark.planted
@pytest.mark.parametrize("kind,name", BAD_CASES)
def test_A44_bad_state_file_fails_closed(tmp_path, logs, kind, name):
    from investos.acquire.access import AccessState, AccessStateError
    e = S1Env(tmp_path)
    p = e.host_file() if kind == "host" else e.source_file()
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_bytes(_mut(kind)[name])
    with pytest.raises(AccessStateError):
        AccessState(e.cfg, SOURCE, clock=e.clock).status(HOST)
    e.script_ok(D17, D18)
    r = e.make().run(rr(D17, D18))
    assert e.transport.n() == 0 and r.http_calls == 0 and r.status == "aborted"
    assert e.reasons(r)[D17].startswith("access state unreadable")
    assert events(logs(), "access_state_unreadable", "ERROR")


@pytest.mark.planted
def test_A44_valid_state_file_reads_fine(tmp_path):
    from investos.acquire.access import AccessState
    e = S1Env(tmp_path)
    e.plant_block()
    st = AccessState(e.cfg, SOURCE, clock=e.clock).status(HOST)
    assert st.blocked_today is True and st.halted is False and st.today == TODAY


@pytest.mark.planted
def test_A44_writer_refuses_to_append_to_torn_tail(tmp_path):
    from investos.acquire.access import AccessState, AccessStateError
    e = S1Env(tmp_path)
    p = e.host_file()
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_bytes(dumps(good_line("host"))[:-1])
    before = p.read_bytes()
    with pytest.raises(AccessStateError):
        AccessState(e.cfg, SOURCE, clock=e.clock).record_block(host=HOST, detail="x", run_id="r")
    assert p.read_bytes() == before


@pytest.mark.planted
def test_A44_reader_waits_for_a_writer_mid_append(tmp_path):
    from investos.acquire.access import AccessState
    e = S1Env(tmp_path)
    p = e.host_file()
    p.parent.mkdir(parents=True, exist_ok=True)
    line = dumps(good_line("host")).decode().rstrip("\n")
    ready = CTX.Event()
    w = CTX.Process(target=torn_writer, args=(str(p), line, ready, 0.6))
    w.start()
    try:
        assert ready.wait(30)
        st = AccessState(e.cfg, SOURCE, clock=e.clock).status(HOST)                  # must wait, not raise
        assert st.blocked_today is True
    finally:
        w.join(30)
    assert w.exitcode == 0


def test_A44_record_block_creates_files_and_dirs(tmp_path):
    from investos.acquire.access import AccessState
    e = S1Env(tmp_path)
    ev = AccessState(e.cfg, SOURCE, clock=e.clock).record_block(host=HOST, detail="http 403", run_id="r1")
    assert ev.event == "blocked" and ev.host == HOST and ev.source == SOURCE and ev.date == TODAY
    assert e.host_events()[0]["detail"] == "http 403"


@pytest.mark.planted
@pytest.mark.parametrize("host", ["Bad_Host", "a/b", "", "-x.test", "x" * 254, "../evil"])
def test_A44_bad_host_name_refused_by_state(tmp_path, host):
    from investos.acquire.access import AccessState, AccessStateError
    e = S1Env(tmp_path)
    with pytest.raises((ValueError, AccessStateError)):
        AccessState(e.cfg, SOURCE, clock=e.clock).record_block(host=host, detail="x", run_id="r")
    assert not list((e.ledger).rglob("*.jsonl"))


# ------------------------------------------------------------------ A45 (R26)
def _state(e, source=SOURCE):
    from investos.acquire.access import AccessState
    return AccessState(e.cfg, source, clock=e.clock)


def _block_days(e, days, source=SOURCE):
    st = _state(e, source)
    for d in days:
        e.at_ist(day(d))
        st.record_block(host=HOST, detail="http 403", run_id="r")
    return st


def _halted_events(e):
    return [x for x in e.source_events() if x["event"] == "halted"]


@pytest.mark.planted
def test_A45_three_blocked_days_in_seven_halt(tmp_path):
    e = S1Env(tmp_path)
    st = _block_days(e, [1, 4])
    assert not _halted_events(e)
    _block_days(e, [7])
    h = _halted_events(e)
    assert len(h) == 1 and h[0]["detail"] == "3 blocked days in 7" and h[0]["host"] is None and h[0]["source"] == SOURCE
    assert st.status(HOST).halted is True


@pytest.mark.planted
def test_A45_halted_adapter_makes_zero_calls_even_after_the_day_block_ends(tmp_path):
    e = S1Env(tmp_path)
    _block_days(e, [1, 4, 7])
    e.at_ist(day(8))
    e.script_ok(D17)
    r = e.make().run(rr(D17))
    assert r.http_calls == 0 and r.status == "aborted"
    assert e.reasons(r)[D17].startswith("adapter halted")
    e.at_ist(day(20))
    assert e.make(run_id="r2").run(rr(D17)).http_calls == 0                          # any day, until cleared


@pytest.mark.planted
def test_A45_window_is_seven_dates_ending_today(tmp_path):
    e = S1Env(tmp_path / "a")
    _block_days(e, [1, 4, 8])                                                        # day 1 is outside days 2..8
    assert not _halted_events(e)
    e2 = S1Env(tmp_path / "b")
    _block_days(e2, [2, 5, 8])                                                       # days 2..8 inclusive
    assert len(_halted_events(e2)) == 1


@pytest.mark.planted
def test_A45_two_blocks_on_one_day_count_once(tmp_path):
    e = S1Env(tmp_path)
    _block_days(e, [1, 1, 4])
    assert not _halted_events(e)
    _block_days(e, [4, 5])
    assert not _halted_events(e)
    _block_days(e, [7])
    assert len(_halted_events(e)) == 1


@pytest.mark.planted
def test_A45_another_sources_blocks_do_not_count(tmp_path):
    e = S1Env(tmp_path)
    _block_days(e, [1, 4], source="src_b")
    _block_days(e, [7], source=SOURCE)
    assert not _halted_events(e) and not e.source_events("src_b")
    assert _state(e).status(HOST).halted is False and _state(e, "src_b").status(HOST).halted is False


def test_A45_halted_event_not_appended_twice(tmp_path):
    e = S1Env(tmp_path)
    _block_days(e, [1, 4, 7])
    _block_days(e, [8])
    assert len(_halted_events(e)) == 1


# ------------------------------------------------------------------ A46 (R26, R33)
def make_halted(tmp_path, notifier=None, logs_cap=None):
    e = S1Env(tmp_path)
    notifier = notifier or e.notifier
    for d in (1, 4, 7):
        e.at_ist(day(d))
        e.fresh_transport()
        e.transport.set(url_of(D17), resp(403, b"denied"))
        e.make(run_id=f"run-{d}", notifier=notifier).run(rr(D17))
    return e


def decision(e, name="0004-clear.md", src=SOURCE):
    write_decision(e.cfg.repo_root, name, f"Clears-halt: {src}")


def clear(e, dec="0004"):
    from investos.acquire.access import clear_halt
    return clear_halt(e.cfg, SOURCE, decision=dec, clock=e.clock, run_id="clr-1")


@pytest.mark.planted
def test_A46_halt_notifies_exactly_once_naming_source_and_host(tmp_path, logs):
    e = make_halted(tmp_path)
    assert e.notifier.calls == [("Invest OS: source halted", f"{SOURCE} halted: 3 blocked days in 7 (host {HOST})")]
    blob = " ".join(" ".join(c) for c in e.notifier.calls)
    assert "InvestOS/" not in blob and "example.invalid" not in blob and "Accept-Encoding" not in blob
    assert events(logs(), "halted", "ERROR")


@pytest.mark.planted
def test_A46_failing_notifier_never_changes_the_outcome(tmp_path, logs):
    from investos.acquire.base import Outcome
    n = __import__("_s1_helpers").FakeNotifier(raises=RuntimeError("boom"))
    e = make_halted(tmp_path, notifier=n)
    assert len(_halted_events(e)) == 1 and len(n.calls) == 1
    assert events(logs(), "halted", "ERROR")                                         # logged even if the notifier fails
    e.at_ist(day(7))


@pytest.mark.planted
def test_A46_default_notifier_runs_no_command(tmp_path, monkeypatch):
    import subprocess

    def boom(*a, **k):
        raise AssertionError("subprocess.run called by a default collector")
    monkeypatch.setattr(subprocess, "run", boom)
    monkeypatch.setattr("sys.platform", "darwin")
    e = S1Env(tmp_path)
    for d in (1, 4, 7):
        e.at_ist(day(d))
        e.fresh_transport()
        e.transport.set(url_of(D17), resp(403, b"denied"))
        e.make(run_id=f"run-{d}").run(rr(D17))                                        # no notifier argument
    assert len(_halted_events(e)) == 1


@pytest.mark.planted
@pytest.mark.parametrize("case", ["no_file", "no_line", "two_files", "not_halted", "wrong_source"])
def test_A46_clear_halt_refusals_leave_state_untouched(tmp_path, case):
    if case == "not_halted":
        e = S1Env(tmp_path)
        decision(e)
        before = None
    else:
        e = make_halted(tmp_path)
        if case == "no_line":
            write_decision(tmp_path, "0004-clear.md", "no clearing line here")
        elif case == "two_files":
            decision(e, "0004-a.md")
            decision(e, "0004-b.md")
        elif case == "wrong_source":
            decision(e, src="other_src")
        before = e.source_file().read_bytes()
    with pytest.raises(ValueError):
        clear(e)
    if before is not None:
        assert e.source_file().read_bytes() == before
    else:
        assert not e.source_file().exists()


@pytest.mark.planted
def test_A46_valid_clear_resets_count_but_not_todays_host_block(tmp_path):
    e = make_halted(tmp_path)
    decision(e)
    ev = clear(e)
    assert ev.event == "cleared" and ev.decision == "0004"
    last = e.source_events()[-1]
    assert last["event"] == "cleared" and last["decision"] == "0004" and last["host"] is None
    assert _state(e).status(HOST).halted is False
    # the day-block recorded today (day 7) still holds until midnight
    e.fresh_transport()
    e.script_ok(D17)
    r = e.make(run_id="after-clear").run(rr(D17))
    assert r.http_calls == 0 and e.reasons(r)[D17] == f"host {HOST} blocked for IST day {day(7)}"
    # next IST day it runs
    e.at_ist(day(8))
    r = e.make(run_id="d8").run(rr(D17))
    assert r.outcomes[D17].value == "fetched"
    # two further blocked days do not re-halt: earlier blocks no longer count
    for d in (9, 11):
        e.at_ist(day(d))
        e.fresh_transport()
        e.transport.set(url_of(D18), resp(403, b"denied"))
        e.make(run_id=f"b{d}").run(rr(D18))
    assert len(_halted_events(e)) == 1


# ------------------------------------------------------------------ A54 (R32)
@pytest.mark.planted
def test_A54_blocked_log_line_has_status_host_source_and_no_header_values(tmp_path, logs):
    e = S1Env(tmp_path)
    blocked_run(e, 429)
    recs = events(logs(), "blocked", "ERROR")
    assert len(recs) == 1
    f = recs[0]["fields"]
    assert f["status"] == 429 and f["host"] == HOST and f["source"] == SOURCE and f["key"] == D17
    blob = json.dumps(logs())
    assert "InvestOS/" not in blob and "example.invalid" not in blob


@pytest.mark.planted
def test_A54_cookie_value_never_logged(tmp_path, logs):
    e = scoped_env(tmp_path)
    secret = "nsit=SECRETCOOKIEVALUE123"
    e.transport.set(url_of(D17), resp(403, b"denied"))
    e.make(mode_cls("scoped_exception", extra_headers={"Cookie": secret})).run(rr(D17))
    assert e.transport.calls[0]["headers"]["Cookie"] == secret
    assert "SECRETCOOKIEVALUE123" not in json.dumps(logs())
    assert "SECRETCOOKIEVALUE123" not in e.host_file().read_text()


@pytest.mark.planted
def test_A54_challenge_log_has_signature(tmp_path, logs):
    e = sig_env(tmp_path)
    e.transport.set(url_of(D17), resp(200, SIG.encode()))
    e.make().run(rr(D17))
    recs = events(logs(), "blocked", "ERROR")
    assert recs and recs[0]["fields"]["signature"] == SIG and recs[0]["fields"]["host"] == HOST


def test_A54_no_rate_wait_event(tmp_path, logs):
    e = S1Env(tmp_path)
    e.script_ok(D17, D18)
    e.make().run(rr(D17, D18))
    assert events(logs(), "rate_wait") == []


# ------------------------------------------------------------------ A56 (R24, R25, R26 per-host scope)
def host_cls(source, host):
    return demo_class(source=source, url_fn=lambda key, h=host: f"https://{h}/f/{key}.csv")


@pytest.mark.planted
def test_A56_block_on_one_adapter_stops_every_adapter_on_that_host_only(tmp_path):
    from investos.acquire.base import Outcome
    e = S1Env(tmp_path)
    A, B, C = host_cls("src_a", "h1.test"), host_cls("src_b", "h1.test"), host_cls("src_c", "h2.test")
    e.transport.set("https://h1.test/f/%s.csv" % D17, resp(403, b"denied"))
    r = e.make(A).run(rr(D17))
    assert r.status == "aborted"
    lines = (e.ledger / "access" / "hosts" / "h1.test.jsonl").read_text().splitlines()
    assert len(lines) == 1 and json.loads(lines[0])["source"] == "src_a"
    e.fresh_transport()
    e.transport.set("https://h1.test/f/%s.csv" % D17, resp(200, CSV))
    e.transport.set("https://h2.test/f/%s.csv" % D17, resp(200, CSV))
    rb = e.make(B, run_id="rb").run(rr(D17))
    assert rb.http_calls == 0 and e.reasons(rb)[D17] == f"host h1.test blocked for IST day {TODAY}"
    rc = e.make(C, run_id="rc").run(rr(D17))
    assert rc.outcomes == {D17: Outcome.FETCHED}
    assert not e.source_file("src_b").exists() and not e.source_file("src_a").exists()
    # src_a counts one blocked day, src_b none
    assert [x["source"] for x in e.host_events("h1.test")] == ["src_a"]


@pytest.mark.planted
def test_A56_block_is_checked_per_request_host(tmp_path):
    from investos.acquire.base import Outcome

    def url_fn(key):
        return f"https://{'h1.test' if key == D18 else 'h2.test'}/f/{key}.csv"
    cls = demo_class(source="src_c", url_fn=url_fn)
    e = S1Env(tmp_path)
    e.plant_block("h1.test", source="src_a")
    for k in (D17, D19):
        e.transport.set(f"https://h2.test/f/{k}.csv", resp(200, CSV))
    e.transport.set(f"https://h1.test/f/{D18}.csv", resp(200, CSV))
    r = e.make(cls).run(rr(D17, D19))
    assert r.outcomes[D17] is Outcome.FETCHED
    assert e.transport.n(f"https://h1.test/f/{D18}.csv") == 0 and e.transport.n(f"https://h2.test/f/{D19}.csv") == 0
    assert r.status == "aborted"
    assert e.gaps(r)[D18]["outcome"] == "not_attempted" == e.gaps(r)[D19]["outcome"]
    assert e.reasons(r)[D18] == f"host h1.test blocked for IST day {TODAY}"


@pytest.mark.planted
def test_A56_all_adapters_resume_at_ist_midnight(tmp_path):
    e = S1Env(tmp_path)
    e.plant_block("h1.test", source="src_a")
    e.at_ist(TODAY, "23:59:59")
    for src in ("src_a", "src_b"):
        e.fresh_transport()
        e.transport.set(f"https://h1.test/f/{D17}.csv", resp(200, CSV))
        assert e.make(host_cls(src, "h1.test"), run_id=src).run(rr(D17)).http_calls == 0
    e.at_ist("2026-11-22", "00:00:00")
    for src in ("src_a", "src_b"):
        e.fresh_transport()
        e.transport.set(f"https://h1.test/f/{D17}.csv", resp(200, CSV))
        r = e.make(host_cls(src, "h1.test"), run_id=src + "2").run(rr(D17))
        assert r.outcomes[D17].value == "fetched"


@pytest.mark.planted
def test_A56_browser_adapters_share_the_host_block(tmp_path):
    e = S1Env(tmp_path, access=cfg_access("real_browser"))
    e.plant_block(HOST, source="src_a")
    e.browser.set(bresp(200, CSV, url=url_of(D17)))
    r = e.make(mode_cls("real_browser")).run(rr(D17))
    assert e.browser.urls == [] and r.status == "aborted"


@pytest.mark.planted
def test_A56_browser_block_is_recorded_like_http(tmp_path):
    e = S1Env(tmp_path, access=cfg_access("real_browser"))
    e.browser.set(bresp(403, b"denied", url=url_of(D17)))
    r = e.make(mode_cls("real_browser")).run(rr(D17))
    assert e.reasons(r)[D17] == "blocked http 403" and len(e.host_events()) == 1 and len(e.browser.urls) == 1
