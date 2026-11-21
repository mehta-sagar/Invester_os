# Checkpoint S0-AUD (Independent audit of Stage 0)
Status: DONE 2026-11-21 (run 2): reports/S0-AUD/AUDIT.md written, verdict PASS WITH CONDITIONS. Branch audit/S0 (local only).
Done (run 1): read docs/specs/briefs/reports/reviews; read all src. Suite 3x root: 558 pass 2 skip; non-root: 560 pass (suite_runs.txt). Demos (demo_output.txt) all PASS.
Done (run 2): re-ran suite (558/2) and demo script in /tmp/aud copy: all PASS. Re-read all src.
Early findings run 2: git check-ignore shows TRACKED: data/manifests/*.jsonl.lock, *.runs/ sidecars + .tmp-*, registry/trials.jsonl(.lock), reports/checks/*.json + .tmp, config/investos.toml. log._safe raises on NaN (suspect). chain.append single fh.write w/o loop.
Next: probes in /tmp/aud_probes (symlink, 2-proc race, archive skip cost, D7, index sig), traceability, mutations, AUDIT.md.
## Run 2 results so far (probes in reports/S0-AUD/probes/)
- symlink lead CONFIRMED: raw file replaced by symlink to writable outside file -> `investos check all` exit 0 (p_symlink.py). builtin.py:18-19 omit "symlink".
- stray names (raw/BSE, raw/stray.bin 0666, manifests/Bad-Name.jsonl) ignored by checks -> exit 0 (p_stray.py).
- unlocked reads CONFIRMED: OS-level torn reads 3/15 (p_race_low.py); ManifestStore.get_by_key in other process raised ManifestCorruptError 4/66925 (p_race3.py); verify false bad_line 1 (p_race2.py). Cache stays correct (cache==fresh).
- sig cache: same-size rewrite 0/200 stale, utime-restore not stale (p_sig.py) -> adequate on ext4/APFS.
- archive skip cost: 30x2MB zips skip re-run 0.1 s (fine); tampered stored zip -> RawIntegrityError crashes whole run, no sidecar.
- D7 run: ENOSPC on 2nd store -> exception, no sidecar, orphan member line; re-run refetches and completes; verify clean.
- D4 headers: Authorization, Host, Forwarded, X-Client-IP, True-Client-IP, Via, Cookie2, "Referer " (trailing space), CRLF value all ACCEPTED.
- fresh clone: data/raw,canonical,... .gitkeep not tracked (dir patterns) -> load_config fails "zone directory does not exist". config/investos.toml not ignored.
- git check-ignore: lock files, sidecars, .tmp-*, reports/checks/*.json, registry/trials.jsonl(.lock) TRACKED.
- macOS: FsyncSpy uses /proc/self/fd -> D3a/D3b assertions FAIL (not skip) on Mac.
- log: NaN field raises ValueError (spec R12 says never raise; pinned by regression test D2).
- secrets: none found (git grep, all-history file names).
- Mutations X1-X20: 11 killed, 9 survived (X1,X2 cache sig, X4 run_id regex, X5 roots newline, X8 decision date canonical, X9 reverse nesting, X12 registry check roots=False, X13 dotfile skip, X17 unobservable).
Traceability (41 rules) + T1-T16 mutations (14 killed) in AUDIT.md; results_trace.json. Defects A1-A17, 0 blockers. Done.
