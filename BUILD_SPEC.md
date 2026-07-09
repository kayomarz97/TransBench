# TransBench — Standalone Build Spec (separate repo)

> This is a **standalone project**. It does NOT live inside Iatronix and never edits Iatronix. It **reuses** Iatronix's grounding/retrieval logic by importing its backend as a read-only dependency. Save this at the new repo root as `BUILD_SPEC.md`; the kickoff prompt (`KICKOFF.md`) references it.

One line: *a clinician pastes an observation about any clinical or biomedical phenomenon — any disease, drug, or mechanism; an agent decomposes it, generates falsifiable mechanistic hypotheses, grounds each in retrieved evidence, discards textbook-as-novel, and outputs a reproducible testable computational experiment that hands off to Claude Science via an MCP connector.*

> **Domain-universalization note (post-v1):** this spec's §4/§5/§8 text below is preserved as the original, hypertension-flagship-scoped design record (§5's Decomposer/Hypothesis-Generator prompt quotes, §4's `Axis` literal, §8's flagship). The shipped engine (`src/transbench/`) has since been widened to any clinical/biomedical observation — `schemas.Axis` is now a free-form, normalized string rather than a fixed 8-value hypertension taxonomy, and the Decomposer additionally extracts a `condition_anchor` (the observation's own primary disease/condition) used as the real PubMed retrieval anchor, replacing an earlier hardcoded-to-"hypertension" default. See `src/transbench/schemas.py`, `src/transbench/prompts.py`, and `src/transbench/agents.py`'s own module docstrings for the exact, flagged deviations and their rationale, and `tests/test_universal_domains.py` for the live cross-domain proof (autoimmune/oncology/infectious/metabolic, alongside the still-valid hypertension flagship).

Codename **TransBench** (rename freely). Assumed paths (adjust to yours): new repo `/root/projects/transbench`, Iatronix `/root/projects/med-ai-project`.

---

## 0. HARD RULES

1. **Iatronix is never modified.** Install and import it read-only. The guard is **baseline-diff, not "assert empty"**: snapshot `git -C <IATRONIX_PATH> status --porcelain` at the start of the test session and assert the engine/tests add **no new delta** (and `git -C <IATRONIX_PATH> diff --quiet` for tracked files). Rationale: the Iatronix repo already carries unrelated untracked files, so an absolute-empty assertion false-fails. Also run every TransBench process with `PYTHONDONTWRITEBYTECODE=1` so imports never drop `.pyc` into the Iatronix tree. (`__pycache__/` + `*.egg-info/` are already gitignored there, so the editable install stays invisible to git — verified in Phase −1.) If your build ever needs to *edit* an Iatronix file, stop — you're doing it wrong.
2. **All code lives in the new repo.** Nothing is written outside `<TRANSBENCH_PATH>`.
3. **Reuse only DB-free leaf functions** from Iatronix (see §3). Do NOT import `run_search_graph`, `semantic_cache`, or `vector_search` — they require pgvector/redis. Use `fetch_evidence_data` directly.
4. **BYOK.** The **engine's own agents** call Anthropic via `create_llm(model_id, user_key=key, user_provider="anthropic")`; the key comes from the `ANTHROPIC_API_KEY` env in the MCP-server process. This is independent of Claude Science: Claude Science is the *client* that invokes the `generate_experiment` tool; the tool internally makes its own Anthropic calls with this key. No fallback key.
5. **Research tool, not clinical.** Never emit diagnosis / drug selection / dosing. Every output carries the research-only disclaimer.
6. **Grounded or it doesn't ship.** No mechanistic claim survives without a resolvable citation (§7).
7. **temperature=0** everywhere; hypotheses capped at 3; abstracts/hypothesis capped (~8). **Note:** `create_llm` has **no temperature parameter** — it builds clients at `settings.llm_temperature` (0.2 by default). Force determinism two ways (belt-and-suspenders): set `LLM_TEMPERATURE=0` in the env (Iatronix `Settings` reads it) **and** bind `temperature=0` on every client returned by `create_llm` (`llm = create_llm(...); llm = llm.bind(temperature=0)`).
8. **Real model ids only.** Reasoning agents use `claude-sonnet-4-6`; mechanical agents use `claude-haiku-4-5-20251001`. Both are registry-known and route to Anthropic. Never use bare `claude-sonnet`/`claude-haiku` (not real API models → 404) or the retired `claude-sonnet-4-20250514`.
9. Git discipline: branch `dev`, test, then `main` (this repo's own branches).

---

## 1. Standalone repo layout (build to this exactly)

```
transbench/                              # NEW repo — independent of Iatronix
├─ README.md
├─ pyproject.toml                        # deps below; Iatronix added as an editable path dep
├─ .env.example
├─ .gitignore
├─ BUILD_SPEC.md   KICKOFF.md   CLAUDE_SCIENCE_SETUP.md
├─ .claude/agents/                       # 5 subagent files
├─ src/transbench/
│  ├─ __init__.py
│  ├─ config.py                          # model ids, caps, env reads
│  ├─ schemas.py                         # §4 Pydantic models
│  ├─ prompts.py                         # §5 verbatim agent prompts
│  ├─ reuse.py                           # SINGLE seam to Iatronix (installed pkg → vendored fallback)
│  ├─ agents.py                          # the 8 agents
│  ├─ rigor.py                           # entailment + grounding reuse + novelty guard
│  ├─ graph.py                           # LangGraph → run_transbench(observation, focus_drug, key)->TransBrief
│  └─ engine.py                          # thin public entrypoint (async + sync wrapper)
├─ mcp_server/
│  ├─ server.py                          # FastMCP: generate_experiment + search_grounded_evidence; stdio + http
│  ├─ manifest.json  run_stdio.sh  run_http.sh  README.md
├─ vendored/                             # ONLY created if the Phase 0 smoke test fails the install path
└─ tests/
   ├─ fixtures.py                        # 3 demo observations (§8)
   ├─ test_reuse_imports.py              # the smoke test
   ├─ test_iatronix_untouched.py         # git-clean guard on the Iatronix path
   ├─ test_grounding.py  test_novelty.py  test_schema.py  test_cost.py
```

**Venv:** create with **Python ≥3.11** (`uv venv --python 3.12`) — the backend's `requires-python>=3.11` will reject a 3.10 venv, and the host default here is 3.10.

**Install strategy — lean `--no-deps` + curated (decided in Phase −1):** the DB-free leaves never touch the DB, so do NOT drag in Iatronix's full dep tree (asyncpg/pgvector/redis/firebase-admin/boto3/sentry). Instead:
```bash
uv pip install -e /root/projects/med-ai-project/backend --no-deps   # exposes `app`, installs nothing else
```
then add only what the leaves actually import (curated; the Phase 0 smoke test confirms/extends this set):
`mcp`, `langgraph`, `langchain`, `langchain-anthropic`, `langchain-core`, `anthropic`, `httpx`, `pydantic>=2`, `pydantic-settings`, `pyyaml`, `fastapi`, `json-repair`, `tenacity`. (`fastapi` is required — `llm_factory`/`stance_neutralizer` import `fastapi.HTTPException`; verified in Phase −1. `dspy-ai` is NOT needed — the reused leaves don't import it.)
`iatronix-backend` must also be a **declared dependency**, not only a source; in `pyproject.toml`:
```toml
[project]
dependencies = ["iatronix-backend", "mcp", "langgraph", "langchain", "langchain-anthropic", "anthropic", "httpx", "pydantic>=2", "pydantic-settings", "pyyaml", "fastapi", "json-repair", "tenacity"]
[tool.uv.sources]
iatronix-backend = { path = "/root/projects/med-ai-project/backend", editable = true }
```
`.env.example`: `ANTHROPIC_API_KEY`, `PUBMED_API_KEY`, `LLM_TEMPERATURE=0`, `PYTHONDONTWRITEBYTECODE=1`. **No other env is required at import** — every field of Iatronix's `Settings` has a default (verified in Phase −1), so `ENCRYPTION_KEY`/DB/redis vars are NOT needed. `PUBMED_API_KEY` maps onto `settings.pubmed_api_key` (raises PubMed rate limits); `LLM_TEMPERATURE=0` forces deterministic clients through the reused factory.

---

## 2. Reuse strategy (the whole point — get this right)

The new repo reuses Iatronix's mature grounding stack instead of rebuilding it. Two ways; **Phase 0 smoke test decides**:

- **Path A (chosen — Phase −1 validated).** Install the Iatronix backend as an editable dep (`uv pip install -e <IATRONIX_PATH>/backend --no-deps` + curated deps, §1). Then `from app.services... import ...` works and Iatronix's files are only read. Phase −1 probe result: **8/10 leaves import with just httpx+pydantic-settings+pyyaml**; `llm_factory`/`stance_neutralizer` additionally need `fastapi` (in the curated set); **no live DB is required at import** (all `Settings` fields have defaults). `provider_registry` resolves `config/providers.yaml` *relative to the installed backend dir*, so the editable install is what makes `create_llm` find it.
- **Path B (fallback only): vendor the leaf modules.** Not needed given the validated Path A, and worse: vendoring `.py` alone breaks the `config/providers.yaml` path resolution and the `app.config`/`app.services.*` intra-package imports. Use only if Path A install fails for an unforeseen reason, and then copy `config/providers.yaml` + fix imports too.

`reuse.py` is the single seam:
```python
try:
    from app.services.data_fetcher import fetch_evidence_data, fetch_drug_data, init_http_client, shutdown_http_client
    from app.services.article_registry import build_article_registry
    from app.services.ranking import rank_article_list
    from app.services.grounding_gate import grounding_stats, strip_ungrounded, grounded_ratio
    from app.services.evidence_floor import has_minimum_evidence, ensure_evidence, EvidenceFloorError
    from app.services.citation_validator import validate_citations
    from app.services.llm_factory import create_llm
    from app.services.stance_neutralizer import neutralize_query
    REUSE_SOURCE = "installed_iatronix"
except ImportError:
    from vendored import (fetch_evidence_data, fetch_drug_data, build_article_registry,  # noqa
        rank_article_list, grounding_stats, strip_ungrounded, grounded_ratio,
        has_minimum_evidence, ensure_evidence, EvidenceFloorError, validate_citations,
        create_llm, neutralize_query)
    REUSE_SOURCE = "vendored"
```

Confirmed real signatures (Phase −1, read from source) — note the two shape/contract gotchas the engine MUST honor:
- `create_llm(model_id, max_tokens=None, user_key=None, user_provider=None)` → a LangChain chat client. **No `temperature` param** (built at `settings.llm_temperature`; force 0 per §0.7). Raises **`fastapi.HTTPException`** on missing key (402) / unknown provider (400) — engine/MCP must catch these and return a clean error.
- `async fetch_evidence_data(query, *, extra_pubmed_terms=None, extra_journal_filter=None) -> EvidenceFetchResult` (fields: `clinical_trial_abstracts`, `systematic_review_abstracts`, `guideline_abstracts` — each `list[dict]` PubMed abstracts; `fetch_success`). `async fetch_drug_data(drug_name, ...) -> DrugFetchResult`.
- ⚠️ **`has_minimum_evidence(fd)`, `async ensure_evidence(fd, query, query_type)`, `build_article_registry(fd)`, `build_pmid_index(fd)` all consume a `FetchedData` container, NOT the `EvidenceFetchResult` that `fetch_evidence_data` returns.** Passing the result directly → `AttributeError` (floor) or a **silently empty** registry. Always wrap first: `fd = FetchedData(query_type="evidence", evidence_data=result)`. Import `FetchedData` from `app.services.data_fetcher` (or via the seam). `EvidenceFloorError` is raised when all broadening strategies fail.
- `rank_article_list(articles: list[dict], entities: list[str], query_text="") -> list[dict]` — pure; needs the **raw abstract dicts** (they carry `abstract`/`pub_types`/`year`/`pmid`), not `to_reference_list()` dicts (which lack them).
- `ArticleRegistry`: `.by_pmid`, `.by_token`, `.best_match(...)`, `.to_reference_list() -> list[dict]` (URL-guaranteed refs). Only abstracts with a resolvable id (pmid/nct/doi/url) enter the registry.
- `grounding_stats(response)`, `strip_ungrounded(response)` — pure; expect `response = {"sections":[{"content_items":[{...}]}]}` (see §6 for the exact pseudo-response the engine must build).
- `validate_citations(response_data, query_type, fetched_data=None, ...) -> list[str]` — returns **warnings** and **mutates `response_data` in place** (sets `__drop__` on rejected claims, filters `response_data["references"]`). Pass the wrapped `FetchedData` to validate PMIDs; with `fetched_data=None` PMID validation is skipped.
- `async neutralize_query(raw_query, model_id, user_key, user_provider) -> StanceResult` — all four args required; use `.neutral_clinical_question`. Makes a Haiku call with an 800 ms timeout (heuristic fallback on timeout).

---

## 3. Retrieval flow per hypothesis (no run_search_graph)

```python
stance   = await neutralize_query(hyp.statement, MODEL_CHEAP, key, "anthropic")  # StanceResult
neutral  = stance.neutral_clinical_question                       # anti-sycophancy
result   = await fetch_evidence_data(neutral)                     # -> EvidenceFetchResult (HTTP-only, DB-free)
contra   = await fetch_evidence_data(f"{neutral} limitations OR negative OR no association")

# Merge support + contradiction abstracts, then WRAP in FetchedData (the floor/registry contract).
merged = EvidenceFetchResult(
    clinical_trial_abstracts   = result.clinical_trial_abstracts   + contra.clinical_trial_abstracts,
    systematic_review_abstracts= result.systematic_review_abstracts+ contra.systematic_review_abstracts,
    guideline_abstracts        = result.guideline_abstracts        + contra.guideline_abstracts,
    fetch_success              = result.fetch_success or contra.fetch_success,
)
fd = FetchedData(query_type="evidence", evidence_data=merged)     # ← REQUIRED wrapper
if not has_minimum_evidence(fd):                                  # consumes FetchedData, not the result
    fd = await ensure_evidence(fd, neutral, "evidence")

raw_abstracts = (fd.evidence_data.clinical_trial_abstracts
                 + fd.evidence_data.systematic_review_abstracts
                 + fd.evidence_data.guideline_abstracts)          # list[dict] with abstract/pub_types/pmid
ranked   = rank_article_list(raw_abstracts, entities=hyp.key_entities, query_text=neutral)[:ABSTRACT_CAP]
registry = build_article_registry(fd)                             # URL-guaranteed refs; look up by pmid
# For each ranked article, resolve its citable Reference via registry.by_pmid[str(pmid)] (skip if absent).
```
Cap `ABSTRACT_CAP ~8`. Snapshot `raw_abstracts` (PMIDs+abstracts) into `run_manifest` for replay (§9). Fan the per-hypothesis retrieval out with `asyncio.gather` under a concurrency cap of 3 (mirrors Iatronix `parallel_sections_max_concurrent`). If you want caching, add a tiny in-memory dict in this repo — do NOT import Iatronix's `semantic_cache`.

---

## 4. Schemas — `src/transbench/schemas.py` (Pydantic v2)

```python
from __future__ import annotations
from typing import Literal, Optional
from pydantic import BaseModel, Field

Axis = Literal["raas","sympathetic","endothelial_vascular","renal_volume",
               "immune_inflammatory","drug_pk_metabolism","genetic_pharmacogenomic","other"]
Priority = Literal["high","medium","low"]
NoveltyVerdict = Literal["established","open_question","unsupported"]
EvidenceGrade = Literal["guideline","systematic_review","rct","mechanistic_study",
                        "observational","preclinical","expert_opinion"]

class TransRequest(BaseModel):
    observation: str = Field(min_length=3, max_length=8000)   # free text; FUTURE: full patient history
    focus_drug: Optional[str] = None
    max_hypotheses: int = 3
    user_key: str
    user_provider: Optional[str] = "anthropic"           # pass explicitly so routing never falls to Cerebras
    model_reasoning: str = "claude-sonnet-4-6"            # registry-known Anthropic id (hypothesize/novelty/design)
    model_cheap: str = "claude-haiku-4-5-20251001"       # registry-known Anthropic id (decompose/grade/entail/assemble)
    retrieval_snapshot: Optional[dict] = None            # when set, replay retrieval from PMIDs+abstracts (reproducible reruns)

class Reference(BaseModel):
    source: str; title: Optional[str] = None; year: Optional[int] = None
    url: Optional[str] = None; pmid: Optional[str] = None; grade: Optional[EvidenceGrade] = None

class DecomposedAxis(BaseModel):
    axis: Axis; rationale: str; key_entities: list[str] = []

class Hypothesis(BaseModel):
    id: str; axis: Axis; statement: str; prediction: str; rationale: str; priority: Priority

class EvidenceItem(BaseModel):
    claim_fragment: str; reference: Reference; supports: bool
    entailment: Literal["supports","refutes","unclear"]; grade: EvidenceGrade

class GradedHypothesis(BaseModel):
    hypothesis: Hypothesis; evidence: list[EvidenceItem]
    supporting_count: int; contradicting_count: int
    novelty: NoveltyVerdict; novelty_reason: str
    confidence: Literal["low","moderate","high"]; grounded: bool

class ExperimentPlan(BaseModel):
    hypothesis_id: str; question: str; dataset: str; dataset_pointer: Optional[str] = None
    method: str; protocol_steps: list[str]; confirm_if: str; refute_if: str
    feasibility_notes: str; claude_science_prompt: str

class TransBrief(BaseModel):
    request_echo: str; axes: list[DecomposedAxis]; hypotheses: list[GradedHypothesis]
    top_experiment: ExperimentPlan; references: list[Reference]
    contradictions_surfaced: list[str]; uncertainty_note: str; run_manifest: dict
    disclaimer: str = "Research hypothesis generation only. Not clinical, diagnostic, or prescribing advice."
```

MCP tool return = `TransBrief` (model_dump). SSE not required (connector-first) — a plain async return is fine.

---

## 5. The 8 agents — `src/transbench/agents.py` (+ prompts in `prompts.py`)

Each = `async run_<name>(payload, llm)`. Build clients once as `llm = create_llm(model_id, user_key=key, user_provider="anthropic").bind(temperature=0)` (create_llm has no temp param — see §0.7). **Never call `llm.invoke()` in the async path** (it blocks the MCP event loop) — use `await llm.ainvoke(...)` (or `asyncio.to_thread`). Wrap every LLM call to catch `fastapi.HTTPException` → clean error. strict-JSON, JSON-repair on parse. Reasoning agents (hypothesize/novelty/design) use `model_reasoning` = `claude-sonnet-4-6`; mechanical (decompose/grade/entailment/assemble) use `model_cheap` = `claude-haiku-4-5-20251001`. Retrieval (3) uses no LLM.

Prompt skeletons (verbatim into `prompts.py`):

**1 Decomposer** (Haiku)→ axes: *"Split a clinical observation about antihypertensive drugs into distinct biological axes (raas, sympathetic, endothelial_vascular, renal_volume, immune_inflammatory, drug_pk_metabolism, genetic_pharmacogenomic). Only include axes the observation motivates; give rationale + key entities. STRICT JSON {"axes":[{"axis","rationale","key_entities"}]}."*

**2 Hypothesis Generator** (Sonnet)→ ≤N: *"Generate FALSIFIABLE mechanistic hypotheses for the observed phenomenon. Each names a specific molecule/cell/pathway and includes a PREDICTION true if it holds. Prefer genuinely open questions over textbook facts. Account for population modifiers (ancestry, age, salt sensitivity, plasma renin, CKD). No clinical actions. STRICT JSON list of {"id","axis","statement","prediction","rationale","priority"}."*

**3 Evidence Retriever** (no LLM) — §3 flow + contradiction pass; cap abstracts.

**4 Evidence Grader** (Haiku)→ EvidenceItem[]: for each hypothesis, map its ranked articles to supports/contradicts + evidence grade in **one batched Haiku call over all its ≤8 abstracts** (not per-item). Attach the real, URL-backed `Reference` by looking the article's pmid up in `registry.by_pmid`; drop any article with no registry match (no resolvable citation). Then build a `response_data` dict with a `references` list and call `validate_citations(response_data, "evidence", fetched_data=fd)`; **honor its in-place mutations** — remove any claim it flags with `__drop__` and use the filtered `response_data["references"]`. (Entailment is a *separate* batched pass — see §6.)

**5 Novelty Checker** (Sonnet)→ verdict: *"Classify the hypothesis given its evidence: 'established' (already well-documented → not novel), 'open_question' (plausible, partially supported, unresolved → good target), 'unsupported' (no real evidence). Be strict. STRICT JSON {"novelty","novelty_reason"}."*

**6 Rigor Gate** — §6.

**7 Experiment Designer** (Sonnet)→ ExperimentPlan for the top open_question+grounded hypothesis: *"Design ONE computational experiment to confirm/refute the hypothesis using a NAMED, publicly resolvable dataset. Prefer datasets Claude Science can run (single-cell RNA-seq / Perturb-seq, bulk expression/GEO, GWAS/eQTL). `dataset` MUST be a concrete accession or atlas name a third party can fetch (e.g. a GEO `GSE…`, a CELLxGENE / Tabula Sapiens collection, an ArrayExpress id); `dataset_pointer` is its URL/DOI. Give method, ordered runnable protocol_steps, confirm_if, refute_if, feasibility_notes, and a claude_science_prompt (ready to run in Claude Science to produce a figure). No wet-lab-only. No clinical claims. STRICT JSON = ExperimentPlan."* **Grounding rule for datasets:** never emit a fabricated/guessed accession — if the model isn't sure the id resolves, fall back to the pinned default substrate (§8) and say so in `feasibility_notes`. Phase 5/7 verify the named `dataset_pointer` actually resolves.

**8 Brief Assembler** (Haiku)→ TransBrief; references via `registry.to_reference_list()`; collect contradictions; write uncertainty_note; fill run_manifest (models, temps, neutral queries, PMIDs, timestamps).

Orchestrated by a LangGraph `StateGraph` in `graph.py` (START→decompose→hypothesize→fan-out retrieve/grade/novelty→rigor→design→assemble→END), exposed as `async run_transbench(observation, focus_drug, user_key, ...) -> TransBrief`. `engine.py` wraps it (async + a sync helper for the MCP tool).

---

## 6. Rigor layer — `src/transbench/rigor.py`

Reuses Iatronix gates, adds three checks:

**(1) Entailment (dedicated, batched per hypothesis).** A *separate* Haiku call — not folded into the grader, and not per-item — that classifies ALL of a hypothesis's ≤8 evidence items in ONE structured-JSON call: each item → `supports` / `refutes` / `unclear` w.r.t. the hypothesis. Closes the gap `validate_citations` leaves (existence ≠ support). ~1 call/hypothesis (≈3/run), hypotheses fanned out concurrency-capped at 3. This is the quality-first choice fixed in Phase −1 (per-item's ~24 calls/run risks Anthropic rate-limit/overload for no accuracy gain).

**(2) Grounding enforcement (exact shape — a malformed dict silently no-ops).** `grounding_stats`/`strip_ungrounded` expect **exactly** this shape; build it precisely:
```python
pseudo = {"sections": [
    {"content_items": [
        {"pmid": ev.reference.pmid, "url": ev.reference.url, "source": ev.reference.source}
        for ev in hyp_evidence if ev.entailment == "supports"
    ]}
    for each hypothesis
]}
```
An item is grounded iff it carries a `pmid`/`nct_id`/`doi`/`url` **or** a specific non-generic `source`. If the key `sections` is missing or items lack all of those, `grounding_stats` returns `(0, …)` and the hypothesis is wrongly demoted — so a unit test MUST assert a known-grounded item survives and a sourceless one is stripped. A hypothesis with 0 grounded **supporting** items → `grounded=False` → **excluded** from the experiment stage (demoted + Unverified, never silently deleted).

**(3) Novelty guard.** Any hypothesis graded `established` is not promoted (kills textbook-as-novel); `novelty_reason` MUST cite specific evidence items (PMIDs) so the verdict is auditable, never an unsupported LLM assertion. Confidence = f(supporting grounded count, contradicting count, best grade).

---

## 7. MCP server — `mcp_server/server.py`

FastMCP stdio + HTTP, calling `engine.run_transbench`. Tools:
- `generate_experiment(observation: str, focus_drug: str = "") -> dict` → `TransBrief` (showpiece; `top_experiment.claude_science_prompt` is what Claude Science runs).
- `search_grounded_evidence(question: str) -> dict` → grounded evidence list (utility + fallback demo).
First check the installed SDK version and use the transport it supports (`stdio` always; HTTP = `sse`/`streamable-http` per version) — the `mcp` SDK + `FastMCP` are already importable here (Phase −1). Key from `ANTHROPIC_API_KEY` env — this feeds the **engine's own** Anthropic calls (Claude Science is the tool's client, not the engine's LLM). Tools must be non-blocking (`await` the async engine; the engine uses `ainvoke`) and must **catch `fastapi.HTTPException`** from `create_llm` (missing/invalid key → 402/401, bad model → 400) and return a clean structured error instead of leaking the exception. See `CLAUDE_SCIENCE_SETUP.md` for registration (the standard `mcpServers` block pointing at this repo's venv + `mcp_server.server`, `cwd=<TRANSBENCH_PATH>`, with `PYTHONDONTWRITEBYTECODE=1`).

---

## 8. Flagship demo (+2 backups)

**Flagship — resistant hypertension / immune axis:** input *"58F, resistant hypertension despite ACEi + CCB + thiazide at max dose; elevated hs-CRP; poor response to RAAS blockade."* Expected axes: raas, immune_inflammatory (key), renal_volume. Hypothesis: T-cell/IL-17/effector-memory-driven vascular & renal inflammation contributes to RAAS-resistant hypertension (a real, unresolved area — the agent grounds real PMIDs at runtime, e.g. the line of work showing T cells are required for angiotensin-II hypertension; do NOT hardcode citations). Novelty: open_question. Experiment: scRNA-seq differential-state analysis on a **pinned, verified-real dataset** testing whether an IL-17/RAAS-linked regulator's expression marks a distinct effector-memory T-cell state; confirm_if/refute_if specified; `claude_science_prompt` runs it. **The money moment.**

**Pinned dataset (reproducibility).** Default substrate = the **Tabula Sapiens** immune compartment (a real, versioned, publicly downloadable human single-cell atlas with a T-cell compartment) — guaranteed to resolve, so the experiment is rerunnable. The designer agent may instead name a disease-matched GEO series (search handle: *"angiotensin II hypertension single-cell RNA-seq T cell"*, e.g. a Perturb-seq immune dataset) **only when its accession is verified to resolve**; otherwise it falls back to Tabula Sapiens and says so. Never emit an unverified accession (Phase 5/7 confirm `dataset_pointer` resolves).
**Backup 1 — off-target/repurposing** (ARB pleiotropy). **Backup 2 — pharmacogenomic non-response** (thiazide/Na-transport variation → GWAS/eQTL). Build the flagship end-to-end first.

---

## 9. Cost + reproducibility
Cap 3 hypotheses + ~8 abstracts each; Haiku (`claude-haiku-4-5-20251001`) for agents 1/4, entailment & 8; Sonnet (`claude-sonnet-4-6`) for 2/5/7; fan hypotheses out with concurrency cap 3; pre-warm flagship queries. Entailment is batched per hypothesis (§6), so per run ≈ 1 decompose + 1 hypothesize + 3 grade + 3 entailment + 3 novelty + 1 design + 1 assemble (+≤3 short neutralize calls) ≈ **~13 LLM calls, ~$0.15–0.30** — affordable and demo-fast (vs ~24 entailment calls alone if per-item). `temperature=0` forced via env + bind (§0.7).

**Reproducibility (honest framing).** `temperature=0` does NOT make PubMed results or the LLM bit-identical, so the *reproducible artifact* is the **experiment** — a named, resolvable dataset + ordered protocol + `claude_science_prompt` a third party can rerun — not the hypothesis-generation retrieval. To make a demo replay deterministic, snapshot every run's retrieved PMIDs+abstracts into `run_manifest`; passing `TransRequest.retrieval_snapshot` replays retrieval from that snapshot instead of hitting PubMed. `run_manifest` records models, temps, neutralized queries, PMIDs, dataset pointer, timestamps, and token spend per run.

Phases + the Sonnet-build / Opus-verify loop are in `KICKOFF.md`.
