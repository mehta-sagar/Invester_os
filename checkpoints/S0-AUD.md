# Checkpoint S0-AUD (Independent audit of Stage 0)
Status: IN PROGRESS 2026-11-21 (run 2). Branch audit/S0 (local only).
Done (run 1): read docs/specs/briefs/reports/reviews; read all src. Suite 3x root: 558 pass 2 skip; non-root: 560 pass (suite_runs.txt). Demos (demo_output.txt) all PASS.
Done (run 2): re-ran suite (558/2) and demo script in /tmp/aud copy: all PASS. Re-read all src.
Early findings run 2: git check-ignore shows TRACKED: data/manifests/*.jsonl.lock, *.runs/ sidecars + .tmp-*, registry/trials.jsonl(.lock), reports/checks/*.json + .tmp, config/investos.toml. log._safe raises on NaN (suspect). chain.append single fh.write w/o loop.
Next: probes in /tmp/aud_probes (symlink, 2-proc race, archive skip cost, D7, index sig), traceability, mutations, AUDIT.md.
