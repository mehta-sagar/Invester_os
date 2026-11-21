from common import *
import io, zipfile, time, hashlib
from datetime import date, datetime, timezone, timedelta
from investos.acquire.base import Collector, Response, BodyVerdict, Outcome, RunRange
from investos.acquire.manifest import ManifestStore
from investos.core import log
log.configure_logging(open(os.devnull, "w"))
root, cfg = make_root()
def mkzip(i, size):
    b = io.BytesIO()
    with zipfile.ZipFile(b, "w", zipfile.ZIP_DEFLATED) as z:
        z.writestr("a.csv", os.urandom(size)); z.writestr("b.csv", f"date,{i}\n".encode())
    return b.getvalue()
class T:
    def __init__(self, bodies): self.bodies = bodies; self.calls = 0
    def get(self, url, *, headers, timeout):
        self.calls += 1; k = url.rsplit("/",1)[1]
        return Response(200, {}, self.bodies[k], url)
class Z(Collector):
    source = "nse_arch"; source_version = "v1"; archive = True
    def keys(self, rr):
        d = rr.start
        while d <= rr.end: yield d.isoformat(); d += timedelta(days=1)
    def url(self, k): return f"https://archives.test/{k}"
    def check_body(self, k, r): return BodyVerdict(Outcome.FETCHED)
rr = RunRange(date(2026,1,1), date(2026,1,30))
keys = list(Z.keys(None, rr))
bodies = {k: mkzip(i, 2_000_000) for i, k in enumerate(keys)}
st = ManifestStore.from_config(cfg)
c = Z(transport=T(bodies), manifest=st, cfg=cfg, run_id="r1", sleeper=lambda s: None)
r1 = c.run(rr); print("first:", r1.status, r1.http_calls)
for n in range(2):
    t0 = time.perf_counter()
    c2 = Z(transport=T(bodies), manifest=ManifestStore.from_config(cfg), cfg=cfg, run_id=f"r{n+2}", sleeper=lambda s: None)
    r2 = c2.run(rr); print(f"skip re-run: {r2.status} http={r2.http_calls} {time.perf_counter()-t0:.2f}s for 30 x 2MB zips (re-hash + re-unzip each)")
# tamper one stored zip: skip path -> read_bytes raises?
e = st.get_by_key("nse_arch", keys[3]); p = st.raw_path("nse_arch", e.sha256)
os.chmod(p, 0o644); b = bytearray(p.read_bytes()); b[100] ^= 1; p.write_bytes(bytes(b)); os.chmod(p, 0o444)
c3 = Z(transport=T(bodies), manifest=ManifestStore.from_config(cfg), cfg=cfg, run_id="r9", sleeper=lambda s: None)
try:
    r3 = c3.run(rr); print("tampered:", r3.status)
except Exception as ex:
    print("tampered zip -> run raised", type(ex).__name__, str(ex)[:80])
    print("sidecar for r9 written?", any("r9" in open(f).read() for f in (cfg.zones["manifests"]/"nse_arch.runs").glob("*.json")))
