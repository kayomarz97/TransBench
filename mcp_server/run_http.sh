#!/usr/bin/env bash
# run_http.sh — launches the TransBench MCP server over HTTP (streamable-http
# transport), bound to localhost:8500 by default.
#
# This is the demo-day safety net, not the primary path (CLAUDE_SCIENCE_SETUP.md:
# "only a demo-day safety net in case the beta app misbehaves live; it does
# not replace the connector"). Use it to smoke-test the server standalone, or
# as the manual/HTTP fallback if the Claude Science stdio connector is
# misbehaving live.
#
# Override host/port via MCP_HTTP_HOST / MCP_HTTP_PORT if 8500 is taken.
set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$REPO_ROOT"

export PYTHONDONTWRITEBYTECODE=1
export PYTHONPATH="${REPO_ROOT}/src${PYTHONPATH:+:${PYTHONPATH}}"
export MCP_TRANSPORT="streamable-http"
export MCP_HTTP_HOST="${MCP_HTTP_HOST:-127.0.0.1}"
export MCP_HTTP_PORT="${MCP_HTTP_PORT:-8500}"

PYTHON_BIN="${REPO_ROOT}/.venv/bin/python"
if [[ ! -x "$PYTHON_BIN" ]]; then
    echo "error: venv python not found at $PYTHON_BIN (create it per PLAN.md Phase 0 first)" >&2
    exit 1
fi

echo "TransBench MCP server (HTTP/streamable-http) starting on http://${MCP_HTTP_HOST}:${MCP_HTTP_PORT}${MCP_STREAMABLE_HTTP_PATH:-/mcp}" >&2

exec "$PYTHON_BIN" -m mcp_server.server
