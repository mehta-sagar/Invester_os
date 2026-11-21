# Spec core_manifest   (from brief S0-T3)

Module: `src/investos/core/manifest.py`. Python 3.11+, stdlib only (`hashlib`, `os`, `fcntl`, `json`, `stat`, `uuid`). POSIX only (uses `fcntl.flock`).

## Inputs (schema)

```python
class ManifestError(Exception): ...
class ManifestCorruptError(ManifestError): ...   # unreadable/truncated/invalid manifest line
class RawIntegrityError(ManifestError): ...      # existing raw file does not match its name

class ManifestStore:
    def __init__(self, raw_root: Path, manifests_root: Path, *, clock: Callable[[], datetime] | None = None)
    @classmethod
    def from_config(cls, cfg: Config) -> "ManifestStore"   # cfg.zones["raw"], cfg.zones["manifests"]

    def store(self, source: str, data: bytes, *, url: str, source_version: str,
              logical_key: str, run_id: str, fetched_at: datetime) -> ManifestEntry
    def get_by_key(self, source: str, logical_key: str) -> ManifestEntry | None   # latest line wins
    def get_all_by_key(self, source: str, logical_key: str) -> list[ManifestEntry] # file order
    def get_by_sha(self, source: str, sha256: str) -> list[ManifestEntry]          # file order
    def entries(self, source: str) -> list[ManifestEntry]                          # all lines, file order
    def raw_path(self, source: str, sha256: str) -> Path
    def read_bytes(self, source: str, sha256: str) -> bytes                        # re-verifies hash
    def verify(self, source: str | None = None) -> list[Problem]                   # None = every source
```
`clock` is unused by `store` (the caller supplies `fetched_at`); it exists only for `verify` timestamps if ever needed and defaults to UTC now.

Argument constraints:
- `source`: matches `^[a-z0-9][a-z0-9_]{0,63}$`. Anything else (including `..`, `/`, empty, upper-case) raises `ValueError` before any filesystem access.
- `data`: `bytes` (zero length allowed).
- `url`, `source_version`, `run_id`: non-empty `str`. `logical_key`: non-empty `str`, at most 512 characters (e.g. `"2026-11-20"`; archive members use `"<key>/<member>"`).
- `fetched_at`: timezone-aware `datetime`; converted to UTC. Naive datetime raises `ValueError`.
- `sha256` arguments: 64 lowercase hex characters, else `ValueError`.

## Outputs (schema)

### Raw file
`<raw_root>/<source>/<sha256[:2]>/<sha256>` — exact bytes, mode `0o444` (`stat.S_IMODE == 0o444`), created via temp file in the same directory.

### Manifest line
File `<manifests_root>/<source>.jsonl`, one line per `store()` call (including repeat fetches of identical bytes). Each line is one JSON object, written with the canonical rule `json.dumps(obj, sort_keys=True, separators=(",", ":"), ensure_ascii=False)` followed by `"\n"`, UTF-8. Fields (all required, no others):

| field | type | value |
|---|---|---|
| `sha256` | str | 64 lowercase hex of the stored bytes |
| `size` | int | `len(data)` |
| `url` | str | as given |
| `fetched_at` | str | UTC, format `YYYY-MM-DDTHH:MM:SS.ffffffZ` (always 6 microsecond digits) |
| `source` | str | as given |
| `source_version` | str | as given |
| `logical_key` | str | as given |
| `run_id` | str | as given |

`ManifestEntry` is a frozen dataclass with the same eight fields (`fetched_at` as aware UTC `datetime`; `to_json_line() -> str`).

`Problem` is a frozen dataclass `(kind: str, source: str, path: str, line: int | None, detail: str)` with `kind` one of `bad_line`, `missing_file`, `hash_mismatch`, `size_mismatch`, `writable_file`, `misplaced_file`, `symlink`.

## Rules (numbered — each gets ≥1 test and ≥1 planted failure)

R1. Content addressing: the raw path is `<raw_root>/<source>/<sha[:2]>/<sha>` where `sha = sha256(data).hexdigest()`. Nothing in the API lets a caller choose the path or file name.
R2. Atomic write: new raw files are written as `<dir>/.tmp-<uuid4hex>` in the destination directory, flushed and `os.fsync`-ed, `chmod 0o444`, then moved into place with `os.link(tmp, dest)` followed by `os.unlink(tmp)` (so an existing destination is never replaced, even in a race); the directory is fsync-ed afterwards. On any failure the temp file is removed and the manifest is not touched. A reader never sees a partially written raw file under its final name.
R3. Read-only: after `store`, the file mode is exactly `0o444`. `store` never changes the mode of a pre-existing raw file.
R4. No overwrite, no delete: the module contains no function that deletes, truncates, renames or rewrites a raw file or a manifest line. If `dest` already exists, `store` first re-hashes its content; if it equals `sha`, no new file is written (dedup); if it differs, `store` raises `RawIntegrityError`, leaves the existing file byte-for-byte unchanged, and appends no manifest line.
R5. Manifest append: after the raw file is in place (written or deduplicated), exactly one line is appended per `store()` call, in one `write()` of the full line, then flushed and fsync-ed. Same bytes stored twice produce two manifest lines and one raw file. The raw file is always written before the manifest line, so no line references a missing file under normal operation.
R6. Concurrency: appends hold an exclusive `fcntl.flock` on `<manifests_root>/<source>.jsonl.lock` (created if missing) around the check-then-append sequence in R7. N concurrent processes/threads each storing M distinct payloads leave a manifest with exactly N*M valid lines, none interleaved or torn, and N*M raw files. Locks are per source; different sources do not block each other.
R7. Truncation guard: under the lock, if the manifest file exists, is non-empty, and its last byte is not `"\n"`, `store` raises `ManifestCorruptError` and writes nothing (it does not repair). The raw file may already exist (harmless).
R8. Strict reading: every lookup (`get_by_key`, `get_all_by_key`, `get_by_sha`, `entries`) parses every line of the manifest; a line that is not valid JSON, is not an object, lacks a field, has an extra field, has a wrong type, a malformed `sha256`/`fetched_at`, or is a blank line raises `ManifestCorruptError` naming the file and 1-based line number. A missing manifest file means "no entries" (empty list / `None`), not an error.
R9. Lookups: `get_by_key` returns the entry from the last matching line (file order); `get_all_by_key` and `get_by_sha` return every match in file order. `get_by_sha` matches `sha256` within the given source only.
R10. `verify(source)` never raises on bad data and never modifies anything. For each manifest line it reports `bad_line` (R8 conditions, per line, continuing past it) and, for each parseable line, `missing_file` (raw file absent), `hash_mismatch` (file content hash != `sha256`), `size_mismatch` (file length != `size`). It also walks `<raw_root>/<source>/**` and reports for every regular file (not starting with `.tmp-`): `hash_mismatch` if the content hash != the file name, `misplaced_file` if it is not located under its own `sha[:2]` directory or its name is not 64 lowercase hex, `writable_file` if `mode & 0o222 != 0`. A truncated last line (no trailing newline) is reported as `bad_line`. Any symlink found in the walk (file or directory) is reported as `symlink` and never followed; a manifest-referenced path that is a symlink is reported as `symlink`. A healthy store returns `[]`. Each (kind, path, line) is reported once.

R14. (added 21 Nov 2026 after review S0-T3) Store refuses any non-regular file at the destination (symlink, dangling symlink, directory) with `RawIntegrityError`; a dedup store on an existing writable file raises `RawIntegrityError` and changes nothing. After creating any new raw file, directory or manifest file, the parent directory is fsynced; the dedup path fsyncs the xx dir and the manifest. `data` that is not `bytes` raises `TypeError`.
R11. `read_bytes` returns the file content only if its hash equals the file name; otherwise raises `RawIntegrityError`.
R12. Time format: `fetched_at` is serialised in UTC with exactly six fractional digits and a literal `Z`; two datetimes denoting the same instant in different offsets produce the same string. Parsing back yields an aware UTC datetime equal to the input.
R13. Source isolation and path safety: `source` validation (see Inputs) happens first in every public method; a source name never produces a path outside `raw_root` / `manifests_root`.

## Edge cases
- Empty payload `b""`: stored (sha `e3b0c442…b855`), size 0, mode 0444; collectors decide whether empty bodies are stored (S0-T4), the manifest does not.
- Large payloads: written in a single pass in memory (`data` is already bytes); no streaming API in Stage 0.
- Two sources storing identical bytes: two raw files (one per source dir); manifests independent.
- `logical_key` or `url` containing quotes, newlines, non-ASCII: JSON-escaped; the line remains a single physical line; round-trips exactly.
- Directory `<raw_root>/<source>/<xx>/` missing: created (`mkdir(parents=True, exist_ok=True)`) with default mode; `raw_root` and `manifests_root` themselves must already exist, else `ManifestError`.
- Running as root: `0444` cannot stop a write, so tests assert on mode bits, never on an attempted write succeeding/failing.
- A stale `.tmp-*` file left by a crash is ignored by lookups and `verify` and is never reused.
- Manifest file modified by hand to reorder lines: not detectable by the manifest (no chain); out of scope, the registry covers append-only guarantees.
- `fetched_at` earlier than previous lines: allowed (no ordering rule).

## Acceptance tests (in words)
A1. (R1, R3) Given a store in tmp dirs, when `store("bse", b"hello", ...)` is called, then the file `<raw>/bse/2c/2cf24dba…9824` exists, contains `hello`, has mode `0o444`, and `sha256(read_bytes)` equals the name.
A2. (R5, R12) Given that call with `fetched_at = 2026-11-21 15:00:00+05:30`, then `bse.jsonl` has exactly one line ending in `"\n"`, it parses to the eight fields with correct types, `fetched_at == "2026-11-21T09:30:00.000000Z"`, `size == 5`, and the line text equals `json.dumps(obj, sort_keys=True, separators=(",",":"), ensure_ascii=False) + "\n"`.
A3. (R12, planted) Given a naive `fetched_at`, then `ValueError`, no raw file and no manifest line exist afterwards.
A4. (R4, R5) Given the same bytes stored twice with different `run_id`/`fetched_at`, then one raw file exists (mtime unchanged by the second call), the manifest has two lines with the same `sha256` and different `run_id`s, and `get_by_sha` returns both in order.
A5. (R4, planted) Given a raw file at the path for `sha(b"AAA")` that was replaced out-of-band by different bytes (chmod u+w, overwrite), when `store(b"AAA")` is called, then `RawIntegrityError` is raised, the file still has the tampered bytes, and the manifest line count is unchanged.
A6. (R4) Given the public API surface, then no public name in the module (functions and `ManifestStore` methods) is among `delete`, `remove`, `unlink`, `overwrite`, `update`, `truncate`, `rewrite`; a test enumerates `dir()` against an allow-list of public names equal to the API in Inputs.
A7. (R2, planted) Given `os.fsync` or `os.link` monkeypatched to raise `OSError` mid-store, then `store` raises, no `.tmp-*` file remains, no file exists at the final path, and no manifest line was appended.
A8. (R2) Given `os.link` monkeypatched to raise `FileExistsError` after the destination was created by a concurrent writer with identical bytes, then `store` treats it as dedup, succeeds, and appends its manifest line.
A9. (R6) Given 4 processes (multiprocessing) each calling `store` 50 times with distinct payloads for one source, then the manifest has 200 lines, every line parses, all `sha256` values are distinct and unique, 200 raw files exist, and `verify()` returns `[]`.
A10. (R6, planted) Given a helper process that holds an exclusive `flock` on `<source>.jsonl.lock` for about 1 second, when a second process calls `store` for the same source, then its manifest line does not appear until the helper releases the lock (the call returns after the release), while a `store` for a different source returns immediately.
A11. (R7, planted) Given a manifest whose last line was truncated (final `"\n"` and part of the JSON removed), when `store` is called, then `ManifestCorruptError`, the manifest bytes are unchanged, and a subsequent `verify()` reports `bad_line` at that line.
A12. (R8, planted) Given manifests each containing one of: invalid JSON, a JSON array, a missing `run_id`, an extra field `foo`, `size` as string `"5"`, `sha256` upper-case, `fetched_at` without `Z`, a blank line in the middle, then each lookup method raises `ManifestCorruptError` naming the line number; `verify` reports `bad_line` for that line and still checks the other lines.
A13. (R8) Given no manifest file for a source, then `get_by_key` is `None`, `get_by_sha` and `entries` are `[]`, and no file is created by the lookup.
A14. (R9) Given three lines for key `2026-11-20` (two fetches, one other key in between), then `get_by_key` returns the last one and `get_all_by_key` returns two in file order.
A15. (R10, planted) Given a healthy store with 3 files, when one raw file's bytes are changed (after chmod u+w) keeping its size, then `verify` returns a `hash_mismatch` for that path and nothing else for the other two; when a file is truncated instead, it returns `hash_mismatch` and `size_mismatch`.
A16. (R10, planted) Given a raw file deleted, then `verify` reports `missing_file` for each manifest line referencing it. Given a file chmod 0644 then `writable_file`. Given a file copied to a wrong directory or named `foo.txt` then `misplaced_file`.
A17. (R10) Given a healthy store, `verify()` returns `[]` and no file's mtime or mode changes; with two sources, `verify("a")` reports only source `a`'s problems and `verify()` reports both.
A18. (R11, planted) Given a tampered raw file, `read_bytes` raises `RawIntegrityError`; with an untouched file it returns the bytes.
A19. (R13, planted) Given `source` values `""`, `".."`, `"a/b"`, `"A"`, `"x"*65`, `"-a"`, then `store`, each lookup and `verify` raise `ValueError` and nothing is created on disk. Given `sha256` argument `"../../etc"` or upper-case, `raw_path` raises `ValueError`.
A20. (Edge) Given `b""` payload, then stored with size 0, mode 0444, and the manifest line `sha256 == "e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855"`.
A21. (Edge) Given `url` and `logical_key` containing `"`, `\n` and `é`, then the manifest remains one physical line per call and entries read back equal the inputs exactly.
A22. (R3, R10) Given a stale `.tmp-abc` file in the raw tree, then `verify` ignores it and `store` of other data succeeds.
A23. (Edge) Given `raw_root` missing, then `ManifestStore(...)` or `store` raises `ManifestError` (no directories created above `raw_root`).

## Not in this module
- HTTP, retry, body checks, outcomes (S0-T4); parsing of raw content.
- Chain hashing of manifest lines (the registry ledgers in S0-T6 are the only hash-chained files).
- The `investos check manifest|raw` command (S0-T5 calls `verify` and maps `kind`s to checks).
- Garbage collection, compaction or deletion of raw files (not in Stage 0; any removal is a human act outside the code).

---

# Stage 1 amendment: manifest schema v2   (from brief S1-T2, task S1-SPEC1, 21 Nov 2026)

Implements decision 0002 point 4 and 0002a ("`access_mode` recorded on every manifest row", "every header sent recorded") for audit condition C2. Module: `src/investos/acquire/manifest.py` (placement per decision 0001; the Stage 0 text above says `core/manifest.py`). Rule numbering continues from R14, acceptance tests from A24. This task ships first (before S1-T1): the collector amendment needs the new `store` arguments, and no v2 line may be written by any code before the version-aware reader is merged everywhere (R19).

## What this amendment supersedes
| Old text | Status |
|---|---|
| "Fields (all required, no others)" with eight fields | superseded: the field set depends on the line's version (R15) |
| `ManifestEntry` "same eight fields" | superseded: five more fields (R16) |
| R8 "has an extra field" | superseded: "has a field outside its version's set" (R15). Missing fields, wrong types, blank lines, invalid JSON stay errors |
| `store(...)` signature | extended: two new required keyword arguments (R16) |
| A2 "parses to the eight fields" | applies to reading a v1 fixture; a line written now has thirteen fields (A26) |
| A12 case "an extra field `foo`" | stays (an extra field on a v1 or a v2 line is still corrupt); new cases in A25 |
R1 to R7, R9 to R14 stand; R10 is extended by R18.

## Inputs (schema changes)
```python
class ManifestStore:
    def store(self, source: str, data: bytes, *, url: str, source_version: str,
              logical_key: str, run_id: str, fetched_at: datetime,
              access_mode: str,                                  # NEW, required: "honest" | "real_browser" | "scoped_exception"
              request_headers: Mapping[str, str] | None,         # NEW, required (no default): the exact headers sent; None only for real_browser when the browser could not report them
              final_url: str,                                    # NEW, required
              fetch_host: str                                    # NEW, required
              ) -> ManifestEntry
```
No default values on the four new arguments, so no row can be written without a decision about them.

## Outputs (schema changes)

### Manifest line, version 2 (the only version written)
Same canonical rule as before (`json.dumps(obj, sort_keys=True, separators=(",", ":"), ensure_ascii=False)` plus `"\n"`). Thirteen fields, all required, no others: the eight of v1 plus

| field | type | value |
|---|---|---|
| `schema` | int | exactly `2` (not bool, not str) |
| `access_mode` | str | `honest`, `real_browser` or `scoped_exception` |
| `request_headers` | object or null | header name (str) to value (str), as sent, with Cookie values redacted (R17); `null` only if `access_mode == "real_browser"` |
| `final_url` | str | the URL of the final response: for HTTP the requested URL (redirects are never followed), for `real_browser` the browser's final URL; non-empty, at most 2048 characters, no control characters |
| `fetch_host` | str | host name of the machine that made the request (`socket.gethostname()` at the caller); non-empty, at most 255 characters, printable, no control characters; makes "Mac only" checkable after the fact |

A **v1 line** is a line with no `schema` key and exactly the eight original fields. Anything else (including `"schema": 1`) is not v1.

`ManifestEntry` gains `schema: int` (1 or 2), `access_mode: str | None`, `request_headers: Mapping[str, str] | None` (a read-only copy), `final_url: str | None` and `fetch_host: str | None` (for v1 entries `schema == 1` and the other four are `None`: access mode "unknown", never assumed honest by code). Entries need not be hashable. `to_json_line()` serialises an entry in its own version: a v1 entry reproduces its original text byte for byte, a v2 entry writes the thirteen fields.

## Rules (numbered, each gets at least one test and one planted failure)

R15. Version-aware strict reading. A line is parsed as follows: if the JSON object has no `schema` key it must be exactly the v1 field set with the v1 types; if `schema` is present it must be the integer `2` (not `True`, not `"2"`, not `2.0`) and the object must be exactly the v2 field set (13 fields) with the v2 types, including `final_url` and `fetch_host` as non-empty strings under R21. Any other value of `schema` (1, 3, null, ...) raises `ManifestCorruptError("unsupported schema ...")`. Missing fields, extra fields (relative to the line's own version), wrong types, a `request_headers` that is not an object or null, header names or values that are not `str`, an `access_mode` outside the three values, `request_headers == null` with a mode other than `real_browser`, and every other R8 condition remain `ManifestCorruptError` naming the file and 1-based line number. v1 and v2 lines may appear in any order in one file. `get_by_key` (latest line wins), `get_all_by_key`, `get_by_sha` and `entries` return entries of both versions in file order.

R16. Writer writes v2 only. `store` always creates a `schema == 2` line and returns an entry with `schema == 2`. Argument validation happens before any filesystem access (R13 order stays: `source` first): `access_mode` outside the three values, a `final_url` or `fetch_host` that breaks R21, raises `ValueError`; `request_headers` that is not `None` or a `Mapping[str, str]` raises `TypeError`; `request_headers is None` with a mode other than `real_browser` raises `ValueError`; a `request_headers` that fails R17 raises `ValueError`. On any of these nothing is written (no raw file, no line). Nothing in the module writes a v1 line except `to_json_line()` on an entry that was read as v1.

R17. Header record rules. Applied by `store` to `request_headers` before serialising (and re-checked by `verify`, R18):
(a) at most 64 headers; each name is an RFC 7230 token of at most 128 characters; each value is a `str` of at most 4096 characters with no CR, LF or NUL; the serialised object is at most 16 KiB; names are unique ignoring case;
(b) header names and values are recorded exactly as given (case kept), except (c);
(c) Cookie redaction: a header whose name lowercased is `cookie` has every `name=value` pair rewritten to `name=<redacted>` (pairs separated by `"; "`); a Cookie value that is not made of `name=value` pairs becomes the whole text `<redacted>`. The cookie names are kept so the row still shows that a cookie was sent. Reason: manifests are committed and pushed to GitHub, and a session cookie must never reach it;
(d) names `authorization`, `proxy-authorization`, `cookie2` and `set-cookie` (compared lowercased) are refused with `ValueError` (the collector never sends them, and a response header must not be recorded as a request header);
(e) the returned entry carries the redacted headers; `store` never mutates the caller's mapping.

R18. Verify. `verify` (R10) handles v1 and v2 lines. In addition to the R10 conditions, each v2 line is reported once as `bad_line` (detail names the reason) when R15 or R21 fails, when its `request_headers` break R17(a), (d), or when a `cookie` header value is not of the form `name=<redacted>` pairs (or exactly `<redacted>`), so a leaked cookie in a hand-edited or buggy line is caught by `investos check manifest`. No new `Problem.kind` is added, and `verify` still never raises and never modifies anything. A healthy mixed v1/v2 store returns `[]`.

R19. Compatibility and deployment order. A Stage 0 reader (before this amendment) treats every v2 line as corrupt (extra fields), reporting `bad_line` or raising `ManifestCorruptError`; that is intended and is why this task merges first: S1-T1 and every other writer is blocked until S1-T2 is merged on `main` and present in every checkout and scheduled job that reads or writes manifests (the Mac, the daily routine, CI). Existing v1 lines are never rewritten, converted or back-filled; v1 stays valid forever. `docs/runbooks/manifest-torn-tail.md` gets a short paragraph saying that a `bad_line` on a complete (newline-terminated) line of an older checkout can mean "a v2 line seen by old code": update the code, do not truncate.

R20. Interplay with earlier rules, stated so tests pin it. R5 (one write, flush, fsync, one line per call) and R6/R7 (flock, torn-tail guard) apply to v2 lines unchanged. The S0-FIX A2 behaviour holds: a reader that finds an unterminated tail takes `LOCK_SH` on `<source>.jsonl.lock` and re-reads before judging, so it never reports a writer's half-written v2 line; a tail that is still unterminated under the lock is a real torn tail (`bad_line`, `store` refuses, R7), exactly as for v1. The index cache signature `(inode, size, mtime_ns, ctime_ns)` is unchanged. The manual repair procedure of A8 (the runbook) is unchanged for v2 files.

R21. `final_url` and `fetch_host`. `store` validates both before any filesystem access: `final_url` is a `str` of 1 to 2048 characters, no character below U+0020 or equal to U+007F, and starts with `https://` or `http://`; `fetch_host` is a `str` of 1 to 255 characters, no character below U+0020 or equal to U+007F, no leading or trailing space. Violations raise `ValueError` (non-str: `TypeError`) and write nothing. They are stored verbatim (no redaction: URLs with credentials or query secrets are the caller's responsibility, and the collector passes the plain data URL). `verify` re-checks them (R18).

## Edge cases
- An old file with 10,000 v1 lines and one appended v2 line: all readable; `get_by_key` of an old key returns the v1 entry (`access_mode is None`).
- `request_headers == {}` (an honest fetch with no headers) is allowed for `honest` and `scoped_exception`, and for `real_browser`; it is different from `null`.
- Header value containing `"`, `\`, non-ASCII: JSON-escaped, one physical line, round trip exact (except Cookie redaction).
- `access_mode` is whatever the collector declares; the manifest does not check it against config.
- A line with `"schema": 2` and `"request_headers": {"Cookie": "<redacted>"}` is valid; `"Cookie": "a=<redacted>;b=<redacted>"` (no space) is not (the redaction form is fixed).
- Two sources share no state: a v2 line in one manifest does not change how another is read.

## Acceptance tests (in words)
A24. (R15) Given a manifest file of three v1 lines (the S0-T3 fixtures), then every lookup returns entries with `schema == 1`, `access_mode is None`, `request_headers is None`; `verify()` is `[]`; `to_json_line()` of each entry equals the original line text exactly.
A25. (R15, planted) Given manifests each holding one bad line among good ones: `"schema": 1`, `"schema": 3`, `"schema": "2"`, `"schema": true`, `"schema": null`, a v2 line missing `access_mode`, a v2 line missing `request_headers`, a v2 line missing `final_url`, a v2 line missing `fetch_host`, a v2 line with `final_url` or `fetch_host` as an int or empty string, a v2 line with an extra field `foo`, a v1-shaped line (no `schema`) that has `access_mode`, `access_mode: "stealth"`, `request_headers` as a list, a header value that is an int, `request_headers: null` with `access_mode: "honest"`, then each lookup raises `ManifestCorruptError` naming the line number, and `verify` reports `bad_line` for exactly that line and still checks the others.
A26. (R16) Given `store(..., access_mode="honest", request_headers={"User-Agent": "u", "Accept-Encoding": "identity"}, final_url=url, fetch_host="mac-1")`, then the manifest line equals `json.dumps(obj, sort_keys=True, separators=(",", ":"), ensure_ascii=False) + "\n"` with exactly thirteen keys, `schema == 2`, the returned entry equals the entry read back, and its `to_json_line()` equals the stored text. Given a file with v1, then v2, then v1-text lines (hand-built), `get_by_key` returns the last matching line whatever its version and `entries` returns file order.
A27. (R16, planted) Given `store` called without any one of `access_mode`, `request_headers`, `final_url`, `fetch_host`, then `TypeError`. Given `access_mode="stealth"`, `request_headers=["a"]`, `request_headers=None` with `honest`, `request_headers=None` with `scoped_exception`, then `ValueError`/`TypeError` as specified and afterwards no raw file, no manifest file change, no `.tmp-*`. Given `request_headers=None` with `real_browser`, then the line has `"request_headers":null`.
A28. (R17, planted) Given header sets with: a name containing a space or a colon, an empty name, a name of 129 characters, a value with CR, LF or NUL, a non-str value, 65 headers, a 17 KiB total, `User-Agent` and `user-agent` together, and each of `Authorization`, `Proxy-Authorization`, `Cookie2`, `Set-Cookie`, then `store` raises `ValueError` and writes nothing.
A29. (R17, planted) Given `{"Cookie": "nsit=SECRET1; nseappid=SECRET2", "Referer": "https://www.nseindia.com/x"}`, then neither the manifest file bytes nor the returned entry contain `SECRET1` or `SECRET2`, the entry's `Cookie` is `nsit=<redacted>; nseappid=<redacted>`, `Referer` is verbatim, and the caller's dict is unchanged. Given `{"cookie": "garbage"}` the stored value is `<redacted>` and the key keeps its case.
A30. (R18, planted) Given hand-written v2 lines with a raw cookie value, with `a=<redacted>;b=<redacted>`, with 65 headers, with an `Authorization` header, then `verify` reports `bad_line` for each; a healthy store of v1 plus v2 lines returns `[]`; `verify` modifies no file.
A31. (R20, S0-FIX A2 regression) Given 2 processes, one appending 200 v2 lines with a 2 KiB `request_headers` each while the other loops `get_by_key` and `entries`, then the reader never raises `ManifestCorruptError`, and the final `verify()` is `[]`.
A32. (R20, R19, planted) Given a v2 file whose last line is cut in the middle (no final newline), then `store` raises `ManifestCorruptError` and leaves the bytes unchanged, `verify` reports `bad_line` for the last line, and following the runbook steps in a test (truncate the fragment under the lock) makes `verify` clean. The runbook file contains a paragraph that mentions `schema` and "v2". A source scan finds no code path that writes a line without `schema` except `to_json_line` on an entry whose `schema == 1`.
A33. (R18) Given a mixed healthy store, `investos check manifest` exits 0; given the same store plus a hand-added v2 line with a raw cookie value, it exits 1 and names the line.
A34. (R21, planted) Given `final_url` of `""`, `"ftp://x"`, `"https://x/\n"`, 2049 characters, an int, and `fetch_host` of `""`, `" mac"`, `"a\tb"`, 256 characters, an int, then `store` raises `ValueError`/`TypeError` and writes nothing. Given a valid pair with non-ASCII host text, the round trip is exact and the v2 line has `final_url` and `fetch_host` as given. A hand-written v2 line violating R21 is `bad_line` in `verify`. For a `real_browser` row whose final URL differs from the logical URL only by a fragment, `final_url` keeps the fragment.

## Not in this module
- What the collector puts into `request_headers` and `access_mode` (collect_base amendment, S1-T1).
- The redirect chain (only `final_url` is recorded; redirects are never followed or stored by the collector).
- Rewriting, converting or back-filling v1 lines; compaction.
- Any hash chain over manifest lines.

## Open questions for Sagar (as raised by the Spec writer; see Resolved below)
1. Cookie redaction (R17c) is a departure from decision 0002 point 4, "every header sent recorded": the cookie names are kept, the values are not, because manifests are pushed to GitHub. Confirm, or say the repo may hold cookie values (not recommended even for a private repo).
2. For `real_browser` rows, do you also want the final URL (after redirects) and the host name of the machine that fetched, as manifest fields? Decision 0002a says "Mac only" can only be recorded, not enforced. Adding them is another schema field (v2 as specced has neither).
3. Row growth in git: every poll is one manifest row, now about 40 percent bigger because of the headers. Polling 22 RSS feeds every minute would be roughly 30,000 rows and some 14 MB of manifest text per day pushed to GitHub. Choose the poll interval (and whether unchanged feed bodies should be recorded as rows at all) before the poller task.
4. v1 rows have no access mode. The spec returns `None` for them ("unknown") and does not assume they were honest. Say if reports should treat them as honest.

Folded into the rules above: 1 -> R17c, A29 (confirmed); 2 -> R15, R16, R21, A25, A26, A27, A34 (13 fields); 3 -> not a manifest rule: the RSS poll interval (15 minutes, body-sha dedup writes no row) belongs to the later poller spec, noted there; 4 -> R15 (v1 reports `None`).

## Resolved (21 Nov 2026; S = Sagar, O = Orchestrator default)
1. (S) Cookie values are redacted (`<redacted>`) and cookie names are kept. Decision 0002 point 4 is read as "every header recorded, cookie values redacted".
2. (O) Yes, add `final_url` and `fetch_host` (the machine's hostname) to v2 for every access_mode. They cost little and make "Mac only" checkable after the fact.
3. (O) The RSS poll interval is 15 minutes, not 1, with a few extra polls overnight. A poll whose body sha matches the last stored body for that feed is outcome `skipped` and writes no row (existing dedup). That gives about 2,000 polls a day, with rows only for changed bodies.
4. (O) v1 rows report access_mode `None` (unknown), as specced. Reports show them as "pre-v2 (honest by construction in stage 0)", but code never assumes it.
