from common import *
import io, zipfile, errno
from datetime import date, timedelta
from investos.acquire.base import Collector, Response, BodyVerdict, Outcome, RunRange
from investos.acquire.manifest import ManifestStore
from investos.core import log
log.configure_logging(open(os.devnull, "w"))
root, cfg = make_root()
b = io.BytesIO()
with zipfile.ZipFile(b, "w") as z:
    for n in ("a.csv","b.csv","c.csv"): z.writestr(n, f"date,{n}\n1\n")
ZIP = b.getvalue()
class T:
    calls = 0
    def get(self, url, *, headers, timeout): T.calls += 1; return Response(200, {}, ZIP, url)
class Z(Collector):
    source = "arch"; source_version = "v1"; archive = True
    def keys(self, rr): return ["2026-01-01", "2026-01-02"]
    def url(self, k): return f"https://a.test/{k}"
    def check_body(self, k, r): return BodyVerdict(Outcome.FETCHED)
class FailingStore(ManifestStore):
    n = 0
    def store(self, *a, **kw):
        FailingStore.n += 1
        if FailingStore.n == 2: raise OSError(errno.ENOSPC, "No space left on device (injected)")
        return super().store(*a, **kw)
rr = RunRange(date(2026,1,1), date(2026,1,2))
st = FailingStore(cfg.zones["raw"], cfg.zones["manifests"])
try:
    Z(transport=T(), manifest=st, cfg=cfg, run_id="r1", sleeper=lambda s: None).run(rr); print("run 1 returned")
except OSError as e:
    print("run 1 raised:", e)
m = ManifestStore.from_config(cfg)
print("manifest after crash:", [e.logical_key for e in m.entries("arch")])
print("sidecars:", sorted(p.name for p in (cfg.zones["manifests"]/"arch.runs").glob("*")) if (cfg.zones["manifests"]/"arch.runs").exists() else "none")
r = Z(transport=T(), manifest=m, cfg=cfg, run_id="r2", sleeper=lambda s: None).run(rr)
print("run 2:", r.status, dict(r.outcomes), "http", r.http_calls)
print("manifest after run 2:", [e.logical_key for e in m.entries("arch")])
print("check manifest problems:", m.verify("arch"))
