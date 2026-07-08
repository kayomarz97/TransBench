"""Phase 4 acceptance test (KICKOFF.md Phase 4 / BUILD_SPEC.md §6(3)).

REAL Anthropic + PubMed API call test — requires a working
``ANTHROPIC_API_KEY`` (skips cleanly, with a clear reason, if absent). The
novelty guard's canonical case: "ACE inhibitors cause dry cough" is a
well-established, textbook pharmacological fact (bradykinin/substance P
accumulation from reduced ACE-mediated degradation) — the model MUST grade
it ``established``, and ``select_experiment_candidate`` MUST NOT select it
(BUILD_SPEC.md §6(3): "kills textbook-as-novel").

The hypothesis is constructed DIRECTLY (bypassing agents 1-2, decompose/
hypothesize) rather than generated through the full flagship pipeline — this
test is specifically about the novelty guard (agents 5-6), not hypothesis
generation, so it deliberately does not spend calls on decompose/hypothesize;
it still exercises the REAL retrieve (agent 3) -> grade (agent 4) ->
entailment (agent 6) -> novelty (agent 5) pipeline end to end.
"""
from __future__ import annotations

import asyncio
import os

import pytest

from transbench import agents, config, rigor
from transbench.schemas import GradedHypothesis, Hypothesis

pytestmark = pytest.mark.skipif(
    not os.environ.get("ANTHROPIC_API_KEY"),
    reason="Phase 4 acceptance test makes REAL Anthropic + PubMed API calls; requires a working ANTHROPIC_API_KEY.",
)

_ACEI_COUGH_OBSERVATION = "Patient on an ACE inhibitor develops a new dry, persistent cough."

_ACEI_COUGH_HYPOTHESIS = Hypothesis(
    id="test-acei-cough",
    axis="drug_pk_metabolism",
    statement=(
        "ACE inhibitors cause a dry cough because inhibiting angiotensin-"
        "converting enzyme (which also degrades bradykinin and substance P) "
        "leads to accumulation of these tussive peptides in the airway."
    ),
    prediction=(
        "Discontinuing the ACE inhibitor (or switching to an ARB, which does "
        "not affect bradykinin metabolism) resolves the cough within days to "
        "a few weeks."
    ),
    rationale="Classic, well-documented ACE-inhibitor adverse-effect mechanism.",
    priority="low",
)


def test_established_aceinhibitor_cough_is_graded_established_and_not_selected() -> None:
    """Real end-to-end: retrieve -> grade -> entailment -> novelty for the
    canonical established-fact hypothesis; asserts novelty == "established"
    and that select_experiment_candidate excludes it."""
    user_key = os.environ["ANTHROPIC_API_KEY"]

    async def _run() -> GradedHypothesis:
        retrieval = await agents.run_retrieve(
            _ACEI_COUGH_HYPOTHESIS, user_key, observation=_ACEI_COUGH_OBSERVATION
        )
        evidence = await agents.run_grade(
            _ACEI_COUGH_HYPOTHESIS, retrieval.ranked, retrieval.registry, retrieval.fd, user_key
        )

        entailment_llm = agents.build_llm(config.MODEL_CHEAP, user_key)
        evidence = await rigor.run_entailment(_ACEI_COUGH_HYPOTHESIS, evidence, entailment_llm)

        grounded, grounded_supporting_count = rigor.compute_grounding(evidence)

        novelty_llm = agents.build_llm(config.MODEL_REASONING, user_key)
        novelty, novelty_reason = await rigor.run_novelty(_ACEI_COUGH_HYPOTHESIS, evidence, novelty_llm)

        supporting_count = sum(1 for e in evidence if e.entailment == "supports")
        contradicting_count = sum(1 for e in evidence if e.entailment == "refutes")
        confidence = rigor.compute_confidence(grounded_supporting_count, contradicting_count, evidence)

        return GradedHypothesis(
            hypothesis=_ACEI_COUGH_HYPOTHESIS,
            evidence=evidence,
            supporting_count=supporting_count,
            contradicting_count=contradicting_count,
            novelty=novelty,
            novelty_reason=novelty_reason,
            confidence=confidence,
            grounded=grounded,
        )

    graded = asyncio.run(_run())

    print("\n--- Phase 4 novelty-guard canonical case (ACEi cough) ---")
    print(f"pubmed query used: (see retrieval; evidence count={len(graded.evidence)})")
    print(f"evidence: {[(e.reference.pmid, e.entailment, e.supports) for e in graded.evidence]}")
    print(f"novelty: {graded.novelty}")
    print(f"novelty_reason: {graded.novelty_reason}")
    print(f"grounded: {graded.grounded}  confidence: {graded.confidence}")

    assert graded.novelty == "established", (
        f"expected the textbook ACEi-cough mechanism to be graded 'established', "
        f"got {graded.novelty!r} (reason: {graded.novelty_reason!r})"
    )
    # BUILD_SPEC.md §6(3): novelty_reason must cite a specific PMID (enforced
    # structurally by rigor._ensure_pmid_citation, not merely requested).
    real_pmids = [e.reference.pmid for e in graded.evidence if e.reference.pmid]
    if real_pmids:
        assert any(pmid in graded.novelty_reason for pmid in real_pmids), (
            f"novelty_reason must cite a real PMID: {graded.novelty_reason!r}"
        )

    selected = rigor.select_experiment_candidate([graded])
    assert selected is None, (
        "the novelty guard must exclude an 'established' hypothesis from "
        "experiment-candidate selection, however well-grounded"
    )
