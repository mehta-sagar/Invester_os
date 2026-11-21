# Task report S0-T1 — repository from the skeleton (written retroactively, 21 Nov 2026, audit A15)

- Built: private repo mehta-sagar/Invester_os from ~/Invest_OS/investos plus the stage-0 scaffolding. Skeleton files committed unchanged: reports/S0-T1/skeleton_hashes.txt (50 hashes match the Mac copy).
- Done-means check:
  - [x] skeleton hashes match (skeleton_hashes.txt)
  - [x] .gitignore keeps data/raw, staged, canonical, features, models, quarantine, exports and credentials/ out of git (reports/S0-T1/ignore_test.txt; re-tested by tests/regression/test_repo_hygiene.py). Changed after the audit: ledgers (trials, decisions, roots) and run sidecars ARE tracked, because git history is their tamper evidence and backup; only *.lock, .tmp-* and reports/checks/* are ignored.
  - [x] zone .gitkeep files committed (missing until S0-FIX, audit A3: a fresh clone failed at startup)
  - [ ] branch protection on main: NOT possible (git-only access, no GitHub API; protection rules need a paid plan for private repos). Decision 0001; revisit when API access exists.
  - [x] no file > 5 MB (largest tracked file 168 kB, reports/S0-T6/tests_red.txt)
- Open: branch protection (above).
