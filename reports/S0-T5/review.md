# Review S0-T5 (check runner + planted harness) - Reviewer, 21 Nov 2026

**Verdict: PASS WITH MINOR**

Suite: `python -m pytest tests -q` -> 243 passed, 1 skipped (reports/S0-T5/review_pytest.txt).
Hard rules: tests untouched by Builder (`git log main..task/S0-T5 -- tests` shows only Invest-OS-Test-writer); src imports stdlib + duckdb (via investos.core.db, investos.acquire.manifest), no network code.

## Defects
| id | sev | file:line | rule | evidence | fix |
|---|---|---|---|---|---|
| D1 | minor | src/investos/check/cli.py (empty-registry branch, ~L70) | R5 vs R4 | `main([], registry=CheckRegistry())` returns 2 with "no checks registered" and writes no report (probe P9). R5 says every invocation past arg parsing writes one; R4 says exit 2. Spec is internally ambiguous; no test pins either. | Decide in spec; simplest: keep as is and amend R5 ("except the empty-registry case"). |
| D2 | minor | src/investos/check/runner.py `_digest` (roots exclude `reports`) | R8 | A plant writing by absolute path into the real `reports` zone is not detected (probe P3b: result `caught`, real reports/evil.txt exists). Spec R8 does say reports is the only real path written, but only by write_report. | Digest the reports zone too, ignoring only `checks/<run_id>.json`, or accept and document. |
| D3 | minor | runner.py `_temp_copy` | R7 cost | Every planted check copies all zones in full (raw, ledger, reports with old reports) and `shutil.copytree` follows symlinks, so a 50 GB raw zone is copied three times (manifest, raw, migrations, registry). Spec mandates this copy, so it is spec-compliant; flagged as scaling risk. | Later stage: copy only what each check needs, or hardlink/reflink; no change needed for S0. |
| D4 | nit | runner.py `_digest` | R8 | mtime not in digest (spec lists name/size/mode/sha256 so compliant); mtime-only change on real data not detected (P3c). | none required |
| D5 | nit | runner.py `_make_writable` | R7 | Does not add u+r on directories, so a 0000 dir in a zone would break copy/cleanup (could not test: sandbox runs as root, P8 not meaningful). | add S_IRUSR for dirs |

No blocking defects.

## Safety property (--planted never modifies real data)
Probed on a tmp repo (copy of tests helpers), none broke it:
- P1 symlink to external file inside canonical zone, plant writes through it: external file unchanged (copytree symlinks=False materialises a copy).
- P2 symlink to external directory: external content unchanged.
- P3 plant writes real file by absolute path: all results `error: real data modified`.
- P10 plant chmods a real 0444 raw file by absolute path: detected (`real data modified`); note the harness does not restore the damage (spec does not ask).
- P4 canonical zone nested inside raw zone: works, real data untouched.
- P5 tmp_parent inside a zone: `error: cannot create temp copy` (recursive copy refused), no leftovers.
- P7 symlink loop inside raw: `error: cannot create temp copy`, no crash, temp removed.
- Only gaps are D2 (reports zone) and D4 (mtime), both by design of the digest.

## Mutation probes (on /tmp copy, `pytest tests/unit/check -x`)
KILLED = at least one test failed.
| # | mutation | result |
|---|---|---|
| M1 | harness runs/accepts check without plant | killed |
| M2 | baseline not required to pass | killed |
| M3 | no-op plant counted as caught | killed |
| M4 | plant applied on real cfg instead of temp | killed |
| M5 | real-data digest check skipped | killed |
| M6 | exit code always 0 | killed |
| M7 | report not written | killed |
| M8 | non-canonical report JSON | killed |
| M9 | `bad_line` dropped from manifest kinds (torn tail) | killed |
| M10 | error counted as pass | killed |
| M11 | details cap removed | killed |
| M12 | `error: ` prefix removed after plant | killed |
| M13 | temp dir not cleaned | killed |
| M14 | copytree `symlinks=True` (symlinks copied as links, escape route) | **SURVIVED** |
| M15 | `misplaced_file` dropped from raw kinds | killed |
| M16 | unknown target exit 2 -> 1 | killed |
| M17 | reserved name `all` allowed | killed |
16/17 killed. M14 survives: no test places a symlink in a zone (the key safety edge). Recommend a regression test (see untested edges); not blocking because the code is currently correct.

## Spec rule coverage
R1-R15 each implemented; R3-R9 exercised by tests (mutations above). Table/exit codes/report schema match spec; report path is `<cfg.zones["reports"]>/checks/<run_id>.json`, and the example config sets reports = "reports", so default is reports/checks/<run_id>.json as the brief says (the "under data tree" worry is a non-issue; deviation judged against spec = compliant). `registry` target with absent S0-T6 -> exit 2 via load_builtin_checks (OK).

## Untested edges
- Symlinks (file/dir/loop) inside zones with --planted (M14).
- Plant writing to the real reports zone (D2).
- Unreadable (0000) directories in zones (needs non-root).
- tmp_parent inside a zone.
- Empty-registry report behaviour (D1).
- Large-zone copy cost (D3).
