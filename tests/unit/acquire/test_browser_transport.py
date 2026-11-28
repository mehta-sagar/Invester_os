"""S1-T7 step 1-3: RealBrowserTransport over the fake driver seam (spec collect_browser R1, R4-R7, R9-R12; A1, A4-A14,
A17-A22, A36). No browser, no network, no real sleeps."""
from __future__ import annotations

import inspect
import json
import threading

import pytest

from _browser_helpers import (ANCHOR, CHROME_UA, HOST, N0, NASTY, SENT_OK, SLOT0, URL_N1, BrowserEnv, Cost, PWWorld,
                              bresp, dedupe, fetch_ok, kinds_of, lock_is_held, nse_rows, page_result,
                              record_acquires)
from _collector_helpers import events, logs  # noqa: F401
from _rss_helpers import NSE_REAL, body_of, fields_of, frow, sha


def terror(msg):
    from investos.acquire.base import TransportError
    return TransportError(msg)


def one_feed(tmp):
    return BrowserEnv(tmp, nse_rows(1))


# ================================================================== A1 (R1, R13)
def test_A1_transport_fetch_takes_only_the_url_and_is_a_real_network_transport():
    from investos.acquire.browser import RealBrowserTransport
    assert list(inspect.signature(RealBrowserTransport.fetch).parameters) == ["self", "url"]
    assert RealBrowserTransport.real_network is True
    assert callable(RealBrowserTransport.close) and hasattr(RealBrowserTransport, "__enter__")


def test_A1_adapter_classes_modes_and_sources():
    from investos.acquire.base import AccessMode
    from investos.acquire.rss import RssFeedCollector, RssNseFeedCollector
    assert RssNseFeedCollector.access_mode is AccessMode.REAL_BROWSER and RssNseFeedCollector.source == "rss_nse"
    assert RssFeedCollector.access_mode is AccessMode.HONEST and RssFeedCollector.source == "rss"
    assert issubclass(RssNseFeedCollector, RssFeedCollector)
    assert RssNseFeedCollector.source_version == "rss-v1" and RssNseFeedCollector.date_keyed is False


def test_A1_placeholder_contact_is_refused_for_the_browser_transport(tmp_path):
    from investos.acquire.base import _check_contact
    rb = one_feed(tmp_path).rb()
    with pytest.raises(ValueError):
        _check_contact("someone@example.invalid", rb)


@pytest.mark.planted
def test_A1_wiring_rules_of_the_nse_adapter(tmp_path):
    e = one_feed(tmp_path)
    feed, rb = e.feed("n01"), e.rb()
    with pytest.raises(ValueError):
        e.nse_collector(feed, "20261127T0900", rb, transport=e.transport)                      # both
    with pytest.raises(ValueError):
        e.nse_collector(feed, "20261127T0900", None)                                           # neither
    from investos.acquire.rss import RssNseFeedCollector
    with pytest.raises(ValueError):
        RssNseFeedCollector(feed, "20261127T0900", transport=e.transport, manifest=e.store, cfg=e.cfg)
    e.nse_collector(feed, "20261127T0900", rb)                                                 # ok


@pytest.mark.planted
@pytest.mark.parametrize("access", [None, {"rss_nse": {"mode": "honest"}}], ids=["no_table", "honest_mode"])
def test_A1_nse_adapter_without_a_real_browser_table_raises_naming_the_source(tmp_path, access):
    e = BrowserEnv(tmp_path, nse_rows(1), access=access)
    with pytest.raises(ValueError, match="rss_nse"):
        e.nse_collector(e.feed("n01"), "20261127T0900", e.rb())


# ================================================================== A4 (R4, R14)
def test_A4_call_order_first_fetch_then_no_second_anchor_then_a_second_origin_anchors(tmp_path, monkeypatch):
    from investos.acquire.browser import BROWSER_HOSTS
    assert BROWSER_HOSTS == frozenset({"nsearchives.nseindia.com"})
    e = one_feed(tmp_path)
    record_acquires(monkeypatch, e.log)
    rb = e.rb(allowed_hosts=frozenset({HOST, "www.rbi.org.in"}))
    r = rb.fetch(URL_N1)
    assert r.status == 200
    assert dedupe(kinds_of(e.log, "reap", "launch", "acquire", "anchor", "fetch")) == \
        ["reap", "launch", "acquire", "anchor", "acquire", "fetch"]
    assert e.driver.launches[0]["lock_held"] is True                      # the profile lock is taken before launch
    assert e.driver.urls("anchor") == [ANCHOR]
    n = len(e.log)
    rb.fetch(NSE_REAL + "other.xml")
    assert kinds_of(e.log[n:], "acquire", "anchor", "fetch") == ["fetch"]            # same origin: no anchor, no acquire
    n = len(e.log)
    rb.fetch("https://www.rbi.org.in/pressreleases_rss.xml")
    assert kinds_of(e.log[n:], "acquire", "anchor", "fetch") == ["acquire", "anchor", "acquire", "fetch"]
    assert e.driver.urls("anchor")[-1] == "https://www.rbi.org.in/robots.txt"
    assert len(e.driver.launches) == 1
    rb.close()


def test_A4_limiter_interval_is_the_real_browser_floor_not_the_configured_1s(tmp_path, monkeypatch):
    e = one_feed(tmp_path)
    assert e.cfg.rate_limits.interval_for(HOST) == 1.0
    record_acquires(monkeypatch, e.log)
    with e.rb() as rb:
        rb.fetch(URL_N1)
    acq = [x for x in e.log if x[0] == "acquire"]
    assert [(h, i) for _, h, i in acq] == [(HOST, 2.5), (HOST, 2.5)]


# ================================================================== A5 (R4c, R4d)
@pytest.mark.planted
@pytest.mark.parametrize("status", [401, 403, 429, 451])
def test_A5_a_blocked_anchor_is_returned_as_the_response_and_no_data_fetch_is_made(tmp_path, status):
    from investos.acquire.browser import AnchorResult
    e = one_feed(tmp_path)
    e.driver.anchors = [AnchorResult(status, ANCHOR)]
    with e.rb() as rb:
        r = rb.fetch(URL_N1)
    assert r.status == status and r.final_url == ANCHOR
    assert e.driver.urls("fetch") == []


def test_A5_an_anchor_404_is_fine_and_the_data_fetch_proceeds(tmp_path):
    from investos.acquire.browser import AnchorResult
    e = one_feed(tmp_path)
    e.driver.anchors = [AnchorResult(404, ANCHOR)]
    with e.rb() as rb:
        assert rb.fetch(URL_N1).status == 200
    assert e.driver.urls("fetch") == [URL_N1]


@pytest.mark.planted
def test_A5_an_anchor_that_ends_on_another_origin_is_an_error(tmp_path):
    from investos.acquire.base import TransportError
    from investos.acquire.browser import AnchorResult
    e = one_feed(tmp_path)
    e.driver.anchors = [AnchorResult(200, "https://elsewhere.example/robots.txt")]
    with e.rb() as rb:
        with pytest.raises(TransportError, match="anchor left origin"):
            rb.fetch(URL_N1)
    assert e.driver.urls("fetch") == []


@pytest.mark.planted
def test_A5_blocked_anchor_through_the_adapter_records_a_host_block_aborts_and_closes_chrome(tmp_path):
    from investos.acquire.browser import AnchorResult
    e = BrowserEnv(tmp_path)
    e.driver.anchors = [AnchorResult(403, ANCHOR)]
    e.serve("rbi_press")
    with e.rb() as rb:
        r = e.cycle(N0, browser=rb)
    assert r.outcomes["n01"] == "unreadable" and r.outcomes["n02"] == "not_attempted" \
        and r.outcomes["n03"] == "not_attempted"
    assert [ev["event"] for ev in e.host_events(HOST)] == ["blocked"]
    reasons = {g["feed_id"]: g["reason"] for g in e.gap_lines()}
    assert "blocked" in reasons["n02"] and "host down" not in reasons["n02"]
    assert e.driver.urls("fetch") == []
    assert r.outcomes["rbi_press"] == "fetched"
    assert [s.close_calls for s in e.driver.sessions] == [1]


# ================================================================== A6 (R4a, R14)
def spacing_cycle(tmp, *, n=2):
    e = BrowserEnv(tmp, nse_rows(n), interval=1.0)
    with e.rb() as rb:
        e.cycle(N0, browser=rb)
    return e


def assert_spacing(e):
    starts = [x[-1] for x in e.log if x[0] in ("anchor", "fetch")]
    assert len(starts) >= 3
    gaps = [b - a for a, b in zip(starts, starts[1:])]
    assert all(g >= 2.5 - 1e-6 for g in gaps), gaps


def test_A6_anchor_data_fetch_and_next_key_are_each_at_least_2_5_s_apart(tmp_path):
    e = spacing_cycle(tmp_path)
    assert_spacing(e)
    assert kinds_of(e.log, "anchor", "fetch") == ["anchor", "fetch", "fetch"]


@pytest.mark.planted
def test_A6_a_transport_that_skips_the_limiter_fails_the_spacing_check(tmp_path, monkeypatch):
    from investos.acquire import access
    monkeypatch.setattr(access.HostLimiter, "acquire", lambda self, host, interval: None)
    e = spacing_cycle(tmp_path)
    with pytest.raises(AssertionError):
        assert_spacing(e)


def test_A6_the_daily_counter_counts_data_attempts_only_not_the_anchor(tmp_path):
    e = spacing_cycle(tmp_path, n=2)
    assert e.count_file("rss_nse", "2026-11-27").read_text() == "2"


def test_A6_only_the_first_key_of_a_session_pays_the_two_extra_limiter_calls(tmp_path, monkeypatch):
    e = one_feed(tmp_path)
    record_acquires(monkeypatch, e.log)
    with e.rb() as rb:
        rb.fetch(URL_N1)
        n = len(e.log)
        rb.fetch(URL_N1 + "?2")
        assert [x for x in e.log[n:] if x[0] == "acquire"] == []


# ================================================================== A7 / A8 (R5) via the real driver on the fake Playwright
def pw_env(tmp_path, **kw):
    e = BrowserEnv(tmp_path, nse_rows(1), **kw)
    w = PWWorld(e.log)
    return e, w, e.rb(driver=w.driver())


@pytest.mark.parametrize("case", sorted(NASTY))
def test_A7_body_bytes_come_back_identical(tmp_path, case):
    e, w, rb = pw_env(tmp_path)
    w.bodies[URL_N1] = NASTY[case]
    with rb:
        r = rb.fetch(URL_N1)
    assert sha(r.body) == sha(NASTY[case]) and r.body == NASTY[case]


@pytest.mark.planted
def test_A7_length_mismatch_invalid_base64_and_oversize_are_transport_errors(tmp_path):
    from investos.acquire.base import TransportError
    body = body_of(1)
    cases = [(page_result(body, url=URL_N1, byte_length=len(body) + 1), "body length mismatch"),
             (page_result(body, url=URL_N1, byte_length=len(body) - 1), "body length mismatch"),
             (page_result(b"", url=URL_N1, b64_text="!!!not-base64***", byte_length=3), ""),
             (page_result(b"", url=URL_N1, kind="error", error="too_large"), "body too large")]
    for n, (result, text) in enumerate(cases):
        e, w, rb = pw_env(tmp_path / f"c{n}")
        w.handler = lambda url, arg, result=result: result
        with rb:
            with pytest.raises(TransportError, match=text or ""):
                rb.fetch(URL_N1)


@pytest.mark.planted
def test_A7_a_page_that_reports_more_bytes_than_the_limit_is_too_large_without_decoding(tmp_path):
    from investos.acquire.base import TransportError
    e, w, rb = pw_env(tmp_path, max_body_bytes=1024)
    w.handler = lambda url, arg: page_result(b"", url=url, b64_text="@@@@", byte_length=5000)
    with rb:
        with pytest.raises(TransportError, match="body too large"):
            rb.fetch(URL_N1)


def test_A7_the_byte_limit_handed_to_the_session_is_the_config_limit_capped_at_64_mib(tmp_path):
    e = one_feed(tmp_path)
    with e.rb() as rb:
        rb.fetch(URL_N1)
    assert e.driver.kinds("fetch")[0][3] == 67108864                      # default 512 MiB is capped
    e2 = BrowserEnv(tmp_path / "small", nse_rows(1), max_body_bytes=1024)
    with e2.rb() as rb:
        rb.fetch(URL_N1)
    assert e2.driver.kinds("fetch")[0][3] == 1024


def test_A7_a_transport_level_length_mismatch_is_an_error_too(tmp_path):
    from investos.acquire.base import TransportError
    e = one_feed(tmp_path)
    e.driver.set_fetch(URL_N1, fetch_ok(b"abc", url=URL_N1, byte_length=5))
    with e.rb() as rb:
        with pytest.raises(TransportError, match="body length mismatch"):
            rb.fetch(URL_N1)


@pytest.mark.planted
def test_A8_text_round_trip_would_be_caught_and_the_stored_raw_file_is_byte_exact(tmp_path):
    # the check that must fail for a driver that decodes the bytes as text and re-encodes them
    def roundtrip_ok(body, got):
        return got == body

    assert not roundtrip_ok(NASTY["cp1252"], NASTY["cp1252"].decode("utf-8", "replace").encode("utf-8"))
    for case in ("bom", "crlf"):
        e, w, rb = pw_env(tmp_path / case)
        w.bodies[URL_N1] = NASTY[case]
        with rb:
            res = e.run_nse(e.feed("n01"), SLOT0, rb)
        assert list(res.outcomes.values())[0].value == "fetched"
        row = e.nse_manifest()[0]
        raw = next(p for p in (e.tmp / "raw").rglob("*") if p.is_file())
        assert row["sha256"] == sha(NASTY[case]) and raw.read_bytes() == NASTY[case]


# ================================================================== A9 (R6a)
@pytest.mark.parametrize("status", [200, 304, 404, 500, 503])
def test_A9_any_delivered_status_is_a_response_not_an_exception(tmp_path, status):
    e = one_feed(tmp_path)
    e.driver.set_fetch(URL_N1, fetch_ok(b"x", url=URL_N1, status=status))
    with e.rb() as rb:
        assert rb.fetch(URL_N1).status == status


def test_A9_through_the_base_503_is_retried_four_times_and_404_reaches_check_body(tmp_path):
    e = one_feed(tmp_path)
    e.driver.set_fetch(URL_N1, fetch_ok(b"busy", url=URL_N1, status=503))
    with e.rb() as rb:
        e.run_nse(e.feed("n01"), SLOT0, rb)
    assert len(e.driver.urls("fetch")) == 4 and [s for s in e.sleeper.sleeps if s in (1, 2, 4)][:3] == [1, 2, 4]
    e2 = one_feed(tmp_path / "b")
    e2.driver.set_fetch(URL_N1, fetch_ok(b"nope", url=URL_N1, status=404))
    with e2.rb() as rb:
        res = e2.run_nse(e2.feed("n01"), SLOT0, rb)
    assert len(e2.driver.urls("fetch")) == 1
    assert "http 404" in next(iter(e2.gaps(res).values()))["reason"]


# ================================================================== A10 (R6b)
@pytest.mark.planted
def test_A10_content_encoding_and_length_are_removed_other_headers_lowercased(tmp_path):
    e = one_feed(tmp_path)
    e.driver.set_fetch(URL_N1, fetch_ok(b"<x/>", url=URL_N1, headers={
        "Content-Encoding": "gzip", "Content-Length": "123", "Content-Type": "application/xml", "X-Mixed": "v"}))
    with e.rb() as rb:
        r = rb.fetch(URL_N1)
    assert dict(r.headers) == {"content-type": "application/xml", "x-mixed": "v"}


def test_A10_full_stack_duplicates_are_joined_and_a_gzip_feed_is_fetched(tmp_path):
    e, w, rb = pw_env(tmp_path)
    w.handler = lambda url, arg: page_result(body_of(1), url=url, headers=[
        ["Content-Encoding", "gzip"], ["Content-Length", "123"], ["Content-Type", "application/xml"],
        ["a", "1"], ["A", "2"]])
    with rb:
        r = rb.fetch(URL_N1)
        assert dict(r.headers) == {"content-type": "application/xml", "a": "1, 2"}
        res = e.run_nse(e.feed("n01"), SLOT0, rb)
    assert list(res.outcomes.values())[0].value == "fetched"


@pytest.mark.planted
def test_A10_a_transport_that_kept_content_encoding_would_make_every_gzip_feed_unreadable(tmp_path):
    from investos.acquire.base import Outcome
    e = one_feed(tmp_path)
    from _rss_helpers import numbered
    from _browser_helpers import TimedBrowser
    tb = TimedBrowser(e.mono)
    tb.set(URL_N1, bresp(200, body_of(1), url=URL_N1, headers={"content-encoding": "gzip"}))
    res = e.run_nse(e.feed("n01"), SLOT0, tb)
    assert list(res.outcomes.values())[0] is Outcome.UNREADABLE
    assert "undecoded content-encoding gzip" in next(iter(e.gaps(res).values()))["reason"]


# ================================================================== A11 (R6c)
def redirect_world(tmp, *, observed=True, location="https://nsearchives.nseindia.in/content/RSS/x.xml"):
    e, w, rb = pw_env(tmp)
    w.handler = lambda url, arg: page_result(b"", url=url, status=0, headers=[], kind="opaqueredirect")
    if observed:
        w.redirect_events[URL_N1] = (302, {"location": location, "content-length": "0"})
    return e, w, rb


@pytest.mark.planted
def test_A11_redirect_is_reported_with_its_status_and_target_and_the_target_is_never_requested(tmp_path):
    loc = "https://nsearchives.nseindia.in/content/RSS/x.xml"
    e, w, rb = redirect_world(tmp_path, location=loc)
    with rb:
        r = rb.fetch(URL_N1)
        assert (r.status, r.final_url, r.body) == (302, loc, b"")
        assert r.headers["location"] == loc
        res = e.run_nse(e.feed("n01"), SLOT0, rb)
    assert list(res.outcomes.values())[0].value == "unreadable"
    assert next(iter(e.gaps(res).values()))["reason"].startswith("redirected to https://nsearchives.nseindia.in")
    assert e.nse_manifest() == []
    assert set(w.fetch_urls) == {URL_N1} and loc not in w.fetch_urls


def test_A11_a_relative_location_is_resolved_against_the_request_url(tmp_path):
    e, w, rb = redirect_world(tmp_path, location="/other/y.xml")
    with rb:
        r = rb.fetch(URL_N1)
    assert r.status == 302 and r.final_url == f"https://{HOST}/other/y.xml"


@pytest.mark.planted
def test_A11_an_unobserved_redirect_is_status_0_and_redirect_unobserved_and_stores_nothing(tmp_path):
    e, w, rb = redirect_world(tmp_path, observed=False)
    with rb:
        r = rb.fetch(URL_N1)
        assert (r.status, r.final_url) == (0, "redirect:unobserved")
        res = e.run_nse(e.feed("n01"), SLOT0, rb)
    assert list(res.outcomes.values())[0].value == "unreadable" and e.nse_manifest() == []


# ================================================================== A12 (R6d, R1)
@pytest.mark.parametrize("how,text", [
    ("timeout", "timeout"), ("network", "network error"),
    ("jsexc", "browser failure"), ("closed", "browser failure")])
def test_A12_page_failures_become_transport_errors_with_fixed_texts(tmp_path, how, text):
    from investos.acquire.base import TransportError
    e, w, rb = pw_env(tmp_path)

    def handler(url, arg):
        if how in ("timeout", "network"):
            return page_result(b"", url=url, status=0, headers=[], kind="error", error=how)
        if how == "jsexc":
            return Exception("TypeError: x is not a function")
        return Exception("Target page, context or browser has been closed")
    w.handler = handler
    with rb:
        with pytest.raises(TransportError) as ei:
            rb.fetch(URL_N1)
    msg = str(ei.value)
    assert msg == text if how in ("timeout", "network") else msg.startswith("browser failure")


def test_A12_through_the_base_each_is_retried_four_times_and_the_transport_never_sleeps_for_retries(tmp_path):
    e = one_feed(tmp_path)
    e.driver.set_fetch(URL_N1, terror("timeout"))
    with e.rb() as rb:
        res = e.run_nse(e.feed("n01"), SLOT0, rb)
    assert len(e.driver.urls("fetch")) == 4
    assert next(iter(e.gaps(res).values()))["reason"] == "transport error: timeout after 4 attempts"
    # the transport alone, warm session: one session call, no sleep, no retry
    e2 = one_feed(tmp_path / "b")
    with e2.rb() as rb:
        rb.fetch(URL_N1)
        e2.driver.set_fetch(URL_N1, terror("timeout"))
        n, slept = len(e2.driver.urls("fetch")), list(e2.sleeper.sleeps)
        with pytest.raises(Exception):
            rb.fetch(URL_N1)
        assert len(e2.driver.urls("fetch")) == n + 1 and e2.sleeper.sleeps == slept


# ================================================================== A13 / A14 (R7)
PSEUDO = {":authority": HOST, ":method": "GET", ":path": "/x.xml", ":scheme": "https"}


def test_A13_pseudo_headers_are_removed_user_agent_kept_cookie_not_redacted_by_the_transport(tmp_path):
    e = one_feed(tmp_path)
    sent = {**PSEUDO, "user-agent": CHROME_UA, "cookie": "nsit=SECRET1; nseappid=SECRET2",
            "sec-fetch-site": "same-origin"}
    e.driver.sent[URL_N1] = sent
    with e.rb() as rb:
        r = rb.fetch(URL_N1)
    h = dict(r.request_headers)
    assert not [k for k in h if k.startswith(":")]
    assert h["user-agent"] == CHROME_UA and h["sec-fetch-site"] == "same-origin"
    assert h["cookie"] == "nsit=SECRET1; nseappid=SECRET2"


@pytest.mark.planted
def test_A13_the_manifest_redacts_cookie_values_and_no_secret_reaches_files_or_logs(tmp_path, logs):
    e = one_feed(tmp_path)
    e.driver.sent[URL_N1] = {**PSEUDO, "user-agent": CHROME_UA, "cookie": "nsit=SECRET1; nseappid=SECRET2"}
    with e.rb() as rb:
        res = e.run_nse(e.feed("n01"), SLOT0, rb)
    assert list(res.outcomes.values())[0].value == "fetched"
    row = e.nse_manifest()[0]
    assert row["request_headers"]["cookie"] == "nsit=<redacted>; nseappid=<redacted>"
    assert row["request_headers"]["user-agent"] == CHROME_UA and row["access_mode"] == "real_browser"
    text = (e.man / "rss_nse.jsonl").read_text() + json.dumps(logs())
    assert "SECRET1" not in text and "SECRET2" not in text


BAD_SENT = {
    "authorization": {**SENT_OK, "authorization": "Bearer TOPSECRET"},
    "proxy_authorization": {**SENT_OK, "proxy-authorization": "Basic TOPSECRET"},
    "cookie2": {**SENT_OK, "cookie2": "TOPSECRET"},
    "set_cookie": {**SENT_OK, "set-cookie": "TOPSECRET"},
    "65_headers": {f"x-h{i}": "v" for i in range(65)},
    "20kib_value": {**SENT_OK, "x-big": "TOPSECRET" + "a" * (20 * 1024)},
    "nul_value": {**SENT_OK, "x-n": "TOPSECRET\x00"},
    "crlf_value": {**SENT_OK, "x-n": "TOPSECRET\r\nX: y"},
    "non_str_value": {**SENT_OK, "x-n": 5},
    "bad_name": {**SENT_OK, "bad name": "TOPSECRET"},
    "unobserved": None,
}


@pytest.mark.planted
@pytest.mark.parametrize("case", sorted(BAD_SENT))
def test_A14_an_invalid_header_record_is_dropped_the_body_is_still_stored(tmp_path, logs, case):
    e = one_feed(tmp_path)
    e.driver.sent[URL_N1] = BAD_SENT[case]
    with e.rb() as rb:
        r = rb.fetch(URL_N1)
        assert r.request_headers is None
        res = e.run_nse(e.feed("n01"), SLOT0, rb)
    assert list(res.outcomes.values())[0].value == "fetched"
    assert e.nse_manifest()[0]["request_headers"] is None
    recs = logs()
    warn = [x for x in recs if x["event"] == "request_headers_dropped"]
    assert warn and all(x["level"] == "WARNING" for x in warn)
    assert "TOPSECRET" not in json.dumps(recs)


def test_A14_the_manifest_itself_refuses_an_invalid_mapping_so_letting_one_through_would_lose_the_body(tmp_path):
    e = one_feed(tmp_path)
    with pytest.raises(Exception):
        e.store.store("rss_nse", b"x", url=URL_N1, source_version="rss-v1", logical_key="n01/20261127T0900",
                      run_id="r", fetched_at=N0, access_mode="real_browser",
                      request_headers={"authorization": "x"}, final_url=URL_N1, fetch_host="h")


def test_A14_a_sent_headers_lookup_that_raises_also_drops_the_record_not_the_body(tmp_path):
    e = one_feed(tmp_path)
    e.driver.sent[URL_N1] = RuntimeError("cdp gone")
    with e.rb() as rb:
        r = rb.fetch(URL_N1)
    assert r.status == 200 and r.request_headers is None


# ================================================================== A17 (R9)
def test_A17_default_is_headful_and_the_mode_is_logged(tmp_path, logs):
    e = one_feed(tmp_path)
    with e.rb() as rb:
        rb.fetch(URL_N1)
    assert e.driver.launches[0]["headless"] is False
    assert fields_of(logs(), "browser_launch", "INFO")[0]["headless"] is False


def test_A17_headless_flag_is_passed_through(tmp_path, logs):
    e = one_feed(tmp_path)
    with e.rb(headless=True) as rb:
        rb.fetch(URL_N1)
    assert e.driver.launches[0]["headless"] is True
    assert fields_of(logs(), "browser_launch", "INFO")[0]["headless"] is True


@pytest.mark.planted
def test_A17_no_code_path_changes_the_mode_after_a_block_or_a_failure(tmp_path):
    from investos.acquire.browser import AnchorResult
    e = one_feed(tmp_path)
    e.driver.anchors = [AnchorResult(403, ANCHOR)]
    with e.rb() as rb:
        assert rb.fetch(URL_N1).status == 403
        rb.fetch(URL_N1)
    assert len(e.driver.launches) == 1
    e2 = one_feed(tmp_path / "b")
    e2.driver.set_fetch(URL_N1, terror("browser failure: crashed"), fetch_ok(b"<x/>", url=URL_N1))
    with e2.rb() as rb:
        with pytest.raises(Exception):
            rb.fetch(URL_N1)
        rb.fetch(URL_N1)
    assert [l["headless"] for l in e2.driver.launches] == [False, False]


# ================================================================== A18 (R10)
def test_A18_a_cycle_that_needs_no_browser_never_launches_and_creates_no_profile(tmp_path):
    e = BrowserEnv(tmp_path)
    e.serve("rbi_press")
    for f in ("n01", "n02", "n03"):
        e.fresh_state(f, N0, last_ok_slot=SLOT0)
    with e.rb() as rb:
        e.cycle(N0, browser=rb)
    assert e.driver.launches == [] and not e.profile_dir.exists()
    rows = [dict(r, enabled="no") if r["feed_id"].startswith("n") else r for r in e.rows]
    e2 = BrowserEnv(tmp_path / "b", rows)
    e2.serve("rbi_press")
    with e2.rb() as rb:
        e2.cycle(N0, browser=rb)
    assert e2.driver.launches == [] and not e2.profile_dir.exists()


def test_A18_two_feeds_one_launch_one_session_and_close_twice_is_harmless(tmp_path):
    e = BrowserEnv(tmp_path, nse_rows(2))
    rb = e.rb()
    e.cycle(N0, browser=rb)
    assert len(e.driver.launches) == 1
    rb.close()
    rb.close()
    assert [s.close_calls for s in e.driver.sessions] == [1]
    assert len(e.driver.urls("anchor")) == 1


@pytest.mark.planted
@pytest.mark.parametrize("exc", [RuntimeError("boom"), KeyboardInterrupt()], ids=["runtime", "keyboard"])
def test_A18_close_runs_when_the_with_block_is_left_by_an_exception(tmp_path, exc):
    e = one_feed(tmp_path)
    with pytest.raises(type(exc)):
        with e.rb() as rb:
            rb.fetch(URL_N1)
            raise exc
    assert [s.close_calls for s in e.driver.sessions] == [1] and not lock_is_held(e.lock_path)


# ================================================================== A19 (R10)
@pytest.mark.planted
def test_A19_a_failed_launch_is_not_repeated_in_the_same_cycle_and_later_fetches_fail_at_once(tmp_path):
    from investos.acquire.base import TransportError
    e = one_feed(tmp_path)
    e.driver.launch_errors = [terror("browser unavailable: no chrome")]
    rb = e.rb()
    with pytest.raises(TransportError, match="browser unavailable: no chrome"):
        rb.fetch(URL_N1)
    t, sleeps = e.mono.t, list(e.sleeper.sleeps)
    for _ in range(3):
        with pytest.raises(TransportError, match="browser unavailable: no chrome"):
            rb.fetch(URL_N1 + "?x")
    assert len(e.driver.launches) == 1 and e.mono.t == t and e.sleeper.sleeps == sleeps
    rb.close()
    assert not lock_is_held(e.lock_path)
    rb2 = e.rb()                                                          # the next cycle tries again
    rb2.fetch(URL_N1)
    assert len(e.driver.launches) == 2
    rb2.close()


def test_A19_through_the_base_the_first_feed_is_unreadable_after_four_attempts_with_one_launch(tmp_path):
    e = BrowserEnv(tmp_path, nse_rows(3))
    e.driver.launch_errors = [terror("browser unavailable: no chrome")]
    with e.rb() as rb:
        r = e.cycle(N0, browser=rb)
    assert len(e.driver.launches) == 1
    reasons = {g["feed_id"]: g["reason"] for g in e.gap_lines()}
    assert reasons["n01"] == "transport error: browser unavailable: no chrome after 4 attempts"
    assert r.outcomes["n01"] == "unreadable"


@pytest.mark.planted
def test_A19_a_browser_failure_relaunches_once_a_timeout_does_not(tmp_path):
    e = one_feed(tmp_path)
    e.driver.set_fetch(URL_N1, terror("timeout"), fetch_ok(b"<x/>", url=URL_N1))
    with e.rb() as rb:
        with pytest.raises(Exception):
            rb.fetch(URL_N1)
        rb.fetch(URL_N1)
    assert len(e.driver.launches) == 1
    e2 = one_feed(tmp_path / "b")
    e2.driver.set_fetch(URL_N1, terror("browser failure: renderer crashed"), fetch_ok(b"<x/>", url=URL_N1))
    with e2.rb() as rb:
        with pytest.raises(Exception):
            rb.fetch(URL_N1)
        assert rb.fetch(URL_N1).status == 200
    assert len(e2.driver.launches) == 2 and e2.driver.urls("anchor") == [ANCHOR, ANCHOR]


# ================================================================== A20 (R11)
def test_A20_named_timeouts_reach_the_session(tmp_path):
    e = BrowserEnv(tmp_path, nse_rows(1), timeout_s=12)
    with e.rb() as rb:
        rb.fetch(URL_N1)
    assert e.driver.launches[0]["timeout_s"] == 60
    assert e.driver.kinds("anchor")[0][2] == 12
    assert e.driver.kinds("fetch")[0][2] == 12
    assert e.driver.kinds("session_close")[0][1] == 15


@pytest.mark.planted
def test_A20_a_fetch_that_never_returns_is_killed_by_the_watchdog_from_its_own_thread(tmp_path):
    from investos.acquire.base import TransportError
    e = BrowserEnv(tmp_path, nse_rows(1), timeout_s=30)
    main_id = threading.get_ident()
    killed = threading.Event()
    e.reaper.on_reap = lambda: killed.set() if threading.get_ident() != main_id else None

    def hang(sess):
        e.mono.t += 30 + 11                                    # past timeout_s + 10 on the injected monotonic clock
        assert killed.wait(20), "the watchdog never ran the reaper"
        raise TransportError("browser failure: Target closed")
    e.driver.set_fetch(URL_N1, hang, fetch_ok(b"<x/>", url=URL_N1))
    rb = e.rb()
    with pytest.raises(TransportError, match="browser failure"):
        rb.fetch(URL_N1)
    from_watchdog = [c for c in e.reaper.calls if c[2] != main_id]
    assert len(from_watchdog) == 1
    assert e.driver.sessions[0].thread_ids == {main_id}              # Playwright objects only on the calling thread
    assert rb.fetch(URL_N1).status == 200 and len(e.driver.launches) == 2    # the session was flagged dead
    rb.close()


def test_A20_a_normal_slow_fetch_does_not_wake_the_watchdog(tmp_path):
    e = BrowserEnv(tmp_path, nse_rows(1), timeout_s=30)
    main_id = threading.get_ident()
    e.driver.set_fetch(URL_N1, Cost(35, fetch_ok(b"<x/>", url=URL_N1)))
    with e.rb() as rb:
        assert rb.fetch(URL_N1).status == 200
    assert [c for c in e.reaper.calls if c[2] != main_id] == []


# ================================================================== A21 (R11)
def profile_of(tmp):
    return tmp / "ledger" / ".state" / "chrome-profile"


def proc_env(tmp):
    e = BrowserEnv(tmp, nse_rows(1), with_procs=True)
    p = str(e.profile_dir)
    t = e.procs
    ours = [t.add(f"/Applications/Google Chrome.app/Contents/MacOS/Google Chrome --user-data-dir={p} --type={k}",
                  ignore_term=(k == "renderer")) for k in ("browser", "gpu", "renderer")]
    others = [t.add("/Applications/Google Chrome.app/Contents/MacOS/Google Chrome --user-data-dir=/Users/x/Library/Chrome"),
              t.add(f"python3 tool.py {p}"),
              t.add(f"/Applications/Google Chrome --user-data-dir={p}-old --type=browser")]
    return e, ours, others


@pytest.mark.planted
def test_A21_only_exact_user_data_dir_tokens_are_signalled_term_first_kill_for_survivors_only(tmp_path):
    import signal
    e, ours, others = proc_env(tmp_path)
    n = e.ps_reaper().reap(e.profile_dir, grace_s=5.0)
    assert n == 3
    sigs = e.procs.signals
    assert {p for p, _ in sigs} == set(ours)
    assert [s for _, s in sigs] == [signal.SIGTERM] * 3 + [signal.SIGKILL]
    assert sigs[-1][0] == ours[2]                                               # only the one ignoring TERM
    assert sum(e.sleeper.sleeps) >= 5.0 - 1e-9                                  # waited grace_s on the fake clock
    assert set(e.procs.procs) == set(others)


def test_A21_no_kill_when_everyone_dies_on_term(tmp_path):
    import signal
    e, ours, others = proc_env(tmp_path)
    e.procs.procs[ours[2]]["ignore_term"] = False
    assert e.ps_reaper().reap(e.profile_dir, grace_s=5.0) == 3
    assert all(s == signal.SIGTERM for _, s in e.procs.signals)


@pytest.mark.planted
@pytest.mark.parametrize("how", ["oserror", "garbage"])
def test_A21_a_failing_or_unparseable_ps_warns_returns_zero_and_never_raises(tmp_path, logs, how):
    e, ours, others = proc_env(tmp_path)
    if how == "oserror":
        e.procs.list_error = OSError("ps not found")
    else:
        e.procs.garbage = True
    assert e.ps_reaper().reap(e.profile_dir, grace_s=5.0) == 0
    assert e.procs.signals == []
    assert [x["level"] for x in logs() if x["event"] == "reaper_failed"] == ["WARNING"]


@pytest.mark.planted
def test_A21_the_reaper_runs_at_start_at_close_and_after_a_normal_cycle_nothing_is_left(tmp_path):
    e = BrowserEnv(tmp_path, nse_rows(2), with_procs=True)
    rb = e.rb(reaper=e.ps_reaper())
    e.cycle(N0, browser=rb)
    assert e.procs.matching(e.profile_dir) != []          # the cycle leaves Chrome to its owner
    rb.close()
    assert e.procs.matching(e.profile_dir) == []
    e2 = one_feed(tmp_path / "b")
    with e2.rb() as rb:
        rb.fetch(URL_N1)
    seq = dedupe(kinds_of(e2.log, "reap", "launch", "session_close"))
    assert seq == ["reap", "launch", "session_close", "reap"]


def test_A21_a_leftover_chrome_of_a_crashed_earlier_cycle_is_cleared_before_the_launch(tmp_path):
    e = BrowserEnv(tmp_path, nse_rows(1), with_procs=True)
    stale = e.procs.add(f"Google Chrome --user-data-dir={e.profile_dir} --type=browser")
    seen = {}
    orig = e.driver.launch

    def spy(profile_dir, headless, timeout_s):
        seen["stale_alive"] = stale in e.procs.procs
        return orig(profile_dir, headless, timeout_s)
    e.driver.launch = spy
    with e.rb(reaper=e.ps_reaper()) as rb:
        rb.fetch(URL_N1)
    assert seen["stale_alive"] is False


# ================================================================== A22 (R12)
def test_A22_profile_is_created_0700_and_the_lock_is_held_from_before_launch_until_close(tmp_path):
    e = one_feed(tmp_path)
    assert not e.profile_dir.exists()
    rb = e.rb()
    rb.fetch(URL_N1)
    l = e.driver.launches[0]
    assert l["profile_dir"] == e.profile_dir and l["mode"] == 0o700 and l["lock_held"] is True
    assert lock_is_held(e.lock_path)
    rb.close()
    assert not lock_is_held(e.lock_path)


def test_A22_the_lock_is_released_after_a_launch_exception(tmp_path):
    e = one_feed(tmp_path)
    e.driver.launch_errors = [terror("browser unavailable: boom")]
    rb = e.rb()
    with pytest.raises(Exception):
        rb.fetch(URL_N1)
    assert not lock_is_held(e.lock_path)


@pytest.mark.planted
def test_A22_a_busy_profile_fails_closed_after_at_most_5_s(tmp_path):
    import fcntl
    import os
    from investos.acquire.base import TransportError
    e = one_feed(tmp_path)
    e.lock_path.parent.mkdir(parents=True, exist_ok=True)
    fd = os.open(e.lock_path, os.O_RDWR | os.O_CREAT, 0o644)
    fcntl.flock(fd, fcntl.LOCK_EX)
    try:
        t0 = e.mono.t
        with pytest.raises(TransportError, match="browser unavailable: profile busy"):
            e.rb().fetch(URL_N1)
        assert 5.0 - 1e-9 <= e.mono.t - t0 <= 6.0
        assert e.driver.launches == []
    finally:
        os.close(fd)


def test_A22_chrome_profile_and_lock_are_git_ignored():
    import shutil
    import subprocess
    from _rss_helpers import REPO
    if shutil.which("git") is None or not (REPO / ".git").exists():
        pytest.skip("not a git work tree")
    for p in ("data/ledger/.state/chrome-profile/Default/Cookies", "data/ledger/.state/chrome-profile.lock"):
        r = subprocess.run(["git", "check-ignore", "-q", p], cwd=REPO)
        assert r.returncode == 0, p


# ================================================================== A36 (R10, R11)
@pytest.mark.planted
def test_A36_a_chrome_that_dies_mid_cycle_is_replaced_once_and_nothing_is_left_behind(tmp_path):
    e = BrowserEnv(tmp_path, nse_rows(23), with_procs=True)
    url7 = NSE_REAL + "F07.xml"

    def die(sess):
        sess.dead = True                                   # Chrome hangs: its processes are still there
        raise terror("browser failure: renderer crashed")
    e.driver.set_fetch(url7, die, fetch_ok(body_of(7), url=url7))
    rb = e.rb(reaper=e.ps_reaper())
    r = e.cycle(N0, browser=rb)
    rb.close()
    assert [r.outcomes[f"n{i:02d}"] for i in range(1, 24)] == ["fetched"] * 23
    assert len(e.driver.launches) == 2
    assert e.driver.sessions[1].close_calls == 1 and e.driver.sessions[0].close_calls <= 1
    assert e.procs.matching(e.profile_dir) == [] and not lock_is_held(e.lock_path)
