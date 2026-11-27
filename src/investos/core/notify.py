"""Injectable notifier (spec collect_base R33). Default is NullNotifier: nothing ever runs a command unless
a caller deliberately builds the macOS one."""
from __future__ import annotations

import re
import subprocess
import sys
from typing import Protocol

from investos.core import log

__all__ = ["Notifier", "NullNotifier", "OsascriptNotifier"]

_CONTROL_RE = re.compile(r"[\x00-\x1f\x7f]")


class Notifier(Protocol):
    def notify(self, title: str, message: str) -> None: ...


class NullNotifier:
    def notify(self, title: str, message: str) -> None:
        return None


def _quote(s: str) -> str:
    s = _CONTROL_RE.sub("", str(s))
    return s.replace("\\", "\\\\").replace('"', '\\"')


class OsascriptNotifier:
    """macOS notification through `osascript`. Never raises; failures are logged as WARNING notify_failed."""

    def notify(self, title: str, message: str) -> None:
        if sys.platform != "darwin":
            return None
        script = f'display notification "{_quote(message)}" with title "{_quote(title)}"'
        try:
            r = subprocess.run(["osascript", "-e", script], timeout=5, capture_output=True, check=False)
        except Exception as e:                                  # OSError, TimeoutExpired, anything
            log.get_logger("notify").warning("notify_failed", reason=type(e).__name__)
            return None
        if getattr(r, "returncode", 0) != 0:
            log.get_logger("notify").warning("notify_failed", reason=f"exit {r.returncode}")
        return None
