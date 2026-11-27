# Review S1-T6 (RSS poller and item extractor) - Reviewer (fresh), 27 Nov 2026

Scope: tests commit 7a196c9, build commits 9e1d9e7, cfd929c, 4c8aa0f, 8549d4a (HEAD 8549d4a). Read: brief, spec (all, incl. R3a/R8a/R15a and the Orchestrator resolutions), RED_TESTS.md, PROBE report, BUILD checkpoint, graph report, then the four new modules, the base hook diff, cli/roots/check diffs, shipped table, plist. `src/` and `tests/` unchanged by this review (mutations ran in throwaway copies under /tmp, never in the worktree).

## Verdict: PASS WITH MINOR
No Critical defect. One High defect (D1, first_seen_at can precede true public time by up to a cycle length) has a small, test-compatible fix and should be fixed before any consumer reads `first_seen_at` (none exists yet: loading items is a later task). Two Medium (D2 ledger overlap, D3 test gaps), the rest Low/Info.

## Suite counts
| Run | Result |
|---|---|
| root, run 1 | 2226 passed, 3 skipped, **1 failed**: `tests/unit/acquire/test_acquire_audit_regressions.py::test_A12_tampered_stored_zip_marks_key_and_run_continues` (pre-existing S0 test, unrelated to this task; passed alone and in the next two full runs; see D9) |
| root, run 2 | 2227 passed, 3 skipped |
| non-root (uid claude, 999) | 2230 passed, 0 skipped (the 3 root-only skips run as non-root) |
| rss-focused set (434 tests) | 434 passed, 2 skipped, about 22 s |
`git diff 7a196c9..HEAD -- tests` is empty (tests untouched by the Builder). R23 grep over rss.py, registry.py, rss_items.py, rss_check.py: no forbidden string or import, no clock read except the injectable defaults (`time.sleep` as default sleeper, check `_clock`).

## 1. Rule-by-rule trace
| Rule | Implemented | Proved by | Gap |
|---|---|---|---|
| R1 identity | rss.py:241-251, cli.py `_collect` contact check | A1/A2 tests K, L, Y; M42, M85 killed | none |
| R2 key and slot | rss.py:174-179, :264 | A3/A4 K; M28, M43, M41 killed | none |
| R3 / R3a table | rss.py:96-170, config/rss_feeds.csv (25 rows) | T tests (21 cases, shipped ids/windows); M74-M78 killed | none (25 rows, as resolved Q1) |
| R4 redirect | rss.py:273-275 | A6 K; M37, M40 killed | none |
| R5 body check | rss.py:272-284, rss_items.py:136-160 | A7 K/P; M7-M9, M39 killed. M38 (non-2xx in check_body) survives only because base `_judge` repeats the check (equivalent) | none |
| R6 parser safety | rss_items.py:125-146 | A8 P/K/H; M3 killed. M4-M6 survive as redundant with expat (equivalent) | defence in depth, fine |
| R7 hook | base.py:468-471, 700-709 | A9 H; M81-M84 killed | none |
| R8 / R8a dedup | rss.py:227-229, 286-289, 722-728 | A10/A10a C; M31, M32, M34-M36 killed; M33 (only first lastBuildDate stripped) survives | D3 |
| R9 schedule | rss.py:193-198, 581 | A11 K/C; M29, M30, M72 killed | none |
| R10 cycle | rss.py:569-600, 649-680 | A12/A13 C; M44, M73 killed; M45 (exactly 600 s) survives | D3 |
| R11 state | rss.py:296-398, 711-731 | A14/A15 C; M46-M52, M54, M99, M100, M102 killed; M53 (5 s bound) survives | D3 |
| R12 gaps | rss.py:211-223, 424-473, 624-634, 663-679 | A16/A17/A31 C, RT; M55, M57-M59, M91, M101 killed; M56 (hold back last_ok_slot) survives | D2, D3, D5 |
| R13 freshness | rss.py:508-546, 733-757 | A18/A19 C, K2; M60-M64, M66-M71 killed; M65 (future cap) survives | D3, D4 |
| R14 extractor | rss_items.py:205-227 | A20/A21 P; M15-M18, M25, M26 killed | D1 (clock feeding fetched_at) |
| R15 / R15a dates | rss_items.py:56-95 | A22/A22a P; M1, M2, M19-M21 killed; M22 (negative offset sign) survives | D3 |
| R16 identity | rss_items.py:96-128 | A23 P; M10-M14, M23, M24 killed | D6 |
| R17 rate | example toml | A24 C/Y | none |
| R18 CLI | cli.py `_collect` | A25 L; M85-M88 killed | none |
| R19 plist | ops/launchd/...example | A26 L (valid plist, comments) | none |
| R20 check and plant | check/rss_check.py | A27/A28 K2; M92-M95, M97, M98 killed; M96 (duplicate key) survives | D3 |
| R21 registry | registry.py | A29 K2; M89, M94 killed; M90 survives | D3 |
| R22 logging | rss.py throughout | A30 C | none |
| R23 honest only | all four modules | Y grep tests; M42 killed | none |

## 2. Defects
**D1 (High) first_seen_at (and every manifest `fetched_at`) is the cycle START, not the receipt time.** rss.py:638 `clock=lambda: self.now`; the base stamps `received_at = self._clock()` (base.py:602), so feed N in a cycle gets the start time of feed 1. R14 requires `first_seen_at` to be an upper bound on when the item became public ("never a lower one, which is the safe direction"). Scenario: cycle starts 10:00:00, 25 feeds at a 1.0 s limiter plus 470 KB downloads (the probe's Online_announcements is the first feed, but any later feed is fetched 30 s to 10 min later, retries add 7 s each); an item is published at 10:00:40, our fetch of a later feed at 10:00:55 sees it, and the stored row says `fetched_at = 10:00:00`, so `first_seen_at` (10:00:00) is BEFORE the item existed: a backtest sees it up to 600 s too early. Demonstrated with a throwaway cycle (interval 30 s, mono advanced 30 s): both feeds got `fetched_at = 2026-11-27T04:30:00Z`. The Builder's note 1 states this and says tests pin it: the tests pin `last_ok_at == fmt(now)`, which a separate clock does not break. Suggested fix: `poll_cycle(..., clock: Callable[[], datetime] | None = None)` defaulting to `lambda: now`; the CLI passes a real UTC clock; the collector uses `clock`, the slot and state fields keep `now`. Regression test: a clock that advances 40 s per call, two feeds, assert the second row's `fetched_at` is later than the first and that `fetched_at` is never earlier than the transport-call time.
**D2 (Medium) Overlapping, repeating gap lines during a persistent failure.** rss.py:663-667 computes `missed` from `last_ok_slot`, which does not move on failures, so each cycle re-reports every earlier failed slot as `missed` and also writes the current `failed` line. Throwaway run (feed ok at 09:00, then 503 for 8 slots): 15 lines, `missed 0915-0915`, `missed 0915-0930`, `missed 0915-0945` ... each overlapping the `failed` lines of the same slots; a 24 h outage of one reachable-but-failing feed gives about 150 overlapping lines, 25 feeds x that. R12 says one line per contiguous run, and the backfill runner reading the ledger would fill the same slots many times. Fix without a schema change: measure misses from `max(last_ok_slot, slot_of(last_attempt_at))` (the state already holds `last_attempt_at`). Regression test: three consecutive failing cycles then an ok one, assert no slot is covered by more than one gap line.
**D3 (Medium) Test gaps (surviving mutants, all with a real behaviour attached).** M65 future-dated newest item not capped at now (spec edge case, A19 text; no test where window < the future offset); M56 gap-write failure holding back `last_ok_slot` (Builder note 2, unpinned); M22 negative UTC offsets (`-0500`) untested; M33 only the first `<lastBuildDate>` stripped untested; M45 exactly-600 s budget boundary; M53 lock wait not asserted to be at most 5 s; M90 waived listing without `date_keyed`; M96 duplicate manifest key in the check. Suggested tests: one each, as named. (M4-M6, M38 are equivalent: expat/base already reject.)
**D4 (Low) A feed that never succeeded alarms `poll_missed` only after 3 failures.** rss.py:519-522: with `last_ok_slot is None` and 1-2 failures there is no alarm, while R13(a) says "the latest scheduled slot that is at least 10 minutes old has no ok poll". Delay at most two slots; if the Mac then sleeps no alarm is ever raised for a new feed that never worked (the check passes with state present). Suggested test: state with `last_ok_slot=None`, `consecutive_failures=1`, now 30 minutes later: decide and pin (alarm or documented waiver in the spec).
**D5 (Low) After a gap-write failure the state lies about the last ok slot.** rss.py:678-679 restores the old `last_ok_slot` though the poll was ok; while the writer stays broken (torn tail) every cycle raises `poll_missed` (loud, arguably desirable) and, once the writer works again, writes a `missed` line over slots that were polled fine. Acceptable trade-off for "never lose a gap", but it should be in the spec. Suggested test: pin both behaviours (M56).
**D6 (Info) `guid` is not canonicalised.** rss_items.py:117-122: only a `link` fallback gets the `.in` to `.com` rewrite; if NSE puts the attachment URL in `<guid>` (common in RSS), the `.com` to `.in` move would still make every item new. R16 text is ambiguous (A23 tests the link case). Suggest applying `_canon_link` to a guid that looks like a URL, with a test.
**D7 (Info) The DOCTYPE/ENTITY byte scan also matches inside CDATA and comments.** One announcement whose CDATA description contains `<!DOCTYPE` makes the whole body unreadable for as long as it stays in the window; the raw body is then not stored (fail-safe, raised as `poll_missed` after 3 polls). Matches the spec text; note for the Mac probe.
**D8 (Info) `_scheduled_between` is capped at 38,400 slots from the OLD end** (rss.py:67, 217), so a state older than 400 days reports a range that stops far from the current slot. Practically unreachable.
**D9 (Info) Pre-existing flaky test**: `test_A12_tampered_stored_zip...` failed once in three root runs (not touched by this task; likely order or random-content dependent). Worth a note to the Orchestrator.
**D10 (Info)** `cli.py` and `rss_check.py` import underscore helpers (`_append`, `_dump`, `_check_contact`, `_write_state`, `_blank_state`, `_fmt_ts`): acceptable now, a rename to public names later.

## 3. The Builder's four notes
1. Collector clock = cycle start: **not acceptable as is**, it breaks the safe direction of R14 (D1). The tests allow a fix.
2. Gap-write failure semantics (`gap_write_failed`, exit 1, last_ok_slot held back): sound for "a miss stays detectable"; its side effects are D5 and the survivor M56 (add a test).
3. Never-succeeded feed raises `poll_missed` after 3 failures: a deviation from the letter of R13(a), small (D4).
4. Plant baseline: its own one-feed table and fresh state in the temp copy, real data untouched (A28 tests, M97/M98 killed). Sound. A one-in-millions race (setup and check straddling the 10-minute mark) could turn the baseline red; ignorable.

## 4. Real-shape sanity (throwaway, not committed)
- Body: 1205 items, 556,059 bytes (probe: 470,650), items with `pubDate` `27-Nov-2026 22:58:14`, links on `nsearchives.nseindia.com`, an HTML description, a `<lastBuildDate>`. `parse_feed` 13 ms (1205 items); `content_digest` 1 ms. Under the 1 s bar by 70x.
- Spellings (all 9 of the probe plus `27-NOV-2026 10:00`, `Thu, 3 Dec 2026 9:05:00 GMT`, empty, garbage): `27-Nov-2026 22:58:14` -> 22:58:14+05:30 ok; `27-Nov-2026 22:39` -> 22:39 ok; `27-Nov-2026` -> 23:59:59 date_only; `11-SEP-2026 17:49:09` -> ok; `2026-11-27 18:08:28` -> ok; `Fri, 27 Nov 2026 00:00:00 +0530` -> 00:00 ok; `Fri, 27 Nov 2026 20:45:00` (no zone) -> 20:45+05:30 ok; `27 Nov 2026 22:00:04 +0530` -> ok; `27 Nov, 2026 +0530` -> 23:59:59 date_only; GMT 09:05 -> 14:35 IST; missing and empty -> missing; garbage -> unparseable.
- Dedup: two consecutive-slot cycles on the 1205-item body differing only in `<lastBuildDate>` -> `fetched` then `skipped`, one manifest row. Digests equal; a body with 1204 items differs (regex not greedy on a single-line body).
- Persistent failure and clock probes gave D2 and D1 above.

## 5. Mutation table (102 mutants: 90 killed, 12 survived, of which 4 equivalent)
One break at a time in a throwaway copy of the worktree, run against the rss tests, the check tests and (base/roots mutants) the whole acquire or routine dir with `-x`; each reverted after its run. The mandatory ones are M3 (DTD check removed), M1 (pubDate as UTC), M17 (pubDate as first_seen_at), M34 (raw sha instead of digest), M31 (lastBuildDate greedy), M82 (base hook exception uncaught), M81 (hook before date check), M99 (unreadable state skips the feed), M36 (absent digest treated as unchanged), M55 (gap-write failure swallowed) and M59 (failed gap line skipped), M10 (.in rewrite), M64 (09:15 anchor), M60 and M61 (alert twice), M42 (honest header), M89 (registry duplicate), M91 (roots add-list missing gaps.jsonl): all killed. The one surviving mutant on a mandatory theme is M56 (last_ok_slot not held back after a gap-write failure), see D3.

| id | file | mutation | result | note |
|---|---|---|---|---|
| M1 | rss_items.py | pubDate no-zone read as UTC | KILLED |  |
| M2 | rss_items.py | date_only 23:59:59 -> 00:00:00 | KILLED |  |
| M3 | rss_items.py | DTD/ENTITY check removed | KILLED |  |
| M4 | rss_items.py | DTD regex only doctype (entity dropped) | SURVIVED | equivalent: expat already rejects stray <!ENTITY |
| M5 | rss_items.py | DTD regex case sensitive | SURVIVED | equivalent: expat rejects lower-case <!doctype |
| M6 | rss_items.py | NUL check removed | SURVIVED | equivalent: expat rejects NUL |
| M7 | rss_items.py | 8 MiB cap doubled | KILLED |  |
| M8 | rss_items.py | root check removed (Atom/RDF accepted) | KILLED |  |
| M9 | rss_items.py | channel check removed | KILLED |  |
| M10 | rss_items.py | .in -> .com id rewrite removed | KILLED |  |
| M11 | rss_items.py | guid not stripped | KILLED |  |
| M12 | rss_items.py | duplicate id in body keeps last | KILLED |  |
| M13 | rss_items.py | title/summary caps x10 | KILLED |  |
| M14 | rss_items.py | control chars not removed | KILLED |  |
| M15 | rss_items.py | pub_after_seen margin 5min -> 0 | KILLED |  |
| M16 | rss_items.py | pub_after_seen >= instead of > | KILLED |  |
| M17 | rss_items.py | pubDate used as first_seen_at | KILLED |  |
| M18 | rss_items.py | seen set never updated (items repeat) | KILLED |  |
| M19 | rss_items.py | ISO with space refused | KILLED |  |
| M20 | rss_items.py | month name case-sensitive | KILLED |  |
| M21 | rss_items.py | GMT/Z zone read as IST | KILLED |  |
| M22 | rss_items.py | numeric zone sign ignored | SURVIVED | test gap: negative UTC offsets untested |
| M23 | rss_items.py | h: hash ignores pub_raw | KILLED |  |
| M24 | rss_items.py | dc:date fallback removed | KILLED |  |
| M25 | rss_items.py | unparseable body in extract_items raises | KILLED |  |
| M26 | rss_items.py | feed_id = whole key | KILLED |  |
| M27 | rss.py | slot floor % 15 -> % 5 | KILLED |  |
| M28 | rss.py | slot computed in UTC | KILLED |  |
| M29 | rss.py | overnight 05:00 slot dropped | KILLED |  |
| M30 | rss.py | daytime starts 06:00 | KILLED |  |
| M31 | rss.py | lastBuildDate regex greedy (eats items) | KILLED |  |
| M32 | rss.py | lastBuildDate regex case-insensitive | KILLED |  |
| M33 | rss.py | digest only first lastBuildDate | SURVIVED | test gap: only one lastBuildDate ever tested |
| M34 | rss.py | raw sha instead of digest in dedup | KILLED |  |
| M35 | rss.py | state stores raw sha as digest | KILLED |  |
| M36 | rss.py | absent digest treated as unchanged (skip on no state) | KILLED |  |
| M37 | rss.py | redirect check removed | KILLED |  |
| M38 | rss.py | non-2xx accepted by check_body | SURVIVED | equivalent: base _judge rejects non-2xx first |
| M39 | rss.py | empty feed -> unreadable | KILLED |  |
| M40 | rss.py | redirect location not capped | KILLED |  |
| M41 | rss.py | date_keyed True | KILLED |  |
| M42 | rss.py | honest headers changed (extra header) | KILLED |  |
| M43 | rss.py | key uses '-' not '/' | KILLED |  |
| M44 | rss.py | cycle budget 10 min -> 100 min | KILLED |  |
| M45 | rss.py | cycle budget >= -> > | SURVIVED | test gap: exactly 600 s boundary untested |
| M46 | rss.py | consecutive_failures not reset on ok | KILLED |  |
| M47 | rss.py | consecutive_failures not incremented | KILLED |  |
| M48 | rss.py | last_change_at moves on unchanged ok | KILLED |  |
| M49 | rss.py | newest_pub uses min not max | KILLED |  |
| M50 | rss.py | state schema mismatch accepted | KILLED |  |
| M51 | rss.py | state unknown field accepted | KILLED |  |
| M52 | rss.py | state write not atomic (direct write) | KILLED |  |
| M53 | rss.py | state lock wait 5s -> 50s | SURVIVED | test gap: 5 s lock bound not asserted |
| M54 | rss.py | state flock not taken | KILLED |  |
| M55 | rss.py | gap writer failure swallowed (flag not set) | KILLED |  |
| M56 | rss.py | last_ok_slot not held back on gap-write failure | SURVIVED | test gap: Builder note 2 (hold back last_ok_slot) unpinned |
| M57 | rss.py | missed range includes last_ok_slot itself | KILLED |  |
| M58 | rss.py | missed slots count off by one | KILLED |  |
| M59 | rss.py | failed gap line never written | KILLED |  |
| M60 | rss.py | alert sent twice per day (no daily guard) | KILLED |  |
| M61 | rss.py | alerted map never recorded | KILLED |  |
| M62 | rss.py | alert text carries URL | KILLED |  |
| M63 | rss.py | notifier failure breaks cycle | KILLED |  |
| M64 | rss.py | trading_hours 09:15 anchor removed | KILLED |  |
| M65 | rss.py | future pub not capped at now | SURVIVED | test gap: future-dated newest item (+3h) cap unpinned |
| M66 | rss.py | trading window 09:30-16:00 widened | KILLED |  |
| M67 | rss.py | poll_missed grace 10 min -> 0 | KILLED |  |
| M68 | rss.py | poll_missed failures >=3 -> >=4 | KILLED |  |
| M69 | rss.py | calendar unknown not judged as always | KILLED |  |
| M70 | rss.py | content age no last_change_at fallback | KILLED |  |
| M71 | rss.py | provisional day not evaluated | KILLED |  |
| M72 | rss.py | force ignored for unscheduled slot | KILLED |  |
| M73 | rss.py | same-slot repoll guard removed | KILLED |  |
| M74 | rss.py | feed table: duplicate url allowed | KILLED |  |
| M75 | rss.py | feed table: stale_after_min floor 15 -> 14 | KILLED |  |
| M76 | rss.py | feed table: userinfo allowed | KILLED |  |
| M77 | rss.py | feed table: uppercase host allowed | KILLED |  |
| M78 | rss.py | feed table: http allowed | KILLED |  |
| M79 | rss.py | gaps reader: torn tail accepted | KILLED |  |
| M80 | rss.py | gaps reader: unknown field accepted | KILLED |  |
| M81 | base.py | hook before date check (base) | KILLED |  |
| M82 | base.py | base hook exception not caught | KILLED |  |
| M83 | base.py | base hook non-str return accepted | KILLED |  |
| M84 | base.py | base hook result ignored | KILLED |  |
| M85 | cli.py | CLI contact refusal removed | KILLED |  |
| M86 | cli.py | CLI --once not required | KILLED |  |
| M87 | cli.py | CLI gap_write_failed exit 1 -> 0 | KILLED |  |
| M88 | cli.py | CLI bad feed table exit 2 -> 1 | KILLED |  |
| M89 | registry.py | registry: duplicate source allowed | KILLED |  |
| M90 | registry.py | registry: waived ignores date_keyed | SURVIVED | test gap: waived listing without date_keyed unpinned |
| M91 | roots.py | roots add-list missing gaps.jsonl | KILLED |  |
| M92 | rss_check.py | check: ghost feed key not failing | KILLED |  |
| M93 | rss_check.py | check: bad gap ledger not failing | KILLED |  |
| M94 | rss_check.py | check: waived adapters not listed | KILLED |  |
| M95 | rss_check.py | check: to_confirm no warning | KILLED |  |
| M96 | rss_check.py | check: duplicate key not flagged | SURVIVED | test gap: duplicate manifest key not exercised |
| M97 | rss_check.py | plant: miss not injected | KILLED |  |
| M98 | rss_check.py | check: alarms not failing | KILLED |  |
| M99 | rss.py | unreadable state file makes the feed skipped (no poll) | KILLED | |
| M100 | rss.py | skipped not counted as an ok poll | KILLED | |
| M101 | rss.py | not_attempted not counted as failure | KILLED | |
| M102 | rss.py | last_stored_sha never updated | KILLED | |
