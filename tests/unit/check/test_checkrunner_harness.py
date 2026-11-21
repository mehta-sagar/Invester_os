"""S0-T5 R6-R9 (+R10 isolation): the planted-failure harness."""
from __future__ import annotations

import os
import stat

import pytest

from _chk_helpers import (by_name, digest, fail_check, flag_check, flag_plant, flag_setup, last_line,
                          ok_check, put, read_report, rows, with_flag_plant)


@pytest.mark.planted
def test_A7_check_without_plant_refused_never_called_exit_1(reg, run, cfg, run_id):
    calls = []
    ok_check(reg, "x", calls)
    code, out = run(["--planted"], reg)
    assert code == 1
    assert calls == []
    assert rows(out)["x"][0] == "refused"
    assert "no plant registered" in out
    r = by_name(read_report(cfg, run_id))["x"]
    assert r["status"] == "refused" and r["phase"] == "none" and r["plant"] == "missing"


@pytest.mark.planted
def test_A7_refusal_does_not_stop_other_checks(reg, run, cfg, run_id):
    ok_check(reg, "noplant")
    flag_check(reg, "c")
    with_flag_plant(reg, "c")
    code, out = run(["--planted"], reg)
    assert code == 1
    t = rows(out)
    assert t["noplant"][0] == "refused" and t["c"][0] == "caught"
    assert last_line(out) == "FAILED (1)"


def test_A7_plain_mode_missing_plant_shown_but_exit_0(reg, run, cfg, run_id):
    calls = []
    ok_check(reg, "x", calls)
    code, out = run([], reg)
    assert code == 0 and calls == ["x"]
    assert rows(out)["x"][0] == "pass"
    assert "plant: MISSING" in out
    r = by_name(read_report(cfg, run_id))["x"]
    assert r["plant"] == "missing" and r["status"] == "pass"


def test_A7_plain_mode_registered_plant_recorded(reg, run, cfg, run_id):
    flag_check(reg, "c")
    with_flag_plant(reg, "c")
    (cfg.zones["canonical"] / "flag.txt").write_text("OK")
    code, out = run([], reg)
    assert code == 0
    assert "plant: MISSING" not in out
    assert by_name(read_report(cfg, run_id))["c"]["plant"] == "registered"


@pytest.mark.planted
def test_A8_plant_that_breaks_check_is_caught(reg, run, cfg, run_id):
    flag_check(reg, "c")
    with_flag_plant(reg, "c")
    code, out = run(["--planted"], reg)
    assert code == 0
    assert rows(out)["c"][0] == "caught"
    assert last_line(out) == "OK"
    assert by_name(read_report(cfg, run_id))["c"]["phase"] == "plant"


@pytest.mark.planted
def test_A8_noop_plant_is_missed_exit_1(reg, run, cfg, run_id):
    flag_check(reg, "c")
    with_flag_plant(reg, "c", plant=lambda ctx: None)
    code, out = run(["--planted"], reg)
    assert code == 1
    assert rows(out)["c"][0] == "missed"
    assert by_name(read_report(cfg, run_id))["c"]["phase"] == "plant"


@pytest.mark.planted
def test_A8_always_failing_check_is_baseline_failed_plant_not_called(reg, run, cfg, run_id):
    fail_check(reg, "bad")
    planted = []
    reg.plant_for("bad", setup=flag_setup)(lambda ctx: planted.append(1))
    code, out = run(["--planted"], reg)
    assert code == 1
    assert rows(out)["bad"][0] == "baseline_failed"      # an always-failing check can never count as caught
    assert planted == []
    assert by_name(read_report(cfg, run_id))["bad"]["phase"] == "baseline"


@pytest.mark.planted
def test_A8_always_passing_check_is_missed(reg, run):
    ok_check(reg, "always")
    reg.plant_for("always", setup=flag_setup)(flag_plant)
    code, out = run(["--planted"], reg)
    assert code == 1
    assert rows(out)["always"][0] == "missed"


@pytest.mark.planted
def test_A8_setup_runs_before_baseline_and_plant_after(reg, run):
    order = []
    from investos.check.registry import CheckResult

    def chk(ctx):
        order.append("check")
        return CheckResult(len([o for o in order if o == "plant"]) == 0, "m")
    reg.check("c")(chk)
    reg.plant_for("c", setup=lambda ctx: order.append("setup"))(lambda ctx: order.append("plant"))
    code, out = run(["--planted"], reg)
    assert order == ["setup", "check", "plant", "check"]
    assert rows(out)["c"][0] == "caught"


@pytest.mark.planted
def test_A8_setup_optional(reg, run):
    from investos.check.registry import CheckResult
    ok_check(reg, "dummy")
    state = {"broken": False}

    def chk(ctx):
        return CheckResult(not (ctx.cfg.zones["canonical"] / "marker").exists(), "m")
    reg.check("c")(chk)
    reg.plant_for("c")(lambda ctx: (ctx.cfg.zones["canonical"] / "marker").write_text("x"))
    code, out = run(["--planted", "c"], reg)
    assert code == 0 and rows(out)["c"][0] == "caught"


@pytest.mark.planted
def test_A9_check_raising_after_plant_is_caught_with_error_prefix(reg, run, cfg, run_id):
    from investos.check.registry import CheckResult

    def chk(ctx):
        t = (ctx.cfg.zones["canonical"] / "flag.txt").read_text()
        if "BAD" in t:
            raise RuntimeError("cannot parse")
        return CheckResult(True, "fine")
    reg.check("c")(chk)
    with_flag_plant(reg, "c")
    code, out = run(["--planted"], reg)
    assert code == 0
    assert rows(out)["c"][0] == "caught"
    msg = by_name(read_report(cfg, run_id))["c"]["message"]
    assert msg.startswith("error: ")


@pytest.mark.planted
def test_A9_check_raising_in_baseline_is_not_caught(reg, run):
    def chk(ctx):
        raise RuntimeError("cannot start")
    reg.check("c")(chk)
    with_flag_plant(reg, "c")
    code, out = run(["--planted"], reg)
    assert code == 1
    assert rows(out)["c"][0] in ("error", "baseline_failed")


@pytest.mark.planted
def test_A9_setup_raising_is_error(reg, run, cfg, run_id):
    flag_check(reg, "c")
    called = []

    def bad_setup(ctx):
        raise OSError("setup broke")
    reg.plant_for("c", setup=bad_setup)(lambda ctx: called.append(1))
    code, out = run(["--planted"], reg)
    assert code == 1 and called == []
    assert rows(out)["c"][0] == "error"
    assert "setup broke" in by_name(read_report(cfg, run_id))["c"]["message"]


@pytest.mark.planted
def test_A9_plant_raising_is_error_and_others_still_run(reg, run):
    flag_check(reg, "c")

    def bad_plant(ctx):
        raise KeyError("nope")
    reg.plant_for("c", setup=flag_setup)(bad_plant)
    flag_check(reg, "d")
    with_flag_plant(reg, "d")
    code, out = run(["--planted"], reg)
    assert code == 1
    t = rows(out)
    assert t["c"][0] == "error" and t["d"][0] == "caught"


def test_A8_check_gets_temp_copy_of_real_data(reg, healthy, tmp_path):
    """R7 b/c: temp copy has the real zones' content under different paths."""
    from investos.check.registry import CheckResult
    from investos.check.runner import run_planted
    (healthy.zones["canonical"] / "seed.txt").write_text("seed")
    seen = {}

    def chk(ctx):
        c = ctx.cfg
        seen.setdefault("cfgs", []).append(c)
        seen["seed"] = (c.zones["canonical"] / "seed.txt").read_text()
        seen["mig"] = sorted(p.name for p in c.migrations_dir.iterdir())
        return CheckResult(not (c.zones["canonical"] / "marker").exists(), "m")
    reg.check("c")(chk)
    reg.plant_for("c")(lambda ctx: (ctx.cfg.zones["canonical"] / "marker").write_text("x"))
    tmp_parent = tmp_path / "tp"
    tmp_parent.mkdir()
    run_planted(reg, healthy, tmp_parent=tmp_parent)
    assert seen["seed"] == "seed"
    assert seen["mig"] == ["0000_bootstrap.sql", "0001_second.sql"]
    c = seen["cfgs"][0]
    for k, p in c.zones.items():
        assert p != healthy.zones[k]
        assert tmp_parent in p.parents
    assert tmp_parent in c.migrations_dir.parents and c.migrations_dir != healthy.migrations_dir
    assert c.timezone == healthy.timezone and c.rate_limits == healthy.rate_limits
    assert not (healthy.zones["canonical"] / "marker").exists()


def test_A10_temp_dir_removed_even_with_readonly_raw(reg, healthy, tmp_path):
    from investos.check.runner import run_planted
    raws = [p for p in healthy.zones["raw"].rglob("*") if p.is_file()]
    assert raws and all(stat.S_IMODE(p.stat().st_mode) == 0o444 for p in raws)
    flag_check(reg, "c")
    with_flag_plant(reg, "c")
    tmp_parent = tmp_path / "tp"
    tmp_parent.mkdir()
    run_planted(reg, healthy, tmp_parent=tmp_parent)           # must not raise PermissionError
    assert list(tmp_parent.iterdir()) == []


@pytest.mark.planted
def test_A10_temp_dir_removed_when_plant_and_check_raise(reg, healthy, tmp_path):
    from investos.check.runner import run_planted
    flag_check(reg, "c")

    def bad(ctx):
        raise RuntimeError("x")
    reg.plant_for("c", setup=flag_setup)(bad)
    tmp_parent = tmp_path / "tp"
    tmp_parent.mkdir()
    run_planted(reg, healthy, tmp_parent=tmp_parent)
    assert list(tmp_parent.iterdir()) == []


@pytest.mark.planted
def test_A11_real_data_identical_after_planted_run(reg, healthy):
    from investos.check.runner import run_planted
    from investos.core.config import Config

    def plant(ctx):
        raw = next(p for p in ctx.cfg.zones["raw"].rglob("*") if p.is_file())
        os.chmod(raw, 0o644)
        raw.write_bytes(b"tampered")
        (ctx.cfg.zones["canonical"] / "flag.txt").write_text("BAD")
    flag_check(reg, "c")
    reg.plant_for("c", setup=flag_setup)(plant)
    zones = [healthy.zones[k] for k in ("raw", "manifests", "canonical", "ledger", "reports", "registry")]
    before = digest(*zones, healthy.migrations_dir)
    rep = run_planted(reg, healthy)
    after = digest(*zones, healthy.migrations_dir)
    assert before == after
    assert rep is not None


@pytest.mark.planted
def test_A11_real_data_identical_even_when_plant_raises(reg, healthy):
    from investos.check.runner import run_planted
    flag_check(reg, "c")

    def plant(ctx):
        (ctx.cfg.zones["canonical"] / "half-done").write_text("x")
        raise RuntimeError("mid-plant")
    reg.plant_for("c", setup=flag_setup)(plant)
    zones = [healthy.zones[k] for k in ("raw", "manifests", "canonical", "ledger", "reports", "registry")]
    before = digest(*zones, healthy.migrations_dir)
    run_planted(reg, healthy)
    assert digest(*zones, healthy.migrations_dir) == before


@pytest.mark.planted
def test_A11_defective_plant_writing_real_path_is_error_real_data_modified(reg, healthy, run, run_id):
    real_flag = healthy.zones["canonical"] / "flag.txt"
    real_flag.write_text("OK")

    def evil(ctx):
        real_flag.write_text("BAD")                 # absolute path to the REAL data
        (ctx.cfg.zones["canonical"] / "flag.txt").write_text("BAD")
    flag_check(reg, "c")
    reg.plant_for("c", setup=flag_setup)(evil)
    flag_check(reg, "d")
    with_flag_plant(reg, "d")
    code, out = run(["--planted"], reg, healthy)
    assert code == 1
    t = rows(out)
    assert t["c"][0] == "error" and t["d"][0] == "error"          # every result, not just the offender
    assert "real data modified" in out
    rep = read_report(healthy, run_id)
    assert all(r["status"] == "error" and "real data modified" in r["message"] for r in rep["results"])
    assert rep["ok"] is False


@pytest.mark.planted
def test_A12_planted_report_modes_phases_and_ok(reg, run, cfg, run_id):
    flag_check(reg, "caught")
    with_flag_plant(reg, "caught")
    flag_check(reg, "missed")
    with_flag_plant(reg, "missed", plant=lambda ctx: None)
    ok_check(reg, "refused")
    fail_check(reg, "basefail")
    reg.plant_for("basefail", setup=flag_setup)(flag_plant)
    code, out = run(["--planted"], reg)
    assert code == 1
    rep = read_report(cfg, run_id)
    assert rep["mode"] == "planted" and rep["ok"] is False
    r = by_name(rep)
    assert (r["caught"]["status"], r["caught"]["phase"]) == ("caught", "plant")
    assert (r["missed"]["status"], r["missed"]["phase"]) == ("missed", "plant")
    assert (r["refused"]["status"], r["refused"]["phase"]) == ("refused", "none")
    assert (r["basefail"]["status"], r["basefail"]["phase"]) == ("baseline_failed", "baseline")
    assert [x["name"] for x in rep["results"]] == ["caught", "missed", "refused", "basefail"]


@pytest.mark.planted
def test_A12_ok_true_only_if_all_caught(reg, run, cfg, run_id):
    flag_check(reg, "a")
    with_flag_plant(reg, "a")
    flag_check(reg, "b")
    with_flag_plant(reg, "b")
    code, out = run(["--planted"], reg)
    assert code == 0 and last_line(out) == "OK"
    rep = read_report(cfg, run_id)
    assert rep["ok"] is True and all(r["status"] == "caught" for r in rep["results"])


@pytest.mark.planted
def test_A12_planted_report_written_in_real_reports_zone_only(reg, run, cfg, run_id):
    flag_check(reg, "c")
    with_flag_plant(reg, "c")
    run(["--planted"], reg)
    assert (cfg.zones["reports"] / "checks" / f"{run_id}.json").exists()


def test_two_checks_may_share_a_plant_function(reg, run):
    flag_check(reg, "a")
    flag_check(reg, "b")
    with_flag_plant(reg, "a")
    with_flag_plant(reg, "b")
    code, out = run(["--planted"], reg)
    assert code == 0
