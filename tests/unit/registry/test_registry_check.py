"""S0-T6 acceptance tests A20, A21 for investos.registry.check (rule R13, exit-gate demo 4 via the S0-T5 runner).

Assumed S0-T5 interface (specs/check_runner.md; src/ was not read): investos.check.registry
(DEFAULT_REGISTRY, CheckRegistry, CheckContext, CheckResult) and investos.check.cli.main."""
from __future__ import annotations

import io
import json
from pathlib import Path

import pytest

from _registry_helpers import FakeClock, read_lines


def _check_env():
    import investos.registry.check  # noqa: F401  (registers "registry" in DEFAULT_REGISTRY)
    from investos.check.registry import DEFAULT_REGISTRY, CheckContext
    return DEFAULT_REGISTRY, CheckContext


def _seed(cfg, n_trials=3, n_decisions=3):
    from investos.registry.ledgers import log_decision, open_decisions, open_trials, register_trial
    clk = FakeClock("2026-11-20T10:00:00")
    t, d = open_trials(cfg, clock=clk), open_decisions(cfg, clock=clk)
    for i in range(n_trials):
        register_trial(t, trial_id=f"t-{i}", family="momentum", feature_id=f"f{i}",
                       params={"threshold": "0.05"}, hypothesis=f"hypothesis number {i}")
        clk.advance(seconds=1)
    for i in range(n_decisions):
        log_decision(d, date="2026-11-20", security_id=f"SEC{i}", action="buy", reasons=["signal"],
                     model_versions={"alpha": "v1"})
        clk.advance(seconds=1)
    return t, d


def _flip_char(path: Path, line_idx: int, field: str):
    lines = read_lines(path)
    obj = json.loads(lines[line_idx])
    s = obj["payload"][field]
    obj["payload"][field] = ("X" if s[0] != "X" else "Y") + s[1:]
    lines[line_idx] = (json.dumps(obj, sort_keys=True, separators=(",", ":"), ensure_ascii=False) + "\n").encode()
    path.write_bytes(b"".join(lines))


def _text(res):
    return "\n".join((res.message,) + tuple(res.details))


def _run(cfg, argv):
    from investos.check.cli import main
    reg, _ = _check_env()
    out = io.StringIO()
    code = main(argv, registry=reg, cfg=cfg, stdout=out)
    return code, out.getvalue()


def _reports(cfg):
    return sorted((cfg.zones["reports"] / "checks").glob("*.json"))


def _snapshot(cfg):
    snap = {}
    for z in cfg.zones.values():
        for p in sorted(Path(z).rglob("*")):
            if p.is_file():
                st = p.stat()
                snap[str(p)] = (p.read_bytes(), st.st_mtime_ns, st.st_mode)
    return snap


# ---------- A20 ----------

def test_A20_check_is_registered_in_default_registry():
    reg, _ = _check_env()
    assert "registry" in reg.names()
    assert reg.plant_of("registry") is not None


def test_A20b_healthy_ledgers_pass(cfg):
    reg, Ctx = _check_env()
    _seed(cfg)
    res = reg.get("registry")(Ctx(cfg=cfg, run_id="t-healthy"))
    assert res.ok and res.message == "trials: 3 rows OK; decisions: 3 rows OK"


def test_A20c_absent_ledgers_pass(cfg):
    reg, Ctx = _check_env()
    res = reg.get("registry")(Ctx(cfg=cfg, run_id="t-absent"))
    assert res.ok and "trials: 0 rows OK" in res.message and "decisions: 0 rows OK" in res.message


@pytest.mark.planted
def test_A20d_edited_hypothesis_fails_at_seq_2(cfg):
    reg, Ctx = _check_env()
    t, d = _seed(cfg)
    _flip_char(t.path, 1, "hypothesis")
    res = reg.get("registry")(Ctx(cfg=cfg, run_id="t-bad"))
    assert not res.ok
    assert "trials: broken at seq 2" in _text(res)
    assert "decisions: broken" not in _text(res)


@pytest.mark.planted
def test_A20e_decisions_tamper_reported_with_its_own_kind(cfg):
    reg, Ctx = _check_env()
    t, d = _seed(cfg)
    lines = read_lines(d.path)
    obj = json.loads(lines[2]); obj["payload"]["security_id"] = "EVIL"
    lines[2] = (json.dumps(obj, sort_keys=True, separators=(",", ":"), ensure_ascii=False) + "\n").encode()
    d.path.write_bytes(b"".join(lines))
    res = reg.get("registry")(Ctx(cfg=cfg, run_id="t-bad2"))
    assert not res.ok and "decisions: broken at seq 3" in _text(res)


@pytest.mark.planted
def test_A20f_cli_registry_exit_1_and_report_written(cfg):
    t, d = _seed(cfg)
    _flip_char(t.path, 1, "hypothesis")
    code, out = _run(cfg, ["registry"])
    assert code == 1
    reports = _reports(cfg)
    assert len(reports) == 1
    rep = json.loads(reports[0].read_text(encoding="utf-8"))
    assert rep["ok"] is False and rep["mode"] == "check"
    res = {r["name"]: r for r in rep["results"]}["registry"]
    assert res["status"] == "fail"
    assert "trials: broken at seq 2" in (res["message"] + "\n".join(res["details"]))


def test_A20g_cli_registry_exit_0_on_healthy(cfg):
    _seed(cfg)
    code, out = _run(cfg, ["registry"])
    assert code == 0 and out.strip().splitlines()[-1] == "OK"


# ---------- A21 ----------

@pytest.mark.planted
def test_A21_planted_registry_is_caught(cfg):
    code, out = _run(cfg, ["--planted", "registry"])
    assert code == 0, out
    rep = json.loads(_reports(cfg)[0].read_text(encoding="utf-8"))
    res = {r["name"]: r for r in rep["results"]}["registry"]
    assert rep["mode"] == "planted" and res["status"] == "caught" and res["phase"] == "plant"


@pytest.mark.planted
def test_A21b_check_never_changes_ledger_files(cfg):
    t, d = _seed(cfg)
    clock = FakeClock("2026-11-25T10:00:00")  # closed days exist; a read-only check must still not write roots
    before = _snapshot(cfg)
    code, out = _run(cfg, ["registry"])
    assert code == 0
    after = _snapshot(cfg)
    ledgers = {k: v for k, v in before.items() if k.endswith((".jsonl", ".lock"))}
    assert ledgers
    for k, v in ledgers.items():
        assert after[k] == v
    assert not [k for k in after if k.endswith(".roots.jsonl")]


@pytest.mark.planted
def test_A21c_plant_noop_is_missed(cfg):
    from investos.check.registry import CheckRegistry
    reg, _ = _check_env()
    setup, _plant = reg.plant_of("registry")
    private = CheckRegistry()
    private.check("registry")(reg.get("registry"))
    private.plant_for("registry", setup=setup)(lambda ctx: None)
    from investos.check.cli import main
    code = main(["--planted", "registry"], registry=private, cfg=cfg, stdout=io.StringIO())
    assert code == 1
    rep = json.loads(_reports(cfg)[0].read_text(encoding="utf-8"))
    assert {r["name"]: r for r in rep["results"]}["registry"]["status"] == "missed"


@pytest.mark.planted
def test_A21d_setup_and_plant_behaviour(cfg):
    """setup builds 3+3 valid rows; plant flips one character of seq-1 hypothesis in trials.jsonl, hash untouched."""
    reg, Ctx = _check_env()
    setup, plant = reg.plant_of("registry")
    ctx = Ctx(cfg=cfg, run_id="t-plant")
    setup(ctx)
    from investos.registry.ledgers import open_decisions, open_trials
    t, d = open_trials(cfg), open_decisions(cfg)
    assert t.verify().ok and t.verify().rows == 3 and d.verify().ok and d.verify().rows == 3
    assert reg.get("registry")(ctx).ok
    before = json.loads(read_lines(t.path)[0])
    others_before = read_lines(t.path)[1:]
    plant(ctx)
    after = json.loads(read_lines(t.path)[0])
    assert read_lines(t.path)[1:] == others_before
    assert after["hash"] == before["hash"] and after["prev_hash"] == before["prev_hash"]
    assert after["seq"] == before["seq"] and after["ts"] == before["ts"]
    a, b = before["payload"]["hypothesis"], after["payload"]["hypothesis"]
    assert len(a) == len(b) and sum(x != y for x, y in zip(a, b)) == 1
    assert {k: v for k, v in before["payload"].items() if k != "hypothesis"} == \
        {k: v for k, v in after["payload"].items() if k != "hypothesis"}
    res = reg.get("registry")(ctx)
    assert not res.ok and "trials: broken at seq 1" in _text(res)


# ---------- A20h: canonical-form rule (R7 amended) through the registry check ----------

def _nc_space(line):
    return line.replace(b'"seq":', b'"seq": ', 1)


def _nc_reorder(line):
    o = json.loads(line)
    return (json.dumps({k: o[k] for k in ("seq", "ts", "prev_hash", "payload", "hash")},
                       separators=(",", ":"), ensure_ascii=False) + "\n").encode("utf-8")


def _nc_escape(line):
    # Devanagari/accents hypothesis stored with \u escapes while hashes stay valid (payload unchanged in meaning)
    return line.replace("é".encode("utf-8"), b"\\u00e9")


def _nc_trailing(line):
    return line[:-1] + b" \n"


@pytest.mark.planted
@pytest.mark.parametrize("mutate", [
    pytest.param(_nc_space, id="space_after_colon"),
    pytest.param(_nc_reorder, id="reordered_keys"),
    pytest.param(_nc_escape, id="escaped_e_acute"),
    pytest.param(_nc_trailing, id="trailing_space"),
])
def test_A20h_non_canonical_trials_line_fails_registry_check(cfg, mutate):
    from investos.registry.ledgers import open_trials, register_trial
    reg, Ctx = _check_env()
    t = open_trials(cfg, clock=FakeClock())
    register_trial(t, trial_id="t-0", family="f", feature_id="x", params={}, hypothesis="plain")
    register_trial(t, trial_id="t-1", family="f", feature_id="x", params={}, hypothesis="café")
    register_trial(t, trial_id="t-2", family="f", feature_id="x", params={}, hypothesis="plain")
    lines = read_lines(t.path)
    assert "é".encode("utf-8") in lines[1]
    lines[1] = mutate(lines[1])
    assert lines[1] != read_lines(t.path)[1]
    t.path.write_bytes(b"".join(lines))
    assert t.verify(roots=False).first_broken_seq == 2
    res = reg.get("registry")(Ctx(cfg=cfg, run_id="t-nc"))
    assert not res.ok and "trials: broken at seq 2" in _text(res)
    code, out = _run(cfg, ["registry"])
    assert code == 1


# ---- S0-FIX A7(a): the registry check must verify the daily roots (guards audit mutant X12) ----

def test_A7a_registry_check_fails_on_rehashed_forgery_caught_only_by_roots(cfg):
    from investos.registry.chain import _row_hash
    from investos.registry.ledgers import open_trials
    _check_env()
    _seed(cfg)
    t = open_trials(cfg)
    t.write_roots()
    assert t.roots(), "fixture must have roots"
    # forge: rewrite the LAST row's payload and re-hash it so the chain alone is self-consistent
    lines = t.path.read_bytes().splitlines()
    row = json.loads(lines[-1])
    row["payload"]["hypothesis"] = "forged hypothesis text"
    row["hash"] = _row_hash(row["seq"], row["ts"], row["prev_hash"], row["payload"])
    lines[-1] = json.dumps(row, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode()
    t.path.write_bytes(b"\n".join(lines) + b"\n")
    assert open_trials(cfg).verify(roots=False).ok, "forgery must be chain-consistent for this test to mean anything"
    code, out = _run(cfg, ["registry"])
    assert code != 0, "registry check passed a re-hashed forgery although roots exist:\n" + out
