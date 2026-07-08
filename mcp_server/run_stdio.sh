#!/usr/bin/env bash
# run_stdio.sh — launches the TransBench MCP server over stdio.
#
# This is the transport Claude Science actually uses: it spawns this exact
# command itself (see the "mcpServers" block in README.md /
# CLAUDE_SCIENCE_SETUP.md) rather than you running this script by hand for a
# real Claude Science session. Run it manually only for local smoke-testing
# (e.g. piped into a small MCP client, or just to confirm the process starts
# cleanly and stays up waiting for JSON-RPC on stdin/stdout).
#
# BUILD_SPEC.md §7 / KICKOFF.md Phase 6: "default mcp.run(transport='stdio')".
set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"

# `python -m mcp_server.server` resolves the `mcp_server` package via the
# CURRENT WORKING DIRECTORY (mcp_server/ lives at the repo root, sibling to
# src/ — it is deliberately NOT part of the installable `transbench`
# distribution, see mcp_server/__init__.py) — so cwd MUST be the repo root
# regardless of where this script is invoked from.
cd "$REPO_ROOT"

# PYTHONDONTWRITEBYTECODE=1: required so importing the read-only, editable
# -installed Iatronix backend (via transbench.reuse) never writes .pyc/
# __pycache__ into the Iatronix tree (BUILD_SPEC.md §0.1 baseline-diff
# guard). PYTHONPATH=<repo>/src: explicit per KICKOFF.md Phase 6 (harmless
# even though `transbench` is already editable-installed into this repo's
# own .venv — this makes the dependency obvious/portable regardless of how
# the venv was built).
export PYTHONDONTWRITEBYTECODE=1
export PYTHONPATH="${REPO_ROOT}/src${PYTHONPATH:+:${PYTHONPATH}}"
export MCP_TRANSPORT="stdio"

PYTHON_BIN="${REPO_ROOT}/.venv/bin/python"
if [[ ! -x "$PYTHON_BIN" ]]; then
    echo "error: venv python not found at $PYTHON_BIN (create it per PLAN.md Phase 0 first)" >&2
    exit 1
fi

exec "$PYTHON_BIN" -m mcp_server.server
