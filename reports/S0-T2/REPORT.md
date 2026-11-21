# Task report S0-T2 — config, logging, DuckDB setup

- Built: src/investos/core/{config,log,db}.py, config/investos.example.toml, db/migrations/0000_bootstrap.sql, pyproject.toml
- Tests: 75/75 pass (62 spec + 13 review regressions); all written first by a separate Test writer and seen red (tests_red.txt, regressions_red.txt). Output: tests_green.txt
- Review: PASS WITH MINOR (review.md); 21 mutation probes, 18 red, 3 equivalent. D2–D5 fixed with regression tests; D1 (pyproject outside scope; cli entry point arrives in S0-T5) accepted; D6 (db-locked path untested) open → S0-AUD.
- Decision: strict D4 — git lookup uses only the injected env's PATH; A10 amended by the Test writer.
- Sample run: sample_run.txt — 0000+0001 applied, 10 tables, migration_problems = [], re-apply no-op.
- Merged: main @ tag s0-t2
