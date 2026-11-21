# Checkpoint S0-T4 fixes - Builder - 2026-11-21

## Done
- D1 manifest.py ManifestStore._index/_note_append/_Index: per-source in-memory index, validated by (inode,size,mtime_ns,ctime_ns) on every lookup, extended in store() under the file lock when it matched the pre-append state, else dropped. Corrupt files are never cached (strict R8/R7 unchanged). get_by_key/get_all_by_key/get_by_sha/entries all served from it.
- D2 base.py safe_unzip: "./" (and "/"-only) directory entries skipped; zero file members -> UnsafeArchiveError (unreadable, nothing stored).
- D3 base.py _do_key: skipped event logs self.url(key).
- D5 base.py _known_outputs: members found by unzipping the stored zip via manifest.read_bytes, then get_by_key per member. No entries().
- D6 base.py: no private import; own _SOURCE_RE copy.
## Deviations (need Orchestrator decision)
- D6/D5: tests/unit/acquire/test_manifest_write.py::test_A6 pins the exact public surface of manifest.py (module callables and ManifestStore methods), so a public check_source or get_by_prefix would turn A6 red. Not edited. Hence regex duplicated in base.py and read_bytes used.
## Results
- suite 3x: 558 passed, 2 skipped. reports/S0-T4/tests_green_after_fixes.txt
- sample run + 2000-key skip-only re-run (0.07 s, was 38 s): reports/S0-T4/sample_run_after_fixes.txt
## Not fixed
- D4 (policy pending), D7, D8 (info).
