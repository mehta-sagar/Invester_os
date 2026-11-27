"""Mutation runner for roots.py on a /tmp copy: python mut.py /tmp/s1t3_probe"""
import subprocess, sys, shutil
from pathlib import Path
root = Path(sys.argv[1]); f = root / "src/investos/routine/roots.py"
orig = f.read_text()
M = [
 ("M01 skip branch check", 'if current != branch:', 'if False:'),
 ("M02 skip index-clean check", 'if r.returncode == 1:\n        _refuse(logger, "the index already', 'if False:\n        _refuse(logger, "the index already'),
 ("M03 skip MERGE_HEAD etc", 'if (gitdir / marker).exists():', 'if False:'),
 ("M04 no verify before write", 'if not v.ok:\n            _verify_failed', 'if False:\n            _verify_failed'),
 ("M05 verify without roots", 'led.verify(roots=True)', 'led.verify(roots=False)'),
 ("M06 symlink allowed", 'return p.is_file() and not p.is_symlink()', 'return p.is_file()'),
 ("M07 no ignore filter", 'return [x for x in rels if x not in ignored]', 'return rels'),
 ("M08 no inside-repo check", 'real_p.relative_to(real_root)\n        return', 'return'),
 ("M09 host regex loosened", 'HOST_RE = re.compile(r"[a-z0-9][a-z0-9.-]{0,252}"', 'HOST_RE = re.compile(r".+"'),
 ("M10 sidecar regex loosened", 'SIDECAR_RE = re.compile(\n    r"\\d', 'SIDECAR_RE = re.compile(\n    r".+|\\d'),
 ("M11 source regex dropped for manifests", 'cands += [p for p in man.glob("*.jsonl") if SOURCE_RE.fullmatch(p.stem)]', 'cands += [p for p in man.glob("*.jsonl")]'),
 ("M12 no shared lock", 'with _Flock(root / (rel + ".lock"), fcntl.LOCK_SH):', 'with _Flock(root / (rel + ".lock"), fcntl.LOCK_UN):'),
 ("M13 torn tail not checked", 'if r.stdout and not r.stdout.endswith("\\n"):', 'if False:'),
 ("M14 torn tail not unstaged", 'unstage(staged)\n                _refuse(logger, f"{rel} does not', '_refuse(logger, f"{rel} does not'),
 ("M15 subset check off", 'if extra:\n', 'if False:\n'),
 ("M16 commit --no-verify", '["commit", "-m"', '["commit", "--no-verify", "-m"'),
 ("M17 push --force", '["push", remote, branch]', '["push", "--force", remote, branch]'),
 ("M18 push never when nothing committed", 'ahead = True if r.returncode != 0 else int(r.stdout.strip() or "0") > 0', 'ahead = False'),
 ("M19 always push even if not ahead", 'if not ahead:\n            return', 'if False:\n            return'),
 ("M20 rejected misclassified", 'if "rejected" in low or "non-fast-forward" in low or "fetch first" in low:', 'if False:'),
 ("M21 identity not mapped to USAGE", 'if any(h in err for h in _IDENTITY_HINTS):', 'if False:'),
 ("M22 lock not exclusive-nonblocking", 'fcntl.LOCK_EX | fcntl.LOCK_NB', 'fcntl.LOCK_SH | fcntl.LOCK_NB'),
 ("M23 notify code 4 added", '_NOTIFY_CODES = (2, 3, 5, 6)', '_NOTIFY_CODES = (2, 3, 4, 5, 6)'),
 ("M24 notify code 6 dropped", '_NOTIFY_CODES = (2, 3, 5, 6)', '_NOTIFY_CODES = (2, 3, 5)'),
 ("M25 notify in dry-run", '    if not dry_run:\n        try:\n            obj', '    if True:\n        try:\n            obj'),
 ("M26 notify exception propagates", 'logger.warning("notify_failed", error=type(e).__name__)', 'raise'),
 ("M27 unexpected after write -> USAGE", 'code = ExitCode.COMMIT_FAILED if state["wrote"] else ExitCode.USAGE', 'code = ExitCode.USAGE'),
 ("M28 wrote flag never set", 'state["wrote"] = True', 'pass'),
 ("M29 roots written in wrong order", 'for kind in ("trials", "decisions"):\n        try:', 'for kind in ("decisions", "trials"):\n        try:'),
 ("M30 last.json not written", '_write_last(state_dir, obj)', 'pass'),
 ("M31 commit message IST->UTC date", 'clock().astimezone(_IST).date()', 'clock().astimezone(timezone.utc).date()'),
 ("M32 dry-run still writes", 'if dry_run:\n        state["added"]', 'if False:\n        state["added"]'),
 ("M33 commit even if clean index", 'if r.returncode == 1:\n        ist_day', 'if True:\n        ist_day'),
 ("M34 push timeout dropped to 1s", 'timeout=60', 'timeout=0.001'),
 ("M35 URL not redacted", 'text = re.sub(r"\\S*://\\S*", "[url]", text)', 'pass'),
 ("M36 path not redacted", 'text = re.sub(r"(?<![\\w.\\-])/[\\w./\\-~+@%]+", "[path]", text)', 'pass'),
 ("M37 state dir (.state) included in add-list", 'cands += [p for p in access.glob("*.jsonl") if SOURCE_RE.fullmatch(p.stem)]', 'cands += [p for p in access.glob("*.jsonl") if SOURCE_RE.fullmatch(p.stem)] + list((led / ".state").glob("*"))'),
 ("M38 access hosts dropped", 'cands += [p for p in hosts.glob("*.jsonl") if HOST_RE.fullmatch(p.stem)]', 'pass'),
 ("M39 add whole dir", 'r = git.run(["add", "--", rel])\n        if r.returncode != 0:', 'r = git.run(["add", "--", str(Path(rel).parent)])\n        if r.returncode != 0:'),
 ("M40 stdin NUL check-ignore text", 'input="\\0".join(rels) + "\\0"', 'input="\\0".join(rels[:1]) + "\\0"'),
 ("M41 GIT_TERMINAL_PROMPT removed", 'env["GIT_TERMINAL_PROMPT"] = "0"', 'pass'),
 ("M42 shell=True", 'check=False, shell=False', 'check=False, shell=True'),
 ("M43 busy exit code", 'raise _Stop(ExitCode.BUSY,', 'raise _Stop(ExitCode.USAGE,'),
 ("M44 sidecars dir symlink followed", 'if d.is_dir() and not d.is_symlink() and SOURCE_RE', 'if d.is_dir() and SOURCE_RE'),
 ("M45 write_roots LedgerError not mapped", 'except LedgerError as e:', 'except ZeroDivisionError as e:'),
]
res = []
for name, a, b in M:
    if orig.count(a) != 1:
        res.append((name, "NOMATCH", orig.count(a))); print(name, "NOMATCH", orig.count(a)); continue
    f.write_text(orig.replace(a, b))
    r = subprocess.run([sys.executable, "-m", "pytest", "tests/unit/routine", "-x", "-q", "-p", "no:cacheprovider",
                        "--ignore=tests/unit/routine/test_review_probes.py"], cwd=root, capture_output=True, text=True)
    last = [l for l in r.stdout.splitlines() if "FAILED" in l or "ERROR" in l]
    st = "KILLED" if r.returncode != 0 else "SURVIVED"
    res.append((name, st, last[0][:110] if last else ""))
    print(name, st, last[0][:110] if last else "", flush=True)
f.write_text(orig)
