"""Canonical JSON: the one serialisation rule for hashed data (spec registry_chain R1)."""
from __future__ import annotations

import hashlib
import json
from typing import Any

MAX_DEPTH = 64
MAX_INT = 2**53 - 1


class CanonicalJsonError(ValueError):
    pass


def _check_str(s: str, what: str) -> None:
    try:
        s.encode("utf-8")
    except UnicodeEncodeError:
        raise CanonicalJsonError(f"{what} is not encodable as UTF-8") from None


def _validate(obj: Any, depth: int) -> None:
    t = type(obj)
    if obj is None or t is bool:
        return
    if t is int:
        if abs(obj) > MAX_INT:
            raise CanonicalJsonError(f"integer {obj} outside +-(2**53-1)")
        return
    if t is str:
        _check_str(obj, "string")
        return
    if t is dict:
        if depth > MAX_DEPTH:
            raise CanonicalJsonError(f"nesting deeper than {MAX_DEPTH}")
        for k, v in obj.items():
            if type(k) is not str:
                raise CanonicalJsonError(f"dict key {k!r} is not a str")
            _check_str(k, "dict key")
            _validate(v, depth + 1)
        return
    if t is list:
        if depth > MAX_DEPTH:
            raise CanonicalJsonError(f"nesting deeper than {MAX_DEPTH}")
        for v in obj:
            _validate(v, depth + 1)
        return
    raise CanonicalJsonError(f"type {t.__name__} is not allowed in hashed data")


def canonical_json(obj: Any) -> str:
    _validate(obj, 1)
    return json.dumps(obj, sort_keys=True, separators=(",", ":"), ensure_ascii=False, allow_nan=False)


def sha256_hex(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()
