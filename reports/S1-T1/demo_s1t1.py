"""S1-T1 demo: UrllibTransport against a local 127.0.0.1 http.server only. Run: python reports/S1-T1/demo_s1t1.py"""
import json, os, subprocess, sys, tempfile, threading
from datetime import date
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

from investos.acquire.access_cli import build_collector
from investos.acquire.base import BodyVerdict, Collector, Outcome, RunRange
from investos.acquire.transport import UrllibTransport
from investos.core.config import load_config

seen, mode = [], {"status": 200}


class H(BaseHTTPRequestHandler):
    def log_message(self, *a): pass
    def do_GET(self):
        seen.append(dict(self.headers.items()))
        body = b"date,close\n2026-11-17,100\n" if mode["status"] == 200 else b"denied"
        self.send_response(mode["status"]); self.send_header("Content-Length", str(len(body))); self.end_headers()
        self.wfile.write(body)


srv = ThreadingHTTPServer(("127.0.0.1", 0), H)
threading.Thread(target=srv.serve_forever, daemon=True).start()
port = srv.server_address[1]
root = Path(tempfile.mkdtemp(prefix="s1t1-demo-"))
for d in ("data/raw", "data/manifests", "data/canonical", "data/ledger", "reports", "registry", "db/migrations", "config"):
    (root / d).mkdir(parents=True)
(root / "pyproject.toml").write_text("[project]\nname='demo'\n")
(root / "config" / "investos.toml").write_text('''[general]
timezone = "Asia/Kolkata"
[paths]
raw = "data/raw"
manifests = "data/manifests"
canonical = "data/canonical"
ledger = "data/ledger"
reports = "reports"
registry = "registry"
[db]
migrations_dir = "db/migrations"
[rate_limits]
default_interval_s = 0.05
[collect]
contact = "demo@example.org"
[collect.access.demo_cap]
mode = "honest"
daily_cap = 2
''')
cfg = load_config(root / "config" / "investos.toml", repo_root=root, env={})


def make(source, host="127.0.0.1"):
    ns = {"source": source, "source_version": "demo-v1",
          "keys": lambda self, rr: [f"2026-11-{17 + i}" for i in range((rr.end - rr.start).days + 1)],
          "url": lambda self, k: f"http://{host}:{port}/f/{k}.csv",
          "check_body": lambda self, k, r: BodyVerdict(Outcome.FETCHED)}
    return type("C", (Collector,), ns)


def build(cls):
    c = build_collector(cfg, cls)
    c._transport = UrllibTransport(allowed_schemes=("http",), max_body_bytes=cfg.collect.max_body_bytes)
    return c


R = lambda a, b: RunRange(date.fromisoformat(a), date.fromisoformat(b))
print("== 1. honest fetch: headers the server received")
r = build(make("demo_ok")).run(R("2026-11-17", "2026-11-17"))
print(r.status, r.outcomes, json.dumps(seen[-1], indent=1))
print("== 2. 403 blocks the adapter's host for the IST day")
mode["status"] = 403; n = len(seen)
r = build(make("demo_blk")).run(R("2026-11-18", "2026-11-19"))
print(r.status, {k: v.value for k, v in r.outcomes.items()}, "calls:", len(seen) - n)
print((root / "data/ledger/access/hosts/127.0.0.1.jsonl").read_text())
print("== 3. second process, zero calls")
code = ("from investos.core.config import load_config; from pathlib import Path\n"
        "import sys; sys.path.insert(0, %r)\n" % str(Path(__file__).parent))
n = len(seen)
sub = subprocess.run([sys.executable, "-c", f"""
import sys, tempfile
from datetime import date
from pathlib import Path
from investos.acquire.access_cli import build_collector
from investos.acquire.base import BodyVerdict, Collector, Outcome, RunRange
from investos.core.config import load_config
cfg = load_config(Path({str(root / 'config' / 'investos.toml')!r}), repo_root=Path({str(root)!r}), env={{}})
class C(Collector):
    source, source_version = "demo_two", "v1"
    def keys(self, rr): return ["2026-11-20"]
    def url(self, k): return "http://127.0.0.1:{port}/f/" + k + ".csv"
    def check_body(self, k, r): return BodyVerdict(Outcome.FETCHED)
from investos.acquire.transport import UrllibTransport
c = build_collector(cfg, C); c._transport = UrllibTransport(allowed_schemes=("http",))
r = c.run(RunRange(date(2026,11,20), date(2026,11,20)))
print(r.status, r.http_calls)
"""], capture_output=True, text=True)
print(sub.stdout.strip() or sub.stderr[-400:], "| server saw new requests:", len(seen) - n)
print("== 4. daily cap 2")
mode["status"] = 200; n = len(seen)
# the capped adapter uses the host name "localhost", so the 127.0.0.1 day-block of step 2 does not apply to it
r = build(make("demo_cap", host="localhost")).run(R("2026-11-17", "2026-11-21"))
print(r.status, {k: v.value for k, v in r.outcomes.items()}, "calls:", len(seen) - n)
print(json.dumps([g for g in json.loads(r.sidecar_path.read_text())["gaps"]][:1]))
print("== 5. investos access status")
env = dict(os.environ, INVESTOS_ROOT=str(root), INVESTOS_CONFIG=str(root / "config" / "investos.toml"))
print(subprocess.run([sys.executable, "-m", "investos", "access", "status"], env=env, capture_output=True, text=True).stdout)
print("== 6. investos check all")
p = subprocess.run([sys.executable, "-m", "investos", "check", "all"], env=env, capture_output=True, text=True)
print(p.returncode, p.stdout[-600:], p.stderr[-300:])
srv.shutdown()
