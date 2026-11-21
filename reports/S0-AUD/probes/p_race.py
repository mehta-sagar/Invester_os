"""Writer process appends lines via ManifestStore.store; reader process (separate ManifestStore) loops get_by_key
without the lock. Counts ManifestCorruptError seen by the reader, and checks cache correctness at end."""
from common import *
import multiprocessing as mp, time
from datetime import datetime, timezone
from investos.acquire.manifest import ManifestStore, ManifestCorruptError
def writer(raw, man, n, keylen):
    st = ManifestStore(raw, man)
    for i in range(n):
        st.store("src", f"payload-{i}".encode(), url="https://x.test/"+"u"*keylen, source_version="v",
                 logical_key=f"k{i:06d}-"+"k"*300, run_id="w", fetched_at=datetime.now(timezone.utc))
def reader(raw, man, stop, q):
    st = ManifestStore(raw, man)
    errs = 0; reads = 0; msgs = set()
    while not stop.is_set():
        try:
            st.get_by_key("src", "nope"); reads += 1
        except ManifestCorruptError as e:
            errs += 1; msgs.add(str(e)[:90])
    # final consistency: cached index must equal a fresh parse
    fresh = ManifestStore(raw, man).entries("src")
    q.put((reads, errs, sorted(msgs)[:3], len(st.entries("src")) == len(fresh)))
if __name__ == "__main__":
    root, cfg = make_root()
    raw, man = cfg.zones["raw"], cfg.zones["manifests"]
    for keylen in (50, 5000):
        stop = mp.Event(); q = mp.Queue()
        r = mp.Process(target=reader, args=(raw, man, stop, q)); r.start()
        ws = [mp.Process(target=writer, args=(raw, man, 400, keylen)) for _ in range(2)]
        [w.start() for w in ws]; [w.join() for w in ws]
        stop.set(); print("urllen", keylen, "reads, corrupt-errors, msgs, cache==fresh:", q.get()); r.join()
