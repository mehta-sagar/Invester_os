# Checkpoint S0-T6 — Builder — 2026-11-21

## Done so far
- plant setup made safe on non-empty ledgers (starts 2 days after head)
- canon.py, chain.py, ledgers.py, check.py, __init__.py written
- tests/unit/registry: all green (143)
- full suite: 1 failure outside T6 files: tests/unit/check/test_checkrunner_registration.py::test_A18_default_registry_main_without_registry_module_target_is_usage_error
  (T5 test assumes no `registry` check is registered; once any earlier test runs main() with the default registry, investos.registry.check registers
  globally and monkeypatching sys.modules does not unregister it. Passes alone in its file. Test not edited; needs a test fix.)

## Next step
- sample run saved (reports/S0-T6/sample_run.txt); pytest output saved (tests_green.txt, shows the 1 failure above)
- awaiting decision on the T5 test fix

## Update: R7 canonical-form rule (9d67fbe, c856c11)
- chain.py _walk: stored body must equal canonical_json(parsed row), checked after ts order and before the final-newline check
- suite: 400 passed, 2 failed. Both failures are test bugs: the `reordered_keys` mutation (hash,payload,prev_hash,seq,ts) is alphabetical order, i.e. byte-identical to canonical form, so no break is correct. Tests not edited.
