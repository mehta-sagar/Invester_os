"""S0-T6 acceptance tests A6-A9 for investos.registry.ledgers (rules R5, R6)."""
from __future__ import annotations

import copy

import pytest

from _registry_helpers import FakeClock, read_lines


def _trials(cfg, clock=None):
    from investos.registry.ledgers import open_trials
    return open_trials(cfg, clock=clock or FakeClock())


def _decisions(cfg, clock=None):
    from investos.registry.ledgers import open_decisions
    return open_decisions(cfg, clock=clock or FakeClock())


GOOD_TRIAL = {"trial_id": "t-001", "family": "momentum", "feature_id": "mom_12_1",
              "params": {"threshold": "0.05", "lookback": 12}, "hypothesis": "12-1 momentum predicts returns",
              "registered_before_run": True}

GOOD_DECISION = {"date": "2026-11-20", "security_id": "INE002A01018", "action": "buy",
                 "reasons": ["signal above threshold"], "model_versions": {"alpha": "v1"}}


def _nlines(led):
    return len(read_lines(led.path)) if led.path.exists() and led.path.stat().st_size else 0


def test_A6_openers_use_zone_paths_and_kinds(cfg):
    t, d = _trials(cfg), _decisions(cfg)
    assert t.path == cfg.zones["registry"] / "trials.jsonl" and t.kind == "trials"
    assert d.path == cfg.zones["ledger"] / "decisions.jsonl" and d.kind == "decisions"
    assert t.path.with_suffix(".roots.jsonl").name == "trials.roots.jsonl"


def test_A6b_register_trial_row(cfg):
    from investos.registry.ledgers import register_trial
    led = _trials(cfg)
    row = register_trial(led, trial_id="t-001", family="momentum", feature_id="mom_12_1",
                         params={"threshold": "0.05"}, hypothesis="h")
    assert set(row.payload) == {"trial_id", "family", "feature_id", "params", "hypothesis", "registered_before_run"}
    assert row.payload["registered_before_run"] is True
    assert row.seq == 1
    stored = led.rows()[0].payload
    assert stored["params"] == {"threshold": "0.05"} and isinstance(stored["params"]["threshold"], str)
    assert led.verify().ok


def test_A6c_devanagari_hypothesis_roundtrip(cfg):
    from investos.registry.ledgers import register_trial
    led = _trials(cfg)
    register_trial(led, trial_id="t-9", family="f", feature_id="x", params={}, hypothesis="सिग्नल")
    assert "सिग्नल".encode("utf-8") in led.path.read_bytes()
    assert led.rows()[0].payload["hypothesis"] == "सिग्नल" and led.verify().ok


def _bad_trial(**over):
    p = copy.deepcopy(GOOD_TRIAL)
    for k, v in over.items():
        if v is _MISSING:
            p.pop(k)
        else:
            p[k] = v
    return p


_MISSING = object()


@pytest.mark.planted
@pytest.mark.parametrize("payload_fn", [
    pytest.param(lambda: {**_bad_trial(), "extra": 1}, id="extra_key"),
    pytest.param(lambda: _bad_trial(family=_MISSING), id="missing_key"),
    pytest.param(lambda: _bad_trial(registered_before_run=1), id="rbr_int_1"),
    pytest.param(lambda: _bad_trial(registered_before_run="true"), id="rbr_string_true"),
    pytest.param(lambda: _bad_trial(registered_before_run=False), id="rbr_false"),
    pytest.param(lambda: _bad_trial(hypothesis=""), id="empty_hypothesis"),
    pytest.param(lambda: _bad_trial(trial_id=""), id="empty_trial_id"),
    pytest.param(lambda: _bad_trial(family=""), id="empty_family"),
    pytest.param(lambda: _bad_trial(feature_id=""), id="empty_feature_id"),
    pytest.param(lambda: _bad_trial(trial_id=5), id="trial_id_not_str"),
    pytest.param(lambda: _bad_trial(params=[1, 2]), id="params_not_object"),
    pytest.param(lambda: {}, id="empty_payload"),
])
def test_A7_invalid_trial_payload_refused_nothing_written(cfg, payload_fn):
    from investos.registry.chain import PayloadError
    led = _trials(cfg)
    led.append(GOOD_TRIAL | {"trial_id": "t-000"})
    n = _nlines(led)
    before = led.path.read_bytes()
    with pytest.raises(PayloadError):
        led.append(payload_fn())
    assert led.path.read_bytes() == before and _nlines(led) == n


@pytest.mark.planted
def test_A7b_float_in_params_refused(cfg):
    from investos.registry.canon import CanonicalJsonError
    from investos.registry.chain import PayloadError
    from investos.registry.ledgers import register_trial
    led = _trials(cfg)
    with pytest.raises((CanonicalJsonError, PayloadError)):
        register_trial(led, trial_id="t-1", family="f", feature_id="x", params={"threshold": 0.05}, hypothesis="h")
    assert not led.path.exists() or led.path.stat().st_size == 0


@pytest.mark.planted
def test_A7c_duplicate_trial_id_refused(cfg):
    from investos.registry.chain import PayloadError
    from investos.registry.ledgers import register_trial
    led = _trials(cfg)
    register_trial(led, trial_id="t-1", family="f", feature_id="x", params={}, hypothesis="h")
    before = led.path.read_bytes()
    with pytest.raises(PayloadError, match="duplicate trial_id"):
        register_trial(led, trial_id="t-1", family="g", feature_id="y", params={"a": "1"}, hypothesis="other")
    assert led.path.read_bytes() == before
    register_trial(led, trial_id="t-2", family="f", feature_id="x", params={}, hypothesis="h")
    assert [r.payload["trial_id"] for r in led.rows()] == ["t-1", "t-2"]


@pytest.mark.planted
def test_A7d_empty_params_object_allowed(cfg):
    from investos.registry.ledgers import register_trial
    row = register_trial(_trials(cfg), trial_id="t-1", family="f", feature_id="x", params={}, hypothesis="h")
    assert row.payload["params"] == {}


# ---------- decisions ----------

def test_A8_valid_decision_and_same_key_twice(cfg):
    from investos.registry.ledgers import log_decision
    led = _decisions(cfg)
    r1 = log_decision(led, date="2026-11-20", security_id="INE002A01018", action="buy",
                      reasons=["signal"], model_versions={"alpha": "v1"})
    r2 = log_decision(led, date="2026-11-20", security_id="INE002A01018", action="hold",
                      reasons=["correction"], model_versions={"alpha": "v1"})
    assert set(r1.payload) == {"date", "security_id", "action", "reasons", "model_versions"}
    assert [r.payload["action"] for r in led.rows()] == ["buy", "hold"]
    assert r2.seq == 2 and led.verify().ok
    assert list(led.rows()[0].payload["reasons"]) == ["signal"]


@pytest.mark.parametrize("action", ["buy", "sell", "hold", "no_trade"])
def test_A8b_all_actions_accepted(cfg, action):
    from investos.registry.ledgers import log_decision
    row = log_decision(_decisions(cfg), date="2026-11-20", security_id="X", action=action,
                       reasons=["r"], model_versions={"m": "v"})
    assert row.payload["action"] == action


def _dec(**over):
    d = dict(date="2026-11-20", security_id="INE002A01018", action="buy", reasons=["r"], model_versions={"m": "v1"})
    d.update(over)
    return d


@pytest.mark.planted
@pytest.mark.parametrize("kwargs", [
    pytest.param(_dec(action="short"), id="action_short"),
    pytest.param(_dec(action="BUY"), id="action_uppercase"),
    pytest.param(_dec(date="2026-02-30"), id="date_invalid_calendar"),
    pytest.param(_dec(date="21-11-2026"), id="date_wrong_format"),
    pytest.param(_dec(date="2026-1-5"), id="date_not_zero_padded"),
    pytest.param(_dec(reasons=[]), id="reasons_empty_list"),
    pytest.param(_dec(reasons=[""]), id="reasons_empty_string"),
    pytest.param(_dec(security_id=""), id="security_id_empty"),
    pytest.param(_dec(model_versions={"m": 1}), id="model_versions_non_str_value"),
    pytest.param(_dec(model_versions={}), id="buy_with_empty_model_versions"),
    pytest.param(_dec(action="sell", model_versions={}), id="sell_with_empty_model_versions"),
])
def test_A9_invalid_decision_refused_nothing_written(cfg, kwargs):
    from investos.registry.chain import PayloadError
    from investos.registry.ledgers import log_decision
    led = _decisions(cfg)
    log_decision(led, **_dec(security_id="SEED"))
    before = led.path.read_bytes()
    with pytest.raises(PayloadError):
        log_decision(led, **kwargs)
    assert led.path.read_bytes() == before


@pytest.mark.planted
@pytest.mark.parametrize("payload_fn", [
    pytest.param(lambda: {**GOOD_DECISION, "extra": 1}, id="extra_key"),
    pytest.param(lambda: {k: v for k, v in GOOD_DECISION.items() if k != "reasons"}, id="missing_reasons"),
    pytest.param(lambda: {**GOOD_DECISION, "reasons": "signal"}, id="reasons_not_list"),
    pytest.param(lambda: {**GOOD_DECISION, "reasons": ["ok", 3]}, id="reasons_non_str_item"),
    pytest.param(lambda: {}, id="empty_payload"),
])
def test_A9b_invalid_decision_payload_via_append(cfg, payload_fn):
    from investos.registry.chain import PayloadError
    led = _decisions(cfg)
    with pytest.raises(PayloadError):
        led.append(payload_fn())
    assert not led.path.exists() or led.path.stat().st_size == 0


def test_A9c_no_trade_with_empty_model_versions_accepted(cfg):
    from investos.registry.ledgers import log_decision
    row = log_decision(_decisions(cfg), date="2026-11-20", security_id="INE002A01018", action="no_trade",
                       reasons=["market closed"], model_versions={})
    assert row.payload["model_versions"] == {} and row.payload["action"] == "no_trade"


def test_A9d_float_in_decision_is_refused(cfg):
    from investos.registry.canon import CanonicalJsonError
    from investos.registry.chain import PayloadError
    led = _decisions(cfg)
    with pytest.raises((CanonicalJsonError, PayloadError)):
        led.append({**GOOD_DECISION, "model_versions": {"m": 1.5}})
    assert not led.path.exists() or led.path.stat().st_size == 0
