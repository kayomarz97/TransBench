"""graph.py — LangGraph orchestration (BUILD_SPEC.md §5, KICKOFF.md Phase 5).

Phase 1 shipped a single-node stub. Phase 2 wired agents 1-2 (Decomposer,
Hypothesis Generator). Phase 3 wired agents 3-4 (Evidence Retriever, Evidence
Grader). Phase 4 wired agents 5-6 (Novelty Checker; dedicated batched
entailment + grounding enforcement — BUILD_SPEC.md §5/§6). Phase 5 (this
file, today) wires the last two agents — 7 (Experiment Designer) and 8 (Brief
Assembler, ``agents.run_assemble``) — completing the full topology
(BUILD_SPEC.md §5, last paragraph): ``START -> decompose -> hypothesize ->
retrieve_and_grade -> rigor_and_novelty -> design -> assemble -> END``.
``run_transbench_graph`` now returns the REAL, final ``TransBrief`` end to
end: real ``references``/``contradictions_surfaced``/``uncertainty_note``/
``run_manifest`` (including the BUILD_SPEC.md §9 retrieval snapshot + token
spend), and a real ``top_experiment`` naming a resolvable dataset whenever a
hypothesis clears the novelty guard.
"""
from __future__ import annotations

import asyncio
import datetime as _dt
import logging
from typing import Any, Optional, TypedDict

from langgraph.graph import END, START, StateGraph

from transbench import agents, config, rigor
from transbench.reuse import init_http_client, shutdown_http_client
from transbench.schemas import (
    DecomposedAxis,
    EvidenceItem,
    ExperimentPlan,
    GradedHypothesis,
    Hypothesis,
    TransBrief,
)

logger = logging.getLogger(__name__)


class TransBenchState(TypedDict, total=False):
    """State threaded through the graph. Phases 1-2 populate/read
    observation/focus_drug/.../axes/hypotheses; Phase 3 adds per-hypothesis
    evidence + a retrieval snapshot; Phase 4 adds per-hypothesis rigor
    (entailment-updated evidence, novelty, confidence, grounded); Phase 5
    adds the built ``GradedHypothesis`` list, the designed (or absent)
    experiment plan, each hypothesis's own ``ArticleRegistry`` (needed by
    ``agents.run_assemble`` to build ``references``), and a run-start
    timestamp (for ``run_manifest``) to this same dict rather than a
    parallel shape. Phase 8 (domain-universalization) adds
    ``condition_anchor`` — the Decomposer's own LLM-extracted primary
    disease/condition for this run (``agents.DecomposeResult.
    condition_anchor``), populated by ``_decompose_node`` and consumed by
    ``_retrieve_and_grade_node`` as the real PubMed retrieval anchor for
    EVERY hypothesis (replacing the old hardcoded-to-hypertension default —
    see ``agents.py``'s ``_condition_anchor``/``run_retrieve`` docstrings).
    """

    observation: str
    focus_drug: Optional[str]
    max_hypotheses: int
    user_key: Optional[str]
    user_provider: str
    model_reasoning: str
    model_deep: str
    model_cheap: str
    retrieval_snapshot: Optional[dict]
    run_started_at: str
    axes: list[DecomposedAxis]
    condition_anchor: Optional[str]
    hypotheses: list[Hypothesis]
    evidence_by_hyp_id: dict[str, list[EvidenceItem]]
    retrieval_manifest_by_hyp_id: dict[str, dict]
    registry_by_hyp_id: dict[str, Any]
    rigor_by_hyp_id: dict[str, dict]
    graded_hypotheses: list[GradedHypothesis]
    top_experiment: Optional[ExperimentPlan]
    brief: TransBrief


async def _decompose_node(state: TransBenchState) -> TransBenchState:
    """Agent 1 — Decomposer (reasoning tier, ``config.MODEL_REASONING``). Real LLM call —
    builds its own temperature-0 client via :func:`agents.build_llm` for
    this one call (BUILD_SPEC.md §5: "Build clients once").

    ``agents.run_decompose`` now returns a ``DecomposeResult`` (Phase 8,
    domain-universalization) rather than a bare axes list — its
    ``condition_anchor`` is written into state here so
    :func:`_retrieve_and_grade_node` can thread it into every hypothesis's
    real PubMed retrieval anchor.
    """
    # Decompose now REASONS about the observation's TYPE (drug-toxicity vs
    # disease-response) to choose a focused condition_anchor, so it runs on the
    # reasoning tier (Sonnet), not Haiku: Haiku was unreliable at excluding an
    # incidental indication — e.g. anchoring retrieval on 'atrial fibrillation'
    # for an amiodarone-neutropenia observation, grounding zero evidence.
    llm = agents.build_llm(
        state.get("model_reasoning", config.MODEL_REASONING),
        state.get("user_key"),
        state.get("user_provider", "anthropic"),
    )
    result = await agents.run_decompose(
        {"observation": state["observation"], "focus_drug": state.get("focus_drug")},
        llm,
    )
    return {**state, "axes": result.axes, "condition_anchor": result.condition_anchor}


async def _hypothesize_node(state: TransBenchState) -> TransBenchState:
    """Agent 2 — Hypothesis Generator (deep-reasoning tier, ``config.MODEL_DEEP``). Real
    LLM call — grounded in agent 1's axes output. Runs on the deep-reasoning tier:
    the creative core (novel, falsifiable mechanisms) is the biggest quality lever."""
    llm = agents.build_llm(
        state.get("model_deep", config.MODEL_DEEP),
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

    ``condition_anchor`` (Phase 8, domain-universalization): read once from
    state (written by :func:`_decompose_node`, the Decomposer's own
    LLM-extracted primary disease/condition for this run — e.g. "rheumatoid
    arthritis") and passed to every hypothesis's :func:`agents.run_retrieve`
    call, so every hypothesis's PubMed query anchors on the SAME real
    condition instead of the old hardcoded-to-hypertension default.
    """
    hypotheses = state.get("hypotheses", [])
    observation = state["observation"]
    condition_anchor = state.get("condition_anchor")
    user_key = state.get("user_key")
    user_provider = state.get("user_provider", "anthropic")
    model_cheap = state.get("model_cheap", config.MODEL_CHEAP)
    retrieval_snapshot = state.get("retrieval_snapshot")

    semaphore = asyncio.Semaphore(config.CONCURRENCY)

    async def _one(hypothesis: Hypothesis) -> tuple[str, list[EvidenceItem], dict, Any]:
        async with semaphore:
            retrieval = await agents.run_retrieve(
                hypothesis,
                user_key,
                user_provider=user_provider,
                model_id=model_cheap,
                observation=observation,
                condition_anchor=condition_anchor,
                retrieval_snapshot=retrieval_snapshot,
            )
            # Per-hypothesis error isolation (CODE_REVIEW Finding 1): run_retrieve
            # already never raises, but run_grade CAN raise TransBenchLLMError on a
            # malformed grader response (agents._parse_json/_coerce_list). Without
            # this guard a single bad grader reply fails the whole asyncio.gather
            # batch and discards every OTHER hypothesis's completed work. Degrade
            # just this hypothesis to zero graded evidence instead — the rigor node
            # then classifies it "unsupported"/ungrounded exactly as it does a
            # genuinely zero-evidence hypothesis. Extends run_retrieve's own
            # "never crash the batch" contract to the grade step.
            try:
                evidence = await agents.run_grade(
                    hypothesis,
                    retrieval.ranked,
                    retrieval.registry,
                    retrieval.fd,
                    user_key,
                    user_provider=user_provider,
                    model_id=model_cheap,
                )
            except agents.TransBenchLLMError:
                logger.warning(
                    "retrieve_and_grade: grader failed for hypothesis %r — "
                    "degrading to zero graded evidence (run continues)",
                    hypothesis.id,
                    exc_info=True,
                )
                evidence = []
        # Retrieval snapshot for run_manifest (BUILD_SPEC.md §3/§9): the
        # neutral + pubmed query and the FULL ranked/capped abstract dicts
        # (pmid/title/year/abstract text/journal/etc. — whatever
        # rank_article_list actually returned, not a hand-picked field
        # subset) so a later `TransRequest.retrieval_snapshot` replay
        # (agents._replay_from_snapshot) has everything it needs to
        # reconstruct a real ArticleRegistry and re-grade offline. "statement"
        # (post-release addition) records the EXACT hypothesis statement this
        # entry was captured for -- agents.run_retrieve's STATEMENT-match
        # safety guard reads this back on a later replay attempt (e.g. a
        # bundled TRANSBENCH_MODE=snapshot file) to refuse replaying this
        # entry against a differently-worded hypothesis that merely happens
        # to reuse the same id (agent 2 assigns ids positionally -- "h1" on
        # one run and "h1" on a later run are NOT guaranteed to be the same
        # hypothesis).
        manifest_entry = {
            "neutral_query": retrieval.neutral_query,
            "pubmed_query": retrieval.pubmed_query,
            "tier_queries": retrieval.tier_queries,
            "abstracts": retrieval.ranked,
            "statement": hypothesis.statement,
        }
        return hypothesis.id, evidence, manifest_entry, retrieval.registry

    results = await asyncio.gather(*[_one(h) for h in hypotheses])

    evidence_by_hyp_id = {hyp_id: evidence for hyp_id, evidence, _, _ in results}
    retrieval_manifest_by_hyp_id = {hyp_id: manifest for hyp_id, _, manifest, _ in results}
    registry_by_hyp_id = {hyp_id: registry for hyp_id, _, _, registry in results}
    return {
        **state,
        "evidence_by_hyp_id": evidence_by_hyp_id,
        "retrieval_manifest_by_hyp_id": retrieval_manifest_by_hyp_id,
        "registry_by_hyp_id": registry_by_hyp_id,
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

    A hypothesis with zero evidence still gets a real novelty call (the
    reasoning tier correctly classifies it "unsupported" given "no evidence
    retrieved" —
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
        try:
            async with semaphore:
                entailment_llm = agents.build_llm(model_cheap, user_key, user_provider)
                evidence = await rigor.run_entailment(hypothesis, evidence, entailment_llm)

                grounded, grounded_supporting_count = rigor.compute_grounding(evidence)

                novelty_llm = agents.build_llm(model_reasoning, user_key, user_provider)
                novelty, novelty_reason = await rigor.run_novelty(hypothesis, evidence, novelty_llm)
        except agents.TransBenchLLMError:
            # Per-hypothesis error isolation (CODE_REVIEW Finding 1): a malformed
            # entailment or novelty response (rigor.run_entailment / run_novelty →
            # TransBenchLLMError) must not fail the whole asyncio.gather batch and
            # discard the other hypotheses. Degrade just this hypothesis to the
            # honest "rigor gate could not complete" record — unsupported,
            # ungrounded, low confidence — mirroring _build_graded_hypotheses's
            # missing-rigor fallback. `evidence` keeps whatever entailment state it
            # reached (provisional "unclear" if entailment itself failed), so the
            # counts below stay truthful.
            logger.warning(
                "rigor_and_novelty: rigor gate failed for hypothesis %r — "
                "degrading to unsupported/ungrounded (run continues)",
                hypothesis.id,
                exc_info=True,
            )
            return hypothesis.id, {
                "evidence": evidence,
                "supporting_count": sum(1 for e in evidence if e.entailment == "supports"),
                "contradicting_count": sum(1 for e in evidence if e.entailment == "refutes"),
                "novelty": "unsupported",
                "novelty_reason": (
                    "Rigor gate (agents 5-6, BUILD_SPEC.md §5/§6) could not complete for "
                    "this hypothesis (malformed model response); classified unsupported "
                    "and excluded from experiment design."
                ),
                "confidence": "low",
                "grounded": False,
            }

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


def _build_graded_hypotheses(
    hypotheses: list[Hypothesis],
    evidence_by_hyp_id: dict[str, list[EvidenceItem]],
    rigor_by_hyp_id: dict[str, dict],
) -> list[GradedHypothesis]:
    """Builds the final ``list[GradedHypothesis]`` from agents 1-6's real
    output — relocated, unchanged, from the Phase-4 ``_assemble_node`` stub.
    Built exactly ONCE per run, in :func:`_design_node` (which needs it to
    call ``rigor.select_experiment_candidate``), then threaded through
    ``state`` to :func:`_assemble_node` (``agents.run_assemble``) rather than
    rebuilt a second time.
    """
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
                    supporting_count=sum(1 for e in evidence if e.entailment == "supports"),
                    contradicting_count=sum(1 for e in evidence if e.entailment == "refutes"),
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
    return graded_hypotheses


async def _design_node(state: TransBenchState) -> TransBenchState:
    """Agent 7 — Experiment Designer (deep-reasoning tier, ``config.MODEL_DEEP``) —
    BUILD_SPEC.md §5. Builds the final ``graded_hypotheses`` list (see
    :func:`_build_graded_hypotheses`) and runs ``rigor.select_experiment_
    candidate`` over it (the novelty guard: only a hypothesis both
    ``open_question`` and ``grounded`` is eligible). Makes a REAL deep-tier call
    via ``agents.run_design_experiment`` ONLY when a candidate is eligible —
    a run where every hypothesis is ``established`` or ungrounded makes
    ZERO deep-tier calls in this node at all (nothing eligible to design for);
    ``top_experiment`` is left ``None`` in that case, and
    ``agents.run_assemble`` substitutes the honest "no eligible experiment"
    sentinel instead of a fabricated design.
    """
    hypotheses = state.get("hypotheses", [])
    evidence_by_hyp_id: dict[str, list[EvidenceItem]] = state.get("evidence_by_hyp_id", {})
    rigor_by_hyp_id: dict[str, dict] = state.get("rigor_by_hyp_id", {})
    graded_hypotheses = _build_graded_hypotheses(hypotheses, evidence_by_hyp_id, rigor_by_hyp_id)

    selected = rigor.select_experiment_candidate(graded_hypotheses)

    top_experiment: Optional[ExperimentPlan] = None
    if selected is not None:
        # Experiment design runs on the deep-reasoning tier — it authors the
        # runnable claude_science_prompt, which is the deliverable.
        llm = agents.build_llm(
            state.get("model_deep", config.MODEL_DEEP),
            state.get("user_key"),
            state.get("user_provider", "anthropic"),
        )
        top_experiment = await agents.run_design_experiment(selected.hypothesis, selected.evidence, llm)

    return {**state, "graded_hypotheses": graded_hypotheses, "top_experiment": top_experiment}


async def _assemble_node(state: TransBenchState) -> TransBenchState:
    """Agent 8 — Brief Assembler (mechanical tier, ``config.MODEL_CHEAP``) —
    BUILD_SPEC.md §5/§8. Builds this call's own temperature-0 client (agents
    1-2's convention) and delegates entirely to ``agents.run_assemble``,
    which reads everything else it needs (axes, ``graded_hypotheses``,
    ``top_experiment``, per-hypothesis registries, the retrieval manifest,
    ...) directly off this same ``state`` dict — a ``TypedDict`` IS a plain
    ``dict`` at runtime, so no repacking is needed here.
    """
    llm = agents.build_llm(
        state.get("model_cheap", config.MODEL_CHEAP),
        state.get("user_key"),
        state.get("user_provider", "anthropic"),
    )
    brief = await agents.run_assemble(state, llm)
    return {**state, "brief": brief}


def build_graph():
    """Compile the ``StateGraph``: ``START -> decompose -> hypothesize ->
    retrieve_and_grade -> rigor_and_novelty -> design -> assemble -> END``
    (BUILD_SPEC.md §5's full topology, complete as of Phase 5). Pure graph
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
    graph.add_node("design", _design_node)
    graph.add_node("assemble", _assemble_node)
    graph.add_edge(START, "decompose")
    graph.add_edge("decompose", "hypothesize")
    graph.add_edge("hypothesize", "retrieve_and_grade")
    graph.add_edge("retrieve_and_grade", "rigor_and_novelty")
    graph.add_edge("rigor_and_novelty", "design")
    graph.add_edge("design", "assemble")
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
    model_deep: str = config.MODEL_DEEP,
    model_cheap: str = config.MODEL_CHEAP,
    max_hypotheses: int = config.MAX_HYPOTHESES,
    retrieval_snapshot: Optional[dict] = None,
) -> TransBrief:
    """Run the compiled graph and return the resulting REAL ``TransBrief``
    (Phase 5: every agent, 1-8, now makes its real call — this is the full
    pipeline, not a placeholder anywhere).

    decompose + hypothesize + (per-hypothesis) retrieve + grade + entailment
    + novelty + (conditionally) design + assemble all make REAL calls —
    Anthropic (``agents.build_llm(...).bind(temperature=0)`` + ``await
    llm.ainvoke(...)``, BUILD_SPEC.md §5/§0.7) and PubMed (via
    ``fetch_evidence_data``, HTTP-only, DB-free — skipped entirely for any
    hypothesis a ``retrieval_snapshot`` REPLAYs, BUILD_SPEC.md §9). ``user_key``
    MUST be a usable BYOK key for the Anthropic calls to succeed —
    ``engine.run_transbench`` falls back to ``config.ANTHROPIC_API_KEY`` (the
    process env) when the caller doesn't pass one explicitly (BUILD_SPEC.md
    §0.4). A missing/invalid key surfaces as
    :class:`transbench.agents.TransBenchLLMError`, not a raw
    ``fastapi.HTTPException``.

    Manages the shared HTTP client lifecycle exactly once per run
    (BUILD_SPEC.md §3): ``init_http_client()`` before the graph runs,
    ``shutdown_http_client()`` in a ``finally`` block after — so it is closed
    even if an agent raises (e.g. a bad BYOK key). Wraps the graph invocation
    in ``agents.token_spend_session()`` (BUILD_SPEC.md §9: "run_manifest
    records ... token spend per run") — every real LLM call any node makes,
    including ones fanned out via ``asyncio.gather``, accumulates into that
    one session; ``agents.run_assemble`` (the graph's last node, so it runs
    before this ``with`` block exits) reads the running total into
    ``run_manifest["token_spend"]``.
    """
    run_started_at = _dt.datetime.now(_dt.timezone.utc).isoformat()
    initial_state: TransBenchState = {
        "observation": observation,
        "focus_drug": focus_drug,
        "max_hypotheses": max_hypotheses,
        "user_key": user_key,
        "user_provider": user_provider,
        "model_reasoning": model_reasoning,
        "model_deep": model_deep,
        "model_cheap": model_cheap,
        "retrieval_snapshot": retrieval_snapshot,
        "run_started_at": run_started_at,
    }
    await init_http_client()
    try:
        with agents.token_spend_session():
            final_state = await _COMPILED_GRAPH.ainvoke(initial_state)
    finally:
        await shutdown_http_client()
    return final_state["brief"]
