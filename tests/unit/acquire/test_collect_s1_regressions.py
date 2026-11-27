"""S1-T1 review regressions (reports/S1-T1/review.md D1-D5): fail-closed state writes, framing headers,
mutation survivors, header-phase deadline."""
from __future__ import annotations

import builtins
import json
import os
import ssl
import stat
import time
from dataclasses import replace
from pathlib import Path

import pytest

from _s1_helpers import (CSV, CTX, D17, D18, HOST, SOURCE, S1Env, cfg_access, counter_worker, demo_class, events,
                         get_result, http_transport, logs, mode_cls, resp, rr, scoped_env, server, url_of)


# ------------------------------------------------------------------ D1 fail closed when the block cannot be persisted
def _deny_writes_under(monkeypatch, root: Path):
    """Simulate an unwritable access state directory (works as root too)."""
    root = str(root)
    real_open, real_os_open = builtins.open, os.open

    def under(p):
        try:
            return os.fspath(p).startswith(root)
        except TypeError:
            return False

    def fake_open(file, mode="r", *a, **k):
        if under(file) and any(c in str(mode) for c in "wax+"):
            raise PermissionError(13, "read-only state dir", os.fspath(file))
        return real_open(file, mode, *a, **k)

    def fake_os_open(path, flags, *a, **k):
        if under(path) and flags & (os.O_WRONLY | os.O_RDWR | os.O_CREAT | os.O_APPEND | os.O_TRUNC):
            raise PermissionError(13, "read-only state dir", os.fspath(path))
        return real_os_open(path, flags, *a, **k)
    monkeypatch.setattr(builtins, "open", fake_open)
    monkeypatch.setattr(os, "open", fake_os_open)


@pytest.mark.planted
@pytest.mark.parametrize("status", [401, 403, 429])
def test_D1_unwritable_state_dir_block_still_stops_run_and_next_run_makes_no_call(tmp_path, monkeypatch, status):
    e = S1Env(tmp_path)
    (e.ledger / "access" / "hosts").mkdir(parents=True)
    _deny_writes_under(monkeypatch, e.ledger / "access")
    e.transport.set(url_of(D17), resp(status, b"denied"))
    e.script_ok(D18)
    r = e.make().run(rr(D17, D18))
    assert e.transport.n() == 1 and r.status == "aborted"                       # the block stops the run
    e.fresh_transport()
    e.script_ok(D17, D18)
    r2 = e.make(run_id="run-2").run(rr(D17, D18))
    assert e.transport.n() == 0 and r2.http_calls == 0 and r2.status == "aborted"   # fail closed


@pytest.mark.planted
@pytest.mark.skipif(os.geteuid() == 0, reason="chmod does not restrict root")
def test_D1_chmod_readonly_access_dir_fails_closed(tmp_path):
    e = S1Env(tmp_path)
    hosts = e.ledger / "access" / "hosts"
    hosts.mkdir(parents=True)
    try:
        for d in (hosts, hosts.parent):
            d.chmod(0o555)
        e.transport.set(url_of(D17), resp(403, b"denied"))
        e.make().run(rr(D17))
        e.fresh_transport()
        e.script_ok(D17)
        r2 = e.make(run_id="run-2").run(rr(D17))
        assert e.transport.n() == 0 and r2.status == "aborted"
    finally:
        for d in (hosts, hosts.parent):
            d.chmod(0o755)


@pytest.mark.planted
def test_D1_unwritable_state_dir_refuses_even_before_any_block(tmp_path, monkeypatch):
    """If the state cannot be locked/verified the adapter refuses to fetch at all."""
    e = S1Env(tmp_path)
    (e.ledger / "access" / "hosts").mkdir(parents=True)
    _deny_writes_under(monkeypatch, e.ledger / "access")
    e.script_ok(D17)
    r = e.make().run(rr(D17))
    assert e.transport.n() == 0 and r.status == "aborted"


# ------------------------------------------------------------------ D2 is in test_collect_s1_limiter.py

# ------------------------------------------------------------------ D3 framing headers
@pytest.mark.planted
@pytest.mark.parametrize("name", ["Connection", "connection", "CONTENT-LENGTH", "Content-Length", "Transfer-Encoding",
                                  "transfer_encoding", " Connection"])
def test_D3_framing_headers_refused_in_every_mode(tmp_path, name):
    with pytest.raises(ValueError):
        S1Env(tmp_path / "h").make(demo_class(extra_headers={name: "x"}))
    with pytest.raises(ValueError):
        scoped_env(tmp_path / "s").make(mode_cls("scoped_exception", extra_headers={name: "x"}))


@pytest.mark.planted
def test_D3_headers_for_cannot_add_framing_headers(tmp_path):
    e = scoped_env(tmp_path)
    e.script_ok(D17)

    class Bad(mode_cls("scoped_exception")):
        def headers_for(self, key, url):
            return {"Connection": "keep-alive"}
    with pytest.raises(ValueError):
        e.make(Bad).run(rr(D17))
    assert e.transport.n() == 0


# ------------------------------------------------------------------ D4 mutation survivors
@pytest.mark.planted
def test_D4_M05_scoped_exception_with_no_decision_refused_at_base_level(tmp_path):
    e = scoped_env(tmp_path)
    entry = replace(e.cfg.collect.access[SOURCE], decision=None)               # hand-built config bypasses load_config
    cfg = replace(e.cfg, collect=replace(e.cfg.collect, access={SOURCE: entry}))
    with pytest.raises(ValueError):
        e.make(mode_cls("scoped_exception"), cfg=cfg)


@pytest.mark.planted
def test_D4_M20_raw_body_cap_enforced(server):
    from investos.acquire.base import TransportError
    t = http_transport(max_body_bytes=100)
    with pytest.raises(TransportError):
        t.get(server.url("/ok"), headers={}, timeout=5)                         # 600 identity bytes
    assert len(http_transport(max_body_bytes=600).get(server.url("/ok"), headers={}, timeout=5).body) == 600


@pytest.mark.planted
def test_D4_M22_tls_certificate_verification_is_on(monkeypatch):
    import http.client
    from investos.acquire.base import TransportError
    from investos.acquire.transport import UrllibTransport
    seen = []
    real_init = http.client.HTTPSConnection.__init__

    def spy(self, *a, **k):
        seen.append(k.get("context"))
        real_init(self, *a, **k)
    monkeypatch.setattr(http.client.HTTPSConnection, "__init__", spy)
    import socket
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    port = s.getsockname()[1]
    s.close()
    with pytest.raises(TransportError):
        UrllibTransport().get(f"https://127.0.0.1:{port}/x", headers={}, timeout=1)
    assert len(seen) == 1 and isinstance(seen[0], ssl.SSLContext)
    assert seen[0].verify_mode == ssl.CERT_REQUIRED and seen[0].check_hostname is True


@pytest.mark.planted
def test_D4_M22_no_unverified_tls_in_source():
    src = (Path(__file__).resolve().parents[3] / "src" / "investos" / "acquire" / "transport.py").read_text()
    for bad in ("_create_unverified_context", "CERT_NONE", "verify=False", "check_hostname = False",
                "check_hostname=False"):
        assert bad not in src


@pytest.mark.planted
def test_D4_M42_daily_counter_is_atomic_across_processes(tmp_path):
    S1Env(tmp_path)
    state = tmp_path / "ledger" / ".state"
    cap, procs, n = 120, 8, 40
    go, q = CTX.Event(), CTX.Queue()
    ps = [CTX.Process(target=counter_worker, args=(str(state), SOURCE, n, cap, "2026-11-21T09:30:00+00:00", go, q))
          for _ in range(procs)]
    for p in ps:
        p.start()
    time.sleep(0.5)
    go.set()
    got = sum(get_result(q, ps) for _ in ps)
    for p in ps:
        p.join(30)
        assert p.exitcode == 0
    assert got == cap                                                            # 320 tries, exactly cap succeed
    assert (state / "requests" / f"{SOURCE}.2026-11-21.count").read_text().strip() == str(cap)


@pytest.mark.planted
def test_D4_M74_cookie_and_header_values_never_logged_on_the_retry_path(tmp_path, logs):
    e = scoped_env(tmp_path)
    secret = "nsit=RETRYSECRET987"
    e.transport.set(url_of(D17), resp(503, b"busy"), resp(200, CSV))
    r = e.make(mode_cls("scoped_exception", extra_headers={"Cookie": secret, "Referer": "https://ref.example/zz"})
               ).run(rr(D17))
    assert r.outcomes[D17].value == "fetched" and events(logs(), "retry", "WARNING")
    blob = json.dumps(logs())
    assert "RETRYSECRET987" not in blob and "ref.example/zz" not in blob and "InvestOS/" not in blob


@pytest.mark.planted
def test_D4_M82_truncated_body_is_a_transport_error(server):
    from investos.acquire.base import TransportError
    with pytest.raises(TransportError):
        http_transport().get(server.url("/short"), headers={}, timeout=5)           # Content-Length 100, 3 bytes sent


@pytest.mark.planted
def test_D4_M82_truncated_body_never_stored_by_the_collector(tmp_path):
    e = S1Env(tmp_path)
    from investos.acquire.base import TransportError
    e.transport.set(url_of(D17), TransportError("incomplete read: got 3 of 100 bytes"))
    r = e.make().run(rr(D17))
    assert e.manifest_lines() == [] and r.outcomes[D17].value == "unreadable"


# ------------------------------------------------------------------ D5 deadline while reading headers
@pytest.mark.planted
def test_D5_whole_call_deadline_applies_while_reading_headers(server):
    from investos.acquire.base import TransportError
    t0 = time.monotonic()
    with pytest.raises(TransportError):
        http_transport().get(server.url("/hdrdrip"), headers={}, timeout=0.3)
    assert time.monotonic() - t0 < 4 * 0.3 + 1.0
