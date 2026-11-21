from common import *
from datetime import datetime, timezone
import io, contextlib, hashlib
root, cfg = make_root()
from investos.acquire.manifest import ManifestStore
from investos.check.cli import main
from investos.core.db import open_db
open_db(cfg).close()
st = ManifestStore.from_config(cfg)
e = st.store("bse", b"good", url="u", source_version="v", logical_key="k", run_id="r", fetched_at=datetime.now(timezone.utc))
# plant: an extra symlink in the raw tree pointing to a file outside, named like a sha, at proper place
outside = root/"outside.txt"; outside.write_bytes(b"evil")
sha = hashlib.sha256(b"evil").hexdigest()
d = cfg.zones["raw"]/"bse"/sha[:2]; d.mkdir(exist_ok=True)
os.symlink(outside, d/sha)
print("verify kinds:", sorted({p.kind for p in st.verify("bse")}))
buf = io.StringIO()
rc = main(["all"], cfg=cfg, stdout=buf)
print("check all rc=", rc); print(buf.getvalue())
# case 2: replace the manifest-referenced raw file with a symlink to an identical-content file outside
p = st.raw_path("bse", e.sha256); os.chmod(p, 0o644)
copy = root/"copy"; copy.write_bytes(b"good"); os.chmod(copy, 0o666)
os.unlink(p); os.symlink(copy, p)
os.unlink(d/sha)
print("verify2 kinds:", sorted({(p.kind) for p in st.verify("bse")}))
buf = io.StringIO(); rc = main(["all"], cfg=cfg, stdout=buf); print("check all rc=", rc); print(buf.getvalue())
