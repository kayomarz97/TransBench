# TransBench — Path-Labeled Build Plan

Status as of Phase 0 completion: **PATH A CONFIRMED AND LOCKED.**
Owner of this document: `transbench-architect` (Phase 0). Updated by later phases only to
check off completed work — the phase *contracts* below are frozen unless the orchestrator
and `opus-verifier` agree to change BUILD_SPEC.md/KICKOFF.md first.

---

## 0. Path decision (the headline)

**Path A — install Iatronix as a lean, editable, `--no-deps` dependency of this repo's own
venv, then add only the curated light dependency set.** Reproduced and locked in Phase 0:

- `uv venv --python 3.12` → `.venv` Python 3.12.12 (≥3.11 required by `iatronix-backend`'s
  `requires-python = ">=3.11"`; host default `python3` is 3.10.12 and would have been rejected).
- `uv pip install -e /root/projects/med-ai-project/backend --no-deps` → installs exactly one
  package (`iatronix-backend==0.1.0`), zero transitive deps.
- `uv pip install mcp langgraph langchain langchain-anthropic langchain-core anthropic httpx
  "pydantic>=2" pydantic-settings pyyaml fastapi json-repair tenacity` → 55 packages, all light
  (web/LLM/schema stack only). Verified absent from the venv: `asyncpg`, `pgvector`, `redis`,
  `firebase-admin`, `boto3`, `sqlalchemy`, `dspy-ai`, `sentry-sdk`, `alembic`, `gunicorn`,
  `bcrypt`, `slowapi` — none of Iatronix's DB/cloud tree leaked in.
- `tests/test_reuse_imports.py` — **11/11 passed** with the plain, documented invocation
  (`PYTHONDONTWRITEBYTECODE=1 .venv/bin/python -m pytest -q tests/test_reuse_imports.py`) run
  under this session's **normal, unscrubbed ambient env** (`ANTHROPIC_API_KEY` genuinely set, as
  it is for every later phase's real BYOK calls). The "no env var required at import" claim is
  proven *inside* `test_no_env_vars_required_at_import` by importing all 8 leaves in a **child
  subprocess whose own environment is explicitly scrubbed** (only `PATH` +
  `PYTHONDONTWRITEBYTECODE=1` — no `DATABASE_URL`/`REDIS_URL`/`ENCRYPTION_KEY`/
  `ANTHROPIC_API_KEY`/`SENTRY_DSN` passed through) and asserting `returncode == 0` — the test
  makes **no assertion about this pytest process's own ambient env**, so it is correct and green
  regardless of what's exported in the invoking shell. (Phase 0 originally got this backwards —
  it asserted the *parent* shell lacked `ANTHROPIC_API_KEY`, which is wrong: that var is a
  legitimate, normally-set BYOK runtime var, BUILD_SPEC §0.4 — Opus verification caught this and
  it was fixed before Phase 0 closed.) All 8 DB-free leaves import; `resolve_provider` correctly
  maps `claude-sonnet-4-6` and `claude-haiku-4-5-20251001` to `"anthropic"`, with and without
  explicit `user_provider="anthropic"`.
- `import app` resolves to `/root/projects/med-ai-project/backend/app/__init__.py` — i.e. the
  installed package **is** the live Iatronix source tree (editable install), not a copy. This is
  what makes `provider_registry`'s `Path(__file__).resolve().parents[2] / "config" /
  "providers.yaml"` resolution work without any vendoring gymnastics (confirmed by reading
  `app/services/provider_registry.py`).
- Iatronix baseline-diff guard: BEFORE and AFTER `git -C /root/projects/med-ai-project status
  --porcelain` snapshots are **byte-identical** (only the 2 pre-existing unrelated untracked
  files `plans/promote-dev-to-prod-2026-06-15.md` and `plans/update-readme-2026-06-15.md`);
  `git -C /root/projects/med-ai-project diff --quiet` exits 0 both times. No new `__pycache__`/
  `.pyc` timestamps appeared anywhere under the Iatronix backend during install + test.

**Path B (vendoring) was NOT needed and is NOT created.** `vendored/` stays absent from this
repo; `reuse.py` (Phase 1) still carries the `try/except ImportError` fallback shape from
BUILD_SPEC §2 for spec fidelity and future-proofing, but its `except` branch (`from vendored
import ...`) is dead code today — Python never evaluates it because the `try` branch succeeds.
If a future Iatronix change ever breaks Path A, that's the seam that absorbs it; until then it
is inert.

**Import-time env requirement: NONE.** Every `Settings` field in `app/config.py` has a literal
default (confirmed by reading the full class — zero bare `field: Type` declarations, all are
`field: Type = default`); `model_config = {"env_file": ".env", "extra": "ignore"}` means a
missing `.env` is also fine. `.env.example` documents `ANTHROPIC_API_KEY`, `PUBMED_API_KEY`,
`LLM_TEMPERATURE=0`, `PYTHONDONTWRITEBYTECODE=1` as the vars TransBench itself needs at
**runtime** (BYOK key + determinism + the bytecode guard) — none of these are import-time
requirements of the reused leaves.

---

## 1. Guardrails (enforced from Phase 0 onward; every later phase inherits these unmodified)

| # | Rule | Enforcement mechanism |
|---|---|---|
| 1 | Iatronix never modified | **Baseline-diff** guard (`git -C <IATRONIX_PATH> status --porcelain` before/after, assert identical; `git diff --quiet`) — NOT assert-empty, because Iatronix already carries 2 unrelated untracked files. `PYTHONDONTWRITEBYTECODE=1` on every TransBench process so imports never drop `.pyc` into the Iatronix tree. Proven in Phase 0 (§0 above); formalized as `tests/test_iatronix_untouched.py` in Phase 7. |
| 2 | All code stays in `/root/projects/transbench` | Nothing is ever written with an absolute path under `/root/projects/med-ai-project`. Editable install only *reads* Iatronix source; `uv pip install -e ... --no-deps` writes only into `.venv/` and this repo's own metadata. |
| 3 | Reuse only DB-free leaves | Only these 8 import surfaces are ever touched: `fetch_evidence_data`, `fetch_drug_data`, `build_article_registry`, `rank_article_list`, `grounding_stats`/`strip_ungrounded`/`grounded_ratio`, `has_minimum_evidence`/`ensure_evidence`, `validate_citations`, `create_llm`, `neutralize_query`. `run_search_graph`, `semantic_cache`, `vector_search` are **never** imported anywhere in this repo — `tests/test_reuse_imports.py::test_never_imports_db_or_redis_backed_leaves` AST-parses the test file itself and asserts none of the forbidden names appear in any `import`/`from` statement (self-documenting, not just tribal knowledge). |
| 4 | Wrap `EvidenceFetchResult` in `FetchedData` | `reuse.py`/`agents.py` (Phase 1/3) must call `FetchedData(query_type="evidence", evidence_data=result_or_merged)` before ever calling `has_minimum_evidence`, `ensure_evidence`, or `build_article_registry` — confirmed from source (`evidence_floor.py` type-hints `fetched_data: "FetchedData | None"` and reads `.drug_data`/`.evidence_data`, not the raw `EvidenceFetchResult` fields). |
| 5 | BYOK, real model ids | `create_llm(model_id, user_key=key, user_provider="anthropic")` only; ids `claude-sonnet-4-6` (reasoning) / `claude-haiku-4-5-20251001` (mechanical) — both confirmed present under the `anthropic:` block of `config/providers.yaml` and both confirmed to resolve to `"anthropic"` via `resolve_provider` (Phase 0 test). Never the retired `claude-sonnet-4-20250514` (Iatronix's own *internal* `model_sonnet` default — NOT what TransBench passes) and never bare `claude-sonnet`/`claude-haiku`. `create_llm` raises `fastapi.HTTPException` on bad key/model — every call site (Phase 2+) must catch it. |
| 6 | Research tool, not clinical | `TransBrief.disclaimer` fixed field (BUILD_SPEC §4); prompts (Phase 2/5) explicitly forbid diagnosis/selection/dosing language. |
| 7 | Grounded or it doesn't ship | `rigor.py` (Phase 4): batched per-hypothesis entailment + exact-shape `{"sections":[{"content_items":[...]}]}` pseudo-response into `grounding_stats`/`strip_ungrounded` + novelty guard; 0 grounded supporting items → `grounded=False` → excluded from experiment stage (demoted, never deleted). |
| 8 | temp=0 | `LLM_TEMPERATURE=0` in `.env`/`.env.example` (Iatronix `Settings.llm_temperature` reads it) **and** `.bind(temperature=0)` on every client `create_llm` returns (it has no `temperature` kwarg itself) — belt-and-suspenders per BUILD_SPEC §0.7. |
| 9 | ≤3 hypotheses, ≤~8 abstracts/hypothesis | `config.py` (Phase 1) constants `MAX_HYPOTHESES = 3`, `ABSTRACT_CAP = 8`; enforced at the hypothesize (Phase 2) and retrieval-slice (Phase 3) call sites; `tests/test_cost.py` (Phase 7) asserts the call-count bound this implies (§9 of BUILD_SPEC: ≈13 LLM calls/run). |

---

## 2. Phase-by-phase plan against the exact KICKOFF layout

Legend: **[owner]** = subagent that authors the file. **Path** column marks whether the file
depends on the Path A vs Path B decision (`A` = written assuming Path A install; `N/A` =
path-independent). All phases gate on an `opus-verifier` PASS before advancing (Sonnet-build /
Opus-verify loop, KICKOFF "YOUR ROLE").

### Phase −1 — Plan audit — **DONE** (Claude Fable 5, before this session)
Audited BUILD_SPEC.md/KICKOFF.md/agent files + real Iatronix source; produced the RISK
REGISTER and corrected the plan files in place (Path A decision, batched-not-per-item
entailment, `FetchedData` wrapping gotcha, real model ids, baseline-diff guard framing). No
code. Not reproduced here beyond re-deriving its conclusions from source in Phase 0 (§0 above).

### Phase 0 — Reuse smoke test + plan — **DONE (this document)** — **[transbench-architect]**
| File | Owner | Path | Status |
|---|---|---|---|
| `pyproject.toml` | transbench-architect | A | done — `iatronix-backend` in `[project].dependencies` + `[tool.uv.sources]`; curated deps only; `[dependency-groups] dev = ["pytest","pytest-asyncio"]` added (test-only, not in the frozen runtime list — see §3 deviation note) |
| `.gitignore` | transbench-architect | N/A | done |
| `.env.example` | transbench-architect | N/A | done |
| `tests/test_reuse_imports.py` | transbench-architect | A | done — 11/11 pass |
| `PLAN.md` | transbench-architect | N/A | done (this file) |
| `.venv/` (Python 3.12) | transbench-architect | A | done, gitignored |

Accept (all met): `.venv` Python ≥3.11 ✓; install succeeds, `app` importable from the venv ✓;
`PYTHONDONTWRITEBYTECODE=1 .venv/bin/python -m pytest -q tests/test_reuse_imports.py` passes ✓;
Iatronix baseline-diff shows no new delta ✓; `PLAN.md` committed on `dev` ✓ (this commit).
→ **Next: Opus verifies this plan + the import choice before Phase 1 starts.**

### Phase 1 — Skeleton — **[transbench-engine]**
| File | Path |
|---|---|
| `src/transbench/__init__.py` | N/A |
| `src/transbench/config.py` — model ids (`claude-sonnet-4-6`/`claude-haiku-4-5-20251001`), `MAX_HYPOTHESES=3`, `ABSTRACT_CAP=8`, concurrency cap 3, env reads | N/A |
| `src/transbench/schemas.py` — BUILD_SPEC §4 Pydantic v2 models verbatim (`TransRequest`, `Reference`, `DecomposedAxis`, `Hypothesis`, `EvidenceItem`, `GradedHypothesis`, `ExperimentPlan`, `TransBrief`) | N/A |
| `src/transbench/prompts.py` — BUILD_SPEC §5 prompt skeletons verbatim (can be written in full even though wiring lands in later phases) | N/A |
| `src/transbench/reuse.py` — the single seam (BUILD_SPEC §2 code block): `try` imports the 8 leaves from `app.services.*`; `except ImportError` falls back to `vendored` (present in source for spec fidelity; unreachable under Path A — no `vendored/` package exists, which is fine, Python only evaluates the `except` body if the `try` raises) | **A** |
| `src/transbench/graph.py` + `engine.py` — stub `run_transbench` that echoes input into a stub `TransBrief` | N/A |
| `tests/test_schema.py` (stub-level) | N/A |
| `tests/fixtures.py` — flagship observation text (BUILD_SPEC §8) first needed by Phase 2's acceptance test; created here or at latest by Phase 2 | N/A |

Accept: `python -c "from transbench.engine import run_transbench"` works; `test_schema` passes
on the stub. → Opus.

### Phase 2 — Agents 1–2 — **[transbench-engine]**
`src/transbench/agents.py`: `run_decomposer` (Haiku, `model_cheap`), `run_hypothesis_generator`
(Sonnet, `model_reasoning`) — both strict-JSON with `json_repair` fallback, both built via
`create_llm(...).bind(temperature=0)`, both `await llm.ainvoke(...)` (never sync `.invoke()` in
the async path). `graph.py` wires `decompose → hypothesize` nodes. `tests/fixtures.py` gets (or
already has, from Phase 1) the flagship observation.

Accept: flagship → ≥2 axes incl. `immune_inflammatory` + ≤3 falsifiable hypotheses. → Opus.

### Phase 3 — Retrieval + grading (agents 3–4) — **[transbench-engine]**
`agents.py` gains `run_evidence_retriever` (no LLM — BUILD_SPEC §3 flow: `neutralize_query` →
`fetch_evidence_data` support pass + contradiction pass → merge → **wrap in `FetchedData`** →
`has_minimum_evidence`/`ensure_evidence` → `rank_article_list` on raw abstract dicts →
`build_article_registry(fd)` → resolve each ranked article's `Reference` via `registry.by_pmid`,
skip if absent) and `run_evidence_grader` (Haiku, one batched call per hypothesis over its ≤8
abstracts, then `validate_citations(response_data, "evidence", fetched_data=fd)` honoring its
in-place `__drop__`/references mutation). `graph.py` fans hypotheses out with
`asyncio.gather(..., concurrency<=3)`. `run_manifest` starts accumulating PMIDs+abstracts here.

Accept: each hypothesis returns ≥1 real PMID-backed `EvidenceItem`. → Opus.

### Phase 4 — Rigor + novelty (agents 5–6) — **[transbench-engine]**
`agents.py` gains `run_novelty_checker` (Sonnet, strict verdict + PMID-citing `novelty_reason`).
`src/transbench/rigor.py` — dedicated batched-per-hypothesis entailment (1 Haiku call/hypothesis
classifying all ≤8 items supports/refutes/unclear; fanned out, cap 3) + the **exact-shape**
`{"sections":[{"content_items":[{pmid,url,source}...]}]}` pseudo-response into
`grounding_stats`/`strip_ungrounded` + novelty guard (established → never promoted). New tests:
`tests/test_grounding.py` (grounded item survives, sourceless stripped),
`tests/test_novelty.py` (ACEi-cough → established → blocked).

Accept: `test_grounding` + `test_novelty` pass. → Opus.

### Phase 5 — Experiment + assembler (agents 7–8) — **[transbench-engine]**
`agents.py` gains `run_experiment_designer` (Sonnet, ExperimentPlan for the top
open_question+grounded hypothesis; concrete resolvable `dataset_pointer`, default Tabula
Sapiens immune compartment, never a fabricated accession — feasibility_notes explains any
fallback) and `run_brief_assembler` (Haiku, final `TransBrief`; references via
`registry.to_reference_list()`; `run_manifest` filled with models/temps/neutral
queries/PMIDs/timestamps/token spend). `graph.py`/`engine.py` complete the full
`StateGraph`(START→decompose→hypothesize→fan-out retrieve/grade/novelty→rigor→design→assemble→
END). `tests/test_schema.py` extends to validate the full `TransBrief`.

Accept: flagship → `ExperimentPlan` with a **verified-resolvable** `dataset_pointer`, runnable
`protocol_steps`, `claude_science_prompt`; full `TransBrief` validates; `run_manifest` carries
the retrieval snapshot. → Opus.

### Phase 6 — MCP server — **[transbench-mcp]**
| File | Path |
|---|---|
| `mcp_server/server.py` — FastMCP; `generate_experiment(observation, focus_drug="")`, `search_grounded_evidence(question)`; both `await` the async engine (`ainvoke`), both **catch `fastapi.HTTPException`** from `create_llm` → clean structured error; `ANTHROPIC_API_KEY` env feeds the engine | A (confirms Phase 0's bonus finding: `mcp`/`FastMCP` import cleanly in this venv — reconfirmed live in Phase 0, `mcp.server.fastmcp.FastMCP` importable) |
| `mcp_server/manifest.json`, `run_stdio.sh`, `run_http.sh`, `mcp_server/README.md` | N/A |

First checks the installed `mcp` SDK's actual transport support (stdio always; HTTP =
sse/streamable-http per version) before wiring the HTTP fallback.

Accept: stdio server starts; a local MCP client call to `generate_experiment` returns a
schema-valid brief; HTTP fallback starts. → Opus.

### Phase 7 — QA + merge — **[transbench-qa]** then merge
| File | Owner |
|---|---|
| `tests/test_iatronix_untouched.py` — formalizes the Phase 0 ad hoc baseline-diff proof (§0 above) into a permanent, `PYTHONDONTWRITEBYTECODE=1`-run pytest test | transbench-qa |
| `tests/test_cost.py` — asserts the batched-entailment call bound, ≤3 hypotheses, ≤~8 abstracts/hypothesis (≈13 LLM calls/run per BUILD_SPEC §9) | transbench-qa |
| `tests/fixtures.py` (extended with Backup 1 — ARB pleiotropy, Backup 2 — thiazide pharmacogenomics) | transbench-qa |
| repo root `README.md` | transbench-qa |

Runs all `tests/*`; runs the flagship end-to-end; captures run manifest + token spend; confirms
`dataset_pointer` resolves. Opus re-runs the guard. Only after all green + Opus PASS: merge
`dev` → `main`.

---

## 3. Deviations from the literal instruction set (flagged, not silent)

1. **`pytest` + `pytest-asyncio` added** via `[dependency-groups] dev = [...]` in
   `pyproject.toml`, installed into `.venv`. BUILD_SPEC §1's curated list (the frozen
   `[project].dependencies`) does not include a test runner, but the Phase 0 acceptance
   criterion explicitly requires `python -m pytest`, and Phases 2–7 all gate on pytest-based
   acceptance tests (`test_schema`, `test_grounding`, `test_novelty`, `test_cost`, async fixtures
   for `fetch_evidence_data`/`ensure_evidence`/`neutralize_query`). Kept **out of**
   `[project].dependencies` (so the shipped runtime dependency footprint stays exactly the
   curated set) and instead in a separate dev/test-only group — standard practice, zero
   production footprint, zero Iatronix contact.
2. Everything else in this document matches BUILD_SPEC.md/KICKOFF.md as given. No plan-file
   edits were made (Phase −1 already applied its corrections; Phase 0's job is to reproduce and
   lock, not re-derive).

---

## 4. Verbatim evidence for the report back (see also chat reply)

- `.venv` python: `Python 3.12.12`
- `import app` → `/root/projects/med-ai-project/backend/app/__init__.py`
- `tests/test_reuse_imports.py`: `11 passed in 0.99s` -- plain invocation, this session's normal ambient env (`ANTHROPIC_API_KEY` set, length 108); the import-time-env-free claim is proven inside the test via an internally scrubbed subprocess, not by scrubbing the outer pytest process (see §0 above)
- Iatronix `git status --porcelain` BEFORE == AFTER == `?? plans/promote-dev-to-prod-2026-06-15.md` / `?? plans/update-readme-2026-06-15.md`; `git diff --quiet` exit 0 both times.
