"""S1-T5 collect_base amendment 2: in-file date check. Acceptance A59-A71, rules R34-R41 (+ planted failures)."""
from __future__ import annotations

import ast
import json
import re
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

import pytest

from _collector_helpers import (D17, D18, SOURCE, demo_class, env, events, logs, make_zip, resp, rr, sha,  # noqa: F401
                                url_of)
from _datecheck_helpers import (FRI, HEADER, MON, SAT, SUN, WAIVER, blank_date_body, d_text, date_class,
                                date_verdict, dated_body, evidence, manifest_keys, read_date1, scripted, waived_class)

REPO = Path(__file__).resolve().parents[3]
OUTCOME_KEYS = {"fetched", "fetched_empty", "missing_holiday", "unreadable", "skipped", "incomplete", "rejected",
                "not_attempted"}
REASON_RE = re.compile(r"^(date_mismatch|date_missing): .+")


def _gap(env, r, key):
    return env.gaps(r)[key]


def _no_store(env):
    assert env.manifest_lines() == [] and env.raw_files() == []


# ============================================================ A59 (R35) declaration
@pytest.mark.planted
@pytest.mark.parametrize("attrs", [
    dict(date_keyed=True),                                                                   # neither
    dict(date_keyed=True, internal_dates=read_date1, date_check_waiver=WAIVER),              # both
    dict(date_keyed=True, date_check_waiver="too short"),                                    # 9 chars
    dict(date_keyed=True, date_check_waiver="0123456789"),                                   # 10 chars
    dict(date_keyed=True, date_check_waiver="x" * 19),                                       # 19 chars
    dict(date_keyed=True, date_check_waiver=" " * 10 + "abcdefghij" + " " * 10),           # 10 after strip
    dict(date_keyed=True, date_check_waiver=""),                                             # empty
    dict(date_keyed=False, date_check_waiver=WAIVER),                                        # waiver, not keyed
    dict(date_keyed=False, internal_dates=read_date1),                                       # dead code
    dict(internal_dates=read_date1),                                                         # default False + override
], ids=["neither", "both", "w9", "w10", "w19", "w10-padded", "w-empty", "waiver-unkeyed", "override-unkeyed",
        "override-default-unkeyed"])
def test_A59_declaration_errors(env, attrs):
    attrs = dict(attrs, calls=[])
    with pytest.raises(ValueError) as e:
        env.make(demo_class(**attrs))
    assert SOURCE in str(e.value)


@pytest.mark.parametrize("attrs,mode", [
    (dict(date_keyed=True, internal_dates=read_date1), "enforced"),
    (dict(date_keyed=True, date_check_waiver="x" * 20), "waived"),
    (dict(date_keyed=True, date_check_waiver=WAIVER), "waived"),
    (dict(date_keyed=False), "not_date_keyed"),
], ids=["override", "waiver20", "waiver-long", "not-keyed"])
def test_A59_valid_declarations_instantiate_and_report_mode(env, attrs, mode):
    attrs = dict(attrs, calls=[], verdict_fn=date_verdict)
    c = env.make(demo_class(**attrs))
    scripted(env, FRI, dated_body(FRI))
    r = c.run(rr(FRI))
    assert env.sidecar(r)["date_check"] == mode


def test_A59_defaults_and_logical_date_default(env):
    from investos.acquire.base import Collector
    assert Collector.date_keyed is False and Collector.date_check_waiver is None
    c = env.make(date_class())
    assert c.logical_date(FRI) == date(2026, 11, 20) and type(c.logical_date(FRI)) is date


def test_R35_waiver_counts_characters_after_strip(env):
    exactly20 = "a" * 20
    env.make(waived_class(date_check_waiver="  " + exactly20 + "  "))            # 20 after strip: ok


def test_R35_no_config_switch_disables_the_check(env, tmp_path):
    from _core_helpers import VALID_TOML, make_repo, set_toml
    from investos.core.config import ConfigError, load_config
    repo = make_repo(tmp_path / "r")
    for line in ("date_check = false", "date_check = \"off\"", "enforce_date_check = false"):
        set_toml(repo, VALID_TOML + f'\n[collect]\ncontact = "a@b.c"\n{line}\n')
        with pytest.raises(ConfigError):
            load_config(repo / "config" / "investos.toml", env={}, repo_root=repo)
    with pytest.raises(ConfigError):
        set_toml(repo, VALID_TOML)
        load_config(repo / "config" / "investos.toml", env={"INVESTOS_COLLECT__DATE_CHECK": "off"}, repo_root=repo)


# ============================================================ A60 (R37) pass
def test_A60_matching_date_is_fetched_and_logged(env, logs):
    from investos.acquire.base import Outcome
    body = dated_body(FRI, rows=3)
    scripted(env, FRI, body)
    c = env.make(date_class())
    r = c.run(rr(FRI))
    assert r.outcomes == {FRI: Outcome.FETCHED} and r.status == "complete"
    assert len(env.manifest_lines()) == 1 and [p.read_bytes() for p in env.raw_files()] == [body]
    sc = env.sidecar(r)
    assert sc["date_check"] == "enforced" and sc["outcomes"]["fetched"] == 1 and sc["outcomes"]["rejected"] == 0
    ev = events(logs(), "fetched", "INFO")
    assert len(ev) == 1 and ev[0]["fields"]["trade_date"] == FRI and ev[0]["task"] == f"collect.{SOURCE}"
    assert c.calls == [(FRI, None)]                         # plain file: member is None


def test_R40_fetched_empty_with_verified_date_logs_trade_date(env, logs):
    from investos.acquire.base import Outcome
    cls = date_class(internal_dates=lambda self, k, m, b: evidence(FRI, rows=0))
    scripted(env, FRI, HEADER)
    r = env.make(cls).run(rr(FRI))
    assert r.outcomes == {FRI: Outcome.FETCHED_EMPTY} and len(env.manifest_lines()) == 1
    ev = events(logs(), "fetched_empty", "INFO")
    assert len(ev) == 1 and ev[0]["fields"]["trade_date"] == FRI


def test_R36_only_the_file_decides_not_url_headers_or_key_text(env):
    from investos.acquire.base import Outcome
    scripted(env, FRI, dated_body(FRI), url="https://example.test/f/2026-11-19/x.csv")   # URL names another day
    env.transport.script["https://example.test/f/2026-11-19/x.csv"] = [resp(
        200, dated_body(FRI), headers={"Last-Modified": "Sun, 22 Nov 2026 00:00:00 GMT", "Date": "Mon, 23 Nov 2026"})]
    cls = date_class(url_fn=lambda k: "https://example.test/f/2026-11-19/x.csv")
    r = env.make(cls).run(rr(FRI))
    assert r.outcomes == {FRI: Outcome.FETCHED}


def test_R36_adapter_receives_key_member_none_and_exact_body(env):
    seen = []
    body = dated_body(FRI)
    cls = date_class(internal_dates=lambda self, k, m, b: (seen.append((k, m, b)), read_date1(self, k, m, b))[1])
    scripted(env, FRI, body)
    env.make(cls).run(rr(FRI))
    assert seen == [(FRI, None, body)]


def test_R37_saturday_special_session_passes_no_weekday_logic(env):
    from investos.acquire.base import Outcome
    scripted(env, SAT, dated_body(SAT))
    assert env.make(date_class()).run(rr(SAT)).outcomes == {SAT: Outcome.FETCHED}


# ============================================================ A61 (R37, planted: Sunday returns Friday)
@pytest.mark.planted
def test_A61_sunday_url_returns_fridays_identical_file(env, logs):
    from investos.acquire.base import Outcome
    friday_bytes = dated_body(FRI)
    scripted(env, FRI, friday_bytes)
    scripted(env, SUN, friday_bytes)                          # status 200, byte-identical
    cls = date_class(fixed_keys=[FRI, SUN])
    r = env.make(cls).run(rr(FRI, SUN))
    assert r.outcomes == {FRI: Outcome.FETCHED, SUN: Outcome.REJECTED}
    assert Outcome.REJECTED.value == "rejected" and r.status == "incomplete"
    g = _gap(env, r, SUN)
    assert g["outcome"] == "rejected" and g["reason"].startswith("date_mismatch")
    assert FRI in g["reason"] and SUN in g["reason"]
    assert g["reason"] == f"date_mismatch: key {SUN} but file has {FRI} in DATE1"
    assert len(env.manifest_lines()) == 1 and len(env.raw_files()) == 1          # only Friday
    assert env.store.get_by_key(SOURCE, SUN) is None and env.store.get_by_key(SOURCE, FRI) is not None
    sc = env.sidecar(r)
    assert sc["outcomes"]["rejected"] == 1 and sc["outcomes"]["fetched"] == 1 and sc["date_check"] == "enforced"
    assert sc["received"] == 1 and sc["expected"] == 2 and sc["status"] == "incomplete"
    assert [x["key"] for x in sc["gaps"]] == [SUN] and [o["key"] for o in sc["outputs"]] == [FRI]
    w = events(logs(), "rejected", "WARNING")
    assert len(w) == 1 and w[0]["task"] == f"collect.{SOURCE}"
    f = w[0]["fields"]
    assert f["key"] == SUN and f["url"] == url_of(SUN) and f["reason"] == g["reason"]
    assert f["file_dates"] == [FRI] and f["field"] == "DATE1" and f["rows_examined"] == 2
    # re-run: Friday skipped with zero calls, Sunday asked once more and rejected again
    before_f, before_s = env.transport.n(url_of(FRI)), env.transport.n(url_of(SUN))
    r2 = env.make(date_class(fixed_keys=[FRI, SUN]), run_id="run-2").run(rr(FRI, SUN))
    assert r2.outcomes == {FRI: Outcome.SKIPPED, SUN: Outcome.REJECTED}
    assert env.transport.n(url_of(FRI)) == before_f and env.transport.n(url_of(SUN)) == before_s + 1
    assert len(env.manifest_lines()) == 1 and len(env.raw_files()) == 1


@pytest.mark.planted
def test_edge_same_bytes_two_urls_the_date_not_a_hash_decides(env):
    from investos.acquire.base import Outcome
    b = dated_body(FRI)
    scripted(env, SAT, b)
    scripted(env, FRI, b)
    cls = date_class(fixed_keys=[FRI, SAT])
    r = env.make(cls).run(rr(FRI, SAT))
    assert r.outcomes == {FRI: Outcome.FETCHED, SAT: Outcome.REJECTED}
    assert len(env.manifest_lines()) == 1


# ============================================================ A62 (R37) missing / several / wrong dates
@pytest.mark.planted
def test_A62_header_only_is_date_missing(env):
    from investos.acquire.base import Outcome
    scripted(env, FRI, HEADER)
    r = env.make(date_class()).run(rr(FRI))
    assert r.outcomes == {FRI: Outcome.REJECTED}
    assert _gap(env, r, FRI)["reason"] == "date_missing: no trade date found in DATE1 (0 rows)"
    _no_store(env)


@pytest.mark.planted
def test_A62_blank_date_cells_are_date_missing(env):
    from investos.acquire.base import Outcome
    scripted(env, FRI, blank_date_body(2))
    r = env.make(date_class()).run(rr(FRI))
    assert r.outcomes == {FRI: Outcome.REJECTED}
    assert _gap(env, r, FRI)["reason"] == "date_missing: no trade date found in DATE1 (2 rows)"
    _no_store(env)


@pytest.mark.planted
@pytest.mark.parametrize("dates,shown", [
    ((FRI, "2026-11-19"), r"2026-11-19,\s?2026-11-20"),        # key date plus the day before
    (("2026-11-21",), r"2026-11-21"),                           # the day after
    (("2026-11-19",), r"2026-11-19"),                           # the day before
    (("2026-11-20", "2026-11-23", "2026-11-19"), r"2026-11-19,\s?2026-11-20,\s?2026-11-23"),   # sorted
], ids=["two-with-key", "day-after", "day-before", "three-sorted"])
def test_A62_wrong_or_several_dates_are_date_mismatch(env, logs, dates, shown):
    from investos.acquire.base import Outcome
    cls = date_class(internal_dates=lambda self, k, m, b: evidence(*dates, field="TradDt", rows=7))
    scripted(env, FRI, dated_body(FRI))
    r = env.make(cls).run(rr(FRI))
    assert r.outcomes == {FRI: Outcome.REJECTED}
    reason = _gap(env, r, FRI)["reason"]
    assert reason.startswith(f"date_mismatch: key {FRI} but file has ") and reason.endswith(" in TradDt")
    assert re.search(shown, reason)
    _no_store(env)
    f = events(logs(), "rejected", "WARNING")[0]["fields"]
    assert f["file_dates"] == sorted(dates) and f["field"] == "TradDt" and f["rows_examined"] == 7


def test_R37_no_tolerance_for_adjacent_day_or_any_weekday_rule(env):
    from investos.acquire.base import Outcome
    for key, other in ((MON, SUN), (FRI, SAT)):
        e_cls = date_class(internal_dates=lambda self, k, m, b, o=other: evidence(o))
        scripted(env, key, dated_body(key))
        assert env.make(e_cls).run(rr(key)).outcomes == {key: Outcome.REJECTED}


# ============================================================ A63 (R37) adapter misbehaviour
def _raises(self, k, m, b):
    raise ValueError("bad cell")


def _returns_none(self, k, m, b):
    return None


def _datetime_inside(self, k, m, b):
    from investos.acquire.base import DateEvidence
    return DateEvidence(frozenset({datetime(2026, 11, 20, 0, 0)}), "DATE1", 1)


def _returns_str(self, k, m, b):
    return "2026-11-20"


def _negative_rows(self, k, m, b):
    from investos.acquire.base import DateEvidence
    return DateEvidence(frozenset({date(2026, 11, 20)}), "DATE1", -1)


def _returns_set(self, k, m, b):
    return {date(2026, 11, 20)}


@pytest.mark.planted
@pytest.mark.parametrize("fn,outcome,prefix", [
    (_raises, "unreadable", "date check error"),
    (_returns_none, "rejected", "date_missing"),
    (_datetime_inside, "unreadable", "date check error"),
    (_returns_str, "unreadable", "date check error"),
    (_negative_rows, "unreadable", "date check error"),
    (_returns_set, "unreadable", "date check error"),
], ids=["raises", "none-plain", "datetime", "str", "negative-rows", "bare-set"])
def test_A63_adapter_errors_never_abort_and_store_nothing(env, fn, outcome, prefix):
    from investos.acquire.base import Outcome
    calls = []
    cls = date_class(fixed_keys=[FRI, MON],
                     internal_dates=lambda self, k, m, b: fn(self, k, m, b) if k == FRI else read_date1(self, k, m, b))
    scripted(env, FRI, dated_body(FRI))
    scripted(env, MON, dated_body(MON))
    r = env.make(cls).run(rr(FRI, MON))
    assert r.outcomes[FRI] is Outcome(outcome) and r.outcomes[MON] is Outcome.FETCHED     # next key still requested
    assert r.status == "incomplete" and env.transport.n(url_of(MON)) == 1
    assert _gap(env, r, FRI)["reason"].startswith(prefix)
    assert env.manifest_lines() and manifest_keys(env) == [MON]                           # only the second key stored


# ============================================================ A64 (R38) order of work
@pytest.mark.planted
def test_A64_html_page_with_status_200_is_unreadable_and_date_never_read(env):
    from investos.acquire.base import Outcome
    cls = date_class()
    scripted(env, FRI, b"<html>Error: data not available</html>")
    r = env.make(cls).run(rr(FRI))
    assert r.outcomes == {FRI: Outcome.UNREADABLE} and cls.calls == []
    _no_store(env)


@pytest.mark.planted
def test_A64_404_no_file_is_missing_holiday_and_date_never_read(env):
    from investos.acquire.base import Outcome
    cls = date_class()
    scripted(env, SUN, b"NO FILE", status=404)
    r = env.make(cls).run(rr(SUN))
    assert r.outcomes == {SUN: Outcome.MISSING_HOLIDAY} and cls.calls == []
    assert env.sidecar(r)["outcomes"]["rejected"] == 0


@pytest.mark.planted
def test_A64_skipped_key_makes_zero_http_and_zero_date_checks(env):
    from investos.acquire.base import Outcome
    scripted(env, FRI, dated_body(FRI))
    env.make(date_class()).run(rr(FRI))
    cls = date_class()
    n = env.transport.n()
    r = env.make(cls, run_id="run-2").run(rr(FRI))
    assert r.outcomes == {FRI: Outcome.SKIPPED} and cls.calls == [] and env.transport.n() == n


@pytest.mark.planted
def test_A64_page_limit_incomplete_never_reads_the_date(env):
    from investos.acquire.base import BodyVerdict, Outcome
    cls = date_class(page_limit=100, verdict_fn=lambda s, k, r: BodyVerdict(Outcome.FETCHED, record_count=100))
    scripted(env, FRI, dated_body(FRI))
    r = env.make(cls).run(rr(FRI))
    assert r.outcomes == {FRI: Outcome.INCOMPLETE} and cls.calls == []


@pytest.mark.planted
@pytest.mark.parametrize("status,body", [(403, dated_body(FRI)), (429, dated_body(FRI)), (503, dated_body(FRI)),
                                         (404, dated_body(FRI)), (200, b"")], ids=["403", "429", "503", "404-valid-body", "empty"])
def test_A64_blocked_non2xx_and_failed_never_read_the_date(env, status, body):
    cls = date_class()
    scripted(env, FRI, body, status=status)
    env.make(cls).run(rr(FRI))
    assert cls.calls == []
    _no_store(env)


@pytest.mark.planted
def test_A64_encoded_body_never_read_the_date(env):
    cls = date_class()
    env.transport.set(url_of(FRI), resp(200, dated_body(FRI), headers={"Content-Encoding": "gzip"}))
    env.make(cls).run(rr(FRI))
    assert cls.calls == [] and env.manifest_lines() == []


def test_R38_date_check_runs_after_check_body_which_runs_first(env):
    order = []
    cls = date_class(verdict_fn=lambda s, k, r: (order.append("check_body"), date_verdict(s, k, r))[1],
                     internal_dates=lambda self, k, m, b: (order.append("internal_dates"), read_date1(self, k, m, b))[1])
    scripted(env, FRI, dated_body(FRI))
    env.make(cls).run(rr(FRI))
    assert order == ["check_body", "internal_dates"]


# ============================================================ A65 (R39) archives
def _zip_body(members):
    return make_zip(members)


@pytest.mark.planted
def test_A65_archive_ok_readme_ignored_and_members_checked_in_name_order(env):
    from investos.acquire.base import Outcome
    z = _zip_body({"a.csv": dated_body(FRI), "README.txt": b"hello"})
    scripted(env, FRI, z)
    cls = date_class(archive=True)
    r = env.make(cls).run(rr(FRI))
    assert r.outcomes == {FRI: Outcome.FETCHED}
    assert [m for _k, m in cls.calls] == sorted(["a.csv", "README.txt"]) and all(k == FRI for k, _m in cls.calls)
    keys = manifest_keys(env)
    assert FRI in keys and f"{FRI}/a.csv" in keys
    assert env.store.read_bytes(SOURCE, sha(z)) == z


@pytest.mark.planted
def test_A65_one_bad_member_rejects_and_stores_nothing(env, logs):
    from investos.acquire.base import Outcome
    scripted(env, FRI, _zip_body({"a.csv": dated_body(FRI), "b.csv": dated_body("2026-11-19")}))
    r = env.make(date_class(archive=True)).run(rr(FRI))
    assert r.outcomes == {FRI: Outcome.REJECTED}
    reason = _gap(env, r, FRI)["reason"]
    assert reason.startswith("date_mismatch") and "b.csv" in reason and "a.csv" not in reason
    _no_store(env)
    assert events(logs(), "rejected", "WARNING")


@pytest.mark.planted
def test_A65_first_failing_member_decides_the_reason(env):
    scripted(env, FRI, _zip_body({"a.csv": dated_body("2026-11-18"), "b.csv": dated_body("2026-11-19")}))
    r = env.make(date_class(archive=True)).run(rr(FRI))
    reason = _gap(env, r, FRI)["reason"]
    assert "a.csv" in reason and "b.csv" not in reason and "2026-11-18" in reason


@pytest.mark.planted
def test_A65_all_members_without_date_is_date_missing(env):
    from investos.acquire.base import Outcome
    scripted(env, FRI, _zip_body({"README.txt": b"x", "index.txt": b"y"}))
    r = env.make(date_class(archive=True)).run(rr(FRI))
    assert r.outcomes == {FRI: Outcome.REJECTED} and _gap(env, r, FRI)["reason"].startswith("date_missing")
    _no_store(env)


@pytest.mark.planted
def test_A65_member_with_blank_dates_is_date_missing_for_that_member(env):
    scripted(env, FRI, _zip_body({"a.csv": dated_body(FRI), "b.csv": blank_date_body()}))
    r = env.make(date_class(archive=True)).run(rr(FRI))
    reason = _gap(env, r, FRI)["reason"]
    assert reason.startswith("date_missing") and "b.csv" in reason
    _no_store(env)


@pytest.mark.planted
def test_A65_unsafe_member_is_unreadable_and_date_never_read(env):
    from investos.acquire.base import Outcome
    scripted(env, FRI, _zip_body([("../evil", b"x"), ("a.csv", dated_body(FRI))]))
    cls = date_class(archive=True)
    r = env.make(cls).run(rr(FRI))
    assert r.outcomes == {FRI: Outcome.UNREADABLE} and "unsafe archive" in _gap(env, r, FRI)["reason"]
    assert cls.calls == []
    _no_store(env)


@pytest.mark.planted
def test_A65_member_adapter_error_is_unreadable_nothing_stored(env):
    from investos.acquire.base import Outcome

    def boom(self, k, m, b):
        if m == "b.csv":
            raise ValueError("bad cell")
        return read_date1(self, k, m, b)
    scripted(env, FRI, _zip_body({"a.csv": dated_body(FRI), "b.csv": dated_body(FRI)}))
    r = env.make(date_class(archive=True, internal_dates=boom)).run(rr(FRI))
    assert r.outcomes == {FRI: Outcome.UNREADABLE} and _gap(env, r, FRI)["reason"].startswith("date check error")
    _no_store(env)


# ============================================================ A66 (R40) sidecar and logs, mixed run
def test_A66_mixed_run_sidecar_gaps_and_logs(env, logs):
    from investos.acquire.base import Outcome
    k = ["2026-11-16", "2026-11-17", "2026-11-18", "2026-11-19", "2026-11-20"]

    def idates(self, key, member, body):
        if key == k[1]:
            return evidence(k[1], rows=0)                           # genuine empty file that still states its date
        return read_date1(self, key, member, body)
    scripted(env, k[0], dated_body(k[0]))
    scripted(env, k[1], HEADER)
    scripted(env, k[2], b"NO FILE", status=404)
    scripted(env, k[3], b"garbage")
    scripted(env, k[4], dated_body(k[0]))                             # another day's file
    r = env.make(date_class(fixed_keys=k, internal_dates=idates)).run(rr(k[0], k[4]))
    assert r.outcomes == {k[0]: Outcome.FETCHED, k[1]: Outcome.FETCHED_EMPTY, k[2]: Outcome.MISSING_HOLIDAY,
                          k[3]: Outcome.UNREADABLE, k[4]: Outcome.REJECTED}
    sc = env.sidecar(r)
    c = sc["outcomes"]
    assert set(c) == OUTCOME_KEYS
    assert (c["fetched"], c["fetched_empty"], c["missing_holiday"], c["unreadable"], c["rejected"]) == (1, 1, 1, 1, 1)
    assert c["skipped"] == c["incomplete"] == c["not_attempted"] == 0
    assert sc["received"] == 3 and sc["expected"] == 5 and sum(c.values()) == 5 and sc["status"] == "incomplete"
    assert [(g["key"], g["outcome"]) for g in sc["gaps"]] == [(k[3], "unreadable"), (k[4], "rejected")]
    assert all(g["reason"] for g in sc["gaps"]) and REASON_RE.match(sc["gaps"][1]["reason"])
    recs = logs()
    w = events(recs, "rejected", "WARNING")
    assert len(w) == 1 and {"key", "url", "reason", "file_dates", "field", "rows_examined"} <= set(w[0]["fields"])
    end = events(recs, "run_end")
    assert len(end) == 1 and end[0]["fields"]["rejected"] == 1 and end[0]["fields"]["fetched"] == 1


def test_R40_sidecar_without_rejected_still_lists_zero_and_date_check(env):
    scripted(env, FRI, dated_body(FRI))
    sc = env.sidecar(env.make(date_class()).run(rr(FRI)))
    assert sc["outcomes"]["rejected"] == 0 and set(sc["outcomes"]) == OUTCOME_KEYS and sc["date_check"] == "enforced"


def test_R40_rejected_makes_run_incomplete_and_never_complete(env):
    scripted(env, FRI, dated_body(FRI))
    scripted(env, SUN, dated_body(FRI))
    r = env.make(date_class(fixed_keys=[FRI, SUN])).run(rr(FRI, SUN))
    sc = env.sidecar(r)
    assert r.status == sc["status"] == "incomplete" and sc["received"] == 1


def test_R40_date_missing_warning_has_empty_file_dates(env, logs):
    scripted(env, FRI, blank_date_body(3))
    env.make(date_class()).run(rr(FRI))
    f = events(logs(), "rejected", "WARNING")[0]["fields"]
    assert f["file_dates"] == [] and f["rows_examined"] == 3 and f["field"] == "DATE1" and f["key"] == FRI
    assert f["reason"].startswith("date_missing")


def test_R40_none_for_plain_file_logs_a_rejected_warning(env, logs):
    scripted(env, FRI, dated_body(FRI))
    env.make(date_class(internal_dates=_returns_none)).run(rr(FRI))
    f = events(logs(), "rejected", "WARNING")[0]["fields"]
    assert f["file_dates"] == [] and f["reason"].startswith("date_missing")


# ============================================================ A67 (R35, R40) waived and not date keyed
def test_A67_waived_adapter_stores_what_check_body_accepts(env, logs, monkeypatch):
    from investos.acquire import base
    from investos.acquire.base import Outcome

    def boom(self, *a, **k):
        raise AssertionError("internal_dates must not be called for a waived adapter")
    monkeypatch.setattr(base.Collector, "internal_dates", boom, raising=False)
    scripted(env, FRI, dated_body("2026-11-10"))                       # a date that would fail an enforced check
    scripted(env, MON, dated_body(MON))
    r = env.make(waived_class(fixed_keys=[FRI, MON])).run(rr(FRI, MON))
    assert r.outcomes == {FRI: Outcome.FETCHED, MON: Outcome.FETCHED} and r.status == "complete"
    sc = env.sidecar(r)
    assert sc["date_check"] == "waived" and sc["outcomes"]["rejected"] == 0
    recs = logs()
    w = events(recs, "date_check_waived", "WARNING")
    assert len(w) == 1 and w[0]["task"] == f"collect.{SOURCE}" and WAIVER in json.dumps(w[0]["fields"])
    names = [e["event"] for e in recs]
    assert names.index("date_check_waived") < names.index("fetched")        # logged at run start
    assert "trade_date" not in events(recs, "fetched")[0]["fields"]          # nothing was verified


def test_A67_waiver_warning_is_logged_once_per_run(env, logs):
    scripted(env, FRI, dated_body(FRI))
    c = waived_class(fixed_keys=[FRI, MON])
    scripted(env, MON, dated_body(MON))
    env.make(c).run(rr(FRI, MON))
    env.make(waived_class(fixed_keys=[FRI, MON]), run_id="run-2").run(rr(FRI, MON))
    assert len(events(logs(), "date_check_waived")) == 2          # one per run, not one per key


def test_A67_not_date_keyed_sidecar_and_no_waiver_warning(env, logs):
    scripted(env, D17, b"date,close\n2026-11-17,100\n")
    r = env.make(demo_class()).run(rr(D17))
    assert env.sidecar(r)["date_check"] == "not_date_keyed"
    assert events(logs(), "date_check_waived") == [] and "trade_date" not in events(logs(), "fetched")[0]["fields"]


def test_A67_enforced_adapter_logs_no_waiver_warning(env, logs):
    scripted(env, FRI, dated_body(FRI))
    env.make(date_class()).run(rr(FRI))
    assert events(logs(), "date_check_waived") == []


# ============================================================ A68 (R41) keys
@pytest.mark.planted
@pytest.mark.parametrize("bad", ["2026-11-2", "20261120", "2026-11-20T00:00", "2026-W47-5", "2026-11-20 ", " 2026-11-20",
                                 "2026-13-01", "2026-02-30", "26-11-20", "2026/11/20", "abc"])
def test_A68_bad_key_fails_run_before_any_http_call(env, bad):
    cls = date_class(fixed_keys=[FRI, bad])
    scripted(env, FRI, dated_body(FRI))
    with pytest.raises(ValueError) as e:
        env.make(cls).run(rr(FRI))
    assert repr(bad) in str(e.value) or bad in str(e.value)
    assert env.transport.n() == 0 and cls.calls == []
    assert not env.sidecar_dir.exists() or list(env.sidecar_dir.iterdir()) == []
    _no_store(env)


def test_A68_override_logical_date_for_compact_keys(env):
    from investos.acquire.base import Outcome
    key = "20261120"
    cls = date_class(fixed_keys=[key], url_fn=lambda k: url_of(k),
                     logical_date=lambda self, k: date(int(k[:4]), int(k[4:6]), int(k[6:])))
    scripted(env, key, dated_body(FRI))
    r = env.make(cls).run(rr(FRI))
    assert r.outcomes == {key: Outcome.FETCHED}
    assert key in manifest_keys(env) and FRI not in manifest_keys(env)          # logical_key stays as given
    assert env.store.get_by_key(SOURCE, key) is not None


@pytest.mark.planted
def test_A68_override_logical_date_still_compares_with_the_file(env):
    from investos.acquire.base import Outcome
    key = "20261122"
    cls = date_class(fixed_keys=[key], logical_date=lambda self, k: date(int(k[:4]), int(k[4:6]), int(k[6:])))
    scripted(env, key, dated_body(FRI))
    assert env.make(cls).run(rr(FRI)).outcomes == {key: Outcome.REJECTED}


@pytest.mark.planted
def test_A68_two_strings_naming_one_date_are_refused(env):
    def ld(self, k):
        return date.fromisoformat(k) if "-" in k else date(int(k[:4]), int(k[4:6]), int(k[6:]))
    cls = date_class(fixed_keys=["20261120", FRI], logical_date=ld)
    with pytest.raises(ValueError):
        env.make(cls).run(rr(FRI))
    assert env.transport.n() == 0
    assert not env.sidecar_dir.exists() or list(env.sidecar_dir.iterdir()) == []


@pytest.mark.planted
def test_A68_logical_date_that_raises_fails_the_run_before_http(env):
    def ld(self, k):
        raise KeyError(k)
    cls = date_class(fixed_keys=[FRI], logical_date=ld)
    with pytest.raises(ValueError):
        env.make(cls).run(rr(FRI))
    assert env.transport.n() == 0


@pytest.mark.planted
def test_A68_logical_date_returning_datetime_or_str_is_refused(env):
    for bad in (lambda self, k: datetime(2026, 11, 20), lambda self, k: "2026-11-20"):
        with pytest.raises(ValueError):
            env.make(date_class(fixed_keys=[FRI], logical_date=bad)).run(rr(FRI))
    assert env.transport.n() == 0


def test_A68_non_date_keyed_collector_accepts_any_key_text(env):
    from investos.acquire.base import Outcome
    scripted(env, "monthly-2026-11", b"date,close\n2026-11-17,100\n")
    r = env.make(demo_class(fixed_keys=["monthly-2026-11"])).run(rr(D17))
    assert r.outcomes == {"monthly-2026-11": Outcome.FETCHED}


# ============================================================ A69 (R34) the outcome itself
@pytest.mark.planted
def test_A69_check_body_cannot_return_rejected(env):
    from investos.acquire.base import BodyVerdict, Outcome
    for cls in (date_class(verdict_fn=lambda s, k, r: BodyVerdict(Outcome.REJECTED, "date_mismatch: nope")),
                demo_class(verdict_fn=lambda s, k, r: BodyVerdict(Outcome.REJECTED, "date_mismatch: nope"))):
        scripted(env, FRI, dated_body(FRI))
        r = env.make(cls).run(rr(FRI))
        assert r.outcomes == {FRI: Outcome.UNREADABLE} and "invalid verdict" in _gap(env, r, FRI)["reason"]
        assert env.sidecar(r)["outcomes"]["rejected"] == 0
        _no_store(env)


@pytest.mark.planted
def test_A69_mismatch_leaves_manifest_and_raw_tree_untouched_and_run_goes_on(env, logs):
    from investos.acquire.base import Outcome
    scripted(env, SUN, dated_body(FRI))
    scripted(env, MON, dated_body(MON))
    cls = date_class(fixed_keys=[SUN, MON])
    r = env.make(cls).run(rr(SUN, MON))
    assert r.outcomes == {SUN: Outcome.REJECTED, MON: Outcome.FETCHED} and r.status == "incomplete"
    assert manifest_keys(env) == [MON] and len(env.raw_files()) == 1
    assert events(logs(), "run_aborted") == []
    assert env.sidecar(r)["status"] != "aborted"


@pytest.mark.planted
def test_A69_rejected_reason_always_starts_with_machine_token(env):
    cases = [(blank_date_body(), "date_missing"), (dated_body("2026-11-19"), "date_mismatch")]
    for i, (body, token) in enumerate(cases):
        scripted(env, FRI, body)
        r = env.make(date_class(), run_id=f"run-{i}").run(rr(FRI))
        reason = _gap(env, r, FRI)["reason"]
        assert reason.startswith(token + ": ") and len(reason) > len(token) + 2


def test_A69_rejected_key_is_retried_and_fetches_when_the_server_fixes_itself(env):
    from investos.acquire.base import Outcome
    scripted(env, FRI, dated_body("2026-11-19"))
    assert env.make(date_class()).run(rr(FRI)).outcomes == {FRI: Outcome.REJECTED}
    scripted(env, FRI, dated_body(FRI))
    r = env.make(date_class(), run_id="run-2").run(rr(FRI))
    assert r.outcomes == {FRI: Outcome.FETCHED} and r.status == "complete" and len(env.manifest_lines()) == 1


def test_R34_rejected_is_a_seventh_outcome_with_its_own_value():
    from investos.acquire.base import Outcome
    assert Outcome("rejected") is Outcome.REJECTED and Outcome.REJECTED.value == "rejected"
    assert len(Outcome) == 7 and {o.value for o in Outcome} >= {"fetched", "fetched_empty", "missing_holiday",
                                                               "unreadable", "skipped", "incomplete", "rejected"}


# ============================================================ A70 (R1) base.py imports no parser
def test_A70_base_imports_no_parser_and_does_not_parse_dates():
    p = REPO / "src" / "investos" / "acquire" / "base.py"
    src = p.read_text()
    mods = set()
    for n in ast.walk(ast.parse(src)):
        if isinstance(n, ast.Import):
            mods |= {a.name for a in n.names}
        elif isinstance(n, ast.ImportFrom) and n.module:
            mods.add(n.module)
    forbidden = {"csv", "xml", "html", "bs4", "lxml", "openpyxl", "pandas", "xlrd", "yaml", "tomllib", "pyarrow",
                 "polars", "numpy", "html5lib", "dateutil", "pytz"}
    assert not {m for m in mods if m.split(".")[0] in forbidden}
    assert "strptime" not in src                                     # the base never parses a date format
    assert not re.search(r"json\.loads?\(\s*(resp|response|r)\.body", src)
    assert "class DateEvidence" in src and "REJECTED" in src          # the amendment is in this file


# ============================================================ A71 (R37) no clock, no zone
@pytest.mark.parametrize("now", [
    datetime(2031, 1, 1, 0, 0, tzinfo=timezone.utc),
    datetime(2026, 11, 19, 23, 59, tzinfo=timezone.utc),
    datetime(2026, 11, 21, 0, 30, tzinfo=timezone(timedelta(hours=5, minutes=30))),
    datetime(2026, 11, 20, 18, 31, tzinfo=timezone.utc),             # already the 21st in IST
    datetime(2026, 11, 20, 23, 0, tzinfo=timezone(timedelta(hours=-8))),
], ids=["2031", "day-before-utc", "ist-day-after", "utc-evening", "pacific"])
def test_A71_clock_and_zone_never_matter(env, now):
    from investos.acquire.base import Outcome
    env.clock = lambda: now
    scripted(env, FRI, dated_body(FRI))
    scripted(env, SUN, dated_body(FRI))
    r = env.make(date_class(fixed_keys=[FRI, SUN])).run(rr(FRI, SUN))
    assert r.outcomes == {FRI: Outcome.FETCHED, SUN: Outcome.REJECTED}
