# Checkpoint S0-T3-BUILD — Builder — 2026-11-21

## Done so far
- src/investos/acquire/{__init__,manifest}.py implemented per spec.
- Suite: 126 passed, 1 failed (test_A16_verify_missing_writable_misplaced, line ~177).

## BLOCKED: test looks wrong (not edited)
In A16 the misplaced case asserts both
  (a) a problem whose path endswith f"zz/{HELLO_SHA}" has kind misplaced_file, and
  (b) kinds(for_path(pr, base/"2c"/HELLO_SHA)) == set().
for_path() filters by file NAME only, so the zz/<HELLO_SHA> misplaced problem from (a) is also returned by (b)
(same basename). (a) and (b) cannot both hold. Fix suggestion: compare full paths in (b)
(e.g. [p for p in pr if p.path == str(base/"2c"/HELLO_SHA)]).

## Next step
- Test writer fixes A16 assertion; then run full suite, produce reports/S0-T3/{sample_run,tests_green}.txt, push.
