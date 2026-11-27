# Task report S1-T1 — collector amendment (decisions 0002/0002a)

- Built:
  - access modes per adapter: honest (default) | real_browser | scoped_exception (allowlist + decision file)
  - honest UA `InvestOS/<ver> (personal research; <contact>)`; refuses non-test runs without `[collect] contact`
  - transport: http.client with exactly the given headers, no library-added headers, no redirects followed, TLS verified, env proxies ignored, gzip/deflate (br only if importable), 4x-timeout deadline incl. header phase
  - BrowserTransport interface only; real_browser never uses HTTP transport
  - per-host block policy (401/403/429/451 → host blocked rest of IST day, persisted, fail-closed when state is unreadable or unwritable); halt after 3 blocked days in 7 until clear_halt
  - cross-process per-host rate limiter (stamp after durable write); per-adapter daily caps
  - injectable notifier (osascript on halt); `investos access` CLI; manifest rows and sidecar (schema 2) record access_mode and headers
- Tests: 299 + 16 regression tests written red first by the Test writer; 13 tests the Test writer had written wrong were corrected by the Test writer after the Builder flagged them. Builder edited no tests. 1163 passed, 3 skipped (Linux root); Reviewer: 1143 pass as non-root before fixes.
- Review: PASS WITH MINOR; 86 mutations, 77 killed; 6 real survivors now covered. D1 (fail-open on unwritable state), D2 (limiter spacing under load), D3 (Connection/Content-Length/Transfer-Encoding deny), D5 (header-phase deadline) fixed in ac314a0.
- Spec amended (Resolved 9): limiter stamp after durable write; http.client transport; derived wall clock only when tests inject a monotonic clock.
- Open: macOS run with the next Mac check; dedicated contact email from Sagar (config placeholder until then).
