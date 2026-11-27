# S1-T3 — Nightly roots routine: report

- Code: src/investos/routine/roots.py; CLI `investos routine roots`; ops/launchd/com.investos.roots.plist.example (23:30 IST).
- Review findings and fixes:
  - D1 (High): a user config `remote.origin.push=+refs/heads/*` could turn the push into a force-update. The push now always uses the explicit refspec `refs/heads/<b>:refs/heads/<b>`.
  - D2: remote and branch names are validated before any git call.
  - D3: the shared-lock wait is bounded (30 s). A timeout means REFUSED, exit 3, with an alert.
  - D4: git runs with LC_ALL=C.
  - D5: a BUSY run no longer overwrites last.json.
  - D6: committed paths are checked against the add-list. A stray path means exit 3 with no push.
  - D8: redaction also covers quoted paths with spaces, the repo root and home prefixes.
  - D7 and D9: tests only, nothing to change in src.
- Verification by the Orchestrator: since red commit 31aca13 the Builder changed no test files. Suite run 3×: 1281 passed, 3 skipped each time. After merging with S1-T4, main runs as one suite.
