# Checkpoint S1-T5 — Test writer — 2026-11-27

## Done so far
- (early checkpoint) tests written: tests/unit/acquire/test_collect_s1_datecheck.py + _datecheck_helpers.py (A59-A71, R34-R41), tests/unit/core/test_duckdb_caps.py (A31-A41, R20-R25)
- changed existing test: tests/unit/acquire/test_collector_sidecar.py::test_sidecar_schema_canonical_bytes_and_no_temp_files (top-level key set gains `date_check`; outcomes key set gains `rejected`; asserts date_check == not_date_keyed)
## Next step
- reference-impl proof, red evidence, final numbers
## Open questions
- see end of file after final update
## State
- branch: task/S1-T5 (base main 7824395)
