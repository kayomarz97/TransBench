# AGENTS_PLAYBOOK.md — command cookbook & mistakes ledger

A living reference for Claude Code and its agents. **Read it on demand** — it is intentionally
*not* `@`-imported into `CLAUDE.md`, so it never costs context until it's actually needed.

**How to use it**
- *Before acting:* skim the relevant section instead of rediscovering a command or re-hitting a
  known trap.
- *After a stumble:* append what you learned. A bug you write down once is a bug you never debug
  twice. Keep entries short, dated, and specific.

---

## Command cookbook (verified)

```bash
# Install (self-contained: vendored Iatronix backend, no external paths, no shared keys)
uv sync

# THE OFFLINE BENCH — no key, no network, deterministic, ~2s. Use this to verify changes.
# (hides .env behind a restore-trap, strips the key, golden replay, deselects 2 live-fallback tests)
bash scripts/offline-bench.sh

# One OFFLINE test / pattern (need no key — safe & instant); pass paths straight through the wrapper
.venv/bin/python -m pytest tests/test_pubmed_query_builder.py -q
bash scripts/offline-bench.sh tests/test_grounding.py

# ⚠️ FULL suite the raw way — runs the LIVE pipeline (real API $$, minutes) if a key is in .env,
#    because config.py loads .env with override=True and live tests gate only on key presence.
#    Offline ONLY when no key is present (the CI condition). Prefer offline-bench.sh above.
.venv/bin/python -m pytest -q

# Engine smoke with no key (golden replay of a real committed run)
TRANSBENCH_MODE=golden .venv/bin/python -c "import asyncio; from transbench.engine import run_transbench; \
print(asyncio.run(run_transbench('<paste a de-identified observation>')).top_experiment.claude_science_prompt)"

# Secret / sensitive-file scan (also runs automatically on pre-push)
bash scripts/scan-secrets.sh --all       # whole tree
bash scripts/scan-secrets.sh --staged     # only what's staged

# Activate the pre-push secret hook (once per clone)
git config core.hooksPath .githooks

# Branch policy: work on and push dev only; the user merges main by hand
git push origin dev

# DEMO RECORDING — flip to golden + mint a fresh single-use Claude Science link, then restore live.
# Golden = the captured lupus brief returns INSTANTLY on camera (exact-observation match only, so it
# can't answer any other question). Backs the /record-link skill (trigger phrase: "link to record skill").
# GOTCHA: the CS daemon runs on :8000 on this box (older docs say :3000) — SSH forward must be -L 8000:localhost:8000.
bash scripts/record-golden.sh            # engage golden + verify-then-link + print the link
bash scripts/record-golden.sh --revert   # restore live (ALWAYS run this when filming is done)
bash scripts/record-golden.sh --status   # show current mode, change nothing
```

Running the MCP server and registering it in Claude Science: follow `CLAUDE_SCIENCE_SETUP.md`
and `README.md` (don't hand-type the invocation from memory — those files are the source of truth).

---

## External service & API notes

Populated by the **`docs-researcher`** agent. Record: service, version, minimal correct snippet,
gotchas, source URL + date. Keep the answer, drop the prose.

- **Claude models used in the pipeline** (roles per `README.md`): **Opus 4.8** — hypothesize +
  experiment design (the two quality levers); **Sonnet** — decompose + novelty; **Haiku** —
  grade / entail / assemble. Exact model IDs live in the repo's config — read them there rather
  than hard-coding; for anything about pricing/params/limits, use the `/claude-api` skill instead
  of memory.
- **MCP + Claude Science:** the connector is registered as an MCP server; long tools must be
  async submit-and-poll (see ledger entry below).
- **Claude Artifacts (claude.ai, chat feature — 2026-07-11).** Correct product name is
  **"Artifacts"** (not "Claude Design"). Prompt Claude in a normal chat; it auto-creates an
  artifact for content ">15 lines" that's "significant and self-contained." Toggle:
  **Settings → Capabilities → "Artifacts"** (its own switch, separate from "Code execution and
  file creation"). Plans: **Free, Pro, Max, Team, Enterprise** all get chat Artifacts — don't
  confuse with the *different*, newer **"Claude Code Artifacts"** feature (CLI/desktop-app only,
  requires `/login`, Pro/Max/Team/Enterprise only, **not** Free).
  Types: single-page **HTML** (`text/html` — inline `<style>`/`<script>`, best for
  canvas/SVG/CSS animation, full control, no library allowlist), React (`.jsx`, uses a
  pre-approved-library sandbox that Anthropic has **not** published officially — avoid for pure
  animation work), plus SVG, Markdown, Mermaid, PDF.
  **Sandbox/CSP** — officially documented for Claude Code Artifacts, and the same viewer infra
  is explicitly shared with chat-created artifacts (same `*.claudeusercontent.com` origin, same
  `claude_artifact_*` audit-event family): **no external network** — CSP blocks scripts,
  stylesheets, fonts, images from any other host, and blocks `fetch`/`XHR`/`WebSocket`; CSS/JS
  must be inlined, images as data URIs; rendered page capped at **16 MiB**.
  → **Practical rule: for a recording-ready animated artifact, explicitly tell Claude to inline
  everything (no CDN `<script src>`, no Google Fonts `<link>`, no remote `<img>`) and to set an
  explicit background color in CSS rather than relying on the site theme** — no official doc
  confirms artifacts inherit claude.ai's dark/light mode; forcing it in the artifact's own CSS is
  the reliable path.
  **Recording flow:** artifact panel has an expand-to-full-screen control (confirmed for the
  sibling "Custom visuals" feature, same rendering component); for a clean chrome-free capture,
  click **Publish** (bottom of the artifact panel) → copy the public link → open that link **in
  its own browser tab** and screen-record the tab (no chat UI around it).
  Sources (read 2026-07-11): [What are artifacts](https://support.claude.com/en/articles/9487310-what-are-artifacts-and-how-do-i-use-them) ·
  [Publish and share artifacts](https://support.claude.com/en/articles/9547008-publish-and-share-artifacts) ·
  [Custom visuals in chat and Cowork](https://support.claude.com/en/articles/13979539-custom-visuals-in-chat-and-cowork) ·
  [Claude Code Artifacts docs (CSP/16MiB/domain, official)](https://code.claude.com/docs/en/artifacts) ·
  [Appearance settings](https://support.claude.com/en/articles/8887527-customizing-your-appearance-settings) ·
  [claude.ai published system prompt (confirms "Artifacts" is a distinct toggle)](https://platform.claude.com/docs/en/release-notes/system-prompts).
  **Gap:** no official Anthropic page states the React-artifact library allowlist or explicitly
  confirms chat-HTML-artifacts inherit the CSP verbatim (only Claude Code Artifacts docs state it
  directly) — flagged, not invented.

---

## Mistakes ledger (real traps, with the fix)

> Seeded from this repo's own git history and build notes. Add to it whenever something bites.

- **2026-07 · A bare `pytest` runs the LIVE pipeline on a keyed machine.** `config.py` loads `.env`
  with `override=True`, and every live test gates only on `skipif(not ANTHROPIC_API_KEY)`. So on a
  dev box whose `.env` holds a real key, `pytest -q` silently makes real Anthropic + PubMed calls —
  minutes of wall-clock and real money — which reads as a "hang." **Fix:** verify with
  **`bash scripts/offline-bench.sh`** (hides `.env`, strips the key, golden replay, deselects the two
  live-fallback tests). Clean run: **183 passed / 23 skipped / 2 deselected in ~2s**. (found 2026-07)
- **2026-07 · Two live-fallback tests lack the `skipif` guard their siblings have.**
  `test_experiment_phase5.py::…falls_through_to_live…` and
  `test_snapshot_toggle.py::…falls_back_to_live_retrieval` assert the live-FALLBACK path, so they
  402 without a key and would FAIL in keyless CI. **Long-term fix:** add
  `@pytest.mark.skipif(not os.environ.get("ANTHROPIC_API_KEY"), …)` to both (the suite's own
  convention); until then `offline-bench.sh` deselects them.
- **2026-07 · Opus 4.8 rejects `temperature` → HTTP 400.** Opus 4.8 does not accept the
  `temperature` param and 400s if you send it. **Fix:** gate the param by model — omit
  `temperature` for Opus, keep `temperature=0` for Sonnet/Haiku. (commit `d50ad82`)
- **2026-07 · Claude Science kills MCP tool calls at ~60s.** A synchronous long-running tool times
  out. **Fix:** make long tools **async submit-and-poll** — return a job token immediately, then
  poll for the result. (commit `02726bd`)
- **SOCKS sandbox → HTTP 500 without `socksio`.** Claude Science runs the server in a sandbox that
  forces all egress through a SOCKS proxy. Plain `httpx` dies with
  `ImportError: ... 'socksio' package is not installed`. **Fix:** depend on **`httpx[socks]`**, not
  `httpx`. (`pyproject.toml`)
- **Stale ambient `ANTHROPIC_API_KEY` beats the real BYOK key.** An invalid key already in the
  process env silently wins. **Fix:** load this repo's `.env` with **`override=True`** so the BYOK
  key is authoritative across tests, engine, and MCP server. (`pyproject.toml`)
- **BYOK model calls are 402-gated *inside* the Claude Science sandbox.** Direct model HTTP from
  within the sandbox can 402. **Fix:** run those HTTP calls **outside** the sandbox. (build notes)
- **Vendored backend needs 2 data files beyond the `.py` closure.** `provider_registry.py` →
  `config/providers.yaml` (**required**, hard-fails without it); `data_fetcher.py` →
  `data/medical_journals.json` (graceful if absent). Ship both inside the `vendored` package.
  (`pyproject.toml [tool.setuptools.package-data]`)
- **`src/vendored/` is read-only.** It's a mirror of the Iatronix backend. Never edit it; if
  behavior needs to change, wrap it from `src/transbench/`. (`README.md`)

---

## One-paste installer — verified commands (2026)

Verified 2026-07-12 by the `docs-researcher` agent for a cross-platform (macOS + Linux) one-paste
bootstrap script. Method: official docs, and — for the two flags that make-or-break the MCP tunnel's
Host-header fix — **the actual current binary's own `--help` output**, downloaded fresh from the
official release channel. When a doc page was ambiguous, the tool's own `--help` settled it; that's
strictly more authoritative than a doc page for "does this flag exist / what does it do."

### uv (astral.sh)
```bash
curl -LsSf https://astral.sh/uv/install.sh | sh   # macOS AND Linux — identical one-liner, installs to ~/.local/bin
# macOS alt: brew install uv
uv python install 3.11                             # repo pins requires-python = ">=3.11"; no .python-version file
uv sync
```
`uv sync` **does** auto-fetch a managed Python if none matches ("by default, uv will automatically
download Python versions when they are required") — but there's a known edge case mixing
`.python-version` + `requires-python` (astral-sh/uv#16012). **Reliable order on a fresh machine:**
install uv → `uv python install 3.11` (explicit) → `uv sync`. Don't lean on the implicit auto-fetch
alone in a script meant to "just work" on someone else's laptop.
Sources (read 2026-07-12): [Installation](https://docs.astral.sh/uv/getting-started/installation/) ·
[Installing and managing Python](https://docs.astral.sh/uv/guides/install-python/).

### cloudflared quick tunnel — THE CRITICAL ONE (host-header flag CONFIRMED against the real binary)
```bash
# macOS
brew install cloudflared
# Linux (Debian/Ubuntu, official apt repo)
sudo mkdir -p --mode=0755 /usr/share/keyrings
curl -fsSL https://pkg.cloudflare.com/cloudflare-main.gpg | sudo tee /usr/share/keyrings/cloudflare-main.gpg >/dev/null
echo 'deb [signed-by=/usr/share/keyrings/cloudflare-main.gpg] https://pkg.cloudflare.com/cloudflared any main' | sudo tee /etc/apt/sources.list.d/cloudflared.list
sudo apt-get update && sudo apt-get install cloudflared
# "any" is the generic codename that works across supported Debian/Ubuntu; swap in
# jammy/noble/bookworm/etc. if a specific box ever needs it.

# Quick tunnel, no account/domain needed, WITH the Host-header fix baked in:
cloudflared tunnel --url http://localhost:8500 --http-host-header "127.0.0.1:8500"
```
- Prints the random `https://<two-words>.trycloudflare.com` URL **to stdout** (official doc: "print[ed]
  in the terminal"; grep both streams if scripting, to be safe).
- `--http-host-header` **is real, current, and built for exactly this** — verified directly against
  the binary, not just docs (the origin-parameters doc page never states whether a CLI flag exists or
  whether it applies to `--url` quick tunnels; the binary's own `--help` removes all doubt):
  ```
  $ cloudflared --version
  cloudflared version 2026.7.1 (built 2026-07-09-13:00 UTC)     # official GitHub release, downloaded 2026-07-12
  $ cloudflared tunnel --help | grep -A2 http-host-header
     --http-host-header --url   Sets the HTTP Host header for the local webserver. This flag only
                                  takes effect if you define your origin with --url and if you do not
                                  use ingress rules. ... [$TUNNEL_HTTP_HOST_HEADER]
  ```
  Its own help text says it "only takes effect if you define your origin with `--url`" — i.e. it's
  *for* quick tunnels, not an ingress/config.yml-only feature (the docs page alone left this
  ambiguous). Env var alternative: `TUNNEL_HTTP_HOST_HEADER=127.0.0.1:8500`.
  ⚠️ **Not independently round-trip tested.** Confirmed the flag exists and is documented by the tool
  itself to do what we need, but did not run it against a live trycloudflare.com edge end-to-end —
  the sandbox's own auto-mode classifier correctly blocked launching a live public tunnel as
  out-of-scope for doc verification. **Smoke-test this exact command live before shipping the
  installer**; everything else in this section is either doc- or binary-confirmed with no live-network
  caveat.
- Caveats (official trycloudflare doc): dev/test only, **not for production**, no SLA; hard cap of
  **200 concurrent in-flight requests** (429 above that); **no Server-Sent Events**; a quick tunnel is
  skipped/unsupported if `~/.cloudflared/config.yaml` already exists on the machine.
Sources (read/run 2026-07-12): [Downloads](https://developers.cloudflare.com/tunnel/downloads/) ·
[Quick Tunnels](https://developers.cloudflare.com/cloudflare-one/networks/connectors/cloudflare-tunnel/do-more-with-tunnels/trycloudflare/) ·
[Origin parameters](https://developers.cloudflare.com/cloudflare-one/networks/connectors/cloudflare-tunnel/configure-tunnels/cloudflared-parameters/origin-parameters/)
· actual `cloudflared tunnel --help` output (binary v2026.7.1, official GitHub release).

### ngrok
```bash
# macOS
brew install ngrok
# Linux (Debian/Ubuntu, official apt repo)
curl -sSL https://ngrok-agent.s3.amazonaws.com/ngrok.asc | sudo tee /etc/apt/trusted.gpg.d/ngrok.asc >/dev/null
echo "deb https://ngrok-agent.s3.amazonaws.com bookworm main" | sudo tee /etc/apt/sources.list.d/ngrok.list
sudo apt update && sudo apt install ngrok

ngrok config add-authtoken <TOKEN>   # one-time; free token at dashboard.ngrok.com/get-started/your-authtoken
```
**`--host-header` is GONE — do not use it.** Verified directly against the actual current binary
(`ngrok version 3.39.9`, downloaded 2026-07-12 from the official CDN): `ngrok http --help` does **not
list `--host-header` at all** anymore — fully absent, not merely marked deprecated. Docs corroborate:
"(deprecated) use traffic policy instead." **Current, correct mechanism — Traffic Policy:**
```yaml
# traffic-policy.yml
on_http_request:
  - actions:
      - type: add-headers
        config:
          headers:
            host: "127.0.0.1:8500"
```
```bash
ngrok http 8500 --traffic-policy-file traffic-policy.yml
```
Docs note the `host` key under `add-headers` is special-cased to *replace* the Host header, not
append a second one. Free static domain: every free account gets one auto-assigned, unchangeable "dev
domain" (base is `ngrok-free.app` or `ngrok-free.dev` — docs list both with no stated precedence, so
read the real value from the dashboard rather than hardcoding a suffix):
```bash
ngrok http --url=<your-dev-domain>.ngrok-free.app 8500
```
The real binary's `ngrok http --help` only exposes `--url`, not `--domain` — `--domain` is fully gone
too (consistent with "deprecated, use --url instead").
⚠️ Same caveat as cloudflared: the traffic-policy Host-rewrite was **not** round-trip tested live (no
live tunnel launched, by design). Confirmed only via the actual binary's flag list + two independent
doc pages agreeing. Smoke-test before trusting it in the installer.
Sources (read/run 2026-07-12): [Download Linux](https://ngrok.com/download/linux) ·
[Download macOS](https://ngrok.com/download/mac-os) · [Agent CLI](https://ngrok.com/docs/agent/cli/) ·
[HTTP endpoints](https://ngrok.com/docs/http/) · [Domains](https://ngrok.com/docs/universal-gateway/domains)
· actual `ngrok http --help` output (binary v3.39.9, official CDN).

### Claude Code CLI
```bash
claude --model haiku                         # alias, confirmed accepted & stable
claude --model claude-haiku-4-5-20251001     # fully-qualified pinned snapshot, if you want a fixed
                                              # version rather than "whatever haiku currently resolves to"
```
- **Effort/thinking is exposed via CLI** — `claude --effort <low|medium|high|xhigh|max|ultracode>`
  (also `/effort` mid-session, or env var `CLAUDE_CODE_EFFORT_LEVEL`) — **but it has no effect on
  Haiku.** Confirmed from the official models table: Claude Haiku 4.5 shows **"Adaptive thinking:
  No"** (only Fable 5 / Sonnet 5 / Opus 4.6+ support the adaptive-reasoning system `--effort`
  controls), and the Claude Code effort-level table omits Haiku entirely, stating "models not listed
  here do not support effort." Haiku instead uses the older fixed-budget "extended thinking" (on/off
  toggle: `Option+T` macOS / `Alt+T` Linux mid-session, or `/config`; no session-start CLI flag for it
  besides the `MAX_THINKING_TOKENS` env var, which is a budget cap, not an effort dial). **Bottom
  line: for a Haiku-pinned installer session, `--effort` is a real flag but is a no-op on that model —
  steer via the prompt instead**, as already planned.
- **WebFetch confirmed**: Claude Code has a built-in `WebFetch` tool; pasting "Read
  `https://raw.githubusercontent.com/...` and follow it" will fetch the URL and act on it. Default
  permission mode prompts on **first use of each tool, per domain, per project**
  (`WebFetch(domain:...)` rule, remembered after approval) — a first-time user will see exactly one
  approval prompt for that domain before anything runs.
Sources (read 2026-07-12): [Model configuration](https://code.claude.com/docs/en/model-config) ·
[CLI reference](https://code.claude.com/docs/en/cli-reference) ·
[Configure permissions](https://code.claude.com/docs/en/permissions) ·
[Models overview](https://platform.claude.com/docs/en/about-claude/models/overview).

### git presence
```bash
# macOS — ships with Xcode Command Line Tools
xcode-select --install    # official git-scm.com guidance; or: brew install git
# Linux (Debian/Ubuntu)
sudo apt-get install git -y   # git-scm.com also lists `git-all` for the full suite (gitk, git-gui)
```
Sources (read 2026-07-12): [git-scm.com: Install for macOS](https://git-scm.com/install/mac) ·
[git-scm.com: Install for Linux](https://git-scm.com/download/linux).

---

## Reuse in another project

This playbook is a template. Keep the two headings **How to use it** and **Mistakes ledger** as-is,
replace the **Command cookbook** with your project's real commands, and let the ledger grow. The
`scripts/scan-secrets.sh` + `.githooks/pre-push` pair and the `.claude/agents/` definitions are
project-agnostic and copy straight across.
