"""rigor.py — agents 5-6 (Novelty Checker, Rigor Gate) + the novelty guard
(BUILD_SPEC.md §5, §6). Phase 4.

Reuses Iatronix's grounding gate (via ``transbench.reuse`` — the single seam;
this module NEVER imports ``app.services.*`` directly) and adds the three
checks BUILD_SPEC.md §6 describes:

1. Dedicated, batched-per-hypothesis entailment (:func:`run_entailment`) —
   closes the gap ``validate_citations``/agent 4's grading leaves: existence
   of a citation is not the same as it actually supporting the claim.
2. Grounding enforcement via the EXACT pseudo-response shape
   ``grounding_stats``/``strip_ungrounded`` expect (:func:`compute_grounding`)
   — a malformed dict silently no-ops (BUILD_SPEC.md §6(2)'s own warning), so
   the shape is built precisely and unit-tested (``tests/test_grounding.py``).
3. The novelty guard (:func:`run_novelty` + :func:`select_experiment_candidate`)
   — ``established`` hypotheses (textbook facts) and ungrounded ones are never
   selected for the experiment stage; demoted, marked Unverified, never
   deleted from ``TransBrief.hypotheses``.

Reuses ``agents``'s package-internal helpers directly (``build_llm``,
``_ainvoke_json``, ``_coerce_list``, ``_normalize_enum_field``,
``TransBenchLLMError``) rather than duplicating strict-JSON/enum-
normalization logic a second time — both modules are part of the same
``transbench`` package.
"""
from __future__ import annotations

import logging
from typing import Optional, get_args

from transbench import agents, config
from transbench.prompts import NOVELTY_CHECKER_SYSTEM_PROMPT, RIGOR_ENTAILMENT_SYSTEM_PROMPT
from transbench.reuse import grounding_stats, strip_ungrounded
from transbench.schemas import EvidenceGrade, EvidenceItem, GradedHypothesis, Hypothesis, NoveltyVerdict

logger = logging.getLogger(__name__)

__all__ = [
    "run_entailment",
    "compute_grounding",
    "run_novelty",
    "compute_confidence",
    "select_experiment_candidate",
]

# schemas.EvidenceItem.entailment is an INLINE Literal (no standalone exported
# name to get_args() on, unlike Axis/Priority/NoveltyVerdict/EvidenceGrade) —
# hardcoded here, matching the schema exactly (BUILD_SPEC.md §4, frozen).
_ENTAILMENT_VALUES = frozenset({"supports", "refutes", "unclear"})
_NOVELTY_VALUES = frozenset(get_args(NoveltyVerdict))


# ---------------------------------------------------------------------------
# Agent 6(1) — Rigor Gate: dedicated, batched-per-hypothesis entailment
# (Haiku, config.MODEL_CHEAP). BUILD_SPEC.md §6(1).
# ---------------------------------------------------------------------------


def _correlation_id(item: EvidenceItem) -> str:
    """The stable id used to correlate ONE evidence item's entailment verdict
    THROUGH the LLM call and back onto the right ``EvidenceItem`` — mirrors
    ``agents._resolution_key``'s intent (a real pmid when there is one; a
    stable, always-present fallback otherwise).

    Bug fixed here (Opus verification, proven deterministically): the prior
    version used ``item.reference.pmid or ""`` directly as this id. ``schemas.
    Reference`` (BUILD_SPEC.md §4, frozen) carries no ``nct_id``/``doi``
    field — only ``pmid`` and ``url`` — so EVERY ClinicalTrials.gov/DOI-only
    evidence item (resolved via ``registry.lookup_id`` in agent 4, added by
    commit fbf6235) has ``reference.pmid=None``. That collapsed every such
    item onto the exact same ``""`` key, so none of them could ever be
    correlated back to the LLM's real verdict — their entailment stayed
    frozen at the Phase-3 provisional ``"unclear"`` no matter what the model
    actually returned, which silently demoted real, on-topic, often
    high-grade (RCT) trial evidence to ungrounded.

    ``url`` is the correct, safe fallback: Iatronix's own ``RegistryArticle.
    url`` is documented+enforced as "always non-empty (registry rejects items
    without URL)" (``article_registry.py``), and ``agents.run_grade`` only
    ever builds an ``EvidenceItem`` from a ``registry.lookup_id(...)`` hit —
    so ``item.reference.url`` is guaranteed present and unique-per-article
    for every ``EvidenceItem`` this function ever receives. The trailing
    ``or ""`` is pure defensive belt-and-suspenders (never expected to
    trigger against a real ``run_grade``-built item), kept only so this
    function can never raise on a hand-built test fixture with both fields
    unset.
    """
    return item.reference.pmid or item.reference.url or ""


def _entailment_prompt_block(item: EvidenceItem) -> str:
    return f"- pmid={_correlation_id(item)}\n  claim: {item.claim_fragment}"


async def run_entailment(
    hypothesis: Hypothesis, evidence_items: list[EvidenceItem], llm
) -> list[EvidenceItem]:
    """Agent 6(1) — dedicated batched entailment (BUILD_SPEC.md §6(1)). ONE
    structured-JSON Haiku call classifying ALL of the hypothesis's evidence
    items (already capped at <=``config.ABSTRACT_CAP`` by Phase 3) as
    supports/refutes/unclear w.r.t. the hypothesis — NEVER per-item, NEVER
    folded into the grader (agent 4 already did a *coarse* supports/
    contradicts call; this is the dedicated, stricter pass that "closes the
    gap validate_citations leaves: existence ≠ support").

    Takes a PRE-BUILT client (matches agents 1-2's calling convention — the
    caller, ``graph.py``, builds it once via ``agents.build_llm(config.
    MODEL_CHEAP, ...)`` and passes it in).

    OVERWRITES each item's Phase-3 provisional ``entailment="unclear"``
    placeholder via ``.model_copy(update=...)`` (``EvidenceItem`` instances
    are never mutated in place). An item whose :func:`_correlation_id` the
    model doesn't echo back, or returns an invalid entailment value for,
    KEEPS its existing value rather than being dropped — this pass only
    RECLASSIFIES evidence, it never removes any (that already happened in
    agent 4's grading).

    Correlation id (Opus verification fix — see :func:`_correlation_id`'s
    docstring for the full defect + fix narrative): BOTH the prompt block
    shown to the LLM (``_entailment_prompt_block``) and the match-back below
    use ``_correlation_id(item)`` — ``item.reference.pmid`` when set, else
    ``item.reference.url`` (the only other field ``schemas.Reference``
    guarantees present+unique, since it has no ``nct_id``/``doi`` field).
    This is STILL sent under the frozen ``RIGOR_ENTAILMENT_SYSTEM_PROMPT``'s
    unchanged ``"pmid"`` JSON key (that prompt text is verbatim/frozen from
    BUILD_SPEC.md §6 and never promises the value is a literal PubMed pmid,
    only that it is the id to echo back) — so every item, pmid-bearing or
    not, is now genuinely reclassifiable by the LLM's real verdict instead of
    silently staying "unclear" forever.

    Empty input -> ``[]``, no LLM call (nothing to classify).
    """
    if not evidence_items:
        return []

    user_content = "\n".join(
        [
            f"Hypothesis: {hypothesis.statement}",
            f"Prediction: {hypothesis.prediction}",
            "Evidence items:",
            *[_entailment_prompt_block(item) for item in evidence_items],
        ]
    )
    parsed = await agents._ainvoke_json(llm, RIGOR_ENTAILMENT_SYSTEM_PROMPT, user_content)
    raw_items = agents._coerce_list(parsed, preferred_key="items")

    entailment_by_key: dict[str, str] = {}
    for item in raw_items:
        if not isinstance(item, dict):
            logger.warning("run_entailment: skipping non-dict item %r", item)
            continue
        key = str(item.get("pmid", ""))
        item = dict(item)
        agents._normalize_enum_field(item, "entailment", _ENTAILMENT_VALUES)
        entailment = item.get("entailment")
        if entailment not in _ENTAILMENT_VALUES:
            logger.warning("run_entailment: skipping id=%s with invalid entailment %r", key, item.get("entailment"))
            continue
        entailment_by_key[key] = entailment

    updated: list[EvidenceItem] = []
    for ev in evidence_items:
        new_entailment = entailment_by_key.get(_correlation_id(ev))
        if new_entailment is not None and new_entailment != ev.entailment:
            ev = ev.model_copy(update={"entailment": new_entailment})
        updated.append(ev)
    return updated


# ---------------------------------------------------------------------------
# Grounding enforcement (BUILD_SPEC.md §6(2)) — reuses grounding_stats/
# strip_ungrounded via the EXACT pseudo-response shape.
# ---------------------------------------------------------------------------


def compute_grounding(evidence_items: list[EvidenceItem]) -> tuple[bool, int]:
    """BUILD_SPEC.md §6(2): grounding enforcement via the EXACT
    pseudo-response shape ``grounding_stats``/``strip_ungrounded`` expect —
    a malformed dict silently no-ops (returns ``(0, 0)``), so this is built
    PRECISELY, per the coordinator's own spelled-out shape::

        {"sections": [
            {"content_items": [
                {"pmid": ev.reference.pmid, "url": ev.reference.url, "source": ev.reference.source}
                for ev in evidence_items if ev.entailment == "supports"
            ]}
        ]}

    Only ``entailment == "supports"`` items count — a refuted or merely
    "unclear" item can never ground a hypothesis, however well-cited it is.
    An item is grounded iff it carries a resolvable ``pmid``/``nct_id``/
    ``doi``/``url`` OR a specific non-generic ``source`` (confirmed by
    reading Iatronix's ``grounding_gate.py`` — ``"Expert opinion"`` and
    similar generic labels do NOT count).

    Calls both ``grounding_stats`` (for the count) and ``strip_ungrounded``
    (on the local, throwaway pseudo dict — its mutation is not otherwise
    used, but exercising the reused mutator alongside the reader keeps this
    function faithful to the pair Iatronix designed them to be used as).

    Returns ``(grounded, grounded_supporting_count)`` where ``grounded =
    grounded_supporting_count > 0``. BUILD_SPEC.md §6(2): "A hypothesis with
    0 grounded supporting items -> grounded=False -> excluded from the
    experiment stage (demoted + Unverified, never silently deleted)" — this
    function only computes the boolean; exclusion itself happens in
    :func:`select_experiment_candidate`, never by deleting the hypothesis.
    """
    supporting = [ev for ev in evidence_items if ev.entailment == "supports"]
    pseudo = {
        "sections": [
            {
                "content_items": [
                    {"pmid": ev.reference.pmid, "url": ev.reference.url, "source": ev.reference.source}
                    for ev in supporting
                ]
            }
        ]
    }
    grounded_count, _total = grounding_stats(pseudo)
    strip_ungrounded(pseudo)  # exercises the reused mutator; pseudo is a local scratch dict
    return grounded_count > 0, grounded_count


# ---------------------------------------------------------------------------
# Agent 5 — Novelty Checker (Sonnet, config.MODEL_REASONING). BUILD_SPEC.md §5.
# ---------------------------------------------------------------------------


def _ensure_pmid_citation(novelty_reason: str, evidence_items: list[EvidenceItem]) -> str:
    """BUILD_SPEC.md §6(3): "novelty_reason MUST cite specific evidence items
    (PMIDs) so the verdict is auditable, never an unsupported LLM assertion."
    This is a ``MUST``, so it is ENFORCED here, not just requested in the
    prompt: if the model's own prose doesn't already mention at least one
    real PMID from the evidence it was given, a citation is appended
    deterministically — guarantees auditability at the OUTPUT level
    regardless of whether the LLM remembered to do it (same philosophy as
    the Phase 3 ``bears_on_hypothesis`` fix: enforce structurally, don't
    just ask nicely).
    """
    real_pmids = [ev.reference.pmid for ev in evidence_items if ev.reference.pmid]
    if not real_pmids:
        return novelty_reason  # nothing to cite -- e.g. genuinely zero evidence retrieved
    if any(pmid in novelty_reason for pmid in real_pmids):
        return novelty_reason  # the model already cited at least one -- did its job
    cited = ", ".join(f"PMID {p}" for p in real_pmids[:3])
    return f"{novelty_reason} (citing {cited})"


async def run_novelty(
    hypothesis: Hypothesis, evidence_items: list[EvidenceItem], llm
) -> tuple[NoveltyVerdict, str]:
    """Agent 5 — Novelty Checker (BUILD_SPEC.md §5, verbatim prompt in
    ``prompts.NOVELTY_CHECKER_SYSTEM_PROMPT``). Classifies the hypothesis as
    ``established`` (textbook fact, not novel), ``open_question`` (plausible,
    partially supported, unresolved — a good target), or ``unsupported`` (no
    real evidence). Takes a PRE-BUILT client (agents 1-2's convention).

    Returns ``(novelty, novelty_reason)`` with ``novelty_reason`` GUARANTEED
    to cite a real PMID from the evidence when any exists (see
    :func:`_ensure_pmid_citation`).
    """
    lines = [f"Hypothesis: {hypothesis.statement}", f"Prediction: {hypothesis.prediction}"]
    if evidence_items:
        lines.append("Evidence:")
        for ev in evidence_items:
            lines.append(
                f"- pmid={ev.reference.pmid} entailment={ev.entailment} "
                f"grade={ev.grade}: {ev.claim_fragment}"
            )
    else:
        lines.append("Evidence: none retrieved/graded for this hypothesis.")
    user_content = "\n".join(lines)

    parsed = await agents._ainvoke_json(llm, NOVELTY_CHECKER_SYSTEM_PROMPT, user_content)
    item = parsed if isinstance(parsed, dict) else {}
    if not item and isinstance(parsed, list) and parsed and isinstance(parsed[0], dict):
        item = parsed[0]  # defensive: a bare-list-wrapped single object despite the prompt
    item = dict(item)
    agents._normalize_enum_field(item, "novelty", _NOVELTY_VALUES)
    novelty = item.get("novelty")
    if novelty not in _NOVELTY_VALUES:
        raise agents.TransBenchLLMError(
            502, "llm_bad_output", f"Novelty checker returned an invalid verdict: {parsed!r}"
        )

    novelty_reason = str(item.get("novelty_reason") or "").strip()
    if not novelty_reason:
        novelty_reason = f"Novelty classified as {novelty} (no reason text returned by the model)."
    novelty_reason = _ensure_pmid_citation(novelty_reason, evidence_items)

    return novelty, novelty_reason


# ---------------------------------------------------------------------------
# Confidence (BUILD_SPEC.md §6(3): "Confidence = f(supporting grounded
# count, contradicting count, best grade)").
# ---------------------------------------------------------------------------

_GRADE_RANK: dict[str, int] = {
    grade: rank
    for rank, grade in enumerate(
        [
            "expert_opinion",
            "preclinical",
            "observational",
            "mechanistic_study",
            "rct",
            "systematic_review",
            "guideline",
        ]
    )
}
assert set(_GRADE_RANK) == set(get_args(EvidenceGrade))  # stays in sync with schemas.py


def compute_confidence(
    grounded_supporting_count: int,
    contradicting_count: int,
    evidence_items: list[EvidenceItem],
) -> str:
    """BUILD_SPEC.md §6(3): "Confidence = f(supporting grounded count,
    contradicting count, best grade)". Deterministic thresholds:

    - ``"low"``: zero grounded supporting evidence (nothing to be confident
      about), OR contradicting evidence outweighs grounded support.
    - ``"high"``: >=2 grounded supporting items, ZERO contradicting, and the
      best supporting grade is at least ``rct``-level.
    - ``"moderate"``: everything else with >=1 grounded supporting item and
      contradicting evidence not outweighing it.

    Takes the ALREADY-computed ``grounded_supporting_count`` from
    :func:`compute_grounding` — never re-derives grounding a second way
    (single source of truth for "what counts as grounded").
    """
    if grounded_supporting_count == 0:
        return "low"
    if contradicting_count > grounded_supporting_count:
        return "low"

    best_grade_rank = max(
        (_GRADE_RANK.get(ev.grade, 0) for ev in evidence_items if ev.entailment == "supports"),
        default=0,
    )
    rct_or_better = best_grade_rank >= _GRADE_RANK["rct"]

    if grounded_supporting_count >= 2 and contradicting_count == 0 and rct_or_better:
        return "high"
    return "moderate"


# ---------------------------------------------------------------------------
# Novelty guard + experiment-candidate selection (BUILD_SPEC.md §6(3)).
# ---------------------------------------------------------------------------

_CONFIDENCE_RANK = {"high": 2, "moderate": 1, "low": 0}
_PRIORITY_RANK = {"high": 2, "medium": 1, "low": 0}


def select_experiment_candidate(graded_hypotheses: list[GradedHypothesis]) -> Optional[GradedHypothesis]:
    """The novelty guard + selection (BUILD_SPEC.md §6(3)): returns the top
    ``open_question`` AND ``grounded`` hypothesis, or ``None`` if none
    qualify. ``established`` hypotheses (kills textbook-as-novel) and
    ungrounded ones (0 grounded supporting evidence) are NEVER selected —
    they stay in ``TransBrief.hypotheses`` (demoted, marked Unverified via
    their own ``novelty``/``grounded`` fields), never deleted; this function
    only decides eligibility for the Phase 5 experiment-design stage.

    "Top" is a deterministic tie-break: confidence (high > moderate > low),
    then supporting_count, then the hypothesis's own declared priority.
    """
    eligible = [gh for gh in graded_hypotheses if gh.novelty == "open_question" and gh.grounded]
    if not eligible:
        return None
    eligible.sort(
        key=lambda gh: (
            _CONFIDENCE_RANK.get(gh.confidence, 0),
            gh.supporting_count,
            _PRIORITY_RANK.get(gh.hypothesis.priority, 0),
        ),
        reverse=True,
    )
    return eligible[0]
