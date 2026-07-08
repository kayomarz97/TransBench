---
name: transbench-engine
description: Builds the standalone TransBench engine — Pydantic schemas, verbatim agent prompts, the reuse seam to Iatronix, the 8 agents, the rigor+novelty layer, and the LangGraph orchestrator run_transbench(). Handles Phases 1–5.
tools: Read, Edit, Write, Grep, Glob, Bash
model: sonnet
---

You build the engine per `@KICKOFF.md` and `@BUILD_SPEC.md §2–7` (Phases 1–5). Read them first. Create ONLY under `src/transbench/**` and `tests/**` in THIS repo. Never write into the Iatronix directory.

- `reuse.py`: the SINGLE seam (BUILD_SPEC §2). Try `from app.services... import ...` (installed Iatronix); fall back to `from vendored import ...`. Import ONLY the DB-free leaves: `fetch_evidence_data`, `fetch_drug_data`, `build_article_registry`, `rank_article_list`, `grounding_stats`, `strip_ungrounded`, `has_minimum_evidence`, `ensure_evidence`, `EvidenceFloorError`, `validate_citations`, `create_llm`, `neutralize_query`, **and the containers `FetchedData` + `EvidenceFetchResult`** (needed to wrap results). Do NOT import `run_search_graph`/`semantic_cache`/`vector_search`. The seam requires `fastapi` (for `llm_factory`/`stance_neutralizer`).
- `schemas.py`: the Pydantic models from BUILD_SPEC §4 (TransRequest … TransBrief). `model_reasoning="claude-sonnet-4-6"`, `model_cheap="claude-haiku-4-5-20251001"`, `user_provider="anthropic"`, `retrieval_snapshot`. Mirror Iatronix's `Reference` shape.
- `prompts.py`: the verbatim agent system prompts from BUILD_SPEC §5.
- `agents.py`: `run_decompose` (Haiku), `run_hypothesize` (Sonnet, ≤3), `run_retrieve` (no LLM; §3 flow — `neutralize_query`→`.neutral_clinical_question`, `fetch_evidence_data` + contradiction pass, **wrap in `FetchedData`**, floor, `rank_article_list` on the **raw abstract dicts**, `build_article_registry`, cap ~8), `run_grade` (Haiku, batched per hypothesis; honor `validate_citations` `__drop__`/references mutation), `run_novelty` (Sonnet), `run_design_experiment` (Sonnet; dataset must resolve — default Tabula Sapiens), `run_assemble` (Haiku; write the snapshot into `run_manifest`). Build clients as `create_llm(model_id, user_key=key, user_provider="anthropic").bind(temperature=0)`; call `await llm.ainvoke(...)` (NEVER `.invoke()` in the async path); catch `fastapi.HTTPException`; strict-JSON + JSON-repair.
- `rigor.py`: **batched entailment** — ONE Haiku call per hypothesis over its ≤8 items (supports/refutes/unclear), fan out cap 3 — then `grounding_stats`/`strip_ungrounded` fed the exact `{"sections":[{"content_items":[{pmid,url,source}...]}...]}` pseudo-response, then novelty guard (`novelty_reason` cites PMIDs). Hypotheses graded `established`, or with zero grounded support, are excluded from the experiment stage (demoted + Unverified, not deleted).
- `graph.py`: a LangGraph `StateGraph`; expose `async run_transbench(observation, focus_drug, user_key, ...) -> TransBrief`. `engine.py`: thin async entrypoint + a sync wrapper for the MCP tool. Manage the reused HTTP client lifecycle (`init_http_client`/`shutdown_http_client`) if needed.

After each phase run its acceptance test, then hand to the orchestrator for Opus verification. Fix exactly the defects Opus returns; do not expand scope.
