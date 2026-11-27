import sys, os, tempfile, threading
sys.path.insert(0,"/home/claude/invester_os/src")
from pathlib import Path
from datetime import date
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from investos.acquire.base import *
from investos.acquire.access_cli import build_collector
from investos.acquire.transport import UrllibTransport
from investos.core.config import load_config
raw=[]
class H(BaseHTTPRequestHandler):
    def log_message(s,*a): pass
    def do_GET(s):
        raw.append(1); s.send_response(403); s.send_header("Content-Length","1"); s.end_headers(); s.wfile.write(b"x")
srv=ThreadingHTTPServer(("127.0.0.1",0),H); threading.Thread(target=srv.serve_forever,daemon=True).start(); port=srv.server_address[1]
root=Path(tempfile.mkdtemp(prefix="p2-"))
for d in ("data/raw","data/manifests","data/canonical","data/ledger","reports","registry","db/migrations","config"): (root/d).mkdir(parents=True)
(root/"pyproject.toml").write_text("[project]\nname='x'\n")
(root/"config/investos.toml").write_text('[general]\ntimezone="Asia/Kolkata"\n[paths]\nraw="data/raw"\nmanifests="data/manifests"\ncanonical="data/canonical"\nledger="data/ledger"\nreports="reports"\nregistry="registry"\n[db]\nmigrations_dir="db/migrations"\n[rate_limits]\ndefault_interval_s=0.01\n[collect]\ncontact="a@example.org"\n')
os.chmod(root/"data/ledger",0o777)
cfg=load_config(root/"config/investos.toml",repo_root=root,env={})
(root/"data/ledger/access").mkdir(); os.chmod(root/"data/ledger/access",0o555)  # unwritable access dir (non-root only)
class C(Collector):
    source="p2"; source_version="v"
    def keys(s,r): return ["2026-11-17"]
    def url(s,k): return f"http://127.0.0.1:{port}/{k}"
    def check_body(s,k,r): return BodyVerdict(Outcome.FETCHED)
for i in range(2):
    c=build_collector(cfg,C); c._transport=UrllibTransport(allowed_schemes=("http",))
    r=c.run(RunRange(date(2026,11,17),date(2026,11,17)))
    print("run",i,r.status,"calls",r.http_calls,"server saw",len(raw))
