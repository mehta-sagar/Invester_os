# Decision 0002 — NSE/BSE access: honest by default, scoped exception (21 Nov 2026, Sagar)

Full note: Project doc research/DECISION_NSE_HEADERS.md.

1. Default for every source: honest identity (`InvestOS/<version> (personal research; <contact>)`), no cookies, no fake Referer.
2. Forward capture of filings uses NSE's official RSS feeds (`nsearchives.nseindia.com/content/RSS/*.xml`, 22 corporate feeds) with the honest UA.
3. Scoped exception, only in named adapters for `www.nseindia.com|.in/api/*` and `api.bseindia.com`, only for one-time backfill, gap-fill after a missed RSS poll, or data with no feed: browser UA, same-origin Referer, cookie warm-up allowed.
4. Conditions: Mac residential IP only; at most 1 request per 2 s on /api; small daily volume; personal non-commercial use; every header sent recorded in the manifest row; a 401/403/429 stops that adapter for the day.
5. Never: TLS-fingerprint impersonation, proxy/IP rotation, CAPTCHA solving, paid scraping actors.
6. Sagar owns the NSE ToS clause-9 risk. If NSE objects or blocks: fall back to RSS + archives + a licensed feed.

Spec impact (stage 1): specs/collect_base.md gets `access_mode` per adapter (`honest` | `scoped_exception`, config allowlist, logged per manifest row); only `scoped_exception` adapters may set UA/Referer/Cookie; deny-list widened (review D4).
