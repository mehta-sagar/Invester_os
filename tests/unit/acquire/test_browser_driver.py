"""S1-T7 step 3: PlaywrightDriver against the fake Playwright (spec collect_browser R2, R3, R8b-d, R9, R15; A2, A3, A16,
A28 driver part). The fake raises AssertionError when the driver touches request, route, init scripts, CDP or input."""
from __future__ import annotations

import re
import sys
from pathlib import Path

import pytest

from _browser_helpers import (ANCHOR, N0, URL_N1, BrowserEnv, PWWorld, nse_rows)
from _rss_helpers import SRC

BROWSER_PY = SRC / "acquire" / "browser.py"


def driver_cycle(tmp_path, n=2, **kw):
    e = BrowserEnv(tmp_path, nse_rows(n), **kw)
    w = PWWorld(e.log)
    rb = e.rb(driver=w.driver())
    return e, w, rb


# ------------------------------------------------------------------ A2 (R3): the page-side function is pinned
INIT_RE = re.compile(r"\bfetch\(\s*[^,()]+?\s*,\s*\{(?P<init>[^{}]*)\}\s*\)", re.S)
ALLOWED_KEYS = {"cache", "redirect", "signal"}
FORBIDDEN_JS = [r"\bcredentials\b", r"\breferrer", r"\bmode\b", r"\bintegrity\b", r"\bkeepalive\b", r"\bnavigator\b",
                r"XMLHttpRequest", r"sendBeacon", r"document\.cookie", r"new\s+Request\b", r"WebSocket",
                r"localStorage", r"\beval\b"]


def assert_js_is_pinned(js: str) -> None:
    assert len(re.findall(r"\bfetch\(", js)) == 1, "exactly one fetch call"
    m = INIT_RE.search(js)
    assert m, "fetch(url, {cache, redirect, signal}) not found"
    init = m.group("init")
    keys = re.findall(r"(?:^|,)\s*([A-Za-z_]\w*)\s*:", init)
    assert sorted(keys) == sorted(ALLOWED_KEYS), keys
    assert re.search(r"cache\s*:\s*[\"']no-store[\"']", init)
    assert re.search(r"redirect\s*:\s*[\"']manual[\"']", init)
    assert re.search(r"signal\s*:\s*AbortSignal\.timeout\(", init)
    for bad in FORBIDDEN_JS:
        assert not re.search(bad, js), bad


def one_fetch_js(tmp_path):
    e, w, rb = driver_cycle(tmp_path, 1)
    with rb:
        rb.fetch(URL_N1)
    assert len(w.evaluates) == 1
    return w.js(), w


def test_A2_exactly_one_evaluate_carries_the_fixed_fetch_function(tmp_path):
    js, w = one_fetch_js(tmp_path)
    assert_js_is_pinned(js)
    assert URL_N1 in repr(w.evaluates[0][1])                       # the URL travels as the argument, not in the text
    assert URL_N1 not in js


@pytest.mark.planted
@pytest.mark.parametrize("plant", [
    lambda t: t.replace("{", '{ headers: {"x-y": "z"}, ', 1),
    lambda t: t.replace("{", '{ referrer: "https://x.test/", ', 1),
    lambda t: re.sub(r'cache\s*:\s*["\']no-store["\']', 'credentials: "include"', t),
    lambda t: re.sub(r'cache\s*:\s*["\']no-store["\']', 'cache: "no-store", mode: "no-cors"', t),
    lambda t: re.sub(r'redirect\s*:\s*["\']manual["\'],?', "", t),
    lambda t: re.sub(r'redirect\s*:\s*["\']manual["\']', 'redirect: "follow"', t),
    lambda t: t + "\n;navigator.userAgent;",
], ids=["headers", "referrer", "credentials", "mode", "no_redirect_key", "follow", "navigator"])
def test_A2_a_planted_variant_of_the_function_fails_the_source_pin(tmp_path, plant):
    js, _ = one_fetch_js(tmp_path)
    # plant only inside the init object where applicable: the replacement targets the first brace after "fetch("
    i = js.index("fetch(")
    planted = js[:i] + plant(js[i:])
    assert planted != js
    with pytest.raises(AssertionError):
        assert_js_is_pinned(planted)


# ------------------------------------------------------------------ A3 (R2, R8d)
def test_A3_a_full_cycle_touches_none_of_the_forbidden_playwright_apis(tmp_path):
    e, w, rb = driver_cycle(tmp_path, 3)
    with rb:
        e.cycle(N0, browser=rb)
    assert w.touched == []
    assert len(w.fetch_urls) == 3 and len(w.evaluates) == 3
    assert len(w.gotos) == 1 and w.gotos[0][0] == ANCHOR
    assert len(w.pages_used) == 1                                 # one page, one tab, reused for the cycle
    assert w.closed.count("context") == 1 and "playwright" in w.closed


@pytest.mark.planted
def test_A3_a_driver_that_used_playwrights_own_http_client_would_be_caught_by_the_fake(tmp_path):
    e, w, rb = driver_cycle(tmp_path, 1)
    with rb:
        rb.fetch(URL_N1)
    page = w.context.pages[0]
    for name in ("request", "route", "add_init_script", "new_cdp_session", "mouse", "keyboard"):
        with pytest.raises(AssertionError):
            getattr(page, name)
    assert set(w.touched) == {"request", "route", "add_init_script", "new_cdp_session", "mouse", "keyboard"}
    with pytest.raises(AssertionError):
        w.context.request
    with pytest.raises(AssertionError):
        w.context.route


def test_A3_anchor_navigation_waits_for_commit_with_the_calls_timeout_and_nothing_else(tmp_path):
    e, w, rb = driver_cycle(tmp_path, 1, timeout_s=12)
    with rb:
        rb.fetch(URL_N1)
    url, kw = w.gotos[0]
    assert url == ANCHOR and kw.get("wait_until") == "commit"
    assert kw.get("timeout") in (12000, 12000.0)
    assert set(kw) <= {"wait_until", "timeout"}                    # no referer, no other option


# ------------------------------------------------------------------ A16 (R8b, R8c) and A17 (R9)
LAUNCH_KEYS = {"user_data_dir", "channel", "headless", "args", "no_viewport", "timeout"}
ARG_RES = (re.compile(r"^--window-position=-?\d{1,5},-?\d{1,5}$"), re.compile(r"^--window-size=\d{3,5},\d{3,5}$"))


def assert_launch_kwargs(kw: dict, profile: Path) -> None:
    assert set(kw) == LAUNCH_KEYS, sorted(set(kw) ^ LAUNCH_KEYS)
    assert kw["channel"] == "chrome" and kw["no_viewport"] is True and isinstance(kw["headless"], bool)
    assert Path(kw["user_data_dir"]) == profile
    assert isinstance(kw["args"], list) and all(any(r.match(a) for r in ARG_RES) for a in kw["args"]), kw["args"]


@pytest.mark.parametrize("headless", [False, True])
def test_A16_launch_persistent_context_gets_exactly_the_allowed_keywords(tmp_path, headless):
    w = PWWorld()
    prof = tmp_path / "chrome-profile"
    w.driver().launch(prof, headless, 60)
    assert len(w.launch_calls) == 1
    kw = w.launch_calls[0]
    assert_launch_kwargs(kw, prof)
    assert kw["headless"] is headless
    assert kw["args"] == ["--window-position=-2400,-2400", "--window-size=1280,800"]       # A17: the same in both modes
    assert kw["timeout"] in (60000, 60000.0)


@pytest.mark.planted
@pytest.mark.parametrize("extra", [{"user_agent": "x"}, {"proxy": {"server": "http://x"}}, {"viewport": {"width": 1}},
                                   {"locale": "en-US"}, {"extra_http_headers": {"a": "b"}},
                                   {"ignore_default_args": ["--x"]}, {"executable_path": "/x"}, {"slow_mo": 5},
                                   {"timezone_id": "UTC"}, {"permissions": []}])
def test_A16_a_planted_extra_keyword_fails_the_pin(tmp_path, extra):
    w = PWWorld()
    prof = tmp_path / "chrome-profile"
    w.driver().launch(prof, False, 60)
    with pytest.raises(AssertionError):
        assert_launch_kwargs({**w.launch_calls[0], **extra}, prof)


@pytest.mark.planted
@pytest.mark.parametrize("arg", ["--disable-blink-features=AutomationControlled", "--user-agent=x",
                                 "--proxy-server=x", "--headless=new", "--window-position=a,b"])
def test_A16_a_planted_launch_argument_fails_the_pin(tmp_path, arg):
    w = PWWorld()
    prof = tmp_path / "chrome-profile"
    w.driver().launch(prof, False, 60)
    kw = dict(w.launch_calls[0])
    kw["args"] = kw["args"] + [arg]
    with pytest.raises(AssertionError):
        assert_launch_kwargs(kw, prof)


def test_A16_session_close_closes_the_context_then_stops_playwright(tmp_path):
    w = PWWorld()
    s = w.driver().launch(tmp_path / "chrome-profile", False, 60)
    s.close(15)
    assert w.closed == ["context", "playwright"]


# ------------------------------------------------------------------ A28 (R15): lazy import, hint, no download
def test_A28_launch_without_playwright_installed_says_how_to_install_it(tmp_path, monkeypatch):
    from investos.acquire.base import TransportError
    from investos.acquire.browser import PlaywrightDriver
    monkeypatch.setitem(sys.modules, "playwright", None)
    monkeypatch.setitem(sys.modules, "playwright.sync_api", None)
    with pytest.raises(TransportError) as ei:
        PlaywrightDriver().launch(tmp_path / "chrome-profile", False, 60)
    assert str(ei.value) == "browser unavailable: playwright is not installed (pip install 'investos[browser]')"


@pytest.mark.planted
def test_A28_a_chrome_that_cannot_start_is_browser_unavailable_with_at_most_200_characters(tmp_path):
    from investos.acquire.base import TransportError
    for msg in ("Chromium distribution 'chrome' is not found", "x" * 500):
        w = PWWorld()
        w.launch_error = Exception(msg)
        with pytest.raises(TransportError) as ei:
            w.driver().launch(tmp_path / "chrome-profile", False, 60)
        text = str(ei.value)
        assert text.startswith("browser unavailable: ") and msg[:50] in text
        assert len(text) <= len("browser unavailable: ") + 200
    assert w.closed.count("playwright") >= 1                      # a failed launch does not leak the playwright driver


def test_A28_the_driver_never_downloads_a_browser():
    s = BROWSER_PY.read_text()
    assert not re.search(r"playwright\s+install", s)
    assert not re.search(r"\.install\s*\(", s) and not re.search(r"""['"]install['"]""", s.replace("pip install", ""))
    assert "executable_path" not in s and "chromium.launch(" not in s


def test_A28_playwright_is_imported_inside_a_function_only():
    import ast
    tree = ast.parse(BROWSER_PY.read_text())

    def is_pw(n):
        mods = [n.module or ""] if isinstance(n, ast.ImportFrom) else [a.name for a in n.names]
        return any(m.split(".")[0] == "playwright" for m in mods)
    top = [n for n in tree.body if isinstance(n, (ast.Import, ast.ImportFrom)) and is_pw(n)]
    assert top == [], "playwright imported at module level"
    anywhere = [n for n in ast.walk(tree) if isinstance(n, (ast.Import, ast.ImportFrom)) and is_pw(n)]
    assert anywhere, "playwright must be imported (lazily) somewhere in browser.py"
