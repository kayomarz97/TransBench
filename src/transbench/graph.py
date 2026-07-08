"""graph.py — LangGraph orchestration (BUILD_SPEC.md §5, KICKOFF.md Phase 3).

Phase 1 shipped a single-node stub. Phase 2 wired agents 1-2 (Decomposer,
Hypothesis Generator). Phase 3 (this file, today) wires agents 3-4 (Evidence
Retriever — no LLM directly; Evidence Grader — Haiku, batched per
hypothesis), fanned out across hypotheses with a concurrency cap, so
``GradedHypothesis.evidence``/``supporting_count``/``contradicting_count``
are now real, PMID-backed output. Agents 5-8 (novelty/rigor/design/assemble)
remain an accurate, clearly-labeled placeholder until Phase 4-5.

Phases 4-5 grow this into the full topology (BUILD_SPEC.md §5, last
paragraph): ``START -> decompose -> hypothesize -> fan-out(retrieve -> grade
-> novelty) -> rigor -> design -> assemble -> END``. The ``TransBenchState``
TypedDict below is shaped as a superset target so later phases add
fields/nodes without reshaping what's already established.
"""
from __future__ import annotations

import asyncio
import datetime as _dt
from typing import Any, Optional, TypedDict

from langgraph.graph import END, START, StateGraph

from transbench import agents, config
from transbench.reuse import REUSE_SOURCE, init_http_client, shutdown_http_client
from transbench.schemas import (
    DecomposedAxis,
    EvidenceItem,
    ExperimentPlan,
    GradedHypothesis,
    Hypothesis,
    TransBrief,
)

# Default fallback experiment substrate (BUILD_SPEC.md §8: "Pinned dataset
# (reproducibility)" — a real, versioned, publicly downloadable human
# single-cell atlas with a T-cell compartment). Used only as placeholder
# content until the real experiment designer (agent 7, Phase 5) makes the
# actual named-dataset decision.
_DEFAULT_DATASET = "Tabula Sapiens (immune compartment)"
_DEFAULT_DATASET_POINTER = "https://tabula-sapiens-portal.ds.czbiohub.org/"


class TransBenchState(TypedDict, total=False):
    """State threaded through the graph. Phases 1-2 populate/read
    observation/focus_drug/.../axes/hypotheses; Phase 3 adds per-hypothesis
    evidence + a retrieval snapshot; Phase 4-5 add novelty/rigor/design
    outputs to this same dict rather than a parallel shape.
    """

    observation: str
    focus_drug: Optional[str]
    max_hypotheses: int
    user_key: Optional[str]
    user_provider: str
    model_reasoning: str
    model_cheap: str
    retrieval_snapshot: Optional[dict]
    axes: list[DecomposedAxis]
    hypotheses: list[Hypothesis]
    evidence_by_hyp_id: dict[str, list[EvidenceItem]]
    retrieval_manifest_by_hyp_id: dict[str, dict]
    brief: TransBrief


async def _decompose_node(state: TransBenchState) -> TransBenchState:
    """Agent 1 — Decomposer (Haiku, ``config.MODEL_CHEAP``). Real LLM call —
    builds its own temperature-0 client via :func:`agents.build_llm` for
    this one call (BUILD_SPEC.md §5: "Build clients once")."""
    llm = agents.build_llm(
        state.get("model_cheap", config.MODEL_CHEAP),
        state.get("user_key"),
        state.get("user_provider", "anthropic"),
    )
    axes = await agents.run_decompose(
        {"observation": state["observation"], "focus_drug": state.get("focus_drug")},
        llm,
    )
    return {**state, "axes": axes}


async def _hypothesize_node(state: TransBenchState) -> TransBenchState:
    """Agent 2 — Hypothesis Generator (Sonnet, ``config.MODEL_REASONING``).
    Real LLM call — grounded in agent 1's axes output."""
    llm = agents.build_llm(
        state.get("model_reasoning", config.MODEL_REASONING),
        state.get("user_key"),
        state.get("user_provider", "anthropic"),
    )
    hypotheses = await agents.run_hypothesize(
        {
            "observation": state["observation"],
            "focus_drug": state.get("focus_drug"),
            "axes": state.get("axes", []),
            "max_hypotheses": state.get("max_hypotheses", config.MAX_HYPOTHESES),
        },
        llm,
    )
    return {**state, "hypotheses": hypotheses}


async def _retrieve_and_grade_node(state: TransBenchState) -> TransBenchState:
    """Agents 3 (Evidence Retriever, no LLM) + 4 (Evidence Grader, Haiku,
    batched per hypothesis) — BUILD_SPEC.md §3/§5. Fanned out across
    hypotheses with ``asyncio.gather`` under a concurrency cap of
    ``config.CONCURRENCY`` (mirrors Iatronix's own
    ``parallel_sections_max_concurrent`` — BUILD_SPEC.md §3).

    A hypothesis with genuinely zero retrievable/gradable evidence gets an
    empty list, not a crash (``run_retrieve``/``run_grade`` already handle
    that gracefully) — this node does not add extra error handling on top.
    """
    hypotheses = state.get("hypotheses", [])
    observation = state["observation"]
    user_key = state.get("user_key")
    user_provider = state.get("user_provider", "anthropic")
    model_cheap = state.get("model_cheap", config.MODEL_CHEAP)

    semaphore = asyncio.Semaphore(config.CONCURRENCY)

    async def _one(hypothesis: Hypothesis) -> tuple[str, list[EvidenceItem], dict]:
        async with semaphore:
            retrieval = await agents.run_retrieve(
                hypothesis,
                user_key,
                user_provider=user_provider,
                model_id=model_cheap,
                observation=observation,
            )
            evidence = await agents.run_grade(
                hypothesis,
                retrieval.ranked,
                retrieval.registry,
                retrieval.fd,
                user_key,
                user_provider=user_provider,
                model_id=model_cheap,
            )
        # Retrieval snapshot for run_manifest (BUILD_SPEC.md §3/§9): the
        # neutral query + the RANKED/CAPPED abstracts actually used (not the
        # full uncapped raw_abstracts, to keep the manifest a reasonable
        # size — a scope-appropriate adaptation, documented here and in the
        # phase report).
        manifest_entry = {
            "neutral_query": retrieval.neutral_query,
            "pubmed_query": retrieval.pubmed_query,
            "abstracts": [
                {"pmid": a.get("pmid"), "title": a.get("title"), "year": a.get("year")}
                for a in retrieval.ranked
            ],
        }
        return hypothesis.id, evidence, manifest_entry

    results = await asyncio.gather(*[_one(h) for h in hypotheses])

    evidence_by_hyp_id = {hyp_id: evidence for hyp_id, evidence, _ in results}
    retrieval_manifest_by_hyp_id = {hyp_id: manifest for hyp_id, _, manifest in results}
    return {
        **state,
        "evidence_by_hyp_id": evidence_by_hyp_id,
        "retrieval_manifest_by_hyp_id": retrieval_manifest_by_hyp_id,
    }


def _assemble_placeholder_node(state: TransBenchState) -> TransBenchState:
    """Assembles the ``TransBrief`` from REAL axes (agent 1), REAL hypotheses
    (agent 2), and REAL graded evidence (agents 3-4). Novelty/rigor/
    experiment/assembly (agents 5-8) are not wired yet (Phase 4-5) — each
    hypothesis is wrapped in a placeholder ``GradedHypothesis`` for THOSE
    fields only: ``novelty="unsupported"`` (schema-valid; ``novelty_reason``
    explains why), ``grounded=False`` (correctly means the Phase 4 rigor gate
    would exclude these from experiment design if that stage ran).
    ``evidence``/``supporting_count``/``contradicting_count`` ARE real. No
    LLM calls happen in this function itself — it only assembles prior
    nodes' real output into the final schema.
    """
    observation = state["observation"]
    focus_drug = state.get("focus_drug")
    axes = state.get("axes", [])
    hypotheses = state.get("hypotheses", [])
    evidence_by_hyp_id: dict[str, list[EvidenceItem]] = state.get("evidence_by_hyp_id", {})
    retrieval_manifest_by_hyp_id: dict[str, dict] = state.get("retrieval_manifest_by_hyp_id", {})

    graded_hypotheses = []
    for h in hypotheses:
        evidence = evidence_by_hyp_id.get(h.id, [])
        supporting_count = sum(1 for e in evidence if e.supports)
        contradicting_count = sum(1 for e in evidence if not e.supports)
        graded_hypotheses.append(
            GradedHypothesis(
                hypothesis=h,
                evidence=evidence,
                supporting_count=supporting_count,
                contradicting_count=contradicting_count,
                novelty="unsupported",
                novelty_reason=(
                    "Phase 3: evidence has been retrieved and graded for this "
                    f"hypothesis ({len(evidence)} PMID-backed item(s), "
                    f"{supporting_count} supporting / {contradicting_count} "
                    "contradicting). Novelty classification and the rigor/"
                    "grounding gate (BUILD_SPEC.md §5 agents 5-6) have not run "
                    "yet (Phase 4) — this verdict is still a placeholder, not "
                    "a real novelty assessment."
                ),
                confidence="low",
                grounded=False,
            )
        )

    top_experiment = ExperimentPlan(
        hypothesis_id=hypotheses[0].id if hypotheses else "stub-0",
        question="Phase 3 placeholder — no experiment designer wired yet.",
        dataset=_DEFAULT_DATASET,
        dataset_pointer=_DEFAULT_DATASET_POINTER,
        method="Not yet designed (BUILD_SPEC.md §5 agent 7 / Experiment Designer lands in Phase 5).",
        protocol_steps=["Phase 3 placeholder — no real protocol has been designed yet."],
        confirm_if="N/A — not yet designed.",
        refute_if="N/A — not yet designed.",
        feasibility_notes="Placeholder TransBrief: no real experiment design has run yet.",
        claude_science_prompt="N/A — not yet designed.",
    )

    run_manifest: dict[str, Any] = {
        "phase": "3-retrieval-grading",
        "reuse_source": REUSE_SOURCE,
        "model_reasoning": state.get("model_reasoning", config.MODEL_REASONING),
        "model_cheap": state.get("model_cheap", config.MODEL_CHEAP),
        "temperature": config.TEMPERATURE,
        "max_hypotheses": state.get("max_hypotheses", config.MAX_HYPOTHESES),
        "abstract_cap": config.ABSTRACT_CAP,
        "concurrency": config.CONCURRENCY,
        "focus_drug": focus_drug,
        "retrieval_snapshot_provided": state.get("retrieval_snapshot") is not None,
        "retrieval_snapshot": retrieval_manifest_by_hyp_id,
        "generated_at": _dt.datetime.now(_dt.timezone.utc).isoformat(),
    }

    brief = TransBrief(
        request_echo=observation,
        axes=axes,
        hypotheses=graded_hypotheses,
        top_experiment=top_experiment,
        references=[],
        contradictions_surfaced=[],
        uncertainty_note=(
            "Phase 3: decomposition (agent 1), hypothesis generation (agent "
            "2), evidence retrieval (agent 3), and evidence grading (agent 4) "
            "are real output — every EvidenceItem below carries a real, "
            "resolvable PMID reference. Novelty classification, the rigor/"
            "grounding gate, experiment design, and final brief assembly "
            "(agents 5-8) have not run yet — those land in Phase 4-5. Treat "
            "every hypothesis below as ungrounded/unverified until then."
        ),
        run_manifest=run_manifest,
    )
    return {**state, "brief": brief}


def build_graph():
    """Compile the ``StateGraph``: ``START -> decompose -> hypothesize ->
    retrieve_and_grade -> assemble_placeholder -> END``. Pure graph
    construction — no LLM client or HTTP client is built at compile time
    (clients are built per-call inside the async nodes, using that call's own
    ``user_key``; the shared HTTP client is managed once per run by
    :func:`run_transbench_graph`), so this is cheap and safe to call more
    than once."""
    graph = StateGraph(TransBenchState)
    graph.add_node("decompose", _decompose_node)
    graph.add_node("hypothesize", _hypothesize_node)
    graph.add_node("retrieve_and_grade", _retrieve_and_grade_node)
    graph.add_node("assemble_placeholder", _assemble_placeholder_node)
    graph.add_edge(START, "decompose")
    graph.add_edge("decompose", "hypothesize")
    graph.add_edge("hypothesize", "retrieve_and_grade")
    graph.add_edge("retrieve_and_grade", "assemble_placeholder")
    graph.add_edge("assemble_placeholder", END)
    return graph.compile()


# Compiled once at import time — topology-only, no I/O (see build_graph docstring).
_COMPILED_GRAPH = build_graph()


async def run_transbench_graph(
    observation: str,
    focus_drug: Optional[str] = None,
    user_key: Optional[str] = None,
    *,
    user_provider: str = "anthropic",
    model_reasoning: str = config.MODEL_REASONING,
    model_cheap: str = config.MODEL_CHEAP,
    max_hypotheses: int = config.MAX_HYPOTHESES,
    retrieval_snapshot: Optional[dict] = None,
) -> TransBrief:
    """Run the compiled graph and return the resulting ``TransBrief``.

    Phase 3: decompose + hypothesize + (per-hypothesis) retrieve + grade all
    make REAL calls — Anthropic (``agents.build_llm(...).bind(temperature=0)``
    + ``await llm.ainvoke(...)``, BUILD_SPEC.md §5/§0.7) and PubMed (via
    ``fetch_evidence_data``, HTTP-only, DB-free). ``user_key`` MUST be a
    usable BYOK key for the Anthropic calls to succeed —
    ``engine.run_transbench`` falls back to ``config.ANTHROPIC_API_KEY`` (the
    process env) when the caller doesn't pass one explicitly (BUILD_SPEC.md
    §0.4). A missing/invalid key surfaces as
    :class:`transbench.agents.TransBenchLLMError`, not a raw
    ``fastapi.HTTPException``.

    Manages the shared HTTP client lifecycle exactly once per run
    (BUILD_SPEC.md §3): ``init_http_client()`` before the graph runs,
    ``shutdown_http_client()`` in a ``finally`` block after — so it is closed
    even if an agent raises (e.g. a bad BYOK key).
    """
    initial_state: TransBenchState = {
        "observation": observation,
        "focus_drug": focus_drug,
        "max_hypotheses": max_hypotheses,
        "user_key": user_key,
        "user_provider": user_provider,
        "model_reasoning": model_reasoning,
        "model_cheap": model_cheap,
        "retrieval_snapshot": retrieval_snapshot,
    }
    await init_http_client()
    try:
        final_state = await _COMPILED_GRAPH.ainvoke(initial_state)
    finally:
        await shutdown_http_client()
    return final_state["brief"]
