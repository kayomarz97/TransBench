"""graph.py — LangGraph orchestration (BUILD_SPEC.md §5, KICKOFF.md Phase 1).

Phase 1 (this file, today): a single-node ``StateGraph`` that echoes the
request into a schema-valid stub ``TransBrief`` — no LLM, retrieval, or
network calls anywhere in this module.

Phases 2-5 grow this into the real topology (BUILD_SPEC.md §5, last
paragraph): ``START -> decompose -> hypothesize -> fan-out(retrieve -> grade
-> novelty) -> rigor -> design -> assemble -> END``. The ``TransBenchState``
TypedDict below is deliberately shaped as a superset target so later phases
add fields/nodes without reshaping what Phase 1 already established.
"""
from __future__ import annotations

import datetime as _dt
from typing import Optional, TypedDict

from langgraph.graph import END, START, StateGraph

from transbench import config
from transbench.reuse import REUSE_SOURCE
from transbench.schemas import DecomposedAxis, ExperimentPlan, TransBrief

# Default fallback experiment substrate (BUILD_SPEC.md §8: "Pinned dataset
# (reproducibility)" — a real, versioned, publicly downloadable human
# single-cell atlas with a T-cell compartment, guaranteed to resolve). Used
# only as stub/placeholder content in Phase 1; the real experiment designer
# (agent 7, Phase 5) makes the actual named-dataset decision.
_DEFAULT_DATASET = "Tabula Sapiens (immune compartment)"
_DEFAULT_DATASET_POINTER = "https://tabula-sapiens-portal.ds.czbiohub.org/"


class TransBenchState(TypedDict, total=False):
    """State threaded through the graph. Phase 1 populates/reads only what the
    stub node needs; Phases 2-5 add decompose/hypothesize/retrieve/grade/
    novelty/rigor/design outputs (axes, hypotheses, evidence, references,
    contradictions, ...) to this same dict rather than introducing a parallel
    state shape.
    """

    observation: str
    focus_drug: Optional[str]
    max_hypotheses: int
    user_key: Optional[str]
    user_provider: str
    model_reasoning: str
    model_cheap: str
    retrieval_snapshot: Optional[dict]
    brief: TransBrief


def _stub_node(state: TransBenchState) -> TransBenchState:
    """Phase-1 placeholder node: echoes the input into a schema-valid
    ``TransBrief``. No LLM calls, no retrieval, nothing read from or written
    to Iatronix — agents 1-8 (BUILD_SPEC.md §5) replace this node's guts
    across Phases 2-5; the graph topology grows around it, this function does
    not survive past Phase 1 in its current form.
    """
    observation = state["observation"]
    focus_drug = state.get("focus_drug")

    axes = [
        DecomposedAxis(
            axis="other",
            rationale=(
                "Phase 1 skeleton stub — no decomposition agent wired yet "
                "(BUILD_SPEC.md §5 agent 1 / Decomposer lands in Phase 2)."
            ),
            key_entities=[focus_drug] if focus_drug else [],
        )
    ]

    top_experiment = ExperimentPlan(
        hypothesis_id="stub-0",
        question="Phase 1 placeholder — no experiment designer wired yet.",
        dataset=_DEFAULT_DATASET,
        dataset_pointer=_DEFAULT_DATASET_POINTER,
        method="Not yet designed (BUILD_SPEC.md §5 agent 7 / Experiment Designer lands in Phase 5).",
        protocol_steps=["Phase 1 stub — placeholder step; no real protocol has been designed."],
        confirm_if="N/A — stub.",
        refute_if="N/A — stub.",
        feasibility_notes="Stub TransBrief: no real experiment design has run yet.",
        claude_science_prompt="N/A — stub.",
    )

    run_manifest = {
        "phase": "1-skeleton-stub",
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
        hypotheses=[],
        top_experiment=top_experiment,
        references=[],
        contradictions_surfaced=[],
        uncertainty_note=(
            "Phase 1 skeleton stub: no real decomposition, hypothesis "
            "generation, retrieval, grading, novelty, rigor, or experiment "
            "design has run. This brief only proves the schema contract end "
            "to end (KICKOFF.md Phase 1 acceptance)."
        ),
        run_manifest=run_manifest,
    )
    return {**state, "brief": brief}


def build_graph():
    """Compile the (currently single-node) ``StateGraph``. Pure graph
    construction — no LLM client is built and no network/DB call happens
    here, so this is cheap and safe to call more than once."""
    graph = StateGraph(TransBenchState)
    graph.add_node("stub", _stub_node)
    graph.add_edge(START, "stub")
    graph.add_edge("stub", END)
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

    Phase 1: a single stub node, no LLM/network/DB calls. ``user_key`` is
    accepted and threaded into state for interface stability across phases —
    it is unused until Phase 2 wires real ``create_llm(..., user_key=...)``
    calls (BUILD_SPEC.md §0.4 BYOK).
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
