"""tests/test_enum_salvage_and_partial_failure.py — FINAL-GATE DETERMINISM
regression tests (Opus verification finding, post-Phase-7, TWO rounds),
RETARGETED for Phase 8 (domain-universalization).

Fully OFFLINE / deterministic (zero API cost, zero network, no
``ANTHROPIC_API_KEY`` required) — proves the fix for a real failing-run
defect that recurred TWICE with two DIFFERENT separators: round 1, the
hypothesis generator returned COMPOUND labels for a FIXED-enum field joined
with ``×`` (e.g. ``"drug_pk_metabolism × genetic_pharmacogenomic"``,
``"immune_inflammatory × renal_volume"`` — at the time, axis values); round
2 showed the model joining values with a plain ``"+"`` instead (e.g.
``"renal_volume + immune_inflammatory"``) — a separator the round-1
whitelist did not cover, reproducing the exact same 502
(``agents._normalize_enum_field`` could not map the compound label onto a
single schema ``Literal``, Pydantic rejected the whole item). The round-2
fix replaced the whitelist with a SEPARATOR-AGNOSTIC split on ANY run of
non-word characters (``\\W+``) — every FIXED schema ``Literal`` in this
codebase is composed only of ``[a-z_]`` (word characters), so this can never
again fall one separator behind whatever the model picks next.

**Phase 8 retargeting (domain-universalization) — read this first:**
``schemas.Axis`` is no longer one of those fixed ``Literal`` types (it is
now a free-form, normalized string — any clinical/biomedical domain names
its own relevant axes; see ``schemas.py``'s own docstring) — so it no longer
goes through ``_normalize_enum_field``'s fixed-membership salvage at all
(``agents._normalize_axis_field`` handles it instead: lowercase/strip/
separators->``_``, MERGING a compound label into one token rather than
salvaging its first valid component, since there is no longer a fixed
component set to salvage against). Every test below that used to exercise
this machinery via ``axis`` has been RETARGETED onto ``grade``/``priority``
(still real, unchanged, fixed ``Literal`` types — ``EvidenceGrade``/
``Priority``) so the ORIGINAL regression proof (compound-label salvage for a
fixed enum, separator-agnostic) is fully preserved, just no longer via the
one field that stopped being a fixed enum. A NEW section (1b) directly
covers ``_normalize_axis_field``'s own (different-by-design: merge, not
salvage-first) behavior, and sections 2a/2b's end-to-end
``run_hypothesize``/``run_decompose`` coverage is updated to match (a
compound/oddly-punctuated axis label can no longer by itself zero out a
batch or 502 — it always normalizes to SOME non-empty merged token — so
those end-to-end "all unsalvageable -> 502" proofs are retargeted onto
priority, the field that can still genuinely fail).

1. Direct ``agents._normalize_enum_field`` coverage (retargeted onto
   ``grade``, structurally the closest stand-in for the original axis
   members — multi-word snake_case ``EvidenceGrade`` values): compound
   labels across a broad range of separators — including the two that
   ACTUALLY broke a real run (``×``, ``+``) and several the whitelist never
   explicitly named (``&``, ``-``, en dash, newline, multiple spaces, and a
   parametrized sweep of separators picked specifically because nothing in
   this codebase has hardcoded them) — salvage to the FIRST token that IS a
   valid schema member, not merely the first token unconditionally;
   genuinely-invalid values are left completely UNCHANGED, so they still
   fail Pydantic's own validation loudly; existing simple-casing
   normalization is unaffected. Also exercises priority directly, and both
   priority+grade together, to demonstrate the fix benefits every REMAINING
   fixed-enum-valued agent output uniformly.

1b. Direct ``agents._normalize_axis_field`` coverage (NEW, Phase 8): proves
    the free-form axis normalizer's own contract — lowercase/strip/
    separator collapse, MERGES a compound label into one token (never
    salvages-to-first, since axis has no fixed component set), and leaves a
    value that normalizes to NOTHING (e.g. all-punctuation) completely
    unchanged so Pydantic still fails loudly on it.

2. ``run_hypothesize``/``run_decompose`` end to end (fake LLM double, fully
   offline): a response where every hypothesis carries a compound/oddly
   -punctuated AXIS label survives entirely under the new free-form
   normalizer (merged, never a 502) — reproducing both real historical
   separators (× and +); a response with a genuinely-unsalvageable PRIORITY
   (the field that can still fail) drops only that item and keeps the rest;
   a response where EVERY item's priority is unsalvageable still raises
   ``TransBenchLLMError`` (502, ``llm_bad_output``) — the zero-valid-items
   case must still fail loudly. ``run_decompose``'s new ``condition_anchor``
   extraction (Phase 8) is also covered directly.
"""
from __future__ import annotations

import asyncio
import json
from typing import Any

import pytest
from pydantic import ValidationError

from transbench.agents import (
    _GRADE_VALUES,
    _PRIORITY_VALUES,
    _normalize_axis_field,
    _normalize_enum_field,
    TransBenchLLMError,
    run_decompose,
    run_hypothesize,
)
from transbench.schemas import DecomposedAxis, EvidenceItem, Reference

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
# 1. Direct _normalize_enum_field coverage — retargeted onto `grade`
#    (EvidenceGrade), the still-fixed enum structurally closest to the
#    original axis members (multi-word snake_case labels).
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "raw_grade,expected",
    [
        # The two compound-label shapes that ACTUALLY broke a real run
        # (round 1 and round 2 respectively, back when axis was the field
        # affected) -- reproduced here against `grade` instead.
        ("mechanistic_study × systematic_review", "mechanistic_study"),
        ("systematic_review × rct", "systematic_review"),
        ("observational + mechanistic_study", "observational"),
        # Separators the round-1 whitelist never named (proves round 2 is
        # NOT just "the round-1 list plus one more special case for +").
        ("observational & mechanistic_study", "observational"),
        ("observational-mechanistic_study", "observational"),
        ("observational–mechanistic_study", "observational"),  # en dash
        ("observational\nmechanistic_study", "observational"),  # newline
        ("observational   mechanistic_study", "observational"),  # multiple spaces
        # Round-1-whitelisted separators, kept as a non-regression check.
        ("rct x preclinical", "rct"),
        ("RCT X PRECLINICAL", "rct"),
        ("observational, mechanistic_study", "observational"),
        ("observational/mechanistic_study", "observational"),
        ("observational; mechanistic_study", "observational"),
        ("observational | mechanistic_study", "observational"),
        ("observational and mechanistic_study", "observational"),
        # First token is INVALID -- must skip to the next, genuinely-valid
        # one (proves "first token that IS a valid member", not "first
        # token").
        ("cardiology × observational", "observational"),
        ("cardiology + observational", "observational"),
        ("cardiology, other, rct", "rct"),
    ],
)
def test_normalize_enum_field_salvages_compound_grade_label(raw_grade: str, expected: str) -> None:
    item = {"grade": raw_grade}
    _normalize_enum_field(item, "grade", _GRADE_VALUES)
    assert item["grade"] == expected


# Separators picked specifically because NOTHING in this codebase's fix (or
# in the round-1 whitelist it replaced) ever named them -- the whole point
# is to prove the \W+ split is genuinely separator-AGNOSTIC, not just a
# slightly-longer enumeration that happens to cover every separator seen so
# far. If this class of bug recurs a third time with yet another separator,
# THIS test (not just the two real-shape cases above) must already cover it.
_UNSEEN_NON_WORD_SEPARATORS = [
    "+", "&", "-", "–", "—", "\n", "\t", "   ", "@", "#", "~", ":",
    "!", "(", ")", "•", "=", "%", "$", "^", "*",
]


@pytest.mark.parametrize("separator", _UNSEEN_NON_WORD_SEPARATORS)
def test_normalize_enum_field_salvages_across_arbitrary_non_word_separators(separator: str) -> None:
    """The generality proof (Opus round-2 finding): ``_normalize_enum_field``
    must salvage a compound label joined by ANY run of non-word characters,
    not merely the specific separators a prior real failing run happened to
    use. Directly exercises the ``\\W+``-split mechanism (against `grade`,
    still a real fixed enum post-Phase-8) across separators this codebase
    has never hardcoded anywhere, so this class of bug cannot quietly
    regress the next time the model picks yet another one."""
    raw = f"observational{separator}mechanistic_study"
    item = {"grade": raw}
    _normalize_enum_field(item, "grade", _GRADE_VALUES)
    assert item["grade"] == "observational", f"separator {separator!r} was not salvaged: got {item['grade']!r}"


def test_normalize_enum_field_leaves_genuinely_invalid_single_value_unchanged() -> None:
    """A single, non-compound, genuinely-invalid value (e.g. "bogus_grade" —
    not one of the 7 EvidenceGrade members) is left completely untouched --
    it must still fail Pydantic's own validation loudly, never be silently
    coerced to a plausible-looking guess."""
    item = {"grade": "bogus_grade"}
    _normalize_enum_field(item, "grade", _GRADE_VALUES)
    assert item["grade"] == "bogus_grade"

    with pytest.raises(ValidationError):
        EvidenceItem(
            claim_fragment="c",
            reference=Reference(source="PubMed"),
            supports=True,
            entailment="unclear",
            grade=item["grade"],
        )


@pytest.mark.parametrize(
    "raw_grade", ["bogus_grade × another_bogus_grade", "bogus_grade + another_bogus_grade", "bogus_grade-another_bogus_grade"]
)
def test_normalize_enum_field_leaves_fully_unsalvageable_compound_value_unchanged(raw_grade: str) -> None:
    """A compound value where NEITHER component is a valid member is also
    left completely unchanged (no partial/garbage mutation) -- still fails
    Pydantic loudly -- regardless of which separator joins it (proves the
    separator-agnostic salvage never OVER-salvages a genuinely-bad value)."""
    item = {"grade": raw_grade}
    _normalize_enum_field(item, "grade", _GRADE_VALUES)
    assert item["grade"] == raw_grade

    with pytest.raises(ValidationError):
        EvidenceItem(
            claim_fragment="c",
            reference=Reference(source="PubMed"),
            supports=True,
            entailment="unclear",
            grade=item["grade"],
        )


def test_normalize_enum_field_still_normalizes_simple_casing() -> None:
    """Non-regression: the original Phase-2 exact-case-insensitive-match
    behavior (e.g. Sonnet's observed ``"priority": "HIGH"``) still works
    unchanged after the compound-label salvage pass was added."""
    item = {"priority": "HIGH"}
    _normalize_enum_field(item, "priority", _PRIORITY_VALUES)
    assert item["priority"] == "high"

    item2 = {"grade": "Systematic_Review"}
    _normalize_enum_field(item2, "grade", _GRADE_VALUES)
    assert item2["grade"] == "systematic_review"


def test_normalize_enum_field_salvages_compound_priority_and_grade_labels() -> None:
    """The fix is ONE shared helper -- proves it benefits every REMAINING
    fixed-enum-valued agent output uniformly (priority/grade/novelty/
    entailment; axis has its own, separate, non-enum normalizer post-Phase-8
    — see section 1b below), not just whichever field a real failing run
    happened to hit."""
    priority_item = {"priority": "high, medium"}
    _normalize_enum_field(priority_item, "priority", _PRIORITY_VALUES)
    assert priority_item["priority"] == "high"

    grade_item = {"grade": "rct / observational"}
    _normalize_enum_field(grade_item, "grade", _GRADE_VALUES)
    assert grade_item["grade"] == "rct"


# ---------------------------------------------------------------------------
# 1b. Direct _normalize_axis_field coverage (NEW, Phase 8 domain
#     -universalization) — axis is free-form now, so this is a DIFFERENT
#     contract from _normalize_enum_field above: no fixed membership to
#     salvage against, so a compound label MERGES into one token instead of
#     being salvaged to its first valid component.
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "raw_axis,expected",
    [
        ("Immune_Inflammatory", "immune_inflammatory"),  # simple casing, non-compound
        ("Immune / Inflammatory", "immune_inflammatory"),
        ("  B-cell exhaustion  ", "b_cell_exhaustion"),
        # The two REAL historical separators that broke a run back when axis
        # was still a fixed enum (round 1 `×`, round 2 `+`) -- now MERGE into
        # one token instead of being salvaged to a first valid component
        # (there is no fixed component set anymore).
        ("drug_pk_metabolism × genetic_pharmacogenomic", "drug_pk_metabolism_genetic_pharmacogenomic"),
        ("renal_volume + immune_inflammatory", "renal_volume_immune_inflammatory"),
        # Arbitrary separators (mirrors the enum-salvage generality proof
        # above, but for the merge contract): every run of non-alphanumeric
        # characters becomes exactly one `_`.
        ("renal_volume & immune_inflammatory", "renal_volume_immune_inflammatory"),
        ("renal_volume\nimmune_inflammatory", "renal_volume_immune_inflammatory"),
        ("renal_volume   immune_inflammatory", "renal_volume_immune_inflammatory"),
    ],
)
def test_normalize_axis_field_merges_compound_labels(raw_axis: str, expected: str) -> None:
    item = {"axis": raw_axis}
    _normalize_axis_field(item, "axis")
    assert item["axis"] == expected


@pytest.mark.parametrize("separator", _UNSEEN_NON_WORD_SEPARATORS)
def test_normalize_axis_field_merges_across_arbitrary_non_word_separators(separator: str) -> None:
    raw = f"renal_volume{separator}immune_inflammatory"
    item = {"axis": raw}
    _normalize_axis_field(item, "axis")
    assert item["axis"] == "renal_volume_immune_inflammatory", (
        f"separator {separator!r} was not normalized: got {item['axis']!r}"
    )


def test_normalize_axis_field_leaves_punctuation_only_value_unchanged() -> None:
    """The ONE way axis can still be genuinely invalid post-Phase-8: a value
    that normalizes to NOTHING at all (no alphanumeric content survives) is
    left completely unchanged (mirrors _normalize_enum_field's own
    "genuinely invalid -> unchanged, still fails Pydantic loudly"
    contract) -- schemas.normalize_axis still requires non-empty."""
    item = {"axis": "   ///   "}
    _normalize_axis_field(item, "axis")
    assert item["axis"] == "   ///   "

    with pytest.raises(ValidationError):
        DecomposedAxis(axis=item["axis"], rationale="r", key_entities=[])


def test_normalize_axis_field_noop_on_non_string_or_missing() -> None:
    """Defensive no-op for a missing/non-string value -- mirrors
    _normalize_enum_field's own first-line guard."""
    item: dict = {}
    _normalize_axis_field(item, "axis")
    assert item == {}

    item2 = {"axis": 123}
    _normalize_axis_field(item2, "axis")
    assert item2["axis"] == 123


# ---------------------------------------------------------------------------
# 2a. run_hypothesize end to end (fake LLM, offline).
# ---------------------------------------------------------------------------


def test_run_hypothesize_survives_all_compound_axis_labels() -> None:
    """Domain-universalization (Phase 8): a compound/oddly-punctuated axis
    label is normalized into ONE merged token and NEVER by itself causes a
    hypothesis to be dropped or the whole batch to 502 (there is no fixed
    component set to fail salvaging against anymore). Reproduces both real
    historical separators (round-1 ``×``, round-2 ``+``) in the SAME run to
    prove the new free-form axis path still never regresses to a spurious
    502 for otherwise-good hypotheses."""
    payload = {"observation": "58F, resistant hypertension; elevated hs-CRP."}
    items = [
        _hyp_item("h1", "drug_pk_metabolism × genetic_pharmacogenomic"),
        _hyp_item("h2", "renal_volume + immune_inflammatory"),
        _hyp_item("h3", "  Immune_Inflammatory  "),
    ]
    fake_llm = _FixedJSONLLM({"hypotheses": items})

    result = asyncio.run(run_hypothesize(payload, fake_llm))

    assert len(result) == 3, f"expected all 3 hypotheses to survive regardless of axis punctuation, got {len(result)}"
    by_id = {h.id: h for h in result}
    assert by_id.keys() == {"h1", "h2", "h3"}
    assert by_id["h1"].axis == "drug_pk_metabolism_genetic_pharmacogenomic"
    assert by_id["h2"].axis == "renal_volume_immune_inflammatory"
    assert by_id["h3"].axis == "immune_inflammatory"


def test_run_hypothesize_drops_item_with_punctuation_only_axis() -> None:
    """The one way axis CAN still cause an individual item to be dropped
    post-Phase-8: a value that normalizes to nothing at all (e.g.
    all-punctuation). Proves this drops only that one item -- never the
    whole batch, never a 502 -- when other hypotheses are fine."""
    payload = {"observation": "obs"}
    items = [
        _hyp_item("h1", "immune_inflammatory"),
        _hyp_item("h2", "   ///   "),  # normalizes to "" -- genuinely invalid
    ]
    fake_llm = _FixedJSONLLM({"hypotheses": items})

    result = asyncio.run(run_hypothesize(payload, fake_llm))

    by_id = {h.id: h for h in result}
    assert by_id.keys() == {"h1"}, f"expected only h1 to survive (h2's punctuation-only axis dropped), got {by_id.keys()}"


def test_run_hypothesize_drops_unsalvageable_priority_keeps_valid_ones() -> None:
    """Partial-failure resilience (FIX 1(b)), retargeted from axis (Phase 8:
    axis alone can no longer be genuinely unsalvageable in a way that drops
    a hypothesis, short of all-punctuation) onto PRIORITY -- still a real
    fixed enum: a fake LLM returns 3 hypotheses where 1 has a genuinely
    -unsalvageable priority (no valid component at all) -- the 2 valid ones
    survive, the bad one is dropped, and no 502 is raised."""
    payload = {"observation": "obs"}
    items = [
        _hyp_item("h1", "drug_pk_metabolism", priority="high"),
        _hyp_item("h2", "immune_inflammatory", priority="extremely_urgent"),  # unsalvageable
        _hyp_item("h3", "raas", priority="medium"),
    ]
    fake_llm = _FixedJSONLLM({"hypotheses": items})

    result = asyncio.run(run_hypothesize(payload, fake_llm))

    by_id = {h.id: h for h in result}
    assert by_id.keys() == {"h1", "h3"}, f"expected exactly h1+h3 to survive (h2 dropped), got {by_id.keys()}"
    assert by_id["h1"].priority == "high"
    assert by_id["h3"].priority == "medium"


def test_run_hypothesize_all_unsalvageable_priority_still_raises_502() -> None:
    """The zero-valid-items case must still fail loudly (as before this
    fix) -- retargeted from axis (no longer possible to zero out a batch via
    axis alone, short of every item ALSO being all-punctuation) onto
    priority, which never silently defaults to anything -- never silently
    return an empty/fabricated result."""
    payload = {"observation": "obs"}
    items = [
        _hyp_item("h1", "raas", priority="extremely_urgent"),
        _hyp_item("h2", "sympathetic", priority="super_low"),
        _hyp_item("h3", "renal_volume", priority="critical + severe"),  # compound, neither side valid
    ]
    fake_llm = _FixedJSONLLM({"hypotheses": items})

    with pytest.raises(TransBenchLLMError) as exc_info:
        asyncio.run(run_hypothesize(payload, fake_llm))

    assert exc_info.value.status_code == 502
    assert exc_info.value.error == "llm_bad_output"


# ---------------------------------------------------------------------------
# 2b. run_decompose end to end (fake LLM, offline) -- same axis normalizer,
#     second real call site, PLUS the new condition_anchor extraction
#     (Phase 8).
# ---------------------------------------------------------------------------


def test_run_decompose_survives_compound_axis_label() -> None:
    """Covers both real historical separators (round-1 ``×`` and round-2
    ``+``) at this second real call site too, not just via the direct
    -helper tests above -- each MERGES into one token (Phase 8 free-form
    axis), never dropped, never a 502."""
    payload = {"observation": "58F, resistant hypertension; elevated hs-CRP."}
    fake_llm = _FixedJSONLLM(
        {
            "condition_anchor": "hypertension",
            "axes": [
                {
                    "axis": "immune_inflammatory × renal_volume",
                    "rationale": "Elevated hs-CRP and resistant hypertension both implicate this axis.",
                    "key_entities": ["CRP"],
                },
                {
                    "axis": "drug_pk_metabolism + genetic_pharmacogenomic",
                    "rationale": "Poor response to RAAS blockade could reflect PK or pharmacogenomic variation.",
                    "key_entities": [],
                },
                {"axis": "raas", "rationale": "Poor response to RAAS blockade.", "key_entities": []},
            ],
        }
    )

    result = asyncio.run(run_decompose(payload, fake_llm))

    assert len(result.axes) == 3
    axis_values = {a.axis for a in result.axes}
    assert axis_values == {
        "immune_inflammatory_renal_volume",
        "drug_pk_metabolism_genetic_pharmacogenomic",
        "raas",
    }
    assert result.condition_anchor == "hypertension"


def test_run_decompose_all_punctuation_only_axes_still_raises_502() -> None:
    """The zero-valid-axes case must still fail loudly -- retargeted from a
    non-member string like "cardiac" (no longer invalid under free-form
    axis) onto a genuinely punctuation-only label, the one way axis
    validation can still fail post-Phase-8."""
    payload = {"observation": "obs"}
    fake_llm = _FixedJSONLLM(
        {"condition_anchor": "", "axes": [{"axis": "   ///   ", "rationale": "r", "key_entities": []}]}
    )

    with pytest.raises(TransBenchLLMError) as exc_info:
        asyncio.run(run_decompose(payload, fake_llm))

    assert exc_info.value.status_code == 502
    assert exc_info.value.error == "llm_bad_output"


def test_run_decompose_extracts_condition_anchor() -> None:
    """Phase 8: run_decompose reads condition_anchor from the SAME parsed
    response as axes (stripped; the real primary channel for the PubMed
    retrieval anchor -- see agents.run_retrieve)."""
    payload = {"observation": "52F, rheumatoid arthritis inadequately controlled on methotrexate."}
    fake_llm = _FixedJSONLLM(
        {
            "condition_anchor": "  rheumatoid arthritis  ",
            "axes": [{"axis": "immune_inflammatory", "rationale": "r", "key_entities": []}],
        }
    )

    result = asyncio.run(run_decompose(payload, fake_llm))

    assert result.condition_anchor == "rheumatoid arthritis"


def test_run_decompose_missing_condition_anchor_defaults_to_empty_string() -> None:
    """A response that validly produces axes but omits condition_anchor
    entirely still succeeds -- condition_anchor is "" (never None, never a
    forced single-disease default), and run_retrieve's own fallback chain
    handles that gracefully."""
    payload = {"observation": "obs"}
    fake_llm = _FixedJSONLLM({"axes": [{"axis": "immune_inflammatory", "rationale": "r", "key_entities": []}]})

    result = asyncio.run(run_decompose(payload, fake_llm))

    assert result.condition_anchor == ""
