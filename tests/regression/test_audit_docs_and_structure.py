"""S0-FIX A8 (runbook exists) and A16 (single definition of the source-name regex)."""
from __future__ import annotations

import ast
import re
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
SRC = REPO / "src" / "investos"
PATTERN = "[a-z0-9][a-z0-9_]{0,63}"


# ---------------------------------------------------------------- A8
def test_A8_torn_tail_runbook_exists_and_names_the_steps():
    p = REPO / "docs" / "runbooks" / "manifest-torn-tail.md"
    assert p.is_file(), "docs/runbooks/manifest-torn-tail.md missing"
    t = p.read_text().lower()
    for needle, why in [("torn", "names the problem"), (".jsonl.lock", "take the lock"),
                        ("stop", "stop collectors first"), ("truncat", "truncate the fragment"),
                        ("unterminated", "only an unterminated final fragment"),
                        ("investos check", "verify afterwards"), ("decision", "record a decision row"),
                        ("copy", "copy the file aside before touching it"),
                        ("ledger", "ledger tail (trials/decisions) covered")]:
        assert needle in t, f"runbook must mention {needle!r} ({why})"
    assert len(t) > 600


# ---------------------------------------------------------------- A16
def _trees():
    for f in SRC.rglob("*.py"):
        yield f, ast.parse(f.read_text())


def _definitions():
    out = []
    for f, tree in _trees():
        for n in ast.walk(tree):
            if isinstance(n, ast.Constant) and isinstance(n.value, str) and PATTERN in n.value:
                out.append(f)
    return out


def _imported_modules(f: Path, tree) -> set[str]:
    pkg = ".".join(f.relative_to(SRC.parent).with_suffix("").parts[:-1])
    mods = set()
    for n in ast.walk(tree):
        if isinstance(n, ast.ImportFrom):
            base = n.module or ""
            if n.level:
                parts = pkg.split(".")[: len(pkg.split(".")) - (n.level - 1)]
                base = ".".join(parts + ([n.module] if n.module else []))
            mods.add(base)
            for a in n.names:
                mods.add(f"{base}.{a.name}")
        elif isinstance(n, ast.Import):
            mods.update(a.name for a in n.names)
    return mods


def test_A16_source_regex_defined_exactly_once_in_core():
    defs = _definitions()
    assert len(defs) == 1, f"source-name pattern defined in {[str(d.relative_to(REPO)) for d in defs]}"
    assert defs[0].parent == SRC / "core", f"must live in investos.core, found {defs[0]}"


def test_A16_manifest_base_and_builtin_import_it_from_that_place():
    (home,) = _definitions()
    home_mod = ".".join(home.relative_to(SRC.parent).with_suffix("").parts)
    for rel in ("acquire/manifest.py", "acquire/base.py", "check/builtin.py"):
        f = SRC / rel
        mods = _imported_modules(f, ast.parse(f.read_text()))
        assert home_mod in mods, f"{rel} does not import from {home_mod}"
        assert not re.search(re.escape(PATTERN), f.read_text()), f"{rel} still carries its own copy"
