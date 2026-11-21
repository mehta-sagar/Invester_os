import os, shutil, subprocess, sys, tempfile
from pathlib import Path
sys.path.insert(0, "/tmp/aud/src")
def make_root():
    root = Path(tempfile.mkdtemp(prefix="audroot-"))
    (root/"pyproject.toml").write_text("[project]\nname='x'\n")
    for z in ["data/raw","data/manifests","data/canonical","data/ledger","reports","registry","db/migrations","config"]:
        (root/z).mkdir(parents=True, exist_ok=True)
    shutil.copy("/tmp/aud/config/investos.example.toml", root/"config/investos.toml")
    for f in Path("/tmp/aud/db/migrations").glob("*.sql"):
        shutil.copy(f, root/"db/migrations"/f.name)
    from investos.core.config import load_config
    return root, load_config(root/"config/investos.toml", env={"PATH": os.environ["PATH"]}, repo_root=root)
