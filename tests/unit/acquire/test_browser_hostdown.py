"""S1-T7 step 5: the host-down rule inside poll_cycle (spec collect_browser R16; A29, A30, A31).
A browser double (fetch(url) only) with a fake clock; the honest feeds use the usual fake transport."""
from __future__ import annotations

from datetime import timedelta

import pytest

from _browser_helpers import HOST, N0, SLOT0, URL_SEBI, BrowserEnv, TimedBrowser, TimedTransport, bresp, nse_rows
from _collector_helpers import logs, resp, terr  # noqa: F401
from _rss_helpers import NSE_REAL, URL_RBI, body_of, fields_of, frow

N1, N2, N3 = (NSE_REAL + f"F0{i}.xml" for i in (1, 2, 3))


def hd_rows():
    return nse_rows(3) + [frow("rbi_press", URL_RBI, kind="news", publisher="RBI", scope="always", stale="525600"),
                          frow("sebi_press", URL_SEBI, kind="news", publisher="SEBI", scope="always", stale="525600")]


def hd_env(tmp, *, cost=0.0, rows=None):
    e = BrowserEnv(tmp, rows if rows is not None else hd_rows())
    e.serve("rbi_press")
    e.serve("sebi_press")
    return e, TimedBrowser(e.mono, cost)


def reasons(e):
    return {g["feed_id"]: g["reason"] for g in e.gap_lines()}


# ------------------------------------------------------------------ A29
@pytest.mark.planted
def test_A29_a_dead_host_costs_one_feeds_four_attempts_and_the_rest_of_its_feeds_are_skipped(tmp_path, logs, monkeypatch):
    from investos.acquire.rss import RssNseFeedCollector
    built = []
    orig = RssNseFeedCollector.__init__
    monkeypatch.setattr(RssNseFeedCollector, "__init__", lambda self, *a, **k: (built.append(1), orig(self, *a, **k))[0])
    e, tb = hd_env(tmp_path, cost=30.0)
    tb.default = [terr("timeout")]
    r = e.cycle(N0, browser=tb)
    assert tb.urls == [N1] * 4 and len(built) == 1
    assert r.outcomes["n01"] == "unreadable" and r.outcomes["n02"] == "not_attempted" \
        and r.outcomes["n03"] == "not_attempted"
    assert r.outcomes["rbi_press"] == "fetched" and r.outcomes["sebi_press"] == "fetched"
    assert r.down_hosts == (HOST,)
    rs = reasons(e)
    assert rs["n01"] == "transport error: timeout after 4 attempts"
    assert rs["n02"] == rs["n03"] == f"host down: {HOST}"
    assert [g["kind"] for g in e.gap_lines()] == ["failed"] * 3
    assert all(e.state(f)["consecutive_failures"] == 1 for f in ("n01", "n02", "n03"))
    recs = logs()
    hd = fields_of(recs, "host_down", "INFO")
    assert len(hd) == 1 and hd[0]["host"] == HOST and hd[0]["probe_feed"] == "n01" and hd[0]["skipped"] == 2
    assert fields_of(recs, "cycle_end")[0]["failed"] == 3
    assert e.mono.t < 150                                       # one feed's 4 x 30 s + 7 s of backoff, not three


@pytest.mark.planted
def test_A29_the_rule_applies_to_honest_hosts_too(tmp_path):
    rows = [frow(f"r{i}", f"https://www.rbi.org.in/p{i}.xml", kind="news", publisher="RBI", scope="always",
                 stale="525600") for i in (1, 2, 3)] + [frow("sebi_press", URL_SEBI, kind="news", publisher="SEBI",
                                                           scope="always", stale="525600")]
    e = BrowserEnv(tmp_path, rows)
    e.transport = TimedTransport(e.mono)
    for i in (1, 2, 3):
        e.transport.set(f"https://www.rbi.org.in/p{i}.xml", terr("timeout"))
    e.serve("sebi_press")
    r = e.cycle(N0)
    assert [c["url"] for c in e.transport.calls].count("https://www.rbi.org.in/p2.xml") == 0
    assert len(e.transport.calls) == 4 + 1
    assert r.outcomes["r2"] == r.outcomes["r3"] == "not_attempted" and r.outcomes["sebi_press"] == "fetched"
    assert r.down_hosts == ("www.rbi.org.in",)


# ------------------------------------------------------------------ A30: only a transport failure after four attempts is "down"
@pytest.mark.planted
def test_A30_http_503_after_four_attempts_is_not_host_down(tmp_path):
    e, tb = hd_env(tmp_path)
    tb.set(N1, bresp(503, b"busy", url=N1))
    r = e.cycle(N0, browser=tb)
    assert "http 503 after 4 attempts" in reasons(e)["n01"]
    assert tb.urls == [N1] * 4 + [N2, N3] and r.down_hosts == ()
    assert r.outcomes["n02"] == r.outcomes["n03"] == "fetched"


@pytest.mark.planted
def test_A30_a_block_is_not_host_down_it_keeps_the_bases_blocked_reason(tmp_path):
    e, tb = hd_env(tmp_path)
    tb.set(N1, bresp(403, b"denied", url=N1))
    r = e.cycle(N0, browser=tb)
    assert tb.urls == [N1] and r.down_hosts == ()
    assert r.outcomes["n02"] == r.outcomes["n03"] == "not_attempted"
    rs = reasons(e)
    assert "blocked" in rs["n02"] and "host down" not in rs["n02"] and "host down" not in rs["n03"]
    assert [ev["event"] for ev in e.host_events(HOST)] == ["blocked"]


@pytest.mark.planted
def test_A30_an_unreadable_body_is_not_host_down(tmp_path):
    e, tb = hd_env(tmp_path)
    tb.set(N1, bresp(200, b"<html>challenge</html>", url=N1))
    r = e.cycle(N0, browser=tb)
    assert r.outcomes["n01"] == "unreadable" and r.outcomes["n02"] == r.outcomes["n03"] == "fetched"
    assert r.down_hosts == ()


@pytest.mark.planted
def test_A30_a_feed_that_worked_earlier_in_the_cycle_stays_ok_when_a_later_one_times_out(tmp_path):
    e, tb = hd_env(tmp_path)
    tb.set(N2, terr("timeout"))
    r = e.cycle(N0, browser=tb)
    assert r.outcomes["n01"] == "fetched" and r.outcomes["n02"] == "unreadable" and r.outcomes["n03"] == "not_attempted"
    assert reasons(e)["n03"] == f"host down: {HOST}" and r.down_hosts == (HOST,)
    assert e.state("n01")["last_ok_slot"] == SLOT0 and e.state("n01")["consecutive_failures"] == 0


@pytest.mark.planted
def test_A30_nothing_is_persisted_the_next_cycle_probes_the_host_again(tmp_path):
    e, tb = hd_env(tmp_path)
    tb.default = [terr("timeout")]
    e.cycle(N0, browser=tb)
    tb2 = TimedBrowser(e.mono)                                   # the host is back
    r = e.cycle(N0 + timedelta(minutes=15), browser=tb2)
    assert tb2.urls == [N1, N2, N3] and r.down_hosts == ()
    assert [r.outcomes[f] for f in ("n01", "n02", "n03")] == ["fetched"] * 3


@pytest.mark.planted
def test_A30_a_probe_that_is_still_down_the_next_cycle_costs_one_feed_again(tmp_path):
    e, tb = hd_env(tmp_path)
    tb.default = [terr("timeout")]
    e.cycle(N0, browser=tb)
    tb2 = TimedBrowser(e.mono)
    tb2.default = [terr("timeout")]
    e.cycle(N0 + timedelta(minutes=15), browser=tb2)
    assert tb2.urls == [N1] * 4


# ------------------------------------------------------------------ A31: the incident of 28 Nov
@pytest.mark.planted
def test_A31_without_the_rule_the_cycle_budget_is_eaten_with_it_one_feed_pays(tmp_path):
    # pre-rule behaviour, emulated: five feeds on five different dead hosts (the rule cannot join them)
    rows = [frow(f"d{i}", f"https://dead{i}.example.test/x.xml", scope="always", stale="525600") for i in range(1, 6)]
    e = BrowserEnv(tmp_path / "without", rows)
    e.transport = TimedTransport(e.mono, cost=38.0)
    for r_ in rows:
        e.transport.set(r_["url"], terr("timeout"))
    e.cycle(N0)
    assert "cycle budget" in reasons(e)["d5"] and all("cycle budget" not in reasons(e)[f"d{i}"] for i in range(1, 5))
    # with the rule: five feeds on one dead host, the other host's feeds are still polled
    e2, tb = hd_env(tmp_path / "with", cost=38.0, rows=nse_rows(5) + hd_rows()[3:])
    tb.default = [terr("timeout")]
    r = e2.cycle(N0, browser=tb)
    assert tb.urls == [NSE_REAL + "F01.xml"] * 4 and e2.mono.t < 600
    assert all("cycle budget" not in x for x in reasons(e2).values())
    assert [r.outcomes[f"n0{i}"] for i in range(2, 6)] == ["not_attempted"] * 4
    assert r.outcomes["rbi_press"] == r.outcomes["sebi_press"] == "fetched"
