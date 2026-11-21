"""S0-T4 collector base: planted data traps from R1_SYNTHESIS section 4 that belong in the base.

Source-specific parsing is out of scope; these check that the base never turns a bad response into
stored data, never invents an 'empty' or 'holiday' verdict, and never records a block as a holiday.
"""
from __future__ import annotations

import json

import pytest

from _collector_helpers import (CSV, D17, D18, D19, SOURCE, demo_class, env, events, logs, resp, rr,  # noqa: F401
                                url_of)

pytestmark = pytest.mark.planted


def _nothing_stored(env):
    assert env.raw_files() == [] and env.manifest_lines() == []


# ---- T1: HTTP 200 carrying a page/stub/error payload instead of data
@pytest.mark.parametrize("body,headers", [
    (b"<html>Error: data not available</html>", {"content-type": "text/html"}),
    (b"<!DOCTYPE html><html><body>page moved</body></html>" + b" " * 1760, {"content-type": "text/html"}),  # ~1814 B shell
    (b"<html>" + b"x" * 13_700 + b"</html>", {"content-type": "text/html"}),                                 # ~13.7 kB stub
    (b"", {}),
    (b"No Data Found", {"content-type": "text/plain"}),
])
def test_T1_http200_stub_or_error_body_is_unreadable_and_not_stored(env, body, headers):
    from investos.acquire.base import Outcome
    env.transport.set(url_of(D17), resp(200, body, headers))
    r = env.make().run(rr(D17))
    assert r.outcomes == {D17: Outcome.UNREADABLE}
    _nothing_stored(env)
    assert env.sidecar(r)["gaps"][0]["key"] == D17


def test_T1b_check_body_receives_the_real_response_and_runs_even_on_200(env):
    from investos.acquire.base import BodyVerdict, Outcome
    seen = []

    def v(self, key, r):
        seen.append((key, r.status, r.headers, r.body, r.url))
        return BodyVerdict(Outcome.UNREADABLE, "stub body (content-type text/html)")
    body, hdr = b"<html>stub</html>", {"content-type": "text/html"}
    env.transport.set(url_of(D17), resp(200, body, hdr, url_of(D17)))
    env.make(demo_class(verdict_fn=v)).run(rr(D17))
    assert seen == [(D17, 200, hdr, body, url_of(D17))]
    _nothing_stored(env)


def test_T1c_rate_limit_error_payload_at_200_is_unreadable_not_retried_not_holiday(env):
    from investos.acquire.base import Outcome
    for k in (D17, D18):
        env.transport.set(url_of(k), resp(200, b'{"error":"rate limit exceeded","retry_after":60}'))
    r = env.make().run(rr(D17, D18))
    assert r.outcomes == {D17: Outcome.UNREADABLE, D18: Outcome.UNREADABLE}   # did not abort; both attempted
    assert env.transport.n(url_of(D17)) == 1
    assert Outcome.MISSING_HOLIDAY not in r.outcomes.values()
    _nothing_stored(env)


def test_T1d_http200_with_wrong_content_never_downgrades_a_good_verdict(env):
    """Only the subclass decides: a body the subclass accepts is stored byte-for-byte."""
    env.transport.set(url_of(D17), resp(200, CSV, {"content-type": "application/octet-stream"}))
    r = env.make().run(rr(D17))
    assert env.store.read_bytes(SOURCE, json.loads(env.manifest_lines()[0])["sha256"]) == CSV


# ---- T2: empty-result sentinels; empty only when the subclass says so
SENTINELS = [b"No Record Found!", b"No Data Found", b"[]", b'{"data":[]}', b"No results returned"]


@pytest.mark.parametrize("body", SENTINELS)
def test_T2a_sentinel_without_subclass_knowledge_is_unreadable(env, body):
    from investos.acquire.base import Outcome
    env.transport.set(url_of(D17), resp(200, body))
    r = env.make().run(rr(D17))                      # demo check_body does not know these sentinels
    assert r.outcomes == {D17: Outcome.UNREADABLE}
    _nothing_stored(env)


@pytest.mark.parametrize("body", [b"No Record Found!", b"No Data Found"])
def test_T2b_sentinel_is_fetched_empty_only_when_subclass_says_so(env, body):
    from investos.acquire.base import BodyVerdict, Outcome

    def v(self, key, r):
        if r.body == body:
            return BodyVerdict(Outcome.FETCHED_EMPTY, "source says no records (control key passed)")
        return BodyVerdict(Outcome.UNREADABLE, "unknown")
    env.transport.set(url_of(D17), resp(200, body))
    r = env.make(demo_class(verdict_fn=v)).run(rr(D17))
    assert r.outcomes == {D17: Outcome.FETCHED_EMPTY}
    assert env.store.get_by_key(SOURCE, D17) is not None            # evidence kept
    sc = env.sidecar(r)
    assert sc["outcomes"]["fetched_empty"] == 1 and sc["outcomes"]["missing_holiday"] == 0


def test_T2c_base_never_infers_empty_from_body_size(env):
    from investos.acquire.base import Outcome
    env.transport.set(url_of(D17), resp(200, b""))
    r = env.make().run(rr(D17))
    assert r.outcomes[D17] is Outcome.UNREADABLE
    _nothing_stored(env)


# ---- T3 / T23: 404 on a calendar day, blocks, holidays
def test_T3a_404_is_holiday_only_via_subclass_verdict(env):
    from investos.acquire.base import BodyVerdict, Outcome
    env.transport.set(url_of(D17), resp(404, b"Not Found"))
    r = env.make(demo_class(verdict_fn=lambda s, k, x: BodyVerdict(Outcome.UNREADABLE, "404 not confirmed holiday"))).run(rr(D17))
    assert r.outcomes == {D17: Outcome.UNREADABLE}
    env.fresh_transport()
    env.transport.set(url_of(D17), resp(404, b"Not Found"))
    r2 = env.make(demo_class(verdict_fn=lambda s, k, x: BodyVerdict(Outcome.MISSING_HOLIDAY, "calendar says closed")),
                  run_id="run-2").run(rr(D17))
    assert r2.outcomes == {D17: Outcome.MISSING_HOLIDAY}
    _nothing_stored(env)


@pytest.mark.parametrize("status", [404, 204, 410, 200])
def test_T3b_base_never_invents_missing_holiday(env, status):
    from investos.acquire.base import Outcome
    env.transport.set(url_of(D17), resp(status, b"anything but the subclass sentinel"))
    r = env.make().run(rr(D17))
    assert r.outcomes[D17] is Outcome.UNREADABLE


@pytest.mark.parametrize("status", [403, 429, 503, 401, 451])
def test_T3c_block_or_throttle_never_recorded_as_holiday(env, status):
    """Even when the body is exactly the subclass's holiday sentinel."""
    from investos.acquire.base import Outcome
    env.transport.set(url_of(D17), resp(status, b"NO FILE"))
    r = env.make().run(rr(D17))
    assert r.outcomes == {D17: Outcome.UNREADABLE}
    sc = env.sidecar(r)
    assert sc["outcomes"]["missing_holiday"] == 0 and sc["received"] == 0
    assert sc["gaps"][0]["key"] == D17
    _nothing_stored(env)


def test_T3d_timeouts_are_not_holidays(env):
    from investos.acquire.base import Outcome
    from _collector_helpers import terr
    env.transport.set(url_of(D17), terr("read timeout"))
    r = env.make().run(rr(D17))
    assert r.outcomes == {D17: Outcome.UNREADABLE}
    assert env.sidecar(r)["outcomes"]["missing_holiday"] == 0


def test_T3e_calendar_days_all_requested_weekends_included(env):
    """Weekend special sessions exist: base asks the subclass for every key, no weekday filtering."""
    from investos.acquire.base import Outcome
    keys = ["2026-11-20", "2026-11-21", "2026-11-22"]       # Fri, Sat, Sun
    for k in keys:
        env.transport.set(url_of(k), resp(404, b"NO FILE"))
    r = env.make().run(rr(keys[0], keys[-1]))
    assert list(r.outcomes) == keys and set(r.outcomes.values()) == {Outcome.MISSING_HOLIDAY}
    assert env.transport.n() == 3


# ---- T6 / T7: row caps
def _json_rows(n):
    return json.dumps({"rows": list(range(n))}).encode()


def _json_verdict(self, key, r):
    from investos.acquire.base import BodyVerdict, Outcome
    return BodyVerdict(Outcome.FETCHED, record_count=len(json.loads(r.body)["rows"]))


@pytest.mark.parametrize("limit,n,expect", [(70, 70, "incomplete"), (70, 69, "fetched"), (100, 100, "incomplete"),
                                            (100, 101, "incomplete"), (70, 0, "fetched")])
def test_T7_json_capped_at_exact_page_limit_is_incomplete(env, limit, n, expect):
    env.transport.set(url_of(D17), resp(200, _json_rows(n)))
    r = env.make(demo_class(page_limit=limit, verdict_fn=_json_verdict)).run(rr(D17))
    assert r.outcomes[D17].value == expect
    if expect == "incomplete":
        _nothing_stored(env)
        g = env.sidecar(r)["gaps"]
        assert g[0]["key"] == D17 and g[0]["outcome"] == "incomplete" and str(limit) in g[0]["reason"]
        assert env.sidecar(r)["status"] == "incomplete"
    else:
        assert len(env.manifest_lines()) == 1


def test_T7b_incomplete_page_is_retried_on_next_run_not_skipped(env):
    from investos.acquire.base import Outcome
    env.transport.set(url_of(D17), resp(200, _json_rows(70)))
    env.make(demo_class(page_limit=70, verdict_fn=_json_verdict)).run(rr(D17))
    env.fresh_transport()
    env.transport.set(url_of(D17), resp(200, _json_rows(12)))
    r = env.make(demo_class(page_limit=70, verdict_fn=_json_verdict), run_id="run-2").run(rr(D17))
    assert r.outcomes == {D17: Outcome.FETCHED} and env.transport.n() == 1


def test_T7c_page_limit_not_applied_to_fetched_empty_or_other_sources(env):
    from investos.acquire.base import BodyVerdict, Outcome
    env.transport.set(url_of(D17), resp(200, b"x"))
    r = env.make(demo_class(page_limit=5, verdict_fn=lambda s, k, x: BodyVerdict(Outcome.FETCHED_EMPTY, "e", 5))).run(rr(D17))
    assert r.outcomes == {D17: Outcome.FETCHED_EMPTY}           # R10 applies to FETCHED verdicts only
    env2 = env.make(demo_class(page_limit=None, verdict_fn=lambda s, k, x: BodyVerdict(Outcome.FETCHED, record_count=10**6)),
                    run_id="run-2")
    env.fresh_transport()
    env.transport.set(url_of(D18), resp(200, b"x"))
    assert env2.run(rr(D18)).outcomes == {D18: Outcome.FETCHED}
