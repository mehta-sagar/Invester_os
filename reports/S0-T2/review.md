# Review S0-T2 (config / log / db) - Reviewer, fresh context

**Verdict: PASS WITH MINOR**

`python -m pytest tests -q` -> 62 passed (saved in `reports/S0-T2/review_pytest.txt`).
Hard rules: no write to `data/` in the diff (no `data/` paths in src); no network imports; stdlib + duckdb only (`pyproject` dependencies = `["duckdb"]`); `git log --format='%an %s' main..task/S0-T2 -- tests` shows only `Invest-OS-Test-writer S0-T2: failing tests ...` (Builder did not touch tests).
R1-R19 all implemented; each has at least one test that went red under mutation (see below).

## Defects

| id | severity | file:line | rule | evidence | suggested fix |
|---|---|---|---|---|---|
| D1 | minor | pyproject.toml (whole file), src/investos/__init__.py | brief "Files in scope" | Brief scope lists only core/*, example toml, migrations. Diff adds pyproject.toml and src/investos/__init__.py (absent on main). `[project.scripts] investos = "investos.cli:main"` points at a module that does not exist until S0-T5. | Orchestrator to acknowledge scope extension (needed to run tests); consider dropping the script entry until S0-T5. |
| D2 | minor | src/investos/core/log.py `_safe` / `_emit` (json.dumps, default allow_nan) | R12 "one line, JSON" | float NaN/Inf in a field serialises as bare `NaN`/`Infinity`, which is not valid JSON for strict parsers. Not in spec, no test. | `allow_nan=False` plus repr fallback for non-finite floats. |
| D3 | minor | src/investos/core/log.py `_emit` (`except Exception: pass` on flush) | safety | A failed flush is swallowed silently. `write` is not guarded. Inconsistent and hides sink failure. | Drop the try/except or let it propagate (spec only exempts unserialisable values). |
| D4 | minor | src/investos/core/config.py `_git_check_ignored` (`env["PATH"] if "PATH" in env else os.environ...`) and `subprocess.run` without `env=` | R6 | If injected `env` has no PATH the code silently falls back to the process PATH; git itself runs with the real process environment, not the injected one. Could mask "git unrunnable". | Use `env.get("PATH", "")` and pass `env=dict(env)` to subprocess (or document). |
| D5 | minor | src/investos/core/config.py `load_config` (`env.get("INVESTOS_ROOT")` / `INVESTOS_CONFIG` truthiness) | R1 | Empty-string `INVESTOS_ROOT=""` or `INVESTOS_CONFIG=""` is silently treated as unset. Explicit `repo_root` is not checked to be a directory. | Treat empty as ConfigError, or at least log; validate `root.is_dir()`. |
| D6 | minor | src/investos/core/db.py `open_db` | spec edge "database locked" | Lock path (`"lock" in str(e).lower()` string match) is implemented but has no test; matching on message text is brittle across duckdb versions. | Add a two-connection test; match on duckdb exception type/message more defensively. |

No critical or major defects.

## Mutation probes (scratch copy in /tmp/mut, repo untouched)

| # | Mutation | Rule | Result |
|---|---|---|---|
| M1 | skip sha256 comparison in audit | R16 | RED (test_A26 nopending-byte) |
| M2 | allow back-fill (ordering check off) | R17 | RED (test_A27) |
| M3 | skip git check-ignore when `.git` exists | R6 | RED (test_A11) |
| M4 | typo guard disabled | R3 | RED (test_A6) |
| M5 | reserved field names also written top-level | R9 | RED (test_A15) |
| M7 | bool accepted as number | R2 | RED (test_A3 interval_bool) |
| M8 | nesting check removed | R4 | RED (test_A8) |
| M10 | ensure_ascii=True | spec edge / A20 | RED (test_A20) |
| M11 | timezone check off | R8 | RED (test_A13) |
| M12 | host lookup not lowercased | R7 | RED (test_A12d) |
| M13 | load_config creates zone dirs | R5 | RED (test_A9) |
| M14 | pending applied despite tamper | R16 | RED (test_A26) |
| M15 | no BEGIN (autocommit migrations) | R15 | RED (test_A21/A25) |
| M16 | level filter off | R12 | RED (test_A18) |
| M17 | new run_id every call | R10 | RED (test_A16) |
| M18b | git-not-runnable returns OK | R6 | RED (test_A11b) |
| M19b | relative paths resolve vs cwd | R4 | RED (14 tests) |
| M20 | hash over stripped text | R15/R16 | RED (test_A21) |
| M6 | ROLLBACK replaced by COMMIT | R15 | green - equivalent mutant: duckdb aborts the failed transaction, COMMIT rolls back anyway |
| M9 | read-only existence check removed | R13 | green - equivalent: duckdb itself refuses to open a missing file read-only and the code wraps it into MigrationError (message differs only) |
| M21 | exit 1 no longer raises | R6 | green - equivalent: falls through to the generic non-zero raise |

18 of 21 probes red; the 3 green ones are equivalent mutants, not test gaps (M15 confirms R15 atomicity is covered).

## Untested edge cases
- `INVESTOS_PATHS__RAW` set to a **relative** path resolving against repo_root (spec edge; tests only use absolute env paths).
- `INVESTOS_ROOT` env alone as root source (only appears mixed into another case, test_config.py:137).
- Concurrent `open_db` lock -> `MigrationError("database locked")` (D6).
- `get_logger` before `configure_logging` (stderr, INFO, process run_id).
- Reserved field named `event` (A15 covers ts/level/task/run_id only; works because `event` is positional-only).
- Dangling-symlink zone and unreadable zone (file-as-zone is tested, A9b).
- Migration SQL that itself contains BEGIN/COMMIT, or non-UTF-8 bytes.
- `INVESTOS_RATE_LIMITS__HOSTS__*` env (should be rejected as unknown; works by code reading, no test).
- `rate_limits.hosts` given as non-table; `rate_limits` section absent (R7 "section required").

## Safety notes
No path traversal concern (paths come from trusted config, resolved, nesting-checked). No silent fallbacks other than D4/D5. Exceptions in migrations are re-raised as MigrationError with cause; `open_db` closes the connection on failure. Only swallowed exception: D3.
