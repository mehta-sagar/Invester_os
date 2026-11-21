"""S0-T5 review regressions (D2, M14, D5): safety of the planted harness against real data."""
from __future__ import annotations

import os
import stat

import pytest

from _chk_helpers import by_name, digest, flag_check, flag_plant, last_line, read_report, rows, with_flag_plant


@pytest.mark.planted
def test_RD2_plant_writing_into_real_reports_zone_is_error(reg, healthy, run, run_id):
    real_stray = healthy.zones["reports"] / "evil.txt"

    def evil(ctx):
        real_stray.write_text("x")                           # absolute path into the REAL reports zone
        (ctx.cfg.zones["canonical"] / "flag.txt").write_text("BAD")
    flag_check(reg, "c")
    reg.plant_for("c", setup=lambda ctx: (ctx.cfg.zones["canonical"] / "flag.txt").write_text("OK"))(evil)
    flag_check(reg, "d")
    with_flag_plant(reg, "d")
    code, out = run(["--planted"], reg, healthy)
    assert code == 1
    t = rows(out)
    assert t["c"][0] == "error" and t["d"][0] == "error"
    assert "real data modified" in out
    rep = read_report(healthy, run_id)                       # this run's own report is still written
    assert rep["ok"] is False
    assert all(r["status"] == "error" and "real data modified" in r["message"] for r in rep["results"])


@pytest.mark.planted
def test_RD2_plant_modifying_existing_real_report_is_error(reg, healthy, run):
    old = healthy.zones["reports"] / "old_report.json"
    old.write_text("{}")

    def evil(ctx):
        old.write_text("{\"tampered\":1}")
        (ctx.cfg.zones["canonical"] / "flag.txt").write_text("BAD")
    flag_check(reg, "c")
    reg.plant_for("c", setup=lambda ctx: (ctx.cfg.zones["canonical"] / "flag.txt").write_text("OK"))(evil)
    code, out = run(["--planted"], reg, healthy)
    assert code == 1 and rows(out)["c"][0] == "error" and "real data modified" in out


def test_RD2_control_preexisting_reports_and_own_report_do_not_trigger(reg, healthy, run, run_id):
    (healthy.zones["reports"] / "old_report.json").write_text("{}")
    (healthy.zones["reports"] / "checks").mkdir()
    (healthy.zones["reports"] / "checks" / "other-run.json").write_text("{}")
    flag_check(reg, "c")
    with_flag_plant(reg, "c")
    code, out = run(["--planted"], reg, healthy)
    assert code == 0 and rows(out)["c"][0] == "caught"
    assert (healthy.zones["reports"] / "checks" / f"{run_id}.json").exists()
    assert (healthy.zones["reports"] / "checks" / "other-run.json").read_text() == "{}"


@pytest.mark.planted
def test_RM14_symlinked_file_to_outside_target_not_followed_or_modified(reg, cfg, tmp_path, run):
    from investos.check.runner import run_planted
    ext = tmp_path / "external"
    ext.mkdir()
    target = ext / "secret.txt"
    target.write_text("ORIGINAL")
    os.chmod(target, 0o640)
    link = cfg.zones["canonical"] / "flag.txt"
    os.symlink(target, link)
    seen = {}

    def plant(ctx):
        p = ctx.cfg.zones["canonical"] / "flag.txt"
        seen["is_link"] = p.is_symlink()
        seen["inside"] = ctx.cfg.zones["canonical"].resolve() in p.resolve().parents
        p.write_text("BAD")
    flag_check(reg, "c")
    reg.plant_for("c")(plant)
    tp = tmp_path / "tp"
    tp.mkdir()
    run_planted(reg, cfg, tmp_parent=tp)
    assert target.read_bytes() == b"ORIGINAL"
    assert stat.S_IMODE(target.stat().st_mode) == 0o640
    assert seen == {"is_link": False, "inside": True}       # temp copy holds a materialised file, not the link
    assert os.path.islink(link) and os.readlink(link) == str(target)     # real link itself untouched
    assert list(tp.iterdir()) == []


@pytest.mark.planted
def test_RM14_symlinked_dir_to_outside_target_not_modified(reg, cfg, tmp_path):
    from investos.check.runner import run_planted
    ext = tmp_path / "extdir"
    ext.mkdir()
    (ext / "keep.txt").write_text("KEEP")
    link = cfg.zones["canonical"] / "linkdir"
    os.symlink(ext, link, target_is_directory=True)
    before = digest(ext)

    def plant(ctx):
        d = ctx.cfg.zones["canonical"] / "linkdir"
        (d / "keep.txt").write_text("CHANGED")
        (d / "new.txt").write_text("NEW")
        (ctx.cfg.zones["canonical"] / "flag.txt").write_text("BAD")
    flag_check(reg, "c")
    reg.plant_for("c", setup=lambda ctx: (ctx.cfg.zones["canonical"] / "flag.txt").write_text("OK"))(plant)
    tp = tmp_path / "tp"
    tp.mkdir()
    run_planted(reg, cfg, tmp_parent=tp)
    assert digest(ext) == before
    assert not (ext / "new.txt").exists()


@pytest.mark.planted
def test_RM14_symlinked_raw_file_outside_zone_not_modified(reg, cfg, tmp_path):
    """A symlink in the raw zone to an external 0444 file: a plant must not reach it."""
    from investos.check.runner import run_planted
    ext = tmp_path / "ext_raw"
    ext.write_bytes(b"EXTERNAL")
    os.chmod(ext, 0o444)
    os.symlink(ext, cfg.zones["raw"] / "pointer")

    def plant(ctx):
        p = ctx.cfg.zones["raw"] / "pointer"
        os.chmod(p, 0o644)
        p.write_bytes(b"TAMPER")
        (ctx.cfg.zones["canonical"] / "flag.txt").write_text("BAD")
    flag_check(reg, "c")
    reg.plant_for("c", setup=lambda ctx: (ctx.cfg.zones["canonical"] / "flag.txt").write_text("OK"))(plant)
    tp = tmp_path / "tp"
    tp.mkdir()
    run_planted(reg, cfg, tmp_parent=tp)
    assert ext.read_bytes() == b"EXTERNAL"
    assert stat.S_IMODE(ext.stat().st_mode) == 0o444


@pytest.mark.planted
@pytest.mark.skipif(os.geteuid() == 0, reason="root bypasses directory modes; a 0000 dir cannot break cleanup")
def test_RD5_unreadable_dir_in_temp_copy_does_not_break_cleanup(reg, healthy, tmp_path):
    from investos.check.runner import run_planted

    def plant(ctx):
        d = ctx.cfg.zones["canonical"] / "locked"
        d.mkdir()
        (d / "f.txt").write_text("x")
        os.chmod(d, 0o000)                                    # no r/w/x for anyone
        (ctx.cfg.zones["canonical"] / "flag.txt").write_text("BAD")
    flag_check(reg, "c")
    reg.plant_for("c", setup=lambda ctx: (ctx.cfg.zones["canonical"] / "flag.txt").write_text("OK"))(plant)
    tp = tmp_path / "tp"
    tp.mkdir()
    try:
        run_planted(reg, healthy, tmp_parent=tp)              # must not raise
        assert list(tp.iterdir()) == []                       # temp copy fully removed
    finally:
        for p in tp.rglob("*"):
            if p.is_dir():
                os.chmod(p, 0o755)
