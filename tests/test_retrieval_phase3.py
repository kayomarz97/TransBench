"""Phase 3 acceptance test (KICKOFF.md Phase 3 / BUILD_SPEC.md §3, §5).

REAL PubMed + Anthropic API call test — requires a working
``ANTHROPIC_API_KEY`` (skips cleanly, with a clear reason, if absent). Runs
the FLAGSHIP observation through the full ``run_transbench()`` pipeline
(agents 1-4 now real: decompose, hypothesize, retrieve, grade) — exactly ONE
flagship pass, per the phase's spend-limiting instruction. Asserts EVERY
hypothesis returns >=1 real, PMID-backed ``EvidenceItem`` whose
``reference.pmid`` is a digit-string with a resolvable
``https://pubmed.ncbi.nlm.nih.gov/`` URL — the literal BUILD_SPEC.md §3
acceptance bar (Opus review, DEFECT 2 fix: an earlier version of this test
only asserted >=1 evidence item ACROSS all hypotheses, which could hide a
per-hypothesis retrieval-quality regression; restored to the per-hypothesis
bar the spec actually requires).
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
    reason="Phase 3 acceptance test makes REAL Anthropic + PubMed API calls; requires a working ANTHROPIC_API_KEY.",
)

_PMID_RE = re.compile(r"^\d+$")
_VALID_GRADES = set(EvidenceGrade.__args__)  # type: ignore[attr-defined]


def test_flagship_retrieve_and_grade_real_calls() -> None:
    """Real end-to-end call: flagship -> decompose -> hypothesize -> (per
    hypothesis) retrieve -> grade."""
    brief = asyncio.run(run_transbench(FLAGSHIP_OBSERVATION))

    assert len(brief.hypotheses) >= 1

    for gh in brief.hypotheses:
        h = gh.hypothesis
        # The literal BUILD_SPEC.md §3 acceptance bar: EACH hypothesis
        # returns >=1 real PMID-backed EvidenceItem for the flagship's
        # well-studied area. Do NOT weaken this to an any-hypothesis check —
        # if a specific hypothesis still can't ground after the entity-query
        # fix, that must surface here as a failure to investigate, not be
        # silently averaged away across hypotheses.
        assert len(gh.evidence) >= 1, (
            f"hypothesis {h.id} [{h.axis}] '{h.statement[:80]}...' returned "
            f"ZERO grounded evidence"
        )
        for item in gh.evidence:
            pmid = item.reference.pmid or ""
            assert _PMID_RE.match(pmid), f"non-digit pmid: {pmid!r}"
            assert item.reference.url and item.reference.url.startswith(
                "https://pubmed.ncbi.nlm.nih.gov/"
            ), f"unresolvable url: {item.reference.url!r}"
            assert item.reference.source
            assert item.claim_fragment.strip()
            assert item.grade in _VALID_GRADES
            # Phase 3 provisional placeholder (real batched entailment is Phase 4/rigor.py)
            assert item.entailment == "unclear"

        # supporting/contradicting counts must be internally consistent with the evidence list
        assert gh.supporting_count == sum(1 for e in gh.evidence if e.supports)
        assert gh.contradicting_count == sum(1 for e in gh.evidence if not e.supports)
        # Phase 4/5 fields must still be honest placeholders (not built this phase)
        assert gh.grounded is False

    # Printed (not asserted) so a human can eyeball real grounding, per the
    # coordinator's request — visible with `pytest -q -s`.
    print("\n--- Phase 3 flagship retrieval+grading output ---")
    retrieval_snapshot = brief.run_manifest.get("retrieval_snapshot", {})
    for gh in brief.hypotheses:
        h = gh.hypothesis
        snap = retrieval_snapshot.get(h.id, {})
        print(f"\nHypothesis {h.id} [{h.axis}]: {h.statement[:120]}")
        print(f"  neutralized query: {snap.get('neutral_query')}")
        print(f"  pubmed query (entity-based): {snap.get('pubmed_query')}")
        print(f"  supporting={gh.supporting_count} contradicting={gh.contradicting_count}")
        for item in gh.evidence:
            print(
                f"  - pmid={item.reference.pmid} supports={item.supports} "
                f"grade={item.grade} url={item.reference.url}"
            )
            print(f"    claim: {item.claim_fragment[:150]}")
