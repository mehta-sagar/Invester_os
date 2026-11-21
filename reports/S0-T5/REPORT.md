# Task report S0-T5 — check runner (`investos check`) and planted-failure harness

- Built: src/investos/check/{registry,runner,builtin,cli,__main__}.py, src/investos/cli.py. Built-in checks: manifest (incl. torn tail), raw, migrations; registry check plugs in from S0-T6.
- Tests: 249 pass, 2 skip (root-only chmod cases) — 97 spec + 7 review regressions, written first by the Test writer and seen red. Orchestrator reran 3×.
- Review: PASS WITH MINOR; 17 probes, 16 killed; survivor (symlinks in zones) now covered. Safety property (--planted never modifies real data) could not be broken. D2 fixed (digest covers reports zone except own report), D5 fixed (cleanup u+rwx on dirs). Spec R5 amended (empty registry = usage error, exit 2, no report).
- Open → S0-AUD: D3 every check copies all zones (cost grows with raw store) — narrow per check before stage 1 volume; root-only tests unexercised in this sandbox (need a non-root run on the Mac).
- Sample run: sample_run.txt (check: all pass; --planted: all caught).
