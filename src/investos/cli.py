"""Console entry point `investos` (pyproject [project.scripts])."""
from __future__ import annotations

import sys
from typing import Sequence

USAGE = "usage: investos check [all|manifest|raw|registry|migrations|<name>] [--planted]"


def main(argv: Sequence[str] | None = None) -> int:
    args = list(sys.argv[1:] if argv is None else argv)
    if args and args[0] == "check":
        from investos.check.cli import main as check_main
        return check_main(args[1:])
    print(USAGE)
    return 2


if __name__ == "__main__":
    sys.exit(main())
