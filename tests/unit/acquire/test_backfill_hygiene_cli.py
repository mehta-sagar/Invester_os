"""S1-T8: plist (R18, A35), no evasion (R16, A32), known_at (R25), the CLI (R3, R13, R19), A40 smoke, A41."""
from __future__ import annotations

import os
import re
import subprocess
import sys
import xml.dom.minidom
from pathlib import Path

import pytest

from _backfill_helpers import BfEnv, COM, IN, TUESDAY_NOON, ist, one, status_result
from _rss_helpers import REPO

SRC = REPO / "src" / "investos"
PLIST = REPO / "ops" / "launchd" / "com.investos.backfill.plist.example"
FORBIDDEN = ["stealth", "webdriver", "fake_useragent", "AutomationControlled", "undetected", "patchright", "rebrowser",
             "camoufox", "nodriver", "curl_cffi", "ja3", "impersonat", "userAgent", "user_agent", "extra_http_headers",
             "set_extra_http_headers", "add_init_script", "ignore_default_args", "new_cdp_session",
             "evaluate_on_new_document", "proxy", "socks", "jitter", "import random", "random.", "uniform(", ".mouse",
             ".keyboard", ".click(", ".dblclick(", ".fill(", ".type(", ".press(", ".hover(", ".tap(", ".dispatch_event(",
             "captcha", "CAPTCHA", "apify", "scrapingbee", "brightdata"]


def hits(text: str) -> list[str]:
    return [s for s in FORBIDDEN if s in text]


# ------------------------------------------------------------------ A35 plist
def plist_text() -> str:
    assert PLIST.exists(), "ops/launchd/com.investos.backfill.plist.example is missing"
    return PLIST.read_text()


def plist_problems(text: str) -> list[str]:
    out = []
    try:
        xml.dom.minidom.parseString(text.encode())
    except Exception as e:
        return [f"not XML: {e}"]
    for need in ("caffeinate", "backfill", "run", "StartCalendarInterval", "RunAtLoad", "LimitLoadToSessionType",
                 "Aqua", "com.investos.backfill", "Asia/Kolkata", "pmset"):
        if need not in text:
            out.append(f"missing {need}")
    if not re.search(r"<key>Hour</key>\s*<integer>0</integer>\s*<key>Minute</key>\s*<integer>35</integer>", text):
        out.append("not 0:35")
    if not re.search(r"<key>RunAtLoad</key>\s*<true\s*/>", text):
        out.append("RunAtLoad not true")
    if not re.search(r"<key>LimitLoadToSessionType</key>\s*<string>Aqua</string>", text):
        out.append("not Aqua")
    if not re.search(r"(sleep|wake|missed)", text, re.I):
        out.append("no missed-run comment")
    if not re.search(r"\.state", text):
        out.append("logs not under .state")
    return out


def test_A35_the_example_plist_has_everything_the_spec_names():
    assert plist_problems(plist_text()) == []


def test_A35_caffeinate_comes_before_investos_in_the_program_arguments():
    t = plist_text()
    arr = re.search(r"<key>ProgramArguments</key>\s*<array>(.*?)</array>", t, re.S).group(1)
    strings = re.findall(r"<string>(.*?)</string>", arr)
    assert "caffeinate" in strings[0] and strings[-2:] == ["backfill", "run"] and strings[-3].endswith("/investos")


@pytest.mark.planted
@pytest.mark.parametrize("edit", [lambda t: t.replace("Aqua", "Background"), lambda t: t.replace("caffeinate", "nohup"),
                                  lambda t: t.replace("<integer>35</integer>", "<integer>5</integer>"),
                                  lambda t: t.replace("pmset", "xxxxx"), lambda t: t.replace("Asia/Kolkata", "UTC"),
                                  lambda t: t.replace("<key>RunAtLoad</key><true/>", "<key>RunAtLoad</key><false/>")])
def test_A35_a_plist_that_loses_a_required_part_fails(edit):
    assert plist_problems(edit(plist_text())) != []


# ------------------------------------------------------------------ A32 / R16 no evasion
def backfill_sources() -> list[Path]:
    return sorted((SRC / "backfill").glob("*.py"))


def test_A32_the_backfill_package_exists_with_the_modules_of_the_spec():
    names = {p.name for p in backfill_sources()}
    assert {"endpoints.py", "ledger.py", "planner.py", "collector.py", "probe.py", "runner.py", "status.py"} <= names


@pytest.mark.parametrize("path", backfill_sources(), ids=lambda p: p.name)
def test_A32_no_backfill_module_holds_a_forbidden_string_comments_included(path):
    assert hits(path.read_text()) == [], path.name


def test_A32_the_browser_transport_still_holds_none_of_the_strings_after_the_amendments():
    assert hits((SRC / "acquire" / "browser.py").read_text()) == []


@pytest.mark.planted
@pytest.mark.parametrize("plant", ["import random", "page.mouse.move(1, 2)", "# stealth", "x = proxy", "page.click(1)"])
def test_A32_a_planted_forbidden_string_is_detected(plant):
    assert hits("x = 1\n" + plant + "\n") != []


def test_A32_a_full_backfill_through_a_recording_playwright_sees_only_the_allowed_calls(tmp_path):
    from _browser_helpers import PWWorld, page_result
    from investos.backfill.runner import make_transport
    e = BfEnv(tmp_path, ids=["announcements"])
    one(e, earliest="2026-11-24")
    w = PWWorld()
    w.goto_final[COM + "/page/announcements"] = (200, IN + "/page/announcements")
    w.handler = lambda url, arg: page_result(e.world.handle(url), url=url, headers={"content-type": "application/json"})
    tr = make_transport(e.cfg, api_origin=IN, driver=w.driver(), reaper=e.reaper, clock=e.clock, monotonic=e.mono,
                        wall_clock=e.wall, sleeper=e.sleeper)
    res = e.run(browser=tr)
    assert res.status == "complete" and res.windows_done == 5, res
    assert w.touched == []
    assert len(w.launch_calls) == 1
    kw = w.launch_calls[0]
    assert set(kw) == {"user_data_dir", "channel", "headless", "no_viewport", "args", "timeout"}
    assert kw["args"] == ["--window-position=-2400,-2400", "--window-size=1280,800"]
    assert Path(kw["user_data_dir"]).name == "chrome-profile-backfill"
    assert len({expr for expr, _ in w.evaluates}) == 1 and "fetch(" in w.js()
    assert len(w.gotos) == 1


# ------------------------------------------------------------------ R25 known_at
def test_R25_no_backfill_module_computes_a_known_at():
    assert backfill_sources()
    for p in backfill_sources():
        for n, l in enumerate(p.read_text().splitlines(), 1):
            if "known_at" in l and "known_at_rule" not in l:
                pytest.fail(f"{p.name}:{n} mentions known_at: {l.strip()}")


def test_R25_the_raw_fetched_at_is_the_time_the_raw_was_seen_not_a_historic_stamp(tmp_path):
    e = BfEnv(tmp_path, ids=["announcements"])
    e.table_path.write_text(e.table_path.read_text().replace("2026-11-01", "2005-01-01"))
    e.write_probes(["announcements"], announcements={"earliest_date": "2005-01-03"})
    e.set_bf(ramp=(5,))
    e.run()
    ents = e.store.entries("backfill_announcements")
    assert ents and all(x.fetched_at.year >= 2026 for x in ents)


# ------------------------------------------------------------------ the CLI
@pytest.fixture
def cli(tmp_path, monkeypatch):
    e = BfEnv(tmp_path, ids=["announcements"])
    import investos.backfill.cli as bc
    monkeypatch.setattr(bc, "_load_config", lambda: e.cfg)
    monkeypatch.setattr(bc, "_now", lambda: e.now)
    monkeypatch.setattr(bc, "_make_browser", lambda cfg: e.tr())
    monkeypatch.setattr(bc, "_overrides", dict(sleeper=e.sleeper, monotonic=e.mono, wall_clock=e.wall,
                                              manifest=e.store, notifier=e.notifier, clock=e.clock))
    return e


def test_CLI_no_subcommand_or_an_unknown_one_prints_usage_and_exits_2():
    from investos.cli import main
    assert main(["backfill"]) == 2 and main(["backfill", "frobnicate"]) == 2
    assert main(["backfill", "run", "--bogus"]) == 2 and main(["backfill", "run", "--max-requests", "x"]) == 2


def test_CLI_run_without_a_probe_exits_0_and_makes_no_request(cli):
    code, out, err = cli.cli("backfill", "run")
    assert code == 0 and cli.driver.n_requests() == 0 and not cli.driver.launches


def test_CLI_run_with_max_requests_spends_exactly_that(cli):
    one(cli, earliest="2026-11-10")
    code, out, err = cli.cli("backfill", "run", "--max-requests", "3")
    assert code == 0 and cli.driver.n_requests() == 3


def test_CLI_run_returns_3_on_a_block(cli):
    one(cli, earliest="2026-11-10")
    cli.driver.fetch_hook = lambda n, u: status_result(403, u) if n == 2 else None
    code, out, err = cli.cli("backfill", "run")
    assert code == 3


def test_CLI_run_outside_the_window_exits_0_and_the_market_hours_ban_exits_1(cli):
    one(cli, earliest="2026-11-10")
    cli.at(ist(2026, 11, 29, 12, 0))
    assert cli.cli("backfill", "run")[0] == 0 and cli.driver.n_requests() == 0
    cli.at(TUESDAY_NOON)
    assert cli.cli("backfill", "run", "--outside-window")[0] == 1 and cli.driver.n_requests() == 0


def test_CLI_probe_confirms_an_endpoint_and_a_second_status_shows_it(cli):
    code, out, err = cli.cli("backfill", "probe")
    assert code == 0, err
    assert cli.probe("announcements") is not None and cli.probe("announcements").status == "confirmed"
    code, out, _ = cli.cli("backfill", "status")
    assert "confirmed" in out


def test_CLI_plan_prints_windows_and_makes_no_request(cli):
    one(cli, earliest="2026-11-24")
    code, out, _ = cli.cli("backfill", "plan")
    assert code == 0 and "5" in out and cli.driver.n_requests() == 0


def test_CLI_a_broken_endpoint_table_exits_1(cli):
    cli.table_path.write_text("garbage\n")
    code, out, err = cli.cli("backfill", "status")
    assert code == 1 and err


# ------------------------------------------------------------------ A40 smoke (env gated)
@pytest.mark.real_browser
@pytest.mark.skipif(os.environ.get("INVESTOS_BROWSER_SMOKE") != "1",
                    reason="set INVESTOS_BROWSER_SMOKE=1 to run the real-Chrome backfill smoke test (Mac only)")
def test_A40_real_chrome_announcements_one_day_window_five_days_back(tmp_path):
    pytest.importorskip("playwright.sync_api")
    import json
    import time
    from datetime import date, timedelta
    from _backfill_helpers import COM
    from investos.backfill.runner import make_transport
    e = BfEnv(tmp_path, ids=["announcements"])
    d = date.today() - timedelta(days=5)
    ep = e.ep("announcements")
    tr = make_transport(e.cfg, api_origin=COM, headless=False)
    t0 = time.monotonic()
    try:
        got = tr.fetch(ep.anchor_full) if hasattr(ep, "anchor_full") and not callable(ep.anchor_full) else tr.fetch(ep.anchor_full())
    finally:
        tr.close()
    print("elapsed", round(time.monotonic() - t0, 1))
    assert got.status == 200
    ua = got.request_headers.get("user-agent", "")
    assert ua.startswith("Mozilla/5.0") and "Chrome/" in ua and "HeadlessChrome" not in ua
    json.loads(got.body)


# ------------------------------------------------------------------ A41 no Playwright needed
def test_A41_the_backfill_package_and_check_import_without_playwright(tmp_path):
    code = ("import sys, importlib, pkgutil\n"
            "import investos.backfill as p\n"
            "for m in pkgutil.iter_modules(p.__path__):\n"
            "    importlib.import_module('investos.backfill.' + m.name)\n"
            "import investos.check.backfill_check\n"
            "assert not any(k == 'playwright' or k.startswith('playwright.') for k in sys.modules), 'playwright imported'\n")
    r = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True, cwd=str(tmp_path),
                       env={**os.environ, "PYTHONPATH": str(REPO / "src")})
    assert r.returncode == 0, r.stderr


def test_A41_no_backfill_module_imports_playwright_at_module_level():
    assert backfill_sources()
    for p in backfill_sources():
        for l in p.read_text().splitlines():
            assert not re.match(r"\s*(import|from)\s+playwright", l), (p.name, l)
