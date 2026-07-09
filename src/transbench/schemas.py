"""schemas.py — Pydantic v2 models, from BUILD_SPEC.md §4.

Every field/type/default here is verbatim from the spec EXCEPT ``Axis``
(flagged, not silent — see its own comment below): domain-universalization
(this task) replaced the original 8-value hypertension-specific ``Literal``
with a free-form, normalized string, because BUILD_SPEC.md §0's own tool
scope was widened from "an observation about antihypertensive drugs" to any
clinical/biomedical observation, and a fixed axis taxonomy authored for one
disease area cannot describe another's mechanism (e.g. an oncology
observation has no legitimate use for ``raas``/``renal_volume``). Every
other model — ``Priority``/``NoveltyVerdict``/``EvidenceGrade``, and every
field on ``TransRequest``/``Reference``/``EvidenceItem``/``GradedHypothesis``/
``ExperimentPlan``/``TransBrief`` — is unchanged and still must never
silently drift from §4; if a later phase needs another new field, edit
BUILD_SPEC.md first (orchestrator/Fable-owned), then mirror the change here.
"""
from __future__ import annotations

import re
from typing import Annotated, Literal, Optional

from pydantic import AfterValidator, BaseModel, Field

_AXIS_SEPARATOR_RE = re.compile(r"[^a-z0-9]+")


def normalize_axis(value: str) -> str:
    """Domain-universal ``Axis`` normalization: lowercase, strip, and
    collapse any run of non-alphanumeric characters (spaces, ``/``, ``+``,
    ``-``, ``&``, ...) into a single ``_``, with leading/trailing ``_``
    stripped too (e.g. ``"Immune / Inflammatory"`` -> ``"immune_inflammatory"``,
    ``"B-cell exhaustion"`` -> ``"b_cell_exhaustion"``). ``Axis`` is a
    free-form, descriptive biological-axis label — there is no fixed
    taxonomy to validate membership against (any clinical/biomedical domain
    names its own relevant axes; axes are never load-bearing for grounding
    or hypothesis selection, only for readability/rationale) — so THIS
    normalization is the field's entire validation: it must be non-empty
    once normalized. Raises ``ValueError`` (Pydantic wraps this into a
    normal ``ValidationError``, same as any other failed field validator)
    if nothing alphanumeric survives, e.g. an all-punctuation input.
    """
    normalized = _AXIS_SEPARATOR_RE.sub("_", (value or "").strip().lower()).strip("_")
    if not normalized:
        raise ValueError("axis must be a non-empty, normalizable label")
    return normalized


# Free-form, lowercase-snake_case biological-axis label (validated non-empty
# via `normalize_axis` above) — was a fixed 8-value hypertension-specific
# `Literal` (raas/sympathetic/endothelial_vascular/renal_volume/
# immune_inflammatory/drug_pk_metabolism/genetic_pharmacogenomic/other);
# widened for domain-universality (see module docstring). `DecomposedAxis.axis`
# and `Hypothesis.axis` both use this same `Axis` alias, so both get the
# identical normalization automatically, with no per-class validator needed.
Axis = Annotated[str, AfterValidator(normalize_axis)]
Priority = Literal["high", "medium", "low"]
NoveltyVerdict = Literal["established", "open_question", "unsupported"]
EvidenceGrade = Literal[
    "guideline",
    "systematic_review",
    "rct",
    "mechanistic_study",
    "observational",
    "preclinical",
    "expert_opinion",
]


class TransRequest(BaseModel):
    observation: str = Field(min_length=3, max_length=8000)  # free text; FUTURE: full patient history
    focus_drug: Optional[str] = None
    max_hypotheses: int = 3
    user_key: str
    user_provider: Optional[str] = "anthropic"  # pass explicitly so routing never falls to Cerebras
    model_reasoning: str = "claude-sonnet-4-6"  # registry-known Anthropic id (hypothesize/novelty/design)
    model_cheap: str = "claude-haiku-4-5-20251001"  # registry-known Anthropic id (decompose/grade/entail/assemble)
    retrieval_snapshot: Optional[dict] = None  # when set, replay retrieval from PMIDs+abstracts (reproducible reruns)


class Reference(BaseModel):
    source: str
    title: Optional[str] = None
    year: Optional[int] = None
    url: Optional[str] = None
    pmid: Optional[str] = None
    grade: Optional[EvidenceGrade] = None


class DecomposedAxis(BaseModel):
    axis: Axis
    rationale: str
    key_entities: list[str] = []


class Hypothesis(BaseModel):
    id: str
    axis: Axis
    statement: str
    prediction: str
    rationale: str
    priority: Priority


class EvidenceItem(BaseModel):
    claim_fragment: str
    reference: Reference
    supports: bool
    entailment: Literal["supports", "refutes", "unclear"]
    grade: EvidenceGrade


class GradedHypothesis(BaseModel):
    hypothesis: Hypothesis
    evidence: list[EvidenceItem]
    supporting_count: int
    contradicting_count: int
    novelty: NoveltyVerdict
    novelty_reason: str
    confidence: Literal["low", "moderate", "high"]
    grounded: bool


class ExperimentPlan(BaseModel):
    hypothesis_id: str
    question: str
    dataset: str
    dataset_pointer: Optional[str] = None
    method: str
    protocol_steps: list[str]
    confirm_if: str
    refute_if: str
    feasibility_notes: str
    claude_science_prompt: str


class TransBrief(BaseModel):
    request_echo: str
    axes: list[DecomposedAxis]
    hypotheses: list[GradedHypothesis]
    top_experiment: ExperimentPlan
    references: list[Reference]
    contradictions_surfaced: list[str]
    uncertainty_note: str
    run_manifest: dict
    disclaimer: str = "Research hypothesis generation only. Not clinical, diagnostic, or prescribing advice."
