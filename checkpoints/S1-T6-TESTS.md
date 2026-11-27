# Checkpoint S1-T6 - Test writer - midway

## Done so far
- Spec re-read after amendment (R7 hook gets `body`, R8a content digest + `last_content_digest` in state, R3a 26-row table, A10a).
- Written (red): tests/unit/acquire/_rss_helpers.py, test_rss_parse_items.py, test_rss_base_hook.py, test_rss_feedtable.py, test_rss_collector.py.

## Next step
Write test_rss_cycle.py (A10, A10a, A11 cycle, A12-A19, A24, A30, A31), test_rss_cli.py (A1 CLI, A25, A26), test_rss_check.py (A27-A29), test_rss_registry.py, test_rss_hygiene.py (R23, R12 add-list, A24 config); then satisfiability reference impl in /home/claude/scratch/S1-T6-ref; then reports/S1-T6/RED_TESTS.md.
