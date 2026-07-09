# Claude Science + TransBench connector — setup (headless Linux server)

Claude Science runs as a **local web-UI daemon on your Hetzner Linux server**. You reach its UI from your laptop's browser over a forwarded port (VS Code Remote or an SSH tunnel). The TransBench MCP server runs **on the same box** and is wired in as a **local stdio** connector — no networking/auth between them, and **no secrets in the connector config** (the server reads `ANTHROPIC_API_KEY` / `PUBMED_API_KEY` from `/root/projects/transbench/.env` itself).

Verified against installed build **claude-science 0.1.17-dev** (2026-07-09).

## Current facts (this install)
| Thing | Value |
|---|---|
| App binary | `/root/Downloads/claude-science-linux-x64` |
| App UI port | **`127.0.0.1:8000`** |
| Sandbox-content port | **`127.0.0.1:8001`** (must also be forwarded) |
| HTTP fallback port (TransBench) | `127.0.0.1:8500` (path `/mcp`) |
| Data dir | `/root/.claude-science` |
| Custom MCP config file | `/root/.claude-science/mcp/local-mcp.json` |
| TransBench repo | `/root/projects/transbench` |
| TransBench venv python | `/root/projects/transbench/.venv/bin/python` |
| stdio launcher | `/root/projects/transbench/mcp_server/run_stdio.sh` |

## Step 1 — Start the Claude Science daemon (on the server)
```bash
/root/Downloads/claude-science-linux-x64 serve --no-browser --detached
/root/Downloads/claude-science-linux-x64 status     # should say "running": true, port 8000
```
(One-time prerequisites, already done on this box: `bubblewrap` ≥0.8.0 and `socat` — the daemon needs both for its sandbox.)

## Step 2 — Reach the UI from your laptop
Forward **both** ports 8000 and 8001 to your laptop, then open the login link.

**If you use VS Code Remote (what you're on now):** open the **Ports** panel → **Forward a Port** → add `8000` and `8001`. In the *Local Address* column each MUST read exactly `localhost:8000` and `localhost:8001` (if VS Code remapped one to a different local number, right-click → *Change Local Port* → set it back).

**If you use a plain terminal instead:**
```bash
ssh -N -L 8000:localhost:8000 -L 8001:localhost:8001 root@YOUR_SERVER_IP
```

Then get a fresh login link (single-use, ~3 min) and open it in your laptop browser:
```bash
/root/Downloads/claude-science-linux-x64 url
```

## Step 3 — Connect the TransBench MCP server (copy-paste)
Run this **once on the server**. It (a) grants the MCP sandbox read-only access to the paths the server needs, (b) drops a `.pth` so `mcp_server` imports without `PYTHONPATH`, (c) registers the stdio connector, then (d) restarts the daemon. **No API keys go here** — the server loads them from `.env`.

Why it's shaped this way (learned the hard way against build 0.1.17-dev):
- Claude Science runs local MCP servers **inside a sandbox** where most of `$HOME` is invisible and the config's `env`/`PYTHONPATH` is **not** honored. So we must (a) grant paths in `config.toml` and (b) make imports work without `PYTHONPATH`.
- The managed runtime only runs `node`/`npx`/`python3` **or an absolute binary path** — a `bash` `.sh` launcher is rejected. So `command` points straight at the venv Python.
- The `local-mcp.json` server object is `{ name, command, args[], env{}, description? }` — **no `cwd` field** — which is why the `.pth` (not `cd`) is what makes `-m mcp_server.server` resolve.

```bash
# (a) Sandbox read grants: repo (code/.venv/.env), the uv interpreter the venv points to,
#     and the Iatronix backend that transbench.reuse imports.
cat > /root/.claude-science/config.toml <<'TOML'
[sandbox]
user_read_paths = [
  "/root/projects/transbench",
  "/root/.local/share/uv/python",
  "/root/projects/med-ai-project",
]
TOML

# (b) Make `mcp_server` importable from the venv without PYTHONPATH or a cwd.
SP=$(/root/projects/transbench/.venv/bin/python -c "import site; print(site.getsitepackages()[0])")
echo "/root/projects/transbench" > "$SP/transbench_repo_root.pth"

# (c) Register the connector (absolute venv-python path; no secrets).
mkdir -p /root/.claude-science/mcp
cat > /root/.claude-science/mcp/local-mcp.json <<'JSON'
{
  "servers": [
    {
      "name": "transbench",
      "command": "/root/projects/transbench/.venv/bin/python",
      "args": ["-m", "mcp_server.server"],
      "env": { "MCP_TRANSPORT": "stdio", "PYTHONDONTWRITEBYTECODE": "1" },
      "description": "TransBench - grounded translational experiment generator (generate_experiment, search_grounded_evidence)"
    }
  ]
}
JSON

# (d) Restart so all three are picked up. NOTE: `stop` on this build may fail to
#     kill its own daemon (kill-guard quirk); if the new serve says "port busy",
#     kill the pid holding 8000 first:  kill $(ss -ltnp | grep 127.0.0.1:8000 | grep -oE 'pid=[0-9]+' | cut -d= -f2)
/root/Downloads/claude-science-linux-x64 stop
/root/Downloads/claude-science-linux-x64 serve --no-browser --detached
/root/Downloads/claude-science-linux-x64 url        # open this new link in your browser
```

If you ever add a second connector, add another object to the `servers` array rather than replacing the file. If the repo's venv is ever rebuilt, re-run step (b) (the `.pth`).

> **Sandbox egress is SOCKS — the venv must carry `httpx[socks]`.** Claude Science runs this
> connector inside its sandbox and forces all outbound traffic through a SOCKS proxy
> (`ALL_PROXY`/`HTTPS_PROXY=socks5://…`, injected into the subprocess). Every network path here
> is httpx-backed (the `anthropic` SDK, the GEO fetch, and the Iatronix PubMed client), so if
> `socksio` is absent the first call dies with `ImportError: Using SOCKS proxy, but the 'socksio'
> package is not installed` surfaced as **HTTP 500**. `pyproject.toml` therefore declares
> `httpx[socks]` (not plain `httpx`); a rebuilt venv picks it up automatically. If you ever see
> that 500, install it directly:
> `uv pip install --python /root/projects/transbench/.venv/bin/python "httpx[socks]"`.

## Step 4 — Verify it connected
In the Claude Science web UI, the `transbench` connector should appear with its two tools: **`generate_experiment`** and **`search_grounded_evidence`**. If it doesn't show, check the daemon log:
```bash
/root/Downloads/claude-science-linux-x64 logs --tail
```
Look for a line connecting `transbench` (or an error naming `run_stdio.sh` / a missing `.env` key).

## Step 5 — Run the flagship (the demo)
In a Claude Science session:
1. Call **`generate_experiment`** with your observation (e.g. resistant hypertension + high hs-CRP + poor RAAS response).
2. It returns a grounded `TransBrief` — hypotheses, real citations, and `top_experiment.claude_science_prompt`.
3. Ask Claude Science to run that `claude_science_prompt` — it loads the dataset and produces a reproducible figure.

## The BYOK model gate — why the stdio connector 402s, and the HTTP path that works
**Symptom:** wired as a local **stdio** connector (Step 3), `generate_experiment`'s internal LLM
steps fail with **HTTP 402 Payment Required** / "No credentials available for Anthropic API. Please
sign in with your Claude account."

**Cause — NOT your account.** The `.env` `ANTHROPIC_API_KEY` is valid and funded (proven: a direct
`anthropic` call from a normal shell, and every `POST api.anthropic.com/v1/messages` from the HTTP
server below, return **200 OK**). The 402 happens only *inside* Claude Science's sandbox, which
routes the connector's egress through Claude Science's own model broker. That broker expects a
**Claude-account** credential — and a **Claude Max/Pro subscription does not include Anthropic API
usage** (separate product, separate billing) — so the brokered call is payment-gated and your BYOK
key never reaches Anthropic from inside the sandbox. (Signing in "more" cannot fix this; it's a
product boundary, not a missing login.)

**The working path — run the server OUTSIDE the sandbox so your own key is used directly:**
```bash
bash /root/projects/transbench/mcp_server/run_http.sh      # serves http://127.0.0.1:8500/mcp
```
This process has normal egress (no sandbox, no broker) → it calls `api.anthropic.com` directly with
your `.env` key. **Verified end-to-end:** a real `generate_experiment` run returned a full grounded
`TransBrief` (3 hypotheses, 7 real PubMed refs, a `claude_science_prompt`) — 12 model calls, all
`200 OK`, zero 402s.

Two ways to consume it:
1. **As a Claude Science URL connector (cleanest).** This build ships remote URL/SSE connectors
   (e.g. `pubmed.mcp.claude.com/mcp`), so it can connect to an MCP server by **URL**, not only spawn
   stdio ones. Add a custom connector pointing at `http://127.0.0.1:8500/mcp` (via the UI's
   Add-connector; if it requires SSE, start the server with `MCP_TRANSPORT=sse bash …/run_http.sh`).
   Claude Science connects as a *client*, so the server keeps its own egress + your key — no 402.
2. **Standalone (works today, zero wiring).** Call `generate_experiment` over HTTP with a small MCP
   client (see `scratchpad` `mcp_client.py`), then paste the returned `claude_science_prompt` into
   Claude Science to produce the figure.

> **Distribution (self-host + BYOK):** each user runs their *own* `run_http.sh` with their *own*
> `.env` key — your key/costs are never shared and their data stays on their box. To make it
> portable, fill in `reuse.py`'s built-in `vendored/` fallback (the ~17 DB-free Iatronix leaf
> functions) so a clone needs no external `med-ai-project` path.

## Handy commands
```bash
APP=/root/Downloads/claude-science-linux-x64
$APP url            # fresh login link
$APP status         # running? port/pid/version
$APP logs --tail    # watch the daemon log
$APP stop           # shut it down
```
