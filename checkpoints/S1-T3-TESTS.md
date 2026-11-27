# Checkpoint S1-T3 - Test writer
## State
- branch task/S1-T3 (base main 284fa68). 88 new tests in tests/unit/routine/ (conftest, _routine_helpers, test_roots_core/safety/ops). All red (21 failed, 67 fixture errors: investos.routine.roots absent). Red output: reports/S1-T3/tests_red.txt. Old suite 1163 pass, 3 skip.
- Harness validated against a throwaway stub Git (not committed): real git, bare origin, fixtures work.
## Next step
- Builder: src/investos/routine/{__init__,roots}.py, cli wiring, ops/launchd plist example.
## Map
- core: A1,A2,A3,A4,A15,A16,A20(access files) | safety: A5-A8,A10,A11 | ops: A9,A12,A13,A14(SIGKILL subprocess),A17,A18,A19,A20(plist)
- Every test's git commands are checked at teardown against an allow-list (add -- <one path>, push <remote> <branch>, restore --staged --, read-only subcommands; no force/+/-A/-a/--no-verify).
## Assumptions / open questions
1. `Git` lives in investos.routine.roots, constructor Git(cwd), run(args, check=False) (args may include or omit a leading "git"; tests strip it). Returns CompletedProcess with text stdout/stderr.
2. R11 forbids `replace`/open-write but R14 needs temp+os.replace; scan allows os.replace, forbids open(..., 'w'/'a'/'x') (use tempfile/os.open).
3. Sidecar variant names assumed `<start>_<end>.json.attempt-N` / `.corrupt-N`.
4. CLI: tests call investos.cli.main(["routine","roots",...]) with cwd=repo; monkeypatch run_routine in investos.routine.roots (and investos.cli.run_routine if imported there); notifier kwarg must be OsascriptNotifier only when sys.platform=="darwin". Bad flag -> return/SystemExit 1.
5. Log `done` carries the exit code as an int field value (any name). push_failed fields hold no URL/path.
6. Torn-tail refuse message contains the file name and "manifest-torn-tail".
7. Unwritable last.json simulated by a directory at that path (R10 lock lives in the same .state dir).
