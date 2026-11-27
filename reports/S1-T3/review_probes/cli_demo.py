import io, os, subprocess, sys, tempfile
from pathlib import Path
sys.path.insert(0, "/home/claude/invester_os/tests/unit/routine")
tmp = Path(tempfile.mkdtemp()); (tmp/"g").write_text("")
os.environ.update(GIT_CONFIG_GLOBAL=str(tmp/"g"), GIT_CONFIG_NOSYSTEM="1", HOME=str(tmp))
from _routine_helpers import RoutineEnv
e = RoutineEnv(tmp/"w", io.StringIO()); e.setup_three_days(); e.manifest("a")
def cli(*a):
    env = dict(os.environ, INVESTOS_ROOT=str(e.work), INVESTOS_CONFIG=str(e.work/"config/investos.toml"))
    r = subprocess.run([sys.executable, "-m", "investos.cli", "routine", "roots", *a], capture_output=True, text=True, env=env, cwd=str(e.work))
    print("$ routine roots", *a, "-> exit", r.returncode); print("  stdout:", r.stdout.strip()); print("  stderr:", r.stderr.strip()[:200])
cli("--dry-run"); print("  tree changed by dry-run? commits:", e.count())
cli(); print("  commit files:", e.files_in()); print("  remote==local:", e.remote_head()==e.head())
cli()
e.write("data/manifests/a.jsonl", open(e.work/"data/manifests/a.jsonl").read()+'{"n":9}\n'); e.take_origin_offline(); cli(); print("  local commits ahead:", e.g("rev-list","--count","origin/main..HEAD").stdout.strip())
e.bring_origin_back(); cli(); print("  remote==local:", e.remote_head()==e.head())
cli("--bogus"); cli("--branch", "other"); cli("--remote=--force")
