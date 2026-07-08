"""graph.py — LangGraph orchestration (BUILD_SPEC.md §5, KICKOFF.md Phase 4).

Phase 1 shipped a single-node stub. Phase 2 wired agents 1-2 (Decomposer,
Hypothesis Generator). Phase 3 wired agents 3-4 (Evidence Retriever, Evidence
Grader). Phase 4 (this file, today) wires agents 5-6 (Novelty Checker;
dedicated batched entailment + grounding enforcement — BUILD_SPEC.md §5/§6),
so ``GradedHypothesis.evidence[].entailment``/``novelty``/``novelty_reason``/
``confidence``/``grounded`` are now real, and ``top_experiment.hypothesis_id``
reflects a REAL novelty-guarded selection (``rigor.select_experiment_
candidate``) instead of blindly picking the first hypothesis. Agents 7-8
(experiment design, real assembler) remain an accurate, clearly-labeled
placeholder until Phase 5.

Phase 5 grows this into the full topology (BUILD_SPEC.md §5, last
paragraph): ``START -> decompose -> hypothesize -> fan-out(retrieve -> grade
-> entailment/grounding/novelty) -> design -> assemble -> END``. The
``TransBenchState`` TypedDict below is shaped as a superset target so later
phases add fields/nodes without reshaping what's already established.
"""
from __future__ import annotations

import asyncio
import datetime as _dt
from typing import Any, Optional, TypedDict

from langgraph.graph import END, START, StateGraph

from transbench import agents, config, rigor
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
    evidence + a retrieval snapshot; Phase 4 adds per-hypothesis rigor
    (entailment-updated evidence, novelty, confidence, grounded); Phase 5
    adds experiment-design + real-assembler outputs to this same dict rather
    than a parallel shape.
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
    rigor_by_hyp_id: dict[str, dict]
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


async def _rigor_and_novelty_node(state: TransBenchState) -> TransBenchState:
    """Agents 5 (Novelty Checker, Sonnet) + 6 (Rigor Gate: dedicated batched
    entailment [Haiku] + grounding enforcement [pure, reused gate]) —
    BUILD_SPEC.md §5/§6. Fanned out across hypotheses with ``asyncio.gather``
    under a concurrency cap of ``config.CONCURRENCY`` (same pattern as
    :func:`_retrieve_and_grade_node`).

    Per hypothesis: :func:`rigor.run_entailment` OVERWRITES each
    ``EvidenceItem``'s Phase-3 provisional ``entailment="unclear"`` ->
    :func:`rigor.compute_grounding` (the EXACT pseudo-response shape) ->
    :func:`rigor.run_novelty` (PMID-citing ``novelty_reason``) ->
    :func:`rigor.compute_confidence`. ``supporting_count``/
    ``contradicting_count`` are RECOMPUTED here from the now-real
    ``entailment`` field (``supports``/``refutes``; ``"unclear"`` counts as
    NEITHER) — Phase 3 used the grader's coarser ``supports`` boolean as a
    placeholder since entailment wasn't reliable yet; BUILD_SPEC.md §6(1)
    frames dedicated entailment as the authoritative signal ("closes the gap
    ... existence ≠ support").

    A hypothesis with zero evidence still gets a real novelty call (Sonnet
    correctly classifies it "unsupported" given "no evidence retrieved" —
    this keeps every hypothesis evaluated the same way) but SKIPS the
    entailment call entirely (``run_entailment([])`` returns ``[]`` with no
    LLM call — nothing to classify).
    """
    hypotheses = state.get("hypotheses", [])
    evidence_by_hyp_id: dict[str, list[EvidenceItem]] = state.get("evidence_by_hyp_id", {})
    user_key = state.get("user_key")
    user_provider = state.get("user_provider", "anthropic")
    model_cheap = state.get("model_cheap", config.MODEL_CHEAP)
    model_reasoning = state.get("model_reasoning", config.MODEL_REASONING)

    semaphore = asyncio.Semaphore(config.CONCURRENCY)

    async def _one(hypothesis: Hypothesis) -> tuple[str, dict]:
        evidence = evidence_by_hyp_id.get(hypothesis.id, [])
        async with semaphore:
            entailment_llm = agents.build_llm(model_cheap, user_key, user_provider)
            evidence = await rigor.run_entailment(hypothesis, evidence, entailment_llm)

            grounded, grounded_supporting_count = rigor.compute_grounding(evidence)

            novelty_llm = agents.build_llm(model_reasoning, user_key, user_provider)
            novelty, novelty_reason = await rigor.run_novelty(hypothesis, evidence, novelty_llm)

        supporting_count = sum(1 for e in evidence if e.entailment == "supports")
        contradicting_count = sum(1 for e in evidence if e.entailment == "refutes")
        confidence = rigor.compute_confidence(grounded_supporting_count, contradicting_count, evidence)

        return hypothesis.id, {
            "evidence": evidence,
            "supporting_count": supporting_count,
            "contradicting_count": contradicting_count,
            "novelty": novelty,
            "novelty_reason": novelty_reason,
            "confidence": confidence,
            "grounded": grounded,
        }

    results = await asyncio.gather(*[_one(h) for h in hypotheses])
    rigor_by_hyp_id = dict(results)
    updated_evidence_by_hyp_id = {hyp_id: data["evidence"] for hyp_id, data in rigor_by_hyp_id.items()}
    return {
        **state,
        "evidence_by_hyp_id": updated_evidence_by_hyp_id,
        "rigor_by_hyp_id": rigor_by_hyp_id,
    }


def _assemble_node(state: TransBenchState) -> TransBenchState:
    """Assembles the ``TransBrief`` from REAL axes (agent 1), REAL
    hypotheses (agent 2), and REAL graded+entailed+grounded+novelty-
    classified evidence (agents 3-6). ``top_experiment``'s CONTENT and the
    real brief-assembler prose (agents 7-8) are still Phase-5 stubs, but
    ``top_experiment.hypothesis_id`` now reflects a REAL, novelty-guarded
    selection via :func:`rigor.select_experiment_candidate` — never a blind
    ``hypotheses[0]``. No LLM calls happen in this function itself — it only
    assembles prior nodes' real output into the final schema.
    """
    observation = state["observation"]
    focus_drug = state.get("focus_drug")
    axes = state.get("axes", [])
    hypotheses = state.get("hypotheses", [])
    evidence_by_hyp_id: dict[str, list[EvidenceItem]] = state.get("evidence_by_hyp_id", {})
    rigor_by_hyp_id: dict[str, dict] = state.get("rigor_by_hyp_id", {})
    retrieval_manifest_by_hyp_id: dict[str, dict] = state.get("retrieval_manifest_by_hyp_id", {})

    graded_hypotheses: list[GradedHypothesis] = []
    for h in hypotheses:
        evidence = evidence_by_hyp_id.get(h.id, [])
        rigor_data = rigor_by_hyp_id.get(h.id)
        if rigor_data is None:
            # Defensive fallback only -- the rigor node always runs for
            # every hypothesis in `hypotheses` in the normal flow; this just
            # ensures assembly never crashes if it somehow didn't.
            graded_hypotheses.append(
                GradedHypothesis(
                    hypothesis=h,
                    evidence=evidence,
                    supporting_count=sum(1 for e in evidence if e.supports),
                    contradicting_count=sum(1 for e in evidence if not e.supports),
                    novelty="unsupported",
                    novelty_reason="Rigor gate (agents 5-6, BUILD_SPEC.md §5/§6) did not run for this hypothesis.",
                    confidence="low",
                    grounded=False,
                )
            )
            continue
        graded_hypotheses.append(
            GradedHypothesis(
                hypothesis=h,
                evidence=evidence,
                supporting_count=rigor_data["supporting_count"],
                contradicting_count=rigor_data["contradicting_count"],
                novelty=rigor_data["novelty"],
                novelty_reason=rigor_data["novelty_reason"],
                confidence=rigor_data["confidence"],
                grounded=rigor_data["grounded"],
            )
        )

    selected = rigor.select_experiment_candidate(graded_hypotheses)
    selected_hypothesis_id = selected.hypothesis.id if selected else "no-eligible-hypothesis"

    top_experiment = ExperimentPlan(
        hypothesis_id=selected_hypothesis_id,
        question=(
            f"Phase 4 placeholder — no experiment designer wired yet "
            f"(BUILD_SPEC.md §5 agent 7 lands in Phase 5). Novelty-guarded "
            f"eligible candidate: {selected_hypothesis_id}."
            if selected
            else "Phase 4 placeholder — no experiment designer wired yet; "
            "no hypothesis is currently both open_question and grounded, "
            "so none is eligible for experiment design yet."
        ),
        dataset=_DEFAULT_DATASET,
        dataset_pointer=_DEFAULT_DATASET_POINTER,
        method="Not yet designed (BUILD_SPEC.md §5 agent 7 / Experiment Designer lands in Phase 5).",
        protocol_steps=["Phase 4 placeholder — no real protocol has been designed yet."],
        confirm_if="N/A — not yet designed.",
        refute_if="N/A — not yet designed.",
        feasibility_notes="Placeholder TransBrief: no real experiment design has run yet.",
        claude_science_prompt="N/A — not yet designed.",
    )

    run_manifest: dict[str, Any] = {
        "phase": "4-rigor-novelty",
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
        "selected_experiment_hypothesis_id": selected_hypothesis_id if selected else None,
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
            "Phase 4: decomposition, hypothesis generation, evidence "
            "retrieval, evidence grading, dedicated entailment "
            "classification, grounding enforcement, and novelty "
            "classification (agents 1-6) are all real output. Experiment "
            "design and final brief assembly (agents 7-8) have not run yet "
            "— those land in Phase 5. Only hypotheses graded 'open_question' "
            "AND grounded are eligible for experiment design; 'established' "
            "(textbook fact) or ungrounded hypotheses are demoted here, "
            "never deleted."
        ),
        run_manifest=run_manifest,
    )
    return {**state, "brief": brief}


def build_graph():
    """Compile the ``StateGraph``: ``START -> decompose -> hypothesize ->
    retrieve_and_grade -> rigor_and_novelty -> assemble -> END``. Pure graph
    construction — no LLM client or HTTP client is built at compile time
    (clients are built per-call inside the async nodes, using that call's own
    ``user_key``; the shared HTTP client is managed once per run by
    :func:`run_transbench_graph`), so this is cheap and safe to call more
    than once."""
    graph = StateGraph(TransBenchState)
    graph.add_node("decompose", _decompose_node)
    graph.add_node("hypothesize", _hypothesize_node)
    graph.add_node("retrieve_and_grade", _retrieve_and_grade_node)
    graph.add_node("rigor_and_novelty", _rigor_and_novelty_node)
    graph.add_node("assemble", _assemble_node)
    graph.add_edge(START, "decompose")
    graph.add_edge("decompose", "hypothesize")
    graph.add_edge("hypothesize", "retrieve_and_grade")
    graph.add_edge("retrieve_and_grade", "rigor_and_novelty")
    graph.add_edge("rigor_and_novelty", "assemble")
    graph.add_edge("assemble", END)
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

    Phase 4: decompose + hypothesize + (per-hypothesis) retrieve + grade +
    entailment + novelty all make REAL calls — Anthropic
    (``agents.build_llm(...).bind(temperature=0)`` + ``await
    llm.ainvoke(...)``, BUILD_SPEC.md §5/§0.7) and PubMed (via
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
