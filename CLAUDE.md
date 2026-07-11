# CLAUDE.md — Working Agreement for TransBench

> The rules Claude Code follows **every session** in this repo. Kept short on purpose: a long
> file gets half-ignored. Project-agnostic parts are reusable — see [Reuse](#reuse-in-another-project).
> This complements the user's global `~/.claude/CLAUDE.md`; it does not repeat it.

---

## Project at a glance (the WHAT)

TransBench turns a clinician's bedside observation into a grounded, testable, bench-ready
experiment, shipped as an **MCP connector for Claude Science**. Python 3.11+, `uv`, src-layout,
an 8-agent LangGraph engine, three rigor gates.

**Directories that matter** (point, don't describe):
- `src/transbench/` — the engine, agents, rigor gates, and MCP server code. **Edit here.**
- `src/vendored/` — a read-only mirror of the Iatronix backend. **Never edit; treat as a library.**
- `tests/` — pytest suite (runs offline). `snapshots/` — committed golden runs (replayed with no key).
- `config/`, `mcp_server/`, `docs/` — config, server entry, and asset/generator docs.

**Run modes:** `live` (real API, costs money) · `snapshot` (record/replay) · `golden`
(offline replay, **no API key, deterministic** — this is our bench).

## Commands (exact — don't guess)

```bash
uv sync                        # install (vendored backend, no external paths)
bash scripts/offline-bench.sh  # THE offline bench — no key, no network, deterministic (~2s). Verify with this.
.venv/bin/python -m pytest -q  # full suite — ⚠️ runs LIVE (real API $$) if a key sits in .env; only with intent
```
Running the MCP server / registering in Claude Science: see `CLAUDE_SCIENCE_SETUP.md` and
`README.md`. More one-liners live in `.claude/AGENTS_PLAYBOOK.md` (read it on demand — it is
deliberately **not** auto-loaded, to save context).

---

## Rules (non-negotiable)

1. **Production & scalable only.** No temporary patches, no "quick fix for now." Diagnose the
   root cause and fix it so it still holds as the app grows. If a real fix is large, say so and
   propose it — don't ship a stopgap silently.
2. **Read the real docs before using any service.** For any external API, library, or tool
   (Anthropic SDK, MCP, LangGraph, GEO/PubMed, deploy targets), use the **`docs-researcher`**
   agent to read the *official* docs and record the exact signatures/params in the playbook.
   Never invent an API shape from memory.
3. **Branching.** Always work on and push to **`dev`**. **Never push to `main`** — the user
   merges to `main` manually. Never force-push a shared branch.
4. **Verify before "done."** After any code change, run the **offline bench**
   (`offline-bench-runner` agent). Report pass/fail with the actual output. Never claim something
   works without running it. If tests fail, say so plainly — do not quietly edit tests to pass.
5. **Secrets & privacy — hard stop.** Never commit secrets, API keys, tokens, or personal data.
   `.env`, `*.local.md`, and demo notes are gitignored; keep them that way. Before **every** push,
   run the **`secret-scanner`** agent; the `.githooks/pre-push` hook is the automated backstop.
   If anything is flagged, **STOP, show it, and ask** — never push through it.
6. **Explain in two registers.** The user is a physician, not a software engineer. Lead with a
   plain-language explanation anyone can follow, **then** give the precise technical detail. Both,
   every time — not one or the other.
7. **Options always include a free-typed answer.** When you ask the user to choose, use
   `AskUserQuestion` (its "Other" field lets them type their own), and say so: "…or type your own."
   Never trap the user in a fixed menu.
8. **Token & tool discipline.** Batch independent tool calls into one step. Prefer targeted reads
   over whole files; don't re-read what you've seen. Spawn a subagent only when the task genuinely
   fans out. Don't auto-load the playbook. Wasted tokens are a bug.

---

## The agents (and exactly when to use them)

| Agent | Trigger — use it when… | What it does |
|-------|------------------------|--------------|
| **`docs-researcher`** | about to touch an unfamiliar API/service/library | Reads official docs, extracts exact params/tool schemas/version constraints, records them in the playbook. |
| **`offline-bench-runner`** | you changed code and need to confirm nothing broke | Runs `pytest` + a golden-mode smoke run (offline, no key), reports pass/fail honestly. |
| **`secret-scanner`** | before any `git push` (or when unsure what's staged) | Scans staged/tracked content for secrets & PII, flags findings, asks before proceeding. |
| **`deep-reasoning`** | a problem is genuinely hard (subtle correctness, tricky algorithm, gnarly multi-file diagnosis, architecture tradeoff) | Runs on **Fable** (a more capable tier than the Opus main) and returns a rigorous, evidence-backed solution + reasoning. Delegate the hard 10%; keep routine work on the main session. |

Definitions live in `.claude/agents/`. Invoke via the Agent/Task tool by name.

**Model routing.** Run the main session on **Opus** (`claude --model opus`) so routine work stays fast
and cheap, and **escalate genuinely hard, self-contained reasoning to the `deep-reasoning` sub-agent
(Fable)**. Don't over-delegate — most work should finish on the main session; reach for Fable only when
the problem is actually hard, and hand it the exact files/symptoms (it starts with fresh context).

## Learn from mistakes (self-updating)

When something breaks, a rule is discovered, or a command proves fiddly, append it to the
**Mistakes ledger** or **command cookbook** in `.claude/AGENTS_PLAYBOOK.md` — not to this file.
That keeps this agreement short and turns every stumble into a permanent guardrail.

## Reuse in another project

Everything here is project-agnostic **except** the "Project at a glance" and "Commands" sections
above. To reuse: copy `CLAUDE.md`, `.claude/agents/`, `.claude/AGENTS_PLAYBOOK.md`, `scripts/`,
and `.githooks/`, then swap those two sections and re-point the bench command. Activate the hook
with `git config core.hooksPath .githooks`.
