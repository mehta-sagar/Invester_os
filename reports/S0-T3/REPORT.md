# Task report S0-T3 — manifest writer (src/investos/acquire/manifest.py)

- Built: content-addressed raw store data/raw/<source>/<sha[:2]>/<sha> (tmp + fsync + os.link, 0444, parent-dir fsync), JSONL manifest per source with flock, lookups by key and sha, verify() with 7 problem kinds (bad_line, missing_file, hash_mismatch, size_mismatch, writable_file, misplaced_file, symlink).
- Tests: 147/147 suite (72 for this task: 52 spec + 20 review regressions), written first by the Test writer and seen red (tests_red.txt, regressions_red.txt). Orchestrator reran 5×, all green.
- Review: PASS WITH MINOR; 46 mutation probes, 35 killed; all 8 surviving gaps now covered. D1 symlinks, D2 non-regular dest, D3 dir fsync, T2, T7 fixed. Spec amended: symlink kind + R14.
- Builder found and fixed a concurrent mkdir race (41cc797) — do not cherry-pick 2200b34 alone.
- Open → S0-T5/S0-AUD: D4 torn manifest tail after ENOSPC blocks a source until repaired (by spec R7); fsync spy in tests uses /proc (Linux-only) — must be made portable or skipped on macOS before Mac runs.
- Sample run: sample_run.txt.
