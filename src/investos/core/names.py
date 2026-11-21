"""Shared name rules. One definition of a source name (specs/core_manifest.md)."""
from __future__ import annotations

import re

SOURCE_RE = re.compile(r"[a-z0-9][a-z0-9_]{0,63}", re.ASCII)
