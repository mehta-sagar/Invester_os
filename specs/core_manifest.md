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

`Problem` is a frozen dataclass `(kind: str, source: str, path: str, line: int | None, detail: str)` with `kind` one of `bad_line`, `missing_file`, `hash_mismatch`, `size_mismatch`, `writable_file`, `misplaced_file`.

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
R10. `verify(source)` never raises on bad data and never modifies anything. For each manifest line it reports `bad_line` (R8 conditions, per line, continuing past it) and, for each parseable line, `missing_file` (raw file absent), `hash_mismatch` (file content hash != `sha256`), `size_mismatch` (file length != `size`). It also walks `<raw_root>/<source>/**` and reports for every regular file (not starting with `.tmp-`): `hash_mismatch` if the content hash != the file name, `misplaced_file` if it is not located under its own `sha[:2]` directory or its name is not 64 lowercase hex, `writable_file` if `mode & 0o222 != 0`. A truncated last line (no trailing newline) is reported as `bad_line`. A healthy store returns `[]`. Each (kind, path, line) is reported once.
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
