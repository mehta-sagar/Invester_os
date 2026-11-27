"""S1-T1: UrllibTransport against a local 127.0.0.1 http.server only (R21, R22; A33-A37, A55)."""
from __future__ import annotations

import ast
import socket
import sys
import time
import types
from pathlib import Path

import pytest

from _s1_helpers import (CSV, D17, SOURCE, S1Env, bresp, demo_class, http_transport, mode_cls, resp, rr,  # noqa: F401
                         server, url_of, cfg_access)

SRC = Path(__file__).resolve().parents[3] / "src" / "investos" / "acquire"
HDR = {"User-Agent": "u", "Accept-Encoding": "identity"}


# ------------------------------------------------------------------ A33 (R21a)
@pytest.mark.planted
def test_A33_only_given_headers_are_sent(server):
    t = http_transport()
    r = t.get(server.url("/ok"), headers=HDR, timeout=5)
    assert r.status == 200 and r.body == server.plain
    names = set(server.seen[0]["names"])
    assert names <= {"host", "connection", "user-agent", "accept-encoding"}
    assert server.seen[0]["headers"]["user-agent"] == "u"
    assert server.seen[0]["headers"]["accept-encoding"] == "identity"


@pytest.mark.planted
def test_A33_empty_headers_means_no_library_defaults(server):
    http_transport().get(server.url("/ok"), headers={}, timeout=5)
    names = set(server.seen[0]["names"])
    assert not names & {"user-agent", "accept", "accept-encoding", "accept-language", "cookie", "referer"}
    assert names <= {"host", "connection"}


def test_A33_response_shape(server):
    r = http_transport().get(server.url("/ok"), headers=HDR, timeout=5)
    assert r.url == server.url("/ok")                                  # the requested URL
    assert all(k == k.lower() for k in r.headers)
    assert r.headers["x-multi"] == "a, b"                              # repeated headers joined with ", "
    assert r.headers["x-mixed-case"] == "v"


# ------------------------------------------------------------------ A34 (R21b, c)
@pytest.mark.planted
def test_A34_redirect_returned_not_followed(server):
    r = http_transport().get(server.url("/redir"), headers=HDR, timeout=5)
    assert r.status == 302 and r.headers["location"] == "/ok" and r.body == b"moved"
    assert [s["path"] for s in server.seen] == ["/redir"]


@pytest.mark.planted
@pytest.mark.parametrize("path,status", [("/s404", 404), ("/s503", 503)])
def test_A34_http_errors_are_responses_not_exceptions(server, path, status):
    r = http_transport().get(server.url(path), headers=HDR, timeout=5)
    assert r.status == status and r.body


@pytest.mark.planted
def test_A34_environment_proxies_ignored(server, monkeypatch):
    for k in ("HTTP_PROXY", "http_proxy", "HTTPS_PROXY", "https_proxy", "ALL_PROXY", "all_proxy"):
        monkeypatch.setenv(k, "http://127.0.0.1:9")
    for k in ("NO_PROXY", "no_proxy"):
        monkeypatch.delenv(k, raising=False)
    r = http_transport().get(server.url("/ok"), headers=HDR, timeout=5)
    assert r.status == 200 and len(server.seen) == 1


@pytest.mark.planted
@pytest.mark.parametrize("url", ["file:///etc/passwd", "ftp://127.0.0.1/x", "data:text/plain,hi"])
def test_A34_disallowed_schemes_raise_value_error(url):
    with pytest.raises(ValueError):
        http_transport().get(url, headers=HDR, timeout=5)


@pytest.mark.planted
def test_A34_http_refused_with_default_schemes(server):
    from investos.acquire.transport import UrllibTransport
    with pytest.raises(ValueError):
        UrllibTransport().get(server.url("/ok"), headers=HDR, timeout=5)
    assert server.seen == []


# ------------------------------------------------------------------ A35 (R21d)
@pytest.mark.parametrize("path", ["/gzip", "/deflate", "/deflate-raw"])
def test_A35_gzip_and_deflate_decoded(server, path):
    r = http_transport().get(server.url(path), headers=HDR, timeout=5)
    assert r.body == server.plain
    assert "content-encoding" not in r.headers and "content-length" not in r.headers


@pytest.mark.planted
def test_A35_decoded_size_capped(server):
    t = http_transport(max_body_bytes=1024 * 1024)
    with pytest.raises(Exception) as ei:
        t.get(server.url("/bomb"), headers=HDR, timeout=10)
    from investos.acquire.base import TransportError
    assert isinstance(ei.value, TransportError)


def test_A35_decoded_size_under_cap_ok(server):
    t = http_transport(max_body_bytes=8 * 1024 * 1024)
    assert len(t.get(server.url("/bomb"), headers=HDR, timeout=10).body) == 4 * 1024 * 1024


def test_A35_brotli_absent_body_and_header_untouched_and_not_advertised(server, monkeypatch):
    monkeypatch.setitem(sys.modules, "brotli", None)                   # import brotli -> ImportError
    t = http_transport()
    assert t.accept_encoding == "gzip, deflate"
    r = t.get(server.url("/br"), headers=HDR, timeout=5)
    assert r.body == b"BR:" + server.plain and r.headers["content-encoding"] == "br"


def test_A35_brotli_present_advertised_and_decoded(server, monkeypatch):
    class Dec:
        def __init__(self):
            self._first = True

        def process(self, data):
            if self._first and data.startswith(b"BR:"):
                data = data[3:]
            self._first = False
            return data

        def is_finished(self):
            return True

    fake = types.SimpleNamespace(decompress=lambda b: b[3:] if b.startswith(b"BR:") else b, Decompressor=Dec)
    t = http_transport(brotli_module=fake)
    assert t.accept_encoding == "gzip, deflate, br"
    r = t.get(server.url("/br"), headers=HDR, timeout=5)
    assert r.body == server.plain
    assert "content-encoding" not in r.headers and "content-length" not in r.headers


# ------------------------------------------------------------------ A37 (R21e)
@pytest.mark.planted
def test_A37_server_slower_than_timeout(server):
    from investos.acquire.base import TransportError
    t0 = time.monotonic()
    with pytest.raises(TransportError):
        http_transport().get(server.url("/slow"), headers=HDR, timeout=0.2)
    assert time.monotonic() - t0 < 0.9


@pytest.mark.planted
def test_A37_slow_drip_bounded_by_four_times_timeout(server):
    from investos.acquire.base import TransportError
    t0 = time.monotonic()
    with pytest.raises(TransportError):
        http_transport().get(server.url("/drip"), headers=HDR, timeout=0.3)
    assert time.monotonic() - t0 < 4 * 0.3 + 1.0


@pytest.mark.planted
def test_A37_closed_port_is_transport_error():
    from investos.acquire.base import TransportError
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    port = s.getsockname()[1]
    s.close()
    with pytest.raises(TransportError):
        http_transport().get(f"http://127.0.0.1:{port}/x", headers=HDR, timeout=1)


def test_A37_unresolvable_host_is_transport_error():
    from investos.acquire.base import TransportError
    with pytest.raises(TransportError):
        http_transport().get("http://nonexistent-host.invalid/x", headers=HDR, timeout=1)


# ------------------------------------------------------------------ collector over the real transport (no network beyond 127.0.0.1)
def test_collector_over_urllib_transport_end_to_end(tmp_path, server):
    """Honest fetch through UrllibTransport: the server sees exactly the recorded headers."""
    import investos
    from investos.acquire.base import Collector
    e = S1Env(tmp_path, contact="ops@example.org")

    class Local(demo_class()):
        def url(self, key):
            return server.url(f"/csv/{key}")

    # route /csv/<key> is not defined: the server answers 500, so use /ok and a body check that accepts it
    class Ok(Local):
        def url(self, key):
            return server.url("/ok")

        def check_body(self, key, r):
            from investos.acquire.base import BodyVerdict, Outcome
            return BodyVerdict(Outcome.FETCHED)

    t = http_transport()
    c = Ok(transport=t, manifest=e.store, cfg=e.cfg, run_id="run-1", clock=e.clock, monotonic=e.mono,
           sleeper=e.sleeper, wall_clock=e.wall, hostname=lambda: "h")
    r = c.run(rr(D17))
    assert r.outcomes[D17].value == "fetched"
    seen = server.seen[0]["headers"]
    assert seen["user-agent"] == f"InvestOS/{investos.__version__} (personal research; ops@example.org)"
    assert seen["accept-encoding"] == "gzip, deflate" or seen["accept-encoding"].startswith("gzip, deflate")
    assert set(seen) <= {"host", "connection", "user-agent", "accept-encoding"}
    ent = e.store.get_by_key(SOURCE, D17)
    assert ent.request_headers["User-Agent"] == seen["user-agent"]


# ------------------------------------------------------------------ A36 (R22)
@pytest.mark.planted
def test_A36_undecoded_content_encoding_is_unreadable(tmp_path):
    from investos.acquire.base import Outcome
    e = S1Env(tmp_path)
    calls = []

    def v(self, key, r):
        calls.append(key)
        from _collector_helpers import default_check
        return default_check(self, key, r)

    e.transport.set(url_of(D17), resp(200, CSV, headers={"content-encoding": "br"}))
    r = e.make(demo_class(verdict_fn=v)).run(rr(D17))
    assert r.outcomes == {D17: Outcome.UNREADABLE}
    assert e.reasons(r)[D17] == "undecoded content-encoding br"
    assert calls == [] and e.manifest_lines() == [] and e.raw_files() == []


@pytest.mark.parametrize("value", ["identity", "", "IDENTITY"])
def test_A36_identity_or_empty_is_accepted(tmp_path, value):
    from investos.acquire.base import Outcome
    e = S1Env(tmp_path)
    e.transport.set(url_of(D17), resp(200, CSV, headers={"content-encoding": value}))
    assert e.make().run(rr(D17)).outcomes == {D17: Outcome.FETCHED}


@pytest.mark.planted
def test_A36_applies_to_browser_responses_too(tmp_path):
    from investos.acquire.base import Outcome
    e = S1Env(tmp_path, access=cfg_access("real_browser"))
    e.browser.set(bresp(200, CSV, url=url_of(D17), headers={"content-encoding": "gzip"}))
    r = e.make(mode_cls("real_browser")).run(rr(D17))
    assert r.outcomes == {D17: Outcome.UNREADABLE} and "undecoded content-encoding gzip" in e.reasons(r)[D17]


# ------------------------------------------------------------------ A55 (R21, R23, R33)
FORBIDDEN_IMPORTS = {"requests", "httpx", "aiohttp", "selenium", "playwright", "socks", "urllib3", "pycurl",
                     "mechanize", "pyppeteer"}


def _imports(path: Path) -> set[str]:
    out = set()
    for n in ast.walk(ast.parse(path.read_text())):
        if isinstance(n, ast.Import):
            out |= {a.name.split(".")[0] for a in n.names}
        elif isinstance(n, ast.ImportFrom) and n.module and n.level == 0:
            out.add(n.module.split(".")[0])
    return out


@pytest.mark.parametrize("name", ["base.py", "transport.py", "access.py"])
def test_A55_no_forbidden_http_or_browser_library(name):
    p = SRC / name
    assert p.exists(), f"{p} missing"
    assert not _imports(p) & FORBIDDEN_IMPORTS


def test_A55_transport_is_urllib_only():
    p = SRC / "transport.py"
    assert p.exists()
    imps = _imports(p)
    assert "urllib" in imps
    assert not imps & FORBIDDEN_IMPORTS


@pytest.mark.planted
def test_A55_no_stealth_strings_anywhere_under_acquire():
    files = sorted(SRC.glob("*.py"))
    assert {f.name for f in files} >= {"base.py", "transport.py", "access.py"}
    for f in files:
        low = f.read_text().lower()
        for bad in ("webdriver", "stealth", "fake_useragent"):
            assert bad not in low, f"{f.name} contains {bad!r}"
