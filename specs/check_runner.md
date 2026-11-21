# Spec check_runner   (from brief S0-T5)

Modules: `src/investos/check/__init__.py`, `registry.py` (decorators + `CheckRegistry`), `runner.py` (execution + planted harness), `builtin.py` (checks `manifest`, `raw`, `migrations`), `cli.py` (`main`), `__main__.py` (`python -m investos.check`). Python 3.11+, stdlib + `duckdb` (via `investos.core.db`).

Dependency note: the framework (registry, runner, harness, CLI, report) depends only on S0-T2. The built-in checks `manifest` and `raw` call `ManifestStore.verify` (S0-T3); the `registry` check is defined and planted in S0-T6 (`src/investos/registry/check.py`) and registered into the same default registry. Framework tests use private `CheckRegistry` instances with fake checks; built-in check tests run once S0-T3 is merged.

## Inputs (schema)

```python
@dataclass(frozen=True)
class CheckContext:
    cfg: Config                 # zones/paths the check must use (never module-level globals)
    run_id: str

@dataclass(frozen=True)
class CheckResult:
    ok: bool
    message: str                # one line, <= 200 chars
    details: tuple[str, ...] = ()   # extra lines, e.g. one per problem (capped at 50 by the runner)

CheckFn = Callable[[CheckContext], CheckResult]
SetupFn = Callable[[CheckContext], None]     # builds a healthy fixture inside the temp copy
PlantFn = Callable[[CheckContext], None]     # breaks that fixture inside the temp copy

class CheckRegistry:
    def check(self, name: str) -> Callable[[CheckFn], CheckFn]            # decorator
    def plant_for(self, name: str, *, setup: SetupFn | None = None) -> Callable[[PlantFn], PlantFn]   # decorator
    def names(self) -> list[str]                                          # registration order
    def get(self, name: str) -> CheckFn
    def plant_of(self, name: str) -> tuple[SetupFn | None, PlantFn] | None
DEFAULT_REGISTRY: CheckRegistry
check = DEFAULT_REGISTRY.check            # module-level @check(name)
plant_for = DEFAULT_REGISTRY.plant_for    # module-level @plant_for(name, setup=...)
class RegistryError(Exception): ...

def run_checks(registry: CheckRegistry, ctx: CheckContext, names: Sequence[str] | None = None, *,
               clock: Callable[[], datetime] | None = None, timer: Callable[[], float] | None = None) -> Report
def run_planted(registry: CheckRegistry, cfg: Config, names: Sequence[str] | None = None, *,
                clock=None, timer=None, tmp_parent: Path | None = None) -> Report
def write_report(report: Report, cfg: Config) -> Path        # reports/checks/<run_id>.json
def main(argv: Sequence[str] | None = None, *, registry: CheckRegistry | None = None,
         cfg: Config | None = None, stdout: TextIO | None = None) -> int
```
`@check` applies to plain functions `def f(ctx: CheckContext) -> CheckResult`. Check names match `^[a-z][a-z0-9_]{0,31}$`, are unique, and `all` is reserved.

### CLI
`investos check [TARGET] [--planted]` where `TARGET` is `all` (default) or one registered check name (built-in names: `manifest`, `raw`, `registry`, `migrations`). `main` returns the process exit code; `__main__` calls `sys.exit(main())`. `cfg=None` means `load_config()`.

## Outputs (schema)

### Exit codes
`0` all selected checks passed (or, with `--planted`, all were caught); `1` at least one check failed/errored (or a plant was missed, refused, or had a failing baseline); `2` usage or config error (unknown target, `ConfigError`, report cannot be written).

### Table (stdout)
Header `NAME  STATUS  MS  MESSAGE`, one row per selected check in registration order, columns space-padded, then a final line `OK` or `FAILED (n)`. Statuses in plain mode: `pass`, `fail`, `error`. Statuses in planted mode: `caught`, `missed`, `baseline_failed`, `refused`, `error`. In plain mode a check with no plant also shows `plant: MISSING` appended to its message (no effect on its status or the exit code).

### Report: `<cfg.zones["reports"]>/checks/<run_id>.json`
Written atomically (temp + `os.replace`), created even when checks fail; directory `checks/` is created if missing (the `reports` zone itself must exist). Canonical JSON rule `json.dumps(obj, sort_keys=True, separators=(",", ":"), ensure_ascii=False)` + `"\n"`. No floats.
```json
{"schema":1,"run_id":"...","mode":"check","started_at":"2026-11-21T09:30:00.000000Z","finished_at":"...",
 "selected":["manifest","raw"],"ok":false,
 "results":[{"name":"manifest","status":"fail","message":"1 problem","details":["hash_mismatch data/raw/..."],"duration_ms":12,"plant":"registered"}]}
```
`mode` is `check` or `planted`; `duration_ms` is an int; `plant` is `registered` or `missing`; in planted mode extra field `phase` (`baseline|plant|none`) says where the result was decided. `ok` is true iff every result passed (plain) or was `caught` (planted).

## Rules (numbered — each gets ≥1 test and ≥1 planted failure)

R1. Registration: `@check(name)` registers a plain function; a duplicate name, an invalid name, or the name `all` raises `RegistryError` at decoration time. `@plant_for(name, setup=...)` registers the plant for an existing check name; a plant for an unknown check, or a second plant for the same check, raises `RegistryError`. A check may be registered without a plant (R6 handles that).
R2. Selection: no target or `all` selects every registered check in registration order; a target selects that one check; a name not registered, or a second positional argument, is a usage error (exit 2, table not printed, message on stdout naming the registered names).
R3. Execution and results: each selected check is called once with a `CheckContext`. Returned `ok=True` → `pass`; `ok=False` → `fail`; an exception, or a return value that is not a `CheckResult`, → `error` (message `"<ExceptionType>: <text>"` truncated to 200 chars). An `error` counts as a failure. One check raising never prevents the remaining checks from running.
R4. Exit status: the process result is 0 only if every selected check passed; any `fail` or `error` gives 1. A selection that is empty (registry has no checks) is exit 2 with message "no checks registered".
R5. Report: every invocation that gets past argument parsing (except an empty registry, which exits 2 as a usage error with no report — amended 21 Nov after review S0-T5; and the real-data digest in --planted covers the reports zone except this run's own report file) writes exactly one JSON report at `reports/checks/<run_id>.json` with the schema above, including on failures. `run_id` comes from `investos.core.log.get_run_id()` (one per process, so two invocations in one process overwrite the same file — tests use distinct injected run ids via `CheckContext`/`main` fixtures). `details` is capped at 50 lines (a final line `... and N more` replaces the rest).
R6. Plant requirement: in `--planted` mode a selected check with no registered plant is not executed; its result is `refused` (message `"no plant registered"`), and the overall result is failure (exit 1). A check whose plant is registered is run normally by the harness. In plain mode a missing plant never changes the exit code but is shown (`plant: MISSING`) and recorded (`"plant":"missing"`).
R7. Harness isolation: for each selected check with a plant the harness (a) creates a new temp directory under `tmp_parent` (default system temp), (b) copies every zone directory of the real `cfg` (raw, manifests, canonical, ledger, reports, registry) and `migrations_dir` into it with `shutil.copytree(..., symlinks=False)`, (c) builds a new `Config` (`dataclasses.replace`) whose paths point into the temp copy, (d) runs `setup(ctx_tmp)` if given, (e) runs the check on the temp copy ("baseline") — it must return `ok=True`, else the result is `baseline_failed` and the plant is not applied, (f) runs `plant(ctx_tmp)`, (g) runs the check again — it must return `ok=False` (or raise, which is also "caught" only if the exception is not from the check being unable to start: an `error` after the plant is accepted as `caught` with message prefixed `error: `), else `missed`. A setup/plant function that raises → result `error`. The temp directory is removed afterwards, making files writable first (`chmod u+w` walk) because raw files are 0444.
R8. The real data is never touched by the harness: the real zones' content (file names, sizes, modes, sha256 of every file) before and after `run_planted` is identical, even when a plant or check raises. The `reports` zone is the only real path written, and only by `write_report`. Plants and checks must receive and use the temp `cfg` only.
R9. Planted-mode report: `mode="planted"`, results use the planted statuses, `ok` true iff all selected results are `caught`; every result records `phase`: `none` for `refused`, `baseline` for `baseline_failed`, `plant` for `caught`/`missed`/`error` after the plant.
R10. Check independence: checks use only `ctx.cfg` paths (a check that reads a path outside `cfg` is a defect found by review; the harness's temp-copy isolation makes this visible because the plant would then be missed). Checks must be read-only: a test runs every built-in check on a healthy fixture and asserts no file in the fixture changed (names, sizes, mtimes, modes).
R11. Built-in `manifest`: for every `<manifests>/*.jsonl` source (and every directory `<raw>/<source>`), call `ManifestStore.verify(source)` and fail on problems of kind `bad_line`, `missing_file`, `hash_mismatch`, `size_mismatch`; pass with message `"N sources, M lines OK"` otherwise. An empty manifests zone passes.
R12. Built-in `raw`: fail on problems of kind `writable_file` and `misplaced_file` from `ManifestStore.verify` over all sources. Passes when none.
R13. Built-in `migrations`: opens the DB read-only (`open_db(cfg, read_only=True)`) and returns the failure list from `migration_problems`. Missing DB file: fail with `"database missing"` if `migrations_dir` contains any `.sql` file, else pass.
R14. Built-in plants (one each, with `setup` building a healthy fixture in the temp copy and `plant` breaking it): `manifest` = store two files then flip one byte of one raw file (after `chmod u+w`); `raw` = store a file then `chmod 0o644` it; `migrations` = open/apply migrations to the DB, then append a comment byte to an applied migration file in the temp copy of `migrations_dir`. The `registry` plant is owned by S0-T6 (break a payload in a ledger copy).
R15. `load_builtin_checks()` imports `investos.check.builtin` and, if importable, `investos.registry.check`, so that `main` with the default registry sees `manifest`, `raw`, `migrations` and (once S0-T6 exists) `registry`. A missing `investos.registry.check` module is tolerated only while S0-T6 is unmerged: then target `registry` is a usage error (exit 2).

## Edge cases
- A check that takes long: no timeout in Stage 0 (documented, not enforced).
- Check returns a `CheckResult` with `message` containing a newline: newlines are replaced by spaces in the table and the report message.
- Two checks with the same plant function object: allowed.
- Plant mutates through an absolute path to the real data (defect): detected by R8 (before/after digest differ) and the harness reports every result as `error` with message `real data modified`, exit 1.
- Temp directory cleanup fails: ignored, a WARNING is logged, result unaffected.
- `--planted` with target `registry` when S0-T6 is absent: exit 2.
- `reports` zone read-only: exit 2 with the OS error text; the table is still printed.
- Unicode in messages: written as UTF-8.
- Running `main` twice in one process with the same run id: second report overwrites the first (atomic replace).

## Acceptance tests (in words)
Tests use a tmp repo (config + zones + migrations from S0-T2 fixtures) and private `CheckRegistry` instances unless stated.

A1. (R1, planted) Given a registry, when a check is registered twice, or named `All`, `all`, `1bad`, or 40 characters long, then `RegistryError`. A plant for an unregistered check raises `RegistryError`; a second plant for one check raises `RegistryError`.
A2. (R2) Given checks `a` and `b`, `main([])` (no target) runs both in order; target `b` runs only `b`; target `zzz` returns 2 with a message listing `a, b`; two positional targets return 2.
A3. (R3, planted) Given a check that raises `ValueError("boom")` and another that returns `True` (not a `CheckResult`), then each has status `error`, message begins with `ValueError: boom` / names the bad return type, and a third passing check still runs and reports `pass`; exit 1.
A4. (R3, R4) Given all checks pass, then exit 0 and the table's last line is `OK`; given one `fail`, exit 1 and the last line `FAILED (1)`; given an empty registry, exit 2.
A5. (R5) Given a run with one pass and one fail, then `<reports>/checks/<run_id>.json` exists, parses, has `schema == 1`, `mode == "check"`, `ok == false`, results in registration order with `duration_ms` ints, and file text equals the canonical dump plus newline.
A6. (R5, planted) Given a check returning 80 detail lines, then the report holds exactly 50 detail lines: the first 49 originals and a last line `... and 31 more`. Given the `reports` zone directory removed, `main` returns 2.
A7. (R6, planted) Given registry with check `x` (no plant), when `--planted` runs, then `x` is `refused`, its function was never called (call counter 0), exit 1, report `phase == "none"`; given plain mode, `x` runs, shows `plant: MISSING`, exit 0 if it passes.
A8. (R7, planted) Given check `c` that passes iff file `canonical/flag.txt` does not contain `BAD`, with `setup` writing `OK` and `plant` writing `BAD`, then result `caught`. Given a plant that does nothing, then `missed` and exit 1. Given a check that always returns `fail`, then `baseline_failed`, and the plant was not called. Given a check that always passes, `missed`.
A9. (R7) Given a check that raises after the plant, then `caught` with message prefixed `error: `; given `setup` raising, then `error`.
A10. (R7) Given raw files with mode 0444 in the real data, then the temp copy is removed completely after the harness (directory no longer exists) and no `PermissionError` escapes.
A11. (R8, planted) Given real zones holding files (including 0444 raw files) and a plant that edits the temp copy, then the sha256/mode/size digest of the real zones is identical before and after. Given a (deliberately defective) plant that writes to the real `canonical/flag.txt` by absolute path, then every result is `error` with `real data modified` and exit 1.
A12. (R9) Given a planted run, then the report has `mode == "planted"`, each result has `phase`, and `ok` is true only if all are `caught`.
A13. (R10) Given a healthy fixture with stored manifest data and an applied DB, when the built-in `manifest`, `raw`, `migrations` checks run, then none of the fixture files changes (names, sizes, mtimes, modes) and all pass.
A14. (R11, planted) Given a healthy store, when one raw file's bytes are altered, then `manifest` fails with a message naming `hash_mismatch` and the path. Given a truncated manifest line, then `manifest` fails with `bad_line`. Given a deleted raw file, fails with `missing_file`. Given an empty manifests zone, passes.
A15. (R12, planted) Given a healthy store, when a raw file is chmod 0644, then `raw` fails naming `writable_file` and `manifest` still passes; given a stray file `raw/bse/ab/not_a_hash`, `raw` fails with `misplaced_file`.
A16. (R13, planted) Given a migrated DB, when an applied migration file is edited, then `migrations` fails naming the file; given a migrations dir with `.sql` files but no DB file, fails `database missing`; given no `.sql` files and no DB, passes.
A17. (R14, Exit-gate support) Given the default registry with built-ins loaded, when `main(["--planted"])` runs on a healthy real fixture, then `manifest`, `raw`, `migrations` are all `caught`, exit 0, and a deliberately broken built-in (plant monkeypatched to no-op) yields `missed` and exit 1.
A18. (R15) Given the default registry, then `names()` contains `manifest`, `raw`, `migrations` in that order; after S0-T6 is merged also `registry` (that assertion lives in S0-T6's tests); with `investos.registry.check` absent, `main(["registry"])` returns 2.
A19. (Edge) Given a message with a newline, then table and report show it with a space; given `reports` read-only (mode 0555 and not root), `main` returns 2 and still printed the table.
A20. (R2, R5) Given `python -m investos.check manifest` run as a subprocess with `INVESTOS_CONFIG` pointing at a tmp config, then the process exit code equals `main`'s and the report file exists.

## Not in this module
- The ledger chain verification itself and the `registry` check function and plant (S0-T6).
- Scheduling checks daily, alert delivery, canary and repair triggers (later stages).
- Check timeouts, parallel execution, check dependencies.
- Packaging the `investos` console script entry (`pyproject.toml`, S0-T1).
