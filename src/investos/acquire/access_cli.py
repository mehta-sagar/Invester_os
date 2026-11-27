"""`investos access status|clear` and the shared collector factory (spec collect_base R20, R26, R33; A46, A58)."""
from __future__ import annotations

import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Sequence

from investos.acquire import access as acc
from investos.acquire.access import AccessState, AccessStateError, clear_halt
from investos.core import log
from investos.core.config import Config, ConfigError, load_config
from investos.core.names import SOURCE_RE

USAGE = ("usage: investos access status [source]\n"
         "       investos access clear <source> --decision <id>")


def _notifier():
    """macOS alert on a halt; a no-op notifier everywhere else."""
    from investos.core.notify import NullNotifier, OsascriptNotifier
    return OsascriptNotifier() if sys.platform == "darwin" else NullNotifier()


def build_collector(cfg: Config, cls, *, browser=None, manifest=None, notifier=None, **kw):
    """Build a collector for production use. Refuses (exit 1) before touching any data or building a transport
    while [collect] contact is empty."""
    if not cfg.collect.contact:
        print("collect.contact is not configured", file=sys.stderr)
        raise SystemExit(1)
    from investos.acquire.base import AccessMode
    from investos.acquire.manifest import ManifestStore
    from investos.acquire.transport import UrllibTransport
    transport = None
    if getattr(cls, "access_mode", AccessMode.HONEST) is not AccessMode.REAL_BROWSER:
        transport = UrllibTransport(max_body_bytes=cfg.collect.max_body_bytes)
    store = manifest if manifest is not None else ManifestStore(cfg.zones["raw"], cfg.zones["manifests"])
    return cls(transport=transport, browser=browser, manifest=store, cfg=cfg,
               notifier=notifier if notifier is not None else _notifier(), **kw)


def _clock() -> datetime:
    return datetime.now(timezone.utc)


def _known_sources(cfg: Config) -> list[str]:
    root = Path(cfg.zones["ledger"]) / "access"
    names = set(cfg.collect.access)
    if root.is_dir():
        names |= {p.stem for p in root.glob("*.jsonl") if SOURCE_RE.fullmatch(p.stem)}
        hosts = root / "hosts"
        if hosts.is_dir():
            for p in hosts.glob("*.jsonl"):
                names |= {e.source for e in acc._parse_file(p, "host", None)}
    return sorted(names)


def _status(cfg: Config, sources: list[str]) -> None:
    today = acc.ist_date(_clock())
    for src in sources:
        st = AccessState(cfg, src, clock=_clock)
        events = st.events()
        halted = acc._latest_halt_state([e for e in events if e.event in ("halted", "cleared")]) is not None
        blocked_hosts = sorted({e.host for e in events if e.event == "blocked" and e.date == today and e.host})
        print(f"{src}: blocked_today={'yes' if blocked_hosts else 'no'}"
              f"{' (' + ', '.join(blocked_hosts) + ')' if blocked_hosts else ''} halted={'yes' if halted else 'no'}")
        for e in events[-5:]:
            print(f"  {e.ts} {e.event} host={e.host or '-'} {e.detail}")


def main(args: Sequence[str]) -> int:
    args = list(args)
    if not args or args[0] not in ("status", "clear"):
        print(USAGE)
        return 2
    cmd, rest = args[0], args[1:]
    if cmd == "status":
        if len(rest) > 1 or (rest and not SOURCE_RE.fullmatch(rest[0])):
            print(USAGE)
            return 2
    else:
        if len(rest) != 3 or rest[1] != "--decision" or not SOURCE_RE.fullmatch(rest[0]):
            print(USAGE)
            return 2
    try:
        cfg = load_config()
        if cmd == "status":
            _status(cfg, rest if rest else _known_sources(cfg))
            return 0
        ev = clear_halt(cfg, rest[0], decision=rest[2], clock=_clock, run_id=log.get_run_id())
        print(f"{ev.source}: halt cleared by decision {ev.decision}")
        return 0
    except (ConfigError, AccessStateError, ValueError) as e:
        print(f"error: {e}", file=sys.stderr)
        return 1
