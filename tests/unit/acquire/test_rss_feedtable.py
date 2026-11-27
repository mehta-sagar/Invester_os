"""S1-T6 step 3: feed table loader (R3, R3a; A5)."""
from __future__ import annotations

import pytest

from _rss_helpers import FEED_HEADER, SHIPPED_FEEDS, URL_ANN, URL_RBI, frow, write_feeds


def load(path):
    from investos.acquire.rss import load_feeds
    return load_feeds(path)


def err_for(tmp_path, rows=None, header=FEED_HEADER, raw=None):
    from investos.acquire.rss import FeedTableError
    p = tmp_path / "rss_feeds.csv"
    if raw is not None:
        p.write_bytes(raw)
    else:
        write_feeds(p, rows, header)
    with pytest.raises(FeedTableError) as ei:
        load(p)
    return str(ei.value)


def test_A5_good_table_loads_into_feedrows(tmp_path):
    from investos.acquire.rss import FeedRow
    p = write_feeds(tmp_path / "t.csv", [frow("a1"), frow("b2", url="https://www.rbi.org.in/x.xml", enabled="no",
                                                          kind="news", scope="always", stale="15", status="to_confirm")])
    feeds = load(p)
    assert all(isinstance(f, FeedRow) for f in feeds) and [f.feed_id for f in feeds] == ["a1", "b2"]
    assert feeds[0].enabled is True and feeds[1].enabled is False
    assert feeds[1].stale_after_min == 15 and isinstance(feeds[1].stale_after_min, int)
    assert feeds[0].kind == "corporate" and feeds[0].publisher == "NSE"


@pytest.mark.planted
def test_A5_wrong_or_extra_or_missing_columns(tmp_path):
    for h in ("feed_id,url", FEED_HEADER + ",extra", FEED_HEADER.replace("note", "notes"), FEED_HEADER.replace("url,", "")):
        m = err_for(tmp_path, [], header=h)
        assert "rss_feeds.csv" in m


CASES = [
    ("duplicate_feed_id", [frow("a1"), frow("a1", url="https://x.test/2.xml")], 3, "feed_id"),
    ("bad_feed_id_upper", [frow("Feed-1")], 2, "feed_id"),
    ("bad_feed_id_dash", [frow("feed-1")], 2, "feed_id"),
    ("feed_id_too_long", [frow("a" * 49)], 2, "feed_id"),
    ("http_url", [frow("a1", url="http://x.test/a.xml")], 2, "url"),
    ("userinfo", [frow("a1", url="https://user:pw@x.test/a.xml")], 2, "url"),
    ("fragment", [frow("a1", url="https://x.test/a.xml#frag")], 2, "url"),
    ("uppercase_host", [frow("a1", url="https://X.test/a.xml")], 2, "url"),
    ("space_in_url", [frow("a1", url="https://x.test/a b.xml")], 2, "url"),
    ("url_too_long", [frow("a1", url="https://x.test/" + "a" * 290)], 2, "url"),
    ("bad_kind", [frow("a1", kind="blog")], 2, "kind"),
    ("bad_enabled", [frow("a1", enabled="maybe")], 2, "enabled"),
    ("bad_scope", [frow("a1", scope="sometimes")], 2, "stale_scope"),
    ("bad_status", [frow("a1", status="maybe")], 2, "status"),
    ("stale_14", [frow("a1", stale="14")], 2, "stale_after_min"),
    ("stale_float", [frow("a1", stale="15.0")], 2, "stale_after_min"),
    ("stale_true", [frow("a1", stale="true")], 2, "stale_after_min"),
    ("stale_empty", [frow("a1", stale="")], 2, "stale_after_min"),
    ("empty_publisher", [frow("a1", publisher="")], 2, "publisher"),
    ("duplicate_url", [frow("a1", url="https://x.test/a.xml"), frow("b1", url="https://x.test/a.xml")], 3, "url"),
    ("disabled_rows_are_validated", [frow("a1", enabled="no", stale="14")], 2, "stale_after_min"),
]


@pytest.mark.planted
@pytest.mark.parametrize("name,rows,row_no,col", CASES, ids=[c[0] for c in CASES])
def test_A5_bad_table_names_file_row_and_column(tmp_path, name, rows, row_no, col):
    m = err_for(tmp_path, rows)
    assert "rss_feeds.csv" in m and col in m and str(row_no) in m, m


@pytest.mark.planted
def test_A5_nul_byte_and_non_utf8(tmp_path):
    base = (FEED_HEADER + "\n" + ",".join(frow("a1")[c] for c in FEED_HEADER.split(",")) + "\n").encode()
    err_for(tmp_path, raw=base.replace(b"NSE", b"N\x00E"))
    err_for(tmp_path, raw=base.replace(b"NSE", b"N\xff\xfeE"))


def test_A5_zero_enabled_rows_is_legal(tmp_path):
    feeds = load(write_feeds(tmp_path / "t.csv", [frow("a1", enabled="no")]))
    assert len(feeds) == 1 and not feeds[0].enabled


def test_A5_empty_table_with_only_header_loads(tmp_path):
    assert load(write_feeds(tmp_path / "t.csv", [])) == ()


def test_R3_loader_never_writes_the_file(tmp_path):
    p = write_feeds(tmp_path / "t.csv", [frow("a1")])
    before = (p.read_bytes(), p.stat().st_mtime_ns)
    load(p)
    assert (p.read_bytes(), p.stat().st_mtime_ns) == before


# ------------------------------------------------------------------ R3a: the shipped table
NSE_IDS = ["online_announcements", "annual_reports", "board_meetings", "brsr", "corporate_action",
           "corporate_governance", "daily_buyback", "financial_results", "integrated_filing_financials",
           "insider_trading", "investor_complaints", "offer_documents", "related_party_trans", "sast_regulation29",
           "sast_regulation31", "sast_reasonforencumbrance", "secretarial_compliance", "share_transfers",
           "shareholding_pattern", "statement_of_deviation", "unitholding_patterns", "voting_results", "circulars"]
NAMES = ["Online_announcements", "Annual_Reports", "Board_Meetings", "brsr", "Corporate_action",
         "Corporate_Governance", "Daily_Buyback", "Financial_Results", "Integrated_Filing_Financials",
         "InsiderTrading", "Investor_Complaints", "Offer_Documents", "Related_Party_Trans", "Sast_Regulation29",
         "Sast_Regulation31", "Sast_ReasonForEncumbrance", "Secretarial_Compliance", "Share_Transfers",
         "Shareholding_Pattern", "Statement_Of_Deviation", "Unitholding_Patterns", "Voting_Results", "Circulars"]


def test_A5_shipped_table_has_26_rows_all_enabled_and_confirmed():
    feeds = load(SHIPPED_FEEDS)
    assert len(feeds) == 26
    assert all(f.enabled and f.status == "confirmed" for f in feeds)
    assert [f.feed_id for f in feeds] == NSE_IDS + ["rbi_press", "sebi_press"] or \
        sorted(f.feed_id for f in feeds) == sorted(NSE_IDS + ["rbi_press", "sebi_press"])


def test_A5_shipped_urls_publishers_and_kinds():
    by = {f.feed_id: f for f in load(SHIPPED_FEEDS)}
    for fid, name in zip(NSE_IDS, NAMES):
        f = by[fid]
        assert f.url == f"https://nsearchives.nseindia.com/content/RSS/{name}.xml", fid
        assert f.publisher == "NSE" and f.kind == "corporate", fid
    assert by["rbi_press"].url == URL_RBI and by["rbi_press"].kind == "news"
    assert by["sebi_press"].url == "https://www.sebi.gov.in/sebirss.xml" and by["sebi_press"].kind == "news"
    assert by["online_announcements"].url == URL_ANN


@pytest.mark.planted
def test_A5_shipped_windows_and_scopes():
    by = {f.feed_id: f for f in load(SHIPPED_FEEDS)}
    special = {"online_announcements": (90, "trading_hours"), "insider_trading": (360, "trading_hours"),
               "circulars": (2880, "trading_hours"), "rbi_press": (5760, "always"), "sebi_press": (5760, "always")}
    for fid, f in by.items():
        assert (f.stale_after_min, f.stale_scope) == special.get(fid, (525600, "always")), fid
