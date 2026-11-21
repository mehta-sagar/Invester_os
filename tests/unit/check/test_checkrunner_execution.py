"""S0-T5 R3/R4/R5 and edge cases: execution, results, exit codes, report, usage/config errors."""
from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

from _chk_helpers import (by_name, canonical_dump, fail_check, last_line, ok_check, read_report,
                          report_path, rows)

REPO = Path(__file__).resolve().parents[3]


@pytest.mark.planted
def test_A3_exception_and_bad_return_become_error_and_rest_still_run(reg, run, cfg, run_id):
    calls = []

    def boom(ctx):
        calls.append("boom")
        raise ValueError("boom")

    def badret(ctx):
        calls.append("badret")
        return True

    reg.check("boom")(boom)
    reg.check("badret")(badret)
    ok_check(reg, "third", calls)
    code, out = run([], reg)
    assert code == 1
    assert calls == ["boom", "badret", "third"]
    t = rows(out)
    assert t["boom"][0] == "error" and "ValueError: boom" in t["boom"][1]
    assert t["badret"][0] == "error" and "bool" in t["badret"][1]
    assert t["third"][0] == "pass"
    rep = by_name(read_report(cfg, run_id))
    assert rep["boom"]["status"] == "error" and rep["boom"]["message"].startswith("ValueError: boom")
    assert "bool" in rep["badret"]["message"]
    assert rep["third"]["status"] == "pass"


def test_A3_each_check_called_exactly_once(reg, run):
    calls = []
    ok_check(reg, "a", calls)
    fail_check(reg, "b", calls)
    run([], reg)
    assert calls == ["a", "b"]


def test_A3_error_message_truncated_to_200(reg, run, cfg, run_id):
    def big(ctx):
        raise RuntimeError("x" * 500)
    reg.check("big")(big)
    run([], reg)
    msg = by_name(read_report(cfg, run_id))["big"]["message"]
    assert msg.startswith("RuntimeError: ") and len(msg) <= 200


def test_A3_fail_status_when_ok_false(reg, run, cfg, run_id):
    fail_check(reg, "f", message="nope")
    code, out = run([], reg)
    assert code == 1 and rows(out)["f"][0] == "fail"
    assert by_name(read_report(cfg, run_id))["f"]["status"] == "fail"


def test_A4_all_pass_exit_0_and_last_line_OK(reg, run):
    ok_check(reg, "a")
    ok_check(reg, "b")
    code, out = run([], reg)
    assert code == 0
    assert last_line(out) == "OK"


def test_A4_one_fail_exit_1_and_FAILED_1(reg, run):
    ok_check(reg, "a")
    fail_check(reg, "b")
    code, out = run([], reg)
    assert code == 1
    assert last_line(out) == "FAILED (1)"


def test_A4_failed_count_includes_errors(reg, run):
    fail_check(reg, "a")
    reg.check("b")(lambda ctx: 1 / 0)
    ok_check(reg, "c")
    code, out = run([], reg)
    assert code == 1 and last_line(out) == "FAILED (2)"


def test_A4_empty_registry_exit_2(reg, run):
    code, out = run([], reg)
    assert code == 2
    assert "no checks registered" in out


def test_A5_report_written_with_schema(reg, run, cfg, run_id):
    ok_check(reg, "a")
    fail_check(reg, "b", message="bad", details=("d1", "d2"))
    code, out = run([], reg)
    assert code == 1
    p = report_path(cfg, run_id)
    assert p.exists()
    text = p.read_text(encoding="utf-8")

    def nofloat(s):
        raise AssertionError(f"float in report: {s}")
    rep = json.loads(text, parse_float=nofloat)
    assert text == canonical_dump(rep)
    assert rep["schema"] == 1 and rep["mode"] == "check" and rep["ok"] is False
    assert rep["run_id"] == run_id
    assert rep["selected"] == ["a", "b"]
    assert [r["name"] for r in rep["results"]] == ["a", "b"]
    for r in rep["results"]:
        assert isinstance(r["duration_ms"], int) and not isinstance(r["duration_ms"], bool)
        assert r["plant"] in ("registered", "missing")
    assert rep["results"][1]["details"] == ["d1", "d2"]
    for k in ("started_at", "finished_at"):
        assert rep[k].endswith("Z") and "T" in rep[k]


def test_A5_report_ok_true_when_all_pass(reg, run, cfg, run_id):
    ok_check(reg, "a")
    run([], reg)
    assert read_report(cfg, run_id)["ok"] is True


def test_A5_report_written_even_when_empty_details_and_dir_created(reg, run, cfg, run_id):
    ok_check(reg, "a")
    assert not (cfg.zones["reports"] / "checks").exists()
    run([], reg)
    assert (cfg.zones["reports"] / "checks").is_dir()


def test_A5_second_run_same_run_id_overwrites(reg, run, cfg, run_id):
    ok_check(reg, "a")
    run([], reg)
    reg2 = type(reg)()
    fail_check(reg2, "z")
    run([], reg2)
    files = list((cfg.zones["reports"] / "checks").iterdir())
    assert [f.name for f in files] == [f"{run_id}.json"]            # no stray temp file
    assert read_report(cfg, run_id)["selected"] == ["z"]


@pytest.mark.planted
def test_A6_details_capped_at_50(reg, run, cfg, run_id):
    fail_check(reg, "many", details=[f"line {i}" for i in range(80)])
    run([], reg)
    d = by_name(read_report(cfg, run_id))["many"]["details"]
    assert len(d) == 50
    assert d[:49] == [f"line {i}" for i in range(49)]
    assert d[-1] == "... and 31 more"


def test_A6_details_exactly_50_not_truncated(reg, run, cfg, run_id):
    fail_check(reg, "fifty", details=[f"l{i}" for i in range(50)])
    run([], reg)
    d = by_name(read_report(cfg, run_id))["fifty"]["details"]
    assert d == [f"l{i}" for i in range(50)]


@pytest.mark.planted
def test_A6_reports_zone_removed_exit_2(reg, run, cfg):
    import shutil
    ok_check(reg, "a")
    shutil.rmtree(cfg.zones["reports"])
    code, out = run([], reg)
    assert code == 2


def test_A19_newline_in_message_replaced_by_space(reg, run, cfg, run_id):
    from investos.check.registry import CheckResult
    reg.check("nl")(lambda ctx: CheckResult(False, "first\nsecond"))
    code, out = run([], reg)
    assert code == 1
    assert "first second" in out
    assert len([ln for ln in out.splitlines() if ln.startswith("nl")]) == 1
    assert by_name(read_report(cfg, run_id))["nl"]["message"] == "first second"


def test_A19_unicode_message_written_utf8(reg, run, cfg, run_id):
    from investos.check.registry import CheckResult
    reg.check("uni")(lambda ctx: CheckResult(True, "rupee ₹ ok"))
    run([], reg)
    raw = report_path(cfg, run_id).read_bytes()
    assert "rupee ₹ ok".encode("utf-8") in raw          # ensure_ascii=False


@pytest.mark.skipif(os.geteuid() == 0, reason="root ignores directory modes")
def test_A19_reports_readonly_exit_2_table_still_printed(reg, run, cfg):
    ok_check(reg, "a")
    rz = cfg.zones["reports"]
    os.chmod(rz, 0o555)
    try:
        code, out = run([], reg)
    finally:
        os.chmod(rz, 0o755)
    assert code == 2
    assert "NAME" in out and "a" in rows(out)


def test_config_error_exit_2(reg, run_id, tmp_path, monkeypatch):
    import io
    from investos.check.cli import main
    for k in list(os.environ):
        if k.startswith("INVESTOS_"):
            monkeypatch.delenv(k)
    monkeypatch.setenv("INVESTOS_CONFIG", str(tmp_path / "nonexistent.toml"))
    ok_check(reg, "a")
    buf = io.StringIO()
    assert main([], registry=reg, stdout=buf) == 2        # cfg=None -> load_config() -> ConfigError


def test_A20_python_dash_m_subprocess(healthy, run_id):
    cfgfile = healthy.repo_root / "config" / "investos.toml"
    env = {k: v for k, v in os.environ.items() if not k.startswith("INVESTOS_")}
    env.update(INVESTOS_CONFIG=str(cfgfile), INVESTOS_ROOT=str(healthy.repo_root),
               PYTHONPATH=str(REPO / "src"))
    proc = subprocess.run([sys.executable, "-m", "investos.check", "manifest"], env=env,
                          cwd=str(healthy.repo_root), capture_output=True, text=True, timeout=120)
    assert proc.returncode == 0, proc.stdout + proc.stderr
    assert "manifest" in proc.stdout
    files = list((healthy.zones["reports"] / "checks").glob("*.json"))
    assert len(files) == 1
    rep = json.loads(files[0].read_text(encoding="utf-8"))
    assert rep["selected"] == ["manifest"] and rep["ok"] is True


@pytest.mark.planted
def test_A20_subprocess_exit_code_matches_failure(healthy):
    from _chk_helpers import raw_path, tamper
    tamper(raw_path(healthy, "bse", b"alpha-bytes"), b"ALPHA-bytes")      # same size, different bytes
    cfgfile = healthy.repo_root / "config" / "investos.toml"
    env = {k: v for k, v in os.environ.items() if not k.startswith("INVESTOS_")}
    env.update(INVESTOS_CONFIG=str(cfgfile), INVESTOS_ROOT=str(healthy.repo_root),
               PYTHONPATH=str(REPO / "src"))
    proc = subprocess.run([sys.executable, "-m", "investos.check", "manifest"], env=env,
                          cwd=str(healthy.repo_root), capture_output=True, text=True, timeout=120)
    assert proc.returncode == 1, proc.stdout + proc.stderr
    assert "FAILED (1)" in proc.stdout


def test_A20_subprocess_unknown_target_exit_2(healthy):
    cfgfile = healthy.repo_root / "config" / "investos.toml"
    env = {k: v for k, v in os.environ.items() if not k.startswith("INVESTOS_")}
    env.update(INVESTOS_CONFIG=str(cfgfile), INVESTOS_ROOT=str(healthy.repo_root),
               PYTHONPATH=str(REPO / "src"))
    proc = subprocess.run([sys.executable, "-m", "investos.check", "nonesuch"], env=env,
                          cwd=str(healthy.repo_root), capture_output=True, text=True, timeout=120)
    assert proc.returncode == 2
