"""S1-T1: `investos access status|clear` (R26) and the contact refusal at the CLI factory (R20; A46 CLI, A58)."""
from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone

import pytest

from _s1_helpers import SOURCE, HOST, S1Env, append_line, demo_class, event_line, write_decision


@pytest.fixture
def repo(tmp_path, monkeypatch):
    e = S1Env(tmp_path)                                    # writes config/investos.toml + zone dirs
    for k in list(__import__("os").environ):
        if k.startswith("INVESTOS_"):
            monkeypatch.delenv(k)
    monkeypatch.setenv("INVESTOS_ROOT", str(tmp_path))
    monkeypatch.setenv("INVESTOS_CONFIG", str(tmp_path / "config" / "investos.toml"))
    return e


def run(*args):
    from investos.cli import main
    return main(list(args))


def plant_real_halt(e, n_blocks=7):
    now = datetime.now(timezone.utc)
    for i in range(n_blocks):
        ts = now - timedelta(seconds=n_blocks - i + 10)
        append_line(e.host_file(), event_line("blocked", SOURCE, HOST, ts, detail=f"marker-{i + 1}"))
    append_line(e.source_file(), event_line("halted", SOURCE, None, now - timedelta(seconds=5), detail="3 blocked days in 7"))


@pytest.mark.planted
def test_A46_status_shows_flags_and_only_the_last_five_events(repo, capsys):
    plant_real_halt(repo)
    assert run("access", "status") == 0
    out = capsys.readouterr().out
    assert SOURCE in out and "halted" in out.lower()
    assert "marker-7" in out and "marker-1" not in out and "marker-2" not in out


def test_A46_status_for_one_source_and_clean_state(repo, capsys):
    assert run("access", "status", SOURCE) == 0
    out = capsys.readouterr().out
    assert "halted" not in out.lower() or "false" in out.lower() or "no" in out.lower()


@pytest.mark.planted
def test_A46_clear_exit_codes_and_effect(repo, capsys):
    plant_real_halt(repo, 3)
    write_decision(repo.tmp, "0004-clear.md", f"Clears-halt: {SOURCE}")
    assert run("access", "clear", SOURCE, "--decision", "0004") == 0
    last = repo.source_events()[-1]
    assert last["event"] == "cleared" and last["decision"] == "0004"
    before = repo.source_file().read_bytes()
    assert run("access", "clear", SOURCE, "--decision", "0004") == 1                  # no longer halted: refused
    assert repo.source_file().read_bytes() == before


@pytest.mark.planted
def test_A46_clear_refused_without_matching_decision_file(repo, capsys):
    plant_real_halt(repo, 3)
    before = repo.source_file().read_bytes()
    assert run("access", "clear", SOURCE, "--decision", "0009") == 1
    assert repo.source_file().read_bytes() == before


@pytest.mark.parametrize("args", [["access"], ["access", "bogus"], ["access", "clear"], ["access", "clear", SOURCE],
                                  ["access", "clear", SOURCE, "--decision"]])
def test_A46_usage_errors_exit_2(repo, capsys, args):
    assert run(*args) == 2


@pytest.mark.planted
def test_A46_status_on_unreadable_state_does_not_crash_and_is_not_ok(repo, capsys):
    p = repo.host_file()
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_bytes(b"{garbage}\n")
    assert run("access", "status") in (1, 2)
    assert "Traceback" not in capsys.readouterr().err


# ------------------------------------------------------------------ A58 (R20)
@pytest.mark.planted
@pytest.mark.parametrize("contact", [None, ""])
def test_A58_collector_refuses_to_start_without_contact(tmp_path, contact):
    e = S1Env(tmp_path)
    with pytest.raises(ValueError, match="collect.contact is not configured"):
        e.make(cfg=e.cfg_contact(contact))


@pytest.mark.planted
@pytest.mark.parametrize("contact", [None, ""])
def test_A58_empty_contact_in_the_toml_also_refused(tmp_path, contact):
    e = S1Env(tmp_path, contact=contact)
    with pytest.raises(ValueError, match="collect.contact is not configured"):
        e.make()


@pytest.mark.planted
@pytest.mark.parametrize("contact", [None, ""])
def test_A58_cli_factory_refuses_before_touching_data_or_building_a_transport(tmp_path, capsys, monkeypatch, contact):
    """Factory location/signature is an open question (see checkpoint): `investos.acquire.access_cli.build_collector(cfg, cls)`.
    Either a ValueError or a SystemExit(1) carrying the message is accepted."""
    from investos.acquire import transport as tr
    from investos.acquire.access_cli import build_collector

    e = S1Env(tmp_path, contact=contact)
    built = []
    monkeypatch.setattr(tr.UrllibTransport, "__init__", lambda self, *a, **k: built.append(1))
    snap = sorted(str(p) for p in tmp_path.rglob("*"))
    with pytest.raises((ValueError, SystemExit)) as ei:
        build_collector(e.cfg, demo_class())
    if isinstance(ei.value, SystemExit):
        assert ei.value.code == 1
        cap = capsys.readouterr()
        assert "collect.contact is not configured" in cap.out + cap.err
    else:
        assert "collect.contact is not configured" in str(ei.value)
    assert built == [] and sorted(str(p) for p in tmp_path.rglob("*")) == snap
