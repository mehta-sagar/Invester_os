"""`investos check [TARGET] [--planted]` (spec CLI). Exit: 0 ok, 1 failure, 2 usage/config error."""
from __future__ import annotations

import importlib
import sys
from typing import Sequence, TextIO

from investos.core.config import Config, ConfigError, load_config
from investos.core.log import get_run_id

from .registry import DEFAULT_REGISTRY, CheckContext, CheckRegistry, RegistryError
from .runner import Report, run_checks, run_planted, write_report

USAGE = "usage: investos check [all|<check name>] [--planted]"


def load_builtin_checks() -> None:
    """Import the built-in checks; investos.registry.check only if it exists (S0-T6)."""
    importlib.import_module("investos.check.builtin")
    try:
        importlib.import_module("investos.registry.check")
    except ModuleNotFoundError as e:
        if not (e.name or "").startswith("investos.registry"):
            raise


def format_table(report: Report) -> str:
    planted = report.mode == "planted"
    rows = []
    for r in report.results:
        msg = r.message
        if not planted and r.plant == "missing":
            msg = (msg + " plant: MISSING").strip()
        rows.append((r.name, r.status, str(r.duration_ms), msg))
    head = ("NAME", "STATUS", "MS", "MESSAGE")
    w = [max(len(head[i]), *(len(x[i]) for x in rows)) if rows else len(head[i]) for i in range(3)]
    lines = [f"{head[0]:<{w[0]}}  {head[1]:<{w[1]}}  {head[2]:>{w[2]}}  {head[3]}"]
    for n, s, ms, m in rows:
        lines.append(f"{n:<{w[0]}}  {s:<{w[1]}}  {ms:>{w[2]}}  {m}".rstrip())
    bad = sum(1 for r in report.results if r.status != ("caught" if planted else "pass"))
    lines.append("OK" if bad == 0 else f"FAILED ({bad})")
    return "\n".join(lines) + "\n"


def main(argv: Sequence[str] | None = None, *, registry: CheckRegistry | None = None,
         cfg: Config | None = None, stdout: TextIO | None = None) -> int:
    out = stdout if stdout is not None else sys.stdout
    args = list(sys.argv[1:] if argv is None else argv)

    def say(s: str) -> None:
        out.write(s if s.endswith("\n") else s + "\n")

    planted = False
    positional: list[str] = []
    for a in args:
        if a == "--planted":
            planted = True
        elif a.startswith("-"):
            say(f"unknown option {a}\n{USAGE}")
            return 2
        else:
            positional.append(a)
    if len(positional) > 1:
        say(f"expected at most one target, got {len(positional)}: {' '.join(positional)}\n{USAGE}")
        return 2

    if registry is None:
        load_builtin_checks()
        registry = DEFAULT_REGISTRY
    names = registry.names()
    if not names:
        say("no checks registered")
        return 2
    target = positional[0] if positional else "all"
    if target != "all" and target not in names:
        say(f"unknown check {target!r}; registered checks: {', '.join(names)}")
        return 2
    selected = names if target == "all" else [target]

    try:
        config = cfg if cfg is not None else load_config()
    except ConfigError as e:
        say(f"config error: {e}")
        return 2

    try:
        if planted:
            report = run_planted(registry, config, selected)
        else:
            report = run_checks(registry, CheckContext(config, get_run_id()), selected)
    except RegistryError as e:
        say(f"registry error: {e}")
        return 2
    out.write(format_table(report))
    try:
        path = write_report(report, config)
    except OSError as e:
        say(f"cannot write report: {e}")
        return 2
    print(f"report: {path}", file=sys.stderr)
    return 0 if report.ok else 1
