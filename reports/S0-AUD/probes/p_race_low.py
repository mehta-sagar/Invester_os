import os, multiprocessing as mp, sys
def w(path, n, L):
    fd = os.open(path, os.O_WRONLY|os.O_APPEND|os.O_CREAT, 0o644)
    line = b"x"*(L-1)+b"\n"
    for _ in range(n): os.write(fd, line)
    os.close(fd)
def r(path, stop, q):
    torn = 0; reads = 0
    while not stop.is_set():
        try:
            st = os.stat(path)
            with open(path, "rb") as f: data = f.read()
        except FileNotFoundError: continue
        reads += 1
        if data and not data.endswith(b"\n"): torn += 1
        # mimic ManifestStore: stat-size vs data
    q.put((reads, torn))
if __name__ == "__main__":
    for L in (300, 6000, 70000):
        p = f"/tmp/aud_probes/race_{L}.jsonl"
        if os.path.exists(p): os.unlink(p)
        stop = mp.Event(); q = mp.Queue()
        rp = mp.Process(target=r, args=(p, stop, q)); rp.start()
        wp = mp.Process(target=w, args=(p, 3000 if L<70000 else 300, L)); wp.start(); wp.join()
        stop.set(); print("line", L, "reads, torn:", q.get()); rp.join()
    os.system("df -T /tmp | tail -1")
