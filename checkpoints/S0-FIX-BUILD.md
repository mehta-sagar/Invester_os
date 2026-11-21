# Checkpoint S0-FIX — Builder — 2026-11-21
## Done
- A1 builtin._RAW_KINDS += symlink; A2 manifest._read_manifest (LOCK_SH re-read on torn tail), used by _index/_verify_source
- A3/A4 .gitignore rewritten, 7 zone .gitkeep files; ledgers and sidecars tracked
- A6 base.py header name token + deny-list + value CR/LF/NUL; A8 docs/runbooks/manifest-torn-tail.md
- A9 plant_for(zones=...), runner._temp_copy copies only needed zones; A10 ManifestStore.verify() reports unknown names, check messages name files
- A11 log._safe repr fallback; A12/A13 base.py skip path and run loop; A14 chain._write_all; A16 core/names.py
## Blocked (test defect, tests/ not editable)
- tests/unit/core/test_review_regressions.py D2 (5) + A11 (3) read rec["v"] / rec["other"] at top level; spec R9 puts them in "fields". Output verified correct under "fields".
## State
- 677 pass, 2 skip, 8 fail (those 8)
