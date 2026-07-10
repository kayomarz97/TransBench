# TransBench MCP server (`mcp_server/`)

Exposes the TransBench engine (`transbench.engine.run_transbench`) as an MCP connector with two tools:

- **`generate_experiment(observation, focus_drug="")`** — the showpiece. Runs the full 8-agent pipeline on **any** clinical/biomedical observation (a disease's drug response/resistance, a drug's adverse effect/toxicity, or any mechanism) and returns a grounded, cited `TransBrief` (JSON) whose `top_experiment` names a resolvable dataset and includes a `claude_science_prompt`.
- **`search_grounded_evidence(question)`** — utility/fallback. Runs the *same* engine pipeline, reshaped into a lighter grounded-evidence projection (per-hypothesis evidence + references, no `axes`/`top_experiment`/`run_manifest`).

Both tools call the engine directly — no duplicated retrieval/grounding logic here. See `server.py` and `../src/transbench/schemas.py` for the exact `TransBrief` shape.

## SDK / transports

`mcp==1.28.1`, `mcp.server.fastmcp.FastMCP`. Two transports matter:

- **streamable-http** (`run_http.sh`, binds `127.0.0.1:8500`, path `/mcp`) — **the transport Claude Science connects to**, as a *remote/URL* connector via an https tunnel (see below). `sse` is also available (`MCP_TRANSPORT=sse`).
- **stdio** (`run_stdio.sh`) — for direct/embedded MCP clients. **stdio does NOT work as a Claude Science connector:** CS runs local (command/stdio) connectors inside a sandbox whose egress proxy returns `403` for `api.anthropic.com`, so the engine's own LLM calls die (`402/403`). That's why the connector path is the URL one.

Transport is selected via the `MCP_TRANSPORT` env var (`stdio` | `sse` | `streamable-http`); the run scripts set it explicitly.

## Prerequisites

- This repo's venv is built (`uv sync`), or self-contained via `src/vendored/` (see the root README).
- `/root/projects/transbench/.env` has a working `ANTHROPIC_API_KEY` (BYOK — feeds the *engine's* own Anthropic calls). `PUBMED_API_KEY` is optional (raises NCBI rate limits).

## Running

```bash
# HTTP — what Claude Science connects to (via a tunnel). Serves 127.0.0.1:8500/mcp.
bash mcp_server/run_http.sh

# Fully local, no connector/server: grounded brief + a paste-ready Claude Science prompt.
bash mcp_server/ask.sh "30M on amiodarone for AF, developed neutropenia"

# stdio — direct MCP clients only (NOT for Claude Science).
bash mcp_server/run_stdio.sh
```

`run_http.sh` also enables the **deep-reasoning tier** for the two quality-lever agents:
`MODEL_DEEP=claude-opus-4-8` + `PROVIDERS_CONFIG_PATH=config/providers.yaml` (an Anthropic registry that includes Opus). Set `MODEL_DEEP=claude-sonnet-4-6` to disable Opus (cost).

## Registering with Claude Science

Full walkthrough — including the private Cloudflare/nginx tunnel and a self-host guide — is in **`../CLAUDE_SCIENCE_SETUP.md`**. Short version: run `run_http.sh`, put it behind an **https URL** (CS's Remote connector rejects `http://` and localhost), then in Claude Science → **Add connector → Remote**, enter the URL, transport **Streamable HTTP**. Do **not** use the "local command" connector — that's the sandbox dead end described above.

## Manual fallback (no connector at all)

```bash
bash mcp_server/ask.sh "<observation>"
```
Prints the grounded brief + the `claude_science_prompt`; paste that block into a Claude Science chat to produce the figure. Same payoff, zero dependency on the connector.

## Error handling

Every tool call returns either a schema-valid result (a `TransBrief` dict for `generate_experiment`, the lighter projection for `search_grounded_evidence`) or a clean structured error:

```json
{"error": "no_api_key", "message": "No API key configured. ...", "status_code": 402}
```

Never a raw traceback. `server.py` catches the engine's own `TransBenchLLMError`, a bare `fastapi.HTTPException` (defensive — `create_llm` raises this on a missing/invalid key or unsupported model), and any other unexpected exception. Diagnostics go to stderr via the stdlib `logging` module only — this process never writes to stdout outside the MCP protocol itself (stdout is the JSON-RPC channel for the `stdio` transport).

## Cost note

Both tools run the **full** pipeline: ~13 LLM calls across three tiers — **Haiku** (grade / entail / assemble / neutralize), **Sonnet** (decompose / novelty), **Opus** (`MODEL_DEEP`: hypothesize + experiment-design) — plus live PubMed and, when a candidate is selected, a live GEO content-verification fetch (`BUILD_SPEC.md` §9). Opus on the two creative steps raises per-run cost (grading, the many-call step, stays on Haiku, so it's bounded); dial it off with `MODEL_DEEP=claude-sonnet-4-6`.

## Run time & keepalive

That same full pipeline means a run legitimately takes **~60–120s** (longer on a cold first call). There is **no 60s timeout in this server** — nginx is `3600s`, the per-model-call timeout is `90s`, engine import is <1s; the ~60s an MCP client may hit is *its own* wait-for-result timeout. So instead of a bigger number, each tool emits an **MCP progress notification every `MCP_HEARTBEAT_SECONDS` (default 10s)** while the engine runs (`_await_with_heartbeat` in `server.py`) — the MCP-standard keepalive for long-running tools, so the client keeps the call open. It's best-effort and never affects the result: `report_progress` is a documented no-op if the client sent no `progressToken`, and any notification error is swallowed. The injected `ctx: Context` parameter is hidden from the tools' public schema (clients still see only `observation`/`focus_drug` and `question`).

## Files

| File | Purpose |
|---|---|
| `server.py` | `FastMCP("transbench")` app; both tools; error handling |
| `run_http.sh` | Launch over streamable-HTTP (`127.0.0.1:8500/mcp`) — what CS connects to; sets the Opus deep tier |
| `run_stdio.sh` | Launch over stdio (direct MCP clients; not usable as a Claude Science connector) |
| `ask.sh` / `ask.py` | Fully-local: grounded brief → paste-ready `claude_science_prompt` |
| `manifest.json` | Descriptive connector manifest (live tool schemas are served by `server.py` at runtime) |
| `requirements.txt` | Pins this package's own direct `mcp` SDK dependency |
