# Checkpoint S0-SPEC — Spec writer — 2026-11-21T04:45Z

## Done so far
- S0-T2 → specs/core_config_log_db.md (19 rules, 30 acceptance tests)
- S0-T3 → specs/core_manifest.md (13 rules, 23 acceptance tests)
- S0-T4 → specs/collect_base.md (16 rules, 27 acceptance tests)
- S0-T5 → specs/check_runner.md (15 rules, 20 acceptance tests)
- S0-T6 → specs/registry_chain.md (13 rules, 23 acceptance tests)
## Next step
- All five specs written; commit on task/S0-SPEC and report to Orchestrator
## Open questions
- S0-T2 R6: brief says refuse "a raw path inside the git tree" but default raw is data/raw under repo root. Chose most conservative reading: allowed only if outside the repo or git-ignored (git check-ignore); git unavailable => refuse.
- S0-T2 R8: timezone fixed to Asia/Kolkata only (any other value refused) in Stage 0.
- S0-T2 R4: zones must be pairwise non-nested (stricter than brief).
- S0-T3: manifest line has exactly the 8 brief fields; no "dedup" flag field added. Repeat fetch of same bytes = new line, same raw file.
- S0-T3: truncated manifest tail makes store() refuse (no auto-repair).
- S0-T4: "3 retries" read as 1 attempt + 3 retries = 4 attempts (matches planted test "4th retry not attempted"). Backoff 1,2,4 s.
- S0-T4: brief says "stop-and-log" after retries; chose conservative: retry exhaustion or 401/403/451 aborts the whole run (remaining keys not_attempted). Check_body failures do not abort.
- S0-T4: missing_holiday/unreadable/incomplete store nothing, so they are re-attempted each run (holidays cost one request per re-run). Alternative: persist holiday markers; not done.
- S0-T4: incomplete (page limit) bodies are NOT stored (so re-run refetches); brief does not say.
- S0-T4: exact HTTP statuses retried: 429, 5xx, TransportError. Archive members stored as separate manifest entries key/<member>.
- S0-T5: brief says "@check(name)" and "registered plant()" but not how they pair. Chose two decorators (@check, @plant_for(name, setup=...)). Added optional setup so the harness can assert baseline passes before the plant (otherwise an always-failing check would count as caught).
- S0-T5: brief blocks T5 only on T2, but built-ins manifest/raw need T3 and registry needs T6. Spec: framework needs only T2; built-in check tests wait for T3; registry check+plant defined in S0-T6 (src/investos/registry/check.py). Orchestrator to confirm dependency order.
- S0-T5: check lacking a plant: refused in --planted mode (exit 1); in plain mode only a warning (exit unaffected). Stricter alternative: fail plain mode too.
- S0-T5: migrations check fails when DB file missing but migrations exist.
- S0-T6: brief wants canonical JSON "stable across float repr". Chose: floats FORBIDDEN in hashed payloads (CanonicalJsonError); decimals travel as strings; ints limited to +-(2^53-1). Tests assert floats are rejected rather than normalised.
- S0-T6: seq is 1-based; roots only for closed IST days (never today); roots file name = trials.roots.jsonl (path.with_suffix). first_broken_seq = 1-based line position (deleted row k reports k).
- S0-T6: chain alone cannot detect edit-with-recomputed-hash of the final row or deletion of tail rows; only committed roots catch them (documented limit, tested).
- S0-T6: append re-verifies the whole chain each time (O(n)); decisions allow repeated (date, security_id); trial_id must be unique.
- S0-T6/T5: registry check+plant lives in src/investos/registry/check.py (T6), imported by T5's load_builtin_checks.
## State
- commit: see `git log task/S0-SPEC` (hash reported to Orchestrator)   branch: task/S0-SPEC
- files touched: specs/core_config_log_db.md, specs/core_manifest.md, specs/collect_base.md, specs/check_runner.md, specs/registry_chain.md, checkpoints/S0-SPEC.md
- tokens used so far: not exposed to this agent
