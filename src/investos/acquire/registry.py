"""Adapter registry (spec collect_rss R21; closes the deferred listing of collect_base R35).

``register_adapter`` is a class decorator. ``adapters()`` imports the fixed list of adapter modules (a later
adapter adds one line to ``_ADAPTER_MODULES``) and returns every registered class.
"""
from __future__ import annotations

import importlib

from investos.acquire.base import Collector

__all__ = ["register_adapter", "adapters", "waived_adapters"]

_ADAPTER_MODULES = ("investos.acquire.rss",)
_REGISTERED: dict[str, type[Collector]] = {}


def register_adapter(cls):
    source = getattr(cls, "source", None)
    if not isinstance(source, str) or not source:
        raise ValueError(f"{cls!r} has no source name")
    have = _REGISTERED.get(source)
    if have is cls:
        return cls
    if have is not None:
        raise ValueError(f"source {source!r} is already registered by {have.__name__}")
    _REGISTERED[source] = cls
    return cls


def adapters() -> tuple[type[Collector], ...]:
    for name in _ADAPTER_MODULES:
        mod = importlib.import_module(name)
        for cls in getattr(mod, "ADAPTERS", ()):                 # idempotent: survives a registry reset
            register_adapter(cls)
    return tuple(_REGISTERED.values())


def waived_adapters() -> list[tuple[str, str]]:
    out = []
    for cls in adapters():
        waiver = getattr(cls, "date_check_waiver", None)
        if getattr(cls, "date_keyed", False) and isinstance(waiver, str) and waiver.strip():
            out.append((cls.source, waiver))
    return out
