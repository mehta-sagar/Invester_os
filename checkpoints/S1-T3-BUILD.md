# Checkpoint S1-T3 - Builder
## State
- DONE: src/investos/routine/{__init__,roots}.py, cli `routine roots` (cli.py), ops/launchd/com.investos.roots.plist.example.
- Full suite x3: 1251 passed, 3 skipped (88 new). Output: reports/S1-T3/tests_green.txt.
## Notes
- A14 spawns `python -I`, which needs investos installed: I ran `pip install -e . --no-deps` (env only, not in repo); it fails without it.
- BUSY runs also write last.json (spec R14 literal). Staged paths are unstaged (restore --staged, own paths only) on torn tail, stray path and commit failure so the next run is not blocked by R5.
- Dry run skips the routine lock (no files created).
