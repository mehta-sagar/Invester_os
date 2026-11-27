# Spec trading_calendar   (from brief S1-T4, task S1-SPEC2, 22 Nov 2026)

Module: `src/investos/calendar/trading.py` (+ `src/investos/calendar/__init__.py`), reference tables `config/calendar/close_eras.csv`, `config/calendar/sessions.csv`, `config/calendar/years.csv`, and a built-in check `investos check calendar` (registered like the other checks of `specs/check_runner.md`). Python 3.11+, stdlib only plus `investos.core.config`. All times are IST (`zoneinfo.ZoneInfo("Asia/Kolkata")`, no DST); every datetime returned is timezone-aware. No network access, no clock reads: callers pass dates.

Why this module exists: point-in-time stamping (what was known when) needs two facts per trade date that cannot be guessed: was it a trading day, and when did that day's official close price become final and public. Both changed in 2026 (closing auction session, CAS), so both are dated rows in versioned tables, never constants in code.

## Inputs (schema)

### `config/calendar/years.csv`: which exchange lists have been loaded
Header `exchange,year,list_source,loaded_at`. One row per (exchange, calendar year) whose holiday list has been entered completely, including that year's announced special sessions as of `loaded_at` (ISO date). `exchange` in `NSE`, `BSE`. `list_source` = free text or manifest key of the stored holiday list (non-empty).

### `config/calendar/sessions.csv`: dated facts about single days
Header `date,exchange,segment,status,open_ist,close_ist,evidence_kind,evidence_ref,recorded_at`.
- `date` ISO date. `exchange` `NSE` or `BSE`. `segment` `CM` (cash), `FO` (futures and options) or `ALL`.
- `status` in `holiday` (market closed on a weekday), `special_session` (a session on a day that is normally closed, or an extra or shortened session on a normal day, for example a weekend session or the Diwali muhurat session), `trading` (a normal session confirmed by evidence).
- `open_ist`, `close_ist`: `HH:MM` or empty. Mandatory (both) for `special_session`; they are the only session times for that day and override the era table (R9). Empty for `holiday` and `trading`.
- `evidence_kind` in `holiday_list` (the exchange's published holiday list), `circular` (an exchange circular or notice, `evidence_ref` = its number or URL), `bhavcopy` (a stored end-of-day file whose in-file date matched the key, `evidence_ref` = manifest `logical_key` plus `source`, written `<source>:<key>`; only sources whose `date_check` is `enforced`, see `specs/collect_base.md` R35), `manual` (Sagar's entry, `evidence_ref` = reason).
- `recorded_at` ISO date. One row per (`date`, `exchange`, `segment`): a duplicate is an error (R2).

### `config/calendar/close_eras.csv`: the versioned closing-price table
Header: `era_id,effective_from,effective_to,exchange,segment,category,continuous_end,cas_start,cas_end,cas_applicable,close_method,market_close,close_earliest,post_close_start,post_close_end,cas_carry_forward,settlement_window,public_lag_min,verified`.
- `era_id` unique text, e.g. `E2-CM-cat1`. `effective_from` ISO date (inclusive). `effective_to` ISO date (inclusive) or empty (open, only the newest row of a group). A group is the triple (`exchange`, `segment`, `category`).
- `segment` `CM` or `FO`. `category` (the `security_category` argument below): `cat1` (cash stocks that are F&O-eligible, "Category I"), `non_fo` (all other cash stocks), `index_fo` (index futures and options), `index_fo_expiring` (index F&O on their expiry day), `stock_fo` (stock futures and options). CM rows use `cat1`/`non_fo`; FO rows use the other three.
- Times `HH:MM` IST or empty. `continuous_end` = last minute of continuous trading. `cas_start`/`cas_end` = closing auction window, empty when none. `cas_applicable` in `yes`, `no`, `unverified` (R7). `close_method` in `vwap_last_30m` (VWAP of the last 30 minutes of continuous trading, 15:00 to 15:30), `closing_auction` (auction price: reference price, then a random close), `fo_session_end` (derivatives: the close of the session; no auction). `market_close` = the time from which that day's close price for this group is final (the latest possible moment when the method has a random end). `close_earliest` = earliest moment the close can have been set (equals `market_close` when not random). `post_close_start`/`post_close_end` = post-close session at the closing price (CM only, empty otherwise). `cas_carry_forward` `yes`/`no`: pending limit and iceberg orders carry forward into CAS (a flag only, no timing effect). `settlement_window` = window whose VWAP is the expiry settlement price (FO rows of expiry days), empty otherwise. `public_lag_min` = whole minutes between `market_close` and the earliest moment the day's end-of-day data may be treated as public (R10). `verified` in `circular` (checked against the NSE circular text), `press` (SEBI circular coverage only), `assumed` (not checked).

### The rows the first version ships (the Builder copies these; they are test data too)
Marker for every row below that is not `circular`: `verified = press`. Timings are "verified 22 Nov 2026 from SEBI circular coverage dated 15-16 Oct 2026; re-check against the NSE circular text in stage 1 probes". Rows are given for `exchange = NSE`; the `BSE` rows are identical copies with `verified = assumed`.

| era_id | from .. to | seg / category | continuous_end | CAS | close_method | market_close | notes |
|---|---|---|---|---|---|---|---|
| E1-CM-cat1, E1-CM-non_fo | 2010-01-01 .. 2026-08-02 | CM / cat1, non_fo | 15:30 | none | vwap_last_30m | 15:30 | `cas_applicable = no` |
| E1-FO-* (three rows) | 2010-01-01 .. 2026-08-02 | FO / index_fo, index_fo_expiring, stock_fo | 15:30 | none | fo_session_end | 15:30 | |
| E2-CM-cat1 | 2026-08-03 .. 2026-12-06 | CM / cat1 | 15:15 | 15:20 to 15:35 | closing_auction | 15:35 | `cas_applicable = yes` |
| E2-CM-non_fo | 2026-08-03 .. 2026-12-06 | CM / non_fo | 15:30 | none | vwap_last_30m | 15:30 | `cas_applicable = no` |
| E2-FO-* (three rows) | 2026-08-03 .. 2026-12-06 | FO / index_fo, index_fo_expiring, stock_fo | 15:40 | none | fo_session_end | 15:40 | expiring index F&O not stated separately: same 15:40 (open question 3) |
| E3-CM-cat1 | 2026-12-07 .. 2027-02-07 | CM / cat1 | 15:30 | 15:30 to 15:40 | closing_auction | 15:40 | `close_earliest = 15:38` (random close 15:38 to 15:40; reference price from the 15:30 to 15:31 last-15-minute VWAP); post-close 15:50 to 16:00; `cas_applicable = yes`; `cas_carry_forward = no` |
| E3-CM-non_fo | 2026-12-07 .. 2027-02-07 | CM / non_fo | 15:30 | 15:30 to 15:40 | closing_auction | 15:40 | `cas_applicable = unverified`: treated as applicable (R7) |
| E3-FO-index_fo, E3-FO-stock_fo | 2026-12-07 .. 2027-02-07 | FO / index_fo, stock_fo | 15:45 | none | fo_session_end | 15:45 | `settlement_window = 15:00-15:30` |
| E3-FO-index_fo_expiring | 2026-12-07 .. 2027-02-07 | FO / index_fo_expiring | 15:30 | none | fo_session_end | 15:30 | expiring index F&O stop at 15:30; `settlement_window = 15:00-15:30` |
| E4-* | 2027-02-08 .. (open) | same five groups as E3 | same as E3 | same as E3 | same as E3 | same as E3 | identical timings; `cas_carry_forward = yes` on both CM rows |
All E3 and E4 rows use `public_lag_min = 30`, as do E1 and E2 (open question 4).

## Outputs (schema)
```python
class CalendarError(Exception): ...                 # bad table, conflicting rows, date outside the tables
class NotATradingDay(CalendarError): ...            # asked for a close on a day that is not (confirmed) trading

class DayStatus(str, Enum):
    TRADING = "trading"          # confirmed by evidence (R3)
    CLOSED = "closed"            # confirmed closed: listed holiday, or weekend in a loaded year with no special session
    PROVISIONAL = "provisional"  # weekday in a loaded year, not a listed holiday, no evidence yet: expected, NOT confirmed
    UNKNOWN = "unknown"          # year not loaded and no row

@dataclass(frozen=True)
class EraRow: ...                # one parsed close_eras.csv row, times as datetime.time, dates as date

@dataclass(frozen=True)
class SessionTimes:
    date: date
    close_era: str               # era_id, or "special:<date>" for a special session row
    market_close: datetime       # aware IST; the moment the close price is final (R6)
    close_earliest: datetime
    close_method: str
    price_fields: tuple[str, ...]  # ("close_price",) or ("close_price", "settlement_price") (R11)

@dataclass(frozen=True)
class PriceFacts:                # the container later stages fill (R11): two separate fields, never derived from each other
    close_price: float | None = None
    settlement_price: float | None = None
    close_era: str | None = None # era_id the close_price was computed under

class TradingCalendar:
    def __init__(self, cfg: Config, *, close_eras: Path | None = None, sessions: Path | None = None, years: Path | None = None)
    def day_status(self, d: date, *, exchange: str = "NSE", segment: str = "CM") -> DayStatus
    def is_trading_day(self, d: date, *, exchange="NSE", segment="CM") -> bool   # True only for TRADING
    def era_for(self, d: date, segment: str, security_category: str, *, exchange="NSE") -> EraRow
    def market_close_ist(self, d: date, segment: str, security_category: str, *, exchange: str = "NSE",
                         allow_provisional: bool = False) -> datetime
    def after_close_public_at(self, d: date, segment: str, security_category: str, *, exchange: str = "NSE",
                              allow_provisional: bool = False) -> datetime
    def known_at(self, d: date, segment: str, security_category: str, fetched_at: datetime, *, exchange="NSE") -> datetime
```
Default paths: the three CSV files under `<repo_root>/config/calendar/`. Tables are loaded and validated at construction; nothing is cached across instances.

## Rules (numbered, each gets at least one test and one planted failure)

R1. Trading days are confirmed, never assumed from the weekday. `is_trading_day` is `True` only when `day_status == TRADING`. A weekday is never `TRADING` because it is a weekday, nor because it is missing from a holiday list: it is `PROVISIONAL` at best. Code outside this module may schedule fetches on `PROVISIONAL` days but must not stamp, join or compute returns on them as confirmed.
R2. Table loading is strict. A wrong header, an unknown `exchange`/`segment`/`status`/`evidence_kind`/`category`/`close_method`/`verified` value, a malformed date or time, a duplicate (`date`,`exchange`,`segment`) in `sessions.csv`, a duplicate `era_id`, a duplicate (`exchange`,`year`) in `years.csv`, or a `special_session` row without both `open_ist` and `close_ist` (or with `close_ist <= open_ist`) raises `CalendarError` naming file, line and field. An empty `sessions.csv` (header only) is valid.
R3. Status resolution for (`d`, `exchange`, `segment`), in this order: (a) a row for (`d`,`exchange`,`segment`), else the `ALL` row for (`d`,`exchange`); the specific segment row wins over `ALL`. `special_session` or `trading` gives `TRADING`; `holiday` gives `CLOSED`. (b) no row, year of `d` loaded in `years.csv` for the exchange: Saturday or Sunday gives `CLOSED`; a weekday gives `PROVISIONAL`. (c) no row, year not loaded: `UNKNOWN`. A `trading` row must have `evidence_kind` of `circular`, `bhavcopy` or `manual`; a `trading` row with `holiday_list` evidence is a load error (a list names closures, it cannot confirm a session). A `holiday` row on a weekend date is allowed (redundant). A `special_session` on any day is allowed.
R4. Conflicts are loud. A `trading` row for (`d`,`exchange`,`segment`) alongside a `holiday` row that applies to the same segment (specific or `ALL`) for the same date raises `CalendarError` at load (a `special_session` is the deliberate exception and may coexist with an `ALL` or other-segment `holiday`, see A7; the same key twice is a duplicate, R2); the module never picks a winner between contradicting evidence, a human resolves it.
R5. `years.csv` gates `CLOSED`/`PROVISIONAL`. Loading a year does not create `TRADING` days. A special session announced after `loaded_at` is entered as a `sessions.csv` row; until then its weekend date reads `CLOSED` (a known blind spot, open question 1). `investos check calendar` reports the newest loaded year and warns when `date.today()` is in a year that is not loaded or in December of a year whose successor is not loaded.
R6. `market_close_ist(d, segment, security_category)` returns the aware IST datetime at which the close price of that group is final for trade date `d`: `d` combined with the `market_close` of the era row whose [`effective_from`, `effective_to`] contains `d` for (`exchange`,`segment`,`category`). Rules: `d` must be `TRADING`, else `NotATradingDay` (`PROVISIONAL` passes only with `allow_provisional=True`, which is meant for scheduling; `UNKNOWN` and `CLOSED` always raise). A `special_session` row for (`d`,`exchange`,segment or `ALL`) overrides the era: `market_close = close_ist` of that row, `close_earliest = close_ist`, `close_era = "special:<date>"`, `close_method = "fo_session_end"` for FO and `vwap_last_30m` for CM (open question 5). A date before the first era of the group, or in a gap, raises `CalendarError` (R8 makes gaps impossible after load); an unknown `security_category` or `segment` raises `ValueError`. The caller supplies the category; for `index_fo_expiring` the caller decides from the expiry calendar (not in this module).
R7. CAS applicability is never assumed away. When the matching era row has `cas_applicable = unverified`, `market_close` is the row's `market_close` (the latest time), which is the conservative choice for point-in-time use: data is treated as known later, never earlier. Rows with `cas_applicable = no` and `yes` use their own `market_close`. `investos check calendar` lists every `unverified` and `assumed` row as a WARNING (exit status unaffected).
R8. Era table integrity (checked at load and by `investos check calendar`): within each group the rows are sorted by `effective_from`, contiguous (the next `effective_from` is exactly the previous `effective_to` plus one day), non-overlapping, only the last has an empty `effective_to`, `effective_from <= effective_to`, and `market_close >= close_earliest`, `market_close >= continuous_end`, `market_close >= cas_end` when a CAS exists, `post_close_start >= market_close` when present. Every group that exists at all has a row on every date from the group's first `effective_from` onwards. A violation raises `CalendarError` naming the two `era_id`s.
R9. Special sessions: a `special_session` row defines the only session of that day for its segment; `after_close_public_at` and `known_at` use it exactly like an era (R6, R10). A shortened normal day (for example the Diwali muhurat session, which is an evening session) is a `special_session` row; a normal full day is never a `special_session`.
R10. `after_close_public_at(d, segment, security_category)` = `market_close_ist(...)` plus `public_lag_min` minutes of the matching era row (`public_lag_min` is always read from the era row that applies to `d` for that group, also on a special session day). It is the earliest moment the end-of-day data of trade date `d` may be treated as public. `known_at(d, ..., fetched_at)` = `max(after_close_public_at, fetched_at)` where `fetched_at` must be aware (naive raises `ValueError`); a file fetched later than publication is known only from when it was fetched. Stamping code uses `known_at`, never the trade date at 00:00 and never the fetch time alone.
R11. Close price and settlement price are separate facts. `close_price` (the official end-of-day price by the era's `close_method`, final at `market_close`) and `settlement_price` (the price that settles an expiring contract, for index and stock F&O from E3: the VWAP of the `settlement_window` 15:00 to 15:30 on expiry day) are two named fields, never one column, never filled from each other, never coalesced. A source that publishes only one leaves the other null. `SessionTimes.price_fields` names which exist for the group on that date: `("close_price",)` for CM and for FO days without expiry settlement; `("close_price", "settlement_price")` for FO rows with a non-empty `settlement_window`. Every stored close price carries the `close_era` (the `era_id`) it was computed under, so later code can tell a VWAP close from an auction close. A series built across eras must keep the `close_era` column; joining closes of different `close_method` without it is a defect. (The storage tables are built in a later stage; this rule fixes the names and the invariant.)
R12. Provenance and `verified`. Each row's `verified` is read, kept, and reported by `investos check calendar` with counts per value. `press` and `assumed` rows are legitimate in stage 1; a row may be set to `circular` only by a human after reading the exchange circular text (the stage-1 probes task), and the row's timings are then re-checked against that text. The module has no network code and never edits the CSV files.
R13. `investos check calendar` fails (exit 1) on any `CalendarError` from R2, R4, R8 and passes otherwise; WARNINGs per R5 and R7 and R12. It has a planted failure (`@plant_for("calendar")`): a copy of the tables with one era gap, which the check must reject.
R14. Purity. No function reads the system clock or the network. `date` arguments are `datetime.date` (a `datetime` instance raises `TypeError`). Results are deterministic given the three files. Weekdays are computed with `date.weekday()`.

## Edge cases
- 2026-08-02 (last day of E1) versus 2026-08-03 (first of E2), 2026-12-06 versus 2026-12-07, 2027-02-07 versus 2027-02-08: the era is chosen by trade date in IST, not by fetch time.
- A holiday listed for `CM` only: `FO` stays `PROVISIONAL`/`TRADING` by its own rules (rows are per segment).
- A weekday holiday that is also a `special_session` date (a special session on a holiday): conflict only if both rows apply to the same segment (R4); `holiday` on `CM` and `special_session` on `FO` is legal.
- `BSE` and `NSE` are independent: a day can be `TRADING` on one and `CLOSED` on the other.
- Leap day, year end, December 31 versus January 1 resolve through `years.csv` by the calendar year of `d`.
- A trade date before 2010-01-01 raises `CalendarError` (not covered, open question 2).
- Muhurat session: `special_session` with evening `open_ist`/`close_ist` on a day that is also a `holiday` for the daytime would be two rows for the same key and is rejected by R2 (a duplicate); record the day as one `special_session` row and note the holiday in `evidence_ref`.

## Acceptance tests (in words)
Fixtures: the shipped tables (copied into `tmp_path`), a builder for small tables, a `Config` for a temp repo. Dates used are fixtures; none claims to be a real holiday except where the shipped era rows are checked.

A1. (R1, planted) Given year 2026 loaded and no row for Monday 2026-11-16, then `day_status == PROVISIONAL`, `is_trading_day` is `False`, and `market_close_ist` raises `NotATradingDay` (and with `allow_provisional=True` returns a value). A calendar must not return `TRADING` for it.
A2. (R3) Given a `trading` row with `evidence_kind = bhavcopy` for that Monday, then `TRADING` and `is_trading_day` is `True`.
A3. (R3, planted) Given a `holiday` row on a Wednesday, then `CLOSED`; given a Saturday and a Sunday with year loaded and no rows, `CLOSED`; given a Saturday with a `special_session` row (09:15 to 10:15 fixture), `TRADING`.
A4. (R3, R5, planted) Given year 2028 not in `years.csv` and no rows, then a weekday and a Saturday of 2028 are both `UNKNOWN`; `market_close_ist` raises `NotATradingDay`.
A5. (R3, planted) Given a `trading` row with `evidence_kind = holiday_list`, then loading raises `CalendarError`.
A6. (R4, planted) Given a `holiday` row and a `bhavcopy` `trading` row for the same date and segment, then loading raises `CalendarError` naming the date; given `holiday` on `CM` and `special_session` on `FO` for one date, loading succeeds.
A7. (R3) Given an `ALL` row `holiday` and a specific `FO` row `special_session` for the same date, the specific row wins for `FO` and `CM` stays `CLOSED`.
A8. (R2, planted) Given a header typo, `status = "Holiday"` (case), `date = 2026-13-01`, a duplicate (`date`,`exchange`,`segment`), a `special_session` with empty `close_ist`, or `close_ist` before `open_ist`, then each raises `CalendarError` naming file and line.
A9. (R6, planted) Given the shipped tables and a `sessions.csv` that confirms each date used as `trading` (fixture rows, any evidence kind but `holiday_list`): `market_close_ist` for a CM `cat1` stock on 2026-08-02 gives 15:30 IST (E1); on 2026-08-03 gives 15:35 (E2); `non_fo` on 2026-08-03 gives 15:30; on 2026-12-06 `cat1` gives 15:35; on 2026-12-07 `cat1` gives 15:40 and `non_fo` gives 15:40 (unverified, R7); on 2027-02-08 `cat1` gives 15:40. Each result is aware, with `tzinfo` key `Asia/Kolkata`.
A10. (R6, planted) Given FO: `stock_fo` and `index_fo` on 2026-08-02 give 15:30, on 2026-08-03 give 15:40, on 2026-12-06 give 15:40, on 2026-12-07 give 15:45; `index_fo_expiring` on 2026-12-07 gives 15:30, on 2026-08-03 gives 15:40.
A11. (R6, planted) Given a date before 2010-01-01, or an unknown category `cat2`, or segment `EQ`, or a `datetime` where a `date` belongs, then `CalendarError`, `ValueError`, `ValueError`, `TypeError`.
A12. (R6, R9) Given a `special_session` row 2026-12-12 (a Saturday) with `close_ist = 10:15`, then `market_close_ist` for CM `cat1` is 10:15 that day, `close_era == "special:2026-12-12"`, and the era table is not consulted for the time.
A13. (R7) Given the E3 `non_fo` row with `cas_applicable = unverified`, then its `market_close` is 15:40 (not 15:30); the check lists it as a WARNING; changing the row to `no` with `market_close = 15:30` makes `market_close_ist` return 15:30 (the table drives it).
A14. (R8, planted) Given a copy of the tables with a one-day gap between E2 and E3 for `CM/cat1` (E2 `effective_to` 2026-12-05), then loading raises `CalendarError`; given two overlapping rows; given the open-ended row not last; given `market_close` before `continuous_end`; given an `effective_to` before `effective_from`, each raises naming the `era_id`s.
A15. (R10) Given `cat1` on 2026-12-07 (close 15:40, lag 30), then `after_close_public_at` is 16:10 IST; for `cat1` on 2026-08-03 it is 16:05; for a special session closing 10:15 it is 10:45 (lag from the era row that would apply).
A16. (R10, planted) Given a naive `fetched_at`, `ValueError`; given an aware `fetched_at` of 2026-12-07T15:35+05:30 (before publication), `known_at` is the `after_close_public_at` instant (16:10 IST); given a `fetched_at` the next morning, the result is the fetch time; the trade date at 00:00 is never returned.
A17. (R11) Given `era_for` and `SessionTimes` for FO `index_fo_expiring` on 2026-12-07 (a fixture confirmed day), `price_fields == ("close_price", "settlement_price")` and the era's `settlement_window == 15:00-15:30`; for CM `cat1` it is `("close_price",)`.
A18. (R11, planted) Given `PriceFacts`, constructing it with only `settlement_price` leaves `close_price` as `None` (and the reverse); a test also scans the module source and finds no expression that assigns one of the two names from the other.
A19. (R12, R13) Given the shipped tables, `investos check calendar` passes, prints counts by `verified` (`press` for every shipped NSE row, `assumed` for BSE), and WARNs for the `unverified` and `assumed` rows; its exit status is 0. Given the planted broken copy (an era gap, from `@plant_for("calendar")`), the check fails.
A20. (R5) Given today's date (injected) in December 2026 with 2027 not loaded, then `investos check calendar` WARNs; with 2027 loaded, no warning.
A21. (R14, planted) Given a `datetime` instead of a `date` for any method, `TypeError`; given the system clock monkeypatched to raise on read, no method raises (no clock use); calling every method twice returns equal results.
A22. (Edge) Given an exchange `NSE` day `TRADING` and the same date `CLOSED` on `BSE`, the two answers differ and neither affects the other.
A23. (R3, R4) Given a Saturday `special_session` row on `CM` for NSE and nothing for `FO`, then `FO` of that date is `CLOSED` (weekend, year loaded), `CM` is `TRADING`.

## Not in this module
- Fetching holiday lists, circulars or bhavcopies, and any job that turns date-checked bhavcopy files into `sessions.csv` rows (a later task; open question 6).
- The expiry calendar (which dates are expiries, which index F&O expire when); the caller passes `index_fo_expiring`.
- Storing close prices and settlement prices (later stage); this spec fixes names, separation and `close_era`.
- Pre-open session, circuit-limit and market-wide halts (an unscheduled halt does not change `market_close` here).
- Commodity, currency and debt segments; MCX; other holidays calendars.
- Timezone handling beyond IST.

## Open questions for Sagar (Spec writer, 22 Nov 2026)
1. A special session announced after a year's list was loaded is only known once a `sessions.csv` row is added; until then a weekend date reads `CLOSED`. Is a manual weekly look at the NSE circulars page acceptable, or should the later probe task poll the circulars feed?
2. Earliest supported trade date is 2010-01-01 with E1 timings (15:30 close, VWAP of the last 30 minutes); I have not checked that the same rule held for the whole of 2010 onwards. Earlier session hours are not modelled. Do you need history before 2010, and should the start date be checked first?
3. For the 2026-08-03 to 2026-12-06 era the coverage says F&O trade to 15:40; it does not say whether expiring index F&O stop earlier. Rows use 15:40 for all three FO categories until the NSE circular text is read.
4. `public_lag_min = 30` after `market_close` is my assumption (bhavcopy and index files are published within the hour after the close). Please confirm 30 or give a number; the stage-1 probes can measure the real publication times from the manifest `fetched_at` of files.
5. A special session on CM uses `vwap_last_30m` and on FO `fo_session_end` for `close_method`; the muhurat session's real close rule is not checked.
6. Who writes `trading` rows with `bhavcopy` evidence: a later routine that reads the manifest, or you by hand? Suggestion: a routine that adds a row for every date whose date-checked bhavcopy (R35 of collect_base) is stored.
7. Whether CAS applies to `non_fo` stocks after 2026-12-07 ("applicable stocks" in the coverage). Rows treat them as applicable (the later, safer close time) until the circular is read.
