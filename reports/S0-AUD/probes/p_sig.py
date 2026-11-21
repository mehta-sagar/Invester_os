from common import *
from datetime import datetime, timezone
from investos.acquire.manifest import ManifestStore
root, cfg = make_root()
st = ManifestStore.from_config(cfg)
t = datetime.now(timezone.utc)
stale = 0; N = 200
for i in range(N):
    src = f"s{i}"
    st.store(src, b"A", url="u", source_version="v", logical_key="keyA", run_id="r", fetched_at=t)
    p = cfg.zones["manifests"]/f"{src}.jsonl"
    assert st.get_by_key(src, "keyA") is not None          # cached now
    data = p.read_bytes().replace(b"keyA", b"keyB")         # same size rewrite, same inode
    with open(p, "r+b") as f: f.write(data)
    if st.get_by_key(src, "keyA") is not None: stale += 1   # stale cache would still see keyA
print(f"same-size in-place rewrite immediately after lookup: stale cache {stale}/{N}")
# with utime restore (ctime still changes)
src="u1"; st.store(src, b"A", url="u", source_version="v", logical_key="keyA", run_id="r", fetched_at=t)
p = cfg.zones["manifests"]/f"{src}.jsonl"; st.get_by_key(src,"keyA"); s0=os.stat(p)
import time; time.sleep(0.05)
data = p.read_bytes().replace(b"keyA", b"keyB")
with open(p,"r+b") as f: f.write(data)
os.utime(p, ns=(s0.st_atime_ns, s0.st_mtime_ns))
print("after rewrite+utime restore, stale:", st.get_by_key(src,"keyA") is not None)
