"""Trial registry and decision log: schemas and openers (spec registry_chain R5, R6)."""
from __future__ import annotations

from datetime import date as _date
from datetime import datetime
from typing import Any, Callable, Mapping, Sequence

from investos.core.config import Config

from .chain import Ledger, PayloadError

_TRIAL_KEYS = frozenset({"trial_id", "family", "feature_id", "params", "hypothesis", "registered_before_run"})
_DECISION_KEYS = frozenset({"date", "security_id", "action", "reasons", "model_versions"})
ACTIONS = frozenset({"buy", "sell", "hold", "no_trade"})


def _nonempty_str(payload: Mapping, key: str) -> None:
    v = payload[key]
    if type(v) is not str or not v:
        raise PayloadError(f"{key} must be a non-empty string")


def validate_trial(payload: Mapping[str, Any], ledger: Ledger) -> None:
    if type(payload) is not dict or set(payload) != _TRIAL_KEYS:
        raise PayloadError(f"trials payload must have exactly keys {sorted(_TRIAL_KEYS)}")
    for k in ("trial_id", "family", "feature_id", "hypothesis"):
        _nonempty_str(payload, k)
    if type(payload["params"]) is not dict:
        raise PayloadError("params must be an object")
    if payload["registered_before_run"] is not True:
        raise PayloadError("registered_before_run must be true")
    if any(r.payload["trial_id"] == payload["trial_id"] for r in ledger.rows()):
        raise PayloadError("duplicate trial_id")


def validate_decision(payload: Mapping[str, Any], ledger: Ledger) -> None:
    if type(payload) is not dict or set(payload) != _DECISION_KEYS:
        raise PayloadError(f"decisions payload must have exactly keys {sorted(_DECISION_KEYS)}")
    d = payload["date"]
    try:
        ok = type(d) is str and _date.fromisoformat(d).isoformat() == d
    except ValueError:
        ok = False
    if not ok:
        raise PayloadError("date must be a valid YYYY-MM-DD calendar date")
    _nonempty_str(payload, "security_id")
    if payload["action"] not in ACTIONS or type(payload["action"]) is not str:
        raise PayloadError(f"action must be one of {sorted(ACTIONS)}")
    reasons = payload["reasons"]
    if type(reasons) is not list or not reasons or any(type(r) is not str or not r for r in reasons):
        raise PayloadError("reasons must be a non-empty list of non-empty strings")
    mv = payload["model_versions"]
    if type(mv) is not dict or any(type(k) is not str or type(v) is not str for k, v in mv.items()):
        raise PayloadError("model_versions must be an object of str to str")
    if not mv and payload["action"] != "no_trade":
        raise PayloadError("model_versions may be empty only for no_trade")


def open_trials(cfg: Config, *, clock: Callable[[], datetime] | None = None) -> Ledger:
    return Ledger(cfg.zones["registry"] / "trials.jsonl", kind="trials", validate=validate_trial, clock=clock)


def open_decisions(cfg: Config, *, clock: Callable[[], datetime] | None = None) -> Ledger:
    return Ledger(cfg.zones["ledger"] / "decisions.jsonl", kind="decisions", validate=validate_decision,
                  clock=clock)


def register_trial(ledger: Ledger, *, trial_id: str, family: str, feature_id: str,
                   params: Mapping[str, Any], hypothesis: str):
    return ledger.append({"trial_id": trial_id, "family": family, "feature_id": feature_id,
                          "params": dict(params) if isinstance(params, Mapping) else params,
                          "hypothesis": hypothesis, "registered_before_run": True})


def log_decision(ledger: Ledger, *, date: str, security_id: str, action: str,
                 reasons: Sequence[str], model_versions: Mapping[str, str]):
    return ledger.append({"date": date, "security_id": security_id, "action": action,
                          "reasons": list(reasons), "model_versions": dict(model_versions)})
