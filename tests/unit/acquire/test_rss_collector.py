"""S1-T6 step 4a: RssFeedCollector identity, slots, body check, redirects (R1, R2, R4, R5, R9 pure; A1-A4, A6, A7, A11)."""
from __future__ import annotations

import os
import re
import time
import tomllib
from datetime import datetime, timezone

import pytest

from _collector_helpers import events, logs, resp  # noqa: F401
from _rss_helpers import (EXAMPLE_TOML, RssEnv, UTC, body_of, exact_size_body, feed_row, frow, ist, make_rss, sha,
                          URL_ANN, numbered, item)
from _s1_helpers import CONTACT, S1Env


@pytest.fixture
def renv(tmp_path):
    return RssEnv(tmp_path, [frow("f1", URL_ANN, scope="always")])


SLOT = "20261127T0900"


# ================================================================== R1 / A1 identity
def assert_identity(cls):
    from investos.acquire.base import AccessMode
    assert cls.source == "rss" and cls.source_version == "rss-v1"
    assert cls.access_mode is AccessMode.HONEST
    assert cls.date_keyed is False and cls.date_check_waiver is None
    assert cls.page_limit is None and cls.archive is False and dict(cls.extra_headers) == {}


def test_A1_class_attributes():
    from investos.acquire.rss import RssFeedCollector
    assert_identity(RssFeedCollector)


@pytest.mark.planted
def test_A1_planted_wrong_attributes_are_caught_by_the_guard():
    from investos.acquire.rss import RssFeedCollector
    from investos.acquire.base import AccessMode
    for attrs in ({"date_keyed": True}, {"source": "other"}, {"access_mode": AccessMode.REAL_BROWSER},
                  {"extra_headers": {"X-A": "b"}}, {"archive": True}, {"page_limit": 5}):
        bad = type("Bad", (RssFeedCollector,), attrs)
        with pytest.raises(AssertionError):
            assert_identity(bad)


def test_A1_keys_yields_the_single_key_and_url_is_the_feed_url(renv):
    from investos.acquire.base import RunRange
    f = renv.feed("f1")
    c = renv.collector(f, SLOT)
    from datetime import date
    assert list(c.keys(RunRange(date(2026, 11, 27), date(2026, 11, 27)))) == [f"f1/{SLOT}"]
    assert c.url(f"f1/{SLOT}") == URL_ANN


def test_A1_collector_sets_no_header_and_headers_for_is_empty(renv):
    c = renv.collector(renv.feed("f1"), SLOT)
    assert dict(c.headers_for(f"f1/{SLOT}", URL_ANN)) == {}


@pytest.mark.planted
def test_A1_config_entry_for_rss_that_disagrees_is_refused(tmp_path):
    e = RssEnv(tmp_path, [frow("f1", URL_ANN)], access={"rss": {"mode": "real_browser", "daily_cap": 300}})
    with pytest.raises(ValueError):
        e.collector(e.feed("f1"), SLOT)


def test_R1_example_config_has_no_collect_access_rss_table():
    data = tomllib.loads(EXAMPLE_TOML.read_text())
    assert "rss" not in data.get("collect", {}).get("access", {})


@pytest.mark.planted
@pytest.mark.parametrize("contact", [None, ""])
def test_A1_empty_contact_refuses_the_collector(tmp_path, contact):
    e = RssEnv(tmp_path, [frow("f1", URL_ANN)], contact=contact)
    with pytest.raises(ValueError, match="collect.contact is not configured"):
        e.collector(e.feed("f1"), SLOT)


# ================================================================== A2 request headers
def test_A2_exactly_user_agent_and_accept_encoding(renv):
    from investos import __version__
    renv.serve("f1")
    renv.run_collector(renv.feed("f1"), SLOT)
    h = renv.transport.calls[0]["headers"]
    assert set(h) == {"User-Agent", "Accept-Encoding"}
    assert h["User-Agent"] == f"InvestOS/{__version__} (personal research; {CONTACT})"
    assert renv.transport.calls[0]["url"] == URL_ANN


@pytest.mark.planted
def test_A2_manifest_row_records_honest_mode_and_no_cookie_or_referer(renv):
    renv.serve("f1")
    renv.run_collector(renv.feed("f1"), SLOT)
    row = renv.rss_manifest()[0]
    assert row["access_mode"] == "honest"
    names = {k.lower() for k in (row["request_headers"] or {})}
    assert names == {"user-agent", "accept-encoding"}


# ================================================================== R2 / A3 slots
def test_A3_slot_of_floors_to_15_minutes_in_ist():
    from investos.acquire.rss import slot_of
    assert slot_of(ist(2026, 11, 27, 9, 14, 59)) == "20261127T0900"
    assert slot_of(ist(2026, 11, 27, 9, 15, 0)) == "20261127T0915"
    assert slot_of(datetime(2026, 11, 26, 20, 0, tzinfo=UTC)) == "20261127T0130"
    assert slot_of(ist(2026, 11, 27, 23, 59, 59)) == "20261127T2345"
    assert slot_of(ist(2026, 11, 28, 0, 0, 0)) == "20261128T0000"


@pytest.mark.planted
def test_A3_slot_uses_ist_not_the_host_zone():
    from investos.acquire.rss import slot_of
    old = os.environ.get("TZ")
    try:
        os.environ["TZ"] = "America/New_York"
        time.tzset()
        assert slot_of(datetime(2026, 11, 26, 20, 0, tzinfo=UTC)) == "20261127T0130"
        assert slot_of(ist(2026, 11, 27, 9, 14, 59)) == "20261127T0900"
    finally:
        if old is None:
            os.environ.pop("TZ", None)
        else:
            os.environ["TZ"] = old
        time.tzset()


def test_A3_sidecar_says_not_date_keyed_and_nothing_is_rejected(renv):
    renv.serve("f1")
    r = renv.run_collector(renv.feed("f1"), SLOT)
    sc = renv.sidecar(r)
    assert sc["date_check"] == "not_date_keyed" and sc["outcomes"]["rejected"] == 0
    assert sc["source"] == "rss" and sc["source_version"] == "rss-v1"


@pytest.mark.planted
def test_A4_date_keyed_subclass_is_caught_by_the_guard():
    from investos.acquire.rss import RssFeedCollector
    bad = type("Bad", (RssFeedCollector,), {"date_keyed": True})
    with pytest.raises(AssertionError):
        assert_identity(bad)


@pytest.mark.planted
def test_A4_key_built_from_the_utc_date_would_fail_the_slot_test():
    """A3 at 01:30 IST has a different date in UTC: a UTC-based slot would read 20261126."""
    from investos.acquire.rss import slot_of
    t = datetime(2026, 11, 26, 20, 0, tzinfo=UTC)
    assert slot_of(t) != t.strftime("%Y%m%dT%H%M")
    assert slot_of(t).startswith("20261127")


# ================================================================== R9 / A11 schedule
@pytest.mark.parametrize("slot,want", [
    ("20261127T0645", False), ("20261127T0700", True), ("20261127T2345", True), ("20261127T0000", False),
    ("20261127T0100", True), ("20261127T0115", False), ("20261127T0300", True), ("20261127T0500", True),
    ("20261127T0515", False), ("20261127T0600", False),
])
def test_A11_is_poll_slot(slot, want):
    from investos.acquire.rss import is_poll_slot
    assert is_poll_slot(slot) is want


@pytest.mark.planted
def test_A11_exactly_71_scheduled_slots_a_day():
    from investos.acquire.rss import is_poll_slot
    slots = [f"20261127T{h:02d}{m:02d}" for h in range(24) for m in (0, 15, 30, 45)]
    got = [s for s in slots if is_poll_slot(s)]
    assert len(got) == 71
    assert [s for s in got if s < "20261127T0700"] == ["20261127T0100", "20261127T0300", "20261127T0500"]


# ================================================================== A7 / R5 body check
@pytest.mark.parametrize("n,outcome", [(3, "fetched"), (0, "fetched_empty")])
def test_A7_valid_body_is_stored_byte_exact(renv, n, outcome):
    body = body_of(*range(1, n + 1)) if n else make_rss([])
    renv.serve("f1", body)
    r = renv.run_collector(renv.feed("f1"), SLOT)
    key = f"f1/{SLOT}"
    assert r.outcomes[key].value == outcome
    rows = renv.rss_manifest()
    assert len(rows) == 1 and rows[0]["logical_key"] == key and rows[0]["sha256"] == sha(body)
    assert rows[0]["source_version"] == "rss-v1"
    assert renv.store.read_bytes("rss", sha(body)) == body


def test_A7_record_count_is_the_item_count(renv):
    c = renv.collector(renv.feed("f1"), SLOT)
    v = c.check_body(f"f1/{SLOT}", resp(200, body_of(1, 2, 3)))
    assert v.outcome.value == "fetched" and v.record_count == 3
    v0 = c.check_body(f"f1/{SLOT}", resp(200, make_rss([])))
    assert v0.outcome.value == "fetched_empty"


BAD_BODIES = {
    "html": b"<html><body>hello</body></html>",
    "truncated": make_rss([numbered(1)])[:-30],
    "atom": b'<?xml version="1.0"?><feed xmlns="http://www.w3.org/2005/Atom"><title>t</title></feed>',
    "rdf": b'<?xml version="1.0"?><rdf:RDF xmlns:rdf="http://www.w3.org/1999/02/22-rdf-syntax-ns#"/>',
    "empty": b"",
    "denied": b"Access Denied",
    "doctype": b'<?xml version="1.0"?><!DOCTYPE x [<!ENTITY a "b">]><rss version="2.0"><channel/></rss>',
}


@pytest.mark.planted
@pytest.mark.parametrize("name", sorted(BAD_BODIES))
def test_A7_unusable_200_bodies_are_unreadable_and_nothing_is_stored(renv, name):
    renv.serve("f1", BAD_BODIES[name])
    r = renv.run_collector(renv.feed("f1"), SLOT)
    assert r.outcomes[f"f1/{SLOT}"].value == "unreadable"
    assert renv.rss_manifest() == [] and renv.raw_count() == 0


def test_A7_atom_and_rdf_reason_names_the_format(renv):
    for name in ("atom", "rdf"):
        c = renv.collector(renv.feed("f1"), SLOT)
        v = c.check_body(f"f1/{SLOT}", resp(200, BAD_BODIES[name]))
        assert v.outcome.value == "unreadable" and "unsupported feed format" in v.reason


@pytest.mark.planted
def test_A7_nine_mib_body_is_unreadable_and_eight_mib_is_fine(renv):
    c = renv.collector(renv.feed("f1"), SLOT)
    big = exact_size_body(9 * 1024 * 1024)
    assert c.check_body(f"f1/{SLOT}", resp(200, big)).outcome.value == "unreadable"
    assert c.check_body(f"f1/{SLOT}", resp(200, exact_size_body(8 * 1024 * 1024))).outcome.value == "fetched"
    renv.serve("f1", big)
    r = renv.run_collector(renv.feed("f1"), SLOT)
    assert r.outcomes[f"f1/{SLOT}"].value == "unreadable" and renv.raw_count() == 0


@pytest.mark.planted
def test_A8_doctype_nul_and_billion_laughs_bodies_are_unreadable_via_the_collector(renv):
    ents = ['<!ENTITY a0 "lol">'] + [f'<!ENTITY a{i} "' + f"&a{i-1};" * 10 + '">' for i in range(1, 8)]
    laughs = ('<?xml version="1.0"?><!DOCTYPE l [' + "".join(ents) + ']><rss version="2.0"><channel><title>&a7;'
              '</title></channel></rss>').encode()
    for body in (BAD_BODIES["doctype"], laughs, b"<!doctype x>" + make_rss([numbered(1)]),
                 body_of(1).replace(b"Item 1", b"It\x00em")):
        e = RssEnv(renv.tmp / "x" / str(abs(hash(body)) % 10**8), [frow("f1", URL_ANN)])
        e.serve("f1", body)
        r = e.run_collector(e.feed("f1"), SLOT)
        assert r.outcomes[f"f1/{SLOT}"].value == "unreadable" and e.rss_manifest() == []


def test_R5_non_2xx_with_valid_body_is_not_stored(renv):
    renv.serve("f1", body_of(1), status=404)
    r = renv.run_collector(renv.feed("f1"), SLOT)
    assert r.outcomes[f"f1/{SLOT}"].value == "unreadable" and renv.rss_manifest() == []


# ================================================================== A6 / R4 redirects
@pytest.mark.planted
@pytest.mark.parametrize("status", [301, 302, 303, 307, 308])
def test_A6_redirect_is_unreadable_one_call_nothing_stored(renv, status):
    loc = "https://nsearchives.nseindia.in/content/RSS/Online_announcements.xml"
    renv.serve("f1", b"", status=status, headers={"Location": loc})
    r = renv.run_collector(renv.feed("f1"), SLOT)
    assert r.outcomes[f"f1/{SLOT}"].value == "unreadable" and r.http_calls == 1
    assert renv.transport.n() == 1 and renv.rss_manifest() == [] and renv.raw_count() == 0
    reason = renv.sidecar(r)["gaps"][0]["reason"]
    assert reason.startswith("redirect to https://nsearchives.nseindia.in")
    assert renv.sleeper.sleeps == []                                   # no retry on 3xx


def test_A6_location_is_cut_at_200_characters_and_header_case_is_free(renv):
    loc = "https://nsearchives.nseindia.in/" + "a" * 400
    renv.serve("f1", b"", status=302, headers={"location": loc})
    r = renv.run_collector(renv.feed("f1"), SLOT)
    assert renv.sidecar(r)["gaps"][0]["reason"] == "redirect to " + loc[:200]
