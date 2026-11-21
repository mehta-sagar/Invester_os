# Checkpoint S0-T3-BUILD — Builder — 2026-11-21

## Done so far
- manifest.py implemented; review follow-ups D1, D2, D3, T2, T7, D5 fixed (spec R14, Problem kind `symlink`).
- Full suite: 147 passed (15 consecutive green runs after fixing a mkdir race); reports/S0-T3/tests_green.txt; sample run redone.
- verify: manifest-referenced files are hashed (hash_mismatch/size_mismatch carry the manifest line; walk skips re-hashing them); symlinks reported, never followed in the walk.
- D4 (torn tail after failed append blocks the source) left as spec'd; note for S0-T5.

## Next step
- Orchestrator re-review and ff-only merge to main.
