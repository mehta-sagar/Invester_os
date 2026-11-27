"""S1-T6 step 6b: `investos check rss` (R20, R21; A27, A28, A29)."""
from __future__ import annotations

import copy
import json
import types
from datetime import timedelta

import pytest

from _collector_helpers import demo_class
from _rss_helpers import (EXAMPLE_TOML, IST, NSE, URL_RBI, RssEnv, UTC, canon, env_tree, fmt_ts, frow, has_count, ist,
                          run_check, set_check_clock, warn_lines, write_feeds)

URL_A = NSE + "a.xml"
NOW = ist(2026, 11, 28, 12, 0, 30)                       # Saturday: trading_hours feeds are not evaluated
ROWS = [frow("f_nse", URL_A, scope="trading_hours", stale="90", status="to_confirm"),
        frow("f_news", URL_RBI, kind="news", publisher="RBI", scope="always", stale="5760", status="confirmed")]


@pytest.fixture
def e(tmp_path, monkeypatch):
    env = RssEnv(tmp_path, ROWS, trading_days=["2026-11-30"])
    set_check_clock(monkeypatch, NOW)
    for fid in ("f_nse", "f_news"):
        env.fresh_state(fid, NOW)
    return env


def verdict(e, argv=("rss",)):
    code, out, rep = run_check(e, argv)
    return code, out, rep


def gap(feed_id="f_news", **over):
    d = {"schema": 1, "feed_id": feed_id, "kind": "missed", "from_slot": "20261127T0700", "to_slot": "20261127T0800",
         "slots": 5, "reason": "mac asleep", "detected_at": "2026-11-27T03:00:00.000000Z", "run_id": "r1"}
    d.update(over)
    return d


def put_gaps(e, *lines, raw_tail=b""):
    e.gaps_path.parent.mkdir(parents=True, exist_ok=True)
    e.gaps_path.write_bytes(b"".join(canon(l).encode() + b"\n" for l in lines) + raw_tail)


# ------------------------------------------------------------------ A27 healthy
def test_A27_check_is_registered_and_passes_on_a_healthy_fixture(e):
    code, out, rep = verdict(e)
    assert code == 0, out + rep
    assert "rss" in out and "pass" in out


def test_A27_to_confirm_feed_is_a_warning_and_confirmed_one_is_not(e):
    code, out, rep = verdict(e)
    w = warn_lines(rep)
    assert any("f_nse" in l and "to_confirm" in l for l in w)
    assert not any("f_news" in l and "to_confirm" in l for l in w)


def test_A27_message_carries_the_counts(e):
    code, out, rep = verdict(e)
    msg = [l for l in out.splitlines() if l.startswith("rss")][0]
    n_warn = len(warn_lines(rep))
    assert n_warn >= 1
    for word, n in (("feeds", 2), ("enabled", 2), ("alarms", 0), ("warnings", n_warn)):
        assert has_count(msg, word, n), (word, n, msg)


def test_A27_check_is_read_only(e):
    verdict(e)
    t1 = env_tree(e)
    verdict(e)
    assert env_tree(e) == t1


# ------------------------------------------------------------------ A27 failures
@pytest.mark.planted
def test_A27_stale_feed_fails_and_names_feed_and_kind(e):
    e.fresh_state("f_news", NOW, newest=NOW.astimezone(IST) - timedelta(days=6),
                  last_change_at=fmt_ts(NOW - timedelta(days=6)))
    code, out, rep = verdict(e)
    assert code == 1 and "f_news" in rep and "content_stale" in rep
    assert "f_nse" not in "".join(l for l in rep.splitlines() if "content_stale" in l)


@pytest.mark.planted
def test_A27_missed_poll_fails_and_names_feed_and_kind(e):
    e.fresh_state("f_news", NOW, last_ok_slot="20261126T1145")
    code, out, rep = verdict(e)
    assert code == 1 and "f_news" in rep and "poll_missed" in rep


@pytest.mark.planted
def test_A19_poll_missed_boundary_is_ten_minutes_after_the_slot(e, monkeypatch):
    e.fresh_state("f_news", NOW, last_ok_slot="20261128T1145")
    set_check_clock(monkeypatch, ist(2026, 11, 28, 12, 9, 59))
    assert verdict(e)[0] == 0
    set_check_clock(monkeypatch, ist(2026, 11, 28, 12, 10, 0))
    code, out, rep = verdict(e)
    assert code == 1 and "poll_missed" in rep


@pytest.mark.planted
def test_A19_three_consecutive_failures_is_poll_missed(e):
    e.fresh_state("f_news", NOW, consecutive_failures=3)
    code, out, rep = verdict(e)
    assert code == 1 and "f_news" in rep and "poll_missed" in rep
    e.fresh_state("f_news", NOW, consecutive_failures=2)
    assert verdict(e)[0] == 0


def test_A18_trading_day_content_stale_is_found_by_the_check(tmp_path, monkeypatch):
    env = RssEnv(tmp_path, ROWS, trading_days=["2026-11-30"])
    now = ist(2026, 11, 30, 11, 0, 30)
    set_check_clock(monkeypatch, now)
    env.fresh_state("f_news", now)
    env.fresh_state("f_nse", now, newest=ist(2026, 11, 27, 15, 0).astimezone(IST))
    code, out, rep = verdict(env)
    assert code == 1 and "f_nse" in rep and "content_stale" in rep
    set_check_clock(monkeypatch, ist(2026, 11, 30, 10, 30, 30))
    env.fresh_state("f_nse", ist(2026, 11, 30, 10, 30, 30), newest=ist(2026, 11, 27, 15, 0).astimezone(IST))
    env.fresh_state("f_news", ist(2026, 11, 30, 10, 30, 30))
    assert verdict(env)[0] == 0


def test_A19_unloaded_calendar_year_is_judged_always_with_a_warning(tmp_path, monkeypatch):
    env = RssEnv(tmp_path, ROWS, calendar="empty")
    set_check_clock(monkeypatch, NOW)
    env.fresh_state("f_news", NOW)
    env.fresh_state("f_nse", NOW, newest=ist(2026, 11, 27, 15, 0).astimezone(IST))      # 21 h old, window 90 min
    code, out, rep = verdict(env)
    assert code == 1 and "f_nse" in rep and "content_stale" in rep
    assert any("calendar" in l.lower() for l in warn_lines(rep))


def test_A18_feed_without_state_is_unknown_a_warning_not_an_alarm(e):
    e.state_path("f_news").unlink()
    code, out, rep = verdict(e)
    assert code == 0
    assert any("f_news" in l and "unknown" in l.lower() for l in warn_lines(rep))


@pytest.mark.planted
def test_R11_unreadable_state_is_absent_so_unknown_never_fresh_or_alarm(e):
    e.write_state("f_news", raw="{broken")
    code, out, rep = verdict(e)
    assert code == 0 and any("f_news" in l and "unknown" in l.lower() for l in warn_lines(rep))


def test_R20_disabled_feed_is_not_judged(tmp_path, monkeypatch):
    rows = [dict(ROWS[1]), frow("f_off", URL_A, enabled="no", scope="always", stale="15")]
    env = RssEnv(tmp_path, rows)
    set_check_clock(monkeypatch, NOW)
    env.fresh_state("f_news", NOW)
    env.fresh_state("f_off", NOW, last_ok_slot="20260101T0900")
    assert verdict(env)[0] == 0


@pytest.mark.planted
def test_A27_unknown_feed_key_in_the_manifest_fails(e):
    e.store.store("rss", b"ghost-body", url=URL_A, source_version="rss-v1", logical_key="ghost/20261127T0900",
                  run_id="r", fetched_at=NOW, access_mode="honest", request_headers={}, final_url=URL_A, fetch_host="h")
    code, out, rep = verdict(e)
    assert code == 1 and "ghost" in rep


@pytest.mark.planted
@pytest.mark.parametrize("key", ["f_news/2026-11-27", "f_news", "f_news/20261127T09", "f_news/20261127T0900/x"])
def test_A27_malformed_key_fails(e, key):
    e.store.store("rss", b"body-" + key.encode(), url=URL_RBI, source_version="rss-v1", logical_key=key,
                  run_id="r", fetched_at=NOW, access_mode="honest", request_headers={}, final_url=URL_RBI, fetch_host="h")
    assert verdict(e)[0] == 1


def test_A27_known_key_with_a_valid_slot_passes(e):
    e.store.store("rss", b"fine-body", url=URL_RBI, source_version="rss-v1", logical_key="f_news/20261127T0900",
                  run_id="r", fetched_at=NOW, access_mode="honest", request_headers={}, final_url=URL_RBI, fetch_host="h")
    assert verdict(e)[0] == 0


@pytest.mark.planted
def test_R20_invalid_feed_table_fails(e):
    write_feeds(e.feeds_path, [frow("f_news", URL_RBI, stale="14")])
    code, out, rep = verdict(e)
    assert code == 1 and "stale_after_min" in rep + out


@pytest.mark.planted
@pytest.mark.parametrize("variant", ["torn", "bad_json", "unknown_field", "schema2", "bad_kind"])
def test_A27_bad_gaps_file_fails(e, variant):
    if variant == "torn":
        put_gaps(e, gap(), raw_tail=b'{"schema":1,"feed_id":"f_ne')
    elif variant == "bad_json":
        put_gaps(e, gap(), raw_tail=b"{not json}\n")
    elif variant == "unknown_field":
        put_gaps(e, gap(extra=1))
    elif variant == "schema2":
        put_gaps(e, gap(schema=2))
    else:
        put_gaps(e, gap(kind="vanished"))
    code, out, rep = verdict(e)
    assert code == 1


def test_A27_valid_gap_events_pass_with_a_warning_for_the_feed(e):
    put_gaps(e, gap("f_news"), gap("f_news", kind="failed", from_slot="20261127T0900", to_slot="20261127T0900", slots=1))
    code, out, rep = verdict(e)
    assert code == 0
    assert any("f_news" in l and "gap" in l.lower() for l in warn_lines(rep))
    assert not any("f_nse" in l and "gap" in l.lower() for l in warn_lines(rep))


# ------------------------------------------------------------------ A28 plant
@pytest.mark.planted
def test_A28_planted_run_is_caught_and_real_data_untouched(e):
    before = env_tree(e)
    code, out, rep = verdict(e, ("rss", "--planted"))
    assert code == 0, out
    line = [l for l in out.splitlines() if l.startswith("rss")][0]
    assert "caught" in line
    assert env_tree(e) == before


def test_A28_planted_run_works_on_empty_real_data(tmp_path, monkeypatch):
    env = RssEnv(tmp_path, ROWS)
    set_check_clock(monkeypatch, NOW)
    code, out, rep = verdict(env, ("rss", "--planted"))
    assert code == 0 and "caught" in out


# ------------------------------------------------------------------ A29 registry (R21)
@pytest.fixture
def isolated_registry():
    import investos.acquire.registry as reg
    snap = {}
    for k, v in vars(reg).items():
        if isinstance(v, (dict, list, set)):
            snap[k] = copy.copy(v)
    yield reg
    for k, v in snap.items():
        cur = getattr(reg, k)
        cur.clear()
        cur.update(v) if isinstance(cur, (dict, set)) and not isinstance(cur, list) else cur.extend(v)


def dummy(source="dummy_waived", waiver="x" * 25, **kw):
    return demo_class(source=source, date_keyed=True, date_check_waiver=waiver, **kw)


def test_A29_rss_collector_is_registered_and_not_waived():
    from investos.acquire.registry import adapters, waived_adapters
    from investos.acquire.rss import RssFeedCollector
    assert RssFeedCollector in adapters() and isinstance(adapters(), tuple)
    assert waived_adapters() == []


@pytest.mark.planted
def test_A29_waived_adapter_is_listed_with_its_reason(isolated_registry):
    reg = isolated_registry
    cls = dummy(waiver="no date inside these files at all")
    assert reg.register_adapter(cls) is cls
    assert ("dummy_waived", "no date inside these files at all") in reg.waived_adapters()
    assert cls in reg.adapters()


@pytest.mark.planted
def test_A29_duplicate_source_from_another_class_is_refused_and_same_class_twice_is_fine(isolated_registry):
    reg = isolated_registry
    a, b = dummy(), dummy()
    reg.register_adapter(a)
    reg.register_adapter(a)
    assert list(reg.adapters()).count(a) == 1
    with pytest.raises(ValueError):
        reg.register_adapter(b)
    from investos.acquire.rss import RssFeedCollector
    with pytest.raises(ValueError):
        reg.register_adapter(demo_class(source="rss"))


def test_A29_non_date_keyed_or_unwaived_adapters_are_not_listed(isolated_registry):
    reg = isolated_registry
    reg.register_adapter(demo_class(source="plain_one"))
    assert all(s != "plain_one" for s, _ in reg.waived_adapters())


@pytest.mark.planted
def test_A29_check_prints_a_warning_per_waived_adapter_and_still_passes(e, isolated_registry):
    reason = "no date inside these files at all"
    isolated_registry.register_adapter(dummy(waiver=reason))
    code, out, rep = verdict(e)
    assert code == 0
    assert any(f"WARNING waived date check: dummy_waived: {reason}" in l for l in rep.splitlines())


def test_A29_without_a_waived_adapter_no_such_line(e):
    assert not any("waived date check" in l for l in verdict(e)[2].splitlines())
