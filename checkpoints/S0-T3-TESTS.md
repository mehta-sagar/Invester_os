# Checkpoint S0-T3-TESTS — Test writer — 2026-11-21

## Done so far
- tests/unit/acquire/{conftest,_acq_helpers}.py
- test_manifest_write.py: A1-A8, A20-A23, extras A3b/A3c (arg constraints), A24 (from_config)
- test_manifest_concurrency.py: A9, A10, extra A9b (threads)
- test_manifest_read_verify.py: A11-A19, extras A19b/A19c
- Red run: 52 failed (all ModuleNotFoundError investos.acquire.manifest), 75 existing passed -> reports/S0-T3/tests_red.txt
## Next step
- Hand to Implementer: src/investos/acquire/manifest.py
## Open questions
- Module path: spec header says core.manifest; ADR 0001 says investos.acquire.manifest (used).
- Spec does not state exception type for empty url/source_version/run_id/logical_key or >512 key: tests assume ValueError. Non-bytes `data` untested.
- missing_file: tests assume one Problem per referencing manifest line with line = that 1-based line number.
- Problem.path compared by file name only (absolute vs relative not constrained).
- A6 compares functions/classes defined in the module and ManifestStore's non-underscore attrs to an exact allow-list.
## State
- branch: task/S0-T3

## Update — A16 fix (Test writer)
- test_A16: last assertion compared by basename and picked up the misplaced zz/ copy; now matches on the "2c/<sha>" path suffix. for_path stays basename-based (other uses have unique basenames).
