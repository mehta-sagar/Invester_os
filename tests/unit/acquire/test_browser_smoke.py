"""S1-T7 R19: the one real-Chrome smoke test. Skipped unless INVESTOS_BROWSER_SMOKE=1 (and playwright importable).
Run by the Orchestrator on the Mac only: INVESTOS_BROWSER_SMOKE=1 pytest -m real_browser -s
Three requests in total (anchor, fetch, one diagnostic navigation), each at least 2.5 s apart. Nothing here touches
the repository's data, a manifest or git. Nothing at module level imports playwright."""
from __future__ import annotations

import hashlib
import os
import time

import pytest

FEED = "https://nsearchives.nseindia.com/content/RSS/Online_announcements.xml"


@pytest.mark.real_browser
@pytest.mark.skipif(os.environ.get("INVESTOS_BROWSER_SMOKE") != "1",
                    reason="set INVESTOS_BROWSER_SMOKE=1 to run the real-Chrome smoke test (Mac only)")
def test_real_chrome_smoke(tmp_path):
    sync_api = pytest.importorskip("playwright.sync_api")
    from _browser_helpers import ACCESS, HOSTS
    from _s1_helpers import S1Env
    from investos.acquire.browser import PsReaper, RealBrowserTransport
    from investos.parse.rss_items import parse_feed
    headless = os.environ.get("INVESTOS_BROWSER_HEADLESS", "") in ("1", "true", "yes")
    env = S1Env(tmp_path, access=ACCESS, hosts=dict(HOSTS))
    profile = tmp_path / "ledger" / ".state" / "chrome-profile"
    transport = RealBrowserTransport(env.cfg, headless=headless)
    t0 = time.monotonic()
    try:
        got = transport.fetch(FEED)                                  # the anchor and the data fetch
    finally:
        t1 = time.monotonic()
        transport.close()
    t2 = time.monotonic()
    assert got.status == 200
    assert len(parse_feed(got.body)) >= 1
    assert got.request_headers is not None
    assert not [k for k in got.request_headers if k.startswith(":")]
    ua = got.request_headers.get("user-agent", "")
    assert "Chrome/" in ua
    if not headless:
        assert "HeadlessChrome" not in ua
    assert "content-encoding" not in {k.lower() for k in got.headers}
    assert PsReaper().reap(profile, grace_s=1.0) == 0                # nothing of ours is left running
    fetched_sha = hashlib.sha256(got.body).hexdigest()
    print(f"\nheadless={headless} user-agent={ua}")
    print(f"fetch sha256={fetched_sha} bytes={len(got.body)}")
    print(f"elapsed: launch+anchor+fetch={t1 - t0:.2f}s close={t2 - t1:.2f}s")
    time.sleep(2.5)
    try:                                                              # diagnostic only: never asserted
        with sync_api.sync_playwright() as pw:
            ctx = pw.chromium.launch_persistent_context(user_data_dir=str(profile), channel="chrome",
                                                        headless=headless, no_viewport=True)
            try:
                page = ctx.pages[0] if ctx.pages else ctx.new_page()
                nav = page.goto(FEED, wait_until="commit")
                nav_body = nav.body() if nav is not None else b""
            finally:
                ctx.close()
        nav_sha = hashlib.sha256(nav_body).hexdigest()
        print(f"navigation sha256={nav_sha} equal_to_fetch={nav_sha == fetched_sha}")
    except Exception as exc:
        print(f"navigation diagnostic failed: {type(exc).__name__}: {str(exc)[:200]}")
