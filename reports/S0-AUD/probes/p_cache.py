"""Probe: ManifestStore in-memory index vs same-size in-place rewrite by another process."""
import sys, os, json, subprocess, tempfile, time
sys.path.insert(0, sys.argv[1])
from pathlib import Path
from datetime import datetime, timezone
from investos.acquire.manifest import ManifestStore
r = Path(tempfile.mkdtemp()); (r/'raw').mkdir(); (r/'man').mkdir()
st = ManifestStore(r/'raw', r/'man')
T = datetime(2026,11,21,tzinfo=timezone.utc)
for k in ("A","B"):
    st.store("src", k.encode()*10, url="https://x.test/"+k, source_version="v1", logical_key="2026-11-2"+("0" if k=="A" else "1"), run_id="r1", fetched_at=T)
mp = r/'man'/'src.jsonl'
print("fs:", subprocess.run(["stat","-f","-c","%T",str(r)],capture_output=True,text=True).stdout.strip())
stale = 0; trials = 200
for i in range(trials):
    st.get_by_key("src","2026-11-20")         # warm cache
    data = mp.read_bytes()
    cur = b"2026-11-20" if b"2026-11-20" in data else b"2026-11-29"
    new = b"2026-11-29" if cur == b"2026-11-20" else b"2026-11-20"
    # other process: same-size in-place rewrite (no new inode, same size)
    subprocess.run([sys.executable, "-c",
        "import sys;p=sys.argv[1];d=open(p,'rb').read().replace(sys.argv[2].encode(),sys.argv[3].encode());"
        "f=open(p,'r+b');f.write(d);f.close()", str(mp), cur.decode(), new.decode()], check=True)
    got = st.get_by_key("src","2026-11-20")
    truth = b'"logical_key":"2026-11-20"' in mp.read_bytes()
    if (got is not None) != truth:
        stale += 1
print(f"same-size rewrite by another process, immediately after a cached read: stale answers {stale}/{trials}")
# in-process, no subprocess delay
stale2 = 0
for i in range(trials):
    st.get_by_key("src","2026-11-20")
    data = mp.read_bytes()
    a, b = (b"2026-11-20", b"2026-11-29") if b"2026-11-20" in data else (b"2026-11-29", b"2026-11-20")
    with open(mp, "r+b") as f: f.write(data.replace(a, b))
    got = st.get_by_key("src","2026-11-20")
    truth = b'"logical_key":"2026-11-20"' in mp.read_bytes()
    stale2 += (got is not None) != truth
print(f"same-size rewrite, tight loop: stale answers {stale2}/{trials}")
# mtime restored with os.utime (ctime still changes)
st.get_by_key("src","2026-11-20"); s0 = os.stat(mp)
data = mp.read_bytes(); a,b = (b"2026-11-20", b"2026-11-29") if b"2026-11-20" in data else (b"2026-11-29", b"2026-11-20")
time.sleep(0.05)
with open(mp,"r+b") as f: f.write(data.replace(a,b))
os.utime(mp, ns=(s0.st_atime_ns, s0.st_mtime_ns))
got = st.get_by_key("src","2026-11-20"); truth = b'"logical_key":"2026-11-20"' in mp.read_bytes()
print("rewrite + mtime restored via utime (50 ms later): stale =", (got is not None) != truth)
