"""Audit mutation runner: each mutation = (id, file, old, new, test path). Applies to a fresh copy of /tmp/aud."""
import subprocess, shutil, sys, os, json
from pathlib import Path
SRC = Path("/tmp/aud"); W = Path("/tmp/aud_mut")
M = [
 ("X1","src/investos/acquire/manifest.py","            if idx.sig == pre_sig:\n","            if True:\n","tests/unit/acquire"),
 ("X2","src/investos/acquire/manifest.py","    return (st.st_ino, st.st_size, st.st_mtime_ns, st.st_ctime_ns)","    return (st.st_ino,)","tests/unit/acquire"),
 ("X3","src/investos/acquire/base.py","        self._last_start[host] = self._mono()\n","        self._last_start[host] = last if last is not None and False else self._mono() - (interval if last is not None else 0)\n","tests/unit/acquire"),
 ("X4","src/investos/acquire/base.py","        if not isinstance(rid, str) or not _RUN_ID_RE.fullmatch(rid):","        if not isinstance(rid, str):","tests/unit/acquire"),
 ("X5","src/investos/registry/chain.py","        if not data.endswith(b\"\\n\"):\n            return roots, \"roots file does not end with newline\"\n","        if not data.endswith(b\"\\n\"):\n            data += b\"\\n\"\n","tests/unit/registry"),
 ("X6","src/investos/registry/chain.py","            elif _ist_date(row.ts) != r.date:","            elif False:","tests/unit/registry"),
 ("X7","src/investos/registry/canon.py","        if abs(obj) > MAX_INT:","        if abs(obj) > MAX_INT + 1:","tests/unit/registry"),
 ("X8","src/investos/registry/ledgers.py","_date.fromisoformat(d).isoformat() == d","bool(_date.fromisoformat(d))","tests/unit/registry"),
 ("X9","src/investos/core/config.py","            if pa == pb or pa in pb.parents or pb in pa.parents:","            if pa == pb or pa in pb.parents:","tests/unit/core"),
 ("X10","src/investos/core/db.py","    if sorted(by_ver) != expected:","    if False:","tests/unit/core"),
 ("X11","src/investos/core/db.py","        if len(names) > 1:","        if False:","tests/unit/core"),
 ("X12","src/investos/registry/check.py","        res = led.verify(roots=True)","        res = led.verify(roots=False)","tests/unit/registry tests/unit/check"),
 ("X13","src/investos/acquire/manifest.py","                    if fn.startswith(\".tmp-\"):","                    if fn.startswith(\".\"):","tests/unit/acquire tests/unit/check"),
 ("X14","src/investos/registry/chain.py","        fcntl.flock(self._fh, fcntl.LOCK_EX)","        fcntl.flock(self._fh, fcntl.LOCK_SH)","tests/unit/registry"),
 ("X15","src/investos/acquire/base.py","            except TransportError as e:\n                resp, cause = None, f\"transport error: {e}\"\n","            except TransportError as e:\n                return self._finish(key, url, _Rec(Outcome.UNREADABLE, f\"transport error: {e}\"), attempts, None, abort=True)\n","tests/unit/acquire"),
 ("X16","src/investos/check/runner.py","            shutil.copytree(cfg.zones[k], zones[k], symlinks=False)","            shutil.copytree(cfg.zones[k], zones[k], symlinks=True)","tests/unit/check"),
 ("X17","src/investos/acquire/manifest.py","            if len(data) == st.st_size:      # file did not change between stat and read\n                self._idx[source] = idx\n","            self._idx[source] = idx\n","tests/unit/acquire"),
 ("X18","src/investos/check/builtin.py","_RAW_KINDS = frozenset({\"writable_file\", \"misplaced_file\"})","_RAW_KINDS = frozenset({\"misplaced_file\"})","tests/unit/check"),
 ("X19","src/investos/registry/chain.py","            if rows and ts < rows[-1].ts:\n                raise LedgerError(\"clock moved backwards\")","            if False:\n                raise LedgerError(\"clock moved backwards\")","tests/unit/registry"),
 ("X20","src/investos/acquire/base.py","        keys = list(dict.fromkeys(self.keys(run_range)))","        keys = list(self.keys(run_range))","tests/unit/acquire"),
]
only = sys.argv[1:]
res = []
for mid, f, old, new, tests in M:
    if only and mid not in only: continue
    if W.exists(): shutil.rmtree(W)
    shutil.copytree(SRC, W, symlinks=True, ignore=shutil.ignore_patterns(".git", "__pycache__", ".pytest_cache"))
    p = W / f; t = p.read_text()
    n = t.count(old)
    if n != 1: res.append((mid, f"BAD PATTERN count={n}")); print(mid, "BAD", n); continue
    p.write_text(t.replace(old, new))
    r = subprocess.run([sys.executable, "-m", "pytest", "-q", "-x", "-p", "no:cacheprovider", *tests.split()],
                       cwd=W, capture_output=True, text=True, env={**os.environ, "PYTHONPATH": str(W/"src")})
    tail = r.stdout.strip().splitlines()
    failed = [l for l in tail if l.startswith("FAILED") or l.startswith("ERROR")]
    verdict = "KILLED" if r.returncode != 0 else "SURVIVED"
    res.append((mid, verdict, failed[0][:150] if failed else tail[-1][:120]))
    print(mid, verdict, failed[0][:150] if failed else tail[-1][:120], flush=True)
json.dump(res, open("/tmp/aud_mut_tools/results.json", "w"), indent=1)
