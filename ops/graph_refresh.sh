#!/bin/bash
# Rebuild the code graph of main (AST only, no LLM) into /home/claude/repo_graph/graphify-out
set -e
# Installs Graphify (PyPI graphifyy, MIT) into /home/claude/tools/gfy if missing. Workspace tool, not a runtime dependency.
[ -x /home/claude/tools/gfy/bin/graphify ] || { python -m venv /home/claude/tools/gfy && /home/claude/tools/gfy/bin/pip install -q graphifyy; }
rm -rf /home/claude/repo_graph && git clone -q --branch main /home/claude/invester_os /home/claude/repo_graph
cd /home/claude/repo_graph && rm -rf .git
GRAPHIFY_FORCE=1 /home/claude/tools/gfy/bin/graphify update . >/tmp/gfy.log 2>&1 || { tail -20 /tmp/gfy.log; exit 1; }
ls -la graphify-out/GRAPH_REPORT.md
