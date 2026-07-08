# TransBench — Claude Code KICKOFF PROMPT (paste as your first message, in the NEW repo)

## ⚙️ MODEL SELECTION — read first
Run this in two model stages:
1. **Phase −1 (audit + fix): select `Claude Fable 5`.** Paste this file. Fable audits the whole plan (Phase −1 below), **asks you clarifying questions on how to correct the faults it finds, then applies those corrections directly to the plan files** (`BUILD_SPEC.md`, `KICKOFF.md`, and the `.claude/agents/*.md`). It does NOT write feature code (no `src/**`, `mcp_server/**`, or `tests/**`). When the plan is corrected, it tells you to switch to Opus.
2. **Phases 0–7 (build): switch the model to `Claude Opus 4.8`.** Once Fable has applied its corrections, switch the model (same conversation is fine) and tell it "plan corrected — begin Phase 0." From here the orchestrator runs on Opus, delegating building to **Sonnet** subagents and verification to the **Opus** verifier. **Fable is never used again.**

Subagents keep their own declared models (`sonnet` builders, `opus` verifier) regardless of the main model. (If Fable routes this request to Opus — which can happen for a minority of biology-adjacent sessions — the audit is still performed correctly; just proceed.)

---

You are the **orchestrator** building TransBench: a translational clinician↔bench agent, shipped as an **MCP connector for Claude Science**. This is a **standalone repo** that REUSES the Iatronix backend read-only and never edits it. There is NO web frontend.

**Confirm these files exist in this repo (I placed them):**
- `BUILD_SPEC.md` (self-contained design: reuse strategy, schemas, agents, prompts, rigor, MCP, demo). Read it fully.
- `.claude/agents/transbench-architect.md`, `transbench-engine.md`, `transbench-mcp.md`, `transbench-qa.md`, `opus-verifier.md`.
- `CLAUDE_SCIENCE_SETUP.md` (connector registration — reference only).
If any are missing, STOP and tell me.

Paths (adjust to reality): this repo `<TRANSBENCH_PATH>` (e.g. `/root/projects/transbench`); Iatronix `<IATRONIX_PATH>` (e.g. `/root/projects/med-ai-project`).

---

## YOUR ROLE
You **route and gate** — you do NOT write feature code. Every build task goes to a **Sonnet** subagent; every verification goes to the **Opus** subagent (`opus-verifier`) as an independent third-party reviewer. Delegate via the Task tool.

**Sonnet-build / Opus-verify loop — run for EVERY phase:**
1. Delegate the phase's build to its Sonnet subagent, with the exact deliverable paths + acceptance test.
2. When done, delegate to `opus-verifier` with the spec section, `git diff --name-only`, and the acceptance test. It reviews as a fresh third person, re-derives correctness, runs/reads the test, hunts for defects, and does NOT trust the builder's summary.
3. It returns `VERDICT: PASS` or `FAIL` + a numbered defect list.
4. On FAIL → send the exact defects back to the Sonnet subagent; re-verify; loop until PASS.
5. On PASS → commit to `dev`, advance.
**No phase advances without an Opus PASS. No merge to `main` until Phase 7 is fully green.**

---

## NON-NEGOTIABLE RULES (enforce; reject violating diffs)
1. **Iatronix is never modified.** Import it read-only. The guard is **baseline-diff, not assert-empty**: snapshot `git -C <IATRONIX_PATH> status --porcelain` before the run and assert no NEW delta (+ `git -C <IATRONIX_PATH> diff --quiet`). The Iatronix repo already has unrelated untracked files, and `.pyc`/`.egg-info` are gitignored there; run all TransBench processes with `PYTHONDONTWRITEBYTECODE=1`.
2. **All code stays in THIS repo** (`<TRANSBENCH_PATH>`). Nothing is written into the Iatronix directory.
3. **Reuse only DB-free leaf functions** (`fetch_evidence_data`, `fetch_drug_data`, `rank_article_list`, `build_article_registry`, `grounding_stats`/`strip_ungrounded`, `has_minimum_evidence`/`ensure_evidence`, `validate_citations`, `create_llm`, `neutralize_query`). NEVER import `run_search_graph`, `semantic_cache`, or `vector_search`. **Wrap** `fetch_evidence_data`'s `EvidenceFetchResult` in `FetchedData(query_type="evidence", evidence_data=...)` before the floor/registry/validator functions (they consume `FetchedData`, not the raw result).
4. **BYOK** via `create_llm(model_id, user_key=key, user_provider="anthropic")` — this drives the **engine's own** Anthropic calls, keyed by the MCP `ANTHROPIC_API_KEY` env (independent of Claude Science, which is only the tool's client). `create_llm` raises `fastapi.HTTPException` — catch it. No fallback key.
5. **Research tool, not clinical.** Never emit diagnosis/selection/dosing; every output carries the disclaimer.
6. **Grounded or it doesn't ship** (batched-per-hypothesis entailment + `grounding_gate` exact-shape pseudo-response + novelty guard — BUILD_SPEC §6).
7. **temperature=0** — `create_llm` has no temp arg, so set `LLM_TEMPERATURE=0` in env AND `.bind(temperature=0)` on every client. ≤3 hypotheses; ≤~8 abstracts/hypothesis.
8. **Real model ids only:** reasoning `claude-sonnet-4-6`, cheap `claude-haiku-4-5-20251001`. Never bare `claude-sonnet`/`claude-haiku` (→404) or retired `claude-sonnet-4-20250514`.

---

## SUBAGENTS
| Subagent | Model | Builds |
|---|---|---|
| `transbench-architect` | sonnet | Phase 0: reuse smoke test (install vs vendor), path-labeled plan, guardrails |
| `transbench-engine` | sonnet | schemas, prompts, reuse seam, 8 agents, rigor, LangGraph `run_transbench` |
| `transbench-mcp` | sonnet | FastMCP server (stdio + HTTP fallback) + register docs |
| `transbench-qa` | sonnet | test suite incl. Iatronix-untouched guard; runs the flagship end-to-end |
| `opus-verifier` | opus | independent adversarial review; gates every phase |

---

## EXACT TARGET LAYOUT
```
<TRANSBENCH_PATH>/
├─ pyproject.toml                     # deps + Iatronix as editable path dep (BUILD_SPEC §1)
├─ .env.example  .gitignore  README.md
├─ BUILD_SPEC.md  KICKOFF.md  CLAUDE_SCIENCE_SETUP.md
├─ .claude/agents/  (5 files)
├─ src/transbench/
│  ├─ __init__.py  config.py  schemas.py  prompts.py
│  ├─ reuse.py        # SINGLE seam: installed Iatronix → vendored fallback (BUILD_SPEC §2)
│  ├─ agents.py  rigor.py  graph.py  engine.py
├─ mcp_server/
│  ├─ server.py  manifest.json  run_stdio.sh  run_http.sh  README.md
├─ vendored/          # only if Phase 0 smoke test fails the install path
└─ tests/
   ├─ fixtures.py  test_reuse_imports.py  test_iatronix_untouched.py
   ├─ test_grounding.py  test_novelty.py  test_schema.py  test_cost.py
```

---

## ENGINE ESSENTIALS (full detail in `@BUILD_SPEC.md §2–7`)
8-step loop: decompose → hypothesize (≤3) → retrieve via `fetch_evidence_data` (+ contradiction pass) → grade+rank → novelty → rigor gate → design ONE computational experiment → assemble reproducible brief. Reasoning agents Sonnet, mechanical Haiku, temp 0. Rigor rule: `established`/ungrounded hypotheses are excluded from the experiment stage (demoted, Unverified, never deleted). MCP exposes `generate_experiment` (showpiece) + `search_grounded_evidence` (utility/fallback), both calling the engine.

---

## PHASES

**Phase −1 — PLAN AUDIT (Claude Fable 5, before a single line of code)**
You are a skeptical senior engineer doing a pre-implementation design review and pre-mortem of this ENTIRE plan. Do NOT write code, create files, scaffold, or delegate — audit only. Read `@BUILD_SPEC.md`, `@KICKOFF.md`, all five `.claude/agents/*.md`, and — critically — the REAL Iatronix source for every reused function: `app/services/data_fetcher.py` (`fetch_evidence_data`, `fetch_drug_data`), `ranking.py` (`rank_article_list`), `article_registry.py`, `grounding_gate.py`, `evidence_floor.py`, `citation_validator.py`, `llm_factory.py` (`create_llm`), `stance_neutralizer.py` (`neutralize_query`). Verify the plan against reality and hunt for everything that can go wrong. Cover at least:
1. **Reuse-by-dependency:** will `pip install -e <IATRONIX_PATH>/backend` actually expose `app` as importable (check its `pyproject.toml` packaging)? Does importing the leaf functions transitively load DB/redis/config at *module import* (which breaks standalone use)? Version conflicts between this repo's langgraph/langchain/dspy and Iatronix's pins? Exact env vars required at import time.
2. **Data-shape compatibility:** does `fetch_evidence_data`'s `EvidenceFetchResult` actually feed `rank_article_list` and `build_article_registry` in the shapes the spec assumes? Does the pseudo-response dict fed to `grounding_stats`/`strip_ungrounded` match the shape those functions really expect, or will the gate silently no-op or throw?
3. **Function contracts:** real signatures, return types, and side effects of every reused function; anything needing an LLM/DB/network at call time that the plan doesn't account for.
4. **BYOK + model routing:** where `create_llm` gets `user_key` in the MCP process; whether `model_registry` actually contains `claude-sonnet`/`claude-haiku` ids and Anthropic provider mapping (Iatronix defaults to Cerebras); whether Anthropic BYOK works end-to-end.
5. **MCP + Claude Science:** FastMCP API drift across `mcp` SDK versions; async-tool blocking pitfalls; the unverified beta Claude Science connector-registration path; whether a headless-server + custom-connector + dataset-load demo is genuinely achievable; Gladstone dataset availability/format.
6. **Cost + latency:** token spend and wall-clock for 3 hypotheses × (retrieval + contradiction pass + per-evidence entailment + grade + novelty) on Sonnet — will it blow the credit budget or be too slow for a live demo? Is per-evidence-item entailment too many calls?
7. **Reproducibility:** temp=0 does not determinize PubMed results or fully determinize the LLM — is the "reproducible" claim honest without a fixed retrieval snapshot?
8. **Scientific validity:** reliability of the novelty check deciding established-vs-open from abstracts alone; exactly where domain expertise must gate the output.
9. **"Never touch Iatronix":** does running the new code write anything into the Iatronix directory (caches/logs/migrations)?
10. **Scope/time:** is 8 phases + per-phase Opus verification achievable in the time left; where to cut if behind; is verifying every phase worth the cost vs only the critical ones?

Output a prioritized **RISK REGISTER** — for each risk: severity (blocker / high / med / low), why it bites, a concrete **pre-code probe** (the exact command or check to confirm it now), and a mitigation. Then run the probes you can run read-only to confirm the real blockers.

Then do this correction loop (this is the whole point):
1. **Ask the user** a focused set of clarifying questions on how they want the identified faults corrected (e.g. install-vs-vendor if the import drags a DB, whether to reduce per-evidence entailment for cost, how to pin a retrieval snapshot for reproducibility, any Claude Science / dataset specifics). Ask only what genuinely needs a decision; propose a sensible default for each.
2. **Apply the corrections directly to the plan files** — edit `BUILD_SPEC.md`, `KICKOFF.md`, and the `.claude/agents/*.md` wherever the fix touches. Keep every change consistent across all files (a change in one place must be reflected everywhere it appears). You may edit ONLY these plan/doc files — never create or edit `src/**`, `mcp_server/**`, or `tests/**` code.
3. Give the user a short changelog of what you edited and where, plus the final **GO / NO-GO** per major assumption.
4. Tell the user: switch to `Claude Opus 4.8` and say "plan corrected — begin Phase 0." Then STOP — your role is complete and Fable is not used again.

**Phase 0 — Reuse smoke test + plan** → `transbench-architect`
Create the venv with **Python ≥3.11** (`uv venv --python 3.12`; backend `requires-python>=3.11`, host default is 3.10). Path A is pre-validated (Phase −1): `uv pip install -e <IATRONIX_PATH>/backend --no-deps` + the curated deps (BUILD_SPEC §1) — do NOT pull Iatronix's full DB/cloud dep tree. Run `test_reuse_imports` **inside the venv** (it needs `fastapi` for `llm_factory`/`stance_neutralizer`). No env vars are required at import (all `Settings` fields default). Set `LLM_TEMPERATURE=0`, `PYTHONDONTWRITEBYTECODE=1`. Establish the baseline-diff Iatronix guard. Write a path-labeled plan; commit. → Opus verifies plan + import choice. (Path B/vendor only if A unexpectedly fails.)

**Phase 1 — Skeleton** → `transbench-engine`
`src/transbench/{__init__,config,schemas,prompts,reuse}.py` + a `graph.py`/`engine.py` whose `run_transbench` echoes input → stub `TransBrief`.
Accept: `python -c "from transbench.engine import run_transbench"` works; `test_schema` passes on stub. → Opus.

**Phase 2 — Agents 1–2** → `transbench-engine`
Decomposer + Hypothesis Generator (real `create_llm`, temp 0, strict JSON).
Accept: flagship → ≥2 axes incl. `immune_inflammatory` + ≤3 falsifiable hypotheses. → Opus.

**Phase 3 — Retrieval + grading (3–4)** → `transbench-engine`
`neutralize_query` (→ `.neutral_clinical_question`) → `fetch_evidence_data` + contradiction pass → merge abstracts → **wrap in `FetchedData`** → `has_minimum_evidence`/`ensure_evidence` → `rank_article_list` on the **raw abstract dicts** → `build_article_registry(fd)` → grade + `validate_citations` (honor its in-place `__drop__`/references mutation). Resolve each `EvidenceItem`'s `Reference` via `registry.by_pmid`. Snapshot PMIDs+abstracts into `run_manifest`.
Accept: each hypothesis returns ≥1 real PMID-backed `EvidenceItem`. → Opus.

**Phase 4 — Rigor + novelty (5–6)** → `transbench-engine`
Dedicated **batched entailment per hypothesis** (one Haiku call over its ≤8 items → supports/refutes/unclear; fan out cap 3) + `grounding_gate` fed the **exact** `{"sections":[{"content_items":[{pmid,url,source}...]}...]}` pseudo-response + novelty guard (`novelty_reason` must cite PMIDs).
Accept: `test_grounding` (grounded item survives, sourceless stripped) + `test_novelty` pass (ACEi-cough → established → blocked). → Opus.

**Phase 5 — Experiment + assembler (7–8)** → `transbench-engine`
Accept: flagship → an `ExperimentPlan` naming a **concrete, resolvable** dataset (`dataset_pointer` verified to resolve; default Tabula Sapiens immune compartment; never a fabricated accession) with runnable protocol + `claude_science_prompt`; full `TransBrief` validates; `run_manifest` carries the retrieval snapshot. → Opus.

**Phase 6 — MCP server** → `transbench-mcp`
FastMCP (`mcp_server/server.py`) exposing both tools, calling the engine. Check the installed `mcp` SDK version first and use its supported transport (SDK + `FastMCP` confirmed importable in Phase −1). Non-blocking async tools (`await` the engine; it uses `ainvoke`); **catch `fastapi.HTTPException`** from `create_llm` and return a clean structured error. `ANTHROPIC_API_KEY` env feeds the engine's calls. Add `manifest.json`, `run_stdio.sh`, `run_http.sh`, `README.md`.
Accept: stdio server starts; a local MCP client call to `generate_experiment` returns a schema-valid brief; HTTP fallback starts. → Opus.

**Phase 7 — QA + merge** → `transbench-qa` then merge
Run all `tests/*` incl. `test_iatronix_untouched` (**baseline-diff** guard on the Iatronix path, `PYTHONDONTWRITEBYTECODE=1`) + `test_cost` (asserts the batched-entailment call bound, ≤3 hypotheses, ≤~8 abstracts). Run the flagship end-to-end; capture run manifest + token spend; confirm `dataset_pointer` resolves. → Opus re-runs the guard. Only after all green + Opus PASS: merge `dev`→`main`. Update this repo's README with usage.

---

## DEFINITION OF DONE
`generate_experiment("<flagship observation>")` returns a grounded, cited `TransBrief` whose `top_experiment` is a runnable scRNA-seq/Perturb-seq analysis naming a **resolvable** dataset with a `claude_science_prompt`; the MCP server serves it over stdio (Claude Science) and HTTP (fallback); the Iatronix baseline-diff guard shows no new delta; every phase carries an Opus PASS. **Scope note:** Claude Science actually *executing* the `claude_science_prompt` against the dataset is a **demo path** (beta app, external) with the HTTP + manual-paste fallback in `CLAUDE_SCIENCE_SETUP.md` — it is NOT a code-correctness gate for this repo.

**If you are Fable:** do Phase −1 — audit, ask the user how to correct the faults, apply the corrections across `BUILD_SPEC.md`/`KICKOFF.md`/the agent files, give a changelog, then tell the user to switch to Opus. Do not write feature code.
**If you are Opus (plan already corrected):** start with Phase 0 — delegate to `transbench-architect`, and report after each Opus verdict before proceeding.
