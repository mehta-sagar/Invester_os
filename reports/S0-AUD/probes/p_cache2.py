import re
"""Probe: index cache on a filesystem with coarse timestamps (emulated: HFS+/exFAT-like 1 s granularity)."""
import sys, os, tempfile
sys.path.insert(0, sys.argv[1])
from pathlib import Path
from datetime import datetime, timezone
import investos.acquire.manifest as m
real_stat = os.stat
class S:   # stat result with timestamps truncated to 1 s
    def __init__(s, st): s._st = st
    def __getattr__(s, k):
        v = getattr(s._st, k)
        return (v // 10**9) * 10**9 if k in ("st_mtime_ns", "st_ctime_ns") else v
m.os.stat = lambda p, *a, **k: S(real_stat(p, *a, **k))
r = Path(tempfile.mkdtemp()); (r/'raw').mkdir(); (r/'man').mkdir()
st = m.ManifestStore(r/'raw', r/'man'); T = datetime(2026,11,21,tzinfo=timezone.utc)
st.store("src", b"x", url="https://x.test/a", source_version="v1", logical_key="2026-11-20", run_id="r1", fetched_at=T)
print("cached lookup:", st.get_by_key("src","2026-11-20") is not None)
mp_ = r/'man'/'src.jsonl'; d = mp_.read_bytes()
mp_.write_bytes(d.replace(b"2026-11-20", b"2026-11-29"))       # same inode, same size
print("after same-size rewrite: get_by_key(2026-11-20) =", st.get_by_key("src","2026-11-20") is not None,
      "| fresh store says:", m.ManifestStore(r/'raw', r/'man').get_by_key("src","2026-11-20") is not None)
mp_.write_bytes(re.sub(rb'"sha256":"(.)', lambda mm: b'"sha256":"' + mm.group(1).upper() if mm.group(1).isalpha() else b'"sha256":"Z', d))  # same size, INVALID sha
try:
    print("after corrupting rewrite: cached store returns", st.get_by_key("src","2026-11-20"))
except m.ManifestCorruptError as e:
    print("corrupt detected:", e)
print("verify() (never cached):", [p.kind for p in st.verify("src")])
