# Brief <TASK-ID>: <title>

- Stage: <n>        - Branch: task/<task-id>        - Algorithm section: <§>
- Agents: Spec writer (Sonnet) → Test writer (Sonnet) → Builder (Sonnet) → Reviewer (Sonnet, fresh)

## Goal
<one paragraph>

## Files in scope (may touch)
- ...
## Out of scope (may not touch)
- data/ (agents change code, never data) · tests/ (Builder only) · anything not listed above

## Done means
- [ ] acceptance tests in specs/<module>.md all pass, each seen failing first
- [ ] at least one planted failure per rule is caught
- [ ] reviewer note has no open critical defect
- [ ] sample run output saved under reports/<task-id>/ (if task touches data)

## Rules (always)
Read checkpoints/<task-id>.md first; continue from "Next step". Save a checkpoint at the fixed points (roadmap §8).
Claims carry evidence (test output, file path). Stop and ask if a step can't be undone, a decision could go either way, or this brief is wrong.
No broker credentials, no network beyond what the spec names.
