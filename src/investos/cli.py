"""Console entry point `investos` (pyproject [project.scripts])."""
from __future__ import annotations

import sys
from typing import Sequence

USAGE = ("usage: investos check [all|manifest|raw|registry|migrations|<name>] [--planted]\n"
         "       investos access status [source] | clear <source> --decision <id>\n"
         "       investos routine roots [--dry-run] [--branch NAME] [--remote NAME]\n"
         "       investos collect rss --once [--force] [--dry-run] [--headless]\n"
         "       investos backfill status [--json] | plan | probe | run [--outside-window] [--max-requests N]")


def main(argv: Sequence[str] | None = None) -> int:
    args = list(sys.argv[1:] if argv is None else argv)
    if args and args[0] == "check":
        from investos.check.cli import main as check_main
        return check_main(args[1:])
    if args and args[0] == "access":
        from investos.acquire.access_cli import main as access_main
        return access_main(args[1:])
    if args and args[0] == "routine":
        return _routine(args[1:])
    if args and args[0] == "collect":
        return _collect(args[1:])
    if args and args[0] == "backfill":
        from investos.backfill.cli import main as backfill_main
        return backfill_main(args[1:])
    print(USAGE)
    return 2


def _routine(args: list[str]) -> int:
    import argparse
    import json
    import os
    from pathlib import Path

    class _Parser(argparse.ArgumentParser):
        def error(self, message):                            # bad flags exit 1, not argparse's 2
            raise ValueError(message)

    if not args or args[0] != "roots":
        print("usage: investos routine roots [--dry-run] [--branch NAME] [--remote NAME]", file=sys.stderr)
        return 1
    parser = _Parser(prog="investos routine roots", add_help=False)
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--branch", default="main")
    parser.add_argument("--remote", default="origin")
    try:
        ns = parser.parse_args(args[1:])
    except ValueError as e:
        print(f"investos routine roots: {e}", file=sys.stderr)
        return 1
    from investos.core.config import ConfigError, load_config
    from investos.core.notify import NullNotifier, OsascriptNotifier
    from investos.routine import roots
    try:
        if "INVESTOS_ROOT" in os.environ or "INVESTOS_CONFIG" in os.environ:
            cfg = load_config()
        else:
            cwd = Path.cwd()
            top = next((d for d in [cwd, *cwd.parents] if (d / "pyproject.toml").is_file()), None)
            cfg = load_config(repo_root=top)
    except ConfigError as e:
        print(f"investos routine roots: {e}", file=sys.stderr)
        return 1
    notifier = OsascriptNotifier() if sys.platform == "darwin" else NullNotifier()
    res = roots.run_routine(cfg, git=roots.Git(cfg.repo_root), branch=ns.branch, remote=ns.remote,
                            dry_run=ns.dry_run, notifier=notifier)
    if ns.dry_run:
        for p in res.added:
            print(f"would stage: {p}")
    if res.exit_code != 0:
        print(roots._redact(roots._first_line(res.message)), file=sys.stderr)
    print(json.dumps(roots.result_json(res, ns.branch), sort_keys=True, separators=(",", ":")))
    return int(res.exit_code)


def _on_sigterm(signum, frame) -> None:
    raise KeyboardInterrupt("SIGTERM")                      # unwinds through the finally that closes Chrome


def _install_sigterm_handler() -> None:
    import signal
    try:
        signal.signal(signal.SIGTERM, _on_sigterm)
    except ValueError:                                       # not the main thread
        pass


COLLECT_USAGE = "usage: investos collect rss --once [--force] [--dry-run] [--headless]"


def _collect(args: list[str]) -> int:
    """`investos collect rss --once [--force] [--dry-run] [--headless]` (spec collect_rss R18, collect_browser R9, R10).
    Exit 0 ran or nothing due, 1 start refused (contact, config, access state, gap ledger), 2 usage error or invalid
    feed table. ``--headless`` only changes how Chrome is started for the NSE feeds; the default is headful."""
    if not args or args[0] != "rss":
        print(COLLECT_USAGE)
        return 2
    flags = set(args[1:])
    if not flags <= {"--once", "--force", "--dry-run", "--headless"} or len(args[1:]) != len(flags) or "--once" not in flags:
        print(COLLECT_USAGE)
        return 2
    force, dry, headless = "--force" in flags, "--dry-run" in flags, "--headless" in flags
    import os
    from datetime import datetime, timezone
    from pathlib import Path
    from types import SimpleNamespace
    from investos.core.config import ConfigError, load_config
    try:
        if "INVESTOS_ROOT" in os.environ or "INVESTOS_CONFIG" in os.environ:
            cfg = load_config()
        else:
            cwd = Path.cwd()
            top = next((d for d in [cwd, *cwd.parents] if (d / "pyproject.toml").is_file()), None)
            cfg = load_config(repo_root=top)
    except ConfigError as e:
        print(f"investos collect rss: {e}", file=sys.stderr)
        return 1
    from investos.acquire import rss
    from investos.acquire.access import AccessState, AccessStateError
    from investos.acquire.base import AccessMode, _check_contact
    try:                                                     # before any data is touched or a transport built
        _check_contact(cfg.collect.contact, SimpleNamespace(real_network=True))
    except ValueError as e:
        print(f"investos collect rss: {e}", file=sys.stderr)
        return 1
    now = datetime.now(timezone.utc)
    try:
        AccessState(cfg, "rss", clock=lambda: now).events()
    except AccessStateError as e:
        print(f"investos collect rss: access state unreadable: {e}", file=sys.stderr)
        return 1
    try:
        feeds = rss.load_feeds(rss.feed_table_path(cfg))
    except rss.FeedTableError as e:
        print(f"investos collect rss: {e}", file=sys.stderr)
        return 2
    slot = rss.slot_of(now)
    if dry:
        due = rss.due_feeds(cfg, feeds, slot)
        scheduled = force or rss.is_poll_slot(slot)
        print(f"slot {slot}" + ("" if scheduled else " (not a scheduled slot; use --force)"))
        for f in (due if scheduled else []):
            print(f"due {f.feed_id}")
        return 0
    from investos.acquire.manifest import ManifestStore
    from investos.acquire.transport import UrllibTransport
    from investos.core.notify import NullNotifier, OsascriptNotifier
    transport = UrllibTransport(max_body_bytes=cfg.collect.max_body_bytes)
    notifier = OsascriptNotifier() if sys.platform == "darwin" else NullNotifier()
    browser = None
    if any(f.enabled and rss.collector_class_for(f).access_mode is AccessMode.REAL_BROWSER for f in feeds):
        import importlib.util
        if importlib.util.find_spec("playwright") is None:
            print("investos collect rss: playwright is not installed (pip install 'investos[browser]'); "
                  "the NSE feeds cannot be read", file=sys.stderr)
        from investos.acquire.browser import RealBrowserTransport
        browser = RealBrowserTransport(cfg, headless=headless)
    try:
        if browser is not None:
            _install_sigterm_handler()
        res = rss.poll_cycle(cfg, now=now, transport=transport, manifest=ManifestStore.from_config(cfg),
                             notifier=notifier, browser=browser, force=force, feeds=feeds,
                             clock=lambda: datetime.now(timezone.utc))
    finally:
        if browser is not None:
            browser.close()
    if res.gap_write_failed:
        print("investos collect rss: gap_write_failed: the gap ledger could not be written", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
