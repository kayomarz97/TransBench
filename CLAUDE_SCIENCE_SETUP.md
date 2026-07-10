# Claude Science + TransBench — the fully-local setup

TransBench runs **fully locally, with nothing exposed on a network**. Because of how Claude Science
sandboxes connectors (see the verdict below), the private way to use it is **alongside** Claude
Science: generate a grounded brief + a paste-ready prompt locally with one command, then paste that
prompt into a Claude Science chat to produce the figure.

> ### ⚕️ Healthcare / privacy note — read this first
> TransBench is **not fully offline**. Its reasoning uses **Anthropic's cloud API** and it queries
> **PubMed**, so the **observation text you submit is sent to Anthropic's API** (not used for
> training by default, but transmitted to the cloud). "Local" here = the server/orchestration run on
> your machine; the LLM does not. **De-identify observations before submitting** (age band +
> presentation only — no names, MRNs, or dates). Fully-offline operation would need a local model,
> which is out of scope for this build.

Verified against installed build **claude-science 0.1.17-dev** (2026-07-09).

## Why there is no fully-local *connector* (both Claude Science options are blocked)
Claude Science's "Add connector" offers **Remote** and **Local command** — and for a tool that
itself calls an LLM, both are dead ends locally:

| | **Remote (URL)** | **Local command (stdio)** |
|---|---|---|
| What CS requires | a **public `https://` URL** — its `safeFetch` rejects `http://` **and** localhost/private IPs (SSRF protection) | CS **spawns your process inside a locked-down sandbox** |
| Why it fails locally | `http://127.0.0.1:8500` → `safeFetch: https-only … must be a public https URL` | sandbox proxy returns **`403` for `api.anthropic.com`** + isolated loopback → the engine's model calls die (HTTP 402/403) |

**Proven (2026-07-09):** a probe *inside* the CS sandbox showed `GET api.anthropic.com → 403
Forbidden` and `127.0.0.1:8500 → connection refused`. So a connector that calls an LLM cannot run in
the sandbox, and CS won't point "remote" at a local address. This is a **Claude Science limitation,
not a TransBench bug**. The only way to make it a connector is a **public https tunnel** — i.e.
putting it "online", which defeats the point for a healthcare app.

## ✅ The local workflow (recommended — private, one command)
```bash
bash mcp_server/ask.sh "33F, resistant hypertension on telmisartan + thiazide + CCB; raised CRP"
```
It runs the grounded pipeline locally (reads `ANTHROPIC_API_KEY` from `.env`) and prints:
1. a short grounded brief (hypotheses + real PubMed / Europe PMC citations), and
2. a **`claude_science_prompt`** block.

Copy that prompt block into a **Claude Science chat** → CS loads the dataset and produces the
reproducible figure. Nothing is exposed on a network; only the (de-identified) observation reaches
Anthropic's API, exactly as any LLM call would. Verified end-to-end (3 hypotheses, real citations,
runnable prompt; all model calls `200 OK`).

## Run it AS a Claude Science connector (via a private HTTPS tunnel)
CS's **Remote** connector needs a **public `https://` URL** (it rejects `http://` and localhost). So to
get the "CS agent calls the tool" experience, expose the local server (`127.0.0.1:8500`) at an https URL.
On this box that's done with **nginx + Cloudflare** (proxied, Cloudflare Origin cert), locked to
Cloudflare's edge IPs and gated by an **unguessable secret path**. Add it in CS → **Add connector →
Remote**, transport **Streamable HTTP**, no auth — the real URL is in **`tunnel.local.md`** (git-ignored;
never in this committed doc). Verified end-to-end (full MCP handshake, both tools).

### Set up YOUR OWN tunnel (standalone guide for other self-hosters)
Your data + key stay on your box; each person exposes their own instance. **The one non-obvious gotcha
(both options): TransBench's MCP server does DNS-rebinding protection and only accepts
`Host: 127.0.0.1:8500` (or `localhost:8500`) — your proxy/tunnel MUST send that Host, or you get
`421 Invalid Host header`.**

**Option A — nginx + Cloudflare (proxied origin):**
1. `bash mcp_server/run_http.sh` (serves `127.0.0.1:8500`).
2. Cloudflare → your domain → **DNS → Add** an **A record** `mcp` → your server IP, **Proxied** (orange).
   *(Use an A record, not a CNAME to your proxied apex — that loops → 522.)*
3. Cloudflare → **SSL/TLS → Origin Server → Create Certificate** (hostname `*.yourdomain`); save cert+key
   to `/etc/ssl/<name>/`.
4. New nginx vhost (**specific `server_name`, no `default_server`** so it can't touch your other sites):
   ```nginx
   server {
     listen 443 ssl;
     server_name mcp.yourdomain;
     ssl_certificate /etc/ssl/<name>/origin.pem;  ssl_certificate_key /etc/ssl/<name>/privkey.pem;
     include /etc/nginx/cloudflare-allow.conf;              # allow <CF ranges>; deny all;  (origin lockdown)
     location / { return 404; }
     location /<LONG_RANDOM_SECRET>/mcp {
       proxy_pass http://127.0.0.1:8500/mcp;
       proxy_http_version 1.1;
       proxy_set_header Host 127.0.0.1:8500;                # ← REQUIRED (see gotcha above)
       proxy_set_header Connection "";
       proxy_buffering off;  proxy_read_timeout 3600s;
     }
   }
   ```
   Cloudflare allowlist: `{ for ip in $(curl -fsS https://www.cloudflare.com/ips-v4) $(curl -fsS https://www.cloudflare.com/ips-v6); do echo "allow $ip;"; done; echo "deny all;"; } > /etc/nginx/cloudflare-allow.conf`
5. `nginx -t && nginx -s reload`. Connector URL = `https://mcp.yourdomain/<SECRET>/mcp`.

**Option B — Cloudflare Tunnel (`cloudflared`), no nginx at all (most isolated):**
```bash
bash mcp_server/run_http.sh
cloudflared tunnel login && cloudflared tunnel create transbench
# ingress → service: http://127.0.0.1:8500 , with  httpHostHeader: 127.0.0.1:8500  (the same Host requirement)
cloudflared tunnel route dns transbench mcp.yourdomain && cloudflared tunnel run transbench
```
Connector URL = `https://mcp.yourdomain/mcp` (add a Cloudflare Access policy for privacy).

## "The first call takes a while / times out and I have to rerun"
A full run is **~13–16 model calls** (Opus on the two hardest steps: hypothesize + experiment-design;
Haiku also writes a search query per hypothesis) plus **multi-database retrieval** (PubMed +
ClinicalTrials.gov + **Europe PMC**) and a **GEO** content fetch, so it legitimately takes **~60–120s** — and longer
on a **cold** first call. That is longer than **Claude Science's own wait-for-result timeout** for a
*single* tool call (~60s, a hard ceiling; it is **not** a server timeout — nginx is `3600s`, the
per-model-call timeout is `90s`, engine import is <1s). A progress-notification keepalive does **not**
move that ceiling (CS does not reset its timeout on progress), so the tools no longer block on the run.

Instead they are **async (submit + poll)**:
1. `generate_experiment` (or `search_grounded_evidence`) returns in **<1s** with
   `{"job_id", "status": "running", "poll_tool": "get_experiment_result", ...}`.
2. Call **`get_experiment_result(job_id)`** every ~5s. It returns `{"status": "running"}` while the
   pipeline works, then `{"status": "done", "result": <brief>}` (or `{"status": "error", "result":
   {…}}`). Each call is sub-second, so **no single call ever approaches the ~60s ceiling** — the run can
   take 60s, 120s, or longer cold and it never times out.

The in-conversation CS agent polls automatically (the tool descriptions tell it to). Tuning knobs
(leave default unless you have reason not to), set in the service / `run_http.sh` env:
`MCP_MAX_CONCURRENT_JOBS` (default `4`, max simultaneous engine runs) and `MCP_JOB_TTL_SECONDS`
(default `1800`, how long a finished result stays pollable before eviction). After
`systemctl restart transbench-mcp` the next run is cold again by design — but cold no longer means a
timeout, just a longer poll.

## Data sources (multi-database retrieval)
Grounding is **not** PubMed-only. For each hypothesis the engine writes clean search queries (a cheap
LLM step, with a heuristic fallback) and runs them across **multiple databases**, all graded through
the same rigor pipeline:
- **PubMed + ClinicalTrials.gov** (the Iatronix clinical-evidence fetcher) — trials, reviews, guidelines.
- **Europe PMC** (default on, **no key**) — the general mechanism/biology literature the clinical
  fetcher misses (e.g. T-cell-exhaustion papers). This is what lets mechanistic hypotheses actually ground.
- **Semantic Scholar** (**optional**) — add a free key as `SEMANTIC_SCHOLAR_API_KEY` in `.env`; without
  a key it is skipped (its keyless pool rate-limits immediately). Disable a backend with
  `TRANSBENCH_ENABLE_EUROPEPMC=0` / `TRANSBENCH_ENABLE_SEMANTIC_SCHOLAR=0`.

Mechanism evidence from a **different disease or model** counts as legitimate *translational* support:
a mechanism shown in melanoma or a mouse model is a testable hypothesis for this patient, and the
designed experiment is exactly what tests whether it transfers. Every brief that ships an experiment
states this plainly in its uncertainty note — the inference is disclosed, never hidden.

## Distribution (self-host + BYOK)
TransBench is self-contained (`src/vendored/`) — a clone needs no external `med-ai-project`:
`git clone` → `uv sync` → add `ANTHROPIC_API_KEY` to `.env` → `bash mcp_server/ask.sh "…"`. Each
user runs their own copy with their own key; keys/costs are never shared and data stays on their box.

## Handy commands
```bash
bash mcp_server/ask.sh "<de-identified observation>"   # local: brief + paste-ready CS prompt
APP=/root/Downloads/claude-science-linux-x64
$APP url            # fresh CS login link       $APP status   # running? port/pid/version
$APP logs --tail    # watch the daemon log      $APP stop     # shut CS down
```
