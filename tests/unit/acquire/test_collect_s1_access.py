"""S1-T1: access modes, decision rows, headers, contact (spec R17-R20, R31 order; A28-A32, A53b)."""
from __future__ import annotations

import re
from dataclasses import replace

import pytest

from _s1_helpers import (CONTACT, D17, D18, SOURCE, S1Env, cfg_access, demo_class, env, mode_cls,  # noqa: F401
                         resp, rr, scoped_env, url_of, write_decision)


def _ok_all(e, *keys):
    for k in keys:
        e.transport.set(url_of(k), resp(200))


# ------------------------------------------------------------------ A28 (R17)
@pytest.mark.planted
def test_A28_honest_class_with_config_real_browser_refused(tmp_path):
    e = S1Env(tmp_path, access=cfg_access("real_browser"))
    with pytest.raises(ValueError) as ei:
        e.make(demo_class())
    assert SOURCE in str(ei.value) and "real_browser" in str(ei.value) and "honest" in str(ei.value)


@pytest.mark.planted
def test_A28_real_browser_class_without_config_table_refused(tmp_path):
    e = S1Env(tmp_path)
    with pytest.raises(ValueError) as ei:
        e.make(mode_cls("real_browser"))
    assert SOURCE in str(ei.value)


@pytest.mark.planted
def test_A28_scoped_exception_class_with_honest_config_refused(tmp_path):
    e = S1Env(tmp_path, access=cfg_access("honest"))
    write_decision(tmp_path, "0003-x.md", f"Adapter: {SOURCE}", "Access-mode: scoped_exception")
    with pytest.raises(ValueError) as ei:
        e.make(mode_cls("scoped_exception"))
    assert SOURCE in str(ei.value)


@pytest.mark.planted
def test_A28_unknown_config_mode_rejected_by_load_config(tmp_path):
    from investos.core.config import ConfigError
    with pytest.raises(ConfigError) as ei:
        S1Env(tmp_path, access={SOURCE: {"mode": "stealth", "daily_cap": 5}})
    assert f"collect.access.{SOURCE}.mode" in str(ei.value)


def test_A28_matching_pairs_instantiate(tmp_path):
    S1Env(tmp_path / "a").make(demo_class())                                      # no table = honest
    S1Env(tmp_path / "b", access=cfg_access("honest")).make(demo_class())
    S1Env(tmp_path / "c", access=cfg_access("real_browser")).make(mode_cls("real_browser"))
    scoped_env(tmp_path / "d").make(mode_cls("scoped_exception"))


@pytest.mark.planted
def test_A28_non_honest_class_needs_daily_cap_base_rechecks(tmp_path):
    """The config validator enforces daily_cap; the base re-checks (bypass the validator with replace)."""
    e = S1Env(tmp_path, access=cfg_access("real_browser"))
    entry = replace(e.cfg.collect.access[SOURCE], daily_cap=None)
    cfg = replace(e.cfg, collect=replace(e.cfg.collect, access={SOURCE: entry}))
    with pytest.raises(ValueError):
        e.make(mode_cls("real_browser"), cfg=cfg)


# ------------------------------------------------------------------ A29 (R18)
@pytest.mark.planted
@pytest.mark.parametrize("case", ["no_decision_key", "no_file", "missing_adapter_line", "mode_honest",
                                  "two_files", "other_adapter", "case_differs", "partial_line", "no_mode_line"])
def test_A29_scoped_exception_decision_row_required(tmp_path, case):
    acc = cfg_access("scoped_exception", decision="0003")
    if case == "no_decision_key":                                 # the config validator refuses it, naming the key
        from investos.core.config import ConfigError
        with pytest.raises(ConfigError) as ei:
            S1Env(tmp_path, access={SOURCE: {"mode": "scoped_exception", "daily_cap": 5}})
        assert f"collect.access.{SOURCE}.decision" in str(ei.value)
        return
    e = S1Env(tmp_path, access=acc)
    good = [f"Adapter: {SOURCE}", "Access-mode: scoped_exception"]
    if case == "missing_adapter_line":
        write_decision(tmp_path, "0003-a.md", "Access-mode: scoped_exception")
    elif case == "no_mode_line":
        write_decision(tmp_path, "0003-a.md", f"Adapter: {SOURCE}")
    elif case == "mode_honest":
        write_decision(tmp_path, "0003-a.md", f"Adapter: {SOURCE}", "Access-mode: honest")
    elif case == "two_files":
        write_decision(tmp_path, "0003-a.md", *good)
        write_decision(tmp_path, "0003-b.md", *good)
    elif case == "other_adapter":
        write_decision(tmp_path, "0003-a.md", "Adapter: other_src", "Access-mode: scoped_exception")
    elif case == "case_differs":
        write_decision(tmp_path, "0003-a.md", f"adapter: {SOURCE}", "access-mode: scoped_exception")
    elif case == "partial_line":                                  # whole line only
        write_decision(tmp_path, "0003-a.md", f"Adapter: {SOURCE}_x", "Access-mode: scoped_exception extra")
    # no_file: write nothing
    with pytest.raises(ValueError):
        e.make(mode_cls("scoped_exception"))


def test_A29_correct_file_passes_in_either_line_order_anywhere(tmp_path):
    e = S1Env(tmp_path, access=cfg_access("scoped_exception", decision="0003"))
    write_decision(tmp_path, "0003-ok.md", "intro text", "Access-mode: scoped_exception", "more", f"Adapter: {SOURCE}")
    e.make(mode_cls("scoped_exception"))


@pytest.mark.planted
def test_A29_decision_check_repeated_at_every_instantiation(tmp_path):
    e = scoped_env(tmp_path)
    e.make(mode_cls("scoped_exception"))
    (tmp_path / "docs" / "decisions" / "0003-test.md").unlink()
    with pytest.raises(ValueError):
        e.make(mode_cls("scoped_exception"))


def test_A29_real_browser_without_decision_passes(tmp_path):
    S1Env(tmp_path, access=cfg_access("real_browser")).make(mode_cls("real_browser"))


@pytest.mark.planted
def test_A29_real_browser_with_decision_uses_same_file_test(tmp_path):
    e = S1Env(tmp_path, access=cfg_access("real_browser", decision="0004"))
    with pytest.raises(ValueError):
        e.make(mode_cls("real_browser"))                                      # no file
    write_decision(tmp_path, "0004-rb.md", f"Adapter: {SOURCE}", "Access-mode: real_browser")
    e.make(mode_cls("real_browser"))


# ------------------------------------------------------------------ A30 (R19, R20)
def test_A30_honest_request_has_only_ua_and_accept_encoding(tmp_path):
    import investos
    e = S1Env(tmp_path)
    _ok_all(e, D17, D18)
    e.make().run(rr(D17, D18))
    assert e.transport.n() == 2
    pat = re.compile(r"^InvestOS/" + re.escape(investos.__version__) + r" \(personal research; test@example\.invalid\)$")
    for c in e.transport.calls:
        assert set(c["headers"]) == {"User-Agent", "Accept-Encoding"}
        assert c["headers"]["Accept-Encoding"] == "identity"
        assert pat.match(c["headers"]["User-Agent"])


def test_A30_fake_with_accept_encoding_attribute_is_advertised(tmp_path):
    e = S1Env(tmp_path)
    e.transport.accept_encoding = "gzip, deflate"
    _ok_all(e, D17)
    e.make().run(rr(D17))
    assert e.transport.calls[0]["headers"]["Accept-Encoding"] == "gzip, deflate"


def test_A30_declared_extra_headers_sent_and_nothing_else(tmp_path):
    e = S1Env(tmp_path)
    _ok_all(e, D17)
    e.make(demo_class(extra_headers={"Accept": "text/csv", "X-Api-Version": "2"})).run(rr(D17))
    h = e.transport.calls[0]["headers"]
    assert set(h) == {"User-Agent", "Accept-Encoding", "Accept", "X-Api-Version"}


@pytest.mark.planted
def test_A30_headers_identical_across_retries_and_keys(tmp_path):
    e = S1Env(tmp_path)
    e.transport.set(url_of(D17), resp(503), resp(200))
    e.transport.set(url_of(D18), resp(200))
    e.make().run(rr(D17, D18))
    assert e.transport.n() == 3
    assert len({tuple(sorted(c["headers"].items())) for c in e.transport.calls}) == 1


def test_A30_timeout_comes_from_config_unless_class_overrides(tmp_path):
    e = S1Env(tmp_path / "a", timeout_s=7.5)
    _ok_all(e, D17)
    e.make().run(rr(D17))
    assert e.transport.calls[0]["timeout"] == 7.5
    e2 = S1Env(tmp_path / "b")
    _ok_all(e2, D17)
    e2.make().run(rr(D17))
    assert e2.transport.calls[0]["timeout"] == 30.0
    e3 = S1Env(tmp_path / "c", timeout_s=7.5)
    _ok_all(e3, D17)
    e3.make(demo_class(timeout_s=12.0)).run(rr(D17))
    assert e3.transport.calls[0]["timeout"] == 12.0


# ------------------------------------------------------------------ A31 (R19)
@pytest.mark.planted
@pytest.mark.parametrize("name", ["Referer", "cookie", "User_Agent", "Origin", "Accept-Encoding", "Authorization"])
def test_A31_honest_class_cannot_set_denied_headers(tmp_path, name):
    with pytest.raises(ValueError):
        S1Env(tmp_path).make(demo_class(extra_headers={name: "x"}))


@pytest.mark.parametrize("name", ["Referer", "Cookie", "User-Agent"])
def test_A31_scoped_exception_may_set_referer_cookie_ua(tmp_path, name):
    e = scoped_env(tmp_path)
    _ok_all(e, D17)
    e.make(mode_cls("scoped_exception", extra_headers={name: "val-1"})).run(rr(D17))
    assert e.transport.calls[0]["headers"][name] == "val-1"
    if name == "User-Agent":                                          # replaces the honest UA, not added
        assert [k for k in e.transport.calls[0]["headers"] if k.lower() == "user-agent"] == ["User-Agent"]


@pytest.mark.planted
@pytest.mark.parametrize("name", ["Origin", "Authorization", "Host", "X-Forwarded-For", "Accept-Encoding", "Via",
                                  "X-Real-IP", "Cookie2", "Forwarded", "True-Client-IP", "Proxy-Authorization"])
def test_A31_scoped_exception_still_refuses_the_rest(tmp_path, name):
    with pytest.raises(ValueError):
        scoped_env(tmp_path).make(mode_cls("scoped_exception", extra_headers={name: "x"}))


@pytest.mark.planted
def test_A31_headers_for_validated_on_every_call(tmp_path):
    e = S1Env(tmp_path)
    _ok_all(e, D17, D18)
    n = {"i": 0}

    class Bad(demo_class()):
        def headers_for(self, key, url):
            n["i"] += 1
            return {"X-Ok": "1"} if n["i"] == 1 else {"Host": "evil.test"}

    with pytest.raises(ValueError):
        e.make(Bad).run(rr(D17, D18))
    assert e.transport.n() == 1                                       # second call refused before its request


@pytest.mark.planted
def test_A31_headers_for_on_honest_class_cannot_smuggle_referer(tmp_path):
    e = S1Env(tmp_path)
    _ok_all(e, D17)

    class Bad(demo_class()):
        def headers_for(self, key, url):
            return {"Referer": "https://x.test/"}

    with pytest.raises(ValueError):
        e.make(Bad).run(rr(D17))
    assert e.transport.n() == 0


def test_A31_scoped_headers_for_per_request_values(tmp_path):
    e = scoped_env(tmp_path)
    _ok_all(e, D17, D18)

    class Per(mode_cls("scoped_exception")):
        def headers_for(self, key, url):
            return {"Referer": f"https://example.test/page/{key}"}

    e.make(Per).run(rr(D17, D18))
    assert [c["headers"]["Referer"] for c in e.transport.calls] == [f"https://example.test/page/{D17}",
                                                                    f"https://example.test/page/{D18}"]


# ------------------------------------------------------------------ A32 (R20)
BAD_CONTACTS = [None, "", "ab", "a;b@x.com", "a(b)@x.com", "a)b@x.com", "é@x.com", "a" * 89 + "@example.com",
                " a@x.com", "a@x.com ", "a@x.com\r\nX-Evil: 1", "a\nb@x.com", "a\0b@x.com", "a\tb@x.com"]


@pytest.mark.planted
@pytest.mark.parametrize("contact", BAD_CONTACTS, ids=lambda c: repr(c)[:30])
def test_A32_bad_contact_refused_at_instantiation(tmp_path, contact):
    e = S1Env(tmp_path)
    assert len("a" * 89 + "@example.com") == 101
    with pytest.raises(ValueError):
        e.make(cfg=e.cfg_contact(contact))


@pytest.mark.parametrize("contact", ["a@b", "x" * 88 + "@example.com", "dedicated-inbox@example.org"])
def test_A32_valid_contacts_accepted(tmp_path, contact):
    assert len(contact) <= 100
    e = S1Env(tmp_path)
    _ok_all(e, D17)
    e.make(cfg=e.cfg_contact(contact)).run(rr(D17))
    assert f"personal research; {contact})" in e.transport.calls[0]["headers"]["User-Agent"]


@pytest.mark.planted
def test_A32_invalid_placeholder_contact_refused_with_real_transport_only(tmp_path):
    from investos.acquire.transport import UrllibTransport
    e = S1Env(tmp_path, contact="x@example.invalid")
    e.make()                                                          # fake transport: passes
    with pytest.raises(ValueError):
        e.make(transport=UrllibTransport())
    S1Env(tmp_path / "ok", contact="ops@example.org").make(transport=UrllibTransport())


# ------------------------------------------------------------------ A53 second half (R31 order of failures)
@pytest.mark.planted
def test_A53_contact_error_wins_over_mode_error(tmp_path):
    e = S1Env(tmp_path, access=cfg_access("real_browser"))
    with pytest.raises(ValueError, match="collect.contact is not configured"):
        e.make(demo_class(), cfg=e.cfg_contact(None))                 # bad contact AND bad mode


@pytest.mark.planted
def test_A53_mode_error_wins_over_decision_error(tmp_path):
    e = S1Env(tmp_path, access=cfg_access("honest"))                  # class scoped, config honest, no decision file
    with pytest.raises(ValueError) as ei:
        e.make(mode_cls("scoped_exception"))
    assert "honest" in str(ei.value)


@pytest.mark.planted
def test_A53_decision_error_wins_over_wiring_error(tmp_path):
    e = S1Env(tmp_path, access=cfg_access("scoped_exception", decision="0003"))
    with pytest.raises(ValueError) as ei:
        e.make(mode_cls("scoped_exception"), transport=None, browser=None)   # no decision file AND no transport
    assert "0003" in str(ei.value) or "decision" in str(ei.value).lower()
