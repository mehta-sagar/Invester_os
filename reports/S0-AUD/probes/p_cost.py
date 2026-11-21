from common import *
import io, time
from datetime import datetime, timezone
from investos.acquire.manifest import ManifestStore
from investos.check.runner import run_planted
from investos.check.cli import load_builtin_checks
from investos.check.registry import DEFAULT_REGISTRY
from investos.core.db import open_db
from investos.core import log
log.configure_logging(open(os.devnull,"w"))
root, cfg = make_root(); open_db(cfg).close()
st = ManifestStore.from_config(cfg)
for i in range(40):
    st.store("big", os.urandom(5_000_000), url="u", source_version="v", logical_key=f"k{i}", run_id="r", fetched_at=datetime.now(timezone.utc))
load_builtin_checks()
t0=time.perf_counter(); rep = run_planted(DEFAULT_REGISTRY, cfg); dt=time.perf_counter()-t0
print("raw 200MB: planted", [r.status for r in rep.results], f"{dt:.1f}s")
import tracemalloc
