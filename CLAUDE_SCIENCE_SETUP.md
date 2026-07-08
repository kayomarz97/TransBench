# Claude Science + TransBench connector — setup (Windows laptop + headless Linux server, standalone repo)

Claude Science is a **macOS/Linux desktop app** — it will not run on Windows. Run it **on your Hetzner Linux server**, run the TransBench MCP server **from the new repo on the same box**, and reach the UI from your Windows laptop's **browser over an SSH tunnel**. Both live on one machine, so the connector is a **local stdio** server — no networking/auth between them.

> This is your **primary, required path** — you're demoing through the Claude Science app, so the connector below is essential, not optional. Beta caveat: Claude Science (June 30, 2026) may differ from these exact steps — confirm at claude.com/science. The standalone **HTTP path** at the bottom is only a demo-day safety net in case the beta app misbehaves live; it does not replace the connector.

Paths (adjust): new repo `<TRANSBENCH_PATH>` = `/root/projects/transbench`; Iatronix `<IATRONIX_PATH>` = `/root/projects/med-ai-project`.

## Prerequisites
- A paid Claude plan that includes Claude Science (your hackathon Max qualifies).
- The new TransBench repo on the server, its venv created, and the build complete (`src/transbench/`, `mcp_server/`). The venv has the Iatronix backend installed as a read-only path dependency (Phase 0), so imports work while Iatronix stays untouched.
- Windows 10/11 with OpenSSH (`ssh` in PowerShell).

## Step 1 — Install Claude Science on the Linux server
Follow the official Linux install (claude.com/science), sign in with your paid account. Note the local UI port it serves (call it `CS_PORT`).

## Step 2 — Tunnel from Windows
```powershell
ssh -N -L 3000:localhost:CS_PORT youruser@YOUR_SERVER_IP
```
Then open `http://localhost:3000` in your Windows browser. (Use `-t` instead of `-N` if it needs an interactive session.)

## Step 3 — TransBench MCP server (same box)
- **stdio (Claude Science spawns it):** you don't start it manually — Claude Science launches it from the Step 4 config.
- **HTTP (fallback/testing):** `bash <TRANSBENCH_PATH>/mcp_server/run_http.sh` → serves on `localhost:8500`.

## Step 4 — Register the connector in Claude Science
Add TransBench as a local stdio server in Claude Science's MCP/connector config (confirm the exact path in Settings → Connectors / Developer settings):
```json
{
  "mcpServers": {
    "transbench": {
      "command": "/root/projects/transbench/.venv/bin/python",
      "args": ["-m", "mcp_server.server"],
      "cwd": "/root/projects/transbench",
      "env": {
        "PYTHONPATH": "/root/projects/transbench/src",
        "ANTHROPIC_API_KEY": "sk-ant-YOUR_KEY",
        "PUBMED_API_KEY": "YOUR_NCBI_KEY"
      }
    }
  }
}
```
Add any extra env the Phase 0 smoke test showed the imported Iatronix services need (e.g. `ENCRYPTION_KEY`, model id vars). Match `command`/`args` to `mcp_server/run_stdio.sh`. Restart Claude Science; it should list `transbench` with its two tools.

## Step 5 — Run the flagship (the demo)
In a Claude Science session (via your tunnelled browser):
1. Call `generate_experiment` with the flagship observation (resistant hypertension + high hs-CRP + poor RAAS response).
2. It returns a grounded `TransBrief` — hypotheses, real citations, and `top_experiment.claude_science_prompt`.
3. Ask Claude Science to run that `claude_science_prompt` — it loads the scRNA-seq/Perturb-seq dataset and produces a reproducible figure. Screen-record this.

## Fallback demo (if the desktop app/connector is unreliable on demo day)
Your submission stands alone:
1. `bash <TRANSBENCH_PATH>/mcp_server/run_http.sh` (or run the engine's HTTP entrypoint).
2. Call `generate_experiment` over HTTP (a ~15-line Python MCP client) and show the full grounded brief + experiment.
3. Paste `claude_science_prompt` into Claude Science **manually** to produce the figure — same payoff, zero dependency on live connector wiring.

Record BOTH paths before demo day. Lead with the connector; keep the fallback one keystroke away.
