# Task report S0-T6 — trial registry and decision log (append-only, hash-chained)

- Built: src/investos/registry/{canon,chain,ledgers,check}.py. Canonical JSON (sorted keys, no floats), JSONL ledgers with seq/ts/prev_hash/hash, flock appends with full re-verify, daily IST roots for closed days, trials + decisions schemas, `registry` check + plant in the check runner.
- Tests: 402 suite pass, 2 skip (153 for this task: 143 spec + 10 canonical-form regressions), written first by the Test writer and seen red. Orchestrator reran 3×.
- Exit-gate demo 4: editing any past row (payload/ts/hash/prev_hash), deleting, reordering, inserting → verify fails at that seq; a whitespace-only edit is caught (R7 amended); a tail edit with recomputed hashes is caught by committed roots (sample_run.txt).
- Review: PASS WITH MINOR; 18/18 mutation probes killed; 8-process concurrent append clean. D1 fixed (.gitignore: data/ledger/*.roots.jsonl trackable).
- Known limits → S0-AUD / stage 1: (a) an attacker with write access to both ledger and roots files can rewrite history; protection = roots committed and pushed to GitHub daily (stage 1 routine must do write_roots → git commit → push); (b) a missing roots file counts as ok (spec); (c) today's open day is only protected once its root is written; (d) roots lines not held to canonical form (D2, optional).
