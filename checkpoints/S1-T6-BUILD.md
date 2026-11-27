# Checkpoint S1-T6-BUILD (Builder, 27 Nov 2026, midway)

State: all code written; full suite as root 2227 passed, 3 skipped (red was 284 failed, 29 errors).
Done: parse/rss_items, registry, base hook, rss.py (loader, collector, cycle, state, gaps, freshness), rss_check (+plant),
CLI `collect rss`, roots add-list, rss_feeds.csv (25 rows), example toml hosts, plist example.

## Test disputes
None so far.

## Next step
1. Non-root full-suite run. 2. grep evidence for R23 strings. 3. Sample output under reports/S1-T6/samples/
(script with fakes, no network). 4. Final checkpoint, `git diff 7a196c9..HEAD -- tests` must be empty.
