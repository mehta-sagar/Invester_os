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
