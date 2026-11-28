"""S1-T8: the `[backfill]` config section, the example config and the additive browser amendments (C1-C3)."""
from __future__ import annotations

import re
from dataclasses import replace

import pytest

from _backfill_helpers import (BfEnv, COM, EXAMPLE_TOML, IDS, IN, SRC, THIRD, fetch_ok, day, html_result, status_result)


def load_with(tmp_path, block: str):
    from investos.core.config import load_config
    e = BfEnv(tmp_path)
    text = e.toml_path.read_text()
    text = text[:text.index("[backfill]")] + block
    p = tmp_path / "config" / "alt.toml"
    p.write_text(text)
    return load_config(p, repo_root=tmp_path, env={})


def test_cfg_defaults_when_the_section_is_absent(tmp_path):
    from investos.core.config import load_config
    e = BfEnv(tmp_path)
    text = e.toml_path.read_text()
    p = tmp_path / "config" / "alt.toml"
    p.write_text(text[:text.index("[backfill]")])
    b = load_config(p, repo_root=tmp_path, env={}).backfill
    assert tuple(b.ramp) == (4000, 8000)                       # Sagar's answer 1: night 1 = 4000, then 8000
    assert (b.window_start, b.window_end, b.batch_windows, b.audit_every) == ("00:30", "06:30", 25, 50)
    assert b.profile_name == "chrome-profile-backfill"


def test_cfg_values_are_read(tmp_path):
    b = load_with(tmp_path, '[backfill]\nramp = [10, 20]\nwindow_start = "01:00"\nwindow_end = "05:15"\n'
                            'batch_windows = 3\naudit_every = 7\nprofile_name = "p2"\n').backfill
    assert tuple(b.ramp) == (10, 20) and b.window_start == "01:00" and b.window_end == "05:15"
    assert b.batch_windows == 3 and b.audit_every == 7 and b.profile_name == "p2"


@pytest.mark.parametrize("block", [
    '[backfill]\nnightly_budgetx = 5\n', '[backfill]\nramp = []\n', '[backfill]\nramp = [0, 5]\n',
    '[backfill]\nramp = 4000\n', '[backfill]\nramp = ["a"]\n', '[backfill]\nwindow_start = "25:00"\n',
    '[backfill]\nwindow_end = "6:30"\n', '[backfill]\nbatch_windows = 0\n', '[backfill]\naudit_every = -1\n',
    '[backfill]\nprofile_name = "../x"\n', '[backfill]\nbatch_windows = true\n', '[backfill]\nprofile_name = 5\n'])
def test_cfg_strict_bad_values_raise(tmp_path, block):
    from investos.core.config import ConfigError
    with pytest.raises(ConfigError):
        load_with(tmp_path, block)


def test_cfg_example_file_carries_only_commented_backfill_blocks():
    text = EXAMPLE_TOML.read_text()
    assert "[backfill]" in text and "ramp" in text
    live = [l for l in text.splitlines() if l.strip() and not l.lstrip().startswith("#")]
    assert not any("backfill" in l for l in live)
    for src in IDS + ["probe"]:
        assert f"[collect.access.backfill_{src}]" in text, src
    assert "# [backfill]" in text or "#[backfill]" in text


def test_cfg_example_file_still_loads(tmp_path):
    from investos.core.config import load_config
    for z in ("data/raw", "data/manifests", "data/canonical", "data/ledger", "reports", "registry", "db/migrations"):
        (tmp_path / z).mkdir(parents=True, exist_ok=True)
    (tmp_path / "pyproject.toml").write_text("")
    p = tmp_path / "investos.toml"
    p.write_text(EXAMPLE_TOML.read_text())
    cfg = load_config(p, repo_root=tmp_path, env={})
    assert tuple(cfg.backfill.ramp) == (4000, 8000)
    assert not any(s.startswith("backfill_") for s in cfg.collect.access)


# ------------------------------------------------------------------ C1-C3 (browser amendments)
def test_C1_browser_hosts_constant_and_rss_routing_are_unchanged():
    from investos.acquire.browser import BROWSER_HOSTS
    assert BROWSER_HOSTS == frozenset({"nsearchives.nseindia.com"})        # the existing browser test pins this (see RED_TESTS Q1)
    text = (SRC / "acquire" / "browser.py").read_text()
    assert "www.nseindia.com" not in text and "www.nseindia.in" not in text


def test_C1_make_transport_allows_both_www_hosts_and_refuses_nsearchives(tmp_path):
    e = BfEnv(tmp_path)
    t = e.tr()
    with pytest.raises(ValueError):
        t.fetch("https://nsearchives.nseindia.com/content/RSS/Circulars.xml")
    with pytest.raises(ValueError):
        t.fetch("https://www.example.org/api/x")
    r = t.fetch(IN + "/api/announcements?from_date=01-11-2026&to_date=01-11-2026")
    assert r.status == 200
    assert len(e.driver.launches) == 1


def test_C2_rss_collector_routing_never_reaches_the_new_hosts():
    from investos.acquire import rss
    from investos.acquire.rss import collector_class_for
    row = rss.FeedRow if hasattr(rss, "FeedRow") else None
    assert row is not None
    f = row("x", "https://www.nseindia.com/content/RSS/a.xml", "news", "NSE", True, 90, "always", "confirmed", "")
    assert collector_class_for(f).access_mode.value == "honest"


def test_C2_profile_name_selects_profile_dir_and_lock(tmp_path):
    e = BfEnv(tmp_path)
    e.tr().fetch(IN + "/api/announcements?from_date=01-11-2026&to_date=01-11-2026")
    assert e.driver.launches[0]["profile_dir"] == e.ledger_dir / ".state" / "chrome-profile-backfill"
    from _browser_helpers import lock_is_held
    assert lock_is_held(e.ledger_dir / ".state" / "chrome-profile-backfill.lock")
    assert not lock_is_held(e.ledger_dir / ".state" / "chrome-profile.lock")


def test_C2_default_profile_name_is_unchanged_for_the_rss_poller(tmp_path):
    from investos.acquire.browser import RealBrowserTransport
    e = BfEnv(tmp_path)
    tr = RealBrowserTransport(e.cfg, driver=e.driver, reaper=e.reaper, clock=e.clock, monotonic=e.mono,
                              wall_clock=e.wall, sleeper=e.sleeper)
    from investos.acquire.browser import ANCHOR_PATHS
    e.driver.fetch_hook = lambda n, u: fetch_ok(b"<rss/>", url=u)
    tr.fetch("https://nsearchives.nseindia.com/content/RSS/F01.xml")
    assert e.driver.launches[0]["profile_dir"] == e.ledger_dir / ".state" / "chrome-profile"
    assert e.driver.anchors == ["https://nsearchives.nseindia.com" + ANCHOR_PATHS["nsearchives.nseindia.com"]]


def test_C2_anchor_for_maps_a_data_url_to_its_page(tmp_path):
    e = BfEnv(tmp_path)
    from investos.acquire.browser import RealBrowserTransport
    tr = RealBrowserTransport(e.cfg, driver=e.driver, reaper=e.reaper, clock=e.clock, monotonic=e.mono,
                              wall_clock=e.wall, sleeper=e.sleeper,
                              allowed_hosts=frozenset({"www.nseindia.com", "www.nseindia.in"}),
                              anchor_for=lambda u: COM + "/page/pit", origin_aliases={COM: IN})
    tr.fetch(IN + "/api/pit?from_date=01-11-2026&to_date=01-11-2026")
    assert e.driver.anchors == [COM + "/page/pit"]


def test_C2_alias_is_accepted_but_any_other_origin_change_is_an_anchor_error(tmp_path):
    from investos.acquire.base import TransportError
    e = BfEnv(tmp_path)
    url = IN + "/api/announcements?from_date=01-11-2026&to_date=01-11-2026"
    e.world.fixed_final_origin = THIRD
    tr = e.tr()
    with pytest.raises(TransportError, match="anchor left origin"):
        tr.fetch(url)
    assert e.driver.fetched == []                                               # nothing was fetched on a foreign origin
    e2 = BfEnv(tmp_path / "b")
    e2.world.fixed_final_origin = None
    e2.tr().fetch(IN + "/api/announcements?from_date=01-11-2026&to_date=01-11-2026")
    assert e2.driver.anchors and e2.driver.fetched


def test_C2_without_an_alias_the_com_to_in_redirect_is_refused(tmp_path):
    from investos.acquire.base import TransportError
    from investos.acquire.browser import RealBrowserTransport
    e = BfEnv(tmp_path)
    tr = RealBrowserTransport(e.cfg, driver=e.driver, reaper=e.reaper, clock=e.clock, monotonic=e.mono,
                              wall_clock=e.wall, sleeper=e.sleeper,
                              allowed_hosts=frozenset({"www.nseindia.com", "www.nseindia.in"}),
                              anchor_for=lambda u: COM + "/page/announcements")
    with pytest.raises(TransportError, match="anchor left origin"):
        tr.fetch(IN + "/api/announcements?from_date=01-11-2026&to_date=01-11-2026")


def test_C3_set_anchor_makes_the_next_fetch_navigate_even_when_the_origin_matches(tmp_path):
    e = BfEnv(tmp_path)
    t = e.tr()
    u = IN + "/api/announcements?from_date={d}&to_date={d}"
    t.fetch(u.format(d="01-11-2026"))
    t.fetch(u.format(d="02-11-2026"))
    assert len(e.driver.anchors) == 1                                           # same origin: no second anchor
    t.set_anchor(COM + "/page/pit")
    t.fetch(u.format(d="03-11-2026"))
    t.fetch(u.format(d="04-11-2026"))
    assert e.driver.anchors[1:] == [COM + "/page/pit"] and len(e.driver.anchors) == 2
    assert len(e.driver.launches) == 1                                          # still one Chrome, one page


def test_C3_the_anchor_after_set_anchor_goes_through_the_limiter(tmp_path):
    e = BfEnv(tmp_path)
    t = e.tr()
    u = IN + "/api/announcements?from_date={d}&to_date={d}"
    t.fetch(u.format(d="01-11-2026"))
    t.set_anchor(COM + "/page/pit")
    t.fetch(u.format(d="02-11-2026"))
    starts = e.driver.starts()
    assert all(b - a >= 2.5 - 1e-9 for a, b in zip(starts, starts[1:])), starts


def test_C3_a_blocked_anchor_after_set_anchor_comes_back_as_a_block_response(tmp_path):
    e = BfEnv(tmp_path)
    t = e.tr()
    u = IN + "/api/announcements?from_date={d}&to_date={d}"
    t.fetch(u.format(d="01-11-2026"))
    e.world.anchor_status_for[COM + "/page/pit"] = 403
    t.set_anchor(COM + "/page/pit")
    r = t.fetch(u.format(d="02-11-2026"))
    assert r.status == 403
    assert len(e.driver.fetched) == 1                                           # the data request never went out


def test_C3_fetch_still_sends_only_the_three_key_init_and_no_headers(tmp_path):
    from _browser_helpers import PWWorld
    w = PWWorld()
    from investos.acquire.browser import RealBrowserTransport
    e = BfEnv(tmp_path)
    tr = RealBrowserTransport(e.cfg, driver=w.driver(), reaper=e.reaper, clock=e.clock, monotonic=e.mono,
                              wall_clock=e.wall, sleeper=e.sleeper,
                              allowed_hosts=frozenset({"www.nseindia.com", "www.nseindia.in"}),
                              origin_aliases={COM: IN}, anchor_for=lambda u: COM + "/page/x", profile_name="pb")
    w.goto_final[COM + "/page/x"] = (200, IN + "/page/x")
    w.bodies[IN + "/api/x?a=1"] = b"[]"
    tr.set_anchor(COM + "/page/x")
    tr.fetch(IN + "/api/x?a=1")
    assert w.touched == [] and len(w.evaluates) == 1
