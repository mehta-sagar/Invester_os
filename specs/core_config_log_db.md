# Spec core_config_log_db   (from brief S0-T2)

Modules: `src/investos/core/config.py`, `log.py`, `db.py`, `__init__.py`; `config/investos.example.toml`; `db/migrations/0000_bootstrap.sql`.
Python 3.11+. Deps: stdlib (`tomllib`, `zoneinfo`, `json`, `hashlib`, `subprocess` for `git check-ignore`) and `duckdb`.

## Inputs (schema)

### Config file (TOML)
```toml
[general]
timezone = "Asia/Kolkata"          # str, must equal "Asia/Kolkata" in Stage 0

[paths]                            # all six keys required; str; relative paths resolve against repo_root
raw        = "data/raw"
manifests  = "data/manifests"
canonical  = "data/canonical"
ledger     = "data/ledger"
reports    = "reports"
registry   = "registry"

[db]
migrations_dir = "db/migrations"   # str, same resolution rule

[rate_limits]
default_interval_s = 0.35          # number > 0 (seconds between request starts, per host)
[rate_limits.hosts]                # optional; host (lowercase) -> number > 0
"www.bseindia.com" = 0.35
```
`config/investos.example.toml` is exactly this content (all values as above) and must itself load successfully against a repo root that has the six zone directories.

### Environment variables
- `INVESTOS_CONFIG`: path of the TOML file (used when `load_config(path=None)`).
- `INVESTOS_ROOT`: repo root (used when `repo_root=None`).
- `INVESTOS_<SECTION>__<KEY>` (upper-case, double underscore between section and key) overrides one scalar config value, e.g. `INVESTOS_PATHS__RAW=/mnt/raw`, `INVESTOS_RATE_LIMITS__DEFAULT_INTERVAL_S=0.5`, `INVESTOS_GENERAL__TIMEZONE`, `INVESTOS_DB__MIGRATIONS_DIR`. Per-host rate limits have no env override.

### Python API
```python
class ConfigError(Exception): ...

@dataclass(frozen=True)
class RateLimits:
    default_interval_s: float
    hosts: Mapping[str, float]                 # lowercase host -> seconds
    def interval_for(self, host: str) -> float # host lowercased; falls back to default

@dataclass(frozen=True)
class Config:
    repo_root: Path                            # absolute, resolved
    timezone: str                              # "Asia/Kolkata"
    zones: Mapping[str, Path]                  # keys: raw, manifests, canonical, ledger, reports, registry; absolute, resolved
    migrations_dir: Path                       # absolute, resolved
    rate_limits: RateLimits
    @property
    def db_path(self) -> Path                  # zones["canonical"] / "investos.duckdb"

def load_config(path: Path | None = None, *, env: Mapping[str, str] | None = None,
                repo_root: Path | None = None) -> Config
```
`env=None` means `os.environ`.

### Logging API (`investos.core.log`)
```python
def get_run_id() -> str                                     # same value for the whole process
def configure_logging(sink: TextIO | Path | None = None, *, level: str = "INFO",
                      clock: Callable[[], datetime] | None = None, run_id: str | None = None) -> None
def get_logger(task: str) -> TaskLogger
class TaskLogger:
    def debug/info/warning/error(self, event: str, **fields: Any) -> None
```
`sink=None` means `sys.stderr`; a `Path` is opened in append mode, UTF-8. `clock` returns an aware UTC datetime (default `datetime.now(timezone.utc)`).

### DB API (`investos.core.db`)
```python
class MigrationError(Exception): ...

def open_db(cfg: Config, *, read_only: bool = False, apply: bool = True) -> duckdb.DuckDBPyConnection
def apply_migrations(con, migrations_dir: Path, *, clock=None) -> list[str]   # names applied this call
def migration_problems(con, migrations_dir: Path) -> list[str]                # read-only audit, [] means healthy
```
Migration files: `db/migrations/NNNN_<slug>.sql`, `NNNN` four digits, slug `[a-z0-9_]+`.

## Outputs (schema)

### Log line (one JSON object per line, UTF-8, `\n` terminated)
```json
{"ts":"2026-11-21T09:30:00.123456Z","level":"INFO","run_id":"20261121T093000Z-9f3a1c2e","task":"collect.bse","event":"fetched","fields":{"key":"2026-11-20","size":1234}}
```
Top-level keys exactly: `ts` (str, UTC, always `Z` suffix, 6-digit microseconds), `level` (`DEBUG|INFO|WARNING|ERROR`), `run_id` (str), `task` (str), `event` (str), `fields` (object, may be `{}`). Keys are written in the order above (not sorted). `ensure_ascii=False`, compact separators `(",", ":")`.

### DuckDB
File `data/canonical/investos.duckdb` (i.e. `Config.db_path`). Table created by the runner itself:
```sql
CREATE TABLE IF NOT EXISTS schema_migrations(
  version    VARCHAR PRIMARY KEY,   -- "0000"
  name       VARCHAR NOT NULL,      -- "0000_bootstrap.sql"
  sha256     VARCHAR NOT NULL,      -- hex of the raw file bytes
  applied_at TIMESTAMP NOT NULL     -- UTC, naive
);
```
`db/migrations/0000_bootstrap.sql` (added only if the skeleton lacks it): `CREATE TABLE meta(key VARCHAR PRIMARY KEY, value VARCHAR NOT NULL); INSERT INTO meta VALUES ('schema_family','investos');`

## Rules (numbered — each gets ≥1 test and ≥1 planted failure)

**Config**

R1. Config file resolution: explicit `path` argument, else `INVESTOS_CONFIG`, else `<repo_root>/config/investos.toml`. Repo root resolution: explicit `repo_root` argument, else `INVESTOS_ROOT`, else the nearest ancestor directory of the config file that contains `pyproject.toml`. If no config file or no repo root can be determined, raise `ConfigError` naming what was tried.
R2. Strict schema. Malformed TOML (wrapping `tomllib.TOMLDecodeError`), a missing required key, an unknown section or key, or a wrong value type raises `ConfigError` whose message contains the offending dotted key (or the file path for a parse error). Booleans are not accepted where numbers are required.
R3. Env override: for each recognised `INVESTOS_<SECTION>__<KEY>` variable the env value replaces the file value before validation (numbers parsed with `float()`; a non-numeric value raises `ConfigError`). Any env var matching `^INVESTOS_[A-Z0-9_]+__[A-Z0-9_]+$` that names no recognised key raises `ConfigError` (typo guard). Other `INVESTOS_*` vars (`INVESTOS_CONFIG`, `INVESTOS_ROOT`) are not overrides.
R4. Path resolution: every path in `[paths]` and `db.migrations_dir` that is relative is joined to `repo_root`; all are made absolute with `Path.resolve()`. `~` is not expanded. The six zones must be pairwise distinct and no zone may be inside another zone (compare resolved paths); violation raises `ConfigError`.
R5. Zone existence: each of the six zone paths and `migrations_dir` must already exist and be a directory; `load_config` never creates anything. The error names the zone key and the resolved path.
R6. Raw not in git tree: if `zones["raw"]` is inside `repo_root` and `repo_root` contains a `.git` entry, the raw path must be git-ignored, verified by running `git -C <repo_root> check-ignore -q -- <raw_path>/` (exit 0 = ignored). Exit 1 (not ignored), or `git` not runnable, raises `ConfigError` ("refuse to start"). A raw path outside `repo_root`, or a repo root with no `.git`, passes without calling git.
R7. Rate limits: `default_interval_s` and every host value must be finite and > 0; host keys must already be lowercase; `interval_for(host)` returns the host value if present (lookup lowercased) else the default. Default when `[rate_limits]` is absent is not allowed (section required).
R8. `general.timezone` must equal `"Asia/Kolkata"` and be loadable by `zoneinfo.ZoneInfo`; otherwise `ConfigError`.

**Logging**

R9. Every call emits exactly one line with the key set and order in Outputs. `fields` holds all keyword arguments; a keyword named like a top-level key (`ts`, `level`, `run_id`, `task`, `event`) stays inside `fields` and never overrides the top-level value.
R10. `run_id` has the form `YYYYMMDDTHHMMSSZ-<8 lowercase hex>`, is generated once per process on first use, and is identical across all loggers and calls until `configure_logging(run_id=...)` replaces it explicitly. `get_run_id()` returns the same value the log lines carry.
R11. `ts` is UTC with `Z`. If the injected clock returns a naive datetime or one with a non-UTC offset, it is converted (aware) or rejected with `ValueError` (naive) at `configure_logging`/log time.
R12. Level filtering: events below the configured level are dropped; an unknown level name raises `ValueError` at `configure_logging`. Logging never raises for an unserialisable field value: such values are replaced by `repr(value)` (str). Embedded newlines in any string are JSON-escaped so one event is always exactly one physical line.

**DB**

R13. `open_db(cfg)` opens (creating the file if absent, `read_only=False`) `cfg.db_path`; with `apply=True` it then calls `apply_migrations(con, cfg.migrations_dir)`. With `read_only=True` it never creates the file (raises `MigrationError` if the file is absent) and never applies migrations (`apply` is ignored/forced False).
R14. Discovery: migration files are `*.sql` directly in `migrations_dir`, sorted by name. A file not matching `^\d{4}_[a-z0-9_]+\.sql$`, a duplicate `NNNN`, or a gap in the sequence (must be contiguous starting at `0000`) raises `MigrationError` before anything is applied.
R15. Apply: each unapplied migration runs inside one transaction together with its `schema_migrations` row (`sha256` of the raw file bytes, `applied_at` UTC). If the SQL fails the transaction is rolled back, nothing is recorded, later migrations are not attempted, and `MigrationError` is raised naming the file. Returned list is the names applied in this call, in order.
R16. Tamper refusal: before applying anything, for every row already in `schema_migrations` the file `name` must exist in `migrations_dir` and its current sha256 must equal the recorded one. Otherwise raise `MigrationError` naming the file and nothing new is applied (this holds even when new migrations are pending).
R17. Ordering: an unapplied file whose `NNNN` is lower than the highest applied version raises `MigrationError` (no back-filling).
R18. Idempotence: calling `apply_migrations` on a fully migrated database returns `[]` and does not touch `schema_migrations` rows.
R19. `migration_problems(con, dir)` performs the R14/R16/R17 checks without applying or writing anything and returns human-readable strings (empty list = healthy); it is what the S0-T5 `migrations` check calls. Pending-but-unapplied files in contiguous order are not a problem.

## Edge cases
- Empty `migrations_dir` (no files): `apply_migrations` returns `[]`; `migration_problems` returns `[]`; `open_db` still creates `schema_migrations`.
- Migration file with a trailing-whitespace-only edit counts as changed (hash is over raw bytes; no normalisation). CRLF vs LF counts as changed.
- Migration SQL containing several statements is executed as one script; partial failure rolls the whole file back.
- Config values given as TOML integers where floats are expected (e.g. `default_interval_s = 1`) are accepted and converted to float.
- Zone path that is a file, a dangling symlink or unreadable: `ConfigError`.
- Symlinked zone: resolved before the nesting/git checks.
- `INVESTOS_PATHS__RAW` set to a relative path resolves against `repo_root`, not the current directory.
- Two processes calling `open_db` write mode on the same file: second gets duckdb's own lock error, wrapped in `MigrationError("database locked")`; no retry.
- Log sink path whose parent does not exist: `FileNotFoundError` at `configure_logging` (not swallowed).
- `get_logger` before `configure_logging`: logs to stderr at INFO with the process run_id.
- Non-ASCII text in `event`/`fields` is written as UTF-8, not `\u` escapes.

## Acceptance tests (in words)
A1. (R1) Given a TOML file passed explicitly and an `INVESTOS_CONFIG` pointing elsewhere, when `load_config(path=...)` runs, then the explicit file is used; with `path=None` the env file is used; with neither, `<root>/config/investos.toml` is used.
A2. (R1, planted) Given no config file anywhere, when `load_config()` runs, then `ConfigError` is raised and its message lists the locations tried. Given a config file in a directory tree with no `pyproject.toml` and no `INVESTOS_ROOT`, then `ConfigError`.
A3. (R2, planted) Given a config file with invalid TOML syntax, then `ConfigError` mentions the file path. Given a file missing `paths.ledger`, an extra key `paths.extra`, an unknown section `[foo]`, or `default_interval_s = "0.35"` (string) or `true`, then each raises `ConfigError` containing the dotted key.
A4. (R2) Given `config/investos.example.toml` and a tmp repo root with the six zone directories and migrations dir created, when loaded, then a valid `Config` is returned with `timezone == "Asia/Kolkata"` and `rate_limits.interval_for("example.com") == 0.35`.
A5. (R3) Given `INVESTOS_PATHS__RAW=/other/raw` (existing dir outside the repo) and `INVESTOS_RATE_LIMITS__DEFAULT_INTERVAL_S=0.5`, when loaded, then `zones["raw"] == /other/raw` and the default interval is 0.5 while the file still says 0.35.
A6. (R3, planted) Given env var `INVESTOS_PATHS__RAWW=/x` (typo) then `ConfigError`; given `INVESTOS_RATE_LIMITS__DEFAULT_INTERVAL_S=abc` then `ConfigError`.
A7. (R4) Given relative zone paths, when loaded with `repo_root=tmp`, then every zone is absolute, resolved and starts with `tmp`; given a zone given as a symlink, then the resolved target is returned.
A8. (R4, planted) Given `paths.raw` equal to `paths.manifests`, or `paths.raw = "data/raw"` and `paths.manifests = "data/raw/manifests"` (nested), then `ConfigError`.
A9. (R5, planted) Given one zone directory (e.g. `ledger`) deleted from disk, when loaded, then `ConfigError` names `ledger` and the resolved path, and the directory was not created. Same for a zone that is a regular file and for a missing `migrations_dir`.
A10. (R6) Given a tmp git repo (`git init`) whose `.gitignore` lists `data/raw/` and the zone exists, when loaded, then no error. Given repo with no `.git`, raw inside it passes. Given raw outside the repo, passes.
A11. (R6, planted) Given a tmp git repo where `data/raw` is NOT ignored, when loaded, then `ConfigError` ("refuse to start"). Given `git` made unrunnable (PATH emptied via the injected `env`/monkeypatched PATH) with a `.git` present and raw inside, then `ConfigError`.
A12. (R7, planted) Given `default_interval_s = 0`, `-1`, `nan`, `inf`, or a host key `"WWW.BSEINDIA.COM"`, then `ConfigError`. Given host `www.bseindia.com = 1.0`, `interval_for("WWW.BSEINDIA.COM") == 1.0` and any other host returns the default.
A13. (R8, planted) Given `timezone = "UTC"` or `"Not/AZone"`, then `ConfigError`.
A14. (R9) Given a logger for task `collect.bse` writing to an in-memory sink, when `info("fetched", key="2026-11-20", size=1234)` is called with a fixed clock, then the sink has exactly one line, it parses as JSON, its keys are `ts, level, run_id, task, event, fields` in that order, and `fields == {"key":"2026-11-20","size":1234}`.
A15. (R9, planted) Given `info("x", ts="evil", level="DEBUG", task="evil")`, then top-level `ts`, `level`, `task` keep their real values and the evil values appear only inside `fields`.
A16. (R10) Given two loggers for different tasks, then both lines carry the same `run_id`, it matches the regex, and equals `get_run_id()`. After `configure_logging(run_id="fixed-1")` new lines carry `fixed-1`.
A17. (R11) Given a clock returning `2026-11-21 15:00:00+05:30`, then `ts == "2026-11-21T09:30:00.000000Z"`. (planted) Given a naive datetime from the clock, then `ValueError`.
A18. (R12) Given level `WARNING`, `info(...)` writes nothing and `error(...)` writes a line; (planted) `configure_logging(level="LOUD")` raises `ValueError`.
A19. (R12, planted) Given a field value that is an object with no JSON form (e.g. a `set` or arbitrary class) and a string field containing `"a\nb"`, then the call does not raise, the sink gets exactly one physical line, the set is rendered as its `repr` string, and the newline is escaped (`\n` inside the JSON string).
A20. (R12) Given `event="résumé"`, then the raw line contains `résumé` literally and not `é`.
A21. (R13) Given a fresh tmp config, when `open_db(cfg)` runs, then `data/canonical/investos.duckdb` exists, `schema_migrations` has one row `0000` with the sha256 of `0000_bootstrap.sql`, and `meta` contains `schema_family=investos`.
A22. (R13, planted) Given no DB file, when `open_db(cfg, read_only=True)` runs, then `MigrationError` and no file is created. Given an existing healthy DB, read-only open works and a write statement through that connection fails.
A23. (R14, planted) Given a migrations dir containing `0000_a.sql` and `0002_c.sql` (gap), or `0000_a.sql` and `0000_b.sql` (duplicate), or `bad-name.sql`, then `MigrationError` is raised before any migration is applied (the DB has no `schema_migrations` rows).
A24. (R15) Given `0000_bootstrap.sql` and `0001_x.sql` (creates a table), when applied, then both are applied in order, the returned list is `["0000_bootstrap.sql","0001_x.sql"]`, and each recorded sha256 equals `hashlib.sha256(file_bytes)`.
A25. (R15, planted) Given `0001_bad.sql` whose second statement is invalid SQL after a first statement that creates table `t1`, then `MigrationError` names `0001_bad.sql`, table `t1` does not exist (rolled back), no `0001` row exists, and a later `0002_ok.sql` was not applied.
A26. (R16, planted) Given an applied DB, when `0001_x.sql` is edited by one byte (or only gets a trailing space) and `apply_migrations` runs again, then `MigrationError` names `0001_x.sql`; given additionally a pending new `0002_y.sql`, it is still not applied. Given an applied file deleted from the directory, then `MigrationError`.
A27. (R17, planted) Given a DB where migrations `0000`, `0001`, `0003` are recorded as applied (rows seeded with correct hashes; files `0000..0003` all present), and `0002_late.sql` is therefore unrecorded, when `apply_migrations` runs, then `MigrationError` (back-fill refused) and `0002` is not applied.
A28. (R18) Given a fully migrated DB, when `apply_migrations` runs twice more, then it returns `[]` both times and `applied_at` values are unchanged.
A29. (R19) Given a healthy DB, `migration_problems` returns `[]` and does not modify the DB (file mtime/row count unchanged). (planted) Given an edited applied file, a missing applied file, or a bad file name, it returns a non-empty list naming the file.
A30. (Edge) Given an empty migrations dir, `open_db` creates `schema_migrations` with zero rows and returns normally.

## Not in this module
- Any business tables beyond `schema_migrations` and `meta` (later stages add migrations).
- Rate-limiting, HTTP, retries (S0-T4); manifest and raw-file storage (S0-T3); check CLI (S0-T5); ledgers (S0-T6).
- Creating zone directories or the repo skeleton (S0-T1).
- Secrets or credential handling.
