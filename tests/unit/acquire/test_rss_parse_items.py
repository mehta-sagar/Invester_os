"""S1-T6 step 1: `investos.parse.rss_items` (spec collect_rss R5, R6, R14-R16, R15a; A8 parser part, A20-A23, A22a)."""
from __future__ import annotations

import builtins
import hashlib
from datetime import datetime, timedelta, timezone

import pytest

from _collector_helpers import events, logs  # noqa: F401
from _rss_helpers import (IST, UTC, body_of, entry, exact_size_body, fields_of, ist, ist_dt, item, log_text,
                          make_rss, numbered, row, sha)


def parse(body):
    from investos.parse.rss_items import parse_feed
    return parse_feed(body)


def one(pub, tag="pub"):
    items = parse(make_rss([item(guid="g", title="t", **{tag: pub})]))
    assert len(items) == 1
    return items[0]


# ================================================================== R6 / A8: parser safety
def test_A8_parse_feed_is_importable_and_error_type_exists():
    from investos.parse.rss_items import FeedParseError, ParsedItem, parse_feed  # noqa: F401
    assert issubclass(FeedParseError, Exception)


@pytest.mark.planted
@pytest.mark.parametrize("decl", [
    '<!DOCTYPE foo [<!ENTITY x "y">]>',
    '<!doctype foo [<!entity x "y">]>',
    '<!DocType foo>',
    '<!ENTITY x "y">',
    '<!entity x "y">',
], ids=["upper", "lower", "mixed", "entity_only_upper", "entity_only_lower"])
def test_A8_doctype_or_entity_is_rejected(decl):
    from investos.parse.rss_items import FeedParseError
    body = ('<?xml version="1.0"?>' + decl + '<rss version="2.0"><channel><title>t</title></channel></rss>').encode()
    with pytest.raises(FeedParseError):
        parse(body)


@pytest.mark.planted
def test_A8_billion_laughs_is_rejected_and_never_expanded():
    from investos.parse.rss_items import FeedParseError
    ents = ['<!ENTITY a0 "lol">'] + [
        f'<!ENTITY a{i} "' + f"&a{i-1};" * 10 + '">' for i in range(1, 10)]
    body = ('<?xml version="1.0"?><!DOCTYPE lolz [' + "".join(ents) + ']><rss version="2.0"><channel>'
            '<title>&a9;</title><item><title>x</title></item></channel></rss>').encode()
    with pytest.raises(FeedParseError):
        parse(body)


@pytest.mark.planted
def test_A8_doctype_late_in_a_big_body_is_still_rejected():
    """R6: not only the first 4 KiB: 'any later part' of the body."""
    from investos.parse.rss_items import FeedParseError
    pad = "x" * 6000
    body = make_rss([item(guid="g", title="t", desc=pad)]).replace(
        b"</channel>", b"<![CDATA[<!DOCTYPE foo>]]></channel>")
    assert body.index(b"<!DOCTYPE") > 4096
    with pytest.raises(FeedParseError):
        parse(body)


@pytest.mark.planted
def test_A8_nul_byte_is_rejected():
    from investos.parse.rss_items import FeedParseError
    body = body_of(1).replace(b"Item 1", b"Item\x001")
    with pytest.raises(FeedParseError):
        parse(body)


@pytest.mark.planted
def test_R6_body_over_8_mib_is_rejected_and_exactly_8_mib_is_not():
    from investos.parse.rss_items import FeedParseError
    ok = exact_size_body(8 * 1024 * 1024)
    assert len(parse(ok)) == 1
    with pytest.raises(FeedParseError):
        parse(exact_size_body(8 * 1024 * 1024 + 1))


@pytest.mark.parametrize("body", [
    b"", b"   ", b"not xml at all", b"<html><body>Access Denied</body></html>", b"Access Denied",
    b"<rss version='2.0'><channel><title>t</title>",                       # truncated
    b'<rss version="2.0"></rss>',                                          # no channel
    b'<?xml version="1.0"?><feed xmlns="http://www.w3.org/2005/Atom"><title>t</title></feed>',
    b'<?xml version="1.0"?><rdf:RDF xmlns:rdf="http://www.w3.org/1999/02/22-rdf-syntax-ns#"/>',
    b'<?xml version="1.0"?><other/>',
], ids=["empty", "blank", "text", "html", "denied", "truncated", "no_channel", "atom", "rdf", "other_root"])
def test_R5_unusable_bodies_raise_feed_parse_error(body):
    from investos.parse.rss_items import FeedParseError
    with pytest.raises(FeedParseError):
        parse(body)


def test_R5_atom_and_rdf_message_names_the_unsupported_root():
    from investos.parse.rss_items import FeedParseError
    with pytest.raises(FeedParseError, match="unsupported feed format"):
        parse(b'<?xml version="1.0"?><feed xmlns="http://www.w3.org/2005/Atom"><title>t</title></feed>')


def test_R5_valid_feed_with_zero_items_is_an_empty_list():
    assert parse(make_rss([])) == []


def test_R6_parse_module_uses_only_stdlib_etree_and_never_lxml_or_defusedxml():
    import re
    from _rss_helpers import SRC
    p = SRC / "parse" / "rss_items.py"
    assert p.exists(), p
    src = p.read_text()
    assert re.search(r"^\s*(import|from)\s+xml\.etree", src, re.M), "must use xml.etree.ElementTree"
    for bad in ("lxml", "defusedxml"):
        assert not re.search(rf"^\s*(import|from)\s+{bad}", src, re.M), bad


# ================================================================== R15 / A22: pubDate is IST text
@pytest.mark.planted
def test_A22_no_zone_is_ist_wall_time_never_utc():
    it = one("Fri, 27 Nov 2026 14:05:00")
    assert it.pub_status == "ok"
    assert it.pub_ist == ist_dt(2026, 11, 27, 14, 5)
    assert it.pub_ist.astimezone(UTC) == datetime(2026, 11, 27, 8, 35, tzinfo=UTC)       # 08:35 UTC, not 14:05 UTC
    assert it.pub_ist.astimezone(UTC) != datetime(2026, 11, 27, 14, 5, tzinfo=UTC)


def test_A22_pub_ist_is_an_aware_datetime_in_asia_kolkata():
    it = one("Fri, 27 Nov 2026 14:05:00")
    assert it.pub_ist.utcoffset() == timedelta(hours=5, minutes=30)
    assert getattr(it.pub_ist.tzinfo, "key", None) == "Asia/Kolkata"
    assert it.pub_raw == "Fri, 27 Nov 2026 14:05:00"


@pytest.mark.parametrize("text,expect", [
    ("Fri, 27 Nov 2026 14:05:00 +0530", ist_dt(2026, 11, 27, 14, 5)),
    ("Fri, 27 Nov 2026 08:35:00 +0000", ist_dt(2026, 11, 27, 14, 5)),
    ("Fri, 27 Nov 2026 14:05:00 +0000", ist_dt(2026, 11, 27, 19, 35)),
    ("Fri, 27 Nov 2026 14:05:00 GMT", ist_dt(2026, 11, 27, 19, 35)),
    ("Fri, 27 Nov 2026 14:05:00 UT", ist_dt(2026, 11, 27, 19, 35)),
    ("Fri, 27 Nov 2026 14:05:00 Z", ist_dt(2026, 11, 27, 19, 35)),
    ("2026-11-27T14:05:00", ist_dt(2026, 11, 27, 14, 5)),
    ("2026-11-27T08:35:00Z", ist_dt(2026, 11, 27, 14, 5)),
    ("2026-11-27T14:05:00+05:30", ist_dt(2026, 11, 27, 14, 5)),
    ("2026-11-27T14:05:00+00:00", ist_dt(2026, 11, 27, 19, 35)),
    ("27 Nov 2026 14:05", ist_dt(2026, 11, 27, 14, 5)),
    ("27 Nov 2026 14:05:09", ist_dt(2026, 11, 27, 14, 5, 9)),
    ("27-Nov-2026 14:05:00", ist_dt(2026, 11, 27, 14, 5)),
    ("Fri, 27 Nov 2026 14:05", ist_dt(2026, 11, 27, 14, 5)),
], ids=lambda v: v if isinstance(v, str) else "")
def test_A22_accepted_spellings(text, expect):
    it = one(text)
    assert it.pub_status == "ok", text
    assert it.pub_ist == expect, text


@pytest.mark.planted
def test_A22_date_only_is_end_of_day_ist():
    it = one("27-Nov-2011")
    assert it.pub_status == "date_only"
    assert it.pub_ist == ist_dt(2011, 11, 27, 23, 59, 59)


@pytest.mark.planted
def test_A22_unparseable_keeps_text_and_the_item_is_still_emitted():
    it = one("soon")
    assert it.pub_status == "unparseable" and it.pub_ist is None and it.pub_raw == "soon"
    assert it.title == "t"


def test_A22_missing_element_is_missing():
    items = parse(make_rss([item(guid="g", title="t")]))
    assert len(items) == 1
    assert items[0].pub_status == "missing" and items[0].pub_ist is None and items[0].pub_raw is None


def test_A22_dc_date_is_read_like_pubdate_and_no_zone_means_ist():
    it = one("2026-11-27T14:05:00", tag="dc_date")
    assert it.pub_status == "ok" and it.pub_ist == ist_dt(2026, 11, 27, 14, 5)


@pytest.mark.planted
def test_A22_never_falls_back_to_the_system_clock(monkeypatch):
    """An unparseable date must not become 'now'."""
    it = one("not a date")
    assert it.pub_ist is None


# ================================================================== R15a / A22a: spellings seen on the live feeds
@pytest.mark.parametrize("text,status,expect", [
    ("11-SEP-2026 17:49:09", "ok", ist_dt(2026, 9, 11, 17, 49, 9)),
    ("11-Sep-2026 17:49:09", "ok", ist_dt(2026, 9, 11, 17, 49, 9)),
    ("11-sep-2026 17:49:09", "ok", ist_dt(2026, 9, 11, 17, 49, 9)),
    ("2026-11-27 18:08:28", "ok", ist_dt(2026, 11, 27, 18, 8, 28)),
    ("27 Nov, 2026 +0530", "date_only", ist_dt(2026, 11, 27, 23, 59, 59)),
    ("27-Nov-2026 22:39", "ok", ist_dt(2026, 11, 27, 22, 39)),
    ("27-Nov-2026", "date_only", ist_dt(2026, 11, 27, 23, 59, 59)),
    ("27-Nov-2026 22:58:14", "ok", ist_dt(2026, 11, 27, 22, 58, 14)),
    ("Fri, 27 Nov 2026 00:00:00 +0530", "ok", ist_dt(2026, 11, 27, 0, 0)),
    ("Fri, 27 Nov 2026 20:45:00", "ok", ist_dt(2026, 11, 27, 20, 45)),
    ("27 Nov 2026 22:00:04 +0530", "ok", ist_dt(2026, 11, 27, 22, 0, 4)),
    ("02-MAY-2025 11:13:16", "ok", ist_dt(2025, 5, 2, 11, 13, 16)),
], ids=lambda v: v if isinstance(v, str) else "")
def test_A22a_live_spellings(text, status, expect):
    it = one(text)
    assert (it.pub_status, it.pub_ist) == (status, expect), text


def test_A22a_upper_and_lower_case_months_give_the_same_instant():
    assert one("11-SEP-2026 17:49:09").pub_ist == one("11-sep-2026 17:49:09").pub_ist == one("11-Sep-2026 17:49:09").pub_ist


def test_A22a_items_with_no_pubdate_exist_on_live_feeds_and_are_missing():
    body = make_rss([numbered(1, pub=None), numbered(2, pub="27-Nov-2026 10:00:00")])
    got = {i.guid: i for i in parse(body)}
    assert got["g1"].pub_status == "missing" and got["g2"].pub_status == "ok"


# ================================================================== R16 / A23: identity and fields
def test_A23_guid_is_the_id_whatever_the_link():
    a = parse(make_rss([item(guid="g1", link="https://x.test/a", title="t")]))[0]
    b = parse(make_rss([item(guid="g1", link="https://x.test/b", title="t")]))[0]
    assert a.item_id == b.item_id == "g1"
    assert a.guid == "g1"


def test_A23_guid_is_stripped():
    assert parse(make_rss([item(guid="  g1\n", title="t")]))[0].item_id == "g1"


def test_A23_without_guid_the_link_is_the_id():
    it = parse(make_rss([item(link="https://x.test/a", title="t")]))[0]
    assert it.item_id == "https://x.test/a" and it.guid is None


def test_A23_empty_guid_falls_back_to_link():
    it = parse(make_rss([item(guid="   ", link="https://x.test/a", title="t")]))[0]
    assert it.item_id == "https://x.test/a"


@pytest.mark.planted
def test_A23_with_neither_the_id_is_h_plus_sha256_of_title_newline_pub():
    it = parse(make_rss([item(title="Hello", pub="27-Nov-2026 10:00:00")]))[0]
    assert it.item_id == "h:" + hashlib.sha256("Hello\n27-Nov-2026 10:00:00".encode()).hexdigest()
    it2 = parse(make_rss([item(title="Hello")]))[0]
    assert it2.item_id == "h:" + hashlib.sha256("Hello\n".encode()).hexdigest()


@pytest.mark.planted
@pytest.mark.parametrize("host_in,host_com", [
    ("nsearchives.nseindia.in", "nsearchives.nseindia.com"),
    ("www.nseindia.in", "www.nseindia.com"),
])
def test_A23_in_host_link_gets_the_same_id_as_the_com_host_and_link_keeps_the_original(host_in, host_com):
    a = parse(make_rss([item(link=f"https://{host_in}/content/x.pdf", title="t")]))[0]
    b = parse(make_rss([item(link=f"https://{host_com}/content/x.pdf", title="t")]))[0]
    assert a.item_id == b.item_id == f"https://{host_com}/content/x.pdf"
    assert a.link == f"https://{host_in}/content/x.pdf"                      # stored link keeps the original text
    assert b.link == f"https://{host_com}/content/x.pdf"


def test_A23_other_hosts_are_not_rewritten():
    it = parse(make_rss([item(link="https://www.sebi.gov.in/x.html", title="t")]))[0]
    assert it.item_id == "https://www.sebi.gov.in/x.html"


@pytest.mark.planted
def test_A23_two_items_with_one_id_in_a_body_keep_the_first():
    items = parse(make_rss([item(guid="dup", title="first"), item(guid="other", title="o"),
                            item(guid="dup", title="second")]))
    assert [i.item_id for i in items].count("dup") == 1
    assert {i.item_id: i.title for i in items}["dup"] == "first"


def test_A23_item_order_in_a_body_does_not_matter_for_identity():
    its = [numbered(i) for i in (1, 2, 3)]
    a = {i.item_id: i.title for i in parse(make_rss(its))}
    b = {i.item_id: i.title for i in parse(make_rss(list(reversed(its))))}
    assert a == b


def test_A23_summary_markup_stripped_whitespace_collapsed():
    it = parse(make_rss([item(guid="g", title="t", desc="<p>Hello <b>big</b>   world</p>\n\n again")]))[0]
    assert it.summary == "Hello big world again"


def test_A23_summary_with_cdata_markup_is_stripped():
    it = parse(make_rss([item(guid="g", title="t", desc="<div>Board <i>meeting</i></div>", cdata=True)]))[0]
    assert it.summary == "Board meeting"


@pytest.mark.planted
def test_A23_summary_capped_at_2000_and_title_at_1000():
    it = parse(make_rss([item(guid="g", title="T" * 3000, desc="s" * 5000)]))[0]
    assert len(it.summary) == 2000 and len(it.title) == 1000


def test_A23_control_characters_are_removed_and_text_is_stripped():
    it = parse(make_rss([item(guid="g", title="  A\x7fB  ", desc="C\x7fD")]))[0]
    assert it.title == "AB" and it.summary == "CD"


def test_A23_missing_title_and_description_are_empty_text():
    it = parse(make_rss([item(guid="g")]))[0]
    assert it.title == "" and it.summary == ""


# ================================================================== R14 / A20: first sighting
T1, T2, T3 = ist(2026, 11, 27, 9, 0), ist(2026, 11, 27, 9, 15), ist(2026, 11, 27, 9, 30)


def three_rows():
    a = body_of(1, 2)
    b = body_of(1, 2, 3)
    c = body_of(2, 3, 4)
    return [row("fx", "20261127T0900", a, T1), row("fx", "20261127T0915", b, T2), row("fx", "20261127T0930", c, T3)], (a, b, c)


def extract(rows):
    from investos.parse.rss_items import extract_items
    return list(extract_items(iter(rows)))


def test_A20_each_item_once_at_its_first_sighting():
    rows, (a, b, c) = three_rows()
    out = extract(rows)
    by = {r.item_id: r for r in out}
    assert sorted(by) == ["g1", "g2", "g3", "g4"] and len(out) == 4
    assert by["g1"].first_seen_at == T1 and by["g2"].first_seen_at == T1
    assert by["g3"].first_seen_at == T2 and by["g4"].first_seen_at == T3
    assert by["g1"].first_seen_sha256 == sha(a) and by["g3"].first_seen_sha256 == sha(b)
    assert by["g4"].first_seen_sha256 == sha(c)
    assert by["g1"].first_seen_key == "fx/20261127T0900" and by["g3"].first_seen_key == "fx/20261127T0915"
    assert by["g4"].first_seen_key == "fx/20261127T0930"
    assert all(r.feed_id == "fx" for r in out)


def test_A20_stream_follows_manifest_order():
    rows, _ = three_rows()
    seen = [r.first_seen_at for r in extract(rows)]
    assert seen == sorted(seen)


def test_A20_item_row_carries_the_parsed_fields():
    body = make_rss([item(guid="g1", link="https://x.test/1", title="Hello", pub="27-Nov-2026 10:00:00", desc="<b>s</b>")])
    r = extract([row("fx", "20261127T0900", body, T1)])[0]
    assert (r.guid, r.link, r.title, r.summary) == ("g1", "https://x.test/1", "Hello", "s")
    assert r.pub_raw == "27-Nov-2026 10:00:00" and r.pub_status == "ok" and r.pub_ist == ist_dt(2026, 11, 27, 10, 0)


def test_A20_same_item_in_two_feeds_is_yielded_once_per_feed():
    body = body_of(1)
    out = extract([row("fa", "20261127T0900", body, T1), row("fb", "20261127T0900", body, T2)])
    assert sorted((r.feed_id, r.item_id) for r in out) == [("fa", "g1"), ("fb", "g1")]


@pytest.mark.planted
def test_A20_unparseable_body_is_skipped_with_warning_and_later_rows_still_yield(logs):
    rows, (a, b, c) = three_rows()
    junk = b"<html>Access Denied</html>"
    rows.insert(1, row("fx", "20261127T0905", junk, ist(2026, 11, 27, 9, 5)))
    out = extract(rows)
    assert sorted(r.item_id for r in out) == ["g1", "g2", "g3", "g4"]
    w = [r for r in logs() if r["event"] == "item_parse_failed" and r["level"] == "WARNING"]
    assert len(w) == 1 and sha(junk) in log_text(w)


def test_A20_function_reads_no_clock_and_no_file(monkeypatch):
    import investos.parse.rss_items as m
    rows, _ = three_rows()

    class NoClock(datetime):
        @classmethod
        def now(cls, *a, **k):
            raise AssertionError("clock read")

        @classmethod
        def utcnow(cls):
            raise AssertionError("clock read")

        @classmethod
        def today(cls):
            raise AssertionError("clock read")

    if hasattr(m, "datetime"):
        monkeypatch.setattr(m, "datetime", NoClock)

    def no_open(*a, **k):
        raise AssertionError("file opened")
    monkeypatch.setattr(builtins, "open", no_open)
    monkeypatch.setattr("time.time", lambda: (_ for _ in ()).throw(AssertionError("clock read")))
    assert len(extract(rows)) == 4


def test_A20_accepts_a_plain_iterator_and_a_list():
    from investos.parse.rss_items import extract_items
    rows, _ = three_rows()
    assert len(list(extract_items(rows))) == 4
    assert len(list(extract_items(iter(rows)))) == 4


# ================================================================== A21: first_seen_at is when WE saw it
@pytest.mark.planted
def test_A21_first_seen_is_the_fetch_time_not_the_pubdate():
    fetched = ist(2026, 11, 27, 12, 0)
    body = make_rss([item(guid="g1", title="t", pub="26-Nov-2026 10:00:00")])           # yesterday
    r = extract([row("fx", "20261127T1200", body, fetched)])[0]
    assert r.first_seen_at == fetched
    assert r.pub_ist == ist_dt(2026, 11, 26, 10, 0)
    assert r.first_seen_at != r.pub_ist.astimezone(UTC)


@pytest.mark.planted
@pytest.mark.parametrize("delta,flag", [
    (timedelta(hours=3), True), (timedelta(minutes=4), False), (timedelta(minutes=5), False),
    (timedelta(minutes=5, seconds=1), True), (timedelta(hours=-2), False),
], ids=["3h", "4min", "exactly_5min", "5min_1s", "past"])
def test_A21_pub_after_seen_flags_publisher_clocks_ahead(delta, flag):
    fetched = ist(2026, 11, 27, 12, 0)
    pub = (fetched.astimezone(IST) + delta).strftime("%a, %d %b %Y %H:%M:%S +0530")
    body = make_rss([item(guid="g1", title="t", pub=pub)])
    r = extract([row("fx", "20261127T1200", body, fetched)])[0]
    assert r.pub_after_seen is flag


def test_A21_no_pub_date_never_flags_pub_after_seen():
    r = extract([row("fx", "20261127T1200", make_rss([item(guid="g1", title="t")]), T1)])[0]
    assert r.pub_ist is None and r.pub_after_seen is False


@pytest.mark.planted
def test_A21_first_seen_at_never_changes_when_a_later_row_repeats_the_item():
    out = extract([row("fx", "20261127T0900", body_of(1), T1), row("fx", "20261127T0915", body_of(1, 2), T2),
                   row("fx", "20261127T0930", body_of(1, 2), T3)])
    by = {r.item_id: r for r in out}
    assert len(out) == 2 and by["g1"].first_seen_at == T1 and by["g2"].first_seen_at == T2


def test_A21_pub_in_the_future_item_still_emitted():
    fetched = ist(2026, 11, 27, 12, 0)
    body = make_rss([item(guid="g1", title="t", pub="27-Nov-2026 15:00:00")])
    out = extract([row("fx", "20261127T1200", body, fetched)])
    assert len(out) == 1 and out[0].pub_after_seen is True
