"""S1-T6: honest only, no circumvention (R23; A2), module hygiene (R6), shipped files (R17, R19)."""
from __future__ import annotations

import ast
import re
import tomllib

import pytest

from _rss_helpers import EXAMPLE_TOML, RSS_MODULES, SRC, REPO

FORBIDDEN = ("webdriver", "stealth", "fake_useragent", "Referer", "Cookie")
FORBIDDEN_IMPORTS = ("requests", "httpx", "selenium", "playwright")


def src_of(p):
    assert p.exists(), f"missing {p}"
    return p.read_text()


@pytest.mark.parametrize("p", RSS_MODULES, ids=lambda p: p.name)
def test_A2_module_has_no_forbidden_strings(p):
    s = src_of(p)
    for bad in FORBIDDEN:
        assert bad not in s, f"{p.name} contains {bad!r}"


@pytest.mark.planted
@pytest.mark.parametrize("p", RSS_MODULES, ids=lambda p: p.name)
def test_A2_module_imports_no_browser_or_third_party_http(p):
    tree = ast.parse(src_of(p))
    names = []
    for n in ast.walk(tree):
        if isinstance(n, ast.Import):
            names += [a.name.split(".")[0] for a in n.names]
        elif isinstance(n, ast.ImportFrom) and n.module and n.level == 0:
            names.append(n.module.split(".")[0])
    assert not set(names) & set(FORBIDDEN_IMPORTS), names


def test_R6_rss_py_imports_no_xml_module_itself():
    s = src_of(SRC / "acquire" / "rss.py")
    assert not re.search(r"^\s*(import|from)\s+(xml|lxml|defusedxml|pyexpat)", s, re.M)
    assert re.search(r"parse_feed", s)


def test_R6_rss_py_does_not_read_the_system_clock_in_the_cycle():
    s = src_of(SRC / "acquire" / "rss.py")
    assert not re.search(r"datetime\.(now|utcnow|today)\(", s) or "now=" in s       # clock is injected via `now`
    assert "time.sleep(" not in s or "sleeper" in s


def test_R17_example_config_lists_rbi_and_sebi_hosts():
    d = tomllib.loads(EXAMPLE_TOML.read_text())
    h = d["rate_limits"]["hosts"]
    assert h["www.rbi.org.in"] == 1.0 and h["www.sebi.gov.in"] == 1.0 and h["nsearchives.nseindia.com"] == 1.0
    assert "daily_cap" not in str(d.get("collect", {}))


def test_R1_rss_py_sets_no_header_of_its_own():
    s = src_of(SRC / "acquire" / "rss.py")
    assert not re.search(r"User-Agent|Accept-Encoding|headers\s*=\s*\{", s)


def test_R19_cli_registers_collect_in_the_usage_text():
    from investos.cli import USAGE
    assert "collect rss" in USAGE


def test_R20_check_cli_imports_the_rss_check():
    src = (SRC / "check" / "cli.py").read_text() + (SRC / "check" / "builtin.py").read_text()
    assert "rss_check" in src
