"""test_fanout_resilience.py — CODE_REVIEW Finding 1 regression guard.

The two per-hypothesis fan-out nodes run up to ``config.CONCURRENCY``
hypotheses through ``asyncio.gather``. A single malformed LLM response for ONE
hypothesis (``TransBenchLLMError`` from the grader, entailment, or novelty
call) must NOT propagate out of the node and fail the ENTIRE run — discarding
the other hypotheses' completed work. Each fan-out task isolates that error and
degrades just its own hypothesis (empty graded evidence in the retrieve/grade
node; an honest ``unsupported``/ungrounded/``low`` record in the rigor node),
matching ``run_retrieve``'s existing "never crash the batch" contract.

Fully OFFLINE — no key, no network, no real LLM call. Fake doubles stand in for
every agent/LLM call; ``agents.build_llm`` is stubbed so no real client is ever
constructed. (``import transbench.graph`` compiles the LangGraph topology at
import time — that is I/O-free and safe here.)
"""
from __future__ import annotations

import asyncio
from types import SimpleNamespace

import pytest

from transbench import agents, config, graph, rigor
from transbench.agents import TransBenchLLMError
from transbench.schemas import EvidenceItem, Hypothesis, Reference


def _hyp(hid: str) -> Hypothesis:
    return Hypothesis(
        id=hid,
        axis="immune_inflammatory",
        statement=f"Statement {hid}",
        prediction=f"Prediction {hid}",
        rationale="r",
        priority="high",
    )


def _evidence(pmid: str = "12345678") -> EvidenceItem:
    return EvidenceItem(
        claim_fragment="c",
        reference=Reference(
            source="PubMed",
            title="t",
            year=2020,
            url=f"https://pubmed.ncbi.nlm.nih.gov/{pmid}/",
            pmid=pmid,
            grade="observational",
        ),
        supports=True,
        entailment="supports",
        grade="observational",
    )


def test_retrieve_and_grade_node_isolates_one_grade_failure(monkeypatch: pytest.MonkeyPatch) -> None:
    """One hypothesis's ``run_grade`` raises ``TransBenchLLMError``; the node
    must still return evidence for ALL three hypotheses, with the failed one
    degraded to zero graded evidence — never propagate and fail the batch."""
    hyps = [_hyp("h1"), _hyp("h2"), _hyp("h3")]

    async def _fake_retrieve(hyp: Hypothesis, user_key, **kwargs) -> SimpleNamespace:
        return SimpleNamespace(
            neutral_query="nq",
            pubmed_query="pq",
            tier_queries=[],
            ranked=[{"pmid": "111"}],
            registry=object(),
            fd=None,
        )

    async def _fake_grade(hyp: Hypothesis, ranked, registry, fd, user_key, **kwargs):
        if hyp.id == "h2":
            raise TransBenchLLMError(502, "llm_bad_json", "malformed grader response")
        return [_evidence()]

    monkeypatch.setattr(agents, "run_retrieve", _fake_retrieve)
    monkeypatch.setattr(agents, "run_grade", _fake_grade)

    state = {
        "hypotheses": hyps,
        "observation": "obs",
        "condition_anchor": None,
        "user_key": None,
        "user_provider": "anthropic",
        "model_cheap": config.MODEL_CHEAP,
        "retrieval_snapshot": None,
    }
    out = asyncio.run(graph._retrieve_and_grade_node(state))
    ev_by = out["evidence_by_hyp_id"]

    assert set(ev_by) == {"h1", "h2", "h3"}, "no hypothesis may be lost when one grader fails"
    assert len(ev_by["h1"]) == 1
    assert len(ev_by["h3"]) == 1
    assert ev_by["h2"] == [], "the failed hypothesis degrades to zero graded evidence; run continues"


def test_rigor_node_isolates_one_novelty_failure(monkeypatch: pytest.MonkeyPatch) -> None:
    """One hypothesis's ``run_novelty`` raises ``TransBenchLLMError``; the node
    must still return rigor data for ALL three, with the failed one degraded to
    the honest ``unsupported``/ungrounded/``low`` record (so it is excluded from
    experiment design) — never fail the batch."""
    hyps = [_hyp("h1"), _hyp("h2"), _hyp("h3")]
    evidence_by_hyp_id = {h.id: [_evidence()] for h in hyps}

    monkeypatch.setattr(agents, "build_llm", lambda *a, **k: object())

    async def _fake_entailment(hyp: Hypothesis, evidence, llm):
        return evidence  # unchanged; already carries entailment="supports"

    async def _fake_novelty(hyp: Hypothesis, evidence, llm):
        if hyp.id == "h2":
            raise TransBenchLLMError(502, "llm_bad_output", "invalid novelty verdict")
        return "open_question", f"reason for {hyp.id}"

    monkeypatch.setattr(rigor, "run_entailment", _fake_entailment)
    monkeypatch.setattr(rigor, "run_novelty", _fake_novelty)

    state = {
        "hypotheses": hyps,
        "evidence_by_hyp_id": evidence_by_hyp_id,
        "user_key": None,
        "user_provider": "anthropic",
        "model_cheap": config.MODEL_CHEAP,
        "model_reasoning": config.MODEL_REASONING,
    }
    out = asyncio.run(graph._rigor_and_novelty_node(state))
    rigor_by = out["rigor_by_hyp_id"]

    assert set(rigor_by) == {"h1", "h2", "h3"}, "no hypothesis may be lost when one novelty call fails"
    assert rigor_by["h1"]["novelty"] == "open_question"
    assert rigor_by["h3"]["novelty"] == "open_question"

    degraded = rigor_by["h2"]
    assert degraded["novelty"] == "unsupported"
    assert degraded["grounded"] is False
    assert degraded["confidence"] == "low"
