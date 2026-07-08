"""Phase 3 acceptance test (KICKOFF.md Phase 3 / BUILD_SPEC.md §3, §5).

REAL PubMed + Anthropic API call test — requires a working
``ANTHROPIC_API_KEY`` (skips cleanly, with a clear reason, if absent). Runs
the FLAGSHIP observation through the full ``run_transbench()`` pipeline
(agents 1-4 now real: decompose, hypothesize, retrieve, grade) — exactly ONE
flagship pass, per the phase's spend-limiting instruction. Asserts each
hypothesis's evidence items carry a real digit-string PMID with a resolvable
``https://pubmed.ncbi.nlm.nih.gov/`` URL. Handles the genuinely-empty case
gracefully (no crash) but the flagship's well-studied hypotheses should
return evidence for at least one hypothesis overall.
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

    any_evidence = False
    for gh in brief.hypotheses:
        for item in gh.evidence:
            any_evidence = True
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

    # The flagship targets a well-studied area (RAAS-resistant hypertension /
    # T-cell-IL-17 / vascular inflammation) -- real PubMed should return
    # SOMETHING for at least one hypothesis. A total-zero result across every
    # hypothesis would indicate a wiring bug, not a legitimately empty area.
    assert any_evidence, "expected >=1 PMID-backed EvidenceItem across the flagship's hypotheses"

    # Printed (not asserted) so a human can eyeball real grounding, per the
    # coordinator's request — visible with `pytest -q -s`.
    print("\n--- Phase 3 flagship retrieval+grading output ---")
    retrieval_snapshot = brief.run_manifest.get("retrieval_snapshot", {})
    for gh in brief.hypotheses:
        h = gh.hypothesis
        snap = retrieval_snapshot.get(h.id, {})
        print(f"\nHypothesis {h.id} [{h.axis}]: {h.statement[:120]}")
        print(f"  neutralized query: {snap.get('neutral_query')}")
        print(f"  pubmed query (shortened): {snap.get('pubmed_query')}")
        print(f"  supporting={gh.supporting_count} contradicting={gh.contradicting_count}")
        for item in gh.evidence:
            print(
                f"  - pmid={item.reference.pmid} supports={item.supports} "
                f"grade={item.grade} url={item.reference.url}"
            )
            print(f"    claim: {item.claim_fragment[:150]}")
