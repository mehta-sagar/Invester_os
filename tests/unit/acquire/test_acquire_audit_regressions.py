"""S0-FIX A7(b): ManifestStore index must see appends made by ANOTHER store instance / process."""
from __future__ import annotations

import subprocess
import sys
import textwrap
from pathlib import Path

from _acq_helpers import FETCHED, put

SRC = str(Path(__file__).resolve().parents[3] / "src")


def test_A7b_other_store_instance_append_is_seen(mk, tmp_path):
    a = mk()
    put(a, "bse", b"one", logical_key="k1")
    assert a.get_by_key("bse", "k1") is not None        # populates A's cached index
    assert a.get_by_key("bse", "k2") is None
    b = mk()                                             # same roots, different instance
    e2 = put(b, "bse", b"two", logical_key="k2")
    got = a.get_by_key("bse", "k2")
    assert got is not None and got.sha256 == e2.sha256, "A's cached index went stale after B appended"
    # A's own later append after B's still keeps the index consistent
    put(a, "bse", b"three", logical_key="k3")
    assert b.get_by_key("bse", "k3") is not None
    assert a.get_by_key("bse", "k2") is not None


def test_A7b_other_store_same_size_rewrite_of_latest_wins(mk):
    a = mk()
    put(a, "bse", b"one", logical_key="k1")
    first = a.get_by_key("bse", "k1")
    b = mk()
    put(b, "bse", b"uno", logical_key="k1")              # same key, new bytes: latest wins (R9)
    now = a.get_by_key("bse", "k1")
    assert now.sha256 != first.sha256


def test_A7b_other_process_append_is_seen(mk, roots):
    raw, man = roots
    a = mk()
    put(a, "bse", b"one", logical_key="k1")
    assert a.get_by_key("bse", "k1") is not None
    assert a.get_by_key("bse", "from-child") is None
    code = textwrap.dedent(f"""
        import sys; sys.path.insert(0, {SRC!r})
        from datetime import datetime, timezone
        from pathlib import Path
        from investos.acquire.manifest import ManifestStore
        st = ManifestStore(Path({str(raw)!r}), Path({str(man)!r}))
        st.store("bse", b"child-bytes", url="u", source_version="v1", logical_key="from-child",
                 run_id="run-2", fetched_at=datetime(2026, 11, 21, tzinfo=timezone.utc))
    """)
    subprocess.run([sys.executable, "-I", "-c", code], check=True)
    assert a.get_by_key("bse", "from-child") is not None, "index stale after another process appended"


def test_A7b_own_append_after_foreign_append_does_not_hide_foreign_entry(mk):
    """Audit mutant X1: A extends its cached index on its own append even though B appended in between."""
    a = mk()
    put(a, "bse", b"one", logical_key="k1")
    assert a.get_by_key("bse", "k1") is not None            # A caches the index
    b = mk()
    put(b, "bse", b"two", logical_key="k2")                 # B appends; A not told
    put(a, "bse", b"three", logical_key="k3")               # A appends without looking up first
    assert a.get_by_key("bse", "k2") is not None, "A's cache missed B's entry (own append extended a stale index)"
    assert a.get_by_key("bse", "k3") is not None
