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

## Update — review regressions (Test writer)
- Added tests/unit/acquire/test_manifest_review_regressions.py (renamed: basename clashed with tests/unit/core/test_review_regressions.py) (20 tests): D1a-d symlinks, D2a-b non-regular dest, D3a-c dir/manifest fsync (spy via /proc/self/fd), T1 verify no-mutation, T2 dedup on writable file raises RawIntegrityError without chmod, T3 race with different bytes, T5 newline-less valid JSON last line, T6 size True/5.0/-1, T7 non-bytes data -> TypeError (impl currently raises ValueError).
- Red: 13 failed, 7 passed (controls already satisfied: D3b, T1, T3, T5, T6 x3). Output: reports/S0-T3/regressions_red.txt
- D1d expects hash_mismatch attributed to the manifest line (p.line == 1), per R10 "for each manifest line".
- Needs "symlink" Problem kind (spec lists six kinds; spec update needed).
