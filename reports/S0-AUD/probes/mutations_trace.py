"""Audit mutation runner: each mutation = (id, file, old, new, test path). Applies to a fresh copy of /tmp/aud."""
import subprocess, shutil, sys, os, json
from pathlib import Path
SRC = Path("/tmp/aud"); W = Path("/tmp/aud_mut")
M = [
 ("T1","src/investos/core/config.py","    if path is not None:\n        cfg_path: Path | None = Path(path)\n    elif \"INVESTOS_CONFIG\" in env:\n        cfg_path = Path(env[\"INVESTOS_CONFIG\"])","    if \"INVESTOS_CONFIG\" in env:\n        cfg_path: Path | None = Path(env[\"INVESTOS_CONFIG\"])\n    elif path is not None:\n        cfg_path = Path(path)","tests/unit/core"),
 ("T2","src/investos/core/log.py","    return dt.astimezone(timezone.utc).strftime(","    return dt.replace(tzinfo=timezone.utc).strftime(","tests/unit/core"),
 ("T3","src/investos/core/db.py","        apply = False\n","        pass\n","tests/unit/core"),
 ("T4","src/investos/acquire/manifest.py","        if not isinstance(data, bytes):\n            raise TypeError(\"data must be bytes\")\n","","tests/unit/acquire"),
 ("T5","src/investos/acquire/base.py","_BLOCK_STATUSES = frozenset({401, 403, 451})","_BLOCK_STATUSES = frozenset({401, 451})","tests/unit/acquire"),
 ("T6","src/investos/acquire/base.py","and v.record_count is not None and v.record_count >= self.page_limit):","and v.record_count is not None and v.record_count > self.page_limit):","tests/unit/acquire"),
 ("T7","src/investos/acquire/base.py","                if existing_status == \"complete\" and obj[\"status\"] != \"complete\":","                if False:","tests/unit/acquire"),
 ("T8","src/investos/registry/chain.py","            prev = rows[-1].hash if rows else GENESIS\n            seq = len(rows) + 1","            prev = rows[-1].hash if rows else \"f\" * 64\n            seq = len(rows) + 1","tests/unit/registry"),
 ("T9","src/investos/registry/chain.py","    return dt.astimezone(_IST).date().isoformat()","    return dt.date().isoformat()","tests/unit/registry"),
 ("T10","src/investos/check/cli.py","    return 0 if report.ok else 1","    return 0","tests/unit/check"),
 ("T11","src/investos/check/runner.py","    if before != after:","    if False:","tests/unit/check"),
 ("T12","src/investos/check/builtin.py","_MANIFEST_KINDS = frozenset({\"bad_line\", \"missing_file\", \"hash_mismatch\", \"size_mismatch\"})","_MANIFEST_KINDS = frozenset({\"bad_line\", \"missing_file\", \"hash_mismatch\"})","tests/unit/check"),
 ("T13","src/investos/check/builtin.py","            return CheckResult(False, \"database missing\", (str(cfg.db_path),))","            return CheckResult(True, \"database missing\", (str(cfg.db_path),))","tests/unit/check"),
 ("T14","src/investos/acquire/manifest.py","                if last != b\"\\n\":","                if False:","tests/unit/acquire"),
 ("T15","src/investos/acquire/base.py","        if not 200 <= resp.status <= 299:\n            return _Rec(Outcome.UNREADABLE, f\"non-2xx status {resp.status}\")","        if False:\n            pass","tests/unit/acquire"),
 ("T16","src/investos/check/runner.py","        if r.ok:\n            return out(\"missed\"","        if False:\n            return out(\"missed\"","tests/unit/check"),
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
json.dump(res, open("/tmp/aud_mut_tools/results_trace.json", "w"), indent=1)
