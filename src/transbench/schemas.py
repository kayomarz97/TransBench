"""schemas.py — Pydantic v2 models, verbatim from BUILD_SPEC.md §4.

No field, type, or default has been changed from the spec. If a later phase
needs a new field, edit BUILD_SPEC.md first (orchestrator/Fable-owned), then
mirror the change here — this file must never silently drift from §4.
"""
from __future__ import annotations

from typing import Literal, Optional

from pydantic import BaseModel, Field

Axis = Literal[
    "raas",
    "sympathetic",
    "endothelial_vascular",
    "renal_volume",
    "immune_inflammatory",
    "drug_pk_metabolism",
    "genetic_pharmacogenomic",
    "other",
]
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
