# Spec registry_chain   (from brief S0-T6)

Modules: `src/investos/registry/__init__.py`, `canon.py` (canonical JSON), `chain.py` (`Ledger`, verify, roots), `ledgers.py` (trial and decision schemas, openers), `check.py` (check `registry` + its plant for the S0-T5 runner). Python 3.11+, stdlib only (`hashlib`, `json`, `fcntl`, `zoneinfo`) plus `investos.core.config` and `investos.check.registry`.

## Inputs (schema)

### Canonical JSON (`investos.registry.canon`)
```python
class CanonicalJsonError(ValueError): ...
def canonical_json(obj: Any) -> str      # exact rule below
def sha256_hex(text: str) -> str         # hashlib.sha256(text.encode("utf-8")).hexdigest()
```
The one and only serialisation rule for hashed data:
`json.dumps(obj, sort_keys=True, separators=(",", ":"), ensure_ascii=False, allow_nan=False)`, applied after validation. Allowed value types: `dict` with `str` keys, `list`, `str`, `int` (not `bool` confusion: `bool` is allowed and serialises as `true`/`false`; `abs(int) <= 2**53 - 1`), `bool`, `None`. **Floats are forbidden** in hashed payloads (any `float`, including `nan`, `inf`, `1.0`): `CanonicalJsonError`. Decimal quantities (prices, weights, thresholds) must be passed as strings (e.g. `"0.05"`), and the producer decides their text form. Also rejected: tuples, sets, `bytes`, `Decimal`, `datetime`, non-`str` dict keys, strings that cannot be encoded as UTF-8 (lone surrogates), and objects nested deeper than 64 levels. The hash input is the UTF-8 encoding of the result.

### Ledger API (`investos.registry.chain`)
```python
class LedgerError(Exception): ...
class LedgerCorruptError(LedgerError): ...      # chain/format broken at .seq
class PayloadError(LedgerError): ...            # schema violation on append
class RootMismatchError(LedgerError): ...

@dataclass(frozen=True)
class Row:
    seq: int            # 1-based
    ts: str             # "YYYY-MM-DDTHH:MM:SS.ffffffZ"
    prev_hash: str      # 64 lowercase hex; genesis row: "0"*64
    payload: Mapping[str, Any]
    hash: str           # 64 lowercase hex

@dataclass(frozen=True)
class VerifyResult:
    ok: bool
    rows: int                       # number of well-formed rows read before the first problem (or all rows)
    first_broken_seq: int | None    # None when ok, or when only the roots file itself is malformed
    scope: Literal["chain", "roots", "none"]
    reason: str                     # "" when ok

@dataclass(frozen=True)
class Root:
    date: str           # IST date "YYYY-MM-DD"
    seq: int
    hash: str

class Ledger:
    def __init__(self, path: Path, *, kind: str, validate: Callable[[Mapping, "Ledger"], None],
                 clock: Callable[[], datetime] | None = None)
    path: Path                                   # read-only property
    kind: str                                    # read-only property
    def append(self, payload: Mapping[str, Any]) -> Row
    def rows(self) -> list[Row]                  # strict; raises LedgerCorruptError
    def head(self) -> Row | None
    def verify(self, *, roots: bool = True) -> VerifyResult
    def write_roots(self) -> list[Root]          # newly appended roots only
    def roots(self) -> list[Root]                # reads <ledger>.roots.jsonl, strict
```
`roots_path` for `.../trials.jsonl` is `.../trials.roots.jsonl` (`path.with_suffix(".roots.jsonl")`). Clock default: aware UTC now.

### The two ledgers (`investos.registry.ledgers`)
```python
def open_trials(cfg: Config, *, clock=None) -> Ledger     # cfg.zones["registry"] / "trials.jsonl"
def open_decisions(cfg: Config, *, clock=None) -> Ledger  # cfg.zones["ledger"] / "decisions.jsonl"
def register_trial(ledger: Ledger, *, trial_id: str, family: str, feature_id: str,
                   params: Mapping[str, Any], hypothesis: str) -> Row     # sets registered_before_run=True
def log_decision(ledger: Ledger, *, date: str, security_id: str, action: str,
                 reasons: Sequence[str], model_versions: Mapping[str, str]) -> Row
```

## Outputs (schema)

### Row on disk
One line per row, `canonical_json(row_obj) + "\n"` where `row_obj = {"seq": int, "ts": str, "prev_hash": str, "payload": object, "hash": str}`.
`hash = sha256_hex(canonical_json({"seq": seq, "ts": ts, "prev_hash": prev_hash, "payload": payload}))` (the hash field itself is excluded from its input).

### Payloads
`trials` (file `registry/trials.jsonl`), exact key set, no others:
`trial_id` str non-empty (unique in the ledger), `family` str non-empty, `feature_id` str non-empty, `params` object (any canonical-JSON-valid content; decimals as strings), `hypothesis` str non-empty, `registered_before_run` bool that must be `true`.
`decisions` (file `data/ledger/decisions.jsonl`), exact key set:
`date` str `YYYY-MM-DD` (valid calendar date), `security_id` str non-empty, `action` one of `buy`, `sell`, `hold`, `no_trade`, `reasons` list of non-empty str with at least 1 element, `model_versions` object str->str (may be empty only if `action == "no_trade"`).

### Roots file
`<ledger>.roots.jsonl`: one canonical-JSON line per closed IST day that has rows: `{"date":"2026-11-20","hash":"<64 hex>","seq":42}` — the hash of the last row whose `ts`, converted to `Asia/Kolkata`, falls on that date. Dates strictly increasing. This file is meant to be committed to git.

## Rules (numbered — each gets ≥1 test and ≥1 planted failure)

R1. Canonical JSON: `canonical_json` output is identical for dicts with different key insertion order (at every depth), uses no whitespace, keeps non-ASCII characters literally (`ensure_ascii=False`), and rejects every forbidden type listed above with `CanonicalJsonError` (including `float`, `nan`, `inf`, `1.0`, tuple, `Decimal`, non-str keys, ints beyond ±(2**53-1), nesting deeper than 64). Equal-looking number types are not coerced: `1` and `"1"` and `True` hash differently.
R2. Row hash: `hash` is computed exactly as specified in Outputs and stored lowercase hex; recomputation from the stored fields must reproduce it. A published test vector fixes the format: for `seq=1`, `ts="2026-01-01T00:00:00.000000Z"`, `prev_hash="0"*64`, `payload={"a":1}` the hashed text is `{"payload":{"a":1},"prev_hash":"0000000000000000000000000000000000000000000000000000000000000000","seq":1,"ts":"2026-01-01T00:00:00.000000Z"}` (tests assert this exact string and compare the digest with an independently computed `hashlib.sha256`).
R3. Genesis and numbering: the first row has `seq = 1` and `prev_hash = "0"*64`; each later row has `seq = previous.seq + 1` and `prev_hash = previous.hash`.
R4. Append: `append(payload)` (1) validates `payload` with the ledger's schema validator (R5/R6) and `canonical_json`, (2) takes an exclusive `fcntl.flock` on `<path>.lock`, (3) re-reads and fully verifies the existing chain (R7, without roots) and refuses with `LedgerCorruptError` if broken, (4) takes `ts` from the clock in UTC (`YYYY-MM-DDTHH:MM:SS.ffffffZ`), which must be `>=` the previous row's `ts` (else `LedgerError("clock moved backwards")`), (5) writes the single line with one `write()`, flush and `fsync`, (6) returns the `Row`. The file is created on first append; its directory must already exist. A failed append writes nothing.
R5. Trials schema: payload must match the `trials` key set and types exactly; `registered_before_run` must be exactly `True` (not `1`, not `"true"`); `trial_id` must not already exist in the ledger (`PayloadError("duplicate trial_id")`); `params` must be canonical-JSON-valid (so floats are refused).
R6. Decisions schema: exact key set and types; `date` must parse with `date.fromisoformat` and equal its own `isoformat()`; `action` in the allowed set; `reasons` non-empty list of non-empty strings; `model_versions` rule above. Several decisions for the same `(date, security_id)` are allowed (a correction is a new row, never an edit).
R7. Verify: `verify(roots=False)` reads the file as bytes and walks line by line. Position `p` (1-based) is broken, and `first_broken_seq = p`, `scope="chain"`, if any of the following hold at position `p`, checked in this order: the line is blank, not valid UTF-8/JSON, not an object, has missing or extra row keys, has wrong types or malformed `ts`/`prev_hash`/`hash`; `seq != p`; `prev_hash !=` hash of the row at position `p-1` (genesis: zeros); recomputed hash `!= hash`; `ts` is earlier than the previous row's `ts`; or (for the last line only) the file does not end with `"\n"`. The first failing position ends the walk. An empty or absent file is `ok` with `rows == 0`.
R8. Tamper coverage (consequences of R7, each is tested): editing a past row's `payload`, `ts` or `hash` fails at that row's `seq`; deleting a row (not the last) fails at the position where the deleted row was; reordering rows fails at the first out-of-place position; inserting a foreign row fails at the insertion position; truncating the last line (without removing the final newline guard) fails at that last position; an edit that also recomputes that row's own hash fails at the next row (`prev_hash` mismatch); an edit of the final row with a recomputed hash, and deletion of final rows, are not detectable by the chain alone and are covered only by R10.
R9. Daily roots: `write_roots()` appends one root per IST date `d` such that `d` is strictly before today's IST date (clock), the ledger has at least one row on `d`, and no root for `d` exists yet; root = `{date, seq, hash}` of the last row of `d`. It first verifies the chain and all existing roots (R10) and raises `LedgerCorruptError` / `RootMismatchError` instead of writing if anything is wrong. It never rewrites, reorders or removes an existing root line. Today's (open) IST day never gets a root. Returned list = roots newly appended (possibly empty). IST date of a row = `ts` parsed as UTC then converted with `ZoneInfo("Asia/Kolkata")` (so `2026-11-20T19:00:00Z` is `2026-11-21` IST).
R10. Roots verification (`verify(roots=True)`, default): after a clean chain walk, every root line must parse (else `ok=False, scope="roots", first_broken_seq=None`), have strictly increasing dates, `seq <= rows` (else `first_broken_seq = root.seq`, reason `ledger truncated`), `hash ==` the hash of row `seq` (else `first_broken_seq = root.seq`, reason `root mismatch`), the IST date of row `seq` equal to `root.date`, and no row `seq+1` on the same IST date (else reason `root not last of day`). The reported `first_broken_seq` is the smallest of all findings. Missing roots for closed days are not a failure (they are not written yet). An absent roots file is `ok`.
R11. Append-only API: the public surface of `Ledger` is exactly `{path, kind, append, rows, head, verify, write_roots, roots}`; the modules contain no function that updates, deletes, truncates, rewrites or reorders rows or roots, and neither `Ledger` nor the openers open the ledger file in any mode other than read or append. `rows()` and `head()` raise `LedgerCorruptError` (with `.seq`) if the chain is broken rather than returning partial data.
R12. Concurrency: N processes each appending M payloads through `Ledger.append` yield exactly N*M rows with seq `1..N*M`, a valid chain, and no torn lines.
R13. Check integration: `investos.registry.check` registers `@check("registry")` in the S0-T5 default registry: for `open_trials(cfg)` and `open_decisions(cfg)` run `verify(roots=True)`; absent files pass; any `ok=False` fails with message `"<kind>: broken at seq <n>: <reason>"` (one detail line per ledger); passes with `"trials: N rows OK; decisions: M rows OK"`. It also registers `@plant_for("registry", setup=...)`: setup appends 3 valid rows to each ledger (fixed clock) in the temp copy; the plant rewrites `payload` text of seq 1 of `trials.jsonl` in place (flip one character of `hypothesis`) without touching the hash. The check must be read-only (never calls `append` or `write_roots`).

## Edge cases
- Payload with non-ASCII text (`"hypothesis": "सिग्नल"`): hashed and stored as literal UTF-8; verify round-trips.
- Payload key order in the original dict is irrelevant; the stored line is sorted.
- Row with `payload == {}`: allowed by `Ledger` itself but refused by both ledgers' validators (missing keys).
- Two appends in the same microsecond: allowed (`ts` equal is not "backwards").
- Clock returns naive datetime: `ValueError` at append, nothing written.
- A manually appended valid-looking line with correct hashes computed by someone else: undetectable by the chain (integrity, not authenticity); the committed roots are the defence.
- A ledger file with CRLF line endings: any `\r` byte anywhere in the file is a format error at its position (R7).
- Ledger file mode is not changed by this module (git handles protection); `decisions.jsonl` is not read-only.
- Daily root computed on a day when the machine clock is wrong: the root is whatever the injected clock says; sanity of the clock is not checked beyond monotonic `ts`.
- Very large ledgers: full verification on every append is O(n); accepted for Stage 0 (thousands of rows).

## Acceptance tests (in words)
Fixtures: `FakeClock` (settable aware UTC), tmp dirs, a validator that accepts anything for generic chain tests (`kind="test"`), real validators for ledger tests.

A1. (R1) Given `{"b":1,"a":{"y":2,"x":[true,null,"é"]}}` and the same dict built in the other key order, then both `canonical_json` outputs are the identical string `{"a":{"x":[true,null,"é"],"y":2},"b":1}`.
A2. (R1, planted) Given each of `0.1`, `1.0`, `float("nan")`, `float("inf")`, `(1,2)`, `{1,2}`, `Decimal("1")`, `b"x"`, `datetime.now()`, `{1: "a"}`, `2**53`, a lone surrogate string `"\ud800"`, and 65-deep nesting, then `canonical_json` raises `CanonicalJsonError`. Given `"0.1"` (string) it succeeds. Given `True`, `1`, `"1"` the three outputs differ.
A3. (R2) Given the test vector row, then the hashed text equals the exact string in R2 and the stored `hash` equals `hashlib.sha256` of its UTF-8 bytes; changing any one input (seq, ts, prev_hash, a payload value) changes the hash.
A4. (R3, R4) Given an empty ledger, when three payloads are appended, then rows have seq 1, 2, 3, row 1 has `prev_hash == "0"*64`, rows 2 and 3 link to the previous row's hash, the file has three lines each ending in `\n`, and each line equals `canonical_json(row_obj)+"\n"`.
A5. (R4, planted) Given a ledger whose second line was edited, when `append` is called, then `LedgerCorruptError` with `.seq == 2` and the file is unchanged. Given a clock earlier than the last row's `ts`, then `LedgerError("clock moved backwards")` and nothing is written. Given a payload containing a float, then `CanonicalJsonError`/`PayloadError` and nothing is written. Given a naive clock datetime, `ValueError`.
A6. (R5) Given `register_trial(...)` with valid arguments, then the row payload has exactly the six keys, `registered_before_run is True`, and `params` with string decimals (`{"threshold":"0.05"}`) round-trips.
A7. (R5, planted) Given a payload with an extra key, a missing key, `registered_before_run` as `1`, `"true"` or `false`, an empty `hypothesis`, `params` containing `0.05` (float), or a repeated `trial_id`, then `PayloadError` (or `CanonicalJsonError` for the float) and no row is written.
A8. (R6) Given a valid decision, then it is appended; given two decisions with the same `(date, security_id)`, both are stored.
A9. (R6, planted) Given `action="short"`, `date="2026-02-30"`, `date="21-11-2026"`, `reasons=[]`, `reasons=[""]`, `security_id=""`, `model_versions` with a non-string value, `model_versions={}` with `action="buy"`, or an extra key, then `PayloadError` and no row written; `model_versions={}` with `no_trade` and one reason is accepted.
A10. (R7, planted) Given a ledger of 5 rows, then `verify` is `ok` with `rows == 5`; and each of the following mutations (applied to a fresh copy) is reported by `first_broken_seq` as shown: change a payload value in row 3 -> 3; change `ts` of row 3 -> 3; change `hash` of row 3 to another valid-looking hex -> 3; change `hash` of row 3 to upper case -> 3; blank line inserted after row 2 -> 3; invalid JSON on line 4 -> 4; extra key `"x":1` in row 2 -> 2; remove the final `"\n"` -> 5; add a `\r` to line 2 -> 2.
A11. (R8, planted) Given 5 rows: delete row 3 -> `first_broken_seq == 3`; delete row 1 -> 1; swap rows 2 and 3 -> 2; insert a copy of row 2 between rows 4 and 5 -> 5; replace row 3 payload and recompute row 3's own hash -> 4; replace the last row's payload and recompute its hash -> `verify(roots=False)` is `ok` (documented limit) but `verify(roots=True)` after `write_roots` in a later day reports `root mismatch` at seq 5; delete last row after roots written -> `ledger truncated` at the root's seq.
A12. (R7) Given an empty file and an absent file, then `verify` is `ok`, `rows == 0`, `scope == "none"`; `head()` is `None`.
A13. (R9) Given rows at UTC `2026-11-19T20:00Z` (IST 20 Nov 01:30), `2026-11-20T12:00Z`, `2026-11-20T19:00Z` (IST 21 Nov 00:30) and clock at `2026-11-21T10:00Z` (IST 21 Nov), when `write_roots` runs, then exactly one root: date `2026-11-20`, seq = the second row's seq, hash = that row's hash (the third row belongs to the open day 21 Nov IST). Roots file line equals `canonical_json({...})+"\n"`.
A14. (R9, planted) Given the same state, when `write_roots` runs a second time, then it returns `[]` and the file is unchanged. Given a later day and new rows, then only the new date's root is appended and the earlier roots lines are byte-identical. Given a tampered row that was covered by an existing root, then `write_roots` raises (`LedgerCorruptError`/`RootMismatchError`) and writes nothing. Given a ledger with only today's rows, then no root is written.
A15. (R10, planted) Given valid roots, then `verify` is `ok`; and each of: a root line with wrong `hash`, `seq` beyond the row count, a `date` not matching the row's IST date, a root pointing at a row that is not the last of its day, a duplicated/unsorted date, an invalid JSON root line, makes `verify` fail (`scope="roots"`; `first_broken_seq` equals the root's `seq` where one exists, `None` for a malformed roots file).
A16. (R10) Given roots exist and also a chain break at row 2 and a root mismatch at seq 4, then the reported `first_broken_seq` is 2 (smallest).
A17. (R11, planted) Given `dir(Ledger)`, then its public names (not starting with `_`) equal exactly `{append, head, kind, path, roots, rows, verify, write_roots}`; a source scan of the `registry` package finds no `os.remove`, `unlink`, `truncate`, `rename`, `replace`, and no `open(` with mode `w`, `r+`, `a+` or `x` on ledger paths (append mode `"ab"` appears only in `append`/`write_roots`).
A18. (R11) Given a corrupted chain, then `rows()` and `head()` raise `LedgerCorruptError` carrying `.seq`; they never return a partial list.
A19. (R12) Given 4 processes each appending 25 payloads to one ledger, then 100 rows, seq 1..100 contiguous, `verify` ok, no torn or duplicated lines.
A20. (R13, Exit gate 4) Given a healthy trials ledger with 3 rows and a decisions ledger with 3 rows in a tmp config, then the check function `registry` passes with `"trials: 3 rows OK; decisions: 3 rows OK"`; after editing one character of row 2's `hypothesis` in `trials.jsonl`, the check fails with a message containing `trials: broken at seq 2`; and via the S0-T5 CLI, `main(["registry"])` returns exit code 1 and writes a report with that failure.
A21. (R13, planted) Given the S0-T5 harness (`main(["--planted", "registry"])`), then the `registry` plant is `caught` and exit code 0; the check never changes the ledger files (file bytes identical before/after a plain run). With the plant monkeypatched to a no-op the result is `missed` and exit 1.
A22. (Edge) Given Devanagari text in a payload, then bytes on disk are UTF-8 (not `\u` escapes), `verify` is ok, and the hash input contains the literal characters.
A23. (Edge, planted) Given two appends with the same fake-clock microsecond, then both succeed with equal `ts`; given the second earlier by 1 microsecond, `LedgerError`.

## Not in this module
- Committing roots to git or any git operation (human/Orchestrator step).
- Signing, encryption, authenticity of ledger writers; remote anchoring of roots.
- Reading ledgers for analytics (e.g. counting trials for multiple-testing adjustment): later stages read through `rows()`.
- Schema evolution of payloads (new keys require a new `kind` or a spec change).
- The runner, CLI and report format (S0-T5); config loading (S0-T2).
