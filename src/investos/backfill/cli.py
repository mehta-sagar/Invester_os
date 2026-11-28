"""`investos backfill probe|plan|run|status` (spec collect_backfill R3, R4, R13, R19).

Exit codes: 0 ran, was nothing to do or the window was closed; 1 refused or failed (bad table, unreadable state,
market hours, error); 2 usage; 3 a block (the backfill stopped for the IST day)."""
from __future__ import annotations

import json
import os
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Sequence

USAGE = ("usage: investos backfill status [--json]\n"
         "       investos backfill plan\n"
         "       investos backfill probe [--outside-window] [--max-requests N]\n"
         "       investos backfill run [--outside-window] [--max-requests N]")

_overrides: dict = {}                       # tests inject sleeper, monotonic, wall_clock, manifest, notifier, clock


def _load_config():
    from investos.core.config import load_config
    if "INVESTOS_ROOT" in os.environ or "INVESTOS_CONFIG" in os.environ:
        return load_config()
    cwd = Path.cwd()
    top = next((d for d in [cwd, *cwd.parents] if (d / "pyproject.toml").is_file()), None)
    return load_config(repo_root=top)


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _make_browser(cfg):
    """The real transport: the API origin is the one the probe recorded, so the redirect alias follows the site."""
    import importlib.util
    from investos.backfill.probe import DEFAULT_API_ORIGIN, load_probe_file
    from investos.backfill.runner import make_transport
    if importlib.util.find_spec("playwright") is None:
        print("investos backfill: playwright is not installed (pip install 'investos[browser]')", file=sys.stderr)
    try:
        origins = {p.api_origin for p in load_probe_file(cfg).values()}
    except Exception:
        origins = set()
    from investos.cli import _install_sigterm_handler
    _install_sigterm_handler()                              # SIGTERM unwinds through the finally that closes Chrome
    return make_transport(cfg, api_origin=origins.pop() if len(origins) == 1 else DEFAULT_API_ORIGIN)


def _flags(args: Sequence[str], allowed: set[str], valued: set[str]) -> dict | None:
    out: dict = {}
    it = iter(args)
    for a in it:
        if a in allowed and a not in out:
            out[a] = True
        elif a in valued and a not in out:
            v = next(it, None)
            if v is None or not v.isdigit() or int(v) < 1:
                return None
            out[a] = int(v)
        else:
            return None
    return out


def main(argv: Sequence[str]) -> int:
    args = list(argv)
    sub = args[0] if args else ""
    specs = {"status": ({"--json"}, set()), "plan": (set(), set()),
             "probe": ({"--outside-window"}, {"--max-requests"}), "run": ({"--outside-window"}, {"--max-requests"})}
    if sub not in specs:
        print(USAGE, file=sys.stderr)
        return 2
    flags = _flags(args[1:], *specs[sub])
    if flags is None:
        print(USAGE, file=sys.stderr)
        return 2
    from investos.backfill.endpoints import EndpointTableError, load_endpoints, load_symbols, symbols_path, table_path
    from investos.backfill.ledger import LedgerError
    from investos.backfill.probe import ProbeError
    from investos.core.config import ConfigError
    try:
        cfg = _load_config()
        now = _now()
        eps = load_endpoints(table_path(cfg.repo_root), today=now.astimezone(_ist()).date())
        symbols = load_symbols(symbols_path(cfg.repo_root)) if symbols_path(cfg.repo_root).exists() else None
        if sub == "status":
            from investos.backfill.status import build_status, format_status
            st = build_status(cfg, eps, now, calendar=_calendar(cfg), symbols=symbols)
            print(json.dumps(st, sort_keys=True) if "--json" in flags else format_status(st).rstrip("\n"))
            return 0
        if sub == "plan":
            return _plan(cfg, eps, now, symbols)
        return _run(sub, cfg, eps, flags, symbols)
    except (ConfigError, EndpointTableError, LedgerError, ProbeError, OSError, ValueError) as e:
        print(f"investos backfill {sub}: {e}", file=sys.stderr)
        return 1


def _ist():
    from zoneinfo import ZoneInfo
    return ZoneInfo("Asia/Kolkata")


def _calendar(cfg):
    from investos.acquire.rss import CalendarView
    return CalendarView(cfg)


def _plan(cfg, eps, now, symbols) -> int:
    from investos.backfill.ledger import WindowLedger
    from investos.backfill.probe import load_probe
    from investos.backfill.runner import make_plan, ordered
    today = now.astimezone(_ist()).date()
    total = 0
    for ep in ordered(eps):
        probe = load_probe(cfg, ep)
        if probe is None or probe.status != "confirmed":
            print(f"{ep.endpoint_id:<18} no confirmed probe: no plan")
            continue
        ws = make_plan(cfg, ep, probe, WindowLedger(cfg, ep.endpoint_id, clock=lambda: now), today,
                       calendar=_calendar(cfg), symbols=symbols)
        total += len(ws)
        span = f"{ws[0].start}..{ws[-1].end}" if ws else "nothing to fetch"
        print(f"{ep.endpoint_id:<18} {len(ws)} windows  {span}")
    print(f"sum: {total} windows")
    return 0


def _run(sub: str, cfg, eps, flags: dict, symbols) -> int:
    from investos.acquire.manifest import ManifestStore
    from investos.backfill.runner import run_backfill, run_probe
    from investos.core.notify import NullNotifier, OsascriptNotifier
    ov = dict(_overrides)
    manifest = ov.pop("manifest", None) or ManifestStore.from_config(cfg)
    notifier = ov.pop("notifier", None) or (OsascriptNotifier() if sys.platform == "darwin" else NullNotifier())
    clock = ov.pop("clock", None) or _now
    browser = _make_browser(cfg)
    kw = dict(browser=browser, manifest=manifest, notifier=notifier, clock=clock, endpoints=eps,
              max_requests=flags.get("--max-requests"), outside_window="--outside-window" in flags, **ov)
    try:
        res = run_probe(cfg, symbols=symbols, **kw) if sub == "probe" else \
            run_backfill(cfg, calendar=_calendar(cfg), symbols=symbols, **kw)
    finally:
        browser.close()
    print(f"{sub}: {res.status}" + (f" ({res.reason})" if res.reason else "") + f"; {res.requests} requests")
    for eid, v in res.per_endpoint.items():
        print(f"  {eid:<18} {v['status']}" + (f": {v['reason']}" if v.get("reason") else ""))
    return res.exit_code
