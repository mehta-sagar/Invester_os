# Task report S0-T4 — collector base class with run sidecar

- Built: src/investos/acquire/base.py (Collector base: RunRange, honest fixed headers, retry/backoff on 429/5xx, block statuses 401/403/451 stop before body check, check_body gate, safe_unzip, run sidecar written via temp+fsync+replace, structured log events). manifest.py gained a per-source in-memory index (fix D1).
- Tests: 558 pass, 2 skip (535 after build + 23 regression cases). Written first by the Test writer and seen red; the Builder did not edit tests. Orchestrator reran 3×.
- Exit gate: demo 1 (fetch one file), 2 (broken body rejected), 3 (re-run fetches nothing) in sample_run.txt; re-shown after fixes in sample_run_after_fixes.txt. 2000-key skip-only re-run: 0.07 s, 0 HTTP calls (38 s before fix).
- Review: PASS WITH MINOR (review.md). 72 mutation probes, 57 killed; 9 real survivors, 7 now killed by guard tests (5xx range, ERROR level, atomic sidecar ×2, UTC conversion, sorted outputs). Defects D1, D2, D3, D5, D6 fixed (commit 33befa5).
- Open → S0-AUD / stage 1:
  - D4 extra_headers deny-list too short (Authorization, Forwarded, X-Client-IP, True-Client-IP, Via, Host, CRLF values). Goes with decision 0002 (access_mode) in the stage-1 collect spec change.
  - D7 manifest failure mid-archive leaves member lines without the zip line (not run); D8 minor sidecar leftovers.
  - Survivors not covered by spec: zip line stored before members (M49), transport-error reason text (N12).
  - base.py keeps its own copy of the source-name regex because test_A6 pins manifest's public API (D6 fix); keep the two in sync or relax A6.
  - Index cache validity rests on (inode, size, mtime_ns, ctime_ns); audit should confirm this is enough.
  - Production Transport is not written yet; it must send only the given headers (httpx/requests add Accept/Accept-Encoding) and decode bodies (brotli not in stdlib).
- Tokens: not tracked this task.
