# Spec collect_base   (from brief S0-T4)

Module: `src/investos/collect/base.py` (+ `src/investos/collect/__init__.py`). Python 3.11+, stdlib only plus `investos.core.*`. All HTTP goes through an injected `Transport`; tests use a fake. All time and sleeping are injected so tests run instantly.

## Inputs (schema)

```python
class Outcome(str, Enum):
    FETCHED = "fetched"                  # valid body, has data, stored
    FETCHED_EMPTY = "fetched_empty"      # valid body, legitimately zero records, stored
    MISSING_HOLIDAY = "missing_holiday"  # source says no file for this key (e.g. market holiday); nothing stored
    UNREADABLE = "unreadable"            # error page, block, malformed, retries exhausted; nothing stored
    SKIPPED = "skipped"                  # manifest already holds the key; zero HTTP calls
    INCOMPLETE = "incomplete"            # paged response hit exactly the page limit; nothing stored

@dataclass(frozen=True)
class Response:
    status: int
    headers: Mapping[str, str]           # header names lowercased
    body: bytes
    url: str

class TransportError(Exception): ...    # timeouts, connection reset, DNS failure

class Transport(Protocol):
    def get(self, url: str, *, headers: Mapping[str, str], timeout: float) -> Response: ...
    # must NOT follow redirects; 3xx is returned as-is

@dataclass(frozen=True)
class BodyVerdict:
    outcome: Outcome                     # one of FETCHED, FETCHED_EMPTY, MISSING_HOLIDAY, UNREADABLE (INCOMPLETE/SKIPPED are never returned by subclasses)
    reason: str = ""                     # short text, mandatory when outcome != FETCHED
    record_count: int | None = None      # number of records in the body if the subclass knows it (paged sources)

@dataclass(frozen=True)
class RunRange:
    start: date                          # inclusive
    end: date                            # inclusive, end >= start

@dataclass(frozen=True)
class UnzipLimits:
    max_members: int = 10_000
    max_total_bytes: int = 1_073_741_824   # 1 GiB uncompressed, counted while streaming
    max_ratio: float = 100.0               # total uncompressed / compressed archive size
    max_member_bytes: int = 536_870_912
```

```python
class Collector(ABC):
    # class attributes set by subclasses
    source: ClassVar[str]                          # matches manifest source regex
    source_version: ClassVar[str]                  # e.g. "bse-bhavcopy-v1"
    page_limit: ClassVar[int | None] = None        # records per page for paged sources
    archive: ClassVar[bool] = False                # True: successful bodies are zip files
    extra_headers: ClassVar[Mapping[str, str]] = {}
    timeout_s: ClassVar[float] = 30.0
    unzip_limits: ClassVar[UnzipLimits] = UnzipLimits()

    def __init__(self, *, transport: Transport, manifest: ManifestStore, cfg: Config,
                 run_id: str | None = None, sidecar_dir: Path | None = None,
                 clock: Callable[[], datetime] | None = None,        # aware UTC now
                 monotonic: Callable[[], float] | None = None,       # default time.monotonic
                 sleeper: Callable[[float], None] | None = None)     # default time.sleep

    @abstractmethod
    def keys(self, run_range: RunRange) -> Iterable[str]            # logical keys, e.g. ISO dates
    @abstractmethod
    def url(self, key: str) -> str                                  # absolute https URL
    @abstractmethod
    def check_body(self, key: str, resp: Response) -> BodyVerdict

    def run(self, run_range: RunRange) -> RunResult

@dataclass(frozen=True)
class RunResult:
    status: Literal["complete", "incomplete", "aborted"]
    outcomes: Mapping[str, Outcome]       # key -> outcome (keys in keys() order; NOT_ATTEMPTED keys are absent here, see sidecar)
    http_calls: int
    sidecar_path: Path
    sidecar_replaced_previous: bool

def safe_unzip(data: bytes, *, limits: UnzipLimits = UnzipLimits()) -> list[tuple[str, bytes]]
class UnsafeArchiveError(Exception): ...
```
Defaults: `run_id` = `investos.core.log.get_run_id()`; `sidecar_dir` = `cfg.zones["manifests"] / f"{source}.runs"` (created if missing); rate interval = `cfg.rate_limits.interval_for(host)`.

## Outputs (schema)

### Stored data
Only through `ManifestStore.store(source, bytes, url=..., source_version=..., logical_key=..., run_id=..., fetched_at=...)`. For `archive=False`: one call per `fetched`/`fetched_empty` key with `logical_key = key`. For `archive=True`: one call for the zip itself (`logical_key = key`) and one per extracted member (`logical_key = f"{key}/{member_name}"`).

### Run sidecar
Path `<sidecar_dir>/<start>_<end>.json` (ISO dates). One JSON object, written with `json.dumps(obj, sort_keys=True, separators=(",", ":"), ensure_ascii=False)` plus `"\n"`, via temp file + fsync + `os.replace`. No floats anywhere.
```json
{
 "schema": 1,
 "source": "bse_bhavcopy",
 "source_version": "bse-bhavcopy-v1",
 "run_id": "20261121T093000Z-9f3a1c2e",
 "range": {"start": "2026-11-17", "end": "2026-11-21"},
 "started_at": "2026-11-21T09:30:00.000000Z",
 "finished_at": "2026-11-21T09:30:04.000000Z",
 "status": "complete",
 "expected": 5,
 "received": 5,
 "outcomes": {"fetched": 3, "fetched_empty": 0, "missing_holiday": 2, "unreadable": 0, "skipped": 0, "incomplete": 0, "not_attempted": 0},
 "gaps": [{"key": "2026-11-18", "outcome": "unreadable", "reason": "http 503 after 4 attempts"}],
 "outputs": [{"key": "2026-11-17", "sha256": "<64 hex>"}]
}
```
`expected` = number of distinct keys yielded by `keys()`. `received` = number of keys whose outcome is `fetched`, `fetched_empty`, `skipped` or `missing_holiday`. `gaps` = keys with outcome `unreadable`, `incomplete` or `not_attempted` (reason text mandatory), sorted by key. `outputs` = for each key with a stored/known entry (`fetched`, `fetched_empty`, `skipped`), the zip-or-file `sha256` from the manifest (for archives, also one row per member with `key` = `f"{key}/{member}"`), sorted by `key`. `status`: `complete` iff `expected > 0` and `gaps == []`; `aborted` if the run stopped early (rule R8); otherwise `incomplete`.

## Rules (numbered — each gets ≥1 test and ≥1 planted failure)

R1. Never parse: `base.py` imports no JSON/CSV/HTML/XML/Excel parser for source content (`json` is allowed only for the sidecar). Subclass `check_body` may inspect bytes; the base only stores what it fetched, byte-for-byte.
R2. Honest headers: every request carries exactly `{"User-Agent": "investos/<__version__>", **extra_headers}`. `extra_headers` keys are compared lowercased and must not be any of `user-agent`, `referer`, `origin`, `cookie`, `x-forwarded-for`, `x-real-ip`; a subclass that sets one raises `ValueError` at instantiation. No other headers are added. There is no code path that changes identity after a block.
R3. Rate limit: for each host (`urlparse(url).hostname`, lowercased) consecutive request starts are separated by at least `cfg.rate_limits.interval_for(host)` seconds (default 0.35), measured with `monotonic` and enforced with `sleeper(wait)` where `wait = interval - (now - last_start)` if positive. The first request to a host does not wait. Retries are requests and obey the same spacing. Hosts are independent.
R4. Retries: a request is retried only on `TransportError`, HTTP 429 or HTTP 5xx. Maximum attempts per key = 4 (the first try plus 3 retries); the 4th retry is never attempted. Between attempt n and n+1 the collector calls `sleeper(2**(n-1))` seconds (1, 2, 4) in addition to the rate-limit spacing. After the last failed attempt the key is `unreadable` with reason `"<cause> after 4 attempts"` and the event is logged at ERROR.
R5. Blocks are final: HTTP 401, 403 or 451 is never retried and never worked around; the key is `unreadable` (reason `"blocked http <status>"`) and the run aborts (R8). Every other status (2xx, 3xx, 404, other 4xx) is passed to `check_body` untouched.
R6. Body check before store: `check_body(key, resp)` is called for every non-retried response; HTTP 200 alone is never proof. Nothing is stored unless the verdict is `FETCHED` or `FETCHED_EMPTY`. Additionally the base downgrades a `FETCHED`/`FETCHED_EMPTY` verdict to `UNREADABLE` (reason `"non-2xx status <n>"`) when `resp.status` is not in 200..299, and treats an exception raised by `check_body`, or a return value that is not a `BodyVerdict`, or a verdict with outcome `SKIPPED`/`INCOMPLETE`, as `UNREADABLE` (reason `"check_body error: ..."` / `"invalid verdict"`).
R7. Four outcomes kept separate: `fetched`, `fetched_empty`, `missing_holiday`, `unreadable` are distinct in logs, `RunResult.outcomes`, and sidecar counts; `missing_holiday` and `unreadable` are never merged and never stored. Plus `skipped` (R9) and `incomplete` (R10).
R8. Stop-and-log: when a key becomes `unreadable` through retry exhaustion (R4) or a block (R5), the run stops: no further key is requested, every remaining key from `keys()` gets sidecar outcome `not_attempted` (reason `"run aborted after <key>"`), `status = "aborted"`, and an ERROR log line is emitted. A key made `unreadable` by `check_body` or by a non-2xx downgrade does NOT abort the run.
R9. Skip on manifest hit: before any HTTP call for a key, if `manifest.get_by_key(source, key)` is not `None` the outcome is `skipped` with zero HTTP calls, zero sleeps and zero writes. A re-run over a fully fetched range therefore has `http_calls == 0` and all outcomes `skipped`. Keys with outcomes `missing_holiday`, `unreadable`, `incomplete` leave no manifest entry and are re-attempted on the next run.
R10. Page limit: if `page_limit` is not `None` and the verdict is `FETCHED` and `verdict.record_count == page_limit`, the outcome becomes `INCOMPLETE` (reason `"page limit <n> reached"`), nothing is stored, and the key is a gap. If `record_count < page_limit` or `record_count is None`, the verdict stands. `record_count > page_limit` is also treated as `INCOMPLETE`.
R11. Writes only via the manifest writer: the base never opens files under `raw_root`/`manifests_root` directly (other than the sidecar directory). Stored fetch time is the injected `clock()` value taken when the response was received, `source_version` is the class attribute, `run_id` the run's id.
R12. Archive safety (`archive=True`): after a `FETCHED`/`FETCHED_EMPTY` verdict the body is passed to `safe_unzip`. `safe_unzip` raises `UnsafeArchiveError` (and the key becomes `unreadable`, reason `"unsafe archive: ..."`, nothing stored — neither zip nor members) when: not a valid zip; a member name is absolute, starts with `/` or `\`, contains a drive letter (`C:`), contains a `..` path component (after converting `\` to `/`), contains a NUL byte, or normalises outside the extraction root; a member is a symlink (Unix mode in `external_attr` is `S_IFLNK`); member count > `max_members`; any member exceeds `max_member_bytes`; total uncompressed bytes (counted while reading in chunks, not trusted from headers) exceed `max_total_bytes`; `total_uncompressed / len(data)` exceeds `max_ratio`; duplicate normalised member names; encrypted members. Directory entries are skipped. On success it returns `(posix_name, bytes)` pairs sorted by name. Nothing is ever written to disk by `safe_unzip`.
R13. Sidecar written at the end of every run (including aborted), with the schema above, atomically. Counts: `expected` equals the number of distinct keys (duplicates from `keys()` are collapsed, first occurrence order kept); `sum(outcomes.values()) == expected`.
R14. Sidecar protection: if a sidecar already exists at the target path and its `status == "complete"` and the new run's status is not `complete`, the existing file is left untouched; the new sidecar is written to `<start>_<end>.attempt-<run_id>.json` instead and `RunResult.sidecar_replaced_previous` is `False`. In every other case (no existing file, existing not complete, or both complete) the file at the target path is replaced. An unreadable/corrupt existing sidecar is treated as not complete only if it is not valid JSON; the original is then kept as `<start>_<end>.corrupt-<run_id>.json` (renamed) before writing.
R15. `RunRange` validation: `end < start` raises `ValueError` before any call; `keys()` raising propagates (nothing written, no sidecar).
R16. Logging: each key emits one INFO event named after its outcome (`fetched`, `fetched_empty`, `missing_holiday`, `unreadable`, `skipped`, `incomplete`) with fields `key`, `url`, `size` (when known), `attempts`, `reason`; retries emit WARNING `retry`; run end emits INFO `run_end` with the sidecar counts. Logger task name is `collect.<source>`.

## Edge cases
- `keys()` returns an empty iterable: `expected = 0`, status `incomplete`, no HTTP, sidecar written with `gaps == []`.
- Duplicate keys from `keys()`: fetched once.
- Response body of length zero with status 200: base does nothing special; `check_body` decides (a subclass may say `FETCHED_EMPTY`; an empty body stored as raw file is allowed).
- A 3xx response: passed to `check_body` as-is (no redirect following).
- HTTP 429 with `Retry-After` header: ignored; the fixed backoff schedule applies (conservative, bounded).
- Clock moves backwards: sidecar times are taken as given; no validation.
- `check_body` returns `FETCHED` with `record_count == 0` and `page_limit == 0`: treated as incomplete (equal to limit); subclasses must not set `page_limit` to 0 (`ValueError` at instantiation if `page_limit is not None and page_limit < 1`).
- Archive member name collisions with the zip's own key: members get `"<key>/<name>"` so they cannot equal a top-level key unless the source keys contain `/` (documented, not guarded).
- Interrupted run (exception from transport that is not `TransportError`, e.g. `KeyboardInterrupt`): propagates; no sidecar is written for that run, and previously stored keys remain skippable.
- Per-host rate-limit state lives in the Collector instance (not persisted across processes).
- Subclass sets `source` that fails the manifest regex: `ValueError` at instantiation.

## Acceptance tests (in words)
Fixtures: `FakeTransport` (maps URL -> list of scripted responses/exceptions, counts calls, records headers), `FakeClock`, `FakeSleeper` recording sleep durations, a tiny `DemoCollector` with `keys` = ISO dates in range, `url` = `https://example.test/f/<key>.csv`, `check_body` = `FETCHED` if body starts with `date,` and has more than one line, `FETCHED_EMPTY` if it is only the header line, `MISSING_HOLIDAY` if body is `NO FILE`, else `UNREADABLE`.

A1. (Exit gate 1; R6, R11) Given one key and a valid CSV body, when `run` is called, then outcome `fetched`, one HTTP call, the raw file exists with mode 0444 and the right hash, one manifest line with `logical_key == key`, `source_version`, the run id and the clock's time, and the sidecar has status `complete`, `expected == received == 1`, `outputs == [{key, sha256}]`.
A2. (Exit gate 2; R6, planted) Given a BSE-style response: status 200 with body `<html>Error: data not available</html>`, when run, then outcome `unreadable` (reason mentions check), nothing is in the raw tree, the manifest has no line, and the sidecar lists the key in `gaps` with status `incomplete`.
A3. (R6, planted) Given `check_body` raising `RuntimeError`, or returning `None`, or returning a verdict with outcome `INCOMPLETE`, then outcome `unreadable`, nothing stored, run continues with the next key.
A4. (R6, planted) Given status 404 with a body that `check_body` would accept as `FETCHED`, then the outcome is `unreadable` (reason `non-2xx status 404`) and nothing is stored; given status 404 with body `NO FILE`, `check_body` returns `MISSING_HOLIDAY` and that outcome stands (nothing stored, key counts toward `received`, no gap).
A5. (Exit gate 3; R9) Given the range fetched in A1 plus two more keys all `fetched`, when `run` is called a second time with a fresh `FakeTransport`, then every outcome is `skipped`, the transport reports zero calls, the sleeper recorded zero sleeps, the manifest line count is unchanged, and the new sidecar is `complete` with the same `outputs`.
A6. (R9, planted) Given a range whose first key is `fetched` and second is `missing_holiday`, when re-run, then the first is `skipped` and the second is requested again (one HTTP call), proving non-stored outcomes are not skipped.
A7. (R7) Given a range with one each of fetched, fetched_empty, missing_holiday, unreadable, then `RunResult.outcomes`, the log events and sidecar `outcomes` counts show four distinct values 1/1/1/1, `received == 3`, `gaps` has only the unreadable key; `fetched_empty` body was stored, `missing_holiday` body was not.
A8. (R4, planted) Given a URL scripted to return 503 forever, when run, then exactly 4 HTTP calls were made (not 5), the sleeper recorded `[1, 2, 4]` as backoff sleeps (plus rate-limit waits tracked separately), the outcome is `unreadable` with reason containing `after 4 attempts`, and an ERROR event was logged.
A9. (R4) Given scripted responses [TransportError, 429, 200 valid], then the key is `fetched` after 3 calls and backoff sleeps `[1, 2]`.
A10. (R4, planted) Given a 404 or a 400 response, then no retry occurs (1 call); given 502 then 200, one retry occurs.
A11. (R5, R8, planted) Given the first of three keys returns 403, then it is `unreadable` with reason `blocked http 403`, exactly 1 call was made, the other two keys are `not_attempted` in the sidecar gaps, status `aborted`, and no request ever carried changed headers (all recorded requests have identical header dicts).
A12. (R8, planted) Given the middle of three keys exhausts retries, then the third key is never requested, and its sidecar gap reason is `run aborted after <middle key>`; given the middle key is merely `unreadable` via check_body, the third key IS requested and the run status is `incomplete` (not aborted).
A13. (R2, planted) Given any run, then every recorded request has headers exactly `{"User-Agent": "investos/<version>"}` plus declared extra headers; given a subclass with `extra_headers = {"Referer": "https://x"}` (any case, also `Cookie`, `User-Agent`, `Origin`, `X-Forwarded-For`), then instantiation raises `ValueError`.
A14. (R3) Given a fake monotonic clock that advances only when the sleeper is called and three keys on one host with interval 0.35, then the sleeper is called with 0.35 between consecutive request starts (first request: no wait) and request-start times differ by >= 0.35; with two hosts, the second host's first request does not wait. With config `rate_limits.hosts` overriding to 1.0 for the host, the spacing is 1.0.
A15. (R3, planted) Given a retry sequence on one host, then the interval between retry attempts is at least `max(rate interval, backoff)`; a test with interval 0.35 and backoff 1 shows request starts at least 1.0 apart for the first retry.
A16. (R10, planted) Given `page_limit = 100` and `check_body` returning `FETCHED` with `record_count = 100`, then outcome `incomplete`, nothing stored, key in gaps, sidecar status `incomplete`; with `record_count = 99` outcome `fetched`; with `record_count = 101` outcome `incomplete`; with `record_count = None` outcome `fetched`. Given `page_limit = 0`, instantiation raises `ValueError`.
A17. (R12, planted) Given `archive=True` and a zip containing member `../evil.txt`, then outcome `unreadable` with reason containing `unsafe archive`, and neither the zip nor any member is in the manifest. Same for members `/etc/passwd`, `C:\x.txt`, `a/../../b`, `a\..\..\b`, a symlink member, an encrypted member, duplicate normalised names, and bytes that are not a zip.
A18. (R12, planted) Given a zip bomb (e.g. 50 MB of zeros compressing to a few KB) with default ratio limit, then `UnsafeArchiveError`; given a member whose header claims a small size but expands beyond `max_total_bytes` while streaming (with a small custom limit), then `UnsafeArchiveError`; given more than `max_members` entries (custom limit 3, archive of 4), then `UnsafeArchiveError`.
A19. (R12) Given a valid zip with members `a.csv`, `sub/b.csv`, then `safe_unzip` returns both sorted, and a `run` stores the zip under key `K` plus members under `K/a.csv` and `K/sub/b.csv`; sidecar outputs contain all three; `safe_unzip` creates no files (the tmp dir listing is unchanged).
A20. (R14, planted) Given a first run over range R whose two keys were both fetched (sidecar `complete`), when a second run over the same range R uses a collector whose `keys()` yields one extra key that the fake transport answers with 503 forever, then the status is `aborted`, the original sidecar file bytes are unchanged, a file `<start>_<end>.attempt-<run_id>.json` holds the new sidecar, and `sidecar_replaced_previous` is `False`.
A21. (R14) Given an existing `incomplete` sidecar and a new `complete` run, then the target is replaced and `sidecar_replaced_previous` is `True`. Given existing `incomplete` and new `aborted`, then replaced. Given existing `complete` and new `complete` (the all-skipped re-run), then replaced.
A22. (R14, planted) Given an existing sidecar file with invalid JSON, then it is renamed to `...corrupt-<run_id>.json`, the new sidecar is written at the target, and no exception escapes.
A23. (R13, planted) Given duplicate keys `["2026-11-17","2026-11-17","2026-11-18"]`, then `expected == 2`, the first key is requested once, and `sum(outcomes) == expected`. Given empty `keys()`, then no HTTP, `expected == 0`, status `incomplete`.
A24. (R15, planted) Given `RunRange(end < start)`, then `ValueError`, no sidecar, no HTTP. Given `keys()` raising, then the exception propagates and no sidecar is written.
A25. (R1) Given the source text of `base.py`, then it does not import `csv`, `xml`, `html.parser`, `bs4`, `lxml`, `openpyxl`, `pandas`; and `json` is used only for the sidecar (checked by test over import statements).
A26. (R16) Given a run with one fetched and one unreadable key, then the captured log has an INFO line with `event == "fetched"` and an ERROR/INFO `unreadable` line, each with `task == "collect.<source>"` and a `key` field, plus one `run_end` line.
A27. (R11) Given a `ManifestStore` double that records calls, then the collector calls only its public `get_by_key` and `store` and never opens a path under the raw or manifests roots itself.

## Not in this module
- Parsing or validating source schema beyond the subclass's `check_body`.
- Concrete collectors (BSE, NSE, ...) and canary / repair-trigger logic (later stages).
- Real HTTP client (`urllib`/`httpx` adapter): a production `Transport` is out of Stage 0 scope; only the Protocol and the fake.
- Scheduling, parallel fetching (one request at a time per collector instance).
- Manifest/raw storage internals (S0-T3) and check CLI (S0-T5).

---

# Stage 1 amendment   (from brief S1-T1, task S1-SPEC1, 21 Nov 2026)

Implements decision 0002 and 0002a and audit conditions C2 (A2, A6, A8 are already fixed in S0-FIX; this section covers the rest of C2). Modules: `src/investos/acquire/base.py` (amended), new `src/investos/acquire/access.py` (access modes, state file, limiter, caps), new `src/investos/acquire/transport.py` (`UrllibTransport`, `BrowserTransport` protocol), new `src/investos/core/notify.py` (`Notifier`, `NullNotifier`, `OsascriptNotifier`), `src/investos/core/config.py` (new `[collect]` section), `config/investos.example.toml`, `src/investos/cli.py` (`investos access status|clear`). Stdlib only. Rule numbering continues from R16; acceptance tests from A28. The manifest side (schema v2) is in `specs/core_manifest.md`, "Stage 1 amendment", and must be merged first (S1-T2).

## What this amendment supersedes
| Old text | Status |
|---|---|
| R2 header value `investos/<version>` | superseded by R19, R20 (`InvestOS/<version> (personal research; <contact>)`, plus `Accept-Encoding`) |
| R4 "HTTP 429 is retried" and the edge case "429 with Retry-After: fixed backoff applies" | superseded by R24: 429 is a block, never retried |
| R5 block list `401, 403, 451` | extended by R24 (adds 429 and challenge pages; blocks persist) |
| R3 / edge case "Per-host rate-limit state lives in the Collector instance (not persisted across processes)" | superseded by R27 (cross-process); the in-process monotonic rule of R3 still holds |
| A9 (`[TransportError, 429, 200]` fetched after 3 calls) | superseded by A40; A9's TransportError and 5xx retry parts stay (covered by A10, A8) |
| A13 (headers exactly `investos/<version>`) | superseded by A30, A31 |
| "Not in this module: real HTTP client" | superseded by R21 (`UrllibTransport`) and R23 (`BrowserTransport` interface) |
| `timeout_s: ClassVar[float] = 30.0` | changed to `ClassVar[float | None] = None`; `None` means `cfg.collect.timeout_s` (R21) |
All other rules R1 to R16 and tests A1 to A27 stand. "Source" and "adapter" mean the same thing here: one `Collector` subclass, identified by its `source` class attribute.

## Config addition (extends core_config R2; the schema stays strict)
```toml
[collect]                                   # optional section; a Collector refuses to instantiate without [collect].contact
contact = "name@example.com"                # str, see R20
timeout_s = 30.0                            # number > 0, default 30.0
max_body_bytes = 536870912                  # int >= 1, decoded body cap for UrllibTransport, default 512 MiB

[collect.access.nse_api]                    # one table per adapter that is not plain honest; key = Collector.source (SOURCE_RE)
mode = "real_browser"                       # "honest" (default if table exists) | "real_browser" | "scoped_exception"
decision = "0003"                           # str "NNNN" or "NNNNa"; required for scoped_exception, optional otherwise
daily_cap = 300                             # int >= 1; REQUIRED for real_browser and scoped_exception, optional for honest
challenge_signatures = ["Access Denied"]    # list of non-empty str, each <= 200 chars; optional
```
`Config.collect` is a frozen `CollectConfig(contact: str | None, timeout_s: float, max_body_bytes: int, access: Mapping[str, AccessEntry])` and is always present on `Config` (`contact=None` and an empty `access` when the section is absent). `AccessEntry(mode, decision, daily_cap, challenge_signatures)`. No environment overrides exist for `[collect]` keys; the typo guard of core_config R3 still rejects `INVESTOS_COLLECT__*`. Unknown keys or sections, wrong types, a bool where a number is required, a mode outside the three values, a table name that fails SOURCE_RE, a missing `decision` on `scoped_exception`, a missing `daily_cap` on a non-honest mode: `ConfigError` naming the dotted key. `rate_limits.hosts` in `config/investos.example.toml` gains `"nsearchives.nseindia.com" = 1.0`, `"www.nseindia.com" = 2.5`, `"www.nseindia.in" = 2.5`, `"api.bseindia.com" = 2.5`; the example `[collect]` block uses the placeholder `contact = "CHANGE-ME@example.invalid"` and no `access` tables. The example file must still load.

## Inputs (new schema)
```python
class AccessMode(str, Enum):
    HONEST = "honest"; REAL_BROWSER = "real_browser"; SCOPED_EXCEPTION = "scoped_exception"

class Transport(Protocol):                    # unchanged signature
    accept_encoding: str                      # NEW optional attribute: encodings it can decode, e.g. "identity" or "gzip, deflate"; absent means "identity"
    def get(self, url: str, *, headers: Mapping[str, str], timeout: float) -> Response: ...

@dataclass(frozen=True)
class BrowserResponse:
    status: int
    headers: Mapping[str, str]                # response headers, lowercased names
    body: bytes                               # bytes as received over the wire after the browser's own content decoding
    final_url: str                            # URL after any redirects, fragment included if the browser reports it
    request_headers: Mapping[str, str] | None # headers the browser reports it sent; None if unavailable

class BrowserTransport(Protocol):
    def fetch(self, url: str) -> BrowserResponse: ...      # raises TransportError on timeout / page crash / navigation failure

class Collector(ABC):
    access_mode: ClassVar[AccessMode] = AccessMode.HONEST
    def __init__(self, *, transport: Transport | None = None, browser: BrowserTransport | None = None,
                 manifest, cfg, run_id=None, sidecar_dir=None, clock=None, monotonic=None, sleeper=None,
                 wall_clock: Callable[[], float] | None = None,   # wall_clock = epoch seconds, default time.time
                 notifier: Notifier | None = None,               # R33; default NullNotifier (the CLI wires OsascriptNotifier on macOS)
                 hostname: Callable[[], str] | None = None)      # default socket.gethostname; recorded as fetch_host (R30)
    def headers_for(self, key: str, url: str) -> Mapping[str, str]  # default: dict(self.extra_headers); scoped_exception adapters may override (Referer/Cookie per request)
```
```python
# acquire/access.py (names are the contract; tests import them)
class AccessStateError(Exception): ...        # unreadable or invalid access state file (fail closed)
@dataclass(frozen=True)
class AccessEvent: ts: str; date: str; event: str; source: str; host: str | None; detail: str; run_id: str; decision: str | None; schema: int = 1
# investos.core.notify (new module, built in S1-T1, shared with the roots routine)
class Notifier(Protocol):                      # R33
    def notify(self, title: str, message: str) -> None: ...
class NullNotifier: ...                       # does nothing
class OsascriptNotifier: ...                  # macOS only; R33

class AccessState:
    def __init__(self, cfg: Config, source: str, *, clock)
    def status(self, host: str) -> "AccessStatus"   # (blocked_today: bool for that HOST, halted: bool for this SOURCE, today: str, reason: str)
    def record_block(self, *, host: str, detail: str, run_id: str) -> AccessEvent   # may also append a `halted` event (R26)
    def events(self) -> list[AccessEvent]
def clear_halt(cfg: Config, source: str, *, decision: str, clock, run_id: str) -> AccessEvent
class HostLimiter:                            # R27
    def __init__(self, state_dir: Path, *, wall_clock, monotonic, sleeper)
    def acquire(self, host: str, interval: float) -> None
class DailyCounter:                           # R29
    def __init__(self, state_dir: Path, source: str, *, clock)
    def reserve(self, cap: int | None) -> bool  # True and counted, or False (cap reached); atomic across processes
```
State directories: day-block events live in per-host files `<cfg.zones["ledger"]>/access/hosts/<host>.jsonl`, halt events in per-source files `<cfg.zones["ledger"]>/access/<source>.jsonl` (locks `<file>.lock`); limiter and counter files under `<cfg.zones["ledger"]>/.state/` (ignored by git, see R25).

## Rules (numbered, each gets at least one test and one planted failure)

R17. Access mode per adapter. `Collector.access_mode` is a class attribute (default `honest`). At instantiation the base compares it with `cfg.collect.access[source].mode` (absent table = `honest`). They must be equal; any difference in either direction raises `ValueError` naming the source and both values. So a class cannot become non-honest by config alone, and a config entry cannot upgrade an honest class. A non-honest class must have a config table with `daily_cap` (the config validator already enforces it; the base re-checks). The mode value outside the three raises `ValueError`.

R18. Decision row for `scoped_exception`. A `scoped_exception` adapter instantiates only if its config entry has `decision = "<id>"` and exactly one file `docs/decisions/<id>-*.md` exists under `cfg.repo_root` that contains the exact lines `Adapter: <source>` and `Access-mode: scoped_exception` (case-sensitive, whole line, either order, anywhere in the file). No such file, more than one file, or a file lacking either line raises `ValueError`. The check is repeated at every instantiation (no cache). `real_browser` adapters need only the config table (decision 0002a authorises the mode generally); if their entry has a `decision`, the same file test applies with `Access-mode: real_browser`. In this repo "decision row" means a numbered file in `docs/decisions/`; the `decisions` ledger (`decisions.jsonl`) holds trade decisions and cannot carry access approvals.

R19. Headers by mode (supersedes R2 for the value and the exact set; the validation rules of S0-FIX stay: names are RFC 7230 tokens, `_` normalised to `-`, values have no CR/LF/NUL, a case-insensitive deny-list). Requests built by the base:
- `honest`: exactly `{"User-Agent": <ua>, "Accept-Encoding": <enc>, **headers_for(...)}`. `extra_headers` may not contain `user-agent`, `referer`, `origin`, `cookie`, `accept-encoding` or any other denied name.
- `scoped_exception`: the same, except `headers_for`/`extra_headers` may additionally contain `User-Agent` (then it replaces the honest UA), `Referer` and `Cookie`. Nothing else is unlocked.
- `real_browser`: the base sends no headers at all (there is no header argument in `BrowserTransport.fetch`); the browser's own headers are only recorded (R23, R30).
- In every mode the deny-list still refuses `origin`, `authorization`, `proxy-authorization`, `host`, `forwarded`, `via`, `x-forwarded-for`, `x-forwarded-host`, `x-real-ip`, `x-client-ip`, `true-client-ip`, `cookie2` and `accept-encoding` (the base owns it). `headers_for` is validated on every call; a violation raises `ValueError` before any request. There is still no code path that changes identity or headers after a block (R2 stands).
- `<enc>` is `transport.accept_encoding` if the attribute exists, else `identity`.

R20. Honest identity. `<ua>` = `InvestOS/<investos.__version__> (personal research; <contact>)`, `contact = cfg.collect.contact`. A `None` or empty-string contact raises `ValueError("collect.contact is not configured")` at instantiation (every mode, because the manifest records the headers); so a production run, which always builds its collectors through the CLI factory from `[collect] contact`, refuses to start while the address is empty. Tests satisfy the rule by putting a fake contact in their config. Contact must be 3 to 100 printable ASCII characters, contain no `;`, `(`, `)`, CR, LF or NUL, and no leading/trailing space; otherwise `ValueError`. A contact ending in `.invalid` is refused when the transport is a `UrllibTransport` (placeholder not replaced); fakes may use it.

R21. Production HTTP transport. `acquire/transport.py` provides `UrllibTransport(*, max_body_bytes: int = 536870912, allowed_schemes: tuple[str, ...] = ("https",), brotli_module=None)` implementing `Transport`, built on `urllib.request`:
(a) it sends exactly the headers it is given, no more: an opener with `addheaders = []`, so no default `User-Agent`, `Accept` or `Accept-Encoding`; only the protocol-mandatory `Host` and `Connection: close` added by the library are allowed beyond the given set, and they are not part of the recorded headers;
(b) it never follows redirects: a 3xx is returned as a `Response` with its `location` header and original body; an `HTTPError` (4xx/5xx) is converted to a `Response`, not raised;
(c) environment proxy settings are ignored (empty `ProxyHandler`); schemes outside `allowed_schemes` raise `ValueError`;
(d) `Content-Encoding` gzip and deflate (zlib-wrapped or raw) are decoded; `br` only if `brotli` imports (or `brotli_module` is given), otherwise it is never advertised. `accept_encoding` is `"gzip, deflate"`, plus `, br` when brotli is available. After decoding, the `content-encoding` and `content-length` headers are dropped. Decoding is streamed and the decoded size is capped at `max_body_bytes`; beyond it, `TransportError("body too large")`. An encoding it cannot decode leaves the body and the `content-encoding` header untouched (R22);
(e) timeout: the per-call `timeout` argument bounds every socket operation, and the whole call (connect, headers, body) is bounded by `4 * timeout` wall seconds; a timeout, DNS failure, connection reset or incomplete read raises `TransportError`. The Collector passes `timeout = cls.timeout_s if not None else cfg.collect.timeout_s`;
(f) response header names are lowercased; repeated headers are joined with `", "`; `Response.url` is the requested URL.

R22. Undecoded bodies. After any successful response from either transport kind, if a `content-encoding` header remains whose lowercased value is neither empty nor `identity`, the key becomes `unreadable` (reason `"undecoded content-encoding <value>"`), `check_body` is not called and nothing is stored.

R23. Real-browser interface. `real_browser` adapters use `browser=` and never an HTTP `Transport`. Construction rules: a `real_browser` class with `transport=` set, or any other mode with `browser=` set, or both or neither given, raises `ValueError`. Per key the base calls `browser.fetch(url)` once per attempt and builds the same `check_body` input as for HTTP: `Response(status, headers, body, url=requested url)`. Additional rules: (a) if `final_url` differs from the requested URL (ignoring a fragment) the key is `unreadable` (reason `"redirected to <final_url>"`, nothing stored), the same meaning as "a redirect is an outcome" for HTTP; (b) R4 retries, R5/R24 blocks, R6 body checks, R10 page limits, R12 archives, R22, R27 to R29 apply unchanged; (c) `TransportError` from `fetch` is retried like any transport error; (d) the browser implementation itself (Playwright or the Claude desktop browser) is not in this task: it is built later in the backfill runner task, must not hide `navigator.webdriver`, use stealth plugins or synthetic input, and only has to satisfy this Protocol; (e) "Sagar's Mac only" cannot be enforced in code and is not checked.

R24. Block policy (supersedes R4's retry of 429 and R5's list; R5's abort and "never worked around" stand). A response is a block if (a) its status is 401, 403, 429 or 451, or (b) its body, any status, contains one of the requesting adapter's configured `challenge_signatures` (none ship by default; real ones are added from captured pages) (UTF-8 bytes, case-sensitive substring, first 256 KiB of the body only). The status check happens before `check_body`; the signature check happens before `check_body` and before the R22 check. On a block the base: marks the key `unreadable` with reason `"blocked http <status>"` or `"challenge page: <signature>"`; does not retry (429 included); appends a `blocked` event for the request's host to the host's access state (R25) before anything else; aborts the run per R8. Block scope is the host (decided 21 Nov: NSE blocks by IP, not by adapter): from then until the end of the current IST day (R25) every adapter whose request URL has that host (lowercased `urlparse(url).hostname`) is blocked, whatever its source or mode: every later run of any of them, in this or any other process, makes zero HTTP calls to that host and every key that is not `skipped` becomes `not_attempted` with reason `"host <host> blocked for IST day <YYYY-MM-DD>"` and status `aborted`. Adapters on other hosts are unaffected. A process that is mid-run when another process records a block sees it before its next request attempt (the state is re-read before every attempt, including retries). Block state is never cleared by a successful request, a retry, or a mode/identity change.

R25. Access state files. Append-only JSONL, two kinds, both tracked in git: `<ledger zone>/access/hosts/<host>.jsonl` (events `blocked`; one file per host, `<host>` matches `^[a-z0-9][a-z0-9.-]{0,252}$`) and `<ledger zone>/access/<source>.jsonl` (events `halted` and `cleared`; one file per source). A `blocked` line is in a host file with a non-null `host`; `halted`/`cleared` lines are in a source file with `host` null; any other combination is `AccessStateError`. All tracked (small, an audit trail, and it travels with the daily routine of `specs/ops_roots_routine.md`). The location is chosen over the manifests zone because the manifests check treats every file there as a source manifest (`misplaced_file`/`bad_line`), and over an untracked file because a block history must survive a re-clone. Each line is `canonical_json`-style (`json.dumps(obj, sort_keys=True, separators=(",", ":"), ensure_ascii=False)` plus `"\n"`) of `{"schema":1,"ts":"<UTC, 6 digits, Z>","date":"<IST date of ts>","event":"blocked"|"halted"|"cleared","source":"<source>" (for `blocked`: the adapter that was blocked),"host":<str or null>,"detail":"<text>","run_id":"<id>","decision":<str or null>}`. `decision` is non-null only for `cleared`. Writers take an exclusive `flock` on `<file>.lock`, write the whole line in one `write`, flush and fsync (and fsync the directory after creating the file), and refuse to append to a file whose last byte is not a newline. Readers parse strictly under a shared lock when the tail is unterminated (the S0-FIX A2 pattern): any invalid line, unknown field, unknown event, `schema != 1`, `date` not equal to the IST date of `ts`, a torn tail that persists under the lock, raises `AccessStateError`. The collector fails closed: `AccessStateError` at start or mid-run means zero further requests, status `aborted`, reason `"access state unreadable: <detail>"`, ERROR log; the torn-tail runbook procedure applies to these files too. Before a request the base reads the file of the request's host and the file of its own source. Reads are cached by the stat signature `(inode, size, mtime_ns, ctime_ns)` and re-validated before every request attempt. "IST day" is `clock()` converted with `ZoneInfo("Asia/Kolkata")`. `.gitignore` gains `data/ledger/.state/` (limiter and counter files, R27, R29); `*.lock` is already ignored.

R26. Halt after repeated blocks. After appending a `blocked` event the base counts the distinct IST dates of `blocked` events attributed to that source (`source` field, read from all host files) within the 7 IST dates ending today (today and the 6 days before), counting only events after the latest `cleared` event of that source. The halt is per source even though the day-block is per host: a host block makes other adapters on the host wait a day, but only an adapter's own blocks count towards its halt. If the count is 3 or more and the adapter is not already halted, it appends a `halted` event (`detail = "3 blocked days in 7"`). A halted adapter (latest event among `halted`/`cleared` is `halted`) makes zero HTTP calls on any day: `not_attempted` with reason `"adapter halted: <detail>; needs a decision"`, status `aborted`. When the `halted` event is appended the base also calls `notifier.notify("Invest OS: source halted", "<source> halted: 3 blocked days in 7 (host <host>)")` (R33). Only `clear_halt(cfg, source, decision=<id>, ...)` ends a halt: it requires the adapter to be halted and a decision file `docs/decisions/<id>-*.md` containing the line `Clears-halt: <source>` (exactly one match), and appends `cleared` with that `decision`; otherwise it raises `ValueError` and writes nothing. A `cleared` event resets the 7-day count (earlier blocks no longer count) but does not lift the host's day-block of the current IST day. `investos access status [source]` prints, per adapter, today's block flag, halted flag and the last 5 events; `investos access clear <source> --decision <id>` calls `clear_halt`; exit codes 0 ok, 1 refused, 2 usage.

R27. Cross-process rate limiter. Per host (lowercased `urlparse(url).hostname`), files `<ledger zone>/.state/ratelimit/<host>.lock` and `<host>.last` (decimal integer nanoseconds since the Unix epoch, ASCII, no newline needed). `HostLimiter.acquire(host, interval)` does, in order: take `flock(LOCK_EX)` on the lock file (so concurrent processes queue); read the last-start stamp (missing file: no wait; unreadable or non-numeric content: wait the full `interval`); wait = `interval - (wall_now - last)`, clamped to `[0, interval]` (a stamp in the future, from a clock that moved backwards, never causes a wait longer than `interval`); also wait the in-process `monotonic` rule of R3 (`interval - (mono_now - last_mono_start)`), taking the larger of the two waits; call `sleeper(wait)` while still holding the lock if wait > 0; write `wall_now` (taken after the sleep) to the `.last` file via temp file plus `os.replace` and fsync; release. The first request to a host from a fresh process with no `.last` file does not wait. Hosts are independent. The lock is released on any exception. The Collector calls `acquire` before every request attempt, retries included, instead of its old private `_pace` (R3's observable behaviour with a fake clock is unchanged).

R28. Interval defaults and floors. Interval = `cfg.rate_limits.interval_for(host)` (default 0.35, hosts from config, example config per above). For adapters whose mode is `real_browser` or `scoped_exception` the effective interval is `max(configured, 2.5)` seconds (decision 0002a floor), whatever the config says; `honest` adapters use the configured value unchanged (archives 1.0 s and `/api` hosts 2.5 s in the example config; those are config values, not code constants).

R29. Daily request cap. `cfg.collect.access[source].daily_cap` (required for non-honest modes, optional for honest) bounds the number of HTTP request attempts (retries and browser fetches included, skipped keys excluded) per adapter per IST day, across processes. `DailyCounter.reserve(cap)` takes an exclusive lock on `<ledger zone>/.state/requests/<source>.lock`, reads `<source>.<IST date>.count` (decimal integer; missing = 0), and if `count >= cap` returns `False`, else writes `count + 1` atomically (temp file plus replace) and returns `True`. It is called immediately before each attempt, after the limiter wait. When it returns `False` the key and all later keys are `not_attempted` with reason `"daily cap <cap> reached"`, the run status is `aborted`, no block event is written and the next IST day starts at zero. A count file that is unreadable or not a non-negative integer is treated as "cap reached" (fail closed) with an ERROR log. Counter files older than 14 days are not deleted by the code (human clean-up).

R30. Recording what was sent. Every `ManifestStore.store` call from the base passes `access_mode=<mode value>`, `final_url` (the `Response.url` for HTTP, which is the requested URL; `BrowserResponse.final_url` for the browser), `fetch_host=hostname()` and `request_headers`: for `honest` and `scoped_exception` the exact mapping given to `Transport.get` (including `Accept-Encoding`, not the library-added `Host`/`Connection`); for `real_browser` the `request_headers` of the winning `BrowserResponse` (may be `None`). The same applies to archive members. Cookie redaction is done by the manifest (see core_manifest R17), not by the base. The run sidecar becomes `"schema": 2` and gains `"access_mode"`; R14's reading of an existing sidecar accepts `schema` 1 or 2 and treats any other value like invalid JSON (renamed to `.corrupt-`).

R31. Order of work per key (supersedes the implicit order of R8/R9/R3): (1) `skipped` if the manifest holds the key (zero HTTP, zero sleeps, even when blocked, halted or capped); (2) access state check (R24/R26; `AccessStateError` fails closed); (3) request attempt: limiter (R27), then cap reservation (R29), then the call; (4) response: block check (R24), R22, `check_body` and the rest as before. Instantiation order of failures: contact (R20), mode/config agreement (R17), decision row (R18), transport/browser wiring (R23), header validation (R19), then the existing `source`, `source_version`, `page_limit`, `run_id` checks.

R32. Logging additions on task `collect.<source>`: ERROR `blocked` (fields `key`, `host`, `status` or `signature`, `source`), ERROR `halted` (a halt also writes this line even if the notifier fails), WARNING `cap_reached` (`cap`, `date`), INFO `rate_wait` is not emitted (waits are normal), ERROR `access_state_unreadable`. Header values are never logged; the `Cookie` header is never logged in any form.

R33. Halt alert. The collector reports a halt through an injectable `Notifier` (default `NullNotifier`, so no test or library use ever runs a command). `OsascriptNotifier.notify(title, message)` runs `osascript -e 'display notification "<message>" with title "<title>"'` through `subprocess.run` with an argument list (never `shell=True`), a 5 s timeout, with `"` and `\` in both strings escaped and control characters stripped; it does nothing unless `sys.platform == "darwin"`; any exception, non-zero exit or timeout is caught and logged as WARNING `notify_failed` and never changes the run outcome. The messages contain the source and host names only: no headers, cookie or URL query text. The `investos access` CLI and the future runner pass an `OsascriptNotifier` when on the Mac. Showing the halt on Mission Control (Now panel, sources) is a later task, not this one.

## Edge cases
- A run that crosses IST midnight: the day is evaluated at every request attempt, so a block recorded at 23:59 IST ends at 00:00 IST for later attempts; a block recorded at 00:01 belongs to the new day.
- A challenge signature appearing in a valid data file (for example the text "Access Denied" inside a CSV) blocks the adapter: that is why signatures are per adapter and chosen carefully (open question 3).
- `missing_holiday` responses (404 with `NO FILE`) are not blocks (404 is not in the status set).
- The cap counts attempts, not keys: a key retried three times uses four.
- Two collectors for the same `source` in one process share the state files; their `HostLimiter` in-process monotonic memory is per instance, the file stamp covers the rest.
- Cookie warm-up requests (scoped_exception, held in reserve) would be ordinary adapter keys: counted, limited, recorded.
- A `halted` adapter can still `skip` already stored keys, so a verification re-run over a complete range stays green.

## Acceptance tests (in words)
Fixtures added: `FakeBrowser` (scripted `BrowserResponse`s/`TransportError`s, records URLs), a config builder with `[collect]` and `access` tables and a temp repo with `docs/decisions/`, a `FakeWall` (epoch seconds, advances with the sleeper), a local `http.server` fixture on 127.0.0.1 (tests build `UrllibTransport(allowed_schemes=("http",))`), `run_in_process(fn)` using `multiprocessing` (spawn).

A28. (R17, planted) Given a class `honest` with config mode `real_browser`; a class `real_browser` with no config table; a class `scoped_exception` with config `honest`; and a config mode `"stealth"`, then instantiation (or `load_config` for the last) raises with the source named. Matching pairs instantiate.
A29. (R18, planted) Given a `scoped_exception` adapter: no `decision` key; a decision id with no file; a file lacking `Adapter: <source>`; a file with `Access-mode: honest`; two files with the same id prefix; a file for a different adapter, then `ValueError` each time. A correct file passes. A `real_browser` adapter with no `decision` passes.
A30. (R19, R20) Given an honest run, then every recorded request has exactly the keys `User-Agent` and `Accept-Encoding` (value `identity` for the fake), and `User-Agent` matches `^InvestOS/0\.0\.0 \(personal research; test@example\.invalid\)$`. A fake with `accept_encoding = "gzip, deflate"` sends that value.
A31. (R19, planted) Given an honest class with `extra_headers` containing, in turn, `Referer`, `cookie`, `User_Agent`, `Origin`, `Accept-Encoding`, `Authorization`, then `ValueError`; given a `scoped_exception` class with `Referer`, `Cookie`, `User-Agent` then instantiation passes and requests carry them; the same class with `Origin`, `Authorization`, `Host`, `X-Forwarded-For` still raises; a `headers_for` that returns `Host` on the second call raises `ValueError` before that request.
A32. (R20, planted) Contact `None`, `""`, `"ab"`, `"a;b@x.com"`, `"a(b)@x.com"`, `"é@x.com"`, 101 characters, `" a@x.com"`, a CR/LF value: each raises `ValueError` at instantiation. Contact `x@example.invalid` with a `UrllibTransport` raises; with a fake it passes.
A33. (R21, planted) Given a local server that records the raw request header names it receives, when `UrllibTransport.get` is called with `{"User-Agent": "u", "Accept-Encoding": "identity"}`, then the received names are a subset of `{host, connection, user-agent, accept-encoding}`; with `{}` as headers the server receives no `User-Agent`, `Accept` or `Accept-Encoding`.
A34. (R21, planted) Given the server answers 302 with `Location`, then `get` returns status 302 with the `location` header and the server saw exactly one request. Given status 404 or 503, `get` returns a `Response` and does not raise. With `HTTP_PROXY`/`HTTPS_PROXY` set to a dead address, the call still reaches the local server. A URL with scheme `file` or `http` (default schemes) raises `ValueError`.
A35. (R21) Given bodies served with `Content-Encoding` gzip, deflate (zlib-wrapped) and deflate (raw), then the returned body equals the original and `content-encoding`/`content-length` are absent from the headers. Given a small gzip body that expands past `max_body_bytes` (custom 1 MiB), then `TransportError`. Given `br` and no brotli module, the body and header are returned untouched and `accept_encoding == "gzip, deflate"`; with an injected fake brotli module, `accept_encoding` includes `br` and the body is decoded.
A36. (R22, planted) Given a fake transport returning `content-encoding: br` with a body that `check_body` would accept, then outcome `unreadable` (reason `undecoded content-encoding br`), `check_body` never called, nothing stored. `content-encoding: identity` is accepted.
A37. (R21) Given a server that sleeps longer than the timeout before replying, then `TransportError`; given a server that sends one byte per 0.2 s forever with `timeout=0.3`, then `TransportError` within about `4 * timeout`; given a closed port, `TransportError`.
A38. (R23) Given a `real_browser` class: constructing with `transport=` raises `ValueError`; with neither raises; with both raises; an `honest` class given `browser=` raises. A normal run calls `fake_browser.fetch` once per key, never an HTTP transport, and `fetch` takes only the URL (signature check).
A39. (R23, planted) Given `final_url != url` (host change, path change, added query), then outcome `unreadable` with reason `redirected to <final_url>` and nothing stored; with only a fragment difference the key is fetched. Given a 200 error page, then `check_body` rejects it and nothing is stored. Given `fetch` raising `TransportError` forever, then exactly 4 calls and `unreadable` `after 4 attempts`.
A40. (R24, planted) Given first of three keys returns each of 401, 403, 429, 451, then exactly 1 call, reason `blocked http <status>`, the other two keys `not_attempted`, status `aborted`, a line with event `blocked` in `access/hosts/<host>.jsonl` (host, `source`, run id, today's IST date), and no retry (429 included, superseding A9).
A41. (R24, planted) Given an adapter with `challenge_signatures = ["Pardon Our Interruption"]` and a 200 body containing that text, then `unreadable` (reason `challenge page: Pardon Our Interruption`), a block event, run aborted, `check_body` not called. The same text after byte 262144 does not block. An adapter with no signatures never blocks on a 200. A signature on a 404 response blocks.
A42. (R24, R31, planted) Given a block recorded earlier on the same IST day, when a new collector instance (fresh transport) runs the range, then zero HTTP calls, keys already in the manifest are `skipped`, the rest `not_attempted` with reason `host <host> blocked for IST day <date>`, status `aborted`, sidecar written. With the injected clock at 23:59:59 IST the block still holds; at 00:00:00 IST of the next day requests proceed and a success does not write any event.
A43. (R24, R25, planted) Given two processes sharing one data dir: process 1 receives a 403; process 2 started afterwards makes zero calls. Given process 2 already mid-run on a 5-key range and blocked on an event between keys 2 and 3 until process 1 has written its block, then process 2 never requests key 3.
A44. (R25, planted) Given an access state file (host file or source file) with each of: invalid JSON, missing field, extra field, unknown event, `schema` 2, wrong `date`, a `blocked` line in a source file or a `halted` line in a host file, a `host` that is null in a host file, blank line, torn tail (no final newline), then `AccessState.status()` raises `AccessStateError`, a collector over that state makes zero HTTP calls, status `aborted`, reason starting `access state unreadable`, and an ERROR is logged. Writers refuse to append to a torn tail. A reader that catches a concurrent writer mid-append waits for the lock and does not raise (2-process test).
A45. (R26, planted) Given blocks of one source on 3 distinct IST days within 7 (days 1, 4, 7), then the third block appends `halted` and from day 8 on a run makes zero calls with reason starting `adapter halted`, even after the day-block ended. Days 1, 4, 8 do not halt (day 1 falls outside the window days 2 to 8). Two blocks on one day count once. A block recorded by a different source on the same host does not count towards this source's halt.
A46. (R26, R33, planted) Given a halted adapter (a `FakeNotifier` records calls; the halt call produced exactly one notification whose text names the source and host and contains no header values): `clear_halt` with a missing decision file, a file without `Clears-halt: <source>`, two matching files, or a non-halted adapter raises `ValueError` and leaves the file byte-identical. With a valid file it appends `cleared` (with `decision`), the adapter runs on the next IST day, earlier blocks no longer count (two further blocked days do not re-halt), and the host's day-block recorded today still holds until midnight. `investos access status` shows the state; `investos access clear` returns 0 on success and 1 on refusal.
A47. (R27) Given a `FakeWall`, `.last` stamp at t0, now = t0 + 0.4 and interval 1.0, then the sleeper is called with 0.6; first request with no stamp: no sleep; the stamp afterwards equals the wall time after the sleep.
A48. (R27, planted) Given two processes (real clock, interval 0.3 s) each making 5 requests to the same host through the limiter and logging their request-start wall times, then the merged sorted start times are at least 0.28 s apart. Two different hosts do not delay each other. If the lock holder raises, a later `acquire` is not stuck.
A49. (R27, planted) Given a `.last` stamp 1 hour in the future and interval 2.5, then the wait is at most 2.5; given an empty or non-numeric `.last`, the wait is the full interval; given the wall clock stepping backwards between two requests of one process, request starts are still at least the interval apart by the monotonic rule.
A50. (R28) Given config hosts 0.35 for the host and a `real_browser` or `scoped_exception` adapter, then the effective interval is 2.5; with a configured 5.0 it stays 5.0; an `honest` adapter at 0.35 stays 0.35. The example config loads and gives 1.0 for `nsearchives.nseindia.com` and 2.5 for `www.nseindia.com`, `www.nseindia.in`, `api.bseindia.com`.
A51. (R29, planted) Given `daily_cap = 3` and 5 keys, then 3 attempts are made, the rest `not_attempted` with reason `daily cap 3 reached`, status `aborted`, no block event. A key needing a retry uses 2. A second process on the same IST day makes zero attempts; the next IST day proceeds. Two processes together never exceed the cap. A non-honest adapter without `daily_cap` fails in `load_config`. A corrupt count file stops the adapter and logs ERROR. Skipped keys do not count.
A52. (R30) Given a successful honest fetch, then the manifest entry has `access_mode == "honest"`, `final_url` equal to the requested URL, `fetch_host` equal to the injected hostname, and `request_headers` equal to the headers the fake saw; for an archive, the zip and every member row carry the same values. A `real_browser` fetch records the browser-reported headers (or `None`) and the browser's `final_url`. The sidecar has `schema == 2` and `access_mode`. An existing schema-1 complete sidecar still protects against an incomplete run (R14 unchanged); an existing sidecar with `schema` 3 is renamed `.corrupt-`.
A53. (R31) Given a halted, blocked or capped adapter and a fully fetched range, then every key is `skipped`, zero HTTP, status `complete`. Given the order of instantiation failures (bad contact and bad mode together), then the contact error is raised.
A54. (R32, planted) Given a block, then the captured log has an ERROR `blocked` line with `status`, `host`, `source` and no header values; given a run with a `Cookie` header on a scoped_exception adapter, then no log line contains the cookie value.
A55. (R21, R23, R33) Given the source of `base.py`, `transport.py` and `access.py`, then neither imports `requests`, `httpx`, `aiohttp`, `selenium`, `playwright` or `socks`; `transport.py` imports `urllib` only; and no module under `acquire/` contains the strings `webdriver`, `stealth` or `fake_useragent` (guard against the forbidden techniques of decision 0002 point 5).

A56. (R24, R25, planted) Given adapters `a` and `b` on host `h1` and adapter `c` on host `h2`: a 403 on `a` writes one line to `access/hosts/h1.jsonl`; then `b` (new instance) makes zero calls to `h1` with reason `host h1 blocked for IST day <date>` while `c` runs normally; `b`'s own halt count stays 0 and `a`'s becomes 1. A test subclass whose `url()` varies the host: a run over keys on `h2`, `h1`, `h2` stops at the `h1` key (checked per request host) once `h1` is blocked and the run aborts per R8. The block ends at 00:00 IST for all three.
A57. (R33, planted) Given `OsascriptNotifier` with `subprocess.run` replaced by a recorder: on `darwin` it issues one call with an argument list (not a string, no `shell=True`), a 5 s timeout, and titles/messages containing `"`, `\` or newlines escaped or stripped; on `linux` it issues no call; a recorder raising `OSError`, returning exit 1 or raising `TimeoutExpired` leaves the run outcome unchanged and logs WARNING `notify_failed`. No test in the suite imports `subprocess` for osascript unreplaced (the default collector notifier is `NullNotifier`, asserted).
A58. (R20, planted) Given `cfg.collect.contact` of `None` or `""`, then instantiation raises `ValueError("collect.contact is not configured")`; given the CLI factory (`investos access` and the later runner share `build_collector(cfg, ...)`), then with an empty contact it exits 1 with that message before any file under `data/` is touched and before any transport is built.

## Not in this module (Stage 1 additions)
- The real-browser implementation (Playwright or the desktop browser) and the backfill runner: later task; only the `BrowserTransport` Protocol and the rules above are in scope.
- Concrete NSE/BSE adapters, the RSS poller and its two-step item design (audit section 8, item 7).
- Parsing a challenge page or solving it; any retry of a block; any identity change after a block.
- A `check` for access state files (the torn-tail runbook covers manual repair).
- Cleaning old counter and limiter files.

## Open questions for Sagar (as raised by the Spec writer; see Resolved below)
1. Contact string for the User-Agent: which email or URL goes into `InvestOS/<version> (personal research; <contact>)`. It is sent to NSE/BSE and recorded in every pushed manifest row.
2. Daily request caps per adapter (the config requires one for every `real_browser` and `scoped_exception` adapter; no default is built in). Suggested starting points for you to confirm or change: 300 per adapter for NSE history, 200 for gap-fill.
3. Challenge-page signatures for NSE and BSE: need real captured blocked pages to choose strings; the code ships with none.
4. Block scope: the spec stops one adapter. Should a block on a host (for example `www.nseindia.com`) stop every adapter on that host?
5. HTTP 451 is kept in the block set (it was final in R5); say if it should not persist.
6. "Decision row" means a numbered file in `docs/decisions/` with the lines `Adapter:`, `Access-mode:` and `Clears-halt:` (R18, R26). Say if you want these as rows in a ledger instead (that needs a new ledger schema).
7. Environment proxies are ignored by `UrllibTransport` (R21c). Say if your network needs a proxy.
8. Whether a halted source should also raise a visible alert (for example a push notification) beyond the ERROR log and `investos access status`.

Folded into the rules above: 1 -> R20, A58; 4 -> R24, R25, R26, A56; 8 -> R26, R33, A46, A57; 2, 3, 5, 6, 7 unchanged or already as specced (R28 floors, R29 caps are config values: 300 NSE history, 200 gap-fill; no signatures ship; 451 stays; proxies ignored).

## Resolved (21 Nov 2026; S = Sagar, O = Orchestrator default, change in config or a later decision)
1. (S) A dedicated email address created for this, read from config `[collect] contact`; the collector refuses to start a non-test run while it is empty. Sagar will supply the address.
2. (O) Daily caps start at 300 per adapter for NSE history and 200 for gap-fill, set in config; raising them is a config change.
3. (O) No challenge signatures ship. The stage-1 Mac probes capture real blocked pages, and the signatures are added from them. Until then every 200 response still goes through the body checks.
4. (O) Block scope is per host: a block on a host stops every adapter on that host for the rest of the IST day, because NSE blocks by IP, not by our adapter. The halt (3 blocked days in 7) stays per source.
5. (O) 451 stays in the block set.
6. (O) A decision row is a numbered file in docs/decisions/, as specced.
7. (O) Environment proxies are ignored. Sagar's network needs none.
8. (O) Yes, raise an alert. A halt writes an ERROR log line, shows on Mission Control (the Now panel and sources), and sends a macOS notification via osascript when the run is on the Mac.

---

# Stage 1 amendment 2: in-file date check   (from brief S1-T5, task S1-SPEC2, 22 Nov 2026)

Rule: HTTP 200 is never proof of a trading day. Archive servers answer a date URL with a valid, well-formed file even when the date is a weekend or holiday (for example a Sunday URL returning Friday's file byte for byte). A date-keyed archive file is accepted only if the trade date written inside the file equals the requested key. Module: `src/investos/acquire/base.py` (amended). Rule numbering continues from R33, acceptance tests from A58 (next is A59). Stdlib only. R1 stands: the base parses nothing; the date is read by the adapter (like `check_body`), the base only compares dates.

## What this amendment changes
| Old text | Status |
|---|---|
| `Outcome` has six values; "Four outcomes kept separate" (R7) | extended by R34: a seventh outcome `rejected`; the four of R7 stay distinct from it |
| Sidecar `outcomes` object has the seven count keys of the S0 schema | gains the key `rejected` (R40); `schema` stays 1 (additive, open question 1) |
| Sidecar `received` = fetched, fetched_empty, skipped, missing_holiday | unchanged: `rejected` is not received (R40) |
| Order of work per key, R31 step (4) "block check, R22, `check_body` and the rest as before" | the date check is inserted after the page-limit and archive steps, before any store (R38) |
All other rules R1 to R33 and tests A1 to A58 stand.

## Inputs (new schema)
```python
class Outcome(str, Enum):
    ...                                   # six existing values
    REJECTED = "rejected"                 # NEW: a well-formed body that is not what was asked for; nothing stored; re-attempted next run

@dataclass(frozen=True)
class DateEvidence:
    dates: frozenset[date]                # the DISTINCT trade dates found in the file (see R36); empty = none found
    field: str                            # name of the field read, e.g. "TradDt", "DATE1", "TIMESTAMP" (logged, never parsed by the base)
    rows_examined: int                    # >= 0, number of data rows or records the adapter looked at

class Collector(ABC):
    date_keyed: ClassVar[bool] = False                    # True: keys are ISO trade dates and R35 to R41 apply
    date_check_waiver: ClassVar[str | None] = None        # a reason (R35); only with date_keyed = True
    def logical_date(self, key: str) -> date              # default date.fromisoformat(key); an adapter may override for other key spellings
    def internal_dates(self, key: str, member: str | None, body: bytes) -> DateEvidence | None
        # REQUIRED override when date_keyed and no waiver. Called with the body of a plain file (member None) or of one archive member.
```

## Rules (numbered, each gets at least one test and one planted failure)

R34. The `rejected` outcome. `Outcome.REJECTED` means: the transfer worked and the body passed `check_body`, but the content is not the thing the key names. It stores nothing (no raw file, no manifest line, no archive members), is a gap (`status` can never be `complete`), does not abort the run (unlike a block, R8), does not count as received, and the key is requested again on the next run (no manifest entry exists, R9). The reason is mandatory and starts with a machine token: `date_mismatch` or `date_missing`, then `": "` and a human detail. `BodyVerdict` still may not carry `REJECTED` (subclass verdicts stay limited to the four of the base spec; a verdict with outcome `REJECTED` is an invalid verdict, R6). Only the base creates it, through R37.

R35. Declaration. A subclass that sets `date_keyed = True` declares how its date is read in exactly one of two ways: (a) overriding `internal_dates` (mode `enforced`), or (b) setting `date_check_waiver` to a non-empty reason of at least 20 characters (mode `waived`) for a source whose files carry no date at all, naming why. Neither, both, a waiver on a class with `date_keyed = False`, or a waiver shorter than 20 characters (after strip) raises `ValueError` at instantiation naming the source. A class with `date_keyed = False` that overrides `internal_dates` raises `ValueError` too (dead code that looks like a check). The date check mode is `enforced`, `waived` or `not_date_keyed` and is written to the sidecar (R40). A waived run logs WARNING `date_check_waived` once at run start with the reason, and `investos check` lists every waived adapter as a WARNING. There is no config switch that disables the check; waiving is a code change that is reviewed.

R36. What the adapter reports. `internal_dates` reads the trade-date field of the file's own records (for example the columns named in the per-format table below), nothing else: not the URL, the key, the file name, the HTTP headers (`Last-Modified`, `Date`), nor the fetch time. `dates` holds every distinct trade date the records state, as `datetime.date` with no time zone (the date is the exchange's IST trade date as printed). Columns that hold other dates (expiry date, listing date, settlement date) are not trade dates and are not reported. It examines every record, not a sample (a file may contain a second day appended). `None` means "this member carries no date" and is legal only for archive members (R39); for a plain file `None` is `date_missing`. A cell that cannot be parsed as a date makes the adapter raise (R37 error path); it is never skipped silently. The adapter owns the parser, the base never sees the format.
   Per-format examples, illustrative; the exact column names and date spellings are fixed by the stage-1 probes and the adapters' own tests, not by this spec: BSE equity bhavcopy CSV, a trade-date column such as `TradDt` (ISO or `dd-mmm-yyyy`); NSE cash bhavcopy zip (new UDiFF layout), `TradDt`; NSE legacy cash and F&O bhavcopy zips, column `TIMESTAMP` (`dd-MMM-yyyy`), expiry in `EXPIRY_DT` which is NOT reported; NSE security-wise delivery file `sec_bhavdata_full`, column `DATE1`; index closing file, a column such as `Index Date`; files with the date only in a header line (for example the MTO delivery file) read it there.

R37. The comparison. After the earlier steps of R38 the base calls `internal_dates(key, member, body)` (wrapped like `check_body`) and compares with `logical_date(key)`:
   - the call raises, or returns anything that is neither `DateEvidence` nor (for archive members) `None`, or `rows_examined < 0`, or `dates` contains a `datetime` instead of a `date`: the key is `unreadable`, reason `"date check error: <detail>"` (an adapter bug or an unparseable file; not rejected, because nothing was learned about the date);
   - `dates` empty or `None` for a plain file: `rejected`, reason `date_missing: no trade date found in <field> (<rows_examined> rows)`;
   - `dates == {logical_date}` exactly: pass;
   - any other non-empty set (a different single date, or several dates, even when one of them equals the key): `rejected`, reason `date_mismatch: key <key> but file has <sorted ISO dates, comma-separated> in <field>`.
   There is no tolerance (no adjacent day), no weekday logic and no knowledge of holidays in the base: the file says its date, the key says its date, they must be equal. A genuine holiday that the server answers with 404 or `NO FILE` stays `missing_holiday` (A4, R6); the date check applies only to bodies that `check_body` returned as `FETCHED` or `FETCHED_EMPTY`.
   A `FETCHED_EMPTY` body (a header-only file) is also checked: if it carries no trade date the key is `rejected` as `date_missing`. An adapter whose genuine empty files carry no date must not return `FETCHED_EMPTY`; it returns `MISSING_HOLIDAY` or `UNREADABLE` (open question 2).

R38. Order of work for a date-keyed key (extends R31 step 4): block check (R24), R22 undecoded encoding, non-2xx downgrade (R6), `check_body`, page limit (R10), for archives `safe_unzip` (R12), then the date check (R37, R39), then store. `internal_dates` is never called for a response that was `unreadable`, `missing_holiday`, blocked or `incomplete`, nor for a `skipped` key (a manifest hit makes zero HTTP calls and zero date checks). So an HTML error page served with status 200 is still `unreadable` by `check_body`, not `rejected`.

R39. Archives. For `archive = True` and `date_keyed = True` the date check runs on the extracted members after `safe_unzip` succeeds and before anything is stored. The base calls `internal_dates(key, member_name, member_bytes)` for each member, in name order. Members returning `None` are ignored (readme, manifest or index files). At least one member must return evidence; if none does, the key is `rejected` (`date_missing`). Every returned `DateEvidence` must pass R37 on its own; the first failing member decides the reason (member name included in the detail). On any failure, neither the zip nor any member is stored.

R40. Sidecar and logs. The sidecar `outcomes` object gains `rejected` (count), the always-present key set becomes `fetched, fetched_empty, missing_holiday, unreadable, skipped, incomplete, rejected, not_attempted`; `sum(outcomes.values()) == expected` still holds. `gaps` includes `rejected` keys with their reason. A top-level string `"date_check"` with value `enforced`, `waived` or `not_date_keyed` is added. `received` is unchanged (rejected is not received), so a run with a rejected key has status `incomplete`. A rejected key emits WARNING `rejected` on task `collect.<source>` with fields `key`, `url`, `reason`, `file_dates` (sorted ISO strings, may be `[]`), `field`, `rows_examined`; a key passing the check includes `trade_date` (the verified ISO date) in its INFO `fetched`/`fetched_empty` line. The run-end counts include `rejected`.

R41. Keys. When `date_keyed` is true every key from `keys()` must satisfy `logical_date(key)` without error and, for the default `logical_date`, be exactly the ISO form `YYYY-MM-DD`; a bad key makes the run fail with `ValueError` naming it before any HTTP call and without writing a sidecar (as R15 for `keys()` errors). `logical_date` may be overridden for adapters whose logical key is not ISO (for example `20261117`), but the manifest `logical_key` stays the key string given. Duplicate keys that map to the same date but differ as strings are rejected as `ValueError` too (two keys naming one date would double-store it).

## Edge cases
- The same file bytes returned for two different date URLs: the date check, not a hash comparison, decides. The first date's key is fetched, the second is rejected.
- A file containing two trade dates (a Friday file with a stray Thursday row): rejected, even though the key date is present. Adapters whose source legitimately mixes dates in one file are not `date_keyed` (key by something else, for example a month) and are out of this rule.
- A Saturday special session whose file carries Saturday's date passes; the base has no weekday knowledge (the calendar of `specs/trading_calendar.md` is not consulted here).
- Already-stored files from before this amendment are not rechecked (open question 3); `skipped` stays authoritative.
- Time zones: dates are compared as printed (IST trade date); `datetime.now()` or any clock is never involved in the comparison.
- A waived adapter still stores whatever `check_body` accepts, as before.

## Acceptance tests (in words)
Fixtures added: `DateDemoCollector(DemoCollector)` with `date_keyed = True` and an `internal_dates` that reads a `DATE1` column from the demo CSV (the demo body becomes `DATE1,SYMBOL,CLOSE` plus rows with `dd-mmm-yyyy` dates); a body builder that sets the date; an archive builder; a `FakeTransport` that can return identical bytes for several URLs.

A59. (R35, planted) Given `date_keyed = True` with neither an `internal_dates` override nor a waiver, then instantiation raises `ValueError` naming the source; with both, `ValueError`; a waiver of 10 characters, `ValueError`; `date_keyed = False` with a waiver, `ValueError`; `date_keyed = False` with an `internal_dates` override, `ValueError`; `date_keyed = True` with an override passes and `date_keyed = True` with a 20-character waiver passes.
A60. (R37) Given key `2026-11-20` and a body whose `DATE1` column is `20-Nov-2026` on every row, then outcome `fetched`, the file stored, the INFO line carries `trade_date == "2026-11-20"`, sidecar `date_check == "enforced"`.
A61. (R37, planted, the Sunday case) Given keys `2026-11-20` (a Friday) and `2026-11-22` (a Sunday) on one range, and a transport that answers both URLs with status 200 and the same bytes, whose `DATE1` is `20-Nov-2026`, then the Friday key is `fetched`, the Sunday key is `rejected` with reason beginning `date_mismatch` and naming both dates, nothing is stored for Sunday (manifest has one line, one raw file), the run continues, the sidecar `gaps` lists Sunday with outcome `rejected`, status `incomplete`, `outcomes.rejected == 1`. A re-run makes zero calls for Friday (`skipped`) and one call for Sunday (rejected again).
A62. (R37, planted) Given a valid body with the header only and no rows when `check_body` says `FETCHED_EMPTY`, then `rejected` with `date_missing`; given rows whose date column is empty on every row (the adapter returns an empty set), `date_missing`; given a body for which the adapter returns `dates` with two values (the key date and the day before), `date_mismatch`, nothing stored; given `dates` of the day after the key, `date_mismatch`.
A63. (R37, planted) Given `internal_dates` raising `ValueError("bad cell")`, returning `None` for a plain file, returning a `datetime` inside `dates`, or returning `"2026-11-20"` (a str), then: the first and third and fourth give `unreadable` with reason beginning `date check error`; the `None` for a plain file gives `rejected` `date_missing`. No run abort and nothing stored in any case; the next key is requested.
A64. (R38, planted) Given a status-200 `<html>Error: data not available</html>` page, then `check_body` marks it `unreadable` and `internal_dates` is never called (the fixture counts calls); given a 404 with `NO FILE`, `missing_holiday` and zero calls to `internal_dates`; given a `skipped` key, zero calls to `internal_dates`; given a page-limit `incomplete`, zero calls.
A65. (R39, planted) Given `archive = True` and a zip with members `a.csv` (dated `2026-11-20`, key `2026-11-20`) and `README.txt` (the adapter returns `None`), then `fetched`, the zip and `a.csv` stored. Given members `a.csv` dated right and `b.csv` dated `2026-11-19`, then `rejected` (`date_mismatch`, detail names `b.csv`), and neither the zip nor either member is stored. Given a zip whose members all return `None`, `rejected` `date_missing`. Given a zip with a `../evil` member, `unreadable` (`unsafe archive`) as before and `internal_dates` is not called.
A66. (R40) Given a run with one fetched, one fetched_empty, one missing_holiday, one unreadable and one rejected key, then sidecar `outcomes` shows 1/1/1/1/1 for the five, `received == 3`, `gaps` has the unreadable and the rejected key sorted by key with reasons, `sum(outcomes.values()) == expected`, status `incomplete`; the captured log has a WARNING `rejected` with `key`, `reason`, `file_dates`, `field`, `rows_examined` and a `run_end` line whose counts include `rejected`.
A67. (R35, R40) Given a waived adapter, then a run stores whatever `check_body` accepts, the sidecar says `date_check == "waived"`, a WARNING `date_check_waived` is logged once at run start with the reason, and `internal_dates` is never called. Given a non-date-keyed collector, the sidecar says `not_date_keyed`.
A68. (R41, planted) Given a date-keyed collector whose `keys()` yields `"2026-11-2"`, `"20261120"`, or `"2026-11-20T00:00"` with the default `logical_date`, then `ValueError` naming the key, before any HTTP call, and no sidecar. Given an override `logical_date` for `20261120`-style keys, the run works, and the manifest `logical_key` is `"20261120"`. Given two keys `"20261120"` and `"2026-11-20"` under an override that maps both to one date, `ValueError`.
A69. (R34, planted) Given a subclass `check_body` returning a verdict with outcome `REJECTED`, then the key is `unreadable` (`invalid verdict`). A rejected key leaves the manifest untouched (line count and raw tree unchanged), and a `date_mismatch` never aborts: a following key is fetched in the same run.
A70. (R1) The A25 import test is re-run over the amended `base.py` and still passes: no parser module is imported; the date comparison uses only `datetime.date`.
A71. (R37) Given an adapter that reports `{2026-11-20}` for key `2026-11-20` and the system clock set to another day or a different time zone in the fake clock, then the outcome is unchanged (`fetched`): no clock or zone is consulted.

## Not in this module
- The concrete per-source `internal_dates` implementations and the real column names (the stage-1 adapters and probes).
- Deciding whether a date is a trading day (`specs/trading_calendar.md`); the calendar may later take `bhavcopy` evidence from stored files whose adapter has `date_check == "enforced"`.
- Re-checking files stored before this amendment (a later audit task, open question 3).
- Content checks other than the date (row counts, price sanity): later stages.

## Open questions for Sagar (S1-SPEC2)
1. `rejected` is a new outcome and the sidecar `outcomes` object gains a key; I kept `schema: 1` because consumers only read known keys. If any tool treats the sidecar strictly, bump `schema` to 2 in the same task.
2. A source whose genuinely empty file has no date at all cannot use `FETCHED_EMPTY` under R37; it must return `MISSING_HOLIDAY` (if the server says so) or `UNREADABLE`. Say if you would rather allow a `date_check_waiver`-style exception for empty files.
3. Files stored before this rule (Stage 0 and early Stage 1 fetches) were never date-checked. Do you want a one-off audit task that runs each adapter's `internal_dates` over the stored raw files and reports mismatches?
4. R35 lets an adapter waive the check with a written reason (needed for sources with no date in the file). If you prefer no waivers at all, the rule becomes "every date-keyed source must carry an in-file date or it is not date-keyed".
