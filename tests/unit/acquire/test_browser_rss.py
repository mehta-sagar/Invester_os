"""S1-T7 step 4-6: routing, the NSE adapter inside poll_cycle, source-aware check, rate/cap, coalescing, CLI
(spec collect_browser R13, R14, R17; A17 CLI, A18 CLI, A23-A27, A32)."""
from __future__ import annotations

import os
import signal
import sys
from datetime import timedelta
from types import SimpleNamespace
from urllib.parse import urlsplit

import pytest

from _browser_helpers import (ACCESS, HOST, N0, SLOT0, URL_N1, URL_SEBI, BrowserEnv, TimedBrowser, TimedTransport, bresp,
                              fetch_ok, nse_rows)
from _collector_helpers import events, logs, resp  # noqa: F401
from _rss_helpers import (NSE_REAL, SHIPPED_FEEDS, URL_RBI, RssEnv, body_of, fields_of, fmt_ts, frow, ist,
                          make_rss, numbered, run_check, set_check_clock, write_feeds)


def terror(msg):
    from investos.acquire.base import TransportError
    return TransportError(msg)


# ================================================================== A23 (R13): routing in code
def test_A23_routing_by_url_host_for_the_shipped_table():
    from investos.acquire.rss import RssFeedCollector, RssNseFeedCollector, collector_class_for, load_feeds
    feeds = load_feeds(SHIPPED_FEEDS)
    nse = [f for f in feeds if urlsplit(f.url).hostname == HOST]
    assert len(nse) == 23 and all(collector_class_for(f) is RssNseFeedCollector for f in nse)
    by = {f.feed_id: f for f in feeds}
    assert collector_class_for(by["rbi_press"]) is RssFeedCollector
    assert collector_class_for(by["sebi_press"]) is RssFeedCollector


@pytest.mark.planted
@pytest.mark.parametrize("col", ["access_mode", "mode"])
def test_A23_a_feed_table_cannot_choose_its_access_mode(tmp_path, col):
    from investos.acquire.rss import FeedTableError, load_feeds
    from _rss_helpers import FEED_HEADER
    p = write_feeds(tmp_path / "t.csv", [dict(frow("a1"), **{col: "real_browser"})], header=FEED_HEADER + "," + col)
    with pytest.raises(FeedTableError):
        load_feeds(p)


@pytest.mark.planted
@pytest.mark.parametrize("url", ["https://www.rbi.org.in/pressreleases_rss.xml", "http://nsearchives.nseindia.com/x.xml",
                                 "ftp://nsearchives.nseindia.com/x.xml", "https://nsearchives.nseindia.com.evil.test/x",
                                 "https://nsearchives.nseindia.com@evil.test/x", "file:///etc/passwd", ""])
def test_A23_the_transport_refuses_other_hosts_and_schemes_before_any_browser_work(tmp_path, url):
    e = BrowserEnv(tmp_path, nse_rows(1))
    rb = e.rb()
    with pytest.raises(ValueError):
        rb.fetch(url)
    assert e.driver.launches == [] and not e.profile_dir.exists() and [x for x in e.log if x[0] == "launch"] == []


def test_A23_both_classes_are_registered_and_a_second_rss_nse_source_is_refused():
    from investos.acquire.base import Collector
    from investos.acquire.registry import adapters, register_adapter
    from investos.acquire.rss import RssFeedCollector, RssNseFeedCollector
    listed = adapters()
    assert RssFeedCollector in listed and RssNseFeedCollector in listed
    assert not any(c.date_keyed for c in (RssFeedCollector, RssNseFeedCollector))
    other = type("OtherNse", (Collector,), {"source": "rss_nse", "source_version": "x",
                                            "keys": lambda s, r: [], "url": lambda s, k: "",
                                            "check_body": lambda s, k, r: None})
    with pytest.raises(ValueError):
        register_adapter(other)


# ================================================================== A24 (R13): the cycle routes each feed to its own transport
def route_env(tmp_path):
    rows = [frow("n01", NSE_REAL + "F01.xml", scope="always", stale="525600"),
            frow("rbi_press", URL_RBI, kind="news", publisher="RBI", scope="always", stale="525600")]
    e = BrowserEnv(tmp_path, rows)
    e.serve("rbi_press")
    return e


def assert_routed(browser_urls, honest_urls):
    assert browser_urls == [NSE_REAL + "F01.xml"] and honest_urls == [URL_RBI]


@pytest.mark.planted
def test_A24_nse_goes_to_the_browser_rbi_to_the_honest_transport_with_their_own_sources(tmp_path):
    e = route_env(tmp_path)
    tb = TimedBrowser(e.mono)
    r = e.cycle(N0, browser=tb)
    assert r.outcomes == {"n01": "fetched", "rbi_press": "fetched"}
    assert_routed(tb.urls, [c["url"] for c in e.transport.calls])
    assert len(e.nse_manifest()) == 1 and len(e.rss_manifest()) == 1
    assert e.nse_manifest()[0]["logical_key"] == f"n01/{SLOT0}" and e.nse_manifest()[0]["access_mode"] == "real_browser"
    assert e.rss_manifest()[0]["logical_key"] == f"rbi_press/{SLOT0}" and e.rss_manifest()[0]["access_mode"] == "honest"
    s1, s2 = e.sidecar_for("n01"), e.sidecar_for("rbi_press")
    assert (s1["source"], s1["access_mode"]) == ("rss_nse", "real_browser")
    assert (s2["source"], s2["access_mode"]) == ("rss", "honest")
    assert e.nse_manifest()[0]["fetch_host"] and e.nse_manifest()[0]["final_url"] == NSE_REAL + "F01.xml"


@pytest.mark.planted
def test_A24_a_mis_routed_feed_is_caught_by_the_routing_check():
    with pytest.raises(AssertionError):
        assert_routed([URL_RBI], [NSE_REAL + "F01.xml"])


def test_A24_state_and_gap_ledger_are_shared_between_both_sources(tmp_path):
    e = route_env(tmp_path)
    e.cycle(N0, browser=TimedBrowser(e.mono))
    assert e.state("n01")["last_ok_slot"] == SLOT0 and e.state("rbi_press")["last_ok_slot"] == SLOT0
    assert e.state("n01")["last_stored_sha"] == e.nse_manifest()[0]["sha256"]
    e.cycle(N0, browser=TimedBrowser(e.mono))                        # same slot again: nothing is polled
    assert len(e.nse_manifest()) == 1


# ================================================================== A25 (R13): missing transport / missing access table
@pytest.mark.planted
def test_A25_without_a_browser_the_nse_feeds_are_failed_slots_and_the_honest_feed_is_polled(tmp_path, logs, monkeypatch):
    from investos.acquire.rss import RssNseFeedCollector
    built = []
    orig = RssNseFeedCollector.__init__
    monkeypatch.setattr(RssNseFeedCollector, "__init__", lambda self, *a, **k: (built.append(1), orig(self, *a, **k))[0])
    e = BrowserEnv(tmp_path)
    e.serve("rbi_press")
    r = e.cycle(N0)                                                  # browser=None
    assert built == []
    assert [c["url"] for c in e.transport.calls] == [URL_RBI]
    assert r.outcomes["rbi_press"] == "fetched"
    gl = {g["feed_id"]: g for g in e.gap_lines()}
    assert set(gl) == {"n01", "n02", "n03"}
    assert all(g["kind"] == "failed" and g["reason"] == "browser transport not configured" for g in gl.values())
    assert e.state("n01")["consecutive_failures"] == 1
    warn = [x for x in logs() if x["event"] == "browser_not_configured"]
    assert len(warn) == 1 and warn[0]["level"] == "WARNING"


@pytest.mark.planted
def test_A25_a_missing_access_table_is_one_loud_error_per_cycle_and_the_other_feeds_run(tmp_path, logs):
    e = BrowserEnv(tmp_path, access=None)
    e.serve("rbi_press")
    tb = TimedBrowser(e.mono)
    r = e.cycle(N0, browser=tb)
    assert tb.urls == [] and r.outcomes["rbi_press"] == "fetched"
    err = [x for x in logs() if x["event"] == "access_table_missing"]
    assert len(err) == 1 and err[0]["level"] == "ERROR"
    gl = {g["feed_id"]: g for g in e.gap_lines()}
    assert set(gl) == {"n01", "n02", "n03"} and all(g["kind"] == "failed" and "rss_nse" in g["reason"] for g in gl.values())


# ================================================================== A26 (R13): the check reads both manifests
NOW_CHK = ist(2026, 11, 28, 12, 0, 30)
CHECK_ROWS = [frow("n01", NSE_REAL + "F01.xml", scope="always", stale="525600"),
              frow("rbi_press", URL_RBI, kind="news", publisher="RBI", scope="always", stale="5760")]


@pytest.fixture
def ce(tmp_path, monkeypatch):
    env = BrowserEnv(tmp_path, CHECK_ROWS)
    set_check_clock(monkeypatch, NOW_CHK)
    for fid in ("n01", "rbi_press"):
        env.fresh_state(fid, NOW_CHK)
    return env


def put(e, source, feed_id, slot="20261127T0900"):
    url = {"n01": NSE_REAL + "F01.xml", "rbi_press": URL_RBI}.get(feed_id, "https://example.test/x.xml")
    browser = source == "rss_nse"
    e.store.store(source, body_of(1), url=url, source_version="rss-v1", logical_key=f"{feed_id}/{slot}",
                  run_id="seed", fetched_at=ist(2026, 11, 27, 9, 0, 30),
                  access_mode="real_browser" if browser else "honest",
                  request_headers=None if browser else {}, final_url=url, fetch_host="h")


def healthy(e):
    put(e, "rss_nse", "n01")
    put(e, "rss", "rbi_press")


def test_A26_a_healthy_fixture_with_keys_in_both_manifests_passes(ce):
    healthy(ce)
    code, out, rep = run_check(ce)
    assert code == 0, out + rep


@pytest.mark.planted
@pytest.mark.parametrize("source", ["rss", "rss_nse"])
def test_A26_an_unknown_feed_key_in_either_manifest_fails(ce, source):
    healthy(ce)
    put(ce, source, "ghost")
    code, out, rep = run_check(ce)
    assert code == 1 and "ghost" in rep


@pytest.mark.planted
@pytest.mark.parametrize("source,feed", [("rss", "n01"), ("rss_nse", "rbi_press")])
def test_A26_a_feed_whose_keys_sit_under_the_wrong_source_is_a_ghost(ce, source, feed):
    healthy(ce)
    put(ce, source, feed, slot="20261127T0915")
    code, out, rep = run_check(ce)
    assert code == 1 and feed in rep and "ghost" in rep.lower()


@pytest.mark.planted
@pytest.mark.parametrize("source", ["rss", "rss_nse"])
def test_A26_a_malformed_key_in_either_manifest_fails(ce, source):
    healthy(ce)
    ce.store.store(source, b"x", url=NSE_REAL + "F01.xml", source_version="rss-v1", logical_key="n01/2026-11-27",
                   run_id="seed", fetched_at=ist(2026, 11, 27, 9, 0, 30),
                   access_mode="real_browser" if source == "rss_nse" else "honest",
                   request_headers=None if source == "rss_nse" else {}, final_url="https://x.test/", fetch_host="h")
    code, out, rep = run_check(ce)
    assert code == 1 and "malformed key" in rep


def test_A26_the_planted_run_is_still_caught_and_extract_items_merges_two_sources(ce):
    code, out, _ = run_check(ce, ("rss", "--planted"))
    assert code == 0 and "caught" in [l for l in out.splitlines() if l.startswith("rss")][0]
    from investos.acquire.manifest import ManifestEntry
    from investos.parse.rss_items import extract_items
    from _rss_helpers import sha
    a, b = make_rss([numbered(1, "27-Nov-2026 10:00:00"), numbered(2, "27-Nov-2026 10:00:00")]), body_of(2, 3)
    rows = [(ManifestEntry(sha256=sha(a), size=len(a), url=NSE_REAL + "F01.xml", fetched_at=ist(2026, 11, 27, 9, 0),
                           source="rss_nse", source_version="rss-v1", logical_key="n01/20261127T0900", run_id="r"), a),
            (ManifestEntry(sha256=sha(b), size=len(b), url=URL_RBI, fetched_at=ist(2026, 11, 27, 9, 15),
                           source="rss", source_version="rss-v1", logical_key="rbi_press/20261127T0915", run_id="r"), b)]
    items = list(extract_items(iter(rows)))
    assert len({(i.feed_id, i.item_id) for i in items}) == len(items) == 4
    assert {i.feed_id for i in items} == {"n01", "rbi_press"}
    assert all(i.first_seen_at == ist(2026, 11, 27, 9, 0) for i in items if i.feed_id == "n01")


# ================================================================== A27 (R14): spacing, cap, counter
def full_cycle(tmp, n=23, **kw):
    e = BrowserEnv(tmp, nse_rows(n), launch_cost=2.0, interval=1.0, **kw)
    with e.rb() as rb:
        r = e.cycle(N0, browser=rb)
    return e, r


@pytest.mark.planted
def test_A27_a_23_feed_cycle_is_spaced_2_5_s_apart_anchor_included_and_fits_the_budget(tmp_path):
    e, r = full_cycle(tmp_path)
    assert len(r.polled) == 23 and set(r.outcomes.values()) == {"fetched"}
    starts = [x[-1] for x in e.log if x[0] in ("anchor", "fetch")]
    assert len(starts) == 24 and all(b - a >= 2.5 - 1e-6 for a, b in zip(starts, starts[1:])), starts
    assert e.mono.t <= 23 * 2.5 + 5 + 2.0 + 1e-6


@pytest.mark.planted
def test_A27_the_daily_cap_turns_the_rest_into_failed_slots_never_a_block(tmp_path):
    e = BrowserEnv(tmp_path, nse_rows(4), access={"rss_nse": {"mode": "real_browser", "daily_cap": 3}})
    with e.rb() as rb:
        r = e.cycle(N0, browser=rb)
    assert len(e.driver.urls("fetch")) == 3
    assert r.outcomes["n04"] == "not_attempted"
    assert "daily cap 3 reached" in {g["feed_id"]: g for g in e.gap_lines()}["n04"]["reason"]
    assert e.host_events(HOST) == [] and e.count_file("rss_nse", "2026-11-27").read_text() == "3"


def test_A27_a_retry_counts_as_a_second_attempt_in_the_counter(tmp_path):
    e = BrowserEnv(tmp_path, nse_rows(1))
    e.driver.set_fetch(URL_N1, terror("timeout"), fetch_ok(body_of(1), url=URL_N1))
    with e.rb() as rb:
        r = e.cycle(N0, browser=rb)
    assert r.outcomes["n01"] == "fetched" and e.count_file("rss_nse", "2026-11-27").read_text() == "2"


# ================================================================== A32 (R17): coalescing for a dead host
def seed_failing(e, feed_ids, failures=2):
    for fid in feed_ids:
        e.write_state(fid, last_ok_slot="20261127T0845", last_ok_at=fmt_ts(ist(2026, 11, 27, 8, 45, 30)),
                      last_attempt_at=fmt_ts(ist(2026, 11, 27, 8, 45, 30)), consecutive_failures=failures)


def dead_browser(e):
    tb = TimedBrowser(e.mono)
    tb.default = [terror("timeout")]
    return tb


@pytest.mark.planted
def test_A32_one_notification_for_a_dead_host_with_23_alarms_and_none_the_next_cycle(tmp_path, logs):
    e = BrowserEnv(tmp_path, nse_rows(23))
    ids = [f"n{i:02d}" for i in range(1, 24)]
    seed_failing(e, ids)
    r = e.cycle(N0, browser=dead_browser(e))
    assert e.notifier.calls == [("Invest OS: host down", f"{HOST}: 23 feeds")]
    assert "http" not in e.notifier.calls[0][1]
    assert all(e.state(f)["alerted"] == {"poll_missed": "2026-11-27"} for f in ids)
    assert len([x for x in logs() if x["event"] == "feed_alarm" and x["level"] == "ERROR"]) == 23
    assert len(r.alerts) >= 1
    e.cycle(N0 + timedelta(minutes=15), browser=dead_browser(e))
    assert len(e.notifier.calls) == 1                                 # one "host down" alert per day


@pytest.mark.planted
def test_A32_a_single_alarming_feed_on_its_host_keeps_the_per_feed_message(tmp_path):
    e = BrowserEnv(tmp_path, nse_rows(1))
    seed_failing(e, ["n01"])
    e.cycle(N0, browser=dead_browser(e))
    assert e.notifier.calls == [("Invest OS: feed stale", "n01 poll_missed")]


def test_A32_coalescing_is_per_host(tmp_path):
    rows = nse_rows(2) + [frow("rbi_press", URL_RBI, kind="news", publisher="RBI", scope="always", stale="525600")]
    e = BrowserEnv(tmp_path, rows)
    seed_failing(e, ["n01", "n02", "rbi_press"])
    e.script("rbi_press", resp(503, b"busy"))
    e.cycle(N0, browser=dead_browser(e))
    assert sorted(e.notifier.calls) == sorted([("Invest OS: host down", f"{HOST}: 2 feeds"),
                                               ("Invest OS: feed stale", "rbi_press poll_missed")])


@pytest.mark.planted
def test_A32_content_stale_alarms_are_never_coalesced(tmp_path):
    e = BrowserEnv(tmp_path, [dict(r, stale_after_min="60") for r in nse_rows(2)])
    old = make_rss([numbered(1, "27-Nov-2026 05:00:00")])
    tb = TimedBrowser(e.mono)
    for r in e.rows:
        tb.set(r["url"], bresp(200, old, url=r["url"]))
    e.cycle(N0, browser=tb)
    assert sorted(c[1] for c in e.notifier.calls) == ["n01 content_stale", "n02 content_stale"]
    assert all(c[0] == "Invest OS: feed stale" for c in e.notifier.calls)


# ================================================================== CLI (A17, A18, R15)
@pytest.fixture
def cli(tmp_path, monkeypatch):
    for k in list(os.environ):
        if k.startswith("INVESTOS_"):
            monkeypatch.delenv(k)
    rows = [frow("n01", NSE_REAL + "F01.xml", scope="always", stale="525600"),
            frow("rbi_press", URL_RBI, kind="news", publisher="RBI", scope="always", stale="525600")]
    e = BrowserEnv(tmp_path, rows, contact="ops@example.org", interval=0.001)
    monkeypatch.setenv("INVESTOS_ROOT", str(tmp_path))
    monkeypatch.setenv("INVESTOS_CONFIG", str(tmp_path / "config" / "investos.toml"))
    from investos.acquire import browser as br
    from investos.acquire import transport as tr
    rec = SimpleNamespace(inits=[], fetches=[], closes=0, on_fetch=None, honest=[])
    monkeypatch.setattr(br.RealBrowserTransport, "__init__", lambda self, cfg, **kw: rec.inits.append(kw))

    def fetch(self, url):
        rec.fetches.append(url)
        if rec.on_fetch is not None:
            rec.on_fetch()
        return bresp(200, body_of(1), url=url)
    monkeypatch.setattr(br.RealBrowserTransport, "fetch", fetch)
    monkeypatch.setattr(br.RealBrowserTransport, "close", lambda self: setattr(rec, "closes", rec.closes + 1))
    monkeypatch.setattr(tr.UrllibTransport, "__init__", lambda self, *a, **k: setattr(self, "accept_encoding", "identity"))

    def get(self, url, *, headers, timeout):
        rec.honest.append(url)
        return resp(200, body_of(1), {}, url)
    monkeypatch.setattr(tr.UrllibTransport, "get", get)
    e.rec = rec
    return e


def run(*args):
    from investos.cli import main
    return main(list(args))


def test_A17_the_cli_builds_a_headful_transport_by_default_and_a_headless_one_with_the_flag(cli):
    assert run("collect", "rss", "--once", "--force") == 0
    assert cli.rec.inits[0].get("headless", False) is False
    assert cli.rec.fetches == [NSE_REAL + "F01.xml"] and cli.rec.honest == [URL_RBI]
    assert cli.rec.closes == 1
    cli.rec.inits.clear()
    assert run("collect", "rss", "--once", "--force", "--headless") == 0
    assert cli.rec.inits[0]["headless"] is True


def test_A17_headless_alone_is_still_a_usage_error_and_the_usage_names_the_flag(cli, capsys):
    from investos.cli import COLLECT_USAGE, USAGE
    assert run("collect", "rss", "--headless") == 2
    assert "--headless" in COLLECT_USAGE and "--headless" in USAGE
    assert cli.rec.inits == [] and cli.rec.fetches == []


def test_A18_dry_run_builds_no_transport(cli):
    assert run("collect", "rss", "--once", "--force", "--dry-run") == 0
    assert cli.rec.inits == [] and cli.rec.fetches == []


@pytest.mark.planted
def test_A18_sigterm_reaches_close_through_a_keyboard_interrupt(cli, monkeypatch):
    handlers = {}
    monkeypatch.setattr(signal, "signal", lambda sig, h: handlers.__setitem__(sig, h))
    cli.rec.on_fetch = lambda: handlers[signal.SIGTERM](signal.SIGTERM, None)
    try:
        run("collect", "rss", "--once", "--force")                  # propagating or returning is the CLI's choice
    except KeyboardInterrupt:
        pass
    assert signal.SIGTERM in handlers and cli.rec.closes == 1
    with pytest.raises(KeyboardInterrupt):
        handlers[signal.SIGTERM](signal.SIGTERM, None)


@pytest.mark.planted
def test_A18_a_runtime_error_in_a_fetch_does_not_leave_the_browser_open(cli):
    def boom():
        raise RuntimeError("boom")
    cli.rec.on_fetch = boom
    assert run("collect", "rss", "--once", "--force") in (0, 1)
    assert cli.rec.closes == 1 and cli.rec.honest == [URL_RBI]


@pytest.mark.planted
def test_A18_a_keyboard_interrupt_in_a_fetch_still_closes(cli):
    def stop():
        raise KeyboardInterrupt()
    cli.rec.on_fetch = stop
    with pytest.raises(KeyboardInterrupt):
        run("collect", "rss", "--once", "--force")
    assert cli.rec.closes == 1


def test_A28_without_playwright_the_cli_prints_the_install_hint_once(cli, monkeypatch, capsys):
    monkeypatch.setitem(sys.modules, "playwright", None)
    monkeypatch.setitem(sys.modules, "playwright.sync_api", None)
    run("collect", "rss", "--once", "--force")
    cap = capsys.readouterr()
    assert (cap.out + cap.err).count("pip install 'investos[browser]'") == 1
