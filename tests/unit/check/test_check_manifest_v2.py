"""S1-T2 A33 (R18): `investos check manifest` on mixed v1/v2 stores."""
from __future__ import annotations

import json

import pytest

from _chk_helpers import put


def _v1_line(data: bytes, key: str, source="bse") -> bytes:
    import hashlib
    o = {"sha256": hashlib.sha256(data).hexdigest(), "size": len(data), "url": "https://example.test/f",
         "fetched_at": "2026-11-21T09:30:00.000000Z", "source": source, "source_version": "v1",
         "logical_key": key, "run_id": "run-1"}
    return (json.dumps(o, sort_keys=True, separators=(",", ":"), ensure_ascii=False) + "\n").encode()


@pytest.fixture
def mixed(healthy):
    """healthy store (v2 lines) plus a hand-added, valid v1 line for an existing raw file."""
    mf = healthy.zones["manifests"] / "bse.jsonl"
    with open(mf, "ab") as fh:
        fh.write(_v1_line(b"alpha-bytes", "legacy-key"))
    return healthy


def test_A33_check_manifest_passes_on_mixed_store(mixed, run):
    code, out = run(["manifest"])
    assert code == 0, out


@pytest.mark.planted
def test_A33_check_manifest_fails_on_raw_cookie_and_names_line(mixed, run):
    mf = mixed.zones["manifests"] / "bse.jsonl"
    lines = mf.read_bytes().splitlines()
    obj = json.loads(lines[0])
    assert obj["schema"] == 2
    obj["request_headers"] = {"Cookie": "nsit=SECRET1", "User-Agent": "u"}
    bad = json.dumps(obj, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode()
    n_before = len(lines)
    mf.write_bytes(b"\n".join(lines) + b"\n" + bad + b"\n")
    code, out = run(["manifest"])
    assert code == 1, out
    assert "bad_line" in out and "bse.jsonl" in out
    reports = sorted((mixed.zones["reports"] / "checks").glob("*.json"))
    assert reports, "no check report written"
    rep = json.loads(reports[-1].read_text())
    details = " ".join(d for r in rep["results"] for d in r["details"])
    assert "bad_line" in details and f"line {n_before + 1}" in details and "bse.jsonl" in details, details
    assert "SECRET1" not in out and "SECRET1" not in reports[-1].read_text()      # the leak is not echoed
