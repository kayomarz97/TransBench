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

## Reuse in another project

This playbook is a template. Keep the two headings **How to use it** and **Mistakes ledger** as-is,
replace the **Command cookbook** with your project's real commands, and let the ledger grow. The
`scripts/scan-secrets.sh` + `.githooks/pre-push` pair and the `.claude/agents/` definitions are
project-agnostic and copy straight across.
