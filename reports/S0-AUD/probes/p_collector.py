"""Collector probes: torn tail, D7 mid-archive failure, archive skip cost/limits, header deny-list."""
import sys, os, io, json, tempfile, shutil, zipfile, hashlib
sys.path.insert(0, sys.argv[1])
from pathlib import Path
from datetime import date, datetime, timezone
from investos.acquire.base import *
from investos.acquire.manifest import ManifestStore, ManifestCorruptError
from investos.core.config import load_config
from investos.core.log import configure_logging
configure_logging(io.StringIO())
def mkroot():
    r = Path(tempfile.mkdtemp())
    for z in ("data/raw","data/manifests","data/canonical","data/ledger","reports","registry","db/migrations"): (r/z).mkdir(parents=True)
    (r/"pyproject.toml").write_text(""); (r/"config").mkdir()
    shutil.copy(Path(sys.argv[1]).parent/"config/investos.example.toml", r/"config/investos.toml")
    return load_config(r/"config/investos.toml", env={})
class T:
    def __init__(s, body): s.body, s.n = body, 0
    def get(s, url, *, headers, timeout):
        s.n += 1; return Response(200, {}, s.body, url)
NOW = datetime(2026,11,21,tzinfo=timezone.utc)
class C(Collector):
    source = "psrc"; source_version = "v1"
    def keys(s, rr): return ["2026-11-20", "2026-11-21"]
    def url(s, k): return f"https://x.test/{k}"
    def check_body(s, k, r): return BodyVerdict(Outcome.FETCHED)
def mk(cfg, tr, cls=C, man=None):
    return cls(transport=tr, manifest=man or ManifestStore.from_config(cfg), cfg=cfg, run_id="r1",
               clock=lambda: NOW, monotonic=lambda: 0.0, sleeper=lambda s: None)
RR = RunRange(date(2026,11,20), date(2026,11,21))

print("== P-torn: torn manifest tail")
cfg = mkroot(); mk(cfg, T(b"data")).run(RunRange(date(2026,11,20),date(2026,11,20))) if False else None
c = mk(cfg, T(b"data")); c.run(RR)
mp = cfg.zones["manifests"]/"psrc.jsonl"; d = mp.read_bytes(); mp.write_bytes(d[:-20])     # torn tail
sc = list((cfg.zones["manifests"]/"psrc.runs").glob("*.json"))
try:
    mk(cfg, T(b"data")).run(RR); print("  run returned")
except Exception as e:
    print(f"  run() raised {type(e).__name__}: {e}")
print("  sidecars before/after:", len(sc), len(list((cfg.zones['manifests']/'psrc.runs').glob('*.json'))),
      "(no sidecar for the failed run)")

print("== P-D7: manifest store fails after members, before the zip line")
def zipb(**members):
    b = io.BytesIO()
    with zipfile.ZipFile(b, "w", zipfile.ZIP_DEFLATED) as z:
        for n, v in members.items(): z.writestr(n, v)
    return b.getvalue()
Z = zipb(**{"a.csv": b"x"*100, "b.csv": b"y"*100})
class CA(C):
    archive = True
    def keys(s, rr): return ["2026-11-20"]
class Flaky(ManifestStore):
    fail_on = None
    def store(s, source, data, **kw):
        if kw["logical_key"] == s.fail_on: raise OSError(28, "No space left on device")
        return super().store(source, data, **kw)
cfg = mkroot(); man = Flaky(cfg.zones["raw"], cfg.zones["manifests"]); man.fail_on = "2026-11-20"
try:
    mk(cfg, T(Z), CA, man).run(RunRange(date(2026,11,20),date(2026,11,20)))
except OSError as e:
    print(f"  run() raised OSError: {e}; sidecar written: {(cfg.zones['manifests']/'psrc.runs').exists() and any((cfg.zones['manifests']/'psrc.runs').glob('*.json'))}")
print("  manifest keys after failure:", [e.logical_key for e in ManifestStore.from_config(cfg).entries("psrc")])
man.fail_on = None; tr = T(Z)
res = mk(cfg, tr, CA, man).run(RunRange(date(2026,11,20),date(2026,11,20)))
print("  re-run:", {k: v.value for k, v in res.outcomes.items()}, "http", tr.n,
      "manifest keys:", [e.logical_key for e in ManifestStore.from_config(cfg).entries("psrc")])
print("  verify:", ManifestStore.from_config(cfg).verify("psrc"))

print("== P-arch-skip: skip of an archive key re-reads and unzips the stored zip")
class CA2(CA): unzip_limits = UnzipLimits(max_members=1)
tr = T(Z)
try:
    res = mk(cfg, tr, CA2, ManifestStore.from_config(cfg)).run(RunRange(date(2026,11,20),date(2026,11,20)))
    print("  re-run with tighter limits:", res.status)
except Exception as e:
    print(f"  re-run (all keys already stored) with max_members=1 raised {type(e).__name__}: {e}; http {tr.n}")
calls = {"read_bytes": 0}
class Count(ManifestStore):
    def read_bytes(s, *a): calls["read_bytes"] += 1; return super().read_bytes(*a)
mk(cfg, T(Z), CA, Count(cfg.zones["raw"], cfg.zones["manifests"])).run(RunRange(date(2026,11,20),date(2026,11,20)))
print("  read_bytes calls on a skip-only re-run of 1 archive key:", calls["read_bytes"])

print("== P-headers: names/values the deny-list lets through")
for hdr in ({"Authorization": "Bearer x"}, {"Host": "www.nseindia.com"}, {"Forwarded": "for=1.2.3.4"},
            {"X-Client-IP": "1.2.3.4"}, {"Accept": "a", "accept": "b"}, {"X-A": "v\r\nCookie: nsit=1"},
            {"Sec-Ch-Ua": '"Chromium"'}, {"user-agent ": "Mozilla/5.0"}, {"User_Agent": "Mozilla/5.0"}):
    class H(C): extra_headers = hdr
    try:
        h = mk(cfg, T(b""), H); print("  ALLOWED", hdr)
    except ValueError as e:
        print("  refused", hdr)

print("== P-nohost: url() without a host mid-run")
class NH(C):
    def url(s, k): return "https://x.test/a" if k.endswith("20") else "file:///etc/passwd"
cfg = mkroot()
try: mk(cfg, T(b"d"), NH).run(RR)
except Exception as e: print(f"  {type(e).__name__}: {e}; sidecar: {any((cfg.zones['manifests']/'psrc.runs').glob('*.json'))}; stored: {[x.logical_key for x in ManifestStore.from_config(cfg).entries('psrc')]}")
