"""test_rigor_entailment_correlation.py — regression guard: ``run_entailment``
must correlate a hypothesis's evidence items back to the LLM's real
entailment verdict using a STABLE id that survives ``reference.pmid is
None`` (ClinicalTrials.gov / DOI-only items), never ``reference.pmid`` alone.

Opus verification found this deterministically: the prior correlation used
``ev.reference.pmid or ""`` on BOTH sides (the prompt block shown to the LLM
and the match-back dict) — since ``schemas.Reference`` has no ``nct_id``/
``doi`` field, every ClinicalTrials.gov/DOI-only evidence item (resolved via
``registry.lookup_id`` in agent 4, commit fbf6235) has ``reference.pmid=
None`` and collapsed onto the exact same ``""`` key. Its entailment could
therefore never be correlated back to the LLM's real verdict and stayed
frozen at the Phase-3 provisional ``"unclear"`` forever, however the model
actually classified it — live consequence: real, on-topic, supporting
NCT-trial evidence was silently demoted to ungrounded, and
``select_experiment_candidate`` returned no candidate at all for the
flagship (H1/H2 both affected). Fixed in ``rigor.py`` via
``_correlation_id`` (``reference.pmid or reference.url``) — see that
function's docstring for the full narrative.

Fully OFFLINE — zero API cost, zero network, zero real LLM call. A fake-LLM
double stands in for the real client (the only method ``agents._ainvoke_json``
ever calls on it is ``await llm.ainvoke(messages)``), so this is a
deterministic, permanent guard against this exact class of bug regressing.
"""
from __future__ import annotations

import asyncio
import json
from typing import Any

from transbench.rigor import run_entailment
from transbench.schemas import EvidenceItem, Hypothesis, Reference


class _FakeAIMessage:
    """Minimal stand-in for a LangChain ``AIMessage`` — only ``.content`` is
    read by ``agents._response_text``."""

    def __init__(self, content: str) -> None:
        self.content = content


class _FakeEntailmentLLM:
    """Fake ``llm`` double matching the ONE method ``agents._ainvoke_json``
    calls (``await llm.ainvoke(messages)``). Records every call's messages
    (so a test can assert what the PROMPT actually showed the model — proof
    the "pmid=None" defect symptom is gone, not merely that the final result
    happens to look right) and returns a pre-scripted STRICT-JSON response
    shaped exactly like a real Haiku response to
    ``RIGOR_ENTAILMENT_SYSTEM_PROMPT``.
    """

    def __init__(self, response_items: list[dict]) -> None:
        self._response_json = json.dumps({"items": response_items})
        self.calls: list[list[Any]] = []

    async def ainvoke(self, messages: list[Any]) -> _FakeAIMessage:
        self.calls.append(messages)
        return _FakeAIMessage(self._response_json)


def _hypothesis() -> Hypothesis:
    return Hypothesis(
        id="h1",
        axis="immune_inflammatory",
        statement="Effector-memory T cells drive RAAS-resistant hypertension.",
        prediction="Depleting effector-memory T cells lowers BP in resistant hypertension.",
        rationale="r",
        priority="high",
    )


_NCT_URL = "https://clinicaltrials.gov/study/NCT01805362"
_CONTROL_PMID = "18772860"


def _nct_item(entailment: str = "unclear") -> EvidenceItem:
    """A ClinicalTrials.gov-resolved item: ``reference.pmid=None`` — exactly
    the shape ``agents.run_grade`` builds via ``registry.lookup_id(nct_id=
    ...)`` (commit fbf6235). This is the class of item the bug could never
    reclassify."""
    return EvidenceItem(
        claim_fragment="RCT of T-cell-targeted therapy in resistant hypertension.",
        reference=Reference(
            source="ClinicalTrials.gov",
            title="A Trial of T-cell Depletion in Resistant Hypertension",
            year=2019,
            url=_NCT_URL,
            pmid=None,
            grade="rct",
        ),
        supports=True,
        entailment=entailment,  # Phase-3 provisional, exactly as agents.run_grade sets it
        grade="rct",
    )


def _pmid_item(entailment: str = "unclear") -> EvidenceItem:
    """A normal PubMed-resolved item (control) — proves the fix doesn't
    regress the already-working pmid path."""
    return EvidenceItem(
        claim_fragment="Observational study of T-cell infiltration in hypertensive kidney.",
        reference=Reference(
            source="PubMed",
            title="T-cell infiltration and hypertension",
            year=2011,
            url=f"https://pubmed.ncbi.nlm.nih.gov/{_CONTROL_PMID}/",
            pmid=_CONTROL_PMID,
            grade="observational",
        ),
        supports=False,
        entailment=entailment,  # Phase-3 provisional
        grade="observational",
    )


def test_run_entailment_overwrites_pmid_none_item_not_frozen_at_unclear() -> None:
    """THE regression proof: a pmid=None, url-bearing (NCT) item's Phase-3
    provisional "unclear" entailment IS overwritten by the LLM's real
    verdict — it must NOT stay frozen at "unclear" the way the pmid-only
    correlation bug caused. A pmid-bearing control item is independently
    graded DIFFERENTLY ("refutes") in the SAME call, proving the two items
    are correlated correctly and independently, not just both defaulting to
    one shared/guessed value.
    """
    nct = _nct_item("unclear")
    pmid = _pmid_item("unclear")
    fake_llm = _FakeEntailmentLLM(
        [
            {"pmid": _NCT_URL, "entailment": "supports"},  # keyed by the URL correlation id
            {"pmid": _CONTROL_PMID, "entailment": "refutes"},  # keyed by the real pmid
        ]
    )

    updated = asyncio.run(run_entailment(_hypothesis(), [nct, pmid], fake_llm))

    assert len(fake_llm.calls) == 1  # ONE batched call, never per-item (BUILD_SPEC.md §6(1))

    by_url = {ev.reference.url: ev for ev in updated}
    nct_result = by_url[_NCT_URL]
    pmid_result = by_url[f"https://pubmed.ncbi.nlm.nih.gov/{_CONTROL_PMID}/"]

    assert nct_result.reference.pmid is None  # still a real NCT-only item, unchanged
    assert nct_result.entailment == "supports", (
        f"pmid=None (NCT) item's entailment must be overwritten by the LLM's real "
        f"verdict, not frozen at the Phase-3 provisional 'unclear' — got "
        f"{nct_result.entailment!r}"
    )
    assert pmid_result.entailment == "refutes", (
        f"pmid-bearing control item must independently reflect its own real verdict, "
        f"got {pmid_result.entailment!r}"
    )


def test_run_entailment_prompt_shows_correlation_id_not_pmid_none() -> None:
    """Proves the DEFECT'S EXACT SYMPTOM is gone at the prompt level, not
    just in the final result: the outgoing prompt must show the NCT item's
    URL as its id (so the LLM has something stable to echo back), and must
    NEVER literally render "pmid=None" for it."""
    nct = _nct_item("unclear")
    fake_llm = _FakeEntailmentLLM([{"pmid": _NCT_URL, "entailment": "supports"}])

    asyncio.run(run_entailment(_hypothesis(), [nct], fake_llm))

    assert len(fake_llm.calls) == 1
    sent_messages = fake_llm.calls[0]
    sent_text = "\n".join(str(getattr(m, "content", "")) for m in sent_messages)
    assert "pmid=None" not in sent_text, (
        "the prompt must never show a bare 'pmid=None' for a pmid-less item "
        "— that is the exact defect symptom this test guards against"
    )
    assert _NCT_URL in sent_text, "the prompt must show the item's URL as its correlation id"


def test_run_entailment_keeps_existing_entailment_when_llm_omits_the_item() -> None:
    """An item the LLM's response doesn't mention at all (its correlation id
    is not present in the model's output) KEEPS its existing entailment
    rather than being silently reset — this pass only RECLASSIFIES evidence
    already known to the caller, it never drops/blanks any (unchanged
    contract; guarded here so the correlation-id refactor didn't regress
    it)."""
    nct = _nct_item("unclear")
    untouched_pmid = _pmid_item("unclear")
    fake_llm = _FakeEntailmentLLM([{"pmid": _NCT_URL, "entailment": "supports"}])  # omits the pmid item entirely

    updated = asyncio.run(run_entailment(_hypothesis(), [nct, untouched_pmid], fake_llm))

    reclassified = next(ev for ev in updated if ev.reference.pmid is None)
    untouched = next(ev for ev in updated if ev.reference.pmid is not None)
    assert reclassified.entailment == "supports"
    assert untouched.entailment == "unclear"  # untouched — kept its prior value, not dropped/reset


def test_run_entailment_empty_input_returns_empty_list_without_calling_llm() -> None:
    """Empty ``evidence_items`` -> ``[]``, and the LLM is never called at all
    (nothing to classify) — BUILD_SPEC.md §9's cost budget assumes exactly
    this: an entailment call is skipped entirely for a hypothesis with zero
    evidence, not wasted on an empty batch."""
    fake_llm = _FakeEntailmentLLM([])

    updated = asyncio.run(run_entailment(_hypothesis(), [], fake_llm))

    assert updated == []
    assert fake_llm.calls == []
