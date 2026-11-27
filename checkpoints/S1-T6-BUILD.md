# Checkpoint S1-T6-BUILD (Builder, 27 Nov 2026, final)

Full suite, root: 2227 passed, 3 skipped. As non-root (uid 65534, git safe.directory set via env): 2230 passed.
`git diff 7a196c9..HEAD -- tests` is empty.

Files: src/investos/parse/{__init__,rss_items}.py, acquire/{registry,rss}.py, acquire/base.py (only the
`unchanged_body` hook), check/{rss_check,cli}.py, cli.py (`collect rss`), routine/roots.py (add-list: gaps.jsonl only),
config/{rss_feeds.csv,investos.example.toml}, ops/launchd/com.investos.rss.plist.example,
reports/S1-T6/samples/{make_sample.py,out/}.
R23 grep over the four modules: no forbidden string or import (rss_check's default `_clock` is the injectable clock).

## Test disputes
None.

## Notes for the Reviewer
- Cycle clock: collectors get `clock=lambda: now` (cycle start), as the tests require (`last_ok_at == fmt(now)`), so a
  row's `fetched_at` (and `first_seen_at`) can precede true receipt by up to the cycle length (budget 600 s). Tests pin this.
- A feed with state but never ok raises `poll_missed` only after 3 consecutive failures (not on the first failure).
- If the gap writer fails for a feed, its `last_ok_slot` is not advanced (the miss stays detectable) and the cycle
  returns `gap_write_failed=True` (CLI exit 1); `CycleResult` gained that defaulted field.
- `rss_check` and `cli.py` import a few underscore helpers (`_append`, `_dump`, `_check_contact`, `_write_state`).
- Plant uses its own one-feed table and fresh state in the temp copy, so the baseline is green whatever the real data holds.

## Fix round (review D1-D5, tests 346bf7c)
- D1: `poll_cycle(..., clock=None)`; collectors stamp fetched_at/first_seen_at from it (default `lambda: now`). Slot, state and gap timestamps stay on `now`.
- D2: the missed range starts after max(last_ok_slot, slot of last_attempt_at), so a recorded failed/missed slot is never re-recorded. No schema change.
- D4: state present, last_ok_slot null, failures >= 1 -> `poll_missed`; no state stays `unknown`.
- D5: after a gap-write failure last_ok_slot/last_ok_at and also last_attempt_at are held back, so the unrecorded miss is written once the ledger works again.
- Only src/investos/acquire/rss.py changed. Suite (root): 2253 passed, 3 skipped. `git diff 346bf7c..HEAD -- tests` empty.

## Next step
Orchestrator merge.
