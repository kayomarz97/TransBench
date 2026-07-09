#!/usr/bin/env bash
# ask.sh — fully-local TransBench: get a grounded brief + a paste-ready Claude Science prompt.
# No connector, no server, no sandbox, no network exposure. Reads ANTHROPIC_API_KEY from .env.
#
#   bash mcp_server/ask.sh "33F, resistant hypertension on telmisartan + thiazide + CCB; raised CRP"
#
# Then copy the printed "PASTE THIS INTO CLAUDE SCIENCE" block into a Claude Science chat.
set -euo pipefail
REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
export PYTHONDONTWRITEBYTECODE=1
export PYTHONPATH="${REPO_ROOT}/src${PYTHONPATH:+:${PYTHONPATH}}"
PYTHON_BIN="${REPO_ROOT}/.venv/bin/python"
if [[ ! -x "$PYTHON_BIN" ]]; then
    echo "error: venv python not found at $PYTHON_BIN (run 'uv sync' first)" >&2
    exit 1
fi
exec "$PYTHON_BIN" "${REPO_ROOT}/mcp_server/ask.py" "$@"
