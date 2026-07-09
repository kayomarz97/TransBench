# TransBench

A translational clinician↔bench research agent, shipped as an MCP connector
for Claude Science. A clinician pastes a free-text observation about
antihypertensive drugs; TransBench decomposes it into biological axes,
generates up to 3 falsifiable mechanistic hypotheses, grounds each in
retrieved PubMed evidence (support **and** contradiction), applies a novelty
guard so textbook facts are never shipped as "novel", and designs one
reproducible computational experiment — naming a concrete, resolvable public
dataset — ready to hand to Claude Science.

> **Research tool only.** TransBench never emits diagnosis, drug selection,
> or dosing guidance. Every response carries a fixed disclaimer:
> *"Research hypothesis generation only. Not clinical, diagnostic, or
> prescribing advice."*

This is a **standalone repository**. It reuses the mature grounding/retrieval
stack from the [Iatronix](../med-ai-project) backend as a **read-only**
dependency and never modifies it — see [Reusing Iatronix](#reusing-iatronix-read-only) below.

---

## Install

Requires **Python ≥3.11** (the reused Iatronix backend declares
`requires-python = ">=3.11"`) and [`uv`](https://docs.astral.sh/uv/).

```bash
cd /root/projects/transbench

# 1. Create the venv (the host's default `python3` may be 3.10 — too old).
uv venv --python 3.12

# 2. Install the Iatronix backend read-only, editable, and WITHOUT its full
#    dependency tree (--no-deps): only DB-free leaf functions are reused, so
#    asyncpg/pgvector/redis/firebase-admin/boto3/sentry/etc. are never pulled in.
uv pip install -e /root/projects/med-ai-project/backend --no-deps

# 3. Install the curated, lean dependency set the reused leaves actually need
#    (fastapi is required — llm_factory/stance_neutralizer import
#    fastapi.HTTPException):
uv pip install mcp langgraph langchain langchain-anthropic langchain-core \
    anthropic httpx "pydantic>=2" pydantic-settings pyyaml fastapi \
    json-repair tenacity python-dotenv

# 4. Editable self-install of this package (src/transbench/).
uv pip install -e .

# 5. Test tooling (not part of the runtime dependency set above).
uv pip install pytest pytest-asyncio
```

Verified installed footprint (no DB/cloud packages leaked in):
`iatronix-backend 0.1.0` (editable, from `/root/projects/med-ai-project/backend`),
`transbench 0.1.0` (editable, this repo), `mcp 1.28.1`, `langgraph`,
`langchain`/`langchain-anthropic`, `anthropic`, `fastapi`, `pydantic>=2`,
`pydantic-settings`, `pyyaml`, `json-repair`, `tenacity`, `python-dotenv`,
`httpx`. See `PLAN.md` (Phase 0) for the full path-decision writeup and
`pyproject.toml` for the frozen dependency list (`iatronix-backend` is
declared there too, resolved via `[tool.uv.sources]` as an editable path
dependency).

### `.env`

Copy `.env.example` to `.env` and fill in your own keys — **never commit
real keys** (`.env` is already gitignored):

```bash
cp .env.example .env
```

| Key | Required | Purpose |
|---|---|---|
| `ANTHROPIC_API_KEY` | yes | BYOK key for the **engine's own** Anthropic calls (`create_llm(..., user_key=...)`). Independent of Claude Science, which is only this tool's MCP *client* — it never sees this key. No fallback key exists; without it every LLM-calling agent fails cleanly. |
| `PUBMED_API_KEY` | no | Raises NCBI/PubMed rate limits. |
| `LLM_TEMPERATURE` | yes (`=0`) | Forces deterministic LLM clients. `create_llm` has no `temperature` kwarg — it builds clients at `settings.llm_temperature`, read from this env var (belt 1 of 2; belt 2 is `.bind(temperature=0)` on every client in `agents.py`). |
| `PYTHONDONTWRITEBYTECODE` | yes (`=1`) | Prevents Python from writing `.pyc`/`__pycache__` during imports, so importing the editable-installed Iatronix backend never writes into its (read-only) tree. Set this in your shell/process env for **every** TransBench invocation (tests, the engine, the MCP server), not only in `.env` — a few entry points read it before `.env` would even be loaded. |

No other environment variable is required at import time — every field of
Iatronix's own `Settings` has a default (verified in Phase 0/Phase −1), so
`DATABASE_URL`/`REDIS_URL`/`ENCRYPTION_KEY`/etc. are never needed.

---

## Running the MCP server

```bash
# stdio -- what Claude Science actually spawns (see below)
bash mcp_server/run_stdio.sh

# HTTP fallback (streamable-http), localhost:8500
bash mcp_server/run_http.sh
```

Both scripts `cd` into the repo root, export `PYTHONDONTWRITEBYTECODE=1` +
`PYTHONPATH=<repo>/src`, and select the transport via `MCP_TRANSPORT`. Two
tools are exposed, both calling `transbench.engine.run_transbench` directly
(no duplicated retrieval/grounding logic):

- **`generate_experiment(observation, focus_drug="")`** — the showpiece.
  Returns a schema-valid `TransBrief`: decomposed axes, up to 3 graded
  hypotheses with real PubMed citations and an auditable novelty verdict,
  and `top_experiment` — one runnable computational experiment naming a
  concrete, resolvable dataset plus a `claude_science_prompt`.
- **`search_grounded_evidence(question)`** — utility/fallback. The *same*
  engine run, reshaped into a lighter grounded-evidence-only projection.

See `mcp_server/README.md` for SDK/transport details, the error-response
shape, and per-call cost notes (~13 LLM calls + live PubMed + a live GEO
content-verification fetch per invocation).

### Registering in Claude Science

Full walkthrough (SSH-tunnel-from-Windows-laptop-to-headless-Linux-server
setup, connector registration, demo script, fallback plan):
**[`CLAUDE_SCIENCE_SETUP.md`](CLAUDE_SCIENCE_SETUP.md)**. Connector-specific
registration details (the exact `mcpServers` JSON block, prerequisites,
manual/HTTP fallback) also live in **[`mcp_server/README.md`](mcp_server/README.md)**.
In short: TransBench registers as a local **stdio** MCP server whose
`command` is this repo's **own venv** Python
(`/root/projects/transbench/.venv/bin/python -m mcp_server.server`, `cwd`
= this repo root, `PYTHONDONTWRITEBYTECODE=1` set in the connector's own
`env` block) — that venv is what has `mcp`/`langgraph`/`langchain-anthropic`
and the read-only editable-installed Iatronix backend all resolvable
together.

---

## The flagship demo

Input (a real, unresolved area in resistant-hypertension research — the
agent grounds real PMIDs at runtime, never hardcoded citations):

> *"58F, resistant hypertension despite ACEi + CCB + thiazide at max dose;
> elevated hs-CRP; poor response to RAAS blockade."*

Call `generate_experiment` with that observation (via Claude Science, the
HTTP fallback, or directly in Python — see below). It returns a grounded
`TransBrief` whose `axes` include `immune_inflammatory` (elevated hs-CRP +
resistant hypertension), up to 3 falsifiable hypotheses each graded against
real retrieved evidence, and a `top_experiment` naming a concrete dataset
(a disease-matched GEO series when the model's own proposal is
independently re-verified to actually match its claimed content — not just
that the accession *resolves* — or the pinned, guaranteed-resolvable Tabula
Sapiens immune/kidney compartment otherwise) with an ordered protocol and a
`top_experiment.claude_science_prompt` ready to run in Claude Science to
produce a reproducible figure.

A real captured run of this exact observation (Phase 7 QA): 3 hypotheses
(aldosterone-breakthrough/chymase, CD8⁺ effector-memory T-cell/eNOS/NOX2,
and a SLC12A3/WNK1/ENaC pharmacogenomic axis), 13 LLM calls total, 29
deduplicated references, and a fully-specified scRNA-seq co-expression
protocol against the Tabula Sapiens kidney compartment for the selected
hypothesis (its own model-proposed GEO accession was caught by the
dataset-content-verification gate as a real-but-unrelated record and
correctly rejected before ever reaching the final plan).

### Manual-paste fallback

If the live connector misbehaves during a demo, the payoff is one paste away
regardless:

1. `bash mcp_server/run_http.sh` (or run the engine directly, see below).
2. Call `generate_experiment` over HTTP, or in a Python shell:
   ```python
   import asyncio
   from transbench.engine import run_transbench
   brief = asyncio.run(run_transbench("<your observation>"))
   print(brief.top_experiment.claude_science_prompt)
   ```
3. **Paste `top_experiment.claude_science_prompt` directly into a Claude
   Science chat** — same reproducible-figure payoff, zero dependency on the
   live connector working.

---

## Deterministic demo / snapshot mode

`TRANSBENCH_MODE` is an optional env var that toggles how `run_transbench`
sources its output — read directly off the process env inside
`transbench.engine.run_transbench` itself, so it applies to **every**
caller, including the MCP tools (`generate_experiment`/
`search_grounded_evidence`), with zero code changes anywhere else:

| `TRANSBENCH_MODE` | Behavior |
|---|---|
| `live` (default, or unset) | The normal full 8-agent pipeline — completely unchanged. |
| `golden` | Returns a pre-captured, complete `TransBrief` **verbatim**, instead of running the pipeline at all — for a fully deterministic demo replay. |
| `snapshot` | Runs the **real** pipeline (real decompose/hypothesize/grade/entailment/novelty/design/assemble LLM calls) but replays PubMed retrieval from a bundled snapshot instead of hitting PubMed live — fixed evidence, live LLM reasoning on top of it. |

Two optional path env vars point at the bundled files (a relative path
resolves against the repo root; both already have working defaults, checked
into `snapshots/`):

| Env var | Default |
|---|---|
| `TRANSBENCH_GOLDEN_BRIEF` | `snapshots/flagship_golden_brief.json` |
| `TRANSBENCH_RETRIEVAL_SNAPSHOT` | `snapshots/flagship_retrieval_snapshot.json` |

**Safety guards — neither mode can silently serve the wrong content:**

- `golden` mode returns the golden brief only if the caller's `observation`
  matches the golden brief's own `request_echo` (normalized:
  strip/lower/collapse-whitespace). A mismatch — or a missing/invalid golden
  file — logs a clear warning and transparently falls back to running the
  live pipeline instead.
- `snapshot` mode's replay is keyed by hypothesis id **and** guarded by the
  hypothesis's own `statement`: each bundled snapshot entry also stores the
  exact hypothesis statement it was captured for, and a given hypothesis's
  evidence is only replayed if the *current* run's hypothesis statement
  matches (normalized) — otherwise that one hypothesis transparently falls
  back to live PubMed retrieval (logged), so a fresh hypothesis that happens
  to reuse an old id can never be served evidence captured for a different
  hypothesis. (This is the same underlying `retrieval_snapshot` replay
  mechanism `TransRequest.retrieval_snapshot` has used since Phase 5 —
  BUILD_SPEC.md §9 — now with this extra statement-match guard layered on
  top of the original id-only keying.)

Usage — via the engine directly:

```bash
TRANSBENCH_MODE=golden PYTHONDONTWRITEBYTECODE=1 .venv/bin/python -c "
import asyncio
from transbench.engine import run_transbench
brief = asyncio.run(run_transbench(
    '58F, resistant hypertension despite ACEi + CCB + thiazide at max dose; '
    'elevated hs-CRP; poor response to RAAS blockade.'
))
print(brief.top_experiment.claude_science_prompt)
"
```

Or via the MCP connector — set `TRANSBENCH_MODE` (and, if needed, the two
path env vars above) in the connector's own registration `env` block
alongside `ANTHROPIC_API_KEY`/`PYTHONDONTWRITEBYTECODE` (see
[`mcp_server/README.md`](mcp_server/README.md)'s register-block JSON) —
`generate_experiment`/`search_grounded_evidence` pick it up automatically,
no other change required.

The two bundled files under `snapshots/` were captured from one real,
live flagship run (`fixtures.FLAGSHIP_OBSERVATION`) and contain only public
PubMed metadata plus the generated brief itself — no API keys or secrets.

---

## Reusing Iatronix (read-only)

TransBench never edits Iatronix. It only imports **DB-free leaf functions**
(`fetch_evidence_data`, `fetch_drug_data`, `rank_article_list`,
`build_article_registry`, `grounding_stats`/`strip_ungrounded`,
`has_minimum_evidence`/`ensure_evidence`, `validate_citations`,
`create_llm`, `neutralize_query`) via the single seam `src/transbench/reuse.py`
— never `run_search_graph`, `semantic_cache`, or `vector_search` (those need
pgvector/redis). The Iatronix backend is installed **editable** into this
repo's own venv (`uv pip install -e <IATRONIX>/backend --no-deps`), so
`from app.services... import ...` resolves to the live, read-only source
tree — nothing is ever written there. This is enforced by a **baseline-diff**
guard (`tests/test_iatronix_untouched.py`, run under
`PYTHONDONTWRITEBYTECODE=1`): it snapshots `git -C <IATRONIX_PATH> status
--porcelain` at the true start of every test session and hard-fails on any
new delta (not an absolute-empty assertion — Iatronix legitimately carries
unrelated untracked files). Verified clean across every phase of this build,
including every live pipeline run made during Phase 7 QA.

---

## Development / running the tests

```bash
PYTHONDONTWRITEBYTECODE=1 /root/projects/transbench/.venv/bin/python -m pytest -q tests/
```

Most of the suite is fully offline/deterministic (fake-LLM doubles, pure
functions, or real-but-free NCBI-only network calls). A handful of tests
make real, live Anthropic + PubMed calls and skip cleanly without a working
`ANTHROPIC_API_KEY`; these all share **one** live flagship pipeline run via
a session-scoped fixture (`tests/conftest.py::flagship_brief`) rather than
each independently re-running the ~13-call pipeline, so running the full
suite costs a small, bounded number of live calls, not one per acceptance
test. See `tests/conftest.py`'s module docstring for the full rationale.

Key test files:

| File | Covers |
|---|---|
| `tests/test_reuse_imports.py` | Phase 0 smoke test — the DB-free leaves import via the seam, in-venv. |
| `tests/test_iatronix_untouched.py` | **The Iatronix-safety guard** — baseline-diff, hard-fails on any new delta. |
| `tests/test_grounding.py` | Exact-shape grounding-gate pseudo-response; grounded item survives, sourceless stripped. |
| `tests/test_novelty.py` | "ACE inhibitor causes dry cough" → `established`, never promoted to an experiment. |
| `tests/test_schema.py` | `TransBrief` validates for all 3 `tests/fixtures.py` demo inputs; `top_experiment.dataset_pointer` present. |
| `tests/test_cost.py` | ≤3 hypotheses, ≤`ABSTRACT_CAP` abstracts/hypothesis, entailment batched-per-hypothesis (not per-item). |
| `tests/test_mcp_parity.py` | The MCP tools faithfully pass through the engine's brief; retrieval-snapshot replay is deterministic with zero network calls. |
| `tests/test_agents_phase2.py` / `test_retrieval_phase3.py` / `test_experiment_phase5.py` | Per-phase acceptance tests (decompose/hypothesize; retrieval+grading+grounding; experiment design + full brief assembly). |
| `tests/test_pubmed_query_builder.py` / `test_rigor_entailment_correlation.py` | Fully offline regression guards for the PubMed query builder and the entailment-correlation fix. |
| `tests/test_snapshot_toggle.py` | `TRANSBENCH_MODE=live\|golden\|snapshot` (see [Deterministic demo / snapshot mode](#deterministic-demo--snapshot-mode)) — fully offline/deterministic, zero live calls. |

---

## Repo layout

```
transbench/
├─ src/transbench/        # config, schemas, prompts, reuse seam, 8 agents, rigor, LangGraph engine
├─ mcp_server/             # FastMCP server (stdio + HTTP), run scripts, connector manifest
├─ tests/                  # fixtures + full test suite (see above)
├─ snapshots/               # bundled golden brief + retrieval snapshot (see "Deterministic demo / snapshot mode")
├─ BUILD_SPEC.md            # full design spec (reuse strategy, schemas, agents, prompts, rigor, MCP, demo)
├─ KICKOFF.md               # phase-by-phase build plan + non-negotiable rules
├─ CLAUDE_SCIENCE_SETUP.md  # Claude Science connector registration walkthrough
├─ PLAN.md                  # Phase 0 path-decision + phase-by-phase execution log
└─ .env.example
```

## Scope / status

Phases 0–7 complete. `generate_experiment` returns a grounded, cited
`TransBrief` whose `top_experiment` is a runnable scRNA-seq/Perturb-seq
analysis naming a resolvable dataset with a `claude_science_prompt`; the MCP
server serves it over stdio (Claude Science) and HTTP (fallback); the
Iatronix baseline-diff guard shows no new delta. Claude Science actually
*executing* a `claude_science_prompt` against a dataset is a demo-day path
(the beta app itself, external to this repo) with the HTTP + manual-paste
fallback above — it is not a code-correctness gate for this repo.
