"""Probe: how much does `investos check --planted` copy and hash?"""
import sys, os, io, shutil, tempfile, hashlib
sys.path.insert(0, sys.argv[1])
from pathlib import Path
from datetime import datetime, timezone
import investos.check.runner as runner
from investos.check.cli import main
from investos.core.config import load_config
from investos.acquire.manifest import ManifestStore
r = Path(tempfile.mkdtemp())
for z in ("data/raw","data/manifests","data/canonical","data/ledger","reports","registry"): (r/z).mkdir(parents=True)
(r/"pyproject.toml").write_text(""); (r/"config").mkdir()
shutil.copy(Path(sys.argv[1]).parent/"config/investos.example.toml", r/"config/investos.toml")
shutil.copytree(Path(sys.argv[1]).parent/"db/migrations", r/"db/migrations")
cfg = load_config(r/"config/investos.toml", env={})
st = ManifestStore.from_config(cfg)
for i in range(50):
    st.store("big", os.urandom(1 << 20), url=f"https://x.test/{i}", source_version="v1", logical_key=f"k{i}",
             run_id="r1", fetched_at=datetime(2026,11,21,tzinfo=timezone.utc))
copied = {"bytes": 0, "trees": 0}
real = shutil.copytree
class Spy:
    def copytree(self, src, dst, *a, **kw):
        copied["trees"] += 1
        copied["bytes"] += sum(f.stat().st_size for f in Path(src).rglob("*") if f.is_file())
        return real(src, dst, *a, **kw)
    def __getattr__(self, k): return getattr(shutil, k)
runner.shutil = Spy()
hashed = {"bytes": 0}
rd = hashlib.file_digest
def fd(fh, alg): hashed["bytes"] += os.fstat(fh.fileno()).st_size; return rd(fh, alg)
runner.hashlib.file_digest = fd
import time; t = time.time()
rc = main(["--planted"], cfg=cfg, stdout=io.StringIO())
print(f"raw zone 50 MiB; --planted exit {rc}; copytree calls {copied['trees']}, bytes copied {copied['bytes']/2**20:.0f} MiB, "
      f"real-data bytes hashed by the R8 digest {hashed['bytes']/2**20:.0f} MiB, {time.time()-t:.1f} s")
