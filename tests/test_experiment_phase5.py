"""Phase 5 acceptance test (KICKOFF.md Phase 5 / BUILD_SPEC.md §5, §8, §9).

Mixes live tests (skipped without a working ``ANTHROPIC_API_KEY``, or without
NCBI reachability for the NCBI-only ones) with several fully-OFFLINE,
deterministic tests (zero network, zero API cost) — deliberately NO
module-level ``pytestmark`` skip (that would incorrectly skip the offline
tests too; mirrors ``test_pubmed_query_builder.py``'s own documented
reasoning), so only the live tests carry their own decorators.

Live (agents 7-8, full pipeline, needs ANTHROPIC_API_KEY): exactly ONE
flagship pass — a candidate is selected, its ``ExperimentPlan`` names a
dataset that is INDEPENDENTLY RE-VERIFIED (real NCBI network call, not a
host-only check — Opus verification finding, see below) to actually resolve
to a matching dataset, with a runnable protocol + ``claude_science_prompt``,
the full ``TransBrief`` validates, and ``run_manifest["retrieval_snapshot"]``
is populated (BUILD_SPEC.md §9).

Dataset CONTENT verification (Opus verification finding, post-Phase-5-v1):
a host-only / reachability-only dataset_pointer check is NOT enough — an
accession can resolve (HTTP 200, recognized host) while describing a
COMPLETELY DIFFERENT dataset than the plan claims. Confirmed live, TWICE,
independently: this repo's own earlier flagship run named GSE200257 as
adrenal-cortex bulk RNA-seq when it is REALLY single-cell blood/tonsillar
T-cell data; Opus's verifier run named GSE200827 as KPMP kidney
single-nucleus RNA-seq when it is REALLY an HL-60 leukemia-cell-line
microarray SuperSeries. ``agents.run_design_experiment`` now fetches the
REAL GEO record (``agents._fetch_geo_soft_record``) and checks organism +
keyword-overlap consistency (``agents._verify_geo_record_matches_claim``)
before ever accepting a model-named accession; on rejection it retries with
Tabula Sapiens FORCED and rewrites dataset/dataset_pointer/
feasibility_notes/claude_science_prompt/protocol_steps/method/confirm_if/
refute_if so the rejected accession never leaks into the final plan.

Offline/deterministic:
  - ``_is_recognized_dataset_pointer``'s allow-list (agent 7's fast
    structural pre-filter, layered under the real content-verification
    above).
  - GEO SOFT-text parsing (``_parse_geo_soft_text``) and accession
    extraction (``_extract_geo_accession``).
  - ``_verify_geo_record_matches_claim`` against REAL, hand-captured GEO
    records (a genuine kidney match, and the two confirmed-live mismatches
    above) — proves the keyword-overlap heuristic actually discriminates.
  - ``run_design_experiment``'s full verify-then-fallback wiring, with
    ``agents._fetch_geo_soft_record`` monkeypatched (returns a mismatched or
    empty record) and a 2-call scripted fake LLM (free-choice attempt +
    Tabula-Sapiens-forced retry) — zero network, zero API cost.
  - ``run_assemble`` end to end against a hand-built ``state`` dict + a fake
    Haiku double: references dedup across hypotheses + grade-attach,
    ``contradictions_surfaced``, the ``top_experiment=None`` -> honest
    sentinel path, ``run_manifest`` keys, and the "uncertainty_note LLM call
    fails/returns garbage -> deterministic fallback, brief is never
    discarded" robustness path.
  - ``run_retrieve``'s snapshot REPLAY (BUILD_SPEC.md §9): a hand-built
    snapshot dict reconstructs the identical ranked abstracts + a working
    registry with ``neutralize_query``/``fetch_evidence_data`` monkeypatched
    to raise if called at all (proves zero network); a snapshot with no
    matching hypothesis id still falls through to (mocked) live retrieval.
  - Token-spend accounting (:func:`transbench.agents.token_spend_session`).

Fake-LLM doubles only implement the ONE method ``agents._ainvoke_json``
actually calls (``await llm.ainvoke(messages)``) — same minimal-double
pattern already established in ``test_rigor_entailment_correlation.py``.
Test registries are built via the REAL, approved ``build_article_registry``
leaf (fed a hand-built ``FetchedData``/``EvidenceFetchResult``), never by
constructing Iatronix's internal ``ArticleRegistry``/``RegistryArticle``
classes directly — the same reuse-seam discipline production code follows
(BUILD_SPEC.md §2).
"""
from __future__ import annotations

import asyncio
import functools
import json
import os
from types import SimpleNamespace
from typing import Any, Optional
from urllib.parse import urlsplit

import anthropic
import httpx
import pytest

from tests.fixtures import FLAGSHIP_OBSERVATION
from transbench import agents, config
from transbench.agents import (
    TransBenchLLMError,
    _extract_geo_accession,
    _is_recognized_dataset_pointer,
    _parse_geo_soft_text,
    _verify_geo_record_matches_claim,
    run_assemble,
    run_design_experiment,
    run_retrieve,
)
from transbench.engine import run_transbench
from transbench.reuse import EvidenceFetchResult, FetchedData, build_article_registry
from transbench.rigor import select_experiment_candidate
from transbench.schemas import EvidenceItem, GradedHypothesis, Hypothesis, Reference, TransBrief

# ---------------------------------------------------------------------------
# Shared fakes (fully offline — no network, no real Anthropic call)
# ---------------------------------------------------------------------------


class _FakeAIMessage:
    """Minimal stand-in for a LangChain ``AIMessage`` — only ``.content`` and
    ``.usage_metadata`` are ever read by ``agents.py`` (``_response_text``/
    ``_record_token_usage``)."""

    def __init__(self, content: str, usage_metadata: Optional[dict] = None) -> None:
        self.content = content
        self.usage_metadata = usage_metadata or {}


class _FakeJSONLLM:
    """Fake ``llm`` double matching the ONE method ``agents._ainvoke_json``
    calls (``await llm.ainvoke(messages)``). Returns either ONE fixed
    response (used for every call) or a SEQUENCE of responses consumed in
    call order (for testing multi-call flows, e.g. ``run_design_experiment``
    's verify-then-retry path) — the last provided response repeats for any
    further calls beyond the scripted sequence. Records every call's
    messages."""

    def __init__(self, response_contents: str | list[str], usage_metadata: Optional[dict] = None) -> None:
        self._responses = response_contents if isinstance(response_contents, list) else [response_contents]
        self._usage_metadata = usage_metadata
        self.calls: list[list[Any]] = []

    async def ainvoke(self, messages: list[Any]) -> _FakeAIMessage:
        self.calls.append(messages)
        idx = min(len(self.calls) - 1, len(self._responses) - 1)
        return _FakeAIMessage(self._responses[idx], self._usage_metadata)


class _FakeRaisingLLM:
    """Fake ``llm`` double whose ``ainvoke`` always raises — simulates a
    transient failure on agent 8's ``uncertainty_note`` sub-call."""

    async def ainvoke(self, messages: list[Any]) -> _FakeAIMessage:
        raise RuntimeError("simulated transient LLM failure")


def _json_llm(payload: Any, usage_metadata: Optional[dict] = None) -> _FakeJSONLLM:
    """A fake LLM returning ONE fixed JSON response for every call."""
    return _FakeJSONLLM(json.dumps(payload), usage_metadata)


def _sequential_json_llm(payloads: list[Any], usage_metadata: Optional[dict] = None) -> _FakeJSONLLM:
    """A fake LLM returning a DIFFERENT scripted JSON response for each
    successive call, in order (repeats the last one if over-called)."""
    return _FakeJSONLLM([json.dumps(p) for p in payloads], usage_metadata)


def _skip_on_ncbi_connection_error(fn):
    """Decorator: skip (not fail) a real-NCBI-network test if NCBI itself is
    unreachable from this environment — keeps a pure-connectivity problem
    from being conflated with a genuine code regression."""

    @functools.wraps(fn)
    def wrapper(*args: Any, **kwargs: Any) -> Any:
        try:
            return fn(*args, **kwargs)
        except (httpx.TransportError, httpx.TimeoutException) as exc:
            pytest.skip(f"NCBI unreachable from this environment: {exc}")

    return wrapper


def _hypothesis(hyp_id: str = "h1") -> Hypothesis:
    return Hypothesis(
        id=hyp_id,
        axis="immune_inflammatory",
        statement=(
            "Effector-memory T cells drive RAAS-resistant hypertension via "
            "an IL-17-linked vascular inflammatory program."
        ),
        prediction="Depleting effector-memory T cells lowers BP in resistant hypertension.",
        rationale="r",
        priority="high",
    )


def _supporting_evidence(pmid: str = "18772860", grade: str = "rct") -> EvidenceItem:
    return EvidenceItem(
        claim_fragment="T cells are required for angiotensin-II-induced hypertension in this model.",
        reference=Reference(
            source="PubMed",
            title="T cells and hypertension",
            year=2010,
            url=f"https://pubmed.ncbi.nlm.nih.gov/{pmid}/",
            pmid=pmid,
            grade=grade,
        ),
        supports=True,
        entailment="supports",
        grade=grade,
    )


def _make_registry(abstracts: list[dict]):
    """Build a real ``ArticleRegistry`` via the approved reuse-seam leaf
    (``build_article_registry``), fed a hand-built ``FetchedData`` — never
    constructs Iatronix's internal registry classes directly."""
    fd = FetchedData(
        query_type="evidence",
        evidence_data=EvidenceFetchResult(
            clinical_trial_abstracts=abstracts,
            systematic_review_abstracts=[],
            guideline_abstracts=[],
            fetch_success=bool(abstracts),
        ),
    )
    return build_article_registry(fd)


# ---------------------------------------------------------------------------
# Offline: _is_recognized_dataset_pointer allow-list
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "url",
    [
        "https://cellxgene.cziscience.com/collections/abc-123",
        "https://tabula-sapiens-portal.ds.czbiohub.org/",
        "https://www.ncbi.nlm.nih.gov/geo/query/acc.cgi?acc=GSE12345",
        "https://www.ebi.ac.uk/biostudies/arrayexpress/studies/E-MTAB-9999",
        "https://doi.org/10.1038/s41586-021-03634-9",
    ],
)
def test_is_recognized_dataset_pointer_accepts_named_hosts(url: str) -> None:
    assert _is_recognized_dataset_pointer(url) is True


@pytest.mark.parametrize(
    "url",
    [
        None,
        "",
        "not-a-url",
        "http://www.ncbi.nlm.nih.gov/geo/query/acc.cgi?acc=GSE12345",  # http, not https
        "https://example.com/definitely-not-a-recognized-dataset-host",
        "https://evil-cellxgene.cziscience.com.attacker.example/",  # NOT a real subdomain
    ],
)
def test_is_recognized_dataset_pointer_rejects_everything_else(url: Optional[str]) -> None:
    assert _is_recognized_dataset_pointer(url) is False


# ---------------------------------------------------------------------------
# Offline: agent 7 (run_design_experiment) -- structural pre-filter /
# question-synonym / required-field tests. These are NOT about dataset
# CONTENT verification (that has its own dedicated section below), so each
# monkeypatches ``agents._verify_dataset_pointer`` to a fixed, fast,
# offline-safe verdict to keep them focused and network-free.
# ---------------------------------------------------------------------------


def test_run_design_experiment_falls_back_when_dataset_pointer_unrecognized(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Never reaches _verify_dataset_pointer at all -- the structural
    # pre-filter (unrecognized host) rejects before any network call would
    # happen -- so no monkeypatch is needed for THIS one specifically, but
    # the retry (2nd Sonnet call, forced Tabula Sapiens) still fires.
    fake_llm = _sequential_json_llm(
        [
            {
                "hypothesis_id": "wrong-id-the-model-made-up",
                "question": "Does marker X mark a distinct effector-memory T-cell state?",
                "dataset": "Some Unverified Dataset",
                "dataset_pointer": "https://example.com/not-a-real-dataset-host",
                "method": "scRNA-seq differential-state analysis",
                "protocol_steps": ["Load the dataset", "Cluster T cells", "Compare marker expression"],
                "confirm_if": "Marker X is significantly enriched in the effector-memory cluster.",
                "refute_if": "No differential enrichment of marker X is found across clusters.",
                "feasibility_notes": "Should be feasible with public data.",
                "claude_science_prompt": "Run a differential-expression analysis on the T-cell compartment.",
            },
            {
                "hypothesis_id": "wrong-id-the-model-made-up",
                "question": "Does marker X mark a distinct T-cell state within Tabula Sapiens' immune compartment?",
                "dataset": config.DEFAULT_DATASET,
                "dataset_pointer": config.DEFAULT_DATASET_POINTER,
                "method": "scRNA-seq differential-state analysis of the Tabula Sapiens immune compartment.",
                "protocol_steps": ["Load Tabula Sapiens immune compartment", "Cluster T cells", "Compare marker expression"],
                "confirm_if": "Marker X is significantly enriched in the effector-memory cluster.",
                "refute_if": "No differential enrichment of marker X is found across clusters.",
                "feasibility_notes": "Guaranteed-resolvable pinned atlas.",
                "claude_science_prompt": "Using the Tabula Sapiens immune compartment, run a differential-expression analysis.",
            },
        ]
    )

    plan = asyncio.run(run_design_experiment(_hypothesis("h-real-id"), [_supporting_evidence()], fake_llm))

    assert len(fake_llm.calls) == 2  # free-choice attempt + Tabula-Sapiens-forced retry
    assert plan.hypothesis_id == "h-real-id"  # never trusts the model's echoed id
    assert plan.dataset == config.DEFAULT_DATASET
    assert plan.dataset_pointer == config.DEFAULT_DATASET_POINTER
    assert _is_recognized_dataset_pointer(plan.dataset_pointer)
    assert "pinned default" in plan.feasibility_notes.lower()
    assert "not a well-formed https url" in plan.feasibility_notes.lower()  # the real rejection reason, recorded
    assert "example.com" not in plan.claude_science_prompt  # the rejected dataset never leaks into the final prompt
    assert plan.protocol_steps == [
        "Load Tabula Sapiens immune compartment", "Cluster T cells", "Compare marker expression",
    ]


def test_run_design_experiment_keeps_recognized_dataset_pointer_verbatim(monkeypatch: pytest.MonkeyPatch) -> None:
    # Content verification is tested separately (below); here it is forced
    # to succeed so this test stays focused on "a verified pointer is used
    # verbatim, unmodified" and stays offline/network-free.
    async def _always_verified(dataset: str, dataset_pointer: Any, claimed_text: str) -> tuple[bool, str]:
        return True, ""

    monkeypatch.setattr(agents, "_verify_dataset_pointer", _always_verified)

    fake_llm = _json_llm(
        {
            "hypothesis_id": "irrelevant",
            "question": "Does the candidate regulator mark a distinct T-cell state in resistant hypertension?",
            "dataset": "GSE12345 (peripheral blood scRNA-seq, resistant hypertension cohort)",
            "dataset_pointer": "https://www.ncbi.nlm.nih.gov/geo/query/acc.cgi?acc=GSE12345",
            "method": "scRNA-seq differential-state analysis",
            "protocol_steps": ["Download GSE12345", "QC + cluster", "Differential expression"],
            "confirm_if": "The regulator is enriched in the effector-memory cluster.",
            "refute_if": "No enrichment is found.",
            "feasibility_notes": "Directly relevant public dataset for this cohort.",
            "claude_science_prompt": "Run a differential-expression analysis on GSE12345.",
        }
    )

    plan = asyncio.run(run_design_experiment(_hypothesis("h1"), [_supporting_evidence()], fake_llm))

    assert len(fake_llm.calls) == 1  # verified on the first attempt -- no retry needed
    assert plan.dataset_pointer == "https://www.ncbi.nlm.nih.gov/geo/query/acc.cgi?acc=GSE12345"
    assert plan.dataset.startswith("GSE12345")
    assert "pinned default" not in plan.feasibility_notes.lower()
    assert plan.hypothesis_id == "h1"


def test_run_design_experiment_accepts_title_as_a_question_synonym(monkeypatch: pytest.MonkeyPatch) -> None:
    """Regression guard for a REAL defect found on this phase's own live
    flagship run: Sonnet returned an otherwise excellent, fully-grounded
    response (a real GEO accession, a complete protocol) but used the key
    ``"title"`` instead of ``"question"`` — because BUILD_SPEC.md's own
    verbatim ``EXPERIMENT_DESIGNER_SYSTEM_PROMPT`` text never spells out
    "question" as a literal JSON key the way it does for every other field.
    This is a trimmed-down reproduction of that exact real response shape.
    Dataset content verification is forced to succeed (monkeypatched) —
    this test is specifically about the question/title synonym handling,
    not dataset verification (covered separately below; a GSE200257 pointer
    with an adrenal-cortex claim is in fact one of the two REAL confirmed
    -live content mismatches this whole fix addresses, so it must NOT be
    used here as if it were a legitimate accept-verbatim case).
    """
    async def _always_verified(dataset: str, dataset_pointer: Any, claimed_text: str) -> tuple[bool, str]:
        return True, ""

    monkeypatch.setattr(agents, "_verify_dataset_pointer", _always_verified)

    fake_llm = _json_llm(
        {
            "hypothesis_id": "H1",
            "title": "MR Overactivation via Aldosterone Breakthrough Sustains ENaC-Mediated Sodium Retention",
            "dataset": "GSE200257",
            "dataset_pointer": "https://www.ncbi.nlm.nih.gov/geo/query/acc.cgi?acc=GSE200257",
            "dataset_description": "Bulk RNA-seq of human adrenal cortex tissue (extra field -- must be ignored, not crash).",
            "method": "Differential expression + co-expression network analysis of adrenal cortex transcriptomes.",
            "protocol_steps": ["Download GSE200257", "Run DESeq2", "Correlate CMA1 with CYP11B2"],
            "confirm_if": "CYP11B2 is significantly upregulated and correlates with CMA1.",
            "refute_if": "No significant correlation between CMA1 and CYP11B2 is found.",
            "feasibility_notes": "Publicly available GEO dataset; accession verified to resolve.",
            "claude_science_prompt": "Using GEO dataset GSE200257, run DESeq2 in R to test co-expression of CMA1 and CYP11B2.",
        }
    )

    plan = asyncio.run(run_design_experiment(_hypothesis("H1"), [_supporting_evidence()], fake_llm))

    assert plan.question == (
        "MR Overactivation via Aldosterone Breakthrough Sustains ENaC-Mediated Sodium Retention"
    )
    assert plan.dataset == "GSE200257"
    assert plan.dataset_pointer == "https://www.ncbi.nlm.nih.gov/geo/query/acc.cgi?acc=GSE200257"
    assert plan.hypothesis_id == "H1"


def test_run_design_experiment_raises_on_missing_required_fields(monkeypatch: pytest.MonkeyPatch) -> None:
    async def _always_verified(dataset: str, dataset_pointer: Any, claimed_text: str) -> tuple[bool, str]:
        return True, ""

    monkeypatch.setattr(agents, "_verify_dataset_pointer", _always_verified)

    fake_llm = _json_llm(
        {
            "hypothesis_id": "h1",
            "dataset": "GSE99999",
            "dataset_pointer": "https://doi.org/10.1000/xyz123",
            "question": "",
            "method": "",
            "protocol_steps": [],
            "confirm_if": "",
            "refute_if": "",
            "feasibility_notes": "n/a",
            "claude_science_prompt": "",
        }
    )

    with pytest.raises(TransBenchLLMError):
        asyncio.run(run_design_experiment(_hypothesis(), [_supporting_evidence()], fake_llm))


# ---------------------------------------------------------------------------
# Offline: GEO SOFT-text parsing + accession extraction (pure functions).
# ---------------------------------------------------------------------------


def test_parse_geo_soft_text_extracts_repeated_fields() -> None:
    text = (
        "^SERIES = GSE1\n"
        "!Series_title = Example title\n"
        "!Series_summary = Example summary.\n"
        "!Series_overall_design = Part one.\n"
        "!Series_overall_design = Part two.\n"
        "!Series_type = Expression profiling by array\n"
        "!Series_platform_organism = Homo sapiens\n"
    )
    fields = _parse_geo_soft_text(text)
    assert fields is not None
    assert fields["Series_title"] == ["Example title"]
    assert fields["Series_overall_design"] == ["Part one.", "Part two."]
    assert fields["Series_platform_organism"] == ["Homo sapiens"]


def test_parse_geo_soft_text_returns_none_for_html_error_page() -> None:
    # A trimmed real capture of NCBI's actual HTML response for a
    # nonexistent accession (status 200, but no SOFT fields at all).
    html = (
        '<!DOCTYPE HTML PUBLIC "-//W3C//DTD HTML 4.01 Transitional//EN">\n'
        "<HTML>\n  <HEAD>\n    <TITLE>\n    GEO Accession viewer\n    </TITLE>\n  </HEAD>\n</HTML>\n"
    )
    assert _parse_geo_soft_text(html) is None


def test_parse_geo_soft_text_returns_none_for_empty_text() -> None:
    assert _parse_geo_soft_text("") is None


def test_extract_geo_accession_prefers_pointer_then_dataset() -> None:
    assert (
        _extract_geo_accession("GSE1 something", "https://www.ncbi.nlm.nih.gov/geo/query/acc.cgi?acc=GSE2")
        == "GSE2"
    )
    assert _extract_geo_accession("GSE3 something", "https://tabula-sapiens-portal.ds.czbiohub.org/") == "GSE3"
    assert _extract_geo_accession("no accession here", "https://tabula-sapiens-portal.ds.czbiohub.org/") is None


# ---------------------------------------------------------------------------
# Offline: dataset CONTENT-verification keyword matching, against REAL
# hand-captured GEO records (Opus verification finding). No network -- these
# are literal, trimmed captures of real ``_fetch_geo_soft_record`` output
# fetched live during this fix's own development (kept verbatim so the test
# proves the matcher against REALITY, not an idealized hypothetical).
# ---------------------------------------------------------------------------

# GSE121862 — a REAL, genuine match for a KPMP human kidney snRNA-seq claim.
_REAL_KIDNEY_RECORD: dict[str, list[str]] = {
    "Series_title": [
        "A single-nucleus RNA-sequencing pipeline to decipher the molecular "
        "anatomy and pathophysiology of human kidneys"
    ],
    "Series_summary": [
        "Defining cellular and molecular identities within the kidney is necessary to "
        "understand its organization and function in health and disease. Here we "
        "demonstrate a reproducible method with minimal artifacts for single-nucleus "
        "Droplet-based RNA sequencing (snDrop-Seq) that we use to resolve thirty "
        "distinct cell populations in human adult kidney. We define molecular "
        "transition states along more than ten nephron segments spanning two major "
        "kidney regions. We further delineate cell type-specific expression of genes "
        "associated with chronic kidney disease, diabetes and hypertension."
    ],
    "Series_overall_design": [
        "Single-nucleus (sn)Drop-seq was used to generate RNA expression estimates "
        "across two kidney regions (cortex and medulla), 15 different individuals, 7 "
        "different tissue processing methods, and from tissues acquired from two "
        "different institutions (Washington University and University of Michigan "
        "through KPMP consortium).",
        "From the resulting ~18,000 sequenced nuclei passing QC filtering, we "
        "identified 30 different cell populations.",
    ],
    "Series_type": ["Expression profiling by high throughput sequencing"],
    "Series_platform_organism": ["Homo sapiens"],
    "Series_sample_organism": ["Homo sapiens"],
}

# GSE200827 — a REAL record: Opus verification's exact confirmed-live
# mismatch (an HL-60 leukemia-cell-line microarray SuperSeries, NOT the KPMP
# human-kidney snRNA-seq dataset a plan wrongly claimed it to be).
_REAL_HL60_RECORD: dict[str, list[str]] = {
    "Series_title": [
        "Gene expression profiles during the process of differentiation of "
        "HL-60 cells into neutrophils or eosinophils"
    ],
    "Series_summary": ["This SuperSeries is composed of the SubSeries listed below."],
    "Series_overall_design": ["Refer to individual Series"],
    "Series_type": ["Expression profiling by array"],
    "Series_platform_organism": ["Homo sapien"],  # the real observed truncation (no trailing "s")
}

# GSE200257 — a REAL record: THIS repo's own earlier flagship run's
# confirmed-live mismatch (single-cell blood/tonsillar T-cell data, NOT the
# "adrenal cortex bulk RNA-seq" a plan wrongly claimed it to be).
_REAL_TCELL_RECORD: dict[str, list[str]] = {
    "Series_title": ["Single-cell RNA-sequencing of blood and tonsillar CD4+ CD57+ and CD57- T cells"],
    "Series_summary": [
        "Gene-environment interactions are implicated in immunopathology. We defined "
        "two terminal-differentiation pathways for human CD4+ T cells each marked by "
        "CD57. Rare blood CD57+ CD4+ T cells marked by TCF1 downregulation adopt a "
        "cytotoxic and terminal-effector transcriptome."
    ],
    "Series_overall_design": [
        "CD57+ CD4+ T cells and CD57- CD4+ T cells from tonsil and blood samples of a "
        "child were FACS-purified and processed with 10x Genomics single cell "
        "gene expression and VDJ libraries."
    ],
    "Series_type": ["Expression profiling by high throughput sequencing", "Other"],
    "Series_sample_organism": ["Homo sapiens"],
}


def test_verify_geo_record_accepts_genuine_kidney_match() -> None:
    claimed = (
        "GSE121862 KPMP human kidney single-nucleus RNA-seq study of chronic "
        "kidney disease across nephron segments, cortex and medulla"
    )
    ok, reason = _verify_geo_record_matches_claim(_REAL_KIDNEY_RECORD, claimed)
    assert ok is True, reason


def test_verify_geo_record_rejects_kidney_claim_against_real_hl60_record() -> None:
    """THE exact defect Opus verification found live: a plan claims GSE200827
    is a KPMP human kidney dataset; the real record is HL-60 leukemia-cell
    differentiation. Must be rejected."""
    claimed = "GSE200827 KPMP human kidney single-nucleus RNA-seq, ~50 donors, CKD 1-5"
    ok, reason = _verify_geo_record_matches_claim(_REAL_HL60_RECORD, claimed)
    assert ok is False
    assert "hl-60" in reason.lower() or "neutrophils" in reason.lower()


def test_verify_geo_record_rejects_adrenal_claim_against_real_tcell_record() -> None:
    """This repo's OWN earlier (pre-fix) flagship run's confirmed-live
    mismatch: a plan claimed GSE200257 was adrenal-cortex bulk RNA-seq; the
    real record is single-cell blood/tonsillar T-cell data. Must be
    rejected."""
    claimed = (
        "Bulk RNA-seq of human adrenal cortex tissue samples including "
        "aldosterone-producing adenomas APA and bilateral adrenal hyperplasia BAH"
    )
    ok, reason = _verify_geo_record_matches_claim(_REAL_TCELL_RECORD, claimed)
    assert ok is False
    assert "t cells" in reason.lower() or "tonsillar" in reason.lower()


def test_verify_geo_record_accepts_homo_sapien_truncation() -> None:
    """The organism check must not falsely reject the real "Homo sapien" (no
    trailing s) truncation observed live in the HL-60 record's own
    ``Series_platform_organism`` field -- rejection for an unrelated claim
    must come from the KEYWORD check, never the organism check."""
    ok, reason = _verify_geo_record_matches_claim(
        _REAL_HL60_RECORD, "totally unrelated claim text sharing no real keywords at all"
    )
    assert ok is False
    assert "organism" not in reason.lower()


def test_verify_geo_record_rejects_non_human_organism() -> None:
    record = {
        "Series_title": ["A comprehensive single-cell atlas of mouse kidney"],
        "Series_summary": ["A comprehensive single-cell RNA-seq atlas of the mouse kidney."],
        "Series_platform_organism": ["Mus musculus"],
        "Series_sample_organism": ["Mus musculus"],
    }
    ok, reason = _verify_geo_record_matches_claim(record, "comprehensive mouse kidney single-cell atlas")
    assert ok is False
    assert "organism" in reason.lower()


# ---------------------------------------------------------------------------
# Offline: run_design_experiment's full verify-then-fallback WIRING, with
# agents._fetch_geo_soft_record monkeypatched (the exact seam BUILD_SPEC's
# coordinator asked for) -- proves the fallback REWRITES dataset_pointer AND
# claude_science_prompt (not just dataset/feasibility_notes) so the rejected
# accession never leaks into the final plan. Zero network, zero API cost.
# ---------------------------------------------------------------------------


def test_run_design_experiment_falls_back_and_rewrites_prompt_on_content_mismatch(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    first_response = {
        "hypothesis_id": "h1",
        "question": "Does marker X mark a distinct kidney cell population?",
        "dataset": "GSE200827 (KPMP human kidney single-nucleus RNA-seq, ~50 donors, CKD 1-5)",
        "dataset_pointer": "https://www.ncbi.nlm.nih.gov/geo/query/acc.cgi?acc=GSE200827",
        "method": "Single-nucleus RNA-seq differential expression in human kidney across CKD stages.",
        "protocol_steps": ["Download GSE200827", "Cluster kidney nuclei", "Compare across CKD stage"],
        "confirm_if": "Marker X is enriched in the relevant kidney cell population.",
        "refute_if": "No enrichment is found.",
        "feasibility_notes": "KPMP kidney atlas, verified to resolve.",
        "claude_science_prompt": "Using GSE200827 (KPMP human kidney snRNA-seq), analyze kidney nuclei for marker X.",
    }
    retry_response = {
        "hypothesis_id": "h1",
        "question": "Does marker X mark a distinct kidney-relevant cell population in Tabula Sapiens?",
        "dataset": "ignored -- should be overridden",
        "dataset_pointer": "https://also-ignored.example.com/",
        "method": "Analysis of the kidney compartment of the Tabula Sapiens atlas.",
        "protocol_steps": ["Load the Tabula Sapiens kidney compartment", "Cluster cells", "Test marker X expression"],
        "confirm_if": "Marker X is enriched in a distinct kidney cell population within Tabula Sapiens.",
        "refute_if": "No such enrichment is found.",
        "feasibility_notes": "Using the pinned Tabula Sapiens atlas kidney compartment.",
        "claude_science_prompt": "Using the Tabula Sapiens atlas (kidney compartment), analyze marker X expression across kidney cell types.",
    }
    fake_llm = _sequential_json_llm([first_response, retry_response])

    async def _fake_fetch_geo(accession: str) -> dict[str, list[str]]:
        assert accession == "GSE200827"
        return dict(_REAL_HL60_RECORD)  # the real, topically UNRELATED record

    monkeypatch.setattr(agents, "_fetch_geo_soft_record", _fake_fetch_geo)

    plan = asyncio.run(run_design_experiment(_hypothesis("h1"), [_supporting_evidence()], fake_llm))

    assert len(fake_llm.calls) == 2  # the free-choice attempt + the forced Tabula-Sapiens retry
    assert plan.dataset == config.DEFAULT_DATASET
    assert plan.dataset_pointer == config.DEFAULT_DATASET_POINTER
    # The rejected accession must never leak into ANY final field.
    assert "GSE200827" not in plan.claude_science_prompt
    assert "GSE200827" not in plan.dataset_pointer
    assert all("GSE200827" not in step for step in plan.protocol_steps)
    assert "GSE200827" not in plan.method
    assert "tabula sapiens" in plan.claude_science_prompt.lower()
    assert "kidney" in plan.claude_science_prompt.lower()  # the hypothesis-relevant compartment, from the retry
    assert "pinned default" in plan.feasibility_notes.lower()
    assert "hl-60" in plan.feasibility_notes.lower() or "does not match" in plan.feasibility_notes.lower()


def test_run_design_experiment_falls_back_when_geo_accession_does_not_resolve(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    first_response = {
        "hypothesis_id": "h1",
        "question": "Does marker X mark a distinct kidney cell population?",
        "dataset": "GSE99999999 (fabricated accession)",
        "dataset_pointer": "https://www.ncbi.nlm.nih.gov/geo/query/acc.cgi?acc=GSE99999999",
        "method": "snRNA-seq differential expression.",
        "protocol_steps": ["Download the dataset", "Cluster", "Compare"],
        "confirm_if": "Marker X is enriched.",
        "refute_if": "No enrichment is found.",
        "feasibility_notes": "Should resolve.",
        "claude_science_prompt": "Using GSE99999999, analyze marker X.",
    }
    retry_response = {
        "hypothesis_id": "h1",
        "question": "Does marker X mark a distinct kidney-relevant cell population in Tabula Sapiens?",
        "dataset": "ignored",
        "dataset_pointer": "https://ignored.example.com/",
        "method": "Analysis of the kidney compartment of the Tabula Sapiens atlas.",
        "protocol_steps": ["Load the Tabula Sapiens kidney compartment", "Cluster", "Compare"],
        "confirm_if": "Marker X is enriched within Tabula Sapiens.",
        "refute_if": "No enrichment is found.",
        "feasibility_notes": "Using the pinned Tabula Sapiens atlas.",
        "claude_science_prompt": "Using the Tabula Sapiens atlas (kidney compartment), analyze marker X.",
    }
    fake_llm = _sequential_json_llm([first_response, retry_response])

    async def _fake_fetch_geo_none(accession: str) -> None:
        return None  # simulates a genuinely nonexistent / unresolvable accession

    monkeypatch.setattr(agents, "_fetch_geo_soft_record", _fake_fetch_geo_none)

    plan = asyncio.run(run_design_experiment(_hypothesis("h1"), [_supporting_evidence()], fake_llm))

    assert plan.dataset_pointer == config.DEFAULT_DATASET_POINTER
    assert "GSE99999999" not in plan.claude_science_prompt
    assert "does not resolve" in plan.feasibility_notes.lower() or "pinned default" in plan.feasibility_notes.lower()


# ---------------------------------------------------------------------------
# Live (real NCBI network, NO Anthropic key needed): the fetch+parse+match
# pipeline against REAL, live NCBI responses (not just the hand-captured
# fixtures above) -- proves the wiring genuinely works against reality, not
# only against a controlled offline snapshot.
# ---------------------------------------------------------------------------


@_skip_on_ncbi_connection_error
def test_verify_dataset_pointer_against_real_ncbi_rejects_known_mismatch() -> None:
    """Real network call to REAL NCBI -- the exact accession Opus
    verification found live: GSE200827 genuinely resolves (a real HL-60
    leukemia microarray SuperSeries), but does NOT match a claimed KPMP
    human-kidney snRNA-seq plan."""
    claimed = (
        "KPMP human kidney single-nucleus RNA-seq study of chronic kidney "
        "disease across CKD stages 1-5, nephron segments"
    )
    verified, reason = asyncio.run(
        agents._verify_dataset_pointer(
            "GSE200827", "https://www.ncbi.nlm.nih.gov/geo/query/acc.cgi?acc=GSE200827", claimed
        )
    )
    assert verified is False, reason


@_skip_on_ncbi_connection_error
def test_verify_dataset_pointer_against_real_ncbi_rejects_nonexistent_accession() -> None:
    verified, reason = asyncio.run(
        agents._verify_dataset_pointer(
            "GSE99999999", "https://www.ncbi.nlm.nih.gov/geo/query/acc.cgi?acc=GSE99999999", "anything at all"
        )
    )
    assert verified is False
    assert "does not resolve" in reason.lower()


@_skip_on_ncbi_connection_error
def test_verify_dataset_pointer_against_real_ncbi_accepts_genuine_match() -> None:
    claimed = (
        "GSE121862 KPMP human kidney single-nucleus RNA-seq chronic kidney "
        "disease nephron segments cortex medulla"
    )
    verified, reason = asyncio.run(
        agents._verify_dataset_pointer(
            "GSE121862", "https://www.ncbi.nlm.nih.gov/geo/query/acc.cgi?acc=GSE121862", claimed
        )
    )
    assert verified is True, reason


@_skip_on_ncbi_connection_error
def test_tabula_sapiens_default_pointer_resolves() -> None:
    """BUILD_SPEC.md §8 / Opus verification requirement: confirm the pinned
    fallback default itself genuinely resolves -- the guaranteed-safe
    substrate every fallback trusts absolutely (without re-verification,
    for speed -- see run_design_experiment's docstring) must actually BE
    reachable."""
    response = httpx.get(config.DEFAULT_DATASET_POINTER, timeout=15, follow_redirects=True)
    assert response.status_code == 200


# ---------------------------------------------------------------------------
# Offline: agent 8 (run_assemble)
# ---------------------------------------------------------------------------


def test_run_assemble_builds_references_contradictions_and_manifest(monkeypatch: pytest.MonkeyPatch) -> None:
    # This test is about run_assemble, not dataset verification -- force
    # run_design_experiment's (embedded, below) dataset check to succeed
    # without a network call, keeping this test genuinely offline (its
    # dataset_pointer is the Tabula Sapiens URL, which is NOT GEO-shaped and
    # would otherwise route to _verify_dataset_pointer's real reachability
    # check).
    async def _always_verified(dataset: str, dataset_pointer: Any, claimed_text: str) -> tuple[bool, str]:
        return True, ""

    monkeypatch.setattr(agents, "_verify_dataset_pointer", _always_verified)

    registry_a = _make_registry(
        [
            {"pmid": "11111111", "title": "T cells and hypertension", "abstract": "x", "year": 2015, "journal": "Hypertension"},
            {"pmid": "22222222", "title": "Unrelated uncited abstract", "abstract": "x", "year": 2012, "journal": "J Misc"},
        ]
    )
    registry_b = _make_registry(
        [
            {"pmid": "11111111", "title": "T cells and hypertension", "abstract": "x", "year": 2015, "journal": "Hypertension"},
            {"pmid": "33333333", "title": "No association found", "abstract": "x", "year": 2018, "journal": "J Refute"},
        ]
    )
    # Simulate what agents.run_grade does when an article is actually kept
    # as evidence (Phase 5 addition: registry.mark_used(...)) -- exercised
    # directly here since this test hand-builds state rather than running
    # the full retrieve/grade pipeline.
    registry_a.mark_used(registry_a.by_pmid["11111111"])
    registry_b.mark_used(registry_b.by_pmid["11111111"])
    registry_b.mark_used(registry_b.by_pmid["33333333"])

    ev_a1 = EvidenceItem(
        claim_fragment="T cells and hypertension (hyp A angle).",
        reference=Reference(
            source="PubMed", title="T cells and hypertension", year=2015,
            url="https://pubmed.ncbi.nlm.nih.gov/11111111/", pmid="11111111", grade="rct",
        ),
        supports=True, entailment="supports", grade="rct",
    )
    ev_b1 = EvidenceItem(
        claim_fragment="Same article, cited again from hyp B's angle.",
        reference=Reference(
            source="PubMed", title="T cells and hypertension", year=2015,
            url="https://pubmed.ncbi.nlm.nih.gov/11111111/", pmid="11111111", grade="observational",
        ),
        supports=True, entailment="supports", grade="observational",
    )
    ev_b2 = EvidenceItem(
        claim_fragment="No association was found between T cells and blood pressure.",
        reference=Reference(
            source="PubMed", title="No association found", year=2018,
            url="https://pubmed.ncbi.nlm.nih.gov/33333333/", pmid="33333333", grade="observational",
        ),
        supports=False, entailment="refutes", grade="observational",
    )

    graded_a = GradedHypothesis(
        hypothesis=_hypothesis("h-a"), evidence=[ev_a1], supporting_count=1, contradicting_count=0,
        novelty="open_question", novelty_reason="Partially supported per PMID 11111111.",
        confidence="moderate", grounded=True,
    )
    graded_b = GradedHypothesis(
        hypothesis=_hypothesis("h-b"), evidence=[ev_b1, ev_b2], supporting_count=1, contradicting_count=1,
        novelty="open_question", novelty_reason="Mixed evidence per PMID 11111111 and PMID 33333333.",
        confidence="low", grounded=True,
    )

    fake_top_experiment = asyncio.run(
        run_design_experiment(
            _hypothesis("h-a"),
            [ev_a1],
            _json_llm(
                {
                    "hypothesis_id": "h-a", "question": "q", "dataset": config.DEFAULT_DATASET,
                    "dataset_pointer": config.DEFAULT_DATASET_POINTER, "method": "m",
                    "protocol_steps": ["s1"], "confirm_if": "c1", "refute_if": "r1",
                    "feasibility_notes": "f", "claude_science_prompt": "p",
                }
            ),
        )
    )

    state = {
        "observation": "58F, resistant hypertension...",
        "focus_drug": None,
        "axes": [],
        "graded_hypotheses": [graded_a, graded_b],
        "top_experiment": fake_top_experiment,
        "registry_by_hyp_id": {"h-a": registry_a, "h-b": registry_b},
        "retrieval_manifest_by_hyp_id": {
            "h-a": {"neutral_query": "nq-a", "pubmed_query": "pq-a", "abstracts": []},
            "h-b": {"neutral_query": "nq-b", "pubmed_query": "pq-b", "abstracts": []},
        },
        "model_reasoning": config.MODEL_REASONING,
        "model_cheap": config.MODEL_CHEAP,
        "max_hypotheses": 3,
        "retrieval_snapshot": None,
        "run_started_at": "2026-01-01T00:00:00+00:00",
    }
    fake_assembler_llm = _json_llm({"uncertainty_note": "Evidence for PMID 33333333 conflicts with PMID 11111111."})

    # Wrapped in a token-spend session, matching how graph.py actually
    # invokes agents.run_assemble in production (run_transbench_graph wraps
    # the whole graph run) -- makes the run_manifest["token_spend"]
    # assertion below meaningful rather than trivially zero.
    async def _run() -> TransBrief:
        with agents.token_spend_session():
            return await run_assemble(state, fake_assembler_llm)

    brief = asyncio.run(_run())

    assert isinstance(brief, TransBrief)
    revalidated = TransBrief.model_validate(brief.model_dump())
    assert revalidated == brief

    # references: deduped across hypotheses (pmid 11111111 appears in BOTH
    # registries but must be ONE Reference), grade attached from whichever
    # graded_hypotheses entry is processed first (h-a's "rct", not h-b's
    # "observational"), and the retrieved-but-never-cited pmid 22222222 IS
    # included (registry.to_reference_list() returns all entries) with
    # grade=None.
    by_pmid = {r.pmid: r for r in brief.references}
    assert set(by_pmid) == {"11111111", "22222222", "33333333"}
    assert by_pmid["11111111"].grade == "rct"
    assert by_pmid["22222222"].grade is None
    assert by_pmid["33333333"].grade == "observational"

    assert len(brief.contradictions_surfaced) == 1
    assert "33333333" in brief.contradictions_surfaced[0]
    assert "h-b" in brief.contradictions_surfaced[0]

    assert brief.uncertainty_note == "Evidence for PMID 33333333 conflicts with PMID 11111111."
    assert brief.top_experiment.hypothesis_id == "h-a"

    rm = brief.run_manifest
    assert rm["model_reasoning"] == config.MODEL_REASONING
    assert rm["model_cheap"] == config.MODEL_CHEAP
    assert rm["selected_experiment_hypothesis_id"] == "h-a"
    assert rm["dataset_pointer"] == config.DEFAULT_DATASET_POINTER
    assert rm["retrieval_snapshot"] == state["retrieval_manifest_by_hyp_id"]
    assert rm["retrieval_snapshot_provided"] is False
    assert rm["run_started_at"] == "2026-01-01T00:00:00+00:00"
    assert isinstance(rm["generated_at"], str) and rm["generated_at"]
    assert isinstance(rm["token_spend"], dict)
    assert rm["token_spend"]["calls"] >= 1  # this function's own uncertainty_note call


def test_run_assemble_no_candidate_uses_honest_sentinel_experiment_plan() -> None:
    graded = GradedHypothesis(
        hypothesis=_hypothesis("h-established"), evidence=[], supporting_count=0, contradicting_count=0,
        novelty="established", novelty_reason="Textbook mechanism.", confidence="low", grounded=False,
    )
    state = {
        "observation": "obs", "axes": [], "graded_hypotheses": [graded], "top_experiment": None,
        "registry_by_hyp_id": {}, "retrieval_manifest_by_hyp_id": {},
    }
    fake_llm = _json_llm({"uncertainty_note": "No hypothesis was eligible for experiment design this run."})

    brief = asyncio.run(run_assemble(state, fake_llm))

    assert brief.top_experiment.hypothesis_id == "none"
    assert brief.top_experiment.dataset == config.DEFAULT_DATASET
    assert brief.top_experiment.dataset_pointer == config.DEFAULT_DATASET_POINTER
    assert brief.top_experiment.protocol_steps  # non-empty, schema-valid
    assert brief.run_manifest["selected_experiment_hypothesis_id"] == "none"
    # Still a fully schema-valid TransBrief -- this is the point.
    revalidated = TransBrief.model_validate(brief.model_dump())
    assert revalidated == brief


@pytest.mark.parametrize(
    "bad_llm",
    [_FakeJSONLLM("this is not valid json at all {{{ certainly not repairable"), _FakeRaisingLLM()],
    ids=["malformed_json", "llm_raises"],
)
def test_run_assemble_falls_back_to_deterministic_uncertainty_note(bad_llm: Any) -> None:
    """Neither a malformed-JSON response nor an outright exception from the
    ONE uncertainty_note sub-call may ever discard an otherwise-complete
    brief (see run_assemble's own docstring rationale)."""
    graded = GradedHypothesis(
        hypothesis=_hypothesis("h1"), evidence=[], supporting_count=0, contradicting_count=0,
        novelty="unsupported", novelty_reason="No evidence retrieved.", confidence="low", grounded=False,
    )
    state = {
        "observation": "obs", "axes": [], "graded_hypotheses": [graded], "top_experiment": None,
        "registry_by_hyp_id": {}, "retrieval_manifest_by_hyp_id": {},
    }

    brief = asyncio.run(run_assemble(state, bad_llm))

    assert isinstance(brief, TransBrief)
    assert brief.uncertainty_note.strip()
    assert "preliminary" in brief.uncertainty_note.lower()  # the deterministic fallback's own fixed closer
    assert "unsupported" in brief.uncertainty_note.lower() or "1" in brief.uncertainty_note


# ---------------------------------------------------------------------------
# Offline: retrieval-snapshot REPLAY (BUILD_SPEC.md §9)
# ---------------------------------------------------------------------------


def test_run_retrieve_replays_from_snapshot_without_network(monkeypatch: pytest.MonkeyPatch) -> None:
    hypothesis = _hypothesis("h-snap")
    snapshot_abstract = {
        "pmid": "12345678",
        "title": "A snapshot-replayed abstract",
        "abstract": "Full abstract text captured on a prior live run.",
        "year": 2020,
        "journal": "Journal of Snapshots",
        "doi": None,
        "pmcid": None,
        "_rank_score": 12.5,  # extra fields a real ranked abstract carries -- must not break replay
        "_rank_breakdown": {"study_type": 3.0},
    }
    snapshot = {
        hypothesis.id: {
            "neutral_query": "does effector-memory T-cell activity contribute to resistant hypertension",
            "pubmed_query": "hypertension CD8 effector",
            "abstracts": [snapshot_abstract],
        }
    }

    def _boom(*args: Any, **kwargs: Any) -> Any:
        raise AssertionError("network/LLM call attempted during snapshot replay")

    monkeypatch.setattr(agents, "neutralize_query", _boom)
    monkeypatch.setattr(agents, "fetch_evidence_data", _boom)

    result = asyncio.run(
        run_retrieve(hypothesis, user_key=None, observation="hypertension", retrieval_snapshot=snapshot)
    )

    assert result.neutral_query == snapshot[hypothesis.id]["neutral_query"]
    assert result.pubmed_query == snapshot[hypothesis.id]["pubmed_query"]
    assert result.ranked == [snapshot_abstract]  # identical evidence, byte-for-byte
    assert result.registry.by_pmid["12345678"].title == "A snapshot-replayed abstract"
    assert result.registry.by_pmid["12345678"].url == "https://pubmed.ncbi.nlm.nih.gov/12345678/"
    assert result.fd is not None


def test_run_retrieve_replay_is_deterministic_across_repeated_calls() -> None:
    """The same snapshot entry replayed twice produces the identical
    ``ranked`` list both times (no hidden state/ordering nondeterminism) --
    zero network either time (no monkeypatch needed: the real
    neutralize_query/fetch_evidence_data are simply never reached when
    every hypothesis id has a snapshot entry)."""
    hypothesis = _hypothesis("h-snap-2")
    snapshot = {
        hypothesis.id: {
            "neutral_query": "nq",
            "pubmed_query": "pq",
            "abstracts": [
                {"pmid": "1", "title": "First", "abstract": "a", "year": 2019, "journal": "J1"},
                {"pmid": "2", "title": "Second", "abstract": "b", "year": 2021, "journal": "J2"},
            ],
        }
    }

    result_1 = asyncio.run(run_retrieve(hypothesis, user_key=None, retrieval_snapshot=snapshot))
    result_2 = asyncio.run(run_retrieve(hypothesis, user_key=None, retrieval_snapshot=snapshot))

    assert result_1.ranked == result_2.ranked == snapshot[hypothesis.id]["abstracts"]


def test_run_retrieve_falls_through_to_live_when_snapshot_has_no_matching_entry(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A snapshot that does not cover THIS hypothesis's id is a graceful,
    silent fallback to live retrieval -- not an error. Both
    ``neutralize_query`` and ``fetch_evidence_data`` are mocked (never real
    network) but ARE expected to be called, proving the fallback path is
    genuinely taken. The fake fetch returns >=1 pmid-bearing abstract so
    ``has_minimum_evidence`` is satisfied and Iatronix's OWN internal
    ``ensure_evidence`` broadening (which would call ITS OWN un-mocked
    ``fetch_evidence_data`` reference, a real network call) is never
    triggered -- keeping this test genuinely offline.
    """
    hypothesis = _hypothesis("h-live-fallback")
    calls = {"neutralize": 0, "fetch": 0}

    async def _fake_neutralize(raw_query: str, model_id: str, user_key: Any, user_provider: str) -> SimpleNamespace:
        calls["neutralize"] += 1
        return SimpleNamespace(neutral_clinical_question="does X drive Y", entities=["X", "Y"])

    async def _fake_fetch(query: str, **kwargs: Any) -> EvidenceFetchResult:
        calls["fetch"] += 1
        return EvidenceFetchResult(
            clinical_trial_abstracts=[{"pmid": "99999999", "title": "Fake abstract", "abstract": "x", "year": 2020, "journal": "J"}],
            systematic_review_abstracts=[],
            guideline_abstracts=[],
            fetch_success=True,
        )

    monkeypatch.setattr(agents, "neutralize_query", _fake_neutralize)
    monkeypatch.setattr(agents, "fetch_evidence_data", _fake_fetch)

    result = asyncio.run(
        run_retrieve(
            hypothesis,
            user_key=None,
            observation="hypertension",
            retrieval_snapshot={"some-other-hypothesis-id": {"abstracts": []}},
        )
    )

    assert calls["neutralize"] == 1
    assert calls["fetch"] == 2  # support pass + contradiction pass
    assert result.neutral_query == "does X drive Y"
    assert any(a.get("pmid") == "99999999" for a in result.ranked)


# ---------------------------------------------------------------------------
# Offline: token-spend accounting (BUILD_SPEC.md §9)
# ---------------------------------------------------------------------------


def test_token_spend_session_accumulates_across_calls_and_resets_after() -> None:
    zero = {"calls": 0, "input_tokens": 0, "output_tokens": 0, "total_tokens": 0}
    assert agents.current_token_spend() == zero

    llm1 = _json_llm({"a": 1}, usage_metadata={"input_tokens": 100, "output_tokens": 20, "total_tokens": 120})
    llm2 = _json_llm({"b": 2}, usage_metadata={"input_tokens": 50, "output_tokens": 10, "total_tokens": 60})

    async def _run() -> dict:
        with agents.token_spend_session():
            await agents._ainvoke_json(llm1, "sys", "user")
            await agents._ainvoke_json(llm2, "sys", "user")
            return agents.current_token_spend()

    spend = asyncio.run(_run())
    assert spend == {"calls": 2, "input_tokens": 150, "output_tokens": 30, "total_tokens": 180}
    # Outside the session (back in this sync function's own context, which
    # was never `.set()`), the accumulator is the zero default again.
    assert agents.current_token_spend() == zero


# ---------------------------------------------------------------------------
# Live: exactly ONE flagship pass through the full 8-agent pipeline.
# ---------------------------------------------------------------------------

_TRANSIENT_EXCEPTION_TYPES = (anthropic.APIConnectionError, anthropic.InternalServerError)


def _is_transient_anthropic_failure(exc: BaseException) -> bool:
    if isinstance(exc, _TRANSIENT_EXCEPTION_TYPES):
        return True
    if getattr(exc, "status_code", None) == 500:
        return True
    message = str(exc).lower()
    return "internal server error" in message or "overloaded" in message or " 500 " in f" {message} "


def _run_flagship_with_retry() -> TransBrief:
    """Retries EXACTLY ONCE on a transient-looking Anthropic failure (a bare
    500/connection drop) -- a non-transient exception, or a second
    consecutive failure of any kind, still propagates."""
    try:
        return asyncio.run(run_transbench(FLAGSHIP_OBSERVATION))
    except Exception as exc:  # noqa: BLE001 -- narrowed immediately below
        if not _is_transient_anthropic_failure(exc):
            raise
        return asyncio.run(run_transbench(FLAGSHIP_OBSERVATION))


@pytest.mark.skipif(
    not os.environ.get("ANTHROPIC_API_KEY"),
    reason="Phase 5 acceptance test makes REAL Anthropic + PubMed API calls; requires a working ANTHROPIC_API_KEY.",
)
def test_flagship_experiment_plan_and_full_brief() -> None:
    """Real end-to-end call: the full 8-agent pipeline for the flagship
    observation. Asserts a candidate is selected, its ExperimentPlan names a
    dataset whose FINAL dataset_pointer is INDEPENDENTLY RE-VERIFIED (a
    fresh, real NCBI network call in THIS test, not merely trusting that
    run_design_experiment's own internal gate ran -- Opus verification
    finding: a host-only check is not enough, an accession can resolve
    while describing a completely different dataset) to actually resolve to
    a dataset consistent with the plan's own claims -- either the model's
    own accession genuinely verified, or the guaranteed-resolvable Tabula
    Sapiens fallback. The full TransBrief validates, and the BUILD_SPEC.md
    §9 retrieval snapshot is populated."""
    brief = _run_flagship_with_retry()

    revalidated = TransBrief.model_validate(brief.model_dump())
    assert revalidated == brief

    selected = select_experiment_candidate(brief.hypotheses)
    assert selected is not None, (
        "expected the flagship to yield an eligible (open_question + grounded) "
        "hypothesis end to end -- see test_retrieval_phase3.py's own regression "
        "guard for this same property"
    )

    plan = brief.top_experiment
    assert plan.hypothesis_id == selected.hypothesis.id
    assert plan.hypothesis_id != "none"

    assert plan.dataset.strip()
    parsed_pointer = urlsplit(plan.dataset_pointer or "")
    assert parsed_pointer.scheme == "https", f"dataset_pointer must be https: {plan.dataset_pointer!r}"
    assert parsed_pointer.netloc, f"dataset_pointer must have a real host: {plan.dataset_pointer!r}"
    assert _is_recognized_dataset_pointer(plan.dataset_pointer), (
        f"dataset_pointer must resolve to a recognized public dataset host "
        f"(CELLxGENE/Tabula Sapiens/NCBI GEO/ArrayExpress/DOI): {plan.dataset_pointer!r}"
    )

    # THE core Opus-verification-mandated check: an INDEPENDENT, real-network
    # re-verification that the FINAL dataset_pointer actually resolves to a
    # dataset consistent with the plan's own claims -- not merely that it
    # is a well-formed URL to a recognized host (that alone is exactly what
    # let a real-but-wrong accession through before this fix).
    try:
        claimed_text = " ".join(filter(None, [plan.dataset, plan.method, plan.feasibility_notes, plan.question]))
        verified, reason = asyncio.run(agents._verify_dataset_pointer(plan.dataset, plan.dataset_pointer, claimed_text))
    except (httpx.TransportError, httpx.TimeoutException) as exc:
        pytest.skip(f"NCBI unreachable for independent re-verification: {exc}")
    assert verified, (
        f"FINAL dataset_pointer {plan.dataset_pointer!r} (dataset={plan.dataset!r}) failed "
        f"independent re-verification: {reason}"
    )

    assert plan.protocol_steps and all(isinstance(s, str) and s.strip() for s in plan.protocol_steps)
    assert plan.claude_science_prompt.strip()
    assert plan.confirm_if.strip()
    assert plan.refute_if.strip()
    assert plan.method.strip()
    assert plan.question.strip()
    assert plan.feasibility_notes.strip()

    # Framing guard (BUILD_SPEC.md §0.5): confirm/refute EXPERIMENTAL
    # criteria only -- never a clinical directive for a specific patient.
    lowered = f"{plan.confirm_if} {plan.refute_if} {plan.question}".lower()
    for phrase in ("prescribe", "you should take", "recommended dose", "start the patient on"):
        assert phrase not in lowered, f"clinical-directive language leaked into the experiment framing: {phrase!r}"

    snapshot = brief.run_manifest.get("retrieval_snapshot")
    assert isinstance(snapshot, dict) and snapshot, "run_manifest['retrieval_snapshot'] must be populated"
    assert any(entry.get("abstracts") for entry in snapshot.values()), (
        "expected at least one hypothesis's snapshot entry to carry retrieved abstracts"
    )
    assert brief.run_manifest.get("dataset_pointer") == plan.dataset_pointer
    assert brief.run_manifest.get("selected_experiment_hypothesis_id") == plan.hypothesis_id
    token_spend = brief.run_manifest.get("token_spend")
    assert isinstance(token_spend, dict) and token_spend.get("calls", 0) > 0

    fell_back_to_tabula_sapiens = plan.dataset_pointer == config.DEFAULT_DATASET_POINTER
    print("\n--- Phase 5 flagship experiment plan ---")
    print(f"selected hypothesis: {plan.hypothesis_id}")
    print(f"dataset: {plan.dataset}")
    print(f"dataset_pointer: {plan.dataset_pointer}")
    print(
        f"dataset provenance: {'FELL BACK to Tabula Sapiens (model-named dataset was rejected)' if fell_back_to_tabula_sapiens else 'model-named accession, independently verified'}"
    )
    print(f"method: {plan.method}")
    print("protocol_steps:")
    for i, step in enumerate(plan.protocol_steps, 1):
        print(f"  {i}. {step}")
    print(f"confirm_if: {plan.confirm_if}")
    print(f"refute_if: {plan.refute_if}")
    print(f"feasibility_notes: {plan.feasibility_notes}")
    print(f"claude_science_prompt: {plan.claude_science_prompt}")
    print(f"\nuncertainty_note: {brief.uncertainty_note}")
    print(f"contradictions_surfaced: {brief.contradictions_surfaced}")
    print(f"references: {len(brief.references)}")
    print(f"token_spend: {token_spend}")
