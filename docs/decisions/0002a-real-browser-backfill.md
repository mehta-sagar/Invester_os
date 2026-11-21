# Decision 0002a — amends 0002: history via a real browser, no spoofing (21 Nov 2026, Sagar)

Supersedes point 3 of 0002 for history and gap-fill. Full note: Project doc research/DECISION_NSE_HEADERS.md.

## Access modes (one per adapter, declared in config, recorded on every manifest row)
| access_mode | What it is | Where it may run | Allowed |
|---|---|---|---|
| `honest` (default) | Plain HTTP client. UA `InvestOS/<version> (personal research; <contact>)`. No cookies, no fake Referer. | anywhere | archives (`nsearchives`), NSE RSS feeds, other public files |
| `real_browser` | A real browser (Playwright driving headful installed Chrome, or the Claude desktop built-in browser) opens the NSE page; requests are made by that page with the browser's own cookies, UA, Referer and TLS. | Sagar's Mac only | one-time history backfill; gap-fill after a missed RSS poll; data with no feed |
| `scoped_exception` | Plain HTTP client sending browser-like headers. | Sagar's Mac only | held in reserve; each adapter needs Sagar's explicit approval recorded as a decision row |

## Rules for real_browser
- Sequential, at most 1 request per 2.5 s per host, small date windows, resumable checkpoints.
- No stealth: do not hide `navigator.webdriver`, no stealth plugins, no synthetic mouse movement or "human-like" jitter, no TLS impersonation, no proxy/IP rotation, no CAPTCHA solving, no paid scraping actors.
- A 401/403/429 or a challenge page stops that adapter for the day; repeated blocks stop the source and go to Sagar (fallbacks: manual "Download (.csv)", licensed NDAL feed).
- Raw response bytes are stored and manifested like any other fetch.

## Verified on the Mac (21 Nov, built-in browser)
corporate-announcements back to 2005 (date-only before ~2012 → treat as end-of-day); market-wide week returns 785 rows; corporates-pit, corporateActions, bulk deals (70-row cap), financial-results (small windows needed) all HTTP 200.

Sagar owns the NSE ToS clause-9 risk.
