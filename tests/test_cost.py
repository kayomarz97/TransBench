"""tests/test_cost.py — cost/cap acceptance test (KICKOFF.md Phase 7 /
BUILD_SPEC.md §0.7, §3, §6(1), §9).

Two layers, per this phase's own guidance ("May assert against a captured
run_manifest to avoid extra spend"):

1. Fully OFFLINE / deterministic (zero API cost, zero network): a fake-LLM
   double proves entailment (``rigor.run_entailment``) is genuinely ONE
   batched call per hypothesis, regardless of how many evidence items that
   hypothesis has (1..``ABSTRACT_CAP``) — and that calling it once per
   hypothesis (not once per abstract) is what actually happens across a
   multi-hypothesis run. This is the decisive, non-flaky proof of
   "batched, not per-item" — it does not depend on any live number.
2. Against the REAL captured ``run_manifest`` from the shared
   ``flagship_brief`` fixture (``tests/conftest.py`` — ZERO additional live
   calls beyond the ONE flagship run every other live test in this suite
   already shares): ``len(hypotheses) <= MAX_HYPOTHESES``, each
   hypothesis's retrieved-abstract count ``<= ABSTRACT_CAP``, and
   ``run_manifest["token_spend"]["calls"]`` is bounded by the documented
   BUILD_SPEC.md §9 formula (scales with HYPOTHESIS count, not abstract
   count).
"""
from __future__ import annotations

import asyncio
import json
from typing import Any

import pytest

from transbench import config
from transbench.rigor import run_entailment
from transbench.schemas import EvidenceItem, Hypothesis, Reference, TransBrief

# ---------------------------------------------------------------------------
# 1. Offline: entailment is ONE batched call per hypothesis, not per-item.
# ---------------------------------------------------------------------------


class _FakeAIMessage:
    """Minimal stand-in for a LangChain ``AIMessage`` — only ``.content`` is
    read by ``agents._response_text`` (same minimal-double pattern already
    established in ``tests/test_rigor_entailment_correlation.py`` /
    ``tests/test_experiment_phase5.py``)."""

    def __init__(self, content: str) -> None:
        self.content = content


class _RecordingEntailmentLLM:
    """Fake ``llm`` double matching the ONE method
    ``agents._ainvoke_json`` calls (``await llm.ainvoke(messages)``).
    Records every call so a test can assert the CALL COUNT directly —
    returns an empty ``items`` list every time (this file is about the call
    *count*, not about entailment correctness, which
    ``tests/test_rigor_entailment_correlation.py`` already covers)."""

    def __init__(self) -> None:
        self.calls: list[list[Any]] = []

    async def ainvoke(self, messages: list[Any]) -> _FakeAIMessage:
        self.calls.append(messages)
        return _FakeAIMessage(json.dumps({"items": []}))


def _hypothesis(hyp_id: str = "h1") -> Hypothesis:
    return Hypothesis(
        id=hyp_id,
        axis="immune_inflammatory",
        statement="Effector-memory T cells drive RAAS-resistant hypertension.",
        prediction="Depleting effector-memory T cells lowers BP in resistant hypertension.",
        rationale="r",
        priority="high",
    )


def _evidence_item(n: int) -> EvidenceItem:
    pmid = str(10_000_000 + n)
    return EvidenceItem(
        claim_fragment=f"synthetic claim fragment {n}",
        reference=Reference(source="PubMed", pmid=pmid, url=f"https://pubmed.ncbi.nlm.nih.gov/{pmid}/"),
        supports=True,
        entailment="unclear",
        grade="observational",
    )


@pytest.mark.parametrize("item_count", [1, 2, 4, config.ABSTRACT_CAP])
def test_entailment_is_one_batched_call_regardless_of_item_count(item_count: int) -> None:
    """BUILD_SPEC.md §6(1): entailment is "A *separate* Haiku call — not
    folded into the grader, and not per-item — that classifies ALL of a
    hypothesis's <=8 evidence items in ONE structured-JSON call". Proven
    directly for every item count from 1 up to the real
    ``config.ABSTRACT_CAP`` — the LLM is invoked exactly ONCE no matter how
    many items are in the batch."""
    items = [_evidence_item(i) for i in range(item_count)]
    fake_llm = _RecordingEntailmentLLM()

    asyncio.run(run_entailment(_hypothesis(), items, fake_llm))

    assert len(fake_llm.calls) == 1, (
        f"expected exactly 1 batched entailment call for {item_count} evidence items, "
        f"got {len(fake_llm.calls)} -- entailment must never be per-item"
    )


def test_entailment_calls_scale_with_hypothesis_count_not_abstract_count() -> None:
    """The call-count PROPERTY this whole file is about: simulating a full
    3-hypothesis run, each at the real ``ABSTRACT_CAP`` (8 items) —
    24 evidence items total — entailment makes exactly 3 calls (one per
    hypothesis), never 24 (what a per-item design would cost). This is
    the concrete, deterministic counter-example to "~24+ calls" the Phase 7
    brief itself calls out.
    """
    fake_llm = _RecordingEntailmentLLM()
    num_hypotheses = config.MAX_HYPOTHESES
    total_items = 0
    for i in range(num_hypotheses):
        items = [_evidence_item(i * 100 + j) for j in range(config.ABSTRACT_CAP)]
        total_items += len(items)
        asyncio.run(run_entailment(_hypothesis(f"h{i}"), items, fake_llm))

    assert total_items == num_hypotheses * config.ABSTRACT_CAP  # sanity on the setup itself
    assert len(fake_llm.calls) == num_hypotheses, (
        f"expected entailment call count ({len(fake_llm.calls)}) to equal the "
        f"hypothesis count ({num_hypotheses}), NOT the total abstract count "
        f"({total_items}) -- a per-item design would have made {total_items} calls here"
    )


def test_entailment_empty_batch_costs_zero_calls() -> None:
    """A hypothesis with zero evidence costs ZERO entailment calls (nothing
    to classify) — BUILD_SPEC.md §9's cost budget assumes exactly this
    (mirrors ``tests/test_rigor_entailment_correlation.py``'s own coverage;
    included here too since it's directly a *cost* property)."""
    fake_llm = _RecordingEntailmentLLM()
    asyncio.run(run_entailment(_hypothesis(), [], fake_llm))
    assert fake_llm.calls == []


# ---------------------------------------------------------------------------
# 2. Against the REAL captured run_manifest (shared flagship_brief fixture —
#    tests/conftest.py; zero additional live calls).
# ---------------------------------------------------------------------------


def test_flagship_run_respects_hypothesis_and_abstract_caps(flagship_brief: TransBrief) -> None:
    """KICKOFF.md Phase 7 / BUILD_SPEC.md §0.7/§3: <=3 hypotheses, <=
    ``ABSTRACT_CAP`` (~8) abstracts retrieved per hypothesis."""
    brief = flagship_brief

    assert 1 <= len(brief.hypotheses) <= config.MAX_HYPOTHESES, (
        f"expected 1-{config.MAX_HYPOTHESES} hypotheses, got {len(brief.hypotheses)}"
    )

    snapshot = brief.run_manifest.get("retrieval_snapshot") or {}
    assert snapshot, "run_manifest['retrieval_snapshot'] must be populated by a real run"
    for hyp_id, entry in snapshot.items():
        abstracts = entry.get("abstracts") or []
        assert len(abstracts) <= config.ABSTRACT_CAP, (
            f"hypothesis {hyp_id}: retrieved {len(abstracts)} abstracts, "
            f"exceeds the configured cap of {config.ABSTRACT_CAP}"
        )


def test_flagship_token_spend_scales_with_hypotheses_not_abstracts(flagship_brief: TransBrief) -> None:
    """BUILD_SPEC.md §9: "per run ~= 1 decompose + 1 hypothesize + 3 grade +
    3 entailment + 3 novelty + 1 design + 1 assemble ... ~= ~13 LLM calls"
    (this figure, and ``token_spend["calls"]``, EXCLUDE
    ``neutralize_query``'s own internal Anthropic calls — those don't route
    through ``agents._ainvoke_json``'s token-spend accumulator, only the
    engine's OWN 7 structured-JSON call sites do — see ``agents.py``'s
    ``_record_token_usage``/``token_spend_session``).

    Asserts the REAL captured call count is bounded by that formula (scales
    with hypothesis count N: ``2 + 3*N + 2``, i.e. <=13 for N=3) and stays
    well under the total number of abstracts actually considered — the
    direct evidence that entailment (and grading) are batched-per-hypothesis
    rather than per-abstract (a per-item design would cost roughly one call
    PER abstract, i.e. comparable to or exceeding the abstract count, not a
    small constant-ish multiple of the hypothesis count).
    """
    brief = flagship_brief
    token_spend = brief.run_manifest.get("token_spend") or {}
    calls = token_spend.get("calls")
    assert isinstance(calls, int) and calls > 0, f"expected a positive real call count, got {token_spend!r}"

    n = len(brief.hypotheses)
    upper_bound = 2 + 3 * n + 2  # decompose + hypothesize + N*(grade+entailment+novelty) + design + assemble
    assert calls <= upper_bound, (
        f"token_spend['calls']={calls} exceeds the documented per-run upper bound "
        f"(2 + 3*{n} + 2 = {upper_bound}) for {n} hypotheses -- possible per-item "
        f"(not batched) entailment/grading regression"
    )
    assert calls <= 13 + 2, f"token_spend['calls']={calls} is well outside the documented '~13 LLM calls/run' budget"

    total_abstracts = sum(len(e.get("abstracts") or []) for e in (brief.run_manifest.get("retrieval_snapshot") or {}).values())
    if total_abstracts >= 5:  # only meaningful once there's enough evidence for the contrast to be real
        assert calls < total_abstracts, (
            f"token_spend['calls']={calls} is not clearly less than total retrieved "
            f"abstracts={total_abstracts} -- expected the call count to scale with "
            f"hypothesis count (<= ~13 total), not abstract count (a per-item entailment "
            f"design would cost roughly one call per abstract, i.e. ~{total_abstracts}+)"
        )


def test_flagship_run_manifest_is_complete(flagship_brief: TransBrief) -> None:
    """BUILD_SPEC.md §9: "run_manifest records models, temps, neutralized
    queries, PMIDs, dataset pointer, timestamps, and token spend per run."
    Confirms every one of those is genuinely present on the real captured
    manifest (KICKOFF.md non-negotiable rule 8: real model ids only)."""
    rm = flagship_brief.run_manifest

    assert rm.get("model_reasoning") == "claude-sonnet-4-6"
    assert rm.get("model_cheap") == "claude-haiku-4-5-20251001"
    assert rm.get("temperature") == 0
    assert rm.get("max_hypotheses") == config.MAX_HYPOTHESES
    assert rm.get("abstract_cap") == config.ABSTRACT_CAP
    assert rm.get("run_started_at"), "run_manifest must record a run_started_at timestamp"
    assert rm.get("generated_at"), "run_manifest must record a generated_at timestamp"
    assert isinstance(rm.get("token_spend"), dict) and rm["token_spend"]

    snapshot = rm.get("retrieval_snapshot")
    assert isinstance(snapshot, dict) and snapshot, "run_manifest must carry the retrieval snapshot (neutral queries + PMIDs)"
    for entry in snapshot.values():
        assert "neutral_query" in entry and "pubmed_query" in entry and "abstracts" in entry

    assert "dataset_pointer" in rm  # may legitimately be the pinned Tabula Sapiens fallback -- key must exist either way
