"""S0-T5 R10-R14: built-in checks manifest/raw/migrations and their plants."""
from __future__ import annotations

import io
import os

import pytest

from _chk_helpers import (by_name, put, raw_path, read_report, rows, snap, tamper, text_of)

BUILTINS = ("manifest", "raw", "migrations")


def _run_builtin(name, cfg, run_id="t-direct"):
    import importlib
    importlib.import_module("investos.check.builtin")
    from investos.check.registry import CheckContext, DEFAULT_REGISTRY
    return DEFAULT_REGISTRY.get(name)(CheckContext(cfg, run_id))


def test_A13_builtin_checks_pass_and_do_not_modify_fixture(healthy):
    zones = [healthy.zones[k] for k in ("raw", "manifests", "canonical", "ledger", "reports", "registry")]
    before = snap(*zones, healthy.migrations_dir)
    for n in BUILTINS:
        r = _run_builtin(n, healthy)
        assert r.ok, f"{n}: {text_of(r)}"
        assert "\n" not in r.message and len(r.message) <= 200
    assert snap(*zones, healthy.migrations_dir) == before


def test_A13_migrations_check_leaves_db_openable(healthy):
    from investos.core.db import open_db
    assert _run_builtin("migrations", healthy).ok
    open_db(healthy, read_only=True).close()
    open_db(healthy).close()                    # no lock left behind


def test_A14_manifest_pass_message_counts_sources_and_lines(healthy):
    r = _run_builtin("manifest", healthy)
    assert r.ok and r.message == "2 sources, 3 lines OK"


@pytest.mark.planted
def test_A14_manifest_hash_mismatch_names_kind_and_path(healthy):
    p = raw_path(healthy, "bse", b"alpha-bytes")
    tamper(p, b"ALPHA-bytes")
    r = _run_builtin("manifest", healthy)
    assert not r.ok
    t = text_of(r)
    assert "hash_mismatch" in t and p.name in t


@pytest.mark.planted
def test_A14_manifest_truncated_line_bad_line(healthy):
    mf = healthy.zones["manifests"] / "bse.jsonl"
    data = mf.read_bytes()
    mf.write_bytes(data[: data.rstrip(b"\n").rfind(b"\n") + 1] + b'{"sha256":"abc')
    r = _run_builtin("manifest", healthy)
    assert not r.ok and "bad_line" in text_of(r)


@pytest.mark.planted
def test_S0T3_D4_torn_manifest_tail_fails_manifest_check(healthy):
    """Review D4 from S0-T3: a valid last line whose newline was lost (torn tail) must fail."""
    mf = healthy.zones["manifests"] / "nse.jsonl"
    data = mf.read_bytes()
    assert data.endswith(b"\n")
    mf.write_bytes(data[:-1])                    # drop only the terminating newline
    r = _run_builtin("manifest", healthy)
    assert not r.ok and "bad_line" in text_of(r)


@pytest.mark.planted
def test_S0T3_D4_torn_manifest_tail_cut_mid_record(healthy):
    mf = healthy.zones["manifests"] / "nse.jsonl"
    data = mf.read_bytes()
    mf.write_bytes(data[:-25])
    r = _run_builtin("manifest", healthy)
    assert not r.ok and "bad_line" in text_of(r)


@pytest.mark.planted
def test_A14_manifest_deleted_raw_file_missing_file(healthy):
    p = raw_path(healthy, "nse", b"gamma-bytes")
    os.chmod(p.parent, 0o755)
    p.unlink()
    r = _run_builtin("manifest", healthy)
    assert not r.ok and "missing_file" in text_of(r)


@pytest.mark.planted
def test_A14_manifest_size_mismatch(healthy):
    p = raw_path(healthy, "bse", b"beta-bytes")
    tamper(p, b"beta-bytes-and-more")
    r = _run_builtin("manifest", healthy)
    assert not r.ok
    assert "size_mismatch" in text_of(r) or "hash_mismatch" in text_of(r)


def test_A14_manifest_empty_zone_passes(cfg):
    r = _run_builtin("manifest", cfg)
    assert r.ok


@pytest.mark.planted
def test_A14_manifest_does_not_fail_on_writable_file_only(healthy):
    os.chmod(raw_path(healthy, "bse", b"alpha-bytes"), 0o644)
    assert _run_builtin("manifest", healthy).ok          # that is the raw check's job


@pytest.mark.planted
def test_A15_raw_writable_file_fails_manifest_still_passes(healthy):
    p = raw_path(healthy, "bse", b"alpha-bytes")
    os.chmod(p, 0o644)
    r = _run_builtin("raw", healthy)
    assert not r.ok
    t = text_of(r)
    assert "writable_file" in t and p.name in t
    assert _run_builtin("manifest", healthy).ok


@pytest.mark.planted
def test_A15_raw_misplaced_file_fails(healthy):
    stray = healthy.zones["raw"] / "bse" / "ab" / "not_a_hash"
    stray.parent.mkdir(parents=True, exist_ok=True)
    stray.write_bytes(b"x")
    os.chmod(stray, 0o444)
    r = _run_builtin("raw", healthy)
    assert not r.ok and "misplaced_file" in text_of(r)


def test_A15_raw_passes_on_healthy_and_empty(healthy, cfg):
    assert _run_builtin("raw", healthy).ok


def test_A15_raw_empty_zone_passes(cfg):
    assert _run_builtin("raw", cfg).ok


@pytest.mark.planted
def test_A15_raw_does_not_fail_on_hash_mismatch_alone(healthy):
    tamper(raw_path(healthy, "bse", b"alpha-bytes"), b"ALPHA-bytes")
    assert _run_builtin("raw", healthy).ok


@pytest.mark.planted
def test_A16_migrations_edited_file_fails_naming_file(healthy):
    f = healthy.migrations_dir / "0001_second.sql"
    f.write_bytes(f.read_bytes() + b"-- edited\n")
    r = _run_builtin("migrations", healthy)
    assert not r.ok and "0001_second.sql" in text_of(r)


@pytest.mark.planted
def test_A16_migrations_database_missing(healthy):
    healthy.db_path.unlink()
    r = _run_builtin("migrations", healthy)
    assert not r.ok and "database missing" in r.message


def test_A16_migrations_no_sql_no_db_passes(cfg):
    for p in cfg.migrations_dir.glob("*.sql"):
        p.unlink()
    assert not cfg.db_path.exists()
    assert _run_builtin("migrations", cfg).ok


def test_A16_migrations_healthy_passes(healthy):
    assert _run_builtin("migrations", healthy).ok


@pytest.mark.planted
def test_A16_migrations_unapplied_older_file_fails(healthy):
    (healthy.migrations_dir / "0000_backfill.sql").write_text("SELECT 1;\n")
    r = _run_builtin("migrations", healthy)
    assert not r.ok


@pytest.mark.planted
def test_A17_main_planted_default_registry_catches_all_builtins(healthy, run_id):
    from investos.check.cli import main
    buf = io.StringIO()
    code = main(["--planted"], cfg=healthy, stdout=buf)
    t = rows(buf.getvalue())
    for n in BUILTINS:
        assert t[n][0] == "caught", buf.getvalue()
    assert code == 0, buf.getvalue()
    rep = read_report(healthy, run_id)
    assert rep["mode"] == "planted" and rep["ok"] is True
    assert {n: by_name(rep)[n]["phase"] for n in BUILTINS} == {n: "plant" for n in BUILTINS}


@pytest.mark.planted
def test_A17_planted_run_on_empty_real_data_also_works(cfg, run_id):
    """The plants' setup builds their own healthy fixture; the real data need not hold any."""
    from investos.check.cli import main
    buf = io.StringIO()
    assert main(["--planted"], cfg=cfg, stdout=buf) == 0, buf.getvalue()


@pytest.mark.planted
@pytest.mark.parametrize("name", BUILTINS)
def test_A17_noop_plant_for_builtin_is_missed(name, healthy, run_id):
    """A deliberately broken built-in (plant replaced by a no-op) must be reported missed, exit 1."""
    import importlib
    importlib.import_module("investos.check.builtin")
    from investos.check.registry import CheckRegistry, DEFAULT_REGISTRY
    from investos.check.cli import main
    reg = CheckRegistry()
    setup, _plant = DEFAULT_REGISTRY.plant_of(name)
    reg.check(name)(DEFAULT_REGISTRY.get(name))
    reg.plant_for(name, setup=setup)(lambda ctx: None)
    buf = io.StringIO()
    code = main(["--planted"], registry=reg, cfg=healthy, stdout=buf)
    assert code == 1
    assert rows(buf.getvalue())[name][0] == "missed"


@pytest.mark.planted
@pytest.mark.parametrize("name", BUILTINS)
def test_A17_builtin_checks_pass_baseline_and_fail_after_plant(name, healthy, tmp_path):
    """R14: setup builds a healthy fixture in the temp copy; the plant breaks it."""
    import importlib
    importlib.import_module("investos.check.builtin")
    from investos.check.registry import CheckRegistry, DEFAULT_REGISTRY
    from investos.check.runner import run_planted
    reg = CheckRegistry()
    seen = {}
    setup, plant = DEFAULT_REGISTRY.plant_of(name)
    fn = DEFAULT_REGISTRY.get(name)

    def spy(ctx):
        r = fn(ctx)
        seen.setdefault("results", []).append(r.ok)
        return r
    reg.check(name)(spy)
    reg.plant_for(name, setup=setup)(plant)
    run_planted(reg, healthy, tmp_parent=tmp_path)
    assert seen["results"] == [True, False]


def test_A17_real_data_untouched_by_builtin_planted_run(healthy, run_id):
    from _chk_helpers import digest
    from investos.check.cli import main
    zones = [healthy.zones[k] for k in ("raw", "manifests", "canonical", "ledger", "registry")]
    before = digest(*zones, healthy.migrations_dir)
    main(["--planted"], cfg=healthy, stdout=io.StringIO())
    assert digest(*zones, healthy.migrations_dir) == before


@pytest.mark.planted
def test_main_plain_all_on_healthy_and_planted_corruption(healthy, run_id):
    from investos.check.cli import main
    buf = io.StringIO()
    assert main([], cfg=healthy, stdout=buf) == 0
    tamper(raw_path(healthy, "bse", b"alpha-bytes"), b"ALPHA-bytes")
    buf = io.StringIO()
    code = main([], cfg=healthy, stdout=buf)
    assert code == 1
    assert rows(buf.getvalue())["manifest"][0] == "fail"
    assert rows(buf.getvalue())["migrations"][0] == "pass"
