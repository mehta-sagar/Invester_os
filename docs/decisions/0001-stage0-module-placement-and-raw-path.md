# Decision 0001 — stage 0 module placement and raw path rule (21 Nov 2026, Orchestrator)

## Module placement (skeleton folder names win over spec file names)
| Spec | Module path |
|---|---|
| specs/core_config_log_db.md | src/investos/core/{config,log,db}.py (new package `core`) |
| specs/core_manifest.md | src/investos/acquire/manifest.py (README rule 1: acquire/ only fetches and manifests) |
| specs/collect_base.md | src/investos/acquire/base.py (spec says `investos.collect.base` → read as `investos.acquire.base`) |
| specs/check_runner.md | src/investos/audit/check.py + CLI `investos check` (entry point in src/investos/cli.py) |
| specs/registry_chain.md | src/investos/registry/chain.py (new package `registry`) |
Tests live under tests/<area>/ (existing tests/invariants, tests/regression; new tests/unit/<module>/).

## Raw path rule
The 16 Nov README shows a readable path `data/raw/<source>/<dataset>/<yyyy>/<yyyy-mm-dd>[__disc].<ext>`; the approved final algorithm (20 Nov) says "write the bytes under sha256". The final algorithm wins: raw files are content-addressed `data/raw/<source>/<sha[:2]>/<sha>`; the readable key (date etc.) is `logical_key` in the manifest. README updated to match.

## Merges
The chat's GitHub access is git-only (no API), so PRs cannot be opened from here. Each task still lands on its own branch `task/<id>`, pushed to origin; the Orchestrator merges with --ff-only into main after tests + review + sample run. Branch protection on main is not set: it would block these merges, and GitHub requires a paid plan for protection rules on private repos. Revisit when GitHub API access exists.
