"""S1-T1: injectable Notifier (spec R33, A57). osascript is never really invoked: subprocess.run is always replaced."""
from __future__ import annotations

import io
import json
import re
import subprocess
import sys

import pytest


@pytest.fixture
def logbuf():
    from investos.core import log
    saved = dict(log._state)
    buf = io.StringIO()
    log.configure_logging(buf, level="DEBUG", run_id="notify-run-0001")

    def records():
        return [json.loads(l) for l in buf.getvalue().splitlines() if l.strip()]
    yield records
    log._state.update(saved)


class Rec:
    def __init__(self, result=None, raises=None):
        self.calls = []
        self.result = result
        self.raises = raises

    def __call__(self, *a, **k):
        self.calls.append((a, k))
        if self.raises:
            raise self.raises
        return self.result or subprocess.CompletedProcess(a[0], 0, b"", b"")


@pytest.fixture
def guard(monkeypatch):
    """Whatever the test does, a real subprocess is never started."""
    def boom(*a, **k):
        raise AssertionError("real subprocess.run reached")
    monkeypatch.setattr(subprocess, "run", boom)


def test_null_notifier_does_nothing(guard):
    from investos.core.notify import NullNotifier
    assert NullNotifier().notify("t", "m") is None


def test_notifier_protocol_exists():
    from investos.core.notify import Notifier, NullNotifier, OsascriptNotifier
    assert callable(NullNotifier().notify) and callable(OsascriptNotifier().notify) and Notifier is not None


@pytest.mark.planted
def test_A57_darwin_issues_one_argument_list_call_with_timeout(monkeypatch):
    from investos.core.notify import OsascriptNotifier
    rec = Rec()
    monkeypatch.setattr(subprocess, "run", rec)
    monkeypatch.setattr(sys, "platform", "darwin")
    OsascriptNotifier().notify("Invest OS: source halted", "src halted: 3 blocked days in 7 (host h)")
    assert len(rec.calls) == 1
    (args, kw) = rec.calls[0]
    cmd = args[0] if args else kw["args"]
    assert isinstance(cmd, list) and cmd[0] == "osascript" and cmd[1] == "-e" and len(cmd) == 3
    assert not kw.get("shell")
    assert kw.get("timeout") == 5
    assert "Invest OS: source halted" in cmd[2] and "src halted: 3 blocked days in 7 (host h)" in cmd[2]


@pytest.mark.planted
def test_A57_quotes_backslashes_and_control_characters_are_neutralised(monkeypatch):
    from investos.core.notify import OsascriptNotifier
    rec = Rec()
    monkeypatch.setattr(subprocess, "run", rec)
    monkeypatch.setattr(sys, "platform", "darwin")
    OsascriptNotifier().notify('ti"tle\\x\nT', 'me"ssage\\y\r\nline2\x07\x00end')
    script = (rec.calls[0][0][0])[2]
    assert not re.search(r"[\x00-\x1f\x7f]", script)
    prefix, sep = 'display notification "', '" with title "'
    assert script.startswith(prefix) and sep in script and script.endswith('"')
    msg_part, title_part = script[len(prefix):].split(sep, 1)
    title_part = title_part[:-1]
    for part in (msg_part, title_part):
        assert not re.search(r'(?<!\\)"', part), part                              # every embedded quote is escaped
    assert "\\\\y" in msg_part and "\\\\x" in title_part                             # backslash doubled
    assert 'me\\"ssage' in msg_part and 'ti\\"tle' in title_part


@pytest.mark.planted
def test_A57_no_call_off_darwin(monkeypatch):
    from investos.core.notify import OsascriptNotifier
    rec = Rec()
    monkeypatch.setattr(subprocess, "run", rec)
    monkeypatch.setattr(sys, "platform", "linux")
    OsascriptNotifier().notify("t", "m")
    assert rec.calls == []


@pytest.mark.planted
@pytest.mark.parametrize("kind", ["oserror", "exit1", "timeout"])
def test_A57_failures_are_swallowed_and_logged(monkeypatch, logbuf, kind):
    from investos.core.notify import OsascriptNotifier
    if kind == "oserror":
        rec = Rec(raises=OSError("no osascript"))
    elif kind == "timeout":
        rec = Rec(raises=subprocess.TimeoutExpired("osascript", 5))
    else:
        rec = Rec(result=subprocess.CompletedProcess(["osascript"], 1, b"", b"err"))
    monkeypatch.setattr(subprocess, "run", rec)
    monkeypatch.setattr(sys, "platform", "darwin")
    assert OsascriptNotifier().notify("t", "m") is None                              # nothing raised
    w = [r for r in logbuf() if r["event"] == "notify_failed" and r["level"] == "WARNING"]
    assert len(w) == 1


def test_A57_success_logs_no_warning(monkeypatch, logbuf):
    from investos.core.notify import OsascriptNotifier
    monkeypatch.setattr(subprocess, "run", Rec())
    monkeypatch.setattr(sys, "platform", "darwin")
    OsascriptNotifier().notify("t", "m")
    assert not [r for r in logbuf() if r["event"] == "notify_failed"]


@pytest.mark.planted
def test_A57_default_collector_notifier_is_the_null_one():
    """Source-level guard: the Collector default is NullNotifier and nothing in acquire/ builds an Osascript one."""
    from pathlib import Path
    src = Path(__file__).resolve().parents[3] / "src" / "investos" / "acquire"
    text = "\n".join(p.read_text() for p in sorted(src.glob("*.py")) if p.name != "access_cli.py")
    assert "NullNotifier" in text and "OsascriptNotifier" not in text
