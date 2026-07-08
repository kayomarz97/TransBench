# TransBench MCP server (`mcp_server/`)

Exposes the TransBench engine (`transbench.engine.run_transbench`) as an MCP connector with two tools:

- **`generate_experiment(observation, focus_drug="")`** — the showpiece. Runs the full 8-agent pipeline and returns a grounded, cited `TransBrief` (JSON) whose `top_experiment` names a resolvable dataset and includes a `claude_science_prompt`.
- **`search_grounded_evidence(question)`** — utility/fallback. Runs the *same* engine pipeline, reshaped into a lighter grounded-evidence projection (per-hypothesis evidence + references, no `axes`/`top_experiment`/`run_manifest`).

Both tools call the engine directly — there is no duplicated retrieval/grounding logic in this package. See `server.py` for the implementation and `../src/transbench/schemas.py` for the exact `TransBrief` shape.

This is a **standalone repo** — Iatronix (`/root/projects/med-ai-project`) is only ever imported read-only (`transbench.reuse`); nothing here ever writes to it.

## SDK / transports

Verified in this repo's own venv: `mcp==1.28.1`, `mcp.server.fastmcp.FastMCP`. This SDK version supports three transports — `stdio`, `sse`, `streamable-http`:

- **stdio** (default, `run_stdio.sh`) — what Claude Science actually spawns (see below).
- **streamable-http** (`run_http.sh`, binds `127.0.0.1:8500` by default) — the demo-day / standalone HTTP fallback. `sse` is also available (`MCP_TRANSPORT=sse`) if a particular client only speaks the older SSE transport.

Transport is selected at process start via the `MCP_TRANSPORT` env var (`stdio` | `sse` | `streamable-http`); both run scripts set it explicitly.

## Prerequisites

- This repo's venv exists and is fully built (`PLAN.md` Phase 0 — `iatronix-backend` installed editable `--no-deps` + the curated dependency set, including `mcp`).
- `/root/projects/transbench/.env` (or your process env) has a working `ANTHROPIC_API_KEY` (BYOK — feeds the *engine's* own Anthropic calls; see `../.env.example`). `PUBMED_API_KEY` is optional (raises NCBI rate limits).

## Running directly

```bash
# stdio (what Claude Science spawns — you normally don't run this by hand)
bash /root/projects/transbench/mcp_server/run_stdio.sh

# HTTP fallback, localhost:8500
bash /root/projects/transbench/mcp_server/run_http.sh
```

Both scripts `cd` into the repo root themselves (required — `mcp_server` is resolved via `-m mcp_server.server` off the process's *current working directory*, since it is deliberately not part of the installable `transbench` package under `src/`) and set `PYTHONDONTWRITEBYTECODE=1` + `PYTHONPATH=/root/projects/transbench/src` before launching `.venv/bin/python -m mcp_server.server`.

## Registering with Claude Science (primary path)

This is your **primary, required path** for the demo — see `../CLAUDE_SCIENCE_SETUP.md` for the full walkthrough (SSH tunnel from a Windows laptop to a headless Linux server, etc.). This file covers only the connector registration itself.

Add TransBench as a local **stdio** MCP server. **Confirm the exact settings location in your Claude Science build first** — this has moved between beta builds; look under Settings → Connectors, Settings → Developer, or a `claude_science_config.json` / `claude_desktop_config.json`-style file, depending on version (`claude.com/science` has the current instructions).

```json
{
  "mcpServers": {
    "transbench": {
      "command": "/root/projects/transbench/.venv/bin/python",
      "args": ["-m", "mcp_server.server"],
      "cwd": "/root/projects/transbench",
      "env": {
        "PYTHONPATH": "/root/projects/transbench/src",
        "PYTHONDONTWRITEBYTECODE": "1",
        "ANTHROPIC_API_KEY": "sk-ant-YOUR_KEY",
        "PUBMED_API_KEY": "YOUR_NCBI_KEY"
      }
    }
  }
}
```

Notes:
- `command` MUST be **this repo's own venv** python (`/root/projects/transbench/.venv/bin/python`), not a bare `python`/`python3` — that venv is what has `mcp`, `langgraph`, `langchain-anthropic`, and the read-only editable-installed Iatronix backend all resolvable together (`PLAN.md` Phase 0).
- `PYTHONDONTWRITEBYTECODE=1` is required here too (not just in the run scripts) — Claude Science spawns the process itself via this exact `env` block, bypassing `run_stdio.sh` entirely, so this is the guard that actually applies to a Claude Science-launched process. It prevents `.pyc`/`__pycache__` from ever being written into the read-only Iatronix tree via the editable-install import (the baseline-diff guard, `BUILD_SPEC.md` §0.1).
- `PYTHONPATH` is technically redundant for `transbench` itself (it's already editable-installed into this venv's `site-packages`), but is set explicitly anyway per spec/for portability — harmless either way.
- Restart Claude Science after editing its connector config; it should then list `transbench` with its two tools (`generate_experiment`, `search_grounded_evidence`).
- Swap the placeholder keys for real ones via your own `.env`-style secret management — never commit real keys (see the repo root `.gitignore`, which already excludes `.env`).

## Manual fallback (if the live connector misbehaves during a demo)

`../CLAUDE_SCIENCE_SETUP.md`'s own "Fallback demo" section covers this end to end; the short version:

1. Start the HTTP server: `bash /root/projects/transbench/mcp_server/run_http.sh`.
2. Call `generate_experiment` over HTTP (any MCP HTTP client, or a short ad hoc script using the `mcp` Python SDK's `streamablehttp_client`) — or, simplest of all, run the engine directly in a Python shell:
   ```python
   import asyncio
   from transbench.engine import run_transbench
   brief = asyncio.run(run_transbench("<your observation>"))
   print(brief.top_experiment.claude_science_prompt)
   ```
3. **Paste `top_experiment.claude_science_prompt` directly into a Claude Science chat** — same payoff (a reproducible figure from the named dataset), zero dependency on the live connector working. This is the one-keystroke-away fallback `CLAUDE_SCIENCE_SETUP.md` asks you to keep ready.

## Error handling

Every tool call returns either a schema-valid result (a `TransBrief` dict for `generate_experiment`, the lighter projection for `search_grounded_evidence`) or a clean structured error:

```json
{"error": "no_api_key", "message": "No API key configured. ...", "status_code": 402}
```

Never a raw traceback. `server.py` catches the engine's own `TransBenchLLMError`, a bare `fastapi.HTTPException` (defensive — `create_llm` raises this on a missing/invalid key or unsupported model; `agents.build_llm` inside the engine already converts it to `TransBenchLLMError` today, but this boundary catches it too), and any other unexpected exception. Diagnostics go to stderr via the stdlib `logging` module only — this process never writes to stdout outside the MCP protocol itself (stdout is the JSON-RPC channel for the `stdio` transport).

## Cost note

`generate_experiment` and `search_grounded_evidence` both run the **full** pipeline (~13 LLM calls + live PubMed +, when a candidate is selected, a live GEO content-verification fetch — `BUILD_SPEC.md` §9) — `search_grounded_evidence` is not a cheaper retrieval-only path, only a lighter *return shape*. Budget accordingly for repeated demo calls.

## Files

| File | Purpose |
|---|---|
| `server.py` | `FastMCP("transbench")` app; both tools; error handling |
| `run_stdio.sh` | Launches over stdio (what Claude Science spawns) |
| `run_http.sh` | Launches over HTTP (`streamable-http`, `localhost:8500`) |
| `manifest.json` | Descriptive connector manifest (not a normative MCP file — the live tool schemas are served by `server.py` itself at runtime) |
| `requirements.txt` | Pins this package's own direct `mcp` SDK dependency |
