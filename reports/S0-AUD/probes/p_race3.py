from common import *
import multiprocessing as mp
from datetime import datetime, timezone
from investos.acquire.manifest import ManifestStore, ManifestCorruptError
def writer(raw, man, src, n, wid):
    st = ManifestStore(raw, man)
    for i in range(n):
        st.store(src, f"p-{wid}-{i}".encode(), url="https://x.test/a", source_version="v",
                 logical_key=f"k{wid}-{i}", run_id="w", fetched_at=datetime.now(timezone.utc))
def reader(raw, man, src, stop, q):
    st = ManifestStore(raw, man)
    errs = 0; reads = 0; vbad = 0; msg = None
    while not stop.is_set():
        try:
            st.get_by_key(src, "nope"); reads += 1
        except ManifestCorruptError as e:
            errs += 1; msg = str(e)

    q.put((reads, errs, vbad, msg))
if __name__ == "__main__":
    root, cfg = make_root()
    raw, man = cfg.zones["raw"], cfg.zones["manifests"]
    tot = [0,0,0]
    for rnd in range(30):
        src = f"s{rnd}"
        stop = mp.Event(); q = mp.Queue()
        r = mp.Process(target=reader, args=(raw, man, src, stop, q)); r.start()
        ws = [mp.Process(target=writer, args=(raw, man, src, 150, w)) for w in range(4)]
        [w.start() for w in ws]; [w.join() for w in ws]
        stop.set(); res = q.get(); r.join()
        tot[0]+=res[0]; tot[1]+=res[1]; tot[2]+=res[2]
        if res[3]: last = res[3]
    print("lookups", tot[0], "ManifestCorruptError", tot[1], "verify bad_line", tot[2])
    print("example:", locals().get("last"))
