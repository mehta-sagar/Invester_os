"""S1-T1: real_browser interface (R23; A38, A39), provenance recording (R30; A52), skip precedence (R31; A53)."""
from __future__ import annotations

import inspect
import json

import pytest

from _s1_helpers import (CSV, D17, D18, D19, HOST, SOURCE, S1Env, bresp, cfg_access, demo_class, default_check,
                         mode_cls, resp, rr, scoped_env, url_of)
from _collector_helpers import make_zip


def rb_env(tmp_path, **kw):
    return S1Env(tmp_path, access=cfg_access("real_browser"), **kw)


# ------------------------------------------------------------------ A38 (R23)
@pytest.mark.planted
def test_A38_wiring_rules(tmp_path):
    from _s1_helpers import FakeBrowser
    e = rb_env(tmp_path / "a")
    rb = mode_cls("real_browser")
    with pytest.raises(ValueError):
        e.make(rb, transport=e.transport, browser=None)                                # transport only
    with pytest.raises(ValueError):
        e.make(rb, transport=None, browser=None)                                       # neither
    with pytest.raises(ValueError):
        e.make(rb, transport=e.transport, browser=e.browser)                           # both
    e.make(rb, transport=None, browser=e.browser)                                      # ok
    e2 = S1Env(tmp_path / "b")
    with pytest.raises(ValueError):
        e2.make(demo_class(), transport=e2.transport, browser=FakeBrowser())            # honest + browser (both)
    with pytest.raises(ValueError):
        e2.make(demo_class(), transport=None, browser=FakeBrowser())                    # honest + browser only
    e3 = scoped_env(tmp_path / "c")
    with pytest.raises(ValueError):
        e3.make(mode_cls("scoped_exception"), transport=None, browser=FakeBrowser())
    with pytest.raises(ValueError):
        e3.make(mode_cls("scoped_exception"), transport=None, browser=None)


def test_A38_fetch_takes_only_the_url():
    from investos.acquire.base import BrowserTransport
    assert list(inspect.signature(BrowserTransport.fetch).parameters) == ["self", "url"]


def test_A38_browser_response_fields():
    from investos.acquire.base import BrowserResponse
    r = BrowserResponse(status=200, headers={}, body=b"", final_url="u", request_headers=None)
    assert (r.status, r.final_url, r.request_headers) == (200, "u", None)
    with pytest.raises(Exception):
        r.status = 1                                                                    # frozen


def test_A38_normal_run_one_fetch_per_key_and_no_http_transport(tmp_path):
    from investos.acquire.base import Outcome
    e = rb_env(tmp_path)
    for k in (D17, D18):
        e.browser.set(bresp(200, CSV, url=url_of(k)), url=url_of(k))
    r = e.make(mode_cls("real_browser")).run(rr(D17, D18))
    assert e.browser.urls == [url_of(D17), url_of(D18)]
    assert e.transport.n() == 0 and r.outcomes == {D17: Outcome.FETCHED, D18: Outcome.FETCHED}


@pytest.mark.planted
def test_A38_base_sends_no_headers_for_real_browser(tmp_path):
    """fetch() has no header argument, and headers_for is not consulted for a browser adapter's request."""
    e = rb_env(tmp_path)
    e.browser.set(bresp(200, CSV, url=url_of(D17)))
    seen = []

    class Spy(mode_cls("real_browser")):
        def headers_for(self, key, url):
            seen.append(key)
            return {}
    e.make(Spy).run(rr(D17))
    assert e.browser.urls == [url_of(D17)]


# ------------------------------------------------------------------ A39 (R23)
@pytest.mark.planted
@pytest.mark.parametrize("final", ["https://other.test/f/2026-11-17.csv", f"https://{HOST}/other/2026-11-17.csv",
                                   f"https://{HOST}/f/2026-11-17.csv?a=1"])
def test_A39_redirect_is_unreadable_and_not_stored(tmp_path, final):
    from investos.acquire.base import Outcome
    e = rb_env(tmp_path)
    e.browser.set(bresp(200, CSV, url=url_of(D17), final_url=final))
    r = e.make(mode_cls("real_browser")).run(rr(D17))
    assert r.outcomes == {D17: Outcome.UNREADABLE} and e.reasons(r)[D17] == f"redirected to {final}"
    assert e.manifest_lines() == [] and e.raw_files() == []


def test_A39_fragment_only_difference_is_fetched_and_recorded(tmp_path):
    from investos.acquire.base import Outcome
    e = rb_env(tmp_path)
    final = url_of(D17) + "#section"
    e.browser.set(bresp(200, CSV, url=url_of(D17), final_url=final))
    r = e.make(mode_cls("real_browser")).run(rr(D17))
    assert r.outcomes == {D17: Outcome.FETCHED}
    assert e.store.get_by_key(SOURCE, D17).final_url == final


@pytest.mark.planted
def test_A39_error_page_with_200_is_rejected_by_check_body(tmp_path):
    from investos.acquire.base import Outcome
    e = rb_env(tmp_path)
    e.browser.set(bresp(200, b"<html>Error: data not available</html>", url=url_of(D17)))
    r = e.make(mode_cls("real_browser")).run(rr(D17))
    assert r.outcomes == {D17: Outcome.UNREADABLE} and e.manifest_lines() == [] and e.raw_files() == []


@pytest.mark.planted
def test_A39_transport_error_forever_makes_exactly_four_calls(tmp_path):
    from investos.acquire.base import Outcome, TransportError
    e = rb_env(tmp_path)
    e.browser.set(TransportError("page crash"))
    r = e.make(mode_cls("real_browser")).run(rr(D17, D18))
    assert len(e.browser.urls) == 4 and r.outcomes == {D17: Outcome.UNREADABLE}
    assert "after 4 attempts" in e.reasons(r)[D17] and r.status == "aborted"


def test_A39_browser_5xx_is_retried_then_fetched(tmp_path):
    from investos.acquire.base import Outcome
    e = rb_env(tmp_path)
    e.browser.set(bresp(503, b"busy", url=url_of(D17)), bresp(200, CSV, url=url_of(D17)))
    r = e.make(mode_cls("real_browser")).run(rr(D17))
    assert r.outcomes == {D17: Outcome.FETCHED} and len(e.browser.urls) == 2


def test_A39_browser_archive_members_stored(tmp_path):
    from investos.acquire.base import Outcome
    e = rb_env(tmp_path)
    z = make_zip({"a.csv": b"date,x\n1,2\n"})
    e.browser.set(bresp(200, z, url=url_of(D17)))
    cls = mode_cls("real_browser", archive=True, verdict_fn=lambda self, k, r: __import__(
        "investos.acquire.base", fromlist=["BodyVerdict"]).BodyVerdict(Outcome.FETCHED))
    r = e.make(cls).run(rr(D17))
    assert r.outcomes == {D17: Outcome.FETCHED} and e.store.get_by_key(SOURCE, f"{D17}/a.csv") is not None


# ------------------------------------------------------------------ A52 (R30)
@pytest.mark.planted
def test_A52_honest_fetch_records_mode_headers_final_url_host(tmp_path):
    e = S1Env(tmp_path)
    e.script_ok(D17)
    e.make(hostname=lambda: "mac-1").run(rr(D17))
    ent = e.store.get_by_key(SOURCE, D17)
    assert ent.schema == 2 and ent.access_mode == "honest" and ent.fetch_host == "mac-1"
    assert ent.final_url == url_of(D17)
    assert dict(ent.request_headers) == e.transport.calls[0]["headers"]
    assert "Accept-Encoding" in ent.request_headers and "Host" not in ent.request_headers
    assert e.store.verify() == []


@pytest.mark.planted
def test_A52_archive_zip_and_members_carry_the_same_provenance(tmp_path):
    from investos.acquire.base import BodyVerdict, Outcome
    e = S1Env(tmp_path)
    z = make_zip({"a.csv": b"date,x\n1,2\n", "sub/b.csv": b"date,x\n3,4\n"})
    e.transport.set(url_of(D17), resp(200, z))
    cls = demo_class(archive=True, verdict_fn=lambda self, k, r: BodyVerdict(Outcome.FETCHED))
    e.make(cls, hostname=lambda: "mac-1").run(rr(D17))
    rows = [e.store.get_by_key(SOURCE, k) for k in (D17, f"{D17}/a.csv", f"{D17}/sub/b.csv")]
    assert all(rows)
    for ent in rows:
        assert (ent.access_mode, ent.fetch_host, ent.final_url) == ("honest", "mac-1", url_of(D17))
        assert dict(ent.request_headers) == e.transport.calls[0]["headers"]


@pytest.mark.planted
def test_A52_scoped_exception_records_the_extra_headers_sent(tmp_path):
    e = scoped_env(tmp_path)
    e.script_ok(D17)
    e.make(mode_cls("scoped_exception", extra_headers={"Referer": "https://example.test/"})).run(rr(D17))
    ent = e.store.get_by_key(SOURCE, D17)
    assert ent.access_mode == "scoped_exception" and ent.request_headers["Referer"] == "https://example.test/"


def test_A52_real_browser_records_browser_reported_headers_and_final_url(tmp_path):
    e = rb_env(tmp_path)
    hdrs = {"User-Agent": "BrowserUA/1", "Accept": "text/csv"}
    final = url_of(D17) + "#x"
    e.browser.set(bresp(200, CSV, url=url_of(D17), final_url=final, request_headers=hdrs))
    e.browser.set(bresp(200, CSV, url=url_of(D18), request_headers=None), url=url_of(D18))
    e.make(mode_cls("real_browser"), hostname=lambda: "mac-1").run(rr(D17, D18))
    a, b = e.store.get_by_key(SOURCE, D17), e.store.get_by_key(SOURCE, D18)
    assert a.access_mode == "real_browser" and dict(a.request_headers) == hdrs and a.final_url == final
    assert b.request_headers is None and b.final_url == url_of(D18) and b.fetch_host == "mac-1"
    assert e.store.verify() == []


@pytest.mark.planted
def test_A52_sidecar_is_schema_2_with_access_mode(tmp_path):
    e = S1Env(tmp_path)
    e.script_ok(D17)
    sc = e.sidecar(e.make().run(rr(D17)))
    assert sc["schema"] == 2 and sc["access_mode"] == "honest"
    e2 = rb_env(tmp_path / "b")
    e2.browser.set(bresp(200, CSV, url=url_of(D17)))
    assert e2.sidecar(e2.make(mode_cls("real_browser")).run(rr(D17)))["access_mode"] == "real_browser"


def _first_complete(e):
    e.script_ok(D17)
    r = e.make().run(rr(D17))
    assert r.status == "complete"
    return r.sidecar_path


@pytest.mark.planted
def test_A52_existing_schema_1_complete_sidecar_still_protects(tmp_path):
    e = S1Env(tmp_path)
    p = _first_complete(e)
    sc = json.loads(p.read_text())
    sc["schema"] = 1
    sc.pop("access_mode")
    p.write_text(json.dumps(sc, sort_keys=True, separators=(",", ":"), ensure_ascii=False) + "\n")
    before = p.read_bytes()
    e.fresh_transport()
    e.script_ok(D17)
    e.transport.set(url_of(D18), resp(503, b"busy"))
    r = e.make(demo_class(fixed_keys=[D17, D18]), run_id="run-2").run(rr(D17))
    assert r.status == "aborted" and r.sidecar_replaced_previous is False
    assert p.read_bytes() == before
    assert list(p.parent.glob("*.attempt-run-2.json"))


@pytest.mark.planted
def test_A52_sidecar_with_unknown_schema_is_renamed_corrupt(tmp_path):
    e = S1Env(tmp_path)
    p = _first_complete(e)
    sc = json.loads(p.read_text())
    sc["schema"] = 3
    p.write_text(json.dumps(sc, sort_keys=True, separators=(",", ":"), ensure_ascii=False) + "\n")
    e.fresh_transport()
    e.script_ok(D17)
    r = e.make(run_id="run-2").run(rr(D17))
    assert list(p.parent.glob("*.corrupt-run-2.json")) and json.loads(p.read_text())["schema"] == 2
    assert r.status == "complete"


# ------------------------------------------------------------------ A53 (R31)
@pytest.mark.planted
@pytest.mark.parametrize("state", ["blocked", "halted", "capped", "all"])
def test_A53_fully_fetched_range_is_skipped_whatever_the_access_state(tmp_path, state):
    from investos.acquire.base import Outcome
    e = S1Env(tmp_path, access={SOURCE: {"mode": "honest", "daily_cap": 2}})
    e.script_ok(D17, D18)
    assert e.make().run(rr(D17, D18)).status == "complete"                              # also exhausts the cap
    if state in ("blocked", "all"):
        e.plant_block()
    if state in ("halted", "all"):
        e.plant_halt()
    if state == "capped":
        assert e.count_file().read_text().strip() == "2"
    e.fresh_transport()
    r = e.make(run_id="r2").run(rr(D17, D18))
    assert r.http_calls == 0 and e.sleeper.sleeps == []
    assert set(r.outcomes.values()) == {Outcome.SKIPPED} and r.status == "complete"
