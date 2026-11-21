"""Check registry: @check(name) and @plant_for(name, setup=...) decorators (spec R1)."""
from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Callable

from investos.core.config import Config

_NAME_RE = re.compile(r"[a-z][a-z0-9_]{0,31}", re.ASCII)
RESERVED = frozenset({"all"})


class RegistryError(Exception):
    pass


@dataclass(frozen=True)
class CheckContext:
    cfg: Config
    run_id: str


@dataclass(frozen=True)
class CheckResult:
    ok: bool
    message: str
    details: tuple[str, ...] = ()


CheckFn = Callable[[CheckContext], CheckResult]
SetupFn = Callable[[CheckContext], None]
PlantFn = Callable[[CheckContext], None]


class CheckRegistry:
    def __init__(self) -> None:
        self._checks: dict[str, CheckFn] = {}
        self._plants: dict[str, tuple[SetupFn | None, PlantFn]] = {}

    def check(self, name: str) -> Callable[[CheckFn], CheckFn]:
        if not isinstance(name, str) or not _NAME_RE.fullmatch(name):
            raise RegistryError(f"invalid check name {name!r}: must match [a-z][a-z0-9_]{{0,31}}")
        if name in RESERVED:
            raise RegistryError(f"check name {name!r} is reserved")

        def deco(fn: CheckFn) -> CheckFn:
            if not callable(fn):
                raise RegistryError(f"check {name!r}: not callable")
            if name in self._checks:
                raise RegistryError(f"check {name!r} is already registered")
            self._checks[name] = fn
            return fn
        return deco

    def plant_for(self, name: str, *, setup: SetupFn | None = None) -> Callable[[PlantFn], PlantFn]:
        def deco(fn: PlantFn) -> PlantFn:
            if not callable(fn) or (setup is not None and not callable(setup)):
                raise RegistryError(f"plant for {name!r}: plant and setup must be callable")
            if name not in self._checks:
                raise RegistryError(f"plant for unregistered check {name!r}")
            if name in self._plants:
                raise RegistryError(f"check {name!r} already has a plant")
            self._plants[name] = (setup, fn)
            return fn
        return deco

    def names(self) -> list[str]:
        return list(self._checks)

    def get(self, name: str) -> CheckFn:
        try:
            return self._checks[name]
        except KeyError:
            raise RegistryError(f"unknown check {name!r}") from None

    def plant_of(self, name: str) -> tuple[SetupFn | None, PlantFn] | None:
        return self._plants.get(name)


DEFAULT_REGISTRY = CheckRegistry()
check = DEFAULT_REGISTRY.check
plant_for = DEFAULT_REGISTRY.plant_for
