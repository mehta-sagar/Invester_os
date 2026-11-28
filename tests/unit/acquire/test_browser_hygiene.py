"""S1-T7: decision 0002a hygiene (R8a, R2), optional dependency (R15), launchd example (R18), smoke test shape (R19),
phase A table (R20), base untouched (A37). Grep tests over the source text; nothing here needs a browser."""
from __future__ import annotations

import ast
import inspect
import plistlib
import re
import shutil
import subprocess
import sys
import tomllib
from pathlib import Path

import pytest

from _rss_helpers import EXAMPLE_PLIST, EXAMPLE_TOML, REPO, SHIPPED_FEEDS, SRC

BROWSER_PY = SRC / "acquire" / "browser.py"

# R8(a) in full, plus the R2 list. Case-sensitive.
FORBIDDEN = ["stealth", "webdriver", "fake_useragent", "AutomationControlled", "undetected", "patchright",
             "rebrowser", "camoufox", "nodriver", "curl_cffi", "ja3", "impersonat", "userAgent", "user_agent",
             "extra_http_headers", "set_extra_http_headers", "add_init_script", "ignore_default_args",
             "new_cdp_session", "evaluate_on_new_document", "proxy", "socks", "jitter", "import random", "random.",
             "uniform(", ".mouse", ".keyboard", ".click(", ".dblclick(", ".fill(", ".type(", ".press(", ".hover(",
             ".tap(", ".dispatch_event(",
             "APIRequestContext", "page.request", "context.request", "playwright.request", "route.fetch",
             "route.fulfill", "page.route", "context.route"]
IDENTITY = ["stealth", "webdriver", "fake_useragent", "automationcontrolled", "undetected", "patchright", "rebrowser",
            "camoufox", "nodriver", "curl_cffi", "ja3", "impersonat", "useragent", "user_agent"]


def forbidden_hits(text: str, *, skip=()) -> list[str]:
    hits = [s for s in FORBIDDEN if s in text and s not in skip]
    low = text.lower()
    hits += [s for s in IDENTITY if s in low and s not in skip and s not in hits]
    return hits


def acquire_imports_of(path: Path) -> list[Path]:
    out = []
    for n in ast.walk(ast.parse(path.read_text())):
        mods = []
        if isinstance(n, ast.ImportFrom):
            base = ("." * n.level) + (n.module or "")
            mods = [base] + [f"{base}.{a.name}" for a in n.names]
        elif isinstance(n, ast.Import):
            mods = [a.name for a in n.names]
        for m in mods:
            if m.startswith("investos.acquire."):
                name = m.split(".")[2]
            elif m.startswith(".") and not m.startswith(".."):
                name = m.lstrip(".").split(".")[0]
            else:
                continue
            f = path.parent / f"{name}.py"
            if name and f.exists() and f != path and f not in out:
                out.append(f)
    return out


# ------------------------------------------------------------------ A15 (R8a)
def test_A15_the_transport_source_holds_none_of_the_forbidden_strings():
    assert BROWSER_PY.exists(), "src/investos/acquire/browser.py is missing"
    assert forbidden_hits(BROWSER_PY.read_text()) == []


def test_A15_imported_acquire_modules_hold_no_identity_or_input_api_string():
    assert BROWSER_PY.exists()
    mods = acquire_imports_of(BROWSER_PY)
    assert mods, "browser.py is expected to import from base (TransportError, BrowserResponse)"
    # base.py and manifest.py legitimately say 'proxy-authorization' / MappingProxyType: 'proxy' is checked in browser.py only
    for m in mods:
        assert forbidden_hits(m.read_text(), skip=("proxy",)) == [], m.name


@pytest.mark.planted
@pytest.mark.parametrize("plant", [
    "# stealth", "import random", "x = dict(ignore_default_args=['--x'])", "page.mouse.move(1, 2)",
    "launch(proxy={'server': 'x'})", "opts = {'userAgent': 'x'}", "ctx.add_init_script('x')", "page.request.get(u)",
    "# AutomationControlled", "page.route('**', h)", "time.sleep(random.uniform(1, 2))", "page.click('a')",
    "def f(): return 'WEBDRIVER'"])
def test_A15_each_planted_line_is_caught(plant):
    assert BROWSER_PY.exists()
    assert forbidden_hits(BROWSER_PY.read_text() + "\n" + plant + "\n") != []


def test_A15_only_browser_py_imports_playwright():
    assert BROWSER_PY.exists()
    offenders = []
    for f in sorted(SRC.rglob("*.py")):
        if f == BROWSER_PY:
            continue
        for n in ast.walk(ast.parse(f.read_text())):
            mods = [n.module or ""] if isinstance(n, ast.ImportFrom) else \
                [a.name for a in n.names] if isinstance(n, ast.Import) else []
            if any(m.split(".")[0] == "playwright" for m in mods):
                offenders.append(f.name)
    assert offenders == []


def test_A15_no_navigation_fetch_no_api_urls_one_anchor_navigation():
    s = BROWSER_PY.read_text()
    assert len(re.findall(r"\.goto\(", s)) == 1                        # the anchor of R4 and nothing else
    assert len(re.findall(r"\.evaluate\(", s)) == 1                    # the fixed fetch function of R3
    assert "/api/" not in s and "www.nseindia.com" not in s
    for f in (SRC / "acquire" / "rss.py", SRC / "cli.py"):
        assert "/api/" not in f.read_text(), f.name


# ------------------------------------------------------------------ A28 (R15)
def test_A28_everything_imports_with_playwright_blocked():
    code = (
        "import sys, importlib, pkgutil\n"
        "sys.modules['playwright'] = None; sys.modules['playwright.sync_api'] = None\n"
        f"sys.path.insert(0, {str(REPO / 'src')!r})\n"
        "import investos\n"
        "importlib.import_module('investos.acquire.browser')\n"
        "importlib.import_module('investos.acquire.rss')\n"
        "importlib.import_module('investos.cli')\n"
        "importlib.import_module('investos.check.rss_check')\n"
        "for m in pkgutil.walk_packages(investos.__path__, 'investos.'):\n"
        "    if not m.name.endswith('__main__'):\n"
        "        importlib.import_module(m.name)\n"
        "assert sys.modules['playwright'] is None\n")
    r = subprocess.run([sys.executable, "-I", "-c", code], capture_output=True, text=True, timeout=120)
    assert r.returncode == 0, r.stderr[-800:]


def test_A28_this_suite_imports_nothing_from_playwright_at_module_level():
    offenders = []
    for f in sorted((REPO / "tests").rglob("*.py")):
        for n in ast.parse(f.read_text()).body:
            mods = [n.module or ""] if isinstance(n, ast.ImportFrom) else \
                [a.name for a in n.names] if isinstance(n, ast.Import) else []
            if any(m.split(".")[0] == "playwright" for m in mods):
                offenders.append(str(f.relative_to(REPO)))
    assert offenders == []
    assert "playwright" not in sys.modules or sys.modules["playwright"] is None


def test_A28_pyproject_has_the_browser_extra_the_marker_and_unchanged_dependencies():
    d = tomllib.loads((REPO / "pyproject.toml").read_text())
    proj = d["project"]
    assert proj["dependencies"] == ["duckdb"]
    assert proj["optional-dependencies"]["dev"] == ["pytest"]
    extra = proj["optional-dependencies"]["browser"]
    assert len(extra) == 1 and extra[0].startswith("playwright")
    markers = d["tool"]["pytest"]["ini_options"]["markers"]
    assert any(m.startswith("real_browser") for m in markers) and any(m.startswith("planted") for m in markers)


# ------------------------------------------------------------------ example config (R13) and plist (A33)
def test_R13_example_config_documents_the_access_table_only_as_a_comment():
    text = EXAMPLE_TOML.read_text()
    d = tomllib.loads(text)
    assert dict(d.get("collect", {})).get("access") is None                    # still no live access table
    assert re.search(r"^\s*#\s*\[collect\.access\.rss_nse\]", text, re.M)
    assert re.search(r'^\s*#\s*mode\s*=\s*"real_browser"', text, re.M)
    assert re.search(r"^\s*#\s*daily_cap\s*=\s*2000\b", text, re.M)
    assert not re.search(r"^\s*decision\s*=", text, re.M)


@pytest.mark.planted
def test_A33_plist_asks_for_the_gui_session_and_explains_why():
    raw = EXAMPLE_PLIST.read_bytes()
    d = plistlib.loads(raw)
    assert d["Label"] == "com.investos.rss" and d["StartInterval"] == 900 and d["RunAtLoad"] is True
    assert d["LimitLoadToSessionType"] == "Aqua"
    assert d["ProgramArguments"][1:4] == ["collect", "rss", "--once"] and d["ProgramArguments"][0].endswith("investos")
    text = raw.decode()
    for needle in (r"GUI", r"session", r"investos\[browser\]", r"headless", r"sleep"):
        assert re.search(needle, text, re.I), needle


@pytest.mark.planted
def test_A33_a_plist_without_the_aqua_key_would_fail_the_check():
    d = plistlib.loads(EXAMPLE_PLIST.read_bytes())
    d.pop("LimitLoadToSessionType", None)
    assert d.get("LimitLoadToSessionType") != "Aqua"


# ------------------------------------------------------------------ A34 (R19): shape of the smoke test
def test_A34_the_smoke_test_is_marked_skipped_by_default_and_has_one_diagnostic_navigation():
    import test_browser_smoke as m
    fn = m.test_real_chrome_smoke
    marks = {mk.name: mk for mk in fn.pytestmark}
    assert "real_browser" in marks and "skipif" in marks
    assert "INVESTOS_BROWSER_SMOKE" in inspect.getsource(m)
    src = inspect.getsource(fn)
    assert 'importorskip("playwright' in src
    assert len(re.findall(r"\bpage\.goto\(", src)) == 1
    for bad in ('"data/', "'data/", "ManifestStore", "git ", "subprocess"):
        assert bad not in src, bad


# ------------------------------------------------------------------ A35 (R20): phase A leaves the NSE rows off
def shipped_rows():
    import csv
    with SHIPPED_FEEDS.open(newline="", encoding="utf-8") as fh:
        return list(csv.DictReader(fh))


def assert_phase_b(rows):
    """Phase B (Sagar, 28 Nov, after the live Mac check 25/25): all 25 rows on, the R3b note gone."""
    nse = [r for r in rows if r["url"].startswith("https://nsearchives.nseindia.com/")]
    assert len(rows) == 25 and len(nse) == 23
    assert all(r["enabled"] == "yes" and r["note"] == "" for r in nse)
    assert {r["feed_id"]: r["enabled"] for r in rows if r not in nse} == {"rbi_press": "yes", "sebi_press": "yes"}


def test_A35_phase_b_shipped_table_has_all_23_nse_rows_enabled():
    assert_phase_b(shipped_rows())


@pytest.mark.planted
def test_A35_a_disabled_nse_row_is_caught():
    rows = [dict(r) for r in shipped_rows()]
    next(r for r in rows if r["feed_id"] == "circulars")["enabled"] = "no"
    with pytest.raises(AssertionError):
        assert_phase_b(rows)


# ------------------------------------------------------------------ A37 (R1, R7, R14): the base is not touched
def test_A37_base_py_is_unchanged_against_main():
    if shutil.which("git") is None or not (REPO / ".git").exists():
        pytest.skip("not a git work tree")
    ref = subprocess.run(["git", "rev-parse", "--verify", "-q", "main"], cwd=REPO, capture_output=True)
    if ref.returncode != 0:
        pytest.skip("no main branch to compare with")
    r = subprocess.run(["git", "diff", "--quiet", "main", "--", "src/investos/acquire/base.py"], cwd=REPO)
    assert r.returncode == 0, "src/investos/acquire/base.py differs from main"


def test_A37_base_still_imports_no_browser_or_parser_library():
    tree = ast.parse((SRC / "acquire" / "base.py").read_text())
    names = {(n.module or "").split(".")[0] if isinstance(n, ast.ImportFrom) else a.name.split(".")[0]
             for n in ast.walk(tree) if isinstance(n, (ast.Import, ast.ImportFrom))
             for a in (n.names if isinstance(n, ast.Import) else [n])}
    assert not names & {"playwright", "selenium", "requests", "httpx", "xml", "lxml"}
