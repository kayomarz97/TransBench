"""tests/test_enum_salvage_and_partial_failure.py — FINAL-GATE DETERMINISM
regression tests (Opus verification finding, post-Phase-7).

Fully OFFLINE / deterministic (zero API cost, zero network, no
``ANTHROPIC_API_KEY`` required) — proves the fix for a real failing-run
defect: the hypothesis generator returned COMPOUND axis labels (e.g.
``"drug_pk_metabolism × genetic_pharmacogenomic"``,
``"immune_inflammatory × renal_volume"``); ``agents._normalize_enum_field``
could not map ``"A × B"`` onto a single ``Axis`` Literal, Pydantic rejected
all 3 hypotheses, and the run raised a spurious
``TransBenchLLMError [502 llm_bad_output]`` for an otherwise-perfectly-good
run.

1. Direct ``agents._normalize_enum_field`` coverage: compound labels across
   every documented separator (×, x, comma, slash, semicolon, pipe, "and")
   salvage to the FIRST token that IS a valid schema member — not merely
   the first token unconditionally (an invalid leading token is skipped in
   favor of the next one that IS valid); genuinely-invalid values (a bare
   single value, or a fully-unsalvageable compound where NO token is
   valid) are left completely UNCHANGED, so they still fail Pydantic's own
   validation loudly (strictness against real garbage is preserved);
   existing simple-casing normalization (``"HIGH"`` -> ``"high"``) is
   unaffected (non-regression). Also exercises the priority/grade fields to
   demonstrate the fix benefits every enum-valued agent output uniformly —
   it is ONE shared helper, reused by decompose/hypothesize/grade/
   rigor.run_novelty/rigor.run_entailment.

2. ``run_hypothesize``/``run_decompose`` end-to-end against a fake LLM
   double (fully offline; matches the established minimal-double pattern
   already used in ``tests/test_experiment_phase5.py``/``tests/test_cost.py``
   — only ``await llm.ainvoke(messages)`` is ever called by
   ``agents._ainvoke_json``): a response where EVERY item carries a
   compound axis label survives entirely (no 502) — this is the exact real
   failing-run shape; a response with a MIX of salvageable and genuinely
   -unsalvageable items drops only the bad one(s) and keeps the rest (the
   pre-existing "drop invalid, keep valid" per-item loop in
   ``run_hypothesize``/``run_decompose`` — unaffected by this fix, proven
   here to compose correctly with the new salvage logic); a response where
   NOTHING is salvageable still raises ``TransBenchLLMError`` (502,
   ``llm_bad_output``) — the zero-valid-items case must still fail loudly,
   never silently succeed with an empty/fabricated result.
"""
from __future__ import annotations

import asyncio
import json
from typing import Any

import pytest
from pydantic import ValidationError

from transbench.agents import (
    _AXIS_VALUES,
    _GRADE_VALUES,
    _PRIORITY_VALUES,
    _normalize_enum_field,
    TransBenchLLMError,
    run_decompose,
    run_hypothesize,
)
from transbench.schemas import Hypothesis

# ---------------------------------------------------------------------------
# Shared fakes (fully offline — no network, no real Anthropic call). Mirrors
# the minimal-double pattern already established in
# tests/test_experiment_phase5.py / tests/test_cost.py — only the ONE method
# agents._ainvoke_json actually calls (`await llm.ainvoke(messages)`).
# ---------------------------------------------------------------------------


class _FakeAIMessage:
    """Minimal stand-in for a LangChain ``AIMessage`` — only ``.content`` is
    read by ``agents._response_text``."""

    def __init__(self, content: str) -> None:
        self.content = content


class _FixedJSONLLM:
    """Fake ``llm`` double returning the SAME fixed JSON payload for every
    call. Records every call's messages (unused directly by these tests,
    kept for parity/debuggability with the rest of the suite's fakes)."""

    def __init__(self, payload: Any) -> None:
        self._content = json.dumps(payload)
        self.calls: list[list[Any]] = []

    async def ainvoke(self, messages: list[Any]) -> _FakeAIMessage:
        self.calls.append(messages)
        return _FakeAIMessage(self._content)


def _hyp_item(hyp_id: str, axis: str, priority: str = "high") -> dict:
    return {
        "id": hyp_id,
        "axis": axis,
        "statement": f"Synthetic statement for {hyp_id}.",
        "prediction": f"Synthetic falsifiable prediction for {hyp_id}.",
        "rationale": f"Synthetic rationale for {hyp_id}.",
        "priority": priority,
    }


# ---------------------------------------------------------------------------
# 1. Direct _normalize_enum_field coverage.
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "raw_axis,expected",
    [
        # The exact two compound labels observed on the real failing run.
        ("drug_pk_metabolism × genetic_pharmacogenomic", "drug_pk_metabolism"),
        ("immune_inflammatory × renal_volume", "immune_inflammatory"),
        # Every other documented separator.
        ("raas x sympathetic", "raas"),
        ("RAAS X SYMPATHETIC", "raas"),
        ("renal_volume, immune_inflammatory", "renal_volume"),
        ("renal_volume/immune_inflammatory", "renal_volume"),
        ("renal_volume; immune_inflammatory", "renal_volume"),
        ("renal_volume | immune_inflammatory", "renal_volume"),
        ("renal_volume and immune_inflammatory", "renal_volume"),
        # First token is INVALID -- must skip to the next, genuinely-valid one
        # (proves "first token that IS a valid member", not "first token").
        ("cardiac × renal_volume", "renal_volume"),
        ("cardiac, other, sympathetic", "other"),
    ],
)
def test_normalize_enum_field_salvages_compound_axis_label(raw_axis: str, expected: str) -> None:
    item = {"axis": raw_axis}
    _normalize_enum_field(item, "axis", _AXIS_VALUES)
    assert item["axis"] == expected


def test_normalize_enum_field_leaves_genuinely_invalid_single_value_unchanged() -> None:
    """A single, non-compound, genuinely-invalid value (e.g. "cardiac" —
    not one of the 8 Axis members) is left completely untouched -- it must
    still fail Pydantic's own validation loudly, never be silently coerced
    to a plausible-looking guess."""
    item = {"axis": "cardiac"}
    _normalize_enum_field(item, "axis", _AXIS_VALUES)
    assert item["axis"] == "cardiac"

    with pytest.raises(ValidationError):
        Hypothesis(
            id="h1",
            axis=item["axis"],
            statement="s",
            prediction="p",
            rationale="r",
            priority="high",
        )


def test_normalize_enum_field_leaves_fully_unsalvageable_compound_value_unchanged() -> None:
    """A compound value where NEITHER component is a valid member is also
    left completely unchanged (no partial/garbage mutation) -- still fails
    Pydantic loudly."""
    item = {"axis": "cardiac × pulmonary"}
    _normalize_enum_field(item, "axis", _AXIS_VALUES)
    assert item["axis"] == "cardiac × pulmonary"

    with pytest.raises(ValidationError):
        Hypothesis(
            id="h1",
            axis=item["axis"],
            statement="s",
            prediction="p",
            rationale="r",
            priority="high",
        )


def test_normalize_enum_field_still_normalizes_simple_casing() -> None:
    """Non-regression: the original Phase-2 exact-case-insensitive-match
    behavior (e.g. Sonnet's observed ``"priority": "HIGH"``) still works
    unchanged after the compound-label salvage pass was added."""
    item = {"priority": "HIGH"}
    _normalize_enum_field(item, "priority", _PRIORITY_VALUES)
    assert item["priority"] == "high"

    item2 = {"axis": "Immune_Inflammatory"}
    _normalize_enum_field(item2, "axis", _AXIS_VALUES)
    assert item2["axis"] == "immune_inflammatory"


def test_normalize_enum_field_salvages_compound_priority_and_grade_labels() -> None:
    """The fix is ONE shared helper -- proves it benefits every enum-valued
    agent output uniformly (axis/priority/grade/novelty/entailment), not
    just the axis field the real failing run happened to hit."""
    priority_item = {"priority": "high, medium"}
    _normalize_enum_field(priority_item, "priority", _PRIORITY_VALUES)
    assert priority_item["priority"] == "high"

    grade_item = {"grade": "rct / observational"}
    _normalize_enum_field(grade_item, "grade", _GRADE_VALUES)
    assert grade_item["grade"] == "rct"


# ---------------------------------------------------------------------------
# 2a. run_hypothesize end to end (fake LLM, offline) -- compound-axis salvage.
# ---------------------------------------------------------------------------


def test_run_hypothesize_survives_all_compound_axis_labels() -> None:
    """The exact real failing-run shape: EVERY generated hypothesis carries
    a compound axis label. Before the fix, this zeroed out to 0 valid
    hypotheses and raised a spurious 502. After the fix, all 3 survive,
    each normalized to the FIRST valid component of its own compound
    label."""
    payload = {"observation": "58F, resistant hypertension; elevated hs-CRP."}
    items = [
        _hyp_item("h1", "drug_pk_metabolism × genetic_pharmacogenomic"),
        _hyp_item("h2", "immune_inflammatory × renal_volume"),
        _hyp_item("h3", "raas x sympathetic"),
    ]
    fake_llm = _FixedJSONLLM({"hypotheses": items})

    result = asyncio.run(run_hypothesize(payload, fake_llm))

    assert len(result) == 3, f"expected all 3 compound-axis hypotheses to survive, got {len(result)}"
    by_id = {h.id: h for h in result}
    assert by_id.keys() == {"h1", "h2", "h3"}
    assert by_id["h1"].axis == "drug_pk_metabolism"
    assert by_id["h2"].axis == "immune_inflammatory"
    assert by_id["h3"].axis == "raas"


def test_run_hypothesize_drops_unsalvageable_item_keeps_valid_ones() -> None:
    """Partial-failure resilience (FIX 1(b)): a fake LLM returns 3
    hypotheses where 1 is genuinely unsalvageable (axis="cardiac", no valid
    component at all) -- the 2 valid ones survive, the bad one is dropped,
    and no 502 is raised."""
    payload = {"observation": "obs"}
    items = [
        _hyp_item("h1", "drug_pk_metabolism × genetic_pharmacogenomic"),  # salvageable
        _hyp_item("h2", "cardiac"),  # genuinely unsalvageable -- must be dropped
        _hyp_item("h3", "immune_inflammatory"),  # already valid, untouched
    ]
    fake_llm = _FixedJSONLLM({"hypotheses": items})

    result = asyncio.run(run_hypothesize(payload, fake_llm))

    by_id = {h.id: h for h in result}
    assert by_id.keys() == {"h1", "h3"}, f"expected exactly h1+h3 to survive (h2 dropped), got {by_id.keys()}"
    assert by_id["h1"].axis == "drug_pk_metabolism"
    assert by_id["h3"].axis == "immune_inflammatory"


def test_run_hypothesize_all_unsalvageable_still_raises_502() -> None:
    """The zero-valid-items case must still fail loudly (as before this
    fix) -- never silently return an empty/fabricated result."""
    payload = {"observation": "obs"}
    items = [
        _hyp_item("h1", "cardiac"),
        _hyp_item("h2", "totally_bogus_axis"),
        _hyp_item("h3", "cardiac × pulmonary"),  # compound but neither side valid
    ]
    fake_llm = _FixedJSONLLM({"hypotheses": items})

    with pytest.raises(TransBenchLLMError) as exc_info:
        asyncio.run(run_hypothesize(payload, fake_llm))

    assert exc_info.value.status_code == 502
    assert exc_info.value.error == "llm_bad_output"


# ---------------------------------------------------------------------------
# 2b. run_decompose end to end (fake LLM, offline) -- same shared helper,
#     second real call site (proves the "uniform" benefit end to end, not
#     just via the direct-helper tests above).
# ---------------------------------------------------------------------------


def test_run_decompose_survives_compound_axis_label() -> None:
    payload = {"observation": "58F, resistant hypertension; elevated hs-CRP."}
    fake_llm = _FixedJSONLLM(
        {
            "axes": [
                {
                    "axis": "immune_inflammatory × renal_volume",
                    "rationale": "Elevated hs-CRP and resistant hypertension both implicate this axis.",
                    "key_entities": ["CRP"],
                },
                {"axis": "raas", "rationale": "Poor response to RAAS blockade.", "key_entities": []},
            ]
        }
    )

    result = asyncio.run(run_decompose(payload, fake_llm))

    assert len(result) == 2
    axis_values = {a.axis for a in result}
    assert axis_values == {"immune_inflammatory", "raas"}


def test_run_decompose_all_unsalvageable_still_raises_502() -> None:
    payload = {"observation": "obs"}
    fake_llm = _FixedJSONLLM(
        {"axes": [{"axis": "cardiac", "rationale": "r", "key_entities": []}]}
    )

    with pytest.raises(TransBenchLLMError) as exc_info:
        asyncio.run(run_decompose(payload, fake_llm))

    assert exc_info.value.status_code == 502
    assert exc_info.value.error == "llm_bad_output"
