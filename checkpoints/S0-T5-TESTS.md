# Checkpoint S0-T5-TESTS — Test writer — 2026-11-21

## Done so far
- branch task/S0-T5 created from main
- tests/unit/check/{_chk_helpers,conftest}.py (helpers: fake checks, flag check/plant, digest/snap, report readers)
## Next step
- test_checkrunner_registration.py (A1, A2, cli dispatch), _execution.py (A3-A6, A19, A20), _harness.py (A7-A12), _builtin.py (A13-A18 + torn tail)
## Open questions
- see final report
## State
- branch: task/S0-T5

## Update
- test_checkrunner_registration.py written (A1, A2, A18, cli dispatch)
- test_checkrunner_execution.py written (A3-A6, A19, A20, config error)
- test_checkrunner_harness.py written (A7-A12, shared-plant edge)
- test_checkrunner_builtin.py written (A13-A17, torn manifest tail D4, built-in no-op plant missed)
- Red: 97 new tests (39 failed, 57 errors in fixtures, 1 skipped as root-only), 147 existing passed; all red from ModuleNotFoundError investos.check (and investos.cli). reports/S0-T5/tests_red.txt
## Next step
- Hand to Implementer: src/investos/check/{__init__,registry,runner,builtin,cli,__main__}.py + src/investos/cli.py
