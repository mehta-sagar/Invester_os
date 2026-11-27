# Checkpoint S1-T5 — Reviewer — 2026-11-27

## Done so far
- Code and tests reviewed against collect_base R34-R41 and core_config R20-R25 → reports/S1-T5/review.md
- Full suite: root 1895 passed, 3 skipped; non-root claude 1898 passed → reports/S1-T5/review_pytest.txt
- 77 mutants: 66 killed, 11 survived (4 real gaps) → review.md
- Verdict: PASS WITH MINOR (D1 Medium test-order dependence from leaked log sink + open_db INFO line; D2-D3 Low test gaps; D4 Info)

## Next step
- Test writer: fix log fixture teardown (D1), add waived-key and message/rounding assertions (D2, D3). No src change required. Then Orchestrator: macOS run, merge.

## Open questions
- Sagar: default temp_directory zone-relative vs literal; spec text for sidecar schema (now 2); R35 "check lists waived adapters" follow-up owner.

## State
- commit: see git log   branch: task/S1-T5
- files touched: reports/S1-T5/review.md, reports/S1-T5/review_pytest.txt, checkpoints/S1-T5-REVIEW.md
