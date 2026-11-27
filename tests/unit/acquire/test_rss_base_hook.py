"""S1-T6 step 2: base amendment R7, the default-off `unchanged_body(key, sha256, body)` hook (A9, A8 base part)."""
from __future__ import annotations

import re

import pytest

from _collector_helpers import CSV, CSV_HEADER_ONLY, D17, D18, demo_class, events, logs, resp, rr, sha, url_of  # noqa: F401
from _collector_helpers import env  # noqa: F401


def hooked(fn, **kw):
    return demo_class(unchanged_body=fn, **kw)


def run_one(env, cls, key=D17, body=CSV):
    env.transport.set(url_of(key), resp(200, body))
    r = env.make(cls).run(rr(key))
    return r


def test_A9_default_hook_returns_none(env):
    c = env.make(demo_class())
    assert c.unchanged_body(D17, sha(CSV), CSV) is None


@pytest.mark.planted
def test_A9_hook_string_makes_the_key_skipped_and_stores_nothing(env, logs):
    seen = []

    def hook(self, key, sha256, body):
        seen.append((key, sha256, body))
        return "same"
    r = run_one(env, hooked(hook))
    assert seen == [(D17, sha(CSV), CSV)]                      # base computes the sha and passes the raw body
    assert r.outcomes[D17].value == "skipped" and r.http_calls == 1 and r.status == "complete"
    assert env.manifest_lines() == [] and env.raw_files() == []
    sc = env.sidecar(r)
    assert sc["outcomes"]["skipped"] == 1 and sc["received"] == 1
    assert sc["gaps"] == [] and sc["outputs"] == []
    sk = [x for x in logs() if x["event"] == "skipped" and x["level"] == "INFO"]
    assert sk and sk[0]["fields"]["reason"] == "same"


def test_A9_header_only_body_is_hooked_too(env):
    r = run_one(env, hooked(lambda self, k, s, b: "same"), body=CSV_HEADER_ONLY)
    assert r.outcomes[D17].value == "skipped" and env.manifest_lines() == []


@pytest.mark.planted
def test_A9_hook_none_stores_the_body(env):
    calls = []
    r = run_one(env, hooked(lambda self, k, s, b: calls.append(k)))
    assert calls == [D17]
    assert r.outcomes[D17].value == "fetched" and len(env.manifest_lines()) == 1


@pytest.mark.planted
@pytest.mark.parametrize("bad", ["raise", 5, b"same", True, ""], ids=["raises", "int", "bytes", "bool", "empty"])
def test_A9_bad_hook_is_ignored_and_the_body_is_stored(env, logs, bad):
    def hook(self, k, s, b):
        if bad == "raise":
            raise RuntimeError("boom")
        return bad
    called = []
    r = run_one(env, hooked(lambda self, k, s, b: called.append(1) or hook(self, k, s, b)))
    assert called == [1]
    assert r.outcomes[D17].value == "fetched" and len(env.manifest_lines()) == 1
    if bad != "":
        assert any(x["event"] == "unchanged_hook_error" and x["level"] == "WARNING" for x in logs())


def test_A9_manifest_hit_comes_first_zero_http_and_hook_not_called(env):
    calls = []
    cls = hooked(lambda self, k, s, b: calls.append(k) or None)
    run_one(env, cls)
    assert calls == [D17]
    n = env.transport.n()
    r = env.make(cls, run_id="run-2").run(rr(D17))
    assert r.outcomes[D17].value == "skipped" and env.transport.n() == n and calls == [D17]


def test_A9_hook_not_called_for_unreadable_bodies(env):
    calls = []
    cls = hooked(lambda self, k, s, b: calls.append(k) or "same")
    r = run_one(env, cls, body=b"<html>no</html>")
    assert r.outcomes[D17].value == "unreadable" and calls == []


def test_A9_skipped_by_hook_keeps_run_complete_for_several_keys(env):
    cls = hooked(lambda self, k, s, b: "same" if k == D17 else None)
    env.transport.set(url_of(D17), resp(200, CSV))
    env.transport.set(url_of(D18), resp(200, CSV))
    r = env.make(cls).run(rr(D17, D18))
    assert r.status == "complete" and env.sidecar(r)["received"] == 2
    assert len(env.manifest_lines()) == 1


@pytest.mark.planted
def test_A8_base_still_imports_no_xml_parser_and_no_hook_parsing():
    from _rss_helpers import SRC
    src = (SRC / "acquire" / "base.py").read_text()
    assert not re.search(r"^\s*(import|from)\s+(xml|lxml|defusedxml|pyexpat)", src, re.M)
    assert "unchanged_body" in src                                # the hook exists in the base
    assert "lastBuildDate" not in src                             # digest logic stays in the adapter (R8a)
