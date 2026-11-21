"""Check `registry`: verifies both ledgers read-only, plus its planted failure (spec R13)."""
from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone

from investos.check.registry import CheckContext, CheckResult, check, plant_for

from .canon import canonical_json
from .ledgers import log_decision, open_decisions, open_trials, register_trial


@check("registry")
def check_registry(ctx: CheckContext) -> CheckResult:
    ok_parts: list[str] = []
    bad: list[str] = []
    short: list[str] = []
    for led in (open_trials(ctx.cfg), open_decisions(ctx.cfg)):
        res = led.verify(roots=True)
        if res.ok:
            ok_parts.append(f"{led.kind}: {res.rows} rows OK")
        elif res.first_broken_seq is not None:
            bad.append(f"{led.kind}: broken at seq {res.first_broken_seq}: {res.reason}")
            short.append(f"{led.kind}: broken at seq {res.first_broken_seq}")
        else:
            bad.append(f"{led.kind}: roots file broken: {res.reason}")
            short.append(f"{led.kind}: roots file broken")
    if bad:
        return CheckResult(False, "; ".join(short), tuple(bad))
    return CheckResult(True, "; ".join(ok_parts))


class _StepClock:
    def __init__(self, after: datetime | None = None) -> None:
        self.now = datetime(2026, 1, 1, 10, 0, 0, tzinfo=timezone.utc)
        if after is not None and after + timedelta(days=2) > self.now:
            self.now = after + timedelta(days=2)  # land on a later IST day than any existing row or root

    def __call__(self) -> datetime:
        self.now += timedelta(seconds=1)
        return self.now


def _head_time(led) -> datetime | None:
    head = led.head()
    return datetime.strptime(head.ts, "%Y-%m-%dT%H:%M:%S.%fZ").replace(tzinfo=timezone.utc) if head else None


def _setup(ctx: CheckContext) -> None:
    trials, decisions = open_trials(ctx.cfg), open_decisions(ctx.cfg)
    tclock, dclock = _StepClock(_head_time(trials)), _StepClock(_head_time(decisions))
    trials, decisions = open_trials(ctx.cfg, clock=tclock), open_decisions(ctx.cfg, clock=dclock)
    for i in range(3):
        register_trial(trials, trial_id=f"plant-{ctx.run_id}-{i}", family="plant", feature_id=f"f{i}",
                       params={"threshold": "0.05"}, hypothesis=f"planted hypothesis {i}")
        log_decision(decisions, date="2026-01-01", security_id=f"PLANT{i}", action="hold",
                     reasons=["planted"], model_versions={"plant": "v1"})


@plant_for("registry", setup=_setup, zones={"registry", "ledger"})
def _plant(ctx: CheckContext) -> None:
    """Flip one character of seq 1's hypothesis in the temp copy, leaving its hash untouched."""
    path = open_trials(ctx.cfg).path
    lines = path.read_bytes().split(b"\n")
    obj = json.loads(lines[0])
    h = obj["payload"]["hypothesis"]
    obj["payload"]["hypothesis"] = ("X" if h[0] != "X" else "Y") + h[1:]
    lines[0] = canonical_json(obj).encode("utf-8")
    path.write_bytes(b"\n".join(lines))
