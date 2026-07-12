<!--
  INSTALL_AGENT.md — the public "agentic" installer for TransBench.
  A non-engineer pastes a 4-line prompt into Claude Code; Claude Code reads THIS
  file and executes it top-to-bottom. Written as an explicit, ordered checklist so
  a small, cheap model (Haiku) runs it identically every time — reproducibility
  comes from this file being deterministic, not from the model improvising.
  Verified commands: see .claude/AGENTS_PLAYBOOK.md → "One-paste installer — verified commands (2026)".
-->

# TransBench — one-paste installer 🧪

**If you're a scientist reading this on GitHub:** you don't run these steps by hand. Copy the short
prompt from the project README, paste it into **Claude Code**, and it will do everything below for you,
explaining each step in one plain sentence. This file is the script it follows.

---

## 🤖 AGENT INSTRUCTIONS — read this whole block before you start

You are setting up **TransBench** on the computer of a **non-engineer scientist**. Behave like this:

1. **Explain, then act.** Before each numbered step, say in **one plain sentence** what you're about to
   do and why. No jargon. After it runs, say whether it worked.
2. **Do the steps in order.** Don't skip ahead. Each step checks whether it's already done and is safe
   to re-run, so if the scientist re-runs you later, you won't break anything.
3. **Stop and ask on any failure.** If a command errors, show the error in plain language, say what you
   think it means, and ask the scientist how to proceed. **Never** invent a workaround that changes the
   project's source code — this project is known-good; setup problems are environment problems.
4. **Never print, echo, log, or repeat the API key.** Not in a command, not in a summary, not ever. When
   you need to confirm it's set, check for its *presence* only (a command that prints "detected", never
   the value).
5. **Ask before anything that needs the scientist's input or their computer password** (installing
   system tools with `sudo` on Linux, or Xcode tools on macOS). Show the exact command; let them say go.
6. **Two registers.** The scientist is a clinician/scientist, not a programmer. Lead with plain language;
   keep the technical detail short and secondary.

When you finish, show the **"✅ Next steps in Claude Science"** card at the very bottom — that's the whole
point of the install.

---

## Step 0 — Detect the operating system

```bash
uname -s   # "Darwin" = macOS, "Linux" = Linux
```
Remember the result. Wherever a step says *(macOS)* or *(Linux)*, run only the matching line.

## Step 1 — Make sure `git` is available

```bash
command -v git >/dev/null && echo "git: already installed" || echo "git: NEEDS INSTALL"
```
If it needs installing, **ask the scientist first**, then:
- *(macOS)* `xcode-select --install`  ← opens Apple's installer window; tell them to click "Install" and wait for it to finish, then continue.
- *(Linux)* `sudo apt-get update && sudo apt-get install -y git`  ← needs their computer password.

## Step 2 — Install `uv` (the Python installer this project uses)

`uv` sets up Python and all the project's parts in one go, so the scientist never has to think about Python versions.

```bash
command -v uv >/dev/null && echo "uv: already installed" || curl -LsSf https://astral.sh/uv/install.sh | sh
```
This is the **same command on macOS and Linux** and needs no password. If it just installed, the
scientist may need a new terminal line for `uv` to be found — if `command -v uv` still shows nothing,
run `source $HOME/.local/bin/env` (or tell them to open a fresh terminal) and try again.

## Step 3 — Get TransBench onto the computer

```bash
# If a TransBench folder is already here, update it; otherwise download it fresh.
if [ -d TransBench/.git ]; then
  cd TransBench && git pull --ff-only
elif [ -f pyproject.toml ] && grep -q 'name = "transbench"' pyproject.toml 2>/dev/null; then
  echo "Already inside the TransBench folder."
else
  git clone https://github.com/kayomarz97/TransBench.git && cd TransBench
fi
pwd   # confirm we're inside the TransBench folder before continuing
```
Every later step assumes you are **inside the TransBench folder**. Do not leave it.

## Step 4 — Install Python 3.11+ and all project parts

```bash
uv python install 3.11   # fetches a known-good Python just for this project
uv sync                  # installs TransBench + its bundled engine (no external paths, no network tricks)
```
`uv sync` is the big one; it can take a minute. When it finishes, the project is installed.

## Step 5 — Add the scientist's Anthropic API key (kept private, never shown)

TransBench does its reasoning with **your own** Anthropic API key ("bring your own key"). It lives only in
a local file called `.env`, which the project never commits or uploads.

```bash
[ -f .env ] || cp .env.example .env      # create the local settings file if missing
grep -q '^ANTHROPIC_API_KEY=sk-' .env && echo "API key: already set (value not shown)" || echo "API key: NOT set yet"
```
If the key is **not** set, do **not** ask the scientist to paste it to you in the chat (that would put a
secret in the conversation). Instead tell them, in plain language, to add it to the `.env` file
themselves so it stays only on their machine:

- *(macOS)* `open -e .env`   *(Linux)* `nano .env`  — then find the line `ANTHROPIC_API_KEY=` and type
  their key right after the `=` (no spaces, no quotes), save, and close.
- They get a key at **console.anthropic.com → API keys**. It starts with `sk-ant-`.

Then verify **presence only** (never the value):
```bash
grep -q '^ANTHROPIC_API_KEY=sk-' .env && echo "API key detected ✅ (value not shown)" || echo "Still not detected — ask the scientist to re-check the .env line."
```

## Step 6 — Prove the install works (offline, free, ~2 seconds)

This runs the project's built-in check with **no key and no internet** — a deterministic self-test.

```bash
bash scripts/offline-bench.sh
```
If it ends with a pass/OK, the install is good. Report the result plainly. If it fails, **stop and show
the output** — do not edit anything to force it green.

---

# ✅ You're installed. Two ways to use it — set up the first, offer the second.

## Way 1 (always set up) — Private, local, one command → paste into Claude Science

Nothing is exposed on any network. The scientist runs one command with their **de-identified**
observation, and gets a ready-to-paste prompt.

```bash
bash mcp_server/ask.sh "33F, resistant hypertension on telmisartan + thiazide + CCB; raised CRP"
```
Tell the scientist: replace the example with your own **de-identified** observation (age band +
presentation only — **no names, MRNs, or dates**). It prints a short grounded brief plus a block called
**`claude_science_prompt`** — copy that block and paste it into a **Claude Science chat** to get the figure.

**Set this up and demonstrate it once.** This is the recommended everyday path — private by default.

## Way 2 (optional) — Run it as a real Claude Science *connector*

Ask the scientist: *"Do you also want TransBench to appear as a connector inside Claude Science, so its
agent can call it directly? This briefly opens a temporary secure web address to your computer while it's
running — nothing else is exposed. (You can skip this and just use Way 1.)"*

If **no**, skip to the Next-steps card using Way 1. If **yes**, let them pick **A or B** — explain both in
plain language and let them choose:

> **A — cloudflared (simplest): no signup, no account.** One command opens a temporary web address.
> Downside: the address is *throwaway* — it changes every time you restart, so you re-paste it into
> Claude Science each session. Best for trying it out. *(Most reliable of the two.)*
>
> **B — ngrok (steadier address): needs a free 2-minute signup.** After signing up once, you can get an
> address that **stays the same** across restarts. Downside: the one-time signup + a small settings file.

### Both options first need the server running (leave this terminal open):
```bash
bash mcp_server/run_http.sh    # serves TransBench locally on 127.0.0.1:8500 — keep it running
```
Start this, then open a **second** terminal in the same TransBench folder for the tunnel below.

### Option A — cloudflared
```bash
# Install (once):
#   (macOS)  brew install cloudflared
#   (Linux)  sudo mkdir -p --mode=0755 /usr/share/keyrings
#            curl -fsSL https://pkg.cloudflare.com/cloudflare-main.gpg | sudo tee /usr/share/keyrings/cloudflare-main.gpg >/dev/null
#            echo 'deb [signed-by=/usr/share/keyrings/cloudflare-main.gpg] https://pkg.cloudflare.com/cloudflared any main' | sudo tee /etc/apt/sources.list.d/cloudflared.list
#            sudo apt-get update && sudo apt-get install -y cloudflared

# Open the tunnel (the --http-host-header value is REQUIRED — it satisfies the server's security check):
cloudflared tunnel --url http://localhost:8500 --http-host-header "127.0.0.1:8500"
```
Watch the output for a line like `https://<random-words>.trycloudflare.com`. **That address + `/mcp`** is
the connector URL, e.g. `https://<random-words>.trycloudflare.com/mcp`. (On a restrictive network — some
campus or hospital Wi-Fi — if the tunnel won't connect, add `--protocol http2` to the command above.)

### Option B — ngrok
```bash
# Install (once):
#   (macOS)  brew install ngrok
#   (Linux)  curl -sSL https://ngrok-agent.s3.amazonaws.com/ngrok.asc | sudo tee /etc/apt/trusted.gpg.d/ngrok.asc >/dev/null
#            echo "deb https://ngrok-agent.s3.amazonaws.com bookworm main" | sudo tee /etc/apt/sources.list.d/ngrok.list
#            sudo apt-get update && sudo apt-get install -y ngrok
# Sign in (once): get a free token at dashboard.ngrok.com/get-started/your-authtoken, then:
ngrok config add-authtoken <PASTE_YOUR_TOKEN>

# Create the small settings file that satisfies the server's security check:
cat > traffic-policy.yml <<'YAML'
on_http_request:
  - actions:
      - type: add-headers
        config:
          headers:
            host: "127.0.0.1:8500"
YAML

# Open the tunnel:
ngrok http 8500 --traffic-policy-file traffic-policy.yml
# (Optional: for a URL that stays the same, add  --url=<your-free-domain>.ngrok-free.app )
```
ngrok prints a `https://<something>.ngrok-free.app` forwarding address. **That address + `/mcp`** is the
connector URL.

### 🔎 Confirm the connector is reachable BEFORE opening Claude Science (one line)
Replace `<CONNECTOR_URL>` with the full `https://…/mcp` from A or B:
```bash
curl -s -o /dev/null -w 'connector check: HTTP %{http_code}\n' <CONNECTOR_URL>
```
- **`406`** → 🎉 working. The whole chain (public address → tunnel → your computer) is good. Proceed.
- **`421`** → the security header isn't arriving. Re-check you included `--http-host-header "127.0.0.1:8500"`
  (Option A) or the `traffic-policy.yml` (Option B). If B keeps showing 421, use Option A instead.
- **`000` / no response** → the public address usually needs a few seconds to activate right after the
  tunnel starts (edge propagation). **Wait ~10 seconds and run the check again.** If it still shows `000`,
  the tunnel or the `run_http.sh` server isn't running — restart both.

---

# ✅ Next steps in Claude Science (show this card at the end)

**If you used Way 1 (local, recommended):**
1. Run `bash mcp_server/ask.sh "<your de-identified observation>"`.
2. Copy the **`claude_science_prompt`** block it prints.
3. Open a **Claude Science** chat and paste it → Claude Science loads the dataset and makes the figure.

**If you used Way 2 (connector):**
1. In **Claude Science** → **Add connector** → **Remote**.
2. Transport: **Streamable HTTP** · Auth: **None** · Name: **TransBench**.
3. URL: paste your **`https://…/mcp`** address (the one that returned `406` above).
4. Save. Then in a chat, ask Claude Science to run **`generate_experiment`** on a **de-identified**
   observation. (Keep the `run_http.sh` server + the tunnel running while you use it.)

> ⚕️ **Privacy reminder for the scientist:** TransBench's reasoning uses Anthropic's cloud API and queries
> PubMed, so the observation text you submit leaves your computer. **De-identify first** — age band +
> presentation only, never names, MRNs, or dates.

**That's it — you're done.** 🎉
