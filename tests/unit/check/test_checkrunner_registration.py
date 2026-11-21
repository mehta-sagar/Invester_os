"""S0-T5 R1/R2/R15: registration, selection, CLI dispatch."""
from __future__ import annotations

import sys

import pytest

from _chk_helpers import last_line, ok_check, rows, read_report


@pytest.mark.planted
def test_A1_duplicate_and_invalid_names_rejected(reg):
    from investos.check.registry import RegistryError
    ok_check(reg, "dup")
    with pytest.raises(RegistryError):
        ok_check(reg, "dup")
    for bad in ("All", "all", "1bad", "x" * 40, "", "has-dash", "Upper"):
        with pytest.raises(RegistryError):
            ok_check(reg, bad)
    assert reg.names() == ["dup"]


def test_A1_name_boundaries_accepted(reg):
    ok_check(reg, "a")
    ok_check(reg, "a" + "b" * 31)          # 32 chars: allowed
    ok_check(reg, "a_1")
    assert len(reg.names()) == 3


@pytest.mark.planted
def test_A1_plant_registration_rules(reg):
    from investos.check.registry import RegistryError
    ok_check(reg, "x")
    with pytest.raises(RegistryError):
        reg.plant_for("unregistered")(lambda ctx: None)
    reg.plant_for("x")(lambda ctx: None)
    with pytest.raises(RegistryError):
        reg.plant_for("x")(lambda ctx: None)


def test_A1_check_without_plant_allowed_and_plant_of(reg):
    ok_check(reg, "p")
    ok_check(reg, "q")

    def setup(ctx): ...
    def plant(ctx): ...
    reg.plant_for("q", setup=setup)(plant)
    assert reg.plant_of("p") is None
    assert reg.plant_of("q") == (setup, plant)
    assert callable(reg.get("p"))


def test_A1_decorators_return_the_function_unchanged(reg):
    from investos.check.registry import CheckResult

    def f(ctx):
        return CheckResult(True, "x")
    assert reg.check("same")(f) is f
    def pf(ctx): ...
    assert reg.plant_for("same")(pf) is pf


def test_A1_names_in_registration_order_and_get(reg):
    for n in ("zeta", "alpha", "mid"):
        ok_check(reg, n)
    assert reg.names() == ["zeta", "alpha", "mid"]
    assert reg.get("alpha") is not reg.get("mid")


def test_A1_module_level_decorators_exist_and_bound_to_default():
    from investos.check import registry as r
    assert isinstance(r.DEFAULT_REGISTRY, r.CheckRegistry)
    assert callable(r.check) and callable(r.plant_for)
    assert issubclass(r.RegistryError, Exception)


def test_A2_no_target_runs_all_in_order(reg, run, cfg, run_id):
    calls = []
    ok_check(reg, "a", calls)
    ok_check(reg, "b", calls)
    code, out = run([], reg)
    assert code == 0
    assert calls == ["a", "b"]
    assert list(rows(out)) == ["a", "b"]


def test_A2_all_target_equals_no_target(reg, run):
    calls = []
    ok_check(reg, "a", calls)
    ok_check(reg, "b", calls)
    code, _ = run(["all"], reg)
    assert code == 0 and calls == ["a", "b"]


def test_A2_single_target_runs_only_it(reg, run, cfg, run_id):
    calls = []
    ok_check(reg, "a", calls)
    ok_check(reg, "b", calls)
    code, out = run(["b"], reg)
    assert code == 0
    assert calls == ["b"]
    assert list(rows(out)) == ["b"]
    assert read_report(cfg, run_id)["selected"] == ["b"]


def test_A2_unknown_target_is_usage_error(reg, run, cfg, run_id):
    calls = []
    ok_check(reg, "a", calls)
    ok_check(reg, "b", calls)
    code, out = run(["zzz"], reg)
    assert code == 2
    assert calls == []
    assert "NAME" not in out                      # table not printed
    assert "a" in out and "b" in out and "zzz" in out
    assert "a, b" in out


def test_A2_two_positional_targets_is_usage_error(reg, run):
    calls = []
    ok_check(reg, "a", calls)
    ok_check(reg, "b", calls)
    code, out = run(["a", "b"], reg)
    assert code == 2
    assert calls == []


def test_A2_usage_error_writes_no_report(reg, run, cfg, run_id):
    from _chk_helpers import report_path
    ok_check(reg, "a")
    run(["zzz"], reg)
    assert not report_path(cfg, run_id).exists()


def test_A2_planted_flag_with_target(reg, run):
    calls = []
    ok_check(reg, "a", calls)
    ok_check(reg, "b", calls)
    reg.plant_for("a")(lambda ctx: None)
    code, out = run(["--planted", "a"], reg)
    assert list(rows(out)) == ["a"]


def test_A2_table_header_and_final_line(reg, run):
    ok_check(reg, "a")
    code, out = run([], reg)
    first = out.splitlines()[0].split()
    assert first == ["NAME", "STATUS", "MS", "MESSAGE"]
    assert last_line(out) == "OK"


def test_A18_builtins_registered_in_default_registry():
    import importlib
    importlib.import_module("investos.check.builtin")
    from investos.check.registry import DEFAULT_REGISTRY
    names = DEFAULT_REGISTRY.names()
    pos = [names.index(n) for n in ("manifest", "raw", "migrations")]
    assert pos == sorted(pos)
    for n in ("manifest", "raw", "migrations"):
        assert DEFAULT_REGISTRY.plant_of(n) is not None, f"{n} has no plant"


def test_A18_default_registry_main_without_registry_module_target_is_usage_error(monkeypatch, cfg, run_id):
    import io
    from investos.check.cli import main
    monkeypatch.setitem(sys.modules, "investos.registry.check", None)   # import raises ModuleNotFoundError
    buf = io.StringIO()
    assert main(["registry"], cfg=cfg, stdout=buf) == 2
    buf = io.StringIO()
    assert main(["--planted", "registry"], cfg=cfg, stdout=buf) == 2


def test_A18_default_registry_main_runs_builtin_by_name(healthy, run_id):
    import io
    from investos.check.cli import main
    buf = io.StringIO()
    assert main(["manifest"], cfg=healthy, stdout=buf) == 0
    assert "manifest" in rows(buf.getvalue())


def test_A18_check_registered_from_other_module_is_discovered(reg, run):
    """Stands in for S0-T6: a check registered by another module shows up in `all`."""
    import types
    from investos.check.registry import CheckResult
    other = types.ModuleType("fake_registry_check")
    exec("def chk(ctx):\n    return CheckResult(True, 'chain ok')\n", {"CheckResult": CheckResult}, other.__dict__)
    reg.check("registry")(other.chk)
    code, out = run([], reg)
    assert code == 0
    assert rows(out)["registry"][0] == "pass"
    code, out = run(["registry"], reg)
    assert code == 0


def test_cli_dispatches_check_subcommand(healthy, run_id, monkeypatch):
    """Console entry point investos.cli:main dispatches `check` (config via INVESTOS_CONFIG)."""
    from investos import cli
    monkeypatch.setenv("INVESTOS_CONFIG", str(healthy.repo_root / "config" / "investos.toml"))
    monkeypatch.setenv("INVESTOS_ROOT", str(healthy.repo_root))
    code = cli.main(["check", "manifest"])
    assert code == 0
    assert (healthy.zones["reports"] / "checks" / f"{run_id}.json").exists()
