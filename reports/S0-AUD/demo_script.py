"""S0-AUD exit-gate demos, written by the independent auditor (no reuse of earlier sample runs).

Usage:  python3 reports/S0-AUD/demo_script.py [REPO]      (REPO defaults to the repo holding this file)

Builds a brand-new data root under /tmp (git-initialised, repo .gitignore, example config, repo
migrations), then runs the four stage-0 exit-gate demos with an in-process fake transport (no network):
  1 a collector fetches one file: manifest row, read-only raw file, content-addressed path
  2 a deliberately broken body is rejected (outcome `unreadable`, nothing stored)
  3 a re-run fetches nothing: outcome `skipped`, 0 HTTP calls (fresh ManifestStore + collector)
  4 editing a past registry row breaks the hash chain and `investos check registry` exits 1
Every demo asserts its claim; the script exits non-zero if any claim fails.
"""
from __future__ import annotations

import hashlib
import json
import os
import shutil
import stat
import subprocess
import sys
import tempfile
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

REPO = Path(sys.argv[1]).resolve() if len(sys.argv) > 1 else Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO / "src"))

from investos.acquire.base import (BodyVerdict, Collector, Outcome, Response,  # noqa: E402
                                   RunRange)
from investos.acquire.manifest import ManifestStore  # noqa: E402
from investos.core.config import load_config  # noqa: E402
from investos.core.log import configure_logging  # noqa: E402
from investos.registry.ledgers import open_trials, register_trial  # noqa: E402

FAILS: list[str] = []


def claim(ok: bool, text: str) -> None:
    print(("  PASS  " if ok else "  FAIL  ") + text)
    if not ok:
        FAILS.append(text)


# ------------------------------------------------------------------ fresh data root
root = Path(tempfile.mkdtemp(prefix="investos-aud-demo-")).resolve()  # macOS: /var -> /private/var
for z in ("data/raw", "data/manifests", "data/canonical", "data/ledger", "reports", "registry"):
    (root / z).mkdir(parents=True)
(root / "config").mkdir()
shutil.copy(REPO / "config/investos.example.toml", root / "config/investos.toml")
shutil.copytree(REPO / "db/migrations", root / "db/migrations")
shutil.copy(REPO / ".gitignore", root / ".gitignore")
(root / "pyproject.toml").write_text("[project]\nname='demo'\n")
subprocess.run(["git", "init", "-q", str(root)], check=True)
cfg = load_config(root / "config/investos.toml", env={"PATH": os.environ.get("PATH", "")})
print(f"data root: {root}")
print(f"config loaded; raw zone {cfg.zones['raw']} (git-ignored check passed)")
log_path = root / "demo.log.jsonl"
configure_logging(log_path, run_id="20261121T100000Z-a0d17000")


# ------------------------------------------------------------------ fakes
class FakeTransport:
    """In-process HTTP double: url -> Response. Counts calls, records headers. No network."""

    def __init__(self, routes: dict[str, Response]):
        self.routes, self.calls = routes, []

    def get(self, url, *, headers, timeout):
        self.calls.append({"url": url, "headers": dict(headers), "timeout": timeout})
        return self.routes[url]


class Mono:
    def __init__(self):
        self.t = 0.0

    def __call__(self):
        return self.t


class Sleeper:
    def __init__(self, mono):
        self.mono, self.sleeps = mono, []

    def __call__(self, s):
        self.sleeps.append(s)
        self.mono.t += s


FIXED_NOW = datetime(2026, 11, 21, 9, 30, tzinfo=timezone.utc)


class BhavCollector(Collector):
    """Toy daily-file collector: body must start with the CSV header and contain >= 1 data row."""
    source = "demo_bhav"
    source_version = "demo-bhav-v1"

    def keys(self, rr):
        d = rr.start
        while d <= rr.end:
            yield d.isoformat()
            d += timedelta(days=1)

    def url(self, key):
        return f"https://archives.example.test/bhav/{key}.csv"

    def check_body(self, key, resp):
        b = resp.body
        if b.startswith(b"SYMBOL,DATE,CLOSE\n"):
            n = b.count(b"\n") - 1
            return BodyVerdict(Outcome.FETCHED if n > 0 else Outcome.FETCHED_EMPTY, "", n)
        return BodyVerdict(Outcome.UNREADABLE, f"body check: not a bhavcopy ({b[:30]!r})")


def make(routes, run_id):
    mono = Mono()
    sl = Sleeper(mono)
    tr = FakeTransport(routes)
    c = BhavCollector(transport=tr, manifest=ManifestStore.from_config(cfg), cfg=cfg, run_id=run_id,
                      clock=lambda: FIXED_NOW, monotonic=mono, sleeper=sl)
    return c, tr, sl


def manifest_lines(source):
    p = cfg.zones["manifests"] / f"{source}.jsonl"
    return p.read_text("utf-8").splitlines() if p.exists() else []


def raw_files(source):
    base = cfg.zones["raw"] / source
    return sorted(p for p in base.rglob("*") if p.is_file()) if base.exists() else []


GOOD = b"SYMBOL,DATE,CLOSE\nRELIANCE,2026-11-20,1290.50\nTCS,2026-11-20,4100.00\n"
BROKEN = b"<html><body>Error: data not available</body></html>"
K1, K2 = "2026-11-20", "2026-11-21"

# ------------------------------------------------------------------ demo 1
print("\n== Demo 1: a collector fetches one file")
c, tr, _ = make({f"https://archives.example.test/bhav/{K1}.csv": Response(200, {}, GOOD, "")}, "run-demo-1")
res = c.run(RunRange(date.fromisoformat(K1), date.fromisoformat(K1)))
sha = hashlib.sha256(GOOD).hexdigest()
lines = manifest_lines("demo_bhav")
rp = cfg.zones["raw"] / "demo_bhav" / sha[:2] / sha
print(f"  outcome={res.outcomes[K1].value} http_calls={res.http_calls} status={res.status}")
print(f"  manifest line: {lines[-1] if lines else None}")
print(f"  raw path: {rp.relative_to(root)}  mode={oct(stat.S_IMODE(rp.stat().st_mode)) if rp.exists() else None}")
print(f"  request headers: {tr.calls[0]['headers']}")
claim(res.outcomes[K1] is Outcome.FETCHED and res.http_calls == 1, "outcome fetched after exactly 1 HTTP call")
claim(len(lines) == 1, "exactly one manifest row written")
row = json.loads(lines[0])
claim(set(row) == {"sha256", "size", "url", "fetched_at", "source", "source_version", "logical_key", "run_id"},
      "manifest row has the eight spec fields")
claim(row["sha256"] == sha and row["size"] == len(GOOD) and row["logical_key"] == K1
      and row["run_id"] == "run-demo-1" and row["fetched_at"] == "2026-11-21T09:30:00.000000Z",
      "row values: sha256, size, logical_key, run_id, fetched_at (UTC, 6 digits, Z)")
claim(rp.is_file() and rp.read_bytes() == GOOD, "raw file at <raw>/<source>/<sha[:2]>/<sha> holds exact bytes")
claim(stat.S_IMODE(rp.stat().st_mode) == 0o444, "raw file mode is 0444 (read-only)")
claim(hashlib.sha256(rp.read_bytes()).hexdigest() == rp.name, "file name equals sha256 of its content")
claim(raw_files("demo_bhav") == [rp], "no other file (no tmp leftovers) in the raw tree")
side = json.loads(res.sidecar_path.read_text())
claim(side["status"] == "complete" and side["expected"] == side["received"] == 1
      and side["outputs"] == [{"key": K1, "sha256": sha}], "run sidecar complete, outputs = [key, sha]")
claim(tr.calls[0]["headers"] == {"User-Agent": "investos/0.0.0"}, "honest headers only")
gi = subprocess.run(["git", "-C", str(root), "check-ignore", "-q", str(rp)])
claim(gi.returncode == 0, "raw file is git-ignored")

# ------------------------------------------------------------------ demo 2
print("\n== Demo 2: a deliberately broken body is rejected")
before_lines, before_raw = manifest_lines("demo_bhav"), raw_files("demo_bhav")
c, tr, _ = make({f"https://archives.example.test/bhav/{K2}.csv": Response(200, {}, BROKEN, "")}, "run-demo-2")
res2 = c.run(RunRange(date.fromisoformat(K2), date.fromisoformat(K2)))
side2 = json.loads(res2.sidecar_path.read_text())
print(f"  outcome={res2.outcomes[K2].value} status={res2.status} gaps={side2['gaps']}")
claim(res2.outcomes[K2] is Outcome.UNREADABLE,
      "HTTP 200 with an error page -> outcome `unreadable` (the spec's name for the gate's `rejected`)")
claim(manifest_lines("demo_bhav") == before_lines, "no manifest row added")
claim(raw_files("demo_bhav") == before_raw, "nothing added to the raw tree")
claim(ManifestStore.from_config(cfg).get_by_key("demo_bhav", K2) is None, "key not in manifest -> retried next run")
claim(side2["status"] == "incomplete" and [g["key"] for g in side2["gaps"]] == [K2]
      and "body check" in side2["gaps"][0]["reason"], "sidecar: status incomplete, key listed in gaps with reason")

# ------------------------------------------------------------------ demo 3
print("\n== Demo 3: a re-run fetches nothing (fresh ManifestStore and collector instance)")
before_lines = manifest_lines("demo_bhav")
c, tr, sl = make({}, "run-demo-3")              # empty route table: any HTTP call would KeyError
res3 = c.run(RunRange(date.fromisoformat(K1), date.fromisoformat(K1)))
print(f"  outcome={res3.outcomes[K1].value} http_calls={res3.http_calls} transport_calls={len(tr.calls)} "
      f"sleeps={sl.sleeps} status={res3.status}")
claim(res3.outcomes[K1] is Outcome.SKIPPED, "outcome skipped")
claim(res3.http_calls == 0 and tr.calls == [] and sl.sleeps == [], "0 HTTP calls, 0 sleeps")
claim(manifest_lines("demo_bhav") == before_lines, "manifest unchanged")
claim(json.loads(res3.sidecar_path.read_text())["outputs"] == [{"key": K1, "sha256": sha}],
      "sidecar still complete with the same outputs")
# and from a separate OS process (no in-memory index shared)
code = ("import sys;sys.path.insert(0,sys.argv[1]);from investos.acquire.manifest import ManifestStore as M;"
        "from pathlib import Path;r=Path(sys.argv[2]);print(M(r/'data/raw',r/'data/manifests')"
        ".get_by_key('demo_bhav',sys.argv[3]) is not None)")
out = subprocess.run([sys.executable, "-c", code, str(REPO / "src"), str(root), K1], capture_output=True, text=True)
claim(out.stdout.strip() == "True", "a separate process sees the manifest hit too")

# ------------------------------------------------------------------ demo 4
print("\n== Demo 4: editing a past registry row breaks the hash chain")


class StepClock:
    def __init__(self, t):
        self.t = t

    def __call__(self):
        self.t += timedelta(hours=9)          # rows spread over several IST days
        return self.t


trials = open_trials(cfg, clock=StepClock(datetime(2026, 11, 17, 3, 0, tzinfo=timezone.utc)))
for i in range(6):
    register_trial(trials, trial_id=f"T{i}", family="momentum", feature_id=f"mom_{i}",
                   params={"lookback": 20 + i, "threshold": "0.05"}, hypothesis=f"hypothesis {i}")
roots = trials.write_roots()
print(f"  trials rows=6, roots written={[(r.date, r.seq) for r in roots]}")
env = {**os.environ, "INVESTOS_CONFIG": str(root / "config/investos.toml"), "PYTHONPATH": str(REPO / "src")}


def cli(*args):
    p = subprocess.run([sys.executable, "-m", "investos", "check", *args], env=env, capture_output=True, text=True)
    return p.returncode, p.stdout.strip()


rc, txt = cli("registry")
print(f"  before edit: exit {rc}\n    " + txt.replace("\n", "\n    "))
claim(rc == 0, "healthy ledger: `investos check registry` exits 0")
rc_all, txt_all = cli()
print("  `investos check` (all) before the DB exists:\n    " + txt_all.replace("\n", "\n    "))
claim(rc_all == 1 and "database missing" in txt_all, "no DB yet -> `migrations` check fails (database missing)")
from investos.core.db import open_db  # noqa: E402
open_db(cfg).close()
rc_all, txt_all = cli()
print(f"  `investos check` (all) after open_db: exit {rc_all}\n    " + txt_all.replace("\n", "\n    "))
claim(rc_all == 0, "healthy store + migrated DB: `investos check` (all) exits 0")
rc_p, txt_p = cli("--planted")
print(f"  `investos check --planted`: exit {rc_p}\n    " + txt_p.replace("\n", "\n    "))
claim(rc_p == 0 and txt_p.count("caught") == 4, "planted harness: all 4 plants caught")

path = trials.path
pristine = path.read_bytes()
lines_b = pristine.split(b"\n")
obj = json.loads(lines_b[1])                     # seq 2, a past row
obj["payload"]["hypothesis"] = "hypothesis 1 (edited after the fact)"
lines_b[1] = json.dumps(obj, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode()
path.write_bytes(b"\n".join(lines_b))
rc, txt = cli("registry")
print(f"  after editing seq 2 payload: exit {rc}\n    " + txt.replace("\n", "\n    "))
claim(rc == 1 and "trials: broken at seq 2" in txt, "edit of seq 2 -> exit 1, `trials: broken at seq 2`")

# stronger attacker: edit seq 2 and recompute every later hash (chain self-consistent)
from investos.registry.canon import canonical_json, sha256_hex  # noqa: E402
rows = [json.loads(x) for x in pristine.split(b"\n") if x]
rows[1]["payload"]["hypothesis"] = "forged"
prev = rows[0]["hash"]
for r in rows[1:]:
    r["prev_hash"] = prev
    r["hash"] = sha256_hex(canonical_json({k: r[k] for k in ("seq", "ts", "prev_hash", "payload")}))
    prev = r["hash"]
path.write_bytes(b"".join(canonical_json(r).encode() + b"\n" for r in rows))
rc, txt = cli("registry")
print(f"  after forging seq 2 and re-hashing the chain: exit {rc}\n    " + txt.replace("\n", "\n    "))
claim(rc == 1 and "broken at seq 4" in txt,
      "re-hashed forgery caught by the daily roots (exit 1; reported at seq 4 = the root covering seq 2)")
roots_file = path.with_suffix(".roots.jsonl")
roots_file.rename(roots_file.with_suffix(".bak"))
rc, txt = cli("registry")
print(f"  same forgery with the roots file removed: exit {rc}  (documented limit: absent roots = ok)")
claim(rc == 0, "LIMIT (expected by spec): forgery + deleted roots file passes -> roots must be anchored in git")
report_dir = cfg.zones["reports"] / "checks"
claim(len(list(report_dir.glob("*.json"))) >= 1, "check reports written under reports/checks/")

print(f"\nlog lines written: {len(log_path.read_text().splitlines())}")
print("\nRESULT:", "ALL CLAIMS PASS" if not FAILS else f"{len(FAILS)} CLAIM(S) FAILED: {FAILS}")
shutil.rmtree(root, onerror=lambda f, p, e: (os.chmod(p, 0o755), f(p)))
sys.exit(1 if FAILS else 0)
