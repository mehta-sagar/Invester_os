# Checkpoint S0-T6 — Builder — 2026-11-21

## Done so far
- canon.py, chain.py, ledgers.py, check.py, __init__.py written
- tests/unit/registry: all green (143)
- full suite: 1 failure outside T6 files: tests/unit/check/test_checkrunner_registration.py::test_A18_default_registry_main_without_registry_module_target_is_usage_error
  (T5 test assumes no `registry` check is registered; once any earlier test runs main() with the default registry, investos.registry.check registers
  globally and monkeypatching sys.modules does not unregister it. Passes alone in its file. Test not edited; needs a test fix.)

## Next step
- sample run, tests_green.txt, final report
