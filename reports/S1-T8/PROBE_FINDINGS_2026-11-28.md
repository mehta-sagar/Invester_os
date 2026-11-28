# Backfill probe findings — 28 Nov 2026, Mac (unreviewed build 9e6e1f9, test copy ~/Invest_OS/s1t8_check)

Probe run 17:14-17:27 IST: 160 requests via real Chrome, no block, no challenge. API origin https://www.nseindia.in (the .com page redirects to .in).

| endpoint | probe | notes |
|---|---|---|
| announcements | confirmed | earliest 2002-07-08; no cap seen (a 31-day window = 16,981 rows); planner max 14 days, 572 windows |
| shareholding | confirmed | 285 windows |
| bulk_deals | confirmed | 6,175 windows (small windows because of the cap) |
| block_deals | confirmed | 883 windows |
| board_meetings | confirmed | 442 windows |
| corporate_actions | needs_review | rows exist before 2005-01-15: start date must go earlier (probe deeper) |
| pit | needs_review | 0 rows in recent windows: wrong endpoint for recent dates (see below) |
| results | needs_review | 0 rows in recent windows (22-26 Nov; possibly genuinely empty), see below |

## Orchestrator follow-up in the Mac built-in browser (same-origin fetch from the NSE page, 2.5 s apart)
The NSE PIT page now calls a NEW endpoint, `/api/corporates-pit-gg?index=equities` (JSON `{data:[...]}`, row keys appId, broadcastDateTime, companyName, exchdisstime, ixbrl, regulation, symbol, typeOfSubmission...).

| request | rows |
|---|---|
| OLD `/api/corporates-pit?...from 01-01-2019 to 07-01-2019` | 737 |
| OLD same, Jun 2024 week | 468 |
| OLD same, 1-7 Jun 2025 | 301 |
| OLD same, 22-26 Nov 2026 (probe) | 0 |
| NEW `/api/corporates-pit-gg?...` 1-7 Jun 2025 | 0 |
| NEW same, 1-7 Jun 2026 | 208 |
| NEW same, 24-25 Nov 2026 | 43 |
| NEW, no dates | 3,825 |
| `/api/corporates-financial-results?index=equities&period=Quarterly` 1-7 Aug 2015 | 315 |
| same, 1-14 Aug 2025 | 2 |
| same, 1-7 Nov 2026 | 1 |

## Conclusions for the fix round
1. **Endpoint eras.** Add an optional `end_date` column to config/backfill_endpoints.csv. The planner never plans past it, and the probe measures the last date with data.
   - PIT = two rows: `pit` (old, ends at the switch date, between Jun 2025 and Jun 2026, to probe) and `pit_gg` (new, starts there).
   - The probe must find the switch date: the last old date with rows and the first new date with rows.
2. **Results**: the old endpoint covers history until ~2025. Since then results are filed via NSE "integrated filing" (cf. the RSS feed Integrated_Filing_Financials). Set `results` end_date to the probed last date with data, and add a row `results_integrated` as `to_probe` with an empty api_path until its endpoint is identified; it is excluded from planning. Do NOT guess the URL.
3. **corporate_actions**: earliest is before 2005-01-15; the probe must search earlier (1995 per older notes) before confirming.
4. Everything else as built. Night 1 can run the confirmed endpoints (core first: announcements, bulk, block; then shareholding, board meetings).
