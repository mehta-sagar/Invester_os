# Checkpoint S1-T7 - Builder - midway (28 Nov 2026)

## State
Steps 1-6 of the brief built: `src/investos/acquire/browser.py` (seam types, header sanitiser, PsReaper, PlaywrightDriver, RealBrowserTransport), `rss.py` (RssNseFeedCollector, collector_class_for, browser= parameter, host-down rule, coalescing, source-aware manifest reads), `check/rss_check.py` (both manifests, ghost rule), `cli.py` (--headless, transport build/close, SIGTERM handler, playwright hint), pyproject extra `browser` + marker `real_browser`, example toml (commented block), plist (Aqua + notes).
Full suite: 2450 passed, 4 skipped, 0 failed (root, Linux, no Chrome, no network).

## Next step
Non-root run, run with playwright blocked, grep evidence + REPORT.md, final checkpoint, commit.

## Test disputes
none so far.
