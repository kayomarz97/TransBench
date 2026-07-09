"""tests/test_universal_domains.py — the DECISIVE cross-domain proof (Phase 8,
domain-universalization; KICKOFF.md / BUILD_SPEC.md §0's tool scope widened
from "an observation about antihypertensive drugs" to ANY clinical/
biomedical observation).

REAL Anthropic + PubMed API call test — requires a working
``ANTHROPIC_API_KEY`` (skips cleanly, with a clear reason, if absent).

This is the test that actually proves the fix for the PROVEN live regression
(see ``prompts.py``'s and ``agents.py``'s module docstrings for the full
narrative): before this task, the PubMed disease anchor was hardcoded to
default to "hypertension" for EVERY run regardless of the observation's own
condition — a rheumatoid-arthritis observation's queries were built as e.g.
"hypertension Th17 Methotrexate" / "hypertension MTX Inadequate", grounding
ZERO real evidence, because the anchor bore no relationship at all to the
actual disease being asked about.

**Autoimmune (rheumatoid arthritis)** — ``tests/fixtures.AUTOIMMUNE_OBSERVATION``,
the EXACT scenario that reproduced the regression — gets the full decisive
treatment: schema validity, every hypothesis's real PubMed query anchors on
"rheumatoid"/"arthritis" and NEVER "hypertension", AT LEAST ONE hypothesis
GROUNDS with real on-topic evidence (the direct fix for the proven "0
grounding" defect), and a runnable experiment is produced.

**Oncology (melanoma), infectious disease (recurrent *C. difficile*), and
metabolic (T2D)** — the 3 remaining standalone cross-domain fixtures — get
the same schema-validity + anchor-correctness treatment (never anchors on
"hypertension"; anchors on THEIR OWN condition) and a runnable experiment,
proving the fix generalizes beyond the one proven regression case. Grounding
is reported (printed) for these three but not hard-required — PubMed
coverage for a freshly-generated, genuinely novel hypothesis legitimately
varies by literature volume across domains (the SAME grounding gate that
already honestly demotes a sparse hypothesis in the flagship/hypertension
domain applies identically here; a domain having thinner coverage than
hypertension is not itself a defect in the anchor fix this file proves).

Each domain's brief is captured via its own module-scoped fixture (mirrors
``tests/conftest.py``'s ``flagship_brief`` pattern, but scoped to just this
file) — exactly ONE fresh ``run_transbench`` call per domain (4 total for
this file), each retried once on a transient Anthropic 500 (``tests.
conftest.run_transbench_live_with_retry``).
"""
from __future__ import annotations

import os
import re

import pytest

from tests.conftest import run_transbench_live_with_retry
from tests.fixtures import (
    AUTOIMMUNE_OBSERVATION,
    INFECTIOUS_OBSERVATION,
    METABOLIC_OBSERVATION,
    ONCOLOGY_OBSERVATION,
)
from transbench.schemas import TransBrief

pytestmark = pytest.mark.skipif(
    not os.environ.get("ANTHROPIC_API_KEY"),
    reason="test_universal_domains's cross-domain proof makes REAL Anthropic + PubMed API calls; "
    "requires a working ANTHROPIC_API_KEY.",
)

_HYPERTENSION_RE = re.compile(r"hypertension", re.IGNORECASE)
_PMID_RE = re.compile(r"^\d+$")
_RESOLVABLE_URL_PREFIXES = (
    "https://pubmed.ncbi.nlm.nih.gov/",
    "https://clinicaltrials.gov/",
    "https://doi.org/",
)


def _print_domain_summary(label: str, observation: str, brief: TransBrief) -> None:
    """Best-effort, human-readable per-domain report (visible with `pytest
    -s`) — never raises; the real assertions live in each test function, not
    here."""
    try:
        print("\n" + "=" * 78)
        print(f"CROSS-DOMAIN LIVE PROOF: {label}")
        print("=" * 78)
        print(f"observation: {observation}")
        print(f"axes: {[(a.axis, a.rationale) for a in brief.axes]}")
        print(f"condition_anchor (run_manifest): {brief.run_manifest.get('condition_anchor')!r}")
        snapshot = brief.run_manifest.get("retrieval_snapshot") or {}
        for gh in brief.hypotheses:
            h = gh.hypothesis
            snap = snapshot.get(h.id, {})
            print(f"\nhypothesis {h.id} [{h.axis}]: {h.statement}")
            print(f"  pubmed_query: {snap.get('pubmed_query')!r}")
            print(f"  novelty={gh.novelty} grounded={gh.grounded} confidence={gh.confidence}")
            for ev in gh.evidence:
                print(f"    evidence: pmid={ev.reference.pmid} entailment={ev.entailment} url={ev.reference.url}")
                print(f"      claim: {ev.claim_fragment[:150]}")
        print(f"\ntop_experiment: hypothesis_id={brief.top_experiment.hypothesis_id} "
              f"dataset={brief.top_experiment.dataset!r} dataset_pointer={brief.top_experiment.dataset_pointer!r}")
        print("=" * 78 + "\n")
    except Exception:  # noqa: BLE001 -- diagnostics only, must never break a test
        pass


def _assert_schema_valid_and_anchor(
    brief: TransBrief, observation: str, condition_terms_re: re.Pattern
) -> None:
    """Shared assertions for every cross-domain fixture: schema-valid,
    >=2 axes, every hypothesis's real PubMed query anchors on THIS domain's
    own condition and NEVER on "hypertension", and a runnable experiment was
    still produced."""
    revalidated = TransBrief.model_validate(brief.model_dump())
    assert revalidated == brief
    assert brief.request_echo == observation
    assert len(brief.axes) >= 2, f"expected >=2 axes, got {[a.axis for a in brief.axes]}"
    assert 1 <= len(brief.hypotheses) <= 3

    snapshot = brief.run_manifest.get("retrieval_snapshot") or {}
    assert snapshot, "run_manifest['retrieval_snapshot'] must be populated by a real run"
    for hyp_id, entry in snapshot.items():
        pubmed_query = entry.get("pubmed_query") or ""
        assert condition_terms_re.search(pubmed_query), (
            f"hypothesis {hyp_id}: pubmed_query {pubmed_query!r} does not anchor on this "
            f"observation's own condition at all"
        )
        assert not _HYPERTENSION_RE.search(pubmed_query), (
            f"hypothesis {hyp_id}: pubmed_query {pubmed_query!r} anchors on 'hypertension' -- "
            f"the exact proven regression this fix addresses (this observation is not about "
            f"hypertension)"
        )

    condition_anchor = brief.run_manifest.get("condition_anchor") or ""
    assert condition_terms_re.search(condition_anchor), (
        f"run_manifest['condition_anchor'] = {condition_anchor!r} does not name this "
        f"observation's own condition"
    )
    assert not _HYPERTENSION_RE.search(condition_anchor)

    # Every evidence item shown, if any, must be real/resolvable (never a
    # crash, never a fabricated/off-topic citation) -- mirrors
    # test_retrieval_phase3.py's own "no crash + only real citations when
    # any are made" bar. Grounding itself is reported, not hard-required,
    # for these 3 (see module docstring) -- only autoimmune (the proven
    # regression case) hard-requires >=1 grounded hypothesis, below.
    for gh in brief.hypotheses:
        for item in gh.evidence:
            if item.reference.pmid is not None:
                assert _PMID_RE.match(item.reference.pmid), (
                    f"hypothesis {gh.hypothesis.id}: non-digit pmid {item.reference.pmid!r}"
                )
            assert item.reference.url and item.reference.url.startswith(_RESOLVABLE_URL_PREFIXES), (
                f"hypothesis {gh.hypothesis.id}: unresolvable url {item.reference.url!r}"
            )

    assert brief.top_experiment.dataset_pointer, "top_experiment.dataset_pointer must be present"
    assert str(brief.top_experiment.dataset_pointer).startswith("https://")
    assert brief.top_experiment.claude_science_prompt.strip()


# ---------------------------------------------------------------------------
# Autoimmune (rheumatoid arthritis) — THE proven regression case. Full
# decisive treatment, including a HARD grounding requirement.
# ---------------------------------------------------------------------------

_RA_TERMS_RE = re.compile(r"rheumatoid|arthritis", re.IGNORECASE)


@pytest.fixture(scope="module")
def autoimmune_brief() -> TransBrief:
    """Exactly ONE real, live, full 8-agent-pipeline
    ``run_transbench(AUTOIMMUNE_OBSERVATION)`` call for this file (retried
    once on a transient Anthropic 500 — ``tests.conftest``'s shared helper).
    Module-scoped so every autoimmune-specific test function below shares
    this single call."""
    brief = run_transbench_live_with_retry(AUTOIMMUNE_OBSERVATION, focus_drug="methotrexate")
    _print_domain_summary("autoimmune (rheumatoid arthritis)", AUTOIMMUNE_OBSERVATION, brief)
    return brief


def test_autoimmune_brief_is_schema_valid_and_anchors_on_ra_not_hypertension(
    autoimmune_brief: TransBrief,
) -> None:
    """THE decisive proof: every hypothesis's REAL PubMed query (not just the
    axes/hypothesis text, which an LLM could get right independent of the
    retrieval bug) must anchor on rheumatoid arthritis, and must NEVER
    contain "hypertension" — the literal, previously-hardcoded wrong anchor
    that grounded zero evidence for this exact scenario before this fix."""
    _assert_schema_valid_and_anchor(autoimmune_brief, AUTOIMMUNE_OBSERVATION, _RA_TERMS_RE)


def test_autoimmune_grounds_at_least_one_hypothesis_with_real_ontopic_evidence(
    autoimmune_brief: TransBrief,
) -> None:
    """The direct fix for the proven "0 grounding" defect: with the anchor
    now correctly resolving to rheumatoid arthritis instead of hypertension,
    retrieval must actually find real, on-topic, resolvable RA literature —
    proving the anchor fix has real, not just cosmetic, effect. (This hard
    requirement is specific to THIS proven regression case -- see module
    docstring for why the other 3 domains only report, not require,
    grounding.)"""
    any_grounded = any(gh.evidence and gh.grounded for gh in autoimmune_brief.hypotheses)
    assert any_grounded, (
        "expected at least one hypothesis to ground end-to-end for the rheumatoid-arthritis "
        "observation now that the anchor correctly resolves to its own condition -- zero "
        "grounding here would indicate the anchor fix did not actually restore real retrieval"
    )


# ---------------------------------------------------------------------------
# Oncology (melanoma / checkpoint-inhibitor resistance).
# ---------------------------------------------------------------------------

_ONCOLOGY_TERMS_RE = re.compile(r"melanoma", re.IGNORECASE)


@pytest.fixture(scope="module")
def oncology_brief() -> TransBrief:
    brief = run_transbench_live_with_retry(ONCOLOGY_OBSERVATION, focus_drug="pembrolizumab")
    _print_domain_summary("oncology (melanoma)", ONCOLOGY_OBSERVATION, brief)
    return brief


def test_oncology_brief_is_schema_valid_and_anchors_on_melanoma_not_hypertension(
    oncology_brief: TransBrief,
) -> None:
    _assert_schema_valid_and_anchor(oncology_brief, ONCOLOGY_OBSERVATION, _ONCOLOGY_TERMS_RE)


# ---------------------------------------------------------------------------
# Infectious disease (recurrent Clostridioides difficile).
# ---------------------------------------------------------------------------

_INFECTIOUS_TERMS_RE = re.compile(r"difficile|clostridioides|clostridium", re.IGNORECASE)


@pytest.fixture(scope="module")
def infectious_brief() -> TransBrief:
    brief = run_transbench_live_with_retry(INFECTIOUS_OBSERVATION, focus_drug="vancomycin")
    _print_domain_summary("infectious disease (recurrent C. difficile)", INFECTIOUS_OBSERVATION, brief)
    return brief


def test_infectious_brief_is_schema_valid_and_anchors_on_c_diff_not_hypertension(
    infectious_brief: TransBrief,
) -> None:
    _assert_schema_valid_and_anchor(infectious_brief, INFECTIOUS_OBSERVATION, _INFECTIOUS_TERMS_RE)


# ---------------------------------------------------------------------------
# Metabolic/endocrine (T2D on metformin).
# ---------------------------------------------------------------------------

_METABOLIC_TERMS_RE = re.compile(r"diabet", re.IGNORECASE)


@pytest.fixture(scope="module")
def metabolic_brief() -> TransBrief:
    brief = run_transbench_live_with_retry(METABOLIC_OBSERVATION, focus_drug="metformin")
    _print_domain_summary("metabolic (type 2 diabetes)", METABOLIC_OBSERVATION, brief)
    return brief


def test_metabolic_brief_is_schema_valid_and_anchors_on_diabetes_not_hypertension(
    metabolic_brief: TransBrief,
) -> None:
    _assert_schema_valid_and_anchor(metabolic_brief, METABOLIC_OBSERVATION, _METABOLIC_TERMS_RE)
