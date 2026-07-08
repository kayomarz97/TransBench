"""Phase 3/4 flagship acceptance test (KICKOFF.md Phase 3/4 / BUILD_SPEC.md
§3, §5, §6).

REAL PubMed + Anthropic API call test — requires a working
``ANTHROPIC_API_KEY`` (skips cleanly, with a clear reason, if absent). Runs
the FLAGSHIP observation through the full ``run_transbench()`` pipeline
(agents 1-6: decompose, hypothesize, retrieve, grade, entailment/grounding,
novelty) — exactly ONE flagship pass, per the phase's spend-limiting
instruction.

Assertion design (re-shaped, Opus review): TransBench is a NOVEL-hypothesis
tool — BUILD_SPEC.md §5 explicitly asks the hypothesis generator to "prefer
genuinely open questions over textbook facts," and §6(2)/§6(3) explicitly
design the rigor gate to DEMOTE (never crash on) a hypothesis that can't
ground, because a freshly-generated, genuinely novel gene/pathway
combination legitimately may not have indexed PubMed literature yet — that
is the tool working as intended, not a defect. Requiring EVERY fresh
hypothesis from a non-deterministic real LLM call to always find real
literature was the wrong bar (it conflated "the retrieval/grading code is
correct" with "PubMed happens to index this exact novel combination this
run," which the deterministic, offline ``test_pubmed_query_builder.py``
already covers without that non-determinism). The bar this file actually
needs to prove — that the live system doesn't crash, produces only
real/resolvable/on-topic citations when it does cite something, and
successfully grounds end to end at least once — is:

1. The flagship runs through the FULL graded pipeline without exception.
2. For EACH ``GradedHypothesis``: if ``evidence`` is non-empty, every item
   carries a real digit-string PMID + a resolvable
   ``https://pubmed.ncbi.nlm.nih.gov/`` URL + a valid grade (this can NEVER
   silently regress to off-topic/fabricated citations — guarded further by
   the ``bears_on_hypothesis`` gate, agent 4, and directly here); if
   ``evidence`` IS empty, ``grounded`` must be ``False`` (graceful demotion,
   never a crash, never a phantom citation).
3. At least ONE hypothesis grounds (non-empty evidence AND ``grounded is
   True``) — proves the retrieval -> grading -> entailment -> grounding
   chain genuinely works end to end on live data, not just in isolated
   offline unit tests.

This is robust to legitimate PubMed sparsity/transients for any ONE
hypothesis, but it CANNOT mask an actual query-construction regression
(that's ``test_pubmed_query_builder.py``'s job — deterministic, offline, no
LLM/PubMed non-determinism) or off-topic evidence being accepted as
"grounded" (asserted directly here, and gated upstream by agent 4's
``bears_on_hypothesis`` signal). True cross-run reproducibility of a
specific brief is a Phase-5/7 concern (BUILD_SPEC.md §9): pass
``TransRequest.retrieval_snapshot`` to replay a run's exact retrieved
PMIDs+abstracts instead of hitting PubMed again — this live flagship test is
deliberately NOT that; it is proving the live path works, which by
definition cannot be made deterministic without disabling the thing it's
testing.
"""
from __future__ import annotations

import asyncio
import os
import re

import pytest

from tests.fixtures import FLAGSHIP_OBSERVATION
from transbench.engine import run_transbench
from transbench.schemas import EvidenceGrade

pytestmark = pytest.mark.skipif(
    not os.environ.get("ANTHROPIC_API_KEY"),
    reason="Phase 3/4 acceptance test makes REAL Anthropic + PubMed API calls; requires a working ANTHROPIC_API_KEY.",
)

_PMID_RE = re.compile(r"^\d+$")
_VALID_GRADES = set(EvidenceGrade.__args__)  # type: ignore[attr-defined]


def test_flagship_grounds_end_to_end_without_requiring_every_hypothesis_to() -> None:
    """Real end-to-end call: flagship -> decompose -> hypothesize -> (per
    hypothesis) retrieve -> grade -> entailment/grounding -> novelty. See
    the module docstring for why the per-hypothesis bar is "no crash + only
    real citations when any are made" rather than "every fresh hypothesis
    must find literature"."""
    brief = asyncio.run(run_transbench(FLAGSHIP_OBSERVATION))  # must not raise

    assert len(brief.hypotheses) >= 1

    any_grounded = False
    for gh in brief.hypotheses:
        h = gh.hypothesis

        if gh.evidence:
            for item in gh.evidence:
                pmid = item.reference.pmid or ""
                assert _PMID_RE.match(pmid), f"hypothesis {h.id}: non-digit pmid {pmid!r}"
                assert item.reference.url and item.reference.url.startswith(
                    "https://pubmed.ncbi.nlm.nih.gov/"
                ), f"hypothesis {h.id}: unresolvable url {item.reference.url!r}"
                assert item.reference.source
                assert item.claim_fragment.strip()
                assert item.grade in _VALID_GRADES
        else:
            # No evidence -> MUST be honestly demoted, never phantom-grounded.
            assert gh.grounded is False, (
                f"hypothesis {h.id} has zero evidence but grounded=True — "
                f"impossible per BUILD_SPEC.md §6(2), a real bug if it ever happens"
            )

        if gh.evidence and gh.grounded:
            any_grounded = True

        # supporting/contradicting counts must be internally consistent with
        # the evidence list's REAL entailment field (Phase 4: graph.py
        # computes these from entailment, the authoritative signal, not the
        # grader's coarser `supports` boolean -- "unclear" counts as neither).
        assert gh.supporting_count == sum(1 for e in gh.evidence if e.entailment == "supports")
        assert gh.contradicting_count == sum(1 for e in gh.evidence if e.entailment == "refutes")

    assert any_grounded, (
        "expected at least one hypothesis to ground end-to-end for the "
        "flagship's well-studied clinical area -- zero grounding across ALL "
        "hypotheses would indicate a genuine retrieval/grading regression, "
        "not legitimate per-hypothesis sparsity"
    )

    # Printed (not asserted) so a human can eyeball real grounding + do
    # grader-calibration review (retrieved-count vs graded-count per
    # hypothesis) — visible with `pytest -q -s`.
    print("\n--- Phase 3/4 flagship output ---")
    retrieval_snapshot = brief.run_manifest.get("retrieval_snapshot", {})
    for gh in brief.hypotheses:
        h = gh.hypothesis
        snap = retrieval_snapshot.get(h.id, {})
        retrieved_abstracts = snap.get("abstracts", [])
        print(f"\nHypothesis {h.id} [{h.axis}]: {h.statement[:120]}")
        print(f"  neutralized query: {snap.get('neutral_query')}")
        print(f"  pubmed query (entity-based): {snap.get('pubmed_query')}")
        print(
            f"  retrieved (pre-grading) abstracts: {len(retrieved_abstracts)}  "
            f"-> graded evidence: {len(gh.evidence)}"
        )
        for ab in retrieved_abstracts:
            print(f"    retrieved: pmid={ab.get('pmid')} title={str(ab.get('title'))[:90]}")
        print(f"  supporting={gh.supporting_count} contradicting={gh.contradicting_count}")
        print(f"  novelty={gh.novelty} grounded={gh.grounded} confidence={gh.confidence}")
        print(f"  novelty_reason: {gh.novelty_reason}")
        for item in gh.evidence:
            print(
                f"  - GRADED pmid={item.reference.pmid} supports={item.supports} "
                f"entailment={item.entailment} grade={item.grade} url={item.reference.url}"
            )
            print(f"    claim: {item.claim_fragment[:150]}")
    print(f"\nselect_experiment_candidate picked: {brief.top_experiment.hypothesis_id}")
