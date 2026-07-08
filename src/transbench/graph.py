"""graph.py — LangGraph orchestration (BUILD_SPEC.md §5, KICKOFF.md Phase 2).

Phase 1 shipped a single-node stub. Phase 2 (this file, today) wires the
first two REAL agents — Decomposer (Haiku) and Hypothesis Generator (Sonnet,
BUILD_SPEC.md §5) — so the flagship observation now flows through real LLM
calls and populates ``TransBrief.axes`` + ``hypotheses`` for real.
Downstream stages (retrieve/grade/novelty/rigor/design/assemble — agents
3-8) remain an accurate, clearly-labeled placeholder until Phases 3-5.

Phases 3-5 grow this into the full topology (BUILD_SPEC.md §5, last
paragraph): ``START -> decompose -> hypothesize -> fan-out(retrieve -> grade
-> novelty) -> rigor -> design -> assemble -> END``. The ``TransBenchState``
TypedDict below is shaped as a superset target so later phases add
fields/nodes without reshaping what's already established.
"""
from __future__ import annotations

import datetime as _dt
from typing import Optional, TypedDict

from langgraph.graph import END, START, StateGraph

from transbench import agents, config
from transbench.reuse import REUSE_SOURCE
from transbench.schemas import (
    DecomposedAxis,
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
    observation/focus_drug/.../axes/hypotheses; Phases 3-5 add retrieve/
    grade/novelty/rigor/design outputs (evidence, references,
    contradictions, ...) to this same dict rather than a parallel shape.
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


def _assemble_placeholder_node(state: TransBenchState) -> TransBenchState:
    """Assembles the ``TransBrief`` from REAL axes (agent 1) and REAL
    hypotheses (agent 2). Evidence/grading/novelty/rigor/experiment/assembly
    (agents 3-8) are not wired yet (Phases 3-5) — each hypothesis is wrapped
    in a placeholder ``GradedHypothesis`` that HONESTLY reflects that: empty
    evidence, ``novelty="unsupported"`` (schema-valid; ``novelty_reason``
    explains why), ``grounded=False`` (correctly means the Phase 4 rigor gate
    would exclude these from experiment design if that stage ran). No LLM
    calls happen in this function itself — it only assembles prior nodes'
    real output into the final schema.
    """
    observation = state["observation"]
    focus_drug = state.get("focus_drug")
    axes = state.get("axes", [])
    hypotheses = state.get("hypotheses", [])

    graded_hypotheses = [
        GradedHypothesis(
            hypothesis=h,
            evidence=[],
            supporting_count=0,
            contradicting_count=0,
            novelty="unsupported",
            novelty_reason=(
                "Phase 2: no evidence retrieval/grading has run yet for this "
                "hypothesis (BUILD_SPEC.md §5 agents 3-6 — Evidence Retriever, "
                "Grader, Novelty Checker, Rigor Gate — land in Phases 3-4). "
                "This verdict is a placeholder, not a real novelty assessment."
            ),
            confidence="low",
            grounded=False,
        )
        for h in hypotheses
    ]

    top_experiment = ExperimentPlan(
        hypothesis_id=hypotheses[0].id if hypotheses else "stub-0",
        question="Phase 2 placeholder — no experiment designer wired yet.",
        dataset=_DEFAULT_DATASET,
        dataset_pointer=_DEFAULT_DATASET_POINTER,
        method="Not yet designed (BUILD_SPEC.md §5 agent 7 / Experiment Designer lands in Phase 5).",
        protocol_steps=["Phase 2 placeholder — no real protocol has been designed yet."],
        confirm_if="N/A — not yet designed.",
        refute_if="N/A — not yet designed.",
        feasibility_notes="Placeholder TransBrief: no real experiment design has run yet.",
        claude_science_prompt="N/A — not yet designed.",
    )

    run_manifest = {
        "phase": "2-agents-1-2",
        "reuse_source": REUSE_SOURCE,
        "model_reasoning": state.get("model_reasoning", config.MODEL_REASONING),
        "model_cheap": state.get("model_cheap", config.MODEL_CHEAP),
        "temperature": config.TEMPERATURE,
        "max_hypotheses": state.get("max_hypotheses", config.MAX_HYPOTHESES),
        "abstract_cap": config.ABSTRACT_CAP,
        "focus_drug": focus_drug,
        "retrieval_snapshot_provided": state.get("retrieval_snapshot") is not None,
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
            "Phase 2: decomposition (agent 1) and hypothesis generation "
            "(agent 2) are real LLM output. Evidence retrieval, grading, "
            "novelty classification, the rigor gate, experiment design, and "
            "final brief assembly (agents 3-8) have not run yet — those land "
            "in Phases 3-5. Treat every hypothesis below as ungrounded until "
            "then."
        ),
        run_manifest=run_manifest,
    )
    return {**state, "brief": brief}


def build_graph():
    """Compile the ``StateGraph``: ``START -> decompose -> hypothesize ->
    assemble_placeholder -> END``. Pure graph construction — no LLM client is
    built at compile time (clients are built per-call inside the async
    decompose/hypothesize nodes, using that call's own ``user_key``), so this
    is cheap and safe to call more than once."""
    graph = StateGraph(TransBenchState)
    graph.add_node("decompose", _decompose_node)
    graph.add_node("hypothesize", _hypothesize_node)
    graph.add_node("assemble_placeholder", _assemble_placeholder_node)
    graph.add_edge(START, "decompose")
    graph.add_edge("decompose", "hypothesize")
    graph.add_edge("hypothesize", "assemble_placeholder")
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

    Phase 2: decompose + hypothesize make REAL Anthropic calls via
    ``agents.build_llm(...).bind(temperature=0)`` + ``await llm.ainvoke(...)``
    (BUILD_SPEC.md §5/§0.7). ``user_key`` MUST be a usable BYOK key for this
    to succeed — ``engine.run_transbench`` falls back to
    ``config.ANTHROPIC_API_KEY`` (the process env) when the caller doesn't
    pass one explicitly (BUILD_SPEC.md §0.4). A missing/invalid key surfaces
    as :class:`transbench.agents.TransBenchLLMError`, not a raw
    ``fastapi.HTTPException``.
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
    final_state = await _COMPILED_GRAPH.ainvoke(initial_state)
    return final_state["brief"]
