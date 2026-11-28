"""S1-T8: the per-endpoint collector class (R1, R7, R8a, R11, R17; A15 collector side, A17, A22, A23, A33)."""
from __future__ import annotations

import ast
import json
import subprocess
from datetime import date, timedelta

import pytest

from _backfill_helpers import BF_SRC, BfEnv, DirectBrowser, IN, REPO, day, make_probe

WIN = ("2016-03-07", "2016-03-13")


@pytest.fixture
def e(tmp_path):
    return BfEnv(tmp_path, start_date="2016-01-01", max_window_days="7")


def build(e, eid="announcements", *, windows=None, browser=None, probe_over=None, **kw):
    from investos.backfill.collector import make_collector_class
    from investos.backfill.ledger import WindowKey
    ep = e.ep(eid)
    probe = make_probe(ep, earliest_date="2016-03-01", max_window_days=7, **(probe_over or {}))
    cls = make_collector_class(ep, probe)
    wins = windows if windows is not None else [WindowKey(eid, day(WIN[0]), day(WIN[1]))]
    br = browser or DirectBrowser(e.world, e.mono)
    args = dict(browser=br, manifest=e.store, cfg=e.cfg, run_id="run-1", clock=e.clock, monotonic=e.mono,
                sleeper=e.sleeper, wall_clock=e.wall, hostname=e.hostname, windows=wins)
    args.update(kw)
    return cls, cls(**args), br


def run(c, wins):
    from investos.acquire.base import RunRange
    return c.run(RunRange(wins[0].start, wins[-1].end))


def win(eid="announcements", a=WIN[0], b=WIN[1], part=""):
    from investos.backfill.ledger import WindowKey
    return WindowKey(eid, day(a), day(b), part)


def rows(n, d="2016-03-08", field="an_dt"):
    dd = day(d)
    txt = f"{dd.day:02d}-Mar-{dd.year} 10:15:00"
    return [{"symbol": f"S{i}", field: txt} for i in range(n)]


def body(obj) -> bytes:
    return json.dumps(obj).encode()


# ------------------------------------------------------------------ A33 / R17 class contract
def test_A33_class_attributes_for_bulk_deals(tmp_path):
    from investos.acquire.base import AccessMode, Collector
    e = BfEnv(tmp_path)
    cls, c, _ = build(e, "bulk_deals", probe_over={"row_cap": 70})
    assert issubclass(cls, Collector) and cls.source == "backfill_bulk_deals"
    assert cls.access_mode is AccessMode.REAL_BROWSER and cls.page_limit == 70
    assert cls.source_version == "backfill-v1" and cls.archive is False and cls.date_keyed is False


def test_A33_page_limit_is_none_when_no_cap_was_observed(e):
    cls, _, _ = build(e)
    assert cls.page_limit is None


def test_A33_a_class_built_with_transport_raises(e):
    from _collector_helpers import FakeTransport
    with pytest.raises(ValueError, match="browser"):
        build(e, browser=None, transport=FakeTransport(e.mono), **{})
    cls, _, _ = build(e)
    with pytest.raises(ValueError):
        cls(transport=FakeTransport(e.mono), manifest=e.store, cfg=e.cfg, run_id="r", clock=e.clock,
            monotonic=e.mono, sleeper=e.sleeper, windows=[win()])


def test_A33_base_access_and_manifest_are_not_touched():
    try:
        subprocess.run(["git", "cat-file", "-e", "9cd4fb7"], cwd=REPO, check=True, capture_output=True)
    except (OSError, subprocess.CalledProcessError):
        pytest.skip("the base commit is not available")
    r = subprocess.run(["git", "diff", "--stat", "9cd4fb7", "--", "src/investos/acquire/base.py",
                        "src/investos/acquire/access.py", "src/investos/acquire/manifest.py",
                        "src/investos/acquire/transport.py", "src/investos/acquire/rss.py"],
                       cwd=REPO, capture_output=True, text=True)
    assert r.returncode == 0 and r.stdout.strip() == ""


# ------------------------------------------------------------------ R17 keys and urls
def test_R17_keys_are_the_logical_keys_of_the_batch_and_urls_are_on_the_api_origin(e):
    wins = [win(a="2016-03-07", b="2016-03-10"), win(a="2016-03-11", b="2016-03-13")]
    _, c, br = build(e, windows=wins)
    from investos.acquire.base import RunRange
    assert list(c.keys(RunRange(wins[0].start, wins[-1].end))) == ["announcements/2016-03-07_2016-03-10",
                                                                    "announcements/2016-03-11_2016-03-13"]
    run(c, wins)
    assert br.urls == [f"{IN}/api/announcements?from_date=07-03-2016&to_date=10-03-2016",
                       f"{IN}/api/announcements?from_date=11-03-2016&to_date=13-03-2016"]


def test_A23_to_exclusive_requests_end_plus_one_in_dd_mm_yyyy(e):
    _, c, br = build(e, probe_over={"to_inclusive": False})
    run(c, [win()])
    assert br.urls == [f"{IN}/api/announcements?from_date=07-03-2016&to_date=14-03-2016"]


def test_A23_date_format_follows_the_probe(e):
    _, c, br = build(e, probe_over={"date_format": "YYYY-MM-DD"})
    run(c, [win()])
    assert br.urls[0].endswith("from_date=2016-03-07&to_date=2016-03-13")


# ------------------------------------------------------------------ A23 storage, byte exact
NASTY_BODIES = {
    "bom": b"\xef\xbb\xbf" + body(rows(2)),
    "crlf": body(rows(2)).replace(b"[{", b"[\r\n{").replace(b"}]", b"}\r\n]"),
    "utf8": body(rows(1)).replace(b"S0", "Café ₹".encode("utf-8")),
    "pretty": json.dumps(rows(3), indent=4).encode() + b"\n",
}


@pytest.mark.parametrize("name", sorted(NASTY_BODIES))
def test_A23_stored_bytes_equal_the_response_bytes_exactly(e, name):
    _, c, br = build(e)
    u = f"{IN}/api/announcements?from_date=07-03-2016&to_date=13-03-2016"
    br.bodies[u] = NASTY_BODIES[name]
    res = run(c, [win()])
    key = "announcements/2016-03-07_2016-03-13"
    assert res.outcomes[key].value == "fetched"
    ent = e.store.get_by_key("backfill_announcements", key)
    assert e.store.read_bytes("backfill_announcements", ent.sha256) == NASTY_BODIES[name]


def test_A23_manifest_fields_of_a_stored_window(e):
    _, c, br = build(e)
    run(c, [win()])
    ent = e.store.get_by_key("backfill_announcements", "announcements/2016-03-07_2016-03-13")
    assert ent.source == "backfill_announcements" and ent.access_mode == "real_browser"
    assert ent.source_version == "backfill-v1" and ent.logical_key == "announcements/2016-03-07_2016-03-13"
    assert dict(ent.request_headers)["user-agent"].startswith("Mozilla/5.0")        # as recorded by the browser
    assert ent.url == br.urls[0] and ent.final_url == br.urls[0]


def test_A23_a_part_key_has_the_suffix(e):
    _, c, _ = build(e, windows=[win(a="2016-03-07", b="2016-03-10", part="a")])
    run(c, [win(a="2016-03-07", b="2016-03-10", part="a")])
    assert e.mkeys("announcements") == ["announcements/2016-03-07_2016-03-10/a"]


def test_R1_the_collector_module_imports_no_parser_and_keeps_no_derived_copy():
    banned = {"csv", "xml", "html", "lxml", "bs4", "pandas", "duckdb", "pyarrow", "yaml", "openpyxl", "random"}
    for f in ("collector.py", "runner.py", "planner.py", "ledger.py", "status.py"):
        tree = ast.parse((BF_SRC / f).read_text())
        mods = {n.split(".")[0] for node in ast.walk(tree)
                for n in ([a.name for a in node.names] if isinstance(node, ast.Import)
                          else [node.module or ""] if isinstance(node, ast.ImportFrom) else [])}
        assert not (mods & banned), (f, mods & banned)
    assert "json" in (BF_SRC / "collector.py").read_text()


def test_R1_check_body_does_not_modify_or_filter_rows(e):
    """Rows in the body are never deduplicated or normalised: duplicates in, the same bytes out."""
    dup = body(rows(2) + rows(2))
    _, c, br = build(e)
    br.bodies[f"{IN}/api/announcements?from_date=07-03-2016&to_date=13-03-2016"] = dup
    run(c, [win()])
    ent = e.store.get_by_key("backfill_announcements", "announcements/2016-03-07_2016-03-13")
    assert e.store.read_bytes("backfill_announcements", ent.sha256) == dup


# ------------------------------------------------------------------ A17 / R8a per-window checks
def serve(e, b, **kw):
    _, c, br = build(e, **kw)
    br.bodies[f"{IN}/api/announcements?from_date=07-03-2016&to_date=13-03-2016"] = b
    return c, br


def test_A17_zero_rows_is_fetched_empty_and_stored(e):
    from investos.acquire.base import Outcome
    c, _ = serve(e, b"[]")
    res = run(c, [win()])
    assert res.outcomes["announcements/2016-03-07_2016-03-13"] is Outcome.FETCHED_EMPTY
    assert e.mkeys("announcements") == ["announcements/2016-03-07_2016-03-13"]


def test_A17_wrapped_empty_list_is_empty_but_an_empty_object_is_not(e):
    from investos.acquire.base import Outcome
    c, _ = serve(e, b'{"data":[]}', probe_over={"rows_path": "data"})
    assert run(c, [win()]).outcomes["announcements/2016-03-07_2016-03-13"] is Outcome.FETCHED_EMPTY


@pytest.mark.parametrize("b", [b'{"status":"error"}', b"{}", b"<html><body>x</body></html>", b"", b"[1,",
                               b"\xff\xfe\x00", b'"just a string"', b"null", b"7"])
def test_A17_error_objects_html_empty_or_invalid_json_are_unreadable_and_never_stored(e, b):
    from investos.acquire.base import Outcome
    c, _ = serve(e, b)
    res = run(c, [win()])
    assert res.outcomes["announcements/2016-03-07_2016-03-13"] is Outcome.UNREADABLE
    assert e.mkeys("announcements") == []


def test_A17_object_without_the_rows_key_is_unreadable(e):
    from investos.acquire.base import Outcome
    c, _ = serve(e, b'{"other":[1]}', probe_over={"rows_path": "data"})
    assert run(c, [win()]).outcomes["announcements/2016-03-07_2016-03-13"] is Outcome.UNREADABLE


def test_A17_a_row_dated_outside_the_window_is_never_stored_and_the_reason_says_so(e):
    from investos.acquire.base import Outcome
    c, _ = serve(e, body(rows(2) + rows(1, "2016-03-14")))
    res = run(c, [win()])
    key = "announcements/2016-03-07_2016-03-13"
    assert res.outcomes[key] in (Outcome.UNREADABLE, Outcome.REJECTED)
    assert "date outside window" in e.gaps(res)[key]["reason"]
    assert e.mkeys("announcements") == []


def test_A17_rows_on_the_first_and_last_day_are_inside(e):
    from investos.acquire.base import Outcome
    c, _ = serve(e, body(rows(1, "2016-03-07") + rows(1, "2016-03-13")))
    assert run(c, [win()]).outcomes["announcements/2016-03-07_2016-03-13"] is Outcome.FETCHED


def test_A17_a_row_without_the_date_field_is_unreadable_when_the_probe_found_a_date_field(e):
    from investos.acquire.base import Outcome
    c, _ = serve(e, body([{"symbol": "X"}]))
    assert run(c, [win()]).outcomes["announcements/2016-03-07_2016-03-13"] is Outcome.UNREADABLE


def test_A17_no_date_field_means_no_date_check(e):
    from investos.acquire.base import Outcome
    c, _ = serve(e, body([{"symbol": "X"}]), probe_over={"date_field": ""})
    assert run(c, [win()]).outcomes["announcements/2016-03-07_2016-03-13"] is Outcome.FETCHED


def test_A17_iso_dates_are_read_too(e):
    from investos.acquire.base import Outcome
    c, _ = serve(e, body([{"an_dt": "2016-03-09T10:00:00"}]))
    assert run(c, [win()]).outcomes["announcements/2016-03-07_2016-03-13"] is Outcome.FETCHED


def test_A17_non_2xx_is_never_stored(e):
    from investos.acquire.base import BrowserResponse, Outcome
    c, br = serve(e, b"[]")
    br.override[f"{IN}/api/announcements?from_date=07-03-2016&to_date=13-03-2016"] = [
        BrowserResponse(404, {}, b"[]", f"{IN}/api/announcements?from_date=07-03-2016&to_date=13-03-2016", None)]
    assert run(c, [win()]).outcomes["announcements/2016-03-07_2016-03-13"] is Outcome.UNREADABLE
    assert e.mkeys("announcements") == []


# ------------------------------------------------------------------ A15 / R7 the capped body is never stored
@pytest.mark.parametrize("n,expected,stored", [(70, "incomplete", 0), (71, "incomplete", 0), (69, "fetched", 1),
                                              (500, "incomplete", 0)])
def test_A15_a_window_at_or_over_the_cap_is_incomplete_and_nothing_is_stored(tmp_path, n, expected, stored):
    e = BfEnv(tmp_path, start_date="2016-01-01")
    cls, c, br = build(e, "bulk_deals", probe_over={"row_cap": 70})
    key = "bulk_deals/2016-03-07_2016-03-13"
    br.bodies[f"{IN}/api/bulk_deals?from_date=07-03-2016&to_date=13-03-2016"] = body(rows(n))
    res = run(c, [win("bulk_deals")])
    assert res.outcomes[key].value == expected
    assert len(e.mkeys("bulk_deals")) == stored
    manifest_file = e.man / "backfill_bulk_deals.jsonl"
    assert (len(manifest_file.read_text().splitlines()) if manifest_file.exists() else 0) == stored


def test_A15_without_a_cap_a_big_window_is_stored(e):
    c, _ = serve(e, body(rows(500)))
    assert run(c, [win()]).outcomes["announcements/2016-03-07_2016-03-13"].value == "fetched"


# ------------------------------------------------------------------ A22 idempotence
def test_A22_the_same_window_fetched_twice_has_one_manifest_row_and_no_second_request(e):
    c, br = serve(e, body(rows(2)))
    run(c, [win()])
    _, c2, br2 = build(e)
    res2 = run(c2, [win()])
    assert res2.outcomes["announcements/2016-03-07_2016-03-13"].value == "skipped" and br2.urls == []
    assert len(e.store.entries("backfill_announcements")) == 1


def test_A22_a_changed_source_is_never_overwritten(e):
    c, br = serve(e, body(rows(2)))
    run(c, [win()])
    first = e.store.get_by_key("backfill_announcements", "announcements/2016-03-07_2016-03-13").sha256
    c2, br2 = serve(e, body(rows(3)))
    run(c2, [win()])
    assert br2.urls == []
    assert e.store.get_by_key("backfill_announcements", "announcements/2016-03-07_2016-03-13").sha256 == first
    assert len(e.store.entries("backfill_announcements")) == 1
