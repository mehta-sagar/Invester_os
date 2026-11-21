"""JSON-lines structured logging (spec core_config_log_db R9-R12)."""
from __future__ import annotations

import json
import math
import secrets
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, TextIO

_LEVELS = {"DEBUG": 10, "INFO": 20, "WARNING": 30, "ERROR": 40}

_state: dict[str, Any] = {
    "sink": None,          # TextIO | None (None -> sys.stderr at write time)
    "level": "INFO",
    "clock": None,
    "run_id": None,
    "owned": False,
}


def get_run_id() -> str:
    if _state["run_id"] is None:
        now = datetime.now(timezone.utc)
        _state["run_id"] = f"{now:%Y%m%dT%H%M%SZ}-{secrets.token_hex(4)}"
    return _state["run_id"]


def configure_logging(sink: TextIO | Path | None = None, *, level: str = "INFO",
                      clock: Callable[[], datetime] | None = None, run_id: str | None = None) -> None:
    if level not in _LEVELS:
        raise ValueError(f"unknown log level {level!r}; expected one of {list(_LEVELS)}")
    owned = False
    if isinstance(sink, (str, Path)):
        sink = open(Path(sink), "a", encoding="utf-8")
        owned = True
    if _state["owned"] and _state["sink"] is not None:
        _state["sink"].close()
    _state.update(sink=sink, owned=owned, level=level, clock=clock)
    if run_id is not None:
        _state["run_id"] = run_id


def _ts() -> str:
    clock = _state["clock"]
    dt = clock() if clock is not None else datetime.now(timezone.utc)
    if dt.tzinfo is None or dt.utcoffset() is None:
        raise ValueError("log clock must return an aware datetime")
    return dt.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%fZ")


def _check_finite(value: Any, _depth: int = 0) -> None:
    if _depth > 50:
        return
    if isinstance(value, float) and not math.isfinite(value):
        raise ValueError(f"non-finite float {value!r} is not valid JSON")
    if isinstance(value, dict):
        for v in value.values():
            _check_finite(v, _depth + 1)
    elif isinstance(value, (list, tuple)):
        for v in value:
            _check_finite(v, _depth + 1)


def _safe(value: Any) -> Any:
    _check_finite(value)
    try:
        json.dumps(value, default=repr, ensure_ascii=False, allow_nan=False)
        return value
    except (TypeError, ValueError, RecursionError):
        return repr(value)


class TaskLogger:
    def __init__(self, task: str) -> None:
        self.task = task

    def _emit(self, level: str, event: str, fields: dict[str, Any]) -> None:
        if _LEVELS[level] < _LEVELS[_state["level"]]:
            return
        rec = {
            "ts": _ts(),
            "level": level,
            "run_id": get_run_id(),
            "task": self.task,
            "event": event if isinstance(event, str) else repr(event),
            "fields": {k: _safe(v) for k, v in fields.items()},
        }
        line = json.dumps(rec, separators=(",", ":"), ensure_ascii=False, default=repr,
                          allow_nan=False) + "\n"
        sink = _state["sink"] or sys.stderr
        sink.write(line)
        sink.flush()

    def debug(self, event: str, /, **fields: Any) -> None:
        self._emit("DEBUG", event, fields)

    def info(self, event: str, /, **fields: Any) -> None:
        self._emit("INFO", event, fields)

    def warning(self, event: str, /, **fields: Any) -> None:
        self._emit("WARNING", event, fields)

    def error(self, event: str, /, **fields: Any) -> None:
        self._emit("ERROR", event, fields)


def get_logger(task: str) -> TaskLogger:
    return TaskLogger(task)
