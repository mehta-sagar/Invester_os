"""S1-T2 review regressions (reports/S1-T2/review.md): D1 collector call site, D2 16 KiB measure, D3 final_url
from the response, plus malformed-cookie and non-str header value pins."""
from __future__ import annotations

import json
import socket

import pytest

from _acq_helpers import lines_of, manifest_path, put
from _collector_helpers import CSV, D17, D18, SOURCE, demo_class, env, resp, rr, url_of  # noqa: F401
from _v2_helpers import V2_FIELDS


# ---------------------------------------------------------------- D1: what the collector records
def test_D1_collector_records_v2_provenance_in_real_manifest(env):
    env.script_ok(D17, D18)
    r = env.make(demo_class(extra_headers={"Accept": "text/csv"})).run(rr(D17, D18))
    assert r is not None
    lines = [json.loads(l) for l in env.manifest_lines()]
    assert len(lines) == 2
    assert env.transport.n() == 2
    for o, call in zip(lines, env.transport.calls):
        assert set(o) == V2_FIELDS and o["schema"] == 2
        assert o["access_mode"] == "honest"
        assert o["request_headers"] == call["headers"]            # exactly what the transport received
        assert o["request_headers"].get("Accept") == "text/csv" and "User-Agent" in o["request_headers"]
        assert "cookie" not in {k.lower() for k in o["request_headers"]}
        assert o["final_url"] == call["url"] == o["url"]
        assert o["fetch_host"] == socket.gethostname()
    e = env.store.get_by_key(SOURCE, D17)
    assert e.schema == 2 and e.access_mode == "honest" and e.fetch_host == socket.gethostname()
    assert dict(e.request_headers) == env.transport.calls[0]["headers"]
    assert env.store.verify() == []


def test_D1_fetch_host_follows_gethostname(env, monkeypatch):
    monkeypatch.setattr(socket, "gethostname", lambda: "host-under-test")
    env.script_ok(D17)
    env.make().run(rr(D17))
    assert json.loads(env.manifest_lines()[0])["fetch_host"] == "host-under-test"


# ---------------------------------------------------------------- D3: final_url is the response url
def test_D3_final_url_is_response_url_not_requested_url(env):
    final = "https://cdn.example.test/final/2026-11-17.csv"
    env.transport.set(url_of(D17), resp(200, CSV, url=final))
    env.make().run(rr(D17))
    o = json.loads(env.manifest_lines()[0])
    assert o["url"] == url_of(D17)
    assert o["final_url"] == final
    assert env.store.verify() == []


# ---------------------------------------------------------------- D2: 16 KiB measured on compact JSON
def _fits_compact_not_default(extra=0):
    names = ["X-A", "X-B", "X-C", "X-D"]
    vals = ["a" * 4096, "b" * 4096, "c" * 4096, ""]
    h = dict(zip(names, vals))
    base = len(json.dumps(h, separators=(",", ":"), ensure_ascii=False).encode())
    h["X-D"] = "d" * (16384 - base + extra)                  # compact size == 16384 + extra
    return h


def test_D2_just_under_limit_in_compact_form_is_accepted(mk, roots):
    h = _fits_compact_not_default()
    compact = len(json.dumps(h, separators=(",", ":"), ensure_ascii=False).encode())
    default = len(json.dumps(h, ensure_ascii=False).encode())
    assert compact == 16384 and default > 16384 and all(len(v) <= 4096 for v in h.values())
    st = mk()
    e = put(st, "bse", b"x", request_headers=h)
    assert dict(e.request_headers) == h
    assert mk().entries("bse")[0].request_headers == h
    assert mk().verify() == []


def test_D2_one_byte_over_in_compact_form_is_rejected(mk, roots):
    raw, man = roots
    h = _fits_compact_not_default(extra=1)
    assert len(json.dumps(h, separators=(",", ":"), ensure_ascii=False).encode()) == 16385
    assert all(len(v) <= 4096 for v in h.values())
    with pytest.raises(ValueError):
        put(mk(), "bse", b"x", request_headers=h)
    assert not list(man.iterdir())


# ---------------------------------------------------------------- non-str header value is a TypeError
@pytest.mark.planted
@pytest.mark.parametrize("val", [1, None, 1.5, b"x", ["a"]])
def test_non_str_header_value_is_typeerror_and_writes_nothing(mk, roots, val):
    raw, man = roots
    with pytest.raises(TypeError):
        put(mk(), "bse", b"x", request_headers={"A": val})
    assert not list(man.iterdir()) and not list(raw.rglob("*"))


# ---------------------------------------------------------------- malformed cookie -> whole <redacted>
@pytest.mark.planted
@pytest.mark.parametrize("raw_cookie", [
    "a=SECRETX;c=SECRETY", "SECRETX", "=SECRETX", "a=SECRETX; ", "a = SECRETX", " a=SECRETX",
    "a=SECRETX;  c=SECRETY",
], ids=["no_space", "no_equals", "empty_name", "trailing_sep", "spaces_around_eq", "leading_space", "two_spaces"])
def test_malformed_cookie_value_stored_as_redacted(mk, roots, raw_cookie):
    raw, man = roots
    st = mk()
    e = put(st, "bse", b"x", access_mode="scoped_exception", request_headers={"Cookie": raw_cookie})
    assert dict(e.request_headers) == {"Cookie": "<redacted>"}
    assert b"SECRET" not in manifest_path(man, "bse").read_bytes()
    assert dict(mk().entries("bse")[0].request_headers) == {"Cookie": "<redacted>"}
    assert mk().verify() == []
