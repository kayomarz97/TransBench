"""Phase 4 acceptance test (KICKOFF.md Phase 4 / BUILD_SPEC.md §6(2)).

Fully OFFLINE — zero API cost, zero network. Proves the grounding gate is
fed the EXACT pseudo-response shape ``grounding_stats``/``strip_ungrounded``
expect (a malformed dict silently no-ops — BUILD_SPEC.md §6(2)'s own
warning), and that the novelty guard correctly excludes an ungrounded
hypothesis from experiment-candidate selection.
"""
from __future__ import annotations

from transbench.reuse import grounding_stats, strip_ungrounded
from transbench.rigor import compute_grounding, select_experiment_candidate
from transbench.schemas import EvidenceItem, GradedHypothesis, Hypothesis, Reference


def test_grounding_stats_exact_shape_pmid_item_survives_generic_source_stripped() -> None:
    """A pmid+url-bearing item is grounded; a source="Expert opinion" item is
    NOT (Iatronix's own GENERIC_OR_UNGROUNDED list, confirmed by reading
    grounding_gate.py) — proves the exact
    {"sections":[{"content_items":[...]}]} shape is respected end to end,
    not silently no-op'd the way a malformed dict would be (BUILD_SPEC.md
    §6(2): "If the key sections is missing or items lack all of those,
    grounding_stats returns (0, ...) and the hypothesis is wrongly
    demoted")."""
    pseudo = {
        "sections": [
            {
                "content_items": [
                    {
                        "pmid": "12345678",
                        "url": "https://pubmed.ncbi.nlm.nih.gov/12345678/",
                        "source": "PubMed",
                    },
                    {"pmid": None, "url": None, "source": "Expert opinion"},
                ]
            }
        ]
    }

    grounded, total = grounding_stats(pseudo)
    assert (grounded, total) == (1, 2)

    removed = strip_ungrounded(pseudo)
    assert removed == 1
    assert len(pseudo["sections"][0]["content_items"]) == 1
    assert pseudo["sections"][0]["content_items"][0]["pmid"] == "12345678"


def test_grounding_stats_malformed_shape_silently_no_ops() -> None:
    """Documents the EXACT failure mode BUILD_SPEC.md §6(2) warns about: a
    dict missing the "sections" key entirely (or with a wrong key) is NOT an
    error — grounding_stats just silently returns (0, 0). This is precisely
    why rigor.compute_grounding must build the shape precisely (proven
    above), not something merely close."""
    assert grounding_stats({}) == (0, 0)
    assert grounding_stats({"wrong_key": []}) == (0, 0)
    assert grounding_stats({"sections": "not a list"}) == (0, 0)


def _evidence(pmid: str, entailment: str, grade: str = "rct") -> EvidenceItem:
    return EvidenceItem(
        claim_fragment="claim text",
        reference=Reference(
            source="PubMed", pmid=pmid, url=f"https://pubmed.ncbi.nlm.nih.gov/{pmid}/"
        ),
        supports=(entailment == "supports"),
        entailment=entailment,
        grade=grade,
    )


def test_compute_grounding_true_when_a_supporting_item_has_a_resolvable_pmid() -> None:
    grounded, count = compute_grounding([_evidence("11111111", "supports")])
    assert grounded is True
    assert count == 1


def test_compute_grounding_false_with_no_supporting_evidence() -> None:
    """BUILD_SPEC.md §6(2): "A hypothesis with 0 grounded supporting items ->
    grounded=False -> excluded from the experiment stage". refutes/unclear
    items never count, however well-cited — only entailment=="supports"."""
    items = [
        _evidence("22222222", "refutes"),
        _evidence("33333333", "unclear"),
    ]
    grounded, count = compute_grounding(items)
    assert grounded is False
    assert count == 0


def test_compute_grounding_false_with_zero_evidence() -> None:
    grounded, count = compute_grounding([])
    assert grounded is False
    assert count == 0


def _hypothesis(hyp_id: str, statement: str = "s") -> Hypothesis:
    return Hypothesis(
        id=hyp_id, axis="raas", statement=statement, prediction="p", rationale="r", priority="high"
    )


def test_select_experiment_candidate_excludes_ungrounded_hypothesis() -> None:
    """A hypothesis with no grounded supporting evidence -> grounded=False ->
    NOT returned by select_experiment_candidate (demoted/Unverified, never
    deleted from the caller's own hypothesis list -- this function only
    decides experiment-stage eligibility)."""
    graded = GradedHypothesis(
        hypothesis=_hypothesis("h-ungrounded"),
        evidence=[_evidence("44444444", "unclear")],
        supporting_count=0,
        contradicting_count=0,
        novelty="open_question",
        novelty_reason="Partially relevant per PMID 44444444 but entailment is unclear.",
        confidence="low",
        grounded=False,  # <-- the key condition under test
    )
    assert select_experiment_candidate([graded]) is None
    # The hypothesis itself must still be intact for the caller -- demoted, never deleted.
    assert graded.hypothesis.id == "h-ungrounded"


def test_select_experiment_candidate_excludes_established_even_if_grounded() -> None:
    """The novelty guard: 'established' (textbook fact) hypotheses are never
    selected, however well-grounded (BUILD_SPEC.md §6(3): "kills textbook-
    as-novel")."""
    graded = GradedHypothesis(
        hypothesis=_hypothesis("h-established", "ACE inhibitors cause dry cough via bradykinin accumulation"),
        evidence=[_evidence("55555555", "supports")],
        supporting_count=1,
        contradicting_count=0,
        novelty="established",  # <-- the key condition under test
        novelty_reason="Well-documented mechanism per PMID 55555555.",
        confidence="high",
        grounded=True,
    )
    assert select_experiment_candidate([graded]) is None


def test_select_experiment_candidate_picks_open_question_grounded_hypothesis() -> None:
    graded = GradedHypothesis(
        hypothesis=_hypothesis("h-eligible"),
        evidence=[_evidence("66666666", "supports")],
        supporting_count=1,
        contradicting_count=0,
        novelty="open_question",
        novelty_reason="Partially supported by PMID 66666666; mechanism remains unresolved.",
        confidence="moderate",
        grounded=True,
    )
    selected = select_experiment_candidate([graded])
    assert selected is not None
    assert selected.hypothesis.id == "h-eligible"


def test_select_experiment_candidate_prefers_higher_confidence_among_eligible() -> None:
    low = GradedHypothesis(
        hypothesis=_hypothesis("h-low"),
        evidence=[_evidence("77777777", "supports")],
        supporting_count=1,
        contradicting_count=0,
        novelty="open_question",
        novelty_reason="Weakly supported by PMID 77777777.",
        confidence="low",
        grounded=True,
    )
    high = GradedHypothesis(
        hypothesis=_hypothesis("h-high"),
        evidence=[_evidence("88888888", "supports"), _evidence("99999999", "supports")],
        supporting_count=2,
        contradicting_count=0,
        novelty="open_question",
        novelty_reason="Strongly supported by PMID 88888888 and PMID 99999999.",
        confidence="high",
        grounded=True,
    )
    selected = select_experiment_candidate([low, high])
    assert selected is not None
    assert selected.hypothesis.id == "h-high"


def test_select_experiment_candidate_returns_none_when_nothing_eligible() -> None:
    assert select_experiment_candidate([]) is None
