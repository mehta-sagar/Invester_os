"""Console entry point `investos` (pyproject [project.scripts])."""
from __future__ import annotations

import sys
from typing import Sequence

USAGE = ("usage: investos check [all|manifest|raw|registry|migrations|<name>] [--planted]\n"
         "       investos access status [source] | clear <source> --decision <id>\n"
         "       investos routine roots [--dry-run] [--branch NAME] [--remote NAME]")


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


if __name__ == "__main__":
    sys.exit(main())
