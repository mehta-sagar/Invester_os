"""S1-T6 step 6a: `investos collect rss` (R1 contact refusal, R12 exit 1, R18; A1 CLI, A25) and the launchd example (R19; A26)."""
from __future__ import annotations

import fcntl
import os
import plistlib
import re
import shutil

import pytest

from _collector_helpers import logs, resp  # noqa: F401
from _rss_helpers import (EXAMPLE_PLIST, NSE, SLOT_RE, URL_RBI, RssEnv, body_of, canon, env_tree, frow, log_text,
                          write_feeds, fmt_ts)

URL_A, URL_B = NSE + "a.xml", NSE + "b.xml"
ROWS = [frow("f1", URL_A, scope="always", stale="525600"), frow("f2", URL_B, scope="always", stale="525600"),
        frow("f3", URL_RBI, kind="news", publisher="RBI", scope="always", stale="525600")]


@pytest.fixture
def cli(tmp_path, monkeypatch):
    for k in list(os.environ):
        if k.startswith("INVESTOS_"):
            monkeypatch.delenv(k)
    e = RssEnv(tmp_path, ROWS, contact="ops@example.org", interval=0.001)
    monkeypatch.setenv("INVESTOS_ROOT", str(tmp_path))
    monkeypatch.setenv("INVESTOS_CONFIG", str(tmp_path / "config" / "investos.toml"))
    from investos.acquire import transport as tr
    e.built, e.gets = [], []
    e.status = {}
    monkeypatch.setattr(tr.UrllibTransport, "__init__",
                        lambda self, *a, **k: (e.built.append(1), setattr(self, "accept_encoding", "identity"))[0])

    def fake_get(self, url, *, headers, timeout):
        e.gets.append(url)
        st = e.status.get(url, 200)
        return resp(st, body_of(1) if st == 200 else b"nope", {}, url)
    monkeypatch.setattr(tr.UrllibTransport, "get", fake_get)
    return e


def run(*args):
    from investos.cli import main
    return main(list(args))


def everything(capsys, logs):
    cap = capsys.readouterr()
    return cap.out + cap.err + log_text(logs())


# ------------------------------------------------------------------ A25
@pytest.mark.parametrize("args", [(), ("--force",), ("--dry-run",), ("--bogus",)])
def test_A25_without_once_prints_usage_and_exits_2(cli, capsys, args):
    assert run("collect", "rss", *args) == 2
    out = capsys.readouterr()
    assert "collect rss" in out.out + out.err and cli.gets == []


def test_A25_collect_with_no_adapter_is_usage(cli, capsys):
    assert run("collect") == 2


def test_A25_once_exits_0_even_when_a_feed_failed_and_stores_the_others(cli):
    cli.status[URL_B] = 503
    assert run("collect", "rss", "--once", "--force") == 0
    assert sorted(set(cli.gets)) == sorted([URL_A, URL_B, URL_RBI])
    assert len(cli.rss_manifest()) == 2 and cli.state("f1")["last_ok_slot"] and cli.state("f2")["consecutive_failures"] == 1


def test_A25_once_at_an_unscheduled_or_scheduled_slot_exits_0(cli):
    assert run("collect", "rss", "--once") == 0


@pytest.mark.planted
def test_A25_bad_table_exits_2_with_no_http(cli, capsys):
    write_feeds(cli.feeds_path, [frow("f1", URL_A, stale="14")])
    assert run("collect", "rss", "--once", "--force") == 2
    assert cli.gets == [] and "stale_after_min" in (lambda c: c.out + c.err)(capsys.readouterr())
    assert cli.rss_manifest() == []


def test_A25_dry_run_prints_slot_and_due_feeds_and_writes_nothing(cli, capsys, logs):
    before = env_tree(cli)
    assert run("collect", "rss", "--once", "--force", "--dry-run") == 0
    out = capsys.readouterr().out
    assert re.search(SLOT_RE, out) and all(f in out for f in ("f1", "f2", "f3"))
    assert cli.gets == [] and env_tree(cli) == before


@pytest.mark.planted
def test_A25_unreadable_access_state_at_start_exits_1(cli):
    p = cli.source_file("rss")
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_bytes(b"{this is not json\n")
    assert run("collect", "rss", "--once", "--force") == 1
    assert cli.gets == []


# ------------------------------------------------------------------ A1 contact refusal
@pytest.mark.planted
@pytest.mark.parametrize("contact", [None, ""])
def test_A1_empty_contact_exits_1_before_touching_data_or_building_a_transport(tmp_path, monkeypatch, capsys, contact):
    for k in list(os.environ):
        if k.startswith("INVESTOS_"):
            monkeypatch.delenv(k)
    e = RssEnv(tmp_path, ROWS, contact=contact)
    monkeypatch.setenv("INVESTOS_ROOT", str(tmp_path))
    monkeypatch.setenv("INVESTOS_CONFIG", str(tmp_path / "config" / "investos.toml"))
    from investos.acquire import transport as tr
    built = []
    monkeypatch.setattr(tr.UrllibTransport, "__init__", lambda self, *a, **k: built.append(1))
    snap = sorted(str(p) for p in tmp_path.rglob("*"))
    before = env_tree(e)
    assert run("collect", "rss", "--once", "--force") == 1
    cap = capsys.readouterr()
    assert "collect.contact is not configured" in cap.out + cap.err
    assert built == [] and sorted(str(p) for p in tmp_path.rglob("*")) == snap and env_tree(e) == before


@pytest.mark.planted
def test_A1_placeholder_contact_is_refused_with_a_real_transport(tmp_path, monkeypatch, capsys):
    for k in list(os.environ):
        if k.startswith("INVESTOS_"):
            monkeypatch.delenv(k)
    e = RssEnv(tmp_path, ROWS, contact="CHANGE-ME@example.invalid")
    monkeypatch.setenv("INVESTOS_ROOT", str(tmp_path))
    monkeypatch.setenv("INVESTOS_CONFIG", str(tmp_path / "config" / "investos.toml"))
    from investos.acquire import transport as tr
    gets = []
    monkeypatch.setattr(tr.UrllibTransport, "get", lambda self, *a, **k: gets.append(1))
    assert run("collect", "rss", "--once", "--force") == 1
    cap = capsys.readouterr()
    assert "contact" in cap.out + cap.err and gets == [] and e.rss_manifest() == []


# ------------------------------------------------------------------ R12 / R11 exit codes
@pytest.mark.planted
def test_R12_a_failing_gap_writer_exits_1_after_the_other_feeds_were_polled(cli, capsys, logs):
    cli.write_state("f1", last_ok_slot="20200101T0900", last_ok_at=fmt_ts(__import__("datetime").datetime(2020, 1, 1, tzinfo=__import__("datetime").timezone.utc)))
    cli.gaps_path.parent.mkdir(parents=True, exist_ok=True)
    cli.gaps_path.write_bytes(b'{"schema":1,"feed_id":"zz"')
    assert run("collect", "rss", "--once", "--force") == 1
    assert URL_B in cli.gets and URL_RBI in cli.gets
    assert "gap_write_failed" in everything(capsys, logs)


@pytest.mark.planted
def test_R11_cycle_already_running_exits_0(cli, capsys, logs):
    write_feeds(cli.feeds_path, [ROWS[0]])
    cli.state_dir.mkdir(parents=True)
    fd = os.open(cli.state_dir / "f1.lock", os.O_RDWR | os.O_CREAT, 0o644)
    fcntl.flock(fd, fcntl.LOCK_EX)
    try:
        assert run("collect", "rss", "--once", "--force") == 0
    finally:
        os.close(fd)
    assert cli.gets == [] and "cycle already running" in everything(capsys, logs)


# ------------------------------------------------------------------ A26 plist
def validate_plist(path):
    text = path.read_text()
    with open(path, "rb") as fh:
        d = plistlib.load(fh)
    assert d["Label"] == "com.investos.rss"
    assert d["StartInterval"] == 900 and d["RunAtLoad"] is True
    args = d["ProgramArguments"]
    assert args[0].endswith("investos") and args[1:4] == ["collect", "rss", "--once"]
    for k in ("StandardOutPath", "StandardErrorPath"):
        assert "data/ledger/.state/" in d[k]
    low = text.lower()
    assert "zone" in low and "sleep" in low and "missed" in low
    return d


def test_A26_example_plist_is_valid():
    validate_plist(EXAMPLE_PLIST)


@pytest.mark.planted
@pytest.mark.parametrize("old,new", [
    ("<integer>900</integer>", "<integer>600</integer>"),
    ("com.investos.rss", "com.investos.rsx"),
    ("<string>--once</string>", "<string>--loop</string>"),
    ("<true/>", "<false/>"),
], ids=["interval", "label", "once", "runatload"])
def test_A26_a_broken_plist_is_caught_by_the_validator(tmp_path, old, new):
    p = tmp_path / "x.plist"
    t = EXAMPLE_PLIST.read_text()
    assert old in t
    p.write_text(t.replace(old, new, 1))
    with pytest.raises((AssertionError, KeyError)):
        validate_plist(p)


def test_A26_plist_never_commits_or_pushes():
    t = EXAMPLE_PLIST.read_text().lower()
    assert "git" not in t.replace("digit", "")
