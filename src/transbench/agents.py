"""agents.py — the 8 agents (BUILD_SPEC.md §5). Phase 2 implemented agents 1-2
(Decomposer, Hypothesis Generator). Phase 3 added agents 3-4 (Evidence
Retriever — no LLM; Evidence Grader — Haiku, batched). Phase 4 added agents
5-6 (Novelty Checker, Rigor Gate — in ``rigor.py``, which reuses this
module's ``build_llm``/``_ainvoke_json``/``_coerce_list``/
``_normalize_enum_field`` helpers). Phase 5 adds agents 7-8 (Experiment
Designer, Brief Assembler) and two supporting pieces BUILD_SPEC.md §9
requires: per-run token-spend accounting (:func:`token_spend_session` /
:func:`current_token_spend`) and retrieval-snapshot REPLAY
(:func:`run_retrieve`'s ``retrieval_snapshot`` kwarg). Post-Phase-5 (Opus
verification finding): agent 7 (:func:`run_design_experiment`) now VERIFIES
a named dataset's REAL content (not just that its URL resolves to A record —
confirmed live, twice, that a host-only check lets a real-but-topically
-unrelated accession through) before ever accepting it — see
:func:`_verify_dataset_pointer`'s module comment for the two confirmed-live
mismatches that motivated this. Post-Phase-7 FINAL-GATE (Opus DETERMINISM
-suite finding, two rounds): :func:`_normalize_enum_field` now also
SALVAGES compound enum labels — e.g. real failing runs' ``"drug_pk_metabolism
× genetic_pharmacogenomic"`` and ``"renal_volume + immune_inflammatory"`` —
by splitting on ANY run of non-word characters (separator-AGNOSTIC, not a
whitelist of specific known separators — round 1 whitelisted ``×``/``x``/
``,``/``/``/``;``/``|``/"and" and still missed a plain ``+`` on the very
next failing run, which round 2 fixed by generalizing) instead of leaving
compound labels to fail Pydantic validation outright — see that function's
own docstring for the full before/after; this closes the gap where every
hypothesis in a run happening to carry a compound axis could zero out the
whole batch and raise a spurious ``502 llm_bad_output`` for an otherwise
-perfectly-good run.

Phase 8 (domain-universalization — the tool's scope widened from "an
observation about antihypertensive drugs" to any clinical/biomedical
observation, fixing a PROVEN live regression: a rheumatoid-arthritis
observation's PubMed queries were silently anchored on the hardcoded literal
"hypertension", grounding zero real evidence): agent 1
(:func:`run_decompose`) now ALSO extracts ``condition_anchor`` — the
observation's own primary disease/condition — returned via
:class:`DecomposeResult` and threaded through ``graph.py``'s state into
:func:`run_retrieve`'s real PubMed anchor (:func:`_condition_anchor` is now
only a last-resort heuristic fallback, never a forced single-disease
default). ``schemas.Axis`` is correspondingly now a free-form, normalized
string rather than a fixed 8-value hypertension-specific ``Literal`` — see
:func:`_normalize_axis_field` (replaces ``_normalize_enum_field`` for this
one field only; every other enum-valued field is unchanged) and
``schemas.py``'s own docstring.

Agents 1-2 follow the shape ``async run_<name>(payload: dict, llm) -> ...``
(BUILD_SPEC.md §5), taking a PRE-BUILT, temperature-0-bound client. Agents 3-4
have genuinely different contracts per BUILD_SPEC.md §3/§5 (retrieval has no
LLM at all; grading builds its own client from ``user_key`` since it needs
the registry/ranked-articles output of retrieval first) — see
:func:`run_retrieve` / :func:`run_grade` docstrings. Agent 7
(:func:`run_design_experiment`) takes a hypothesis + its evidence + a
PRE-BUILT client (agents 1-2's convention). Agent 8 (:func:`run_assemble`)
takes the full pipeline ``state`` dict (``graph.py``'s ``TransBenchState`` is
a plain dict at runtime and satisfies this directly) + a PRE-BUILT client,
since assembly needs many upstream pieces (axes, graded hypotheses, per
-hypothesis registries, the experiment plan, retrieval manifest) at once.
"""
from __future__ import annotations

import asyncio
import contextlib
import contextvars
import datetime as _dt
import json
import logging
import re
from dataclasses import dataclass, field
from typing import Any, Optional, get_args
from urllib.parse import urlsplit

import httpx
import json_repair
from fastapi import HTTPException
from langchain_core.messages import HumanMessage, SystemMessage
from pydantic import ValidationError

from transbench import config
from transbench.prompts import (
    BRIEF_ASSEMBLER_SYSTEM_PROMPT,
    DECOMPOSER_SYSTEM_PROMPT,
    EVIDENCE_GRADER_SYSTEM_PROMPT,
    EXPERIMENT_DESIGNER_SYSTEM_PROMPT,
    HYPOTHESIS_GENERATOR_SYSTEM_PROMPT,
)
from transbench.reuse import (
    REUSE_SOURCE,
    EvidenceFetchResult,
    EvidenceFloorError,
    FetchedData,
    build_article_registry,
    create_llm,
    ensure_evidence,
    fetch_evidence_data,
    has_minimum_evidence,
    model_supports_temperature,
    neutralize_query,
    rank_article_list,
    validate_citations,
)
from transbench.schemas import (
    DecomposedAxis,
    EvidenceGrade,
    EvidenceItem,
    ExperimentPlan,
    GradedHypothesis,
    Hypothesis,
    Priority,
    Reference,
    TransBrief,
    normalize_axis,
)

logger = logging.getLogger(__name__)

__all__ = [
    "TransBenchLLMError",
    "DecomposeResult",
    "RetrievalResult",
    "build_llm",
    "token_spend_session",
    "current_token_spend",
    "run_decompose",
    "run_hypothesize",
    "run_retrieve",
    "run_grade",
    "run_design_experiment",
    "run_assemble",
]


class TransBenchLLMError(Exception):
    """Clean, engine-native error for anything that goes wrong building or
    parsing an LLM call — never leaks a raw ``fastapi.HTTPException`` (or any
    other provider-specific exception shape) out of ``agents.py``
    (BUILD_SPEC.md §5: "Wrap every LLM call to catch fastapi.HTTPException →
    clean error"). Carries the same ``status_code``/``error``/``message``
    triple ``HTTPException.detail`` uses, so callers (graph.py today, the MCP
    tool handler in Phase 6) can catch ONE exception type without importing
    fastapi themselves.
    """

    def __init__(self, status_code: int, error: str, message: str) -> None:
        self.status_code = status_code
        self.error = error
        self.message = message
        super().__init__(f"[{status_code} {error}] {message}")


def _without_temperature(llm):
    """Return ``llm`` with ``temperature`` neutralized so it is OMITTED from the
    request payload (langchain sends ``temperature`` only when it is not None).
    For a model that rejects a temperature param outright (e.g. Claude Opus 4.8):
    the underlying client may still carry a baked-in temperature — the installed
    -Iatronix ``create_llm`` sets one unconditionally, and that source tree is
    read-only — so this is the single, path-independent guarantee that
    temperature is never sent for such a model."""
    try:
        return llm.model_copy(update={"temperature": None})
    except Exception:  # pragma: no cover -- defensive; fall back to in-place if copy unsupported
        try:
            llm.temperature = None
        except Exception:
            pass
        return llm


def build_llm(model_id: str, user_key: Optional[str], user_provider: str = "anthropic"):
    """Build a temperature-0 LangChain chat client for ``model_id``
    (BUILD_SPEC.md §5/§0.7 — call this ONCE per agent invocation, then reuse
    the returned client for that call).

    ``.bind(temperature=0)`` is belt #2 for determinism (BUILD_SPEC.md §0.7):
    setting ``LLM_TEMPERATURE=0`` in-process (``config.py``) is belt #1 but is
    only a *default* (``os.environ.setdefault``) — it does NOT retroactively
    correct an operator's own pre-exported non-zero ``LLM_TEMPERATURE``, so the
    bind is what guarantees ``temperature=0`` on every request regardless of
    ambient env. It is applied ONLY to models that accept a ``temperature``
    param: a model flagged ``supports_temperature: false`` in providers.yaml
    (e.g. Claude Opus 4.8, whose API 400s on temperature) gets no temperature
    from ``create_llm`` and none bound here either (see
    :func:`transbench.reuse.model_supports_temperature`).

    Raises:
        TransBenchLLMError: if ``create_llm`` raises ``fastapi.HTTPException``
            (missing/invalid key → 402/401; unsupported provider/model → 400;
            BUILD_SPEC.md §0.4).
    """
    try:
        llm = create_llm(model_id, user_key=user_key, user_provider=user_provider)
        if model_supports_temperature(model_id):
            # Belt #2 (BUILD_SPEC.md §0.7): pin temperature=0 for determinism.
            llm = llm.bind(temperature=0)
        else:
            # Opus 4.8 etc.: 400s on ANY temperature param. Null whatever the
            # factory baked in so temperature is never sent (path-independent —
            # works whether create_llm came from installed Iatronix or vendored).
            llm = _without_temperature(llm)
        return llm
    except HTTPException as exc:
        detail = exc.detail if isinstance(exc.detail, dict) else {}
        raise TransBenchLLMError(
            status_code=exc.status_code,
            error=str(detail.get("error", "llm_error")),
            message=str(detail.get("message", exc.detail)),
        ) from exc


# ---------------------------------------------------------------------------
# LLM-response -> JSON helpers (strict-JSON, JSON-repair on parse — BUILD_SPEC §5)
# ---------------------------------------------------------------------------

_CODE_FENCE_RE = re.compile(r"^```(?:json)?\s*\n?(.*?)\n?```\s*$", re.DOTALL)


def _response_text(response: Any) -> str:
    """Extract plain text from a LangChain ``AIMessage``. Anthropic responses
    are normally a plain ``str`` in ``.content``, but defensively also handles
    the content-block-list shape (``[{"type": "text", "text": "..."}, ...]``)
    some provider/SDK versions can return."""
    content = response.content
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts: list[str] = []
        for block in content:
            if isinstance(block, str):
                parts.append(block)
            elif isinstance(block, dict) and block.get("type") == "text":
                parts.append(str(block.get("text", "")))
        return "".join(parts)
    return str(content)


def _strip_code_fence(text: str) -> str:
    """Strip a single leading/trailing ``` or ```json fence, if present (a
    common real-world LLM habit even under a "STRICT JSON" instruction)."""
    text = text.strip()
    m = _CODE_FENCE_RE.match(text)
    return m.group(1).strip() if m else text


def _parse_json(raw_text: str) -> Any:
    """Strict ``json.loads`` first; on failure, fall back to the standalone
    ``json_repair`` package (BUILD_SPEC.md §5: "strict-JSON, JSON-repair on
    parse"). Raises :class:`TransBenchLLMError` if the text is not valid or
    repairable JSON (``json_repair.loads`` returns ``''`` on unrecoverable
    input rather than raising — confirmed empirically — so that sentinel is
    checked explicitly rather than silently propagated as "parsed data")."""
    cleaned = _strip_code_fence(raw_text)
    try:
        return json.loads(cleaned)
    except json.JSONDecodeError:
        pass

    repaired = json_repair.loads(cleaned)
    if repaired == "" or repaired is None:
        raise TransBenchLLMError(
            502,
            "llm_bad_json",
            f"LLM response was not valid or repairable JSON: {raw_text[:300]!r}",
        )
    return repaired


def _coerce_list(parsed: Any, preferred_key: str) -> list:
    """Defensive shape handling: the prompt specifies a shape (a bare list, or
    a dict wrapping a list under ``preferred_key``), but real LLM output
    occasionally wraps/unwraps differently despite explicit instructions.
    Accepts, in order: a bare list; a dict with ``parsed[preferred_key]`` ==
    a list; or (last resort) a dict with exactly one list-valued key."""
    if isinstance(parsed, list):
        return parsed
    if isinstance(parsed, dict):
        candidate = parsed.get(preferred_key)
        if isinstance(candidate, list):
            return candidate
        list_values = [v for v in parsed.values() if isinstance(v, list)]
        if len(list_values) == 1:
            return list_values[0]
    raise TransBenchLLMError(
        502,
        "llm_bad_output",
        f"Expected a JSON list (optionally under {preferred_key!r}), got: {parsed!r}",
    )


def _coerce_dict(parsed: Any, preferred_key: Optional[str] = None) -> dict:
    """Defensive shape handling for a single-JSON-OBJECT response (the
    ``_coerce_list`` sibling for agents whose STRICT-JSON contract is one
    object, not a list — agent 7's ``ExperimentPlan`` and agent 8's
    ``{"uncertainty_note": ...}``). Accepts, in order: a dict with
    ``parsed[preferred_key]`` itself a dict (a model that wrapped its object
    under a named key despite the prompt asking for the bare object); a bare
    dict; or (defensive — mirrors ``rigor.run_novelty``'s handling of the
    same real, observed LLM habit) a list containing at least one dict,
    using the first one. Returns ``{}`` — never raises — if nothing dict
    -shaped is found; callers are responsible for treating a still-missing
    required field as their own error (this function only unwraps shape, it
    never validates content).
    """
    if isinstance(parsed, dict):
        if preferred_key and isinstance(parsed.get(preferred_key), dict):
            return dict(parsed[preferred_key])
        return dict(parsed)
    if isinstance(parsed, list):
        for entry in parsed:
            if isinstance(entry, dict):
                return dict(entry)
    return {}


# Valid values derived directly from the schema's own Literal definitions —
# single source of truth, never a duplicated string list that could drift.
# NOTE: ``Axis`` is NOT here (and has no `_AXIS_VALUES` frozenset) — Phase-8
# domain-universalization replaced its fixed 8-value hypertension-specific
# Literal with a free-form, normalized string (schemas.Axis/schemas.
# normalize_axis); there is no longer a fixed membership set to validate or
# salvage against for this one field. See :func:`_normalize_axis_field`
# below for its own, separate, non-enum normalizer, and
# :data:`_ENUM_LABEL_SPLIT_RE`'s comment for why axis is intentionally
# excluded from the compound-label salvage this section still provides for
# every OTHER (still-fixed) enum field.
_PRIORITY_VALUES = frozenset(get_args(Priority))
_GRADE_VALUES = frozenset(get_args(EvidenceGrade))

# Compound-label salvage (FINAL-GATE DETERMINISM fix, Opus verification —
# round 2): an LLM occasionally returns a MULTI-VALUE label for a field that
# must be a single schema Literal — confirmed on TWO separate real failing
# runs, joined with TWO DIFFERENT separators each time: round 1 used the
# multiplication sign (``"drug_pk_metabolism × genetic_pharmacogenomic"``,
# ``"immune_inflammatory × renal_volume"``); round 2 used a plain ``" + "``
# (``"renal_volume + immune_inflammatory"``) — a separator a round-1
# whitelist-of-known-separators regex did not (and structurally never could
# fully) cover, since the model is free to pick yet another one next time
# (``"&"``, ``"-"``, an em/en dash, a newline, ...). SEPARATOR-AGNOSTIC fix
# (round 2, replacing the round-1 whitelist): every schema Literal this
# section still applies to (Priority/EvidenceGrade/NoveltyVerdict/
# entailment — Axis is now free-form, see the note above; it never needs
# this kind of salvage since ANY non-empty normalized value is already
# valid for it) is composed ONLY of ``[a-z_]`` — lowercase letters and
# underscores, both ``\w`` word characters — so splitting the lowered value
# on ANY RUN of non-word characters (``\W+``) leaves every real token
# (``systematic_review``/``open_question``/...) intact while treating
# literally anything else (space, ``+``, ``&``, ``×``, ``-``, an em/en
# dash, ``,``, ``/``, ``;``, ``|``, the word "and" surrounded by spaces, a
# newline, ...) as a separator — uniformly, with no enumeration to fall one
# separator behind the model on a future run.
_ENUM_LABEL_SPLIT_RE = re.compile(r"\W+")


def _normalize_enum_field(item: dict, field: str, valid_values: frozenset[str]) -> None:
    """Canonicalize ``item[field]`` to the schema's exact Literal casing, in
    place — salvaging what can genuinely be salvaged from what the model
    actually returned, never inventing or guessing a value it didn't say.
    Three passes, in order; any of them can leave ``item[field]`` unchanged:

    1. Exact match (``value in valid_values``) — already correct, no-op.

    2. Case-insensitive EXACT match (original Phase 2 behavior). Long-term
       -fix rationale (not a one-off patch): confirmed empirically on the
       live flagship run that Sonnet returns ``"priority": "HIGH"`` /
       ``"MEDIUM"`` despite the STRICT-JSON prompt listing the field name in
       quotes — nothing in BUILD_SPEC.md §5's prompt text pins the exact
       value casing, and models are not perfectly obedient to an implicit
       lowercase-enum convention. Rejecting semantically-perfect hypotheses
       over pure casing would be fragile.

    3. COMPOUND-LABEL salvage, SEPARATOR-AGNOSTIC (FINAL-GATE DETERMINISM
       fix, round 2 — see :data:`_ENUM_LABEL_SPLIT_RE`'s comment for the two
       real failing-run separators, ``×`` and ``+``, that motivated
       generalizing this): if there is no exact match, the value might be
       TWO OR MORE otherwise-valid members joined by ANY non-word-character
       separator. Splits ``value.strip().lower()`` on
       :data:`_ENUM_LABEL_SPLIT_RE` (``\\W+`` — any run of non-word chars;
       empty tokens dropped) and takes the FIRST resulting token that IS a
       valid member — not simply "the first token unconditionally": an
       invalid leading token (e.g. ``"cardiac + renal_volume"``) is skipped
       in favor of the next token that IS valid (``"renal_volume"``).
       Before ANY version of this pass existed, a compound label was left
       untouched, Pydantic then rejected the WHOLE item (a single Literal
       field can never hold two values), and when EVERY generated
       hypothesis happened to carry a compound axis, the entire hypothesize
       call zeroed out -> ``TransBenchLLMError [502 llm_bad_output]`` for a
       run that actually had 3 perfectly good, on-topic hypotheses.

    If NEITHER pass 2 NOR pass 3 finds a valid member anywhere in the value,
    it is left completely UNCHANGED and Pydantic's own validation rejects it
    with its normal clear error (so a truly invalid value, e.g. ``"axis":
    "cardiac"`` or ``"cardiac + pulmonary"``, still fails loudly rather than
    being silently coerced to something plausible-looking — this preserves
    strictness against genuinely-invalid values; it is a genuine *salvage*,
    never a guess).

    Reused by every agent with a FIXED-enum-valued LLM output field —
    priority (hypothesize), grade (grade), novelty (rigor.run_novelty),
    entailment (rigor.run_entailment) — via this one shared helper, so this
    fix benefits all of them uniformly from a single change. NOT used for
    axis (decompose/hypothesize) since Phase 8 (domain-universalization) —
    see :func:`_normalize_axis_field` immediately below for axis's own,
    separate, non-enum normalizer.
    """
    value = item.get(field)
    if not isinstance(value, str) or value in valid_values:
        return  # not a string, or already an exact (correctly-cased) match

    lowered = value.strip().lower()
    if lowered in valid_values:
        item[field] = lowered
        return

    for token in _ENUM_LABEL_SPLIT_RE.split(lowered):
        if token and token in valid_values:
            item[field] = token
            return
    # No valid member found anywhere (exact, cased, or as a compound-label
    # component split on ANY non-word separator) -- leave item[field]
    # exactly as the model returned it.


def _normalize_axis_field(item: dict, field: str = "axis") -> None:
    """Light, NON-enum normalizer for the free-form ``axis`` field
    (``schemas.Axis`` is a normalized string, not a fixed ``Literal`` — see
    that module's docstring; Phase 8, domain-universalization). Unlike
    :func:`_normalize_enum_field` (fixed-membership salvage against a closed
    set, still used for priority/grade/novelty/entailment), this never
    checks membership — ANY non-empty string normalizes to a valid axis
    (axes are free-form, descriptive labels, never load-bearing for
    grounding/hypothesis selection, only for readability/rationale). Applies
    the SAME normalization ``schemas.normalize_axis`` uses for real Pydantic
    validation (lowercase, strip, separators/whitespace -> a single ``_``),
    in place, so a compound/oddly-punctuated raw label from the model (e.g.
    ``"Immune / Inflammatory"``, ``"renal_volume + immune_inflammatory"``)
    becomes one coherent snake_case token before it ever reaches Pydantic,
    rather than depending on Pydantic's own (equally-correct, but less
    convenient to inspect/log pre-construction) validator to do it. If
    ``item[field]`` is missing/not a string, or normalizes to nothing at all
    (e.g. an all-punctuation input), this is a deliberate no-op — the value
    is left exactly as given and Pydantic's own validation raises its normal
    clear error (a genuinely-empty axis must still fail loudly, never be
    silently invented).
    """
    value = item.get(field)
    if not isinstance(value, str):
        return
    try:
        item[field] = normalize_axis(value)
    except ValueError:
        pass  # leave as-is; Pydantic construction below will raise clearly


def _coerce_bool(value: Any) -> bool:
    """Robustly interpret a value that SHOULD be a JSON boolean but might
    come back as a string (a known LLM inconsistency — ``json.loads``
    already turns a real JSON ``true``/``false`` literal into a proper
    Python bool, but a model can instead emit the STRING ``"false"``, which
    plain ``bool("false")`` would wrongly treat as truthy since it's a
    non-empty string). ``"false"``/``"no"``/``"0"``/``""`` (case-insensitive,
    stripped) are treated as False; everything else via normal Python
    truthiness. Used for both ``bears_on_hypothesis`` and ``supports`` in
    :func:`run_grade` for consistent, correct handling of either field."""
    if isinstance(value, str):
        return value.strip().lower() not in {"false", "no", "0", ""}
    return bool(value)


async def _ainvoke_json(llm, system_prompt: str, user_content: str) -> Any:
    """Call the LLM with a system+user message pair and parse the response as
    JSON. Always ``await``s ``llm.ainvoke(...)`` — NEVER the blocking
    ``llm.invoke()`` (BUILD_SPEC.md §5: "Never call llm.invoke() in the async
    path", it would block the event loop)."""
    response = await llm.ainvoke(
        [SystemMessage(content=system_prompt), HumanMessage(content=user_content)]
    )
    _record_token_usage(response)
    return _parse_json(_response_text(response))


# ---------------------------------------------------------------------------
# Token-spend accounting (BUILD_SPEC.md §9: "run_manifest records ... token
# spend per run"). A ``contextvars.ContextVar`` rather than a plain module
# global — this repo's own MCP server (Phase 6) may serve multiple concurrent
# ``run_transbench`` calls in one process, and a plain global dict would mix
# unrelated runs' token counts together. Each call to
# :func:`token_spend_session` installs a FRESH accumulator dict; every
# ``asyncio.Task`` spawned from within that `with` block (including ones
# fanned out via ``asyncio.gather`` in graph.py's per-hypothesis nodes) gets
# its own copy of the *context*, but that copy still references the exact
# SAME mutable dict object — so mutations from any fanned-out task are
# visible to every other task and to the code that reads the total back
# after the ``with`` block's body completes. This is race-free under asyncio
# specifically because asyncio is single-threaded/cooperative: a dict
# increment between two ``await`` points can never be interleaved with
# another coroutine's.
# ---------------------------------------------------------------------------

_TOKEN_SPEND: contextvars.ContextVar[Optional[dict]] = contextvars.ContextVar("_TOKEN_SPEND", default=None)

_EMPTY_TOKEN_SPEND: dict = {"calls": 0, "input_tokens": 0, "output_tokens": 0, "total_tokens": 0}


def _record_token_usage(response: Any) -> None:
    """Best-effort token accounting: reads LangChain's ``usage_metadata`` off
    the raw ``AIMessage`` (confirmed populated by langchain-anthropic for
    every real Anthropic response — ``input_tokens``/``output_tokens``/
    ``total_tokens``) and accumulates it into whatever mutable dict the
    current run installed via :func:`token_spend_session`. A deliberate
    no-op — never raises, never logs — when no session is active (e.g. a
    standalone agents.py call/unit test outside ``run_transbench_graph``) or
    the response is a fake-LLM-double with no such attribute (the existing
    Phase 3/4 fake-LLM tests construct a bare object with only ``.content``)
    — token accounting must never be able to break a real pipeline run or an
    existing offline test.
    """
    sink = _TOKEN_SPEND.get()
    if sink is None:
        return
    usage = getattr(response, "usage_metadata", None) or {}
    sink["calls"] = sink.get("calls", 0) + 1
    sink["input_tokens"] = sink.get("input_tokens", 0) + int(usage.get("input_tokens", 0) or 0)
    sink["output_tokens"] = sink.get("output_tokens", 0) + int(usage.get("output_tokens", 0) or 0)
    sink["total_tokens"] = sink.get("total_tokens", 0) + int(
        usage.get("total_tokens", 0) or (usage.get("input_tokens", 0) or 0) + (usage.get("output_tokens", 0) or 0)
    )


@contextlib.contextmanager
def token_spend_session():
    """Install a fresh token-spend accumulator for the duration of ONE engine
    run (BUILD_SPEC.md §9). ``graph.py``'s ``run_transbench_graph`` wraps the
    whole graph invocation in this context manager; :func:`current_token_spend`
    (called by :func:`run_assemble` while building ``run_manifest``, i.e.
    still inside this same ``with`` block since assemble is the graph's last
    node) reads back the running total, which by then includes every real
    LLM call the run made (decompose, hypothesize, each hypothesis's grade +
    entailment + novelty, design, and assemble's own ``uncertainty_note``
    call) — because that call itself runs before ``current_token_spend()`` is
    invoked below.
    """
    token = _TOKEN_SPEND.set({"calls": 0, "input_tokens": 0, "output_tokens": 0, "total_tokens": 0})
    try:
        yield
    finally:
        _TOKEN_SPEND.reset(token)


def current_token_spend() -> dict:
    """Read the current run's accumulated token spend (BUILD_SPEC.md §9). A
    fixed all-zero dict if no :func:`token_spend_session` is active (e.g. an
    agent called directly in a unit test, outside ``run_transbench_graph``)
    — never ``None``, so a caller can always safely read its keys."""
    sink = _TOKEN_SPEND.get()
    return dict(sink) if sink is not None else dict(_EMPTY_TOKEN_SPEND)


# ---------------------------------------------------------------------------
# Agent 1 — Decomposer (Haiku, config.MODEL_CHEAP)
# ---------------------------------------------------------------------------


@dataclass
class DecomposeResult:
    """Bundle returned by :func:`run_decompose` (Phase 8, domain
    -universalization — was a bare ``list[DecomposedAxis]``; widened to also
    carry the decomposer's own extracted ``condition_anchor`` so
    ``graph.py`` can thread it through ``TransBenchState`` into
    :func:`run_retrieve`, the real PubMed retrieval anchor for EVERY
    hypothesis in the run — see that function's own docstring).

    ``axes``: the validated ``DecomposedAxis`` list, exactly as before.
    ``condition_anchor``: the observation's primary disease/condition in
    plain words (e.g. ``"rheumatoid arthritis"``), as extracted by the SAME
    LLM call that produced ``axes`` — ``""`` (never ``None``) when the model
    didn't return one (defensive default; :func:`run_retrieve` falls back to
    its own heuristic, never a forced single-disease default, when this is
    empty).
    """

    axes: list[DecomposedAxis]
    condition_anchor: str = ""


async def run_decompose(payload: dict, llm) -> DecomposeResult:
    """Agent 1 — Decomposer (BUILD_SPEC.md §5, domain-universalized — see
    ``prompts.py``'s module docstring). Splits ANY clinical/biomedical
    observation into distinct, free-form biological axes AND extracts the
    observation's own primary disease/condition as ``condition_anchor``.

    payload keys:
        observation (str, required): the clinical observation to decompose.
        focus_drug (str | None, optional).

    Returns only axes the observation actually motivates. Items that fail
    schema validation are logged and skipped rather than failing the whole
    batch; if NOTHING validates, raises :class:`TransBenchLLMError` (zero
    axes is never treated as a silent success). ``condition_anchor`` has no
    such hard requirement — an LLM response that validly produces axes but
    omits/blanks ``condition_anchor`` still succeeds, just with ``""``
    (:func:`run_retrieve`'s own fallback chain handles that gracefully).
    """
    observation = payload["observation"]
    focus_drug = payload.get("focus_drug")

    user_content = f"Observation: {observation}"
    if focus_drug:
        user_content += f"\nFocus drug: {focus_drug}"

    parsed = await _ainvoke_json(llm, DECOMPOSER_SYSTEM_PROMPT, user_content)
    raw_axes = _coerce_list(parsed, preferred_key="axes")
    condition_anchor = str(parsed.get("condition_anchor") or "").strip() if isinstance(parsed, dict) else ""

    axes: list[DecomposedAxis] = []
    for item in raw_axes:
        if not isinstance(item, dict):
            logger.warning("decompose: skipping non-dict axis item %r", item)
            continue
        item = dict(item)
        _normalize_axis_field(item, "axis")
        try:
            axes.append(DecomposedAxis(**item))
        except ValidationError as exc:
            logger.warning("decompose: skipping invalid axis item %r: %s", item, exc)

    if not axes:
        raise TransBenchLLMError(
            502,
            "llm_bad_output",
            f"Decomposer produced zero valid axes from response: {parsed!r}",
        )
    return DecomposeResult(axes=axes, condition_anchor=condition_anchor)


# ---------------------------------------------------------------------------
# Agent 2 — Hypothesis Generator (Sonnet, config.MODEL_REASONING)
# ---------------------------------------------------------------------------


async def run_hypothesize(payload: dict, llm) -> list[Hypothesis]:
    """Agent 2 — Hypothesis Generator (BUILD_SPEC.md §5). Generates up to
    ``MAX_HYPOTHESES`` FALSIFIABLE mechanistic hypotheses, each naming a
    specific molecule/cell/pathway and carrying a falsifiable ``prediction``.

    payload keys:
        observation (str, required)
        focus_drug (str | None, optional)
        axes (list[DecomposedAxis], optional): agent 1's output, passed as
            grounding context so hypotheses target axes the observation
            actually motivates.
        max_hypotheses (int, optional, default config.MAX_HYPOTHESES): a
            caller-requested cap. Can only ever LOWER the effective limit —
            KICKOFF.md's "≤3 hypotheses" is a hard engine invariant, so the
            effective cap is ``min(requested, config.MAX_HYPOTHESES)``.

    The cap is enforced here by truncation even if the model over-returns.
    Items that fail schema validation are logged and skipped; if NOTHING
    validates, raises :class:`TransBenchLLMError`.
    """
    observation = payload["observation"]
    focus_drug = payload.get("focus_drug")
    axes: list[DecomposedAxis] = payload.get("axes") or []
    requested_cap = payload.get("max_hypotheses", config.MAX_HYPOTHESES)
    effective_cap = min(requested_cap, config.MAX_HYPOTHESES)

    lines = [f"Observation: {observation}"]
    if focus_drug:
        lines.append(f"Focus drug: {focus_drug}")
    if axes:
        lines.append("Decomposed biological axes (from agent 1 — ground hypotheses in these):")
        for a in axes:
            entities = ", ".join(a.key_entities) if a.key_entities else "none listed"
            lines.append(f"- {a.axis}: {a.rationale} (key entities: {entities})")
    lines.append(f"Generate at most {effective_cap} falsifiable mechanistic hypotheses.")
    user_content = "\n".join(lines)

    parsed = await _ainvoke_json(llm, HYPOTHESIS_GENERATOR_SYSTEM_PROMPT, user_content)
    raw_items = _coerce_list(parsed, preferred_key="hypotheses")

    hypotheses: list[Hypothesis] = []
    for idx, item in enumerate(raw_items):
        if not isinstance(item, dict):
            logger.warning("hypothesize: skipping non-dict hypothesis item %r", item)
            continue
        item = dict(item)
        if not item.get("id"):
            item["id"] = f"h{idx + 1}"
        _normalize_axis_field(item, "axis")
        _normalize_enum_field(item, "priority", _PRIORITY_VALUES)
        try:
            hypotheses.append(Hypothesis(**item))
        except ValidationError as exc:
            logger.warning("hypothesize: skipping invalid hypothesis item %r: %s", item, exc)

    if not hypotheses:
        raise TransBenchLLMError(
            502,
            "llm_bad_output",
            f"Hypothesis generator produced zero valid hypotheses from response: {parsed!r}",
        )
    return hypotheses[:effective_cap]


# ---------------------------------------------------------------------------
# Agent 3 — Evidence Retriever (NO LLM directly — BUILD_SPEC.md §3)
# ---------------------------------------------------------------------------


@dataclass
class RetrievalResult:
    """Bundle returned by :func:`run_retrieve`, consumed by :func:`run_grade`.

    ``ranked``: the RAW ranked+capped abstract dicts (BUILD_SPEC.md §3 —
    ``rank_article_list(...)[:ABSTRACT_CAP]``), NOT yet filtered by registry
    resolvability (that filtering is agent 4/``run_grade``'s job, per
    BUILD_SPEC.md §5's exact division of labor between agents 3 and 4).
    ``registry``: the ``ArticleRegistry`` built from ``fd`` (opaque object —
    only its documented ``.by_pmid`` interface is used elsewhere in this repo,
    per BUILD_SPEC.md §2: only the leaf FUNCTIONS are an approved reuse
    surface, not Iatronix's internal classes).
    ``fd``: the wrapped ``FetchedData`` — required by ``validate_citations``
    in agent 4 and by the run_manifest retrieval snapshot in graph.py.
    """

    neutral_query: str
    pubmed_query: str = ""
    ranked: list[dict] = field(default_factory=list)
    registry: Any = None
    fd: Any = None


# ---------------------------------------------------------------------------
# PubMed query shortening — empirically-validated fix (Phase 3 development,
# see run_retrieve's docstring for the full narrative + measurements).
# ---------------------------------------------------------------------------

_QUERY_STOPWORDS = frozenset(
    {
        "a", "an", "the", "is", "are", "was", "were", "be", "been", "being", "to", "of",
        "in", "on", "for", "and", "or", "but", "via", "with", "by", "from", "that", "this",
        "these", "those", "as", "at", "its", "mediated", "contributes", "contribute",
        "associated", "involving", "causing", "cause", "causes", "driven", "drives",
        "drive", "leading", "leads", "lead", "resulting", "result", "results", "non",
        # Generic clinical modifiers: individually near-useless as a PubMed
        # search anchor, and the tell-tale false-positive catch of Iatronix's
        # OWN neutralize_query heuristic-fallback extractor (a plain regex,
        # `[A-Z][a-z]+(?:\s+[A-Z][a-z]+)*`, that fires whenever the LLM call
        # inside neutralize_query misses its own 800ms hard timeout — it has
        # no concept of what a real entity is, so it grabs ANY capitalized
        # word, including ones merely capitalized because they start a
        # sentence, e.g. "Chronic" from "Chronic activation..." — measured
        # live: this produced the query "Chronic hypertension", which
        # returned 8 results, ALL off-topic).
        "chronic", "acute", "persistent", "sustained", "elevated", "increased",
        "decreased", "reduced", "severe", "mild", "moderate", "significant",
        "marked", "direct", "indirect", "primary", "secondary", "dependent",
        "independent",
        # Round 2 (Opus review, DEFECT 1 final fix): more of the SAME failure
        # mode — generic process/anatomy/genetics words that individually
        # carry near-zero PubMed search signal but were observed consuming a
        # query slot ahead of the actual gene symbol or disease anchor
        # (measured live: "Clonally expanded CD8+ effector-memory T cells
        # infiltrating the perivascular adventitia..." -> query "CD8
        # Clonally expanded" — 3 off-topic hits; "SLC12A3 (encoding the
        # thiazide-sensitive NCC cotransporter)... loss-of-function variant"
        # -> query "SLC12A3 loss function" — 0 hits, vs "SLC12A3 thiazide"
        # -> 6, "SLC12A3 NCC" -> 8).
        "clonally", "expanded", "infiltrating", "role", "effector", "memory",
        "loss", "function", "variant", "encoding", "activation", "accumulation",
        "hyperactivation", "downregulation",
        # Parallel word FORMS of already-listed generic terms (a different
        # inflection of the same near-zero-signal root -- "clonally" and
        # "clonal", "expanded" and "expansion", "loss-of-function" and
        # "gain-of-function", ...) and bare word-fragment prefixes that
        # ``_PLAIN_WORD_RE`` splits a hyphenated compound into ("pro-
        # inflammatory" -> "pro" + "inflammatory"; "anti-X" -> "anti" + "X").
        "clonal", "expansion", "gain", "pro", "anti",
        # Round 3 (Phase 4 flagship-log review): connective adverbs a
        # hypothesis-generator LLM uses to link clauses -- zero search
        # signal on their own, same failure class as "chronic"/"clonally"
        # above (measured live: "hypertension SLCO1B1 Concurrently" was one
        # of the queries that hit evidence-floor exhaustion).
        "concurrently", "subsequently", "additionally", "consequently",
    }
)
# Symbol-SHAPED tokens that are nonetheless too GENERIC to anchor a search
# alone: drug-CLASS abbreviations central to this tool's OWN vocabulary
# (ACE/ACEi/ARB/CCB are literally BUILD_SPEC.md's 3 named antihypertensive
# classes -- practically every observation mentions one, so they carry
# almost no discriminating power) or broad, non-specific lab-marker/cytokine
# FAMILY names (CRP is a generic inflammation marker; IFN/TNF/IL name entire
# cytokine families, not a specific target). Measured live: letting these
# occupy a symbol slot alongside (or instead of) a real, specific symbol
# reliably crowded out the hypothesis's actual distinguishing term and found
# nothing/off-topic results (``"hypertension ACE CRP"`` -> 8 hits, all
# off-topic, missing "Aldosterone"/"breakthrough" entirely;
# ``"hypertension ACEi Aldosterone"`` -- still missing "breakthrough" --
# also weaker than dropping ACEi entirely; ``"hypertension CD8 IFN"`` -> 0,
# evidence floor exhausted; ``"hypertension TRPV4 CCB"`` -> 0).
_GENERIC_SYMBOLS = frozenset(
    {
        "ace", "acei", "arb", "ccb", "crp", "bp", "raas", "ifn", "tnf", "il",
        "ckd", "esrd", "ldl", "hdl",
    }
)
# Symbol-like tokens: gene/protein/pathway/receptor names (NLRP3, SLC12A3,
# WNK4, NCC, eNOS, ACE, LDL, ...) — 2+ consecutive uppercase letters
# (optionally with a leading lowercase, e.g. "eNOS"), or a letter run
# containing a digit. These are the highest-signal PubMed search anchors a
# hypothesis statement contains, and neither `neutralize_query`'s own LLM
# extraction NOR (especially) its heuristic timeout-fallback reliably catch
# them — the fallback's `[A-Z][a-z]+` regex specifically CANNOT match an
# ALL-CAPS/digit token like "NLRP3" or "SLC12A3" (verified: Iatronix's own
# heuristic extractor never returns these for jargon-dense hypothesis text).
_SYMBOL_RE = re.compile(r"\b[A-Za-z]*[A-Z]{2,}[A-Za-z0-9]*\b|\b[A-Za-z]+\d+[A-Za-z0-9]*\b")
_WORD_RE = re.compile(r"[A-Za-z0-9][A-Za-z0-9\-]*")
_PLAIN_WORD_RE = re.compile(r"[A-Za-z]+")  # no digits/hyphens -- splits hyphenated compounds into parts


def _shorten_for_pubmed(text: str, max_words: int = 6) -> str:
    """Derive a short, keyword-style PubMed query from a full sentence by
    keeping the first ``max_words`` content words IN SENTENCE ORDER.

    LAST-RESORT FALLBACK — used by :func:`_entity_pubmed_query` only when
    NOTHING else (gene/pathway symbols, ``stance.entities``, plain sentence
    content words) yields any usable term at all (essentially never, in
    practice, since a real hypothesis statement almost always contains at
    least one content word — this exists as a final safety net so
    :func:`_entity_pubmed_query` always returns a non-empty query).

    Deterministic, no LLM (called only from the no-LLM Evidence Retriever,
    agent 3, per BUILD_SPEC.md §3/§5).
    """
    words = _WORD_RE.findall(text)
    kept: list[str] = []
    for w in words:
        wl = w.lower()
        if wl in _QUERY_STOPWORDS or len(w) <= 2 or "-" in w:
            continue
        kept.append(w)
        if len(kept) >= max_words:
            break
    return " ".join(kept) if kept else text


def _is_high_signal_term(word: str) -> bool:
    """True iff ``word`` is a usable PubMed search term: not a stopword/
    generic-clinical-modifier/generic-symbol-shaped-abbreviation, and longer
    than 2 characters. Applied uniformly to every candidate term regardless
    of source (anchor/symbol/entity/content-word) — a term like "CRP" is
    exactly as unhelpful whichever pass happens to find it."""
    w = word.strip()
    wl = w.lower()
    return len(w) > 2 and wl not in _QUERY_STOPWORDS and wl not in _GENERIC_SYMBOLS


# Disease/condition anchor — LAST-RESORT heuristic fallback (Opus review,
# DEFECT 1 original fix; domain-universalized, Phase 8 — see prompts.py's
# module docstring for the full regression narrative this fixes: a
# rheumatoid-arthritis observation's PubMed queries were previously
# silently forced onto the literal word "hypertension" via this function's
# OLD unconditional default, grounding ZERO real evidence). The PRIMARY,
# reliable anchor source is now the Decomposer's own `condition_anchor`
# output (agent 1, prompts.DECOMPOSER_SYSTEM_PROMPT — a single existing LLM
# call that already reads the full observation, so it can name ANY disease,
# not just the handful recognized below), threaded through `graph.py`'s
# state into `run_retrieve`'s `condition_anchor` kwarg. This function is
# consulted ONLY when that real anchor is missing/blank (a standalone
# caller with no decomposer output at all, or a genuine decomposer
# extraction failure) — a small, non-exhaustive set of LITERAL
# condition-name patterns (spanning this repo's own shipped fixtures —
# hypertension, rheumatoid arthritis, type 1/2 diabetes, melanoma, C.
# difficile infection — recognized directly here as defense-in-depth) rather
# than a real disease-name extractor (that is deliberately the decomposer's
# job, not a regex's). Detected from the ORIGINAL OBSERVATION (one anchor
# per run, computed once, shared by every hypothesis's PubMed query — NOT
# per-hypothesis, so it can never be crowded out by a specific hypothesis's
# own jargon-dense phrasing the way a sentence-scanned term can).
_CONDITION_ANCHOR_PATTERNS: list[tuple[re.Pattern, str]] = [
    (re.compile(r"\brheumatoid arthritis\b", re.IGNORECASE), "rheumatoid arthritis"),
    (re.compile(r"\bpsoriatic arthritis\b", re.IGNORECASE), "psoriatic arthritis"),
    (re.compile(r"\btype\s*2\s*diabetes(?:\s*mellitus)?\b|\bT2DM\b", re.IGNORECASE), "type 2 diabetes"),
    (re.compile(r"\btype\s*1\s*diabetes(?:\s*mellitus)?\b|\bT1DM\b", re.IGNORECASE), "type 1 diabetes"),
    (re.compile(r"\bmelanoma\b", re.IGNORECASE), "melanoma"),
    (
        re.compile(r"\bClostridioides\s+difficile\b|\bClostridium\s+difficile\b|\bC\.?\s?diff(?:icile)?\b", re.IGNORECASE),
        "clostridioides difficile infection",
    ),
    (re.compile(r"\bhypertension\b", re.IGNORECASE), "hypertension"),
    (re.compile(r"\bblood pressure\b", re.IGNORECASE), "hypertension"),
    (re.compile(r"\bBP\b"), "hypertension"),
]


def _condition_anchor(observation: str) -> str:
    """LAST-RESORT heuristic disease/condition anchor (see the module
    comment above :data:`_CONDITION_ANCHOR_PATTERNS` for why/when this is
    even consulted). Case-insensitive word-boundary match against a small
    set of literal condition names/synonyms (except "BP", which must be
    uppercase to avoid matching inside ordinary words). Returns ``""`` — NOT
    a forced single-disease default — when nothing is recognized (e.g. an
    observation about a condition this small pattern list doesn't happen to
    name), so a genuinely-unrecognized observation never gets a wrong/
    misleading anchor forced onto it; :func:`_entity_pubmed_query`'s own
    ``_add("")`` already no-ops gracefully on an empty anchor, so simply
    omitting the anchor slot is a safe, correct outcome, not a special case
    this function needs to handle itself.
    """
    for pattern, anchor in _CONDITION_ANCHOR_PATTERNS:
        if pattern.search(observation or ""):
            return anchor
    return ""


def _entity_pubmed_query(
    stance_entities: list[str],
    neutral: str,
    condition_anchor: str,
    max_terms: int = 3,
    max_symbols: int = 1,
) -> str:
    """Build a short, HIGH-SIGNAL PubMed query — the DEFECT 1 fix (Opus
    review; two earlier attempts, each itself found to regress on real data,
    converged on this design — see the narrative below).

    PubMed ANDs terms in a ``[Title/Abstract]`` search, and — measured
    directly against real flagship hypothesis text — the sweet spot is
    narrow: 2-3 well-chosen terms consistently found real, on-topic PMIDs
    (``"hypertension SLC12A3 thiazide"`` -> 10, incl. PMID 28274929
    "Hydrochlorothiazide treatment increases the abundance of the NaCl
    cotransporter..."; ``"hypertension ADMA DDAH2"`` -> 3, incl. PMID
    18772860 "Nebivolol treatment reduces serum levels of asymmetric
    dimethylarginine..."; ``"hypertension NLRP3 inflammasome"`` -> PMID
    30091406 "Role of NLRP-3 Inflammasome in Hypertension"), while stacking
    MULTIPLE specific terms WITHOUT a stable disease anchor regularly found
    NOTHING or an off-topic hit that the grader then correctly drops
    entirely (``"SLC12A3 NCC WNK4 SPAK"`` -> 0; ``"CD8 Clonally expanded"``
    -> 3 off-topic hits — MS/hepatitis/ovarian cancer; ``"SLC12A3 loss
    function"`` -> 0). Terms are gathered as follows, until ``max_terms`` is
    reached:

    0. ``condition_anchor`` — GUARANTEED to be one of the ``max_terms``
       slots (when non-empty), added FIRST, before anything else, so a
       hypothesis's own jargon-dense phrasing can never crowd it out (this
       is the DEFECT 1 root-cause fix — earlier rounds had no guaranteed
       slot at all, so a hypothesis with 2-3 sentence-order-early generic
       words, e.g. "CD8 Clonally expanded", never reached the anchor or a
       second concept term). Resolved ONCE PER RUN from the observation's
       OWN condition (Phase 8, domain-universalization: primarily the
       Decomposer's real LLM-extracted ``condition_anchor``, e.g.
       "rheumatoid arthritis"/"melanoma"/"type 2 diabetes"; the heuristic
       :func:`_condition_anchor` is only a last-resort fallback) and passed
       in by the caller — not re-derived per hypothesis, so every hypothesis
       in a run shares the exact same anchor. This function's own ``_add``
       already no-ops gracefully on an empty string, so a run whose
       condition truly could not be determined simply omits this slot
       (never a wrong/misleading forced anchor) rather than defaulting to
       any single disease.
    1. Gene/pathway-SYMBOL-like token(s) scanned directly out of ``neutral``
       (see ``_SYMBOL_RE`` above), capped at ``max_symbols`` (default 1) —
       the most reliable, specific anchor a hypothesis statement contains
       (NLRP3, SLC12A3, WNK4, NCC, eNOS, CD8, ADMA, TRPV4, ...), and the one
       ``neutralize_query``'s own extraction most reliably MISSES, especially
       via its heuristic timeout-fallback path (measured live: that
       fallback's own entity list for a real hypothesis was just
       ``["Chronic"]`` — a sentence-initial capitalized common word, not a
       real entity at all; its regex structurally cannot match an
       ALL-CAPS/digit token like "NLRP3"). Capped at just 1 (not several) —
       stacking multiple symbols together, even alongside the now-guaranteed
       anchor, was measured to let a second, GENERIC symbol-shaped
       abbreviation (e.g. "ACE"/"CRP"/"IFN" — see ``_GENERIC_SYMBOLS`` below)
       crowd out the hypothesis's real distinguishing term; a single
       specific symbol plus a content-word-derived second concept performed
       more reliably across real hypotheses than stacking symbols.
    2. ``neutralize_query``'s own ``stance.entities`` (drug/disease names) —
       filtered through the same stopword/generic-modifier/length check
       (:func:`_is_high_signal_term`) so a heuristic-fallback artifact like
       "Chronic" or "In" never becomes a query term on its own.
    3. Plain sentence content words (hyphenated compounds SPLIT into parts,
       not dropped whole — e.g. "chymase-mediated" contributes "chymase";
       "mediated" is filtered separately as a stopword) — fills any
       remaining room, covering PHRASE-based hypotheses with no single gene
       symbol (e.g. "aldosterone breakthrough", a named clinical phenomenon,
       not a gene). ``_QUERY_STOPWORDS`` specifically excludes the generic
       process/anatomy/genetics words repeatedly observed consuming a slot
       ahead of the real signal: clonally, expanded, infiltrating, role,
       effector, memory, loss, function, variant, encoding, activation,
       accumulation, hyperactivation, downregulation (+ the round-1 list —
       mediated, driven, chronic, non, dependent, via, ...).

    Falls back to :func:`_shorten_for_pubmed` only if ``condition_anchor``
    itself is somehow falsy AND nothing else is found (should not happen in
    practice — ``_condition_anchor`` always returns a non-empty string).
    """
    terms: list[str] = []
    seen: set[str] = set()

    def _add(word: str) -> bool:
        w = word.strip()
        if not w or w.lower() in seen or not _is_high_signal_term(w):
            return False
        wl = w.lower()
        # Reject a candidate that is a bare PREFIX FRAGMENT of an
        # already-added term — the plain-letters content-word pass
        # (``_PLAIN_WORD_RE`` has no digits) splits a digit-bearing symbol
        # like "SLC12A3" into letter-only runs ("SLC", "A"); without this
        # check "SLC" would be re-added as if it were a distinct, meaningful
        # term (measured live: produced the query "hypertension SLC12A3
        # SLC" — a meaningless duplicate slot). A real second concept is
        # never merely a strict prefix of a term already chosen.
        if any(existing.startswith(wl) for existing in seen):
            return False
        seen.add(wl)
        terms.append(w)
        return True

    _add(condition_anchor)  # guaranteed slot 0 -- added before anything else

    symbols_taken = 0
    for m in _SYMBOL_RE.findall(neutral):
        if symbols_taken >= max_symbols or len(terms) >= max_terms:
            break
        if _add(m):
            symbols_taken += 1

    for e in stance_entities or []:
        if len(terms) >= max_terms:
            break
        _add(e or "")

    if len(terms) < max_terms:
        for w in _PLAIN_WORD_RE.findall(neutral):
            if len(terms) >= max_terms:
                break
            _add(w)

    if not terms:
        return _shorten_for_pubmed(neutral)
    return " ".join(terms)


_STATEMENT_MATCH_RE = re.compile(r"\s+")


def _normalize_statement(text: str) -> str:
    """strip/lower/collapse-internal-whitespace normalization used ONLY to
    compare a snapshot entry's stored ``"statement"`` against the CURRENT
    hypothesis's ``statement`` before ever replaying evidence for it (see
    :func:`run_retrieve`'s ``retrieval_snapshot`` STATEMENT-match safety
    guard) — never used for anything citation/grading-related."""
    return _STATEMENT_MATCH_RE.sub(" ", (text or "").strip().lower())


def _replay_from_snapshot(hypothesis_id: str, snapshot_entry: dict) -> RetrievalResult:
    """Snapshot REPLAY (BUILD_SPEC.md §9): a pure, fully OFFLINE, zero
    -network reconstruction of a prior :func:`run_retrieve` call's
    ``RetrievalResult`` from a previously-captured
    ``run_manifest["retrieval_snapshot"][hypothesis_id]`` entry (shape:
    ``{"neutral_query": str, "pubmed_query": str, "abstracts": list[dict],
    "statement": str}`` — exactly what ``graph.py``'s
    ``_retrieve_and_grade_node`` writes there on a live run; ``"statement"``
    is optional/absent on snapshots captured before the post-release
    STATEMENT-match safety guard was added, and is otherwise checked by the
    CALLER, :func:`run_retrieve`, before this function is ever invoked — this
    function itself does not read or care about that key). Never calls
    ``neutralize_query`` or ``fetch_evidence_data``.

    Rebuilds a REAL, fully-functional ``ArticleRegistry`` from the
    snapshot's own raw abstract dicts via the SAME reused
    ``build_article_registry`` leaf a live fetch uses (wrapped in
    ``EvidenceFetchResult``/``FetchedData`` exactly per BUILD_SPEC.md §2's
    contract) — both pure, in-process, no I/O — so downstream
    ``run_grade``'s ``registry.lookup_id(...)`` resolves precisely as it
    would have the first time. All snapshot abstracts are placed in the
    ``clinical_trial_abstracts`` bucket; which of the 3 buckets they land in
    is immaterial to registry construction (``build_article_registry``
    unions all 3 and infers ``source_type`` per-item from ``nct_id``
    presence, not from which list it came from — confirmed by reading
    ``article_registry.py``'s ``_walk_abstracts``).

    The snapshot's own stored abstract ORDER is preserved verbatim as
    ``ranked`` (not re-run through ``rank_article_list``) — it already IS
    the exact ranked+capped output of the original live run, and
    re-ranking a pure function against unchanged input would only
    reproduce the same order at the cost of needing to also snapshot
    ``entities``/``query_text`` for no benefit.
    """
    neutral_query = str(snapshot_entry.get("neutral_query") or "")
    pubmed_query = str(snapshot_entry.get("pubmed_query") or "")
    abstracts = [a for a in (snapshot_entry.get("abstracts") or []) if isinstance(a, dict)]

    merged = EvidenceFetchResult(
        clinical_trial_abstracts=abstracts,
        systematic_review_abstracts=[],
        guideline_abstracts=[],
        fetch_success=bool(abstracts),
    )
    fd = FetchedData(query_type="evidence", evidence_data=merged)
    registry = build_article_registry(fd)

    logger.info(
        "run_retrieve: REPLAYED hypothesis %s from retrieval_snapshot (%d abstracts, zero network calls)",
        hypothesis_id,
        len(abstracts),
    )
    return RetrievalResult(
        neutral_query=neutral_query, pubmed_query=pubmed_query, ranked=abstracts, registry=registry, fd=fd
    )


async def run_retrieve(
    hypothesis: Hypothesis,
    user_key: Optional[str],
    *,
    user_provider: str = "anthropic",
    model_id: Optional[str] = None,
    observation: str = "",
    condition_anchor: Optional[str] = None,
    retrieval_snapshot: Optional[dict] = None,
) -> RetrievalResult:
    """Agent 3 — Evidence Retriever (BUILD_SPEC.md §3; no LLM call is made
    directly by this function — ``neutralize_query`` makes its own internal,
    self-contained Haiku call with its own 800ms timeout + heuristic
    fallback, so from this function's perspective it is just an async data
    transform).

    ``condition_anchor`` (Phase 8, domain-universalization) is the REAL
    disease/condition anchor for this run, when the caller has one — in
    practice, ``graph.py`` threads through agent 1's own LLM-extracted
    ``DecomposeResult.condition_anchor`` (e.g. "rheumatoid arthritis",
    "melanoma") via ``TransBenchState``, so this is the PRIMARY, reliable
    source, resolved ONCE per run and shared by every hypothesis's query
    (never re-derived per hypothesis, so it can never be crowded out by one
    hypothesis's own jargon-dense phrasing). ``observation`` is the ORIGINAL
    clinical observation (not the hypothesis) — used ONLY as the input to
    the last-resort heuristic fallback, :func:`_condition_anchor`, when
    ``condition_anchor`` itself is ``None``/blank. The effective anchor is
    therefore ``(condition_anchor or "").strip() or _condition_anchor(
    observation)`` — which may itself legitimately resolve to ``""`` (no
    anchor at all, NOT a forced single-disease default — see
    :func:`_condition_anchor`'s own docstring) when neither source finds
    one; :func:`_entity_pubmed_query` already handles an empty anchor
    gracefully. Both default to falsy (``None``/``""``) for standalone/test
    callers that don't have either handy.

    ``retrieval_snapshot`` (BUILD_SPEC.md §9, Phase 5): when provided AND it
    contains an entry keyed by ``hypothesis.id``, this function REPLAYS that
    entry via :func:`_replay_from_snapshot` instead of calling
    ``neutralize_query``/``fetch_evidence_data`` at all — a fully
    deterministic, zero-network reconstruction (see that function's
    docstring). When ``retrieval_snapshot`` is ``None``, empty, or simply
    has no entry for THIS hypothesis's id, this falls through to live
    retrieval exactly as before — a graceful, silent fallback (a snapshot
    captured on a prior run legitimately may not cover a freshly-generated
    hypothesis id on a later run; that is not an error).

    STATEMENT-match safety guard (post-release addition, feeds
    ``TRANSBENCH_MODE=snapshot`` in ``engine.py`` as well as any direct
    ``retrieval_snapshot`` caller): an id match ALONE is not sufficient to
    prove a snapshot entry was actually captured for THIS hypothesis — a
    fresh ``run_transbench`` call regenerates hypothesis ids independently
    each run (agent 2 assigns ``h1``/``h2``/``h3`` positionally), so id
    collision between an unrelated snapshot and today's differently-worded
    hypothesis is entirely possible. When a matched entry ALSO carries a
    non-empty ``"statement"`` key, it is replayed ONLY if
    ``_normalize_statement(entry["statement"]) ==
    _normalize_statement(hypothesis.statement)`` — a mismatch is logged and
    falls through to LIVE retrieval for that one hypothesis (never an
    error, never a crash; every other hypothesis in the same run is
    unaffected). An entry with NO ``"statement"`` key at all (every snapshot
    captured before this guard existed) keeps the original, unconditional
    id-only-keyed replay behavior — fully backward compatible.

    Implements the corrected §3 flow, with one empirically-forced query-
    construction fix (see below):
      1. ``neutralize_query(hypothesis.statement, MODEL_CHEAP, user_key,
         "anthropic")`` -> ``stance.neutral_clinical_question``.
      2. ``fetch_evidence_data(pubmed_query)`` (support) +
         ``fetch_evidence_data(f"{pubmed_query} (limitations OR negative OR
         no association)")`` (contradiction pass) — see deviation #2 below
         for what ``pubmed_query`` is and why.
      3. Merge the 3 abstract lists from both results into ONE
         ``EvidenceFetchResult``, then **WRAP** in
         ``FetchedData(query_type="evidence", evidence_data=merged)`` — the
         load-bearing fix: ``has_minimum_evidence``/``ensure_evidence``/
         ``build_article_registry`` all consume ``FetchedData``, never the
         raw ``EvidenceFetchResult``.
      4. ``has_minimum_evidence(fd)`` -> if False, broaden via
         ``ensure_evidence(fd, pubmed_query, "evidence")``.
      5. ``rank_article_list(raw_abstracts, entities=..., query_text=
         pubmed_query)[:ABSTRACT_CAP]``, then ``build_article_registry(fd)``.

    Two deviations flagged (not silent) — both required for retrieval to
    actually find real, on-topic evidence for realistic hypothesis
    statements, verified empirically during Phase 3 development:

    1. BUILD_SPEC.md §3's own pseudocode passes ``entities=hyp.key_entities``
       to ``rank_article_list`` — but ``schemas.Hypothesis`` (§4, verbatim)
       has NO ``key_entities`` field (only ``DecomposedAxis`` does); that
       line would raise ``AttributeError`` as literally written. This
       function instead passes ``entities=stance.entities`` — already
       produced by the exact same ``neutralize_query`` call §3 mandates, and
       its own docstring says "Drug/disease entities extracted from the
       query", i.e. semantically exactly what ranking wants. No
       ``schemas.py`` change (frozen/verbatim); zero extra plumbing.

    2. BUILD_SPEC.md §3's pseudocode passes the FULL ``neutral_clinical_
       question`` sentence directly into ``fetch_evidence_data``, and builds
       the contradiction query as a bare, unparenthesized
       ``f"{neutral} limitations OR negative OR no association"`` suffix.
       PubMed's ``[Title/Abstract]`` search is literal keyword matching, not
       semantic search, so a long synthesized sentence rarely matches
       verbatim, and ``fetch_evidence_data`` wraps whatever string it's given
       as ``f"{query}[Title/Abstract] AND (...)"`` — a bare trailing ``OR``
       breaks that field-tag's intended scope. Fix (revised three times after
       Opus review — DEFECT 1): derive ``pubmed_query =
       _entity_pubmed_query(stance.entities, neutral, condition_anchor)`` —
       a SHORT, HIGH-SIGNAL query that GUARANTEES the run's disease/
       condition anchor (from ``observation``, see :func:`_condition_anchor`)
       a slot, then fills the rest from gene/pathway-symbol-like tokens
       scanned out of ``neutral`` directly, ``neutralize_query``'s own
       extracted entities, and (if still short) plain sentence content words,
       in that priority order (see :func:`_entity_pubmed_query`'s docstring
       for the full narrative + measurements — three earlier attempts each
       still discarded high-signal terms, diluted an already-good query, or
       let a hypothesis's own jargon crowd out the disease anchor entirely
       on real data; this version does not). Use ``pubmed_query`` everywhere
       §3 uses ``neutral`` for an actual PubMed call; PROPERLY parenthesize
       the contradiction-pass OR-group: ``f"{pubmed_query} (limitations OR
       negative OR no association)"``. The TRUE ``neutral_clinical_question``
       is still what is returned as ``RetrievalResult.neutral_query`` (used
       for reporting/the run_manifest) — only the actual PubMed calls use
       the shortened form.

    Never raises: ``EvidenceFloorError`` (all 5 broadening strategies
    exhausted) and any other unexpected error are caught and logged, and this
    returns an EMPTY ``RetrievalResult`` (``ranked=[]``) instead — so one
    hypothesis's retrieval failure can never crash a concurrent
    ``asyncio.gather`` over several hypotheses. A genuinely-zero-evidence
    hypothesis (e.g. about a very sparse/novel gene combination) is a valid,
    expected outcome (handled gracefully downstream), not a bug.
    """
    snapshot_entry = (retrieval_snapshot or {}).get(hypothesis.id)
    if snapshot_entry is not None:
        snapshot_statement = snapshot_entry.get("statement")
        if not snapshot_statement or _normalize_statement(snapshot_statement) == _normalize_statement(
            hypothesis.statement
        ):
            return _replay_from_snapshot(hypothesis.id, snapshot_entry)
        logger.warning(
            "run_retrieve: snapshot entry for hypothesis id %s carries a DIFFERENT statement "
            "than the CURRENT hypothesis (safety guard against replaying evidence captured for "
            "a different hypothesis) -- falling back to LIVE retrieval for this hypothesis only. "
            "snapshot_statement=%r current_statement=%r",
            hypothesis.id,
            snapshot_statement,
            hypothesis.statement,
        )

    stance = await neutralize_query(
        hypothesis.statement, model_id or config.MODEL_CHEAP, user_key, user_provider
    )
    neutral = stance.neutral_clinical_question
    # Real decomposer-extracted anchor wins; last-resort heuristic on the
    # observation text is the fallback; "" (no anchor at all) is a valid,
    # honest outcome for either -- never a forced single-disease default
    # (Phase 8, domain-universalization; see this function's own docstring).
    anchor = (condition_anchor or "").strip() or _condition_anchor(observation)
    pubmed_query = _entity_pubmed_query(stance.entities, neutral, anchor)

    try:
        result = await fetch_evidence_data(pubmed_query)
        contra = await fetch_evidence_data(f"{pubmed_query} (limitations OR negative OR no association)")

        merged = EvidenceFetchResult(
            clinical_trial_abstracts=result.clinical_trial_abstracts + contra.clinical_trial_abstracts,
            systematic_review_abstracts=result.systematic_review_abstracts + contra.systematic_review_abstracts,
            guideline_abstracts=result.guideline_abstracts + contra.guideline_abstracts,
            fetch_success=result.fetch_success or contra.fetch_success,
        )
        fd = FetchedData(query_type="evidence", evidence_data=merged)  # REQUIRED wrapper

        if not has_minimum_evidence(fd):  # consumes FetchedData, not the raw result
            fd = await ensure_evidence(fd, pubmed_query, "evidence")

        raw_abstracts = (
            fd.evidence_data.clinical_trial_abstracts
            + fd.evidence_data.systematic_review_abstracts
            + fd.evidence_data.guideline_abstracts
        )
        ranked = rank_article_list(raw_abstracts, entities=stance.entities, query_text=pubmed_query)[
            : config.ABSTRACT_CAP
        ]
        registry = build_article_registry(fd)  # URL-guaranteed refs; look up by pmid

        return RetrievalResult(
            neutral_query=neutral, pubmed_query=pubmed_query, ranked=ranked, registry=registry, fd=fd
        )

    except EvidenceFloorError as exc:
        logger.warning("run_retrieve: evidence floor exhausted for %r: %s", pubmed_query, exc)
        return RetrievalResult(
            neutral_query=neutral, pubmed_query=pubmed_query, ranked=[], registry=build_article_registry(None), fd=None
        )
    except Exception:
        logger.exception("run_retrieve: unexpected retrieval failure for %r", pubmed_query)
        return RetrievalResult(
            neutral_query=neutral, pubmed_query=pubmed_query, ranked=[], registry=build_article_registry(None), fd=None
        )


# ---------------------------------------------------------------------------
# Agent 4 — Evidence Grader (Haiku, config.MODEL_CHEAP, BATCHED per hypothesis)
# ---------------------------------------------------------------------------


def _article_prompt_block(key: str, article: dict) -> str:
    year = article.get("year")
    title = (article.get("title") or "").strip()
    abstract = (article.get("abstract") or "").strip()[:1200]
    return f"- pmid={key} year={year}\n  title: {title}\n  abstract: {abstract}"


def _resolution_key(registry_entry: Any) -> str:
    """The internal correlation id used for ONE ranked article throughout
    ``run_grade`` — shown to the grader LLM under the (unchanged)
    ``EVIDENCE_GRADER_SYSTEM_PROMPT``'s ``"pmid"`` JSON field, and used to
    match its response back to the right article. A real pmid when the
    REGISTRY ENTRY has one (the common case — preserves existing behavior/
    prompt semantics unchanged for PubMed abstracts); otherwise the
    registry's own ``nct_id``/``doi``/``ref_token`` (always present, always
    unique), in that preference order, for ClinicalTrials.gov/DOI-only/
    title-only-matched entries that have no pmid at all. This is PURELY an
    internal correlation id — the final ``EvidenceItem.reference.pmid`` is
    always built directly from ``registry_entry.pmid`` (may genuinely stay
    ``None``), never from this key.
    """
    return (
        registry_entry.pmid
        or registry_entry.nct_id
        or registry_entry.doi
        or registry_entry.ref_token
    )


async def run_grade(
    hypothesis: Hypothesis,
    ranked: list[dict],
    registry: Any,
    fd: Any,
    user_key: Optional[str],
    *,
    user_provider: str = "anthropic",
    model_id: Optional[str] = None,
) -> list[EvidenceItem]:
    """Agent 4 — Evidence Grader (BUILD_SPEC.md §5). ONE batched Haiku call
    over the hypothesis's <=``ABSTRACT_CAP`` ranked abstracts -> supports/
    contradicts + evidence grade for each. Builds its own client from
    ``user_key`` (unlike agents 1-2, this function receives ``ranked``/
    ``registry`` — the output of agent 3 — rather than a pre-built ``llm``,
    per the coordinator's explicit Phase 3 signature).

    Steps (BUILD_SPEC.md §5 agent 4, exactly):
      1. Resolve each ``ranked`` article's citable ``Reference`` via
         ``registry.lookup_id(pmid=, nct_id=, doi=, title=)`` (Opus review —
         resolves via pmid OR nct_id OR doi OR title, not pmid alone: the
         original pmid-only ``registry.by_pmid[...]`` lookup silently
         dropped every ClinicalTrials.gov result — which carries an
         ``nct_id``, not a ``pmid`` — before it ever reached grading, even
         though it is real, resolvable, citable evidence and often the
         HIGHEST-grade evidence available, e.g. an RCT registered on
         ClinicalTrials.gov). Drop any article with no registry match at all
         (no resolvable citation via any of pmid/nct_id/doi/title) — done
         HERE, not in agent 3, per BUILD_SPEC.md §5's division of labor.
         See :func:`_resolution_key` for how a pmid-less item is still given
         a stable identifier to correlate through the LLM call below.
      2. ONE batched Haiku call over all resolvable abstracts (never one call
         per abstract) -> ``bears_on_hypothesis``/``supports``/``grade``/
         ``claim_fragment`` per pmid. ``grade`` is case-insensitively
         normalized via the same schema-derived :func:`_normalize_enum_field`
         helper Phase 2 added; ``bears_on_hypothesis``/``supports`` are
         parsed via :func:`_coerce_bool` (a model can emit the STRING
         ``"false"``, which plain ``bool(...)`` would wrongly treat as
         truthy).
      2a. DROP any item where ``bears_on_hypothesis`` is not true — BEFORE
         constructing an ``EvidenceItem`` (Opus review, DEFECT 3 fix): the
         prompt already asked the model to omit off-topic abstracts on its
         own, but that was observed to fail in practice (off-topic abstracts
         emitted as ``supports=False``, i.e. as if they were real
         contradicting evidence). An explicit per-item relevance signal that
         the CODE enforces is not optional/best-effort the way an implicit
         "please omit these" instruction is — an off-topic abstract must
         never become an ``EvidenceItem`` at all, neither supporting nor
         contradicting.
      3. Build ``response_data = {"references": [...], "content_items":
         [...]}`` (the "adaptive claim" shape Iatronix's own
         ``_extract_claims`` recognizes: ``text`` + ``source``/``pmid``) and
         call ``validate_citations(response_data, "evidence", fetched_data=
         fd)``; honor its in-place mutations — drop any claim flagged
         ``__drop__`` and re-read ``response_data["references"]`` AFTER the
         call as the source of truth (it may remove hallucinated-PMID refs or
         null out an unsafe URL in place; ``query_type="evidence"`` is not in
         ``validate_citations``'s "strict" set so ``__drop__`` is inert for
         this call in practice today — handled generically/correctly
         regardless, per the function's real, general contract).

    ``entailment`` is set to ``"unclear"`` for every item — PROVISIONAL, not
    derived from ``supports``. BUILD_SPEC.md §6 frames entailment as a
    deliberately SEPARATE signal from this grader's coarse supports/
    contradicts call ("existence ≠ support"); mapping ``supports ->
    entailment`` here would risk being mistaken for the real, dedicated
    Phase-4 batched-entailment pass (rigor.py). Phase 4 overwrites this field
    with real per-item verdicts.

    Never invents a citation that wasn't in ``ranked``. Returns ``[]`` (not
    an error) if ``ranked`` is empty or nothing resolves/validates/grades —
    zero evidence for a hypothesis is a valid outcome, handled by the (future)
    rigor gate, not a crash here.
    """
    if not ranked:
        return []

    resolvable: dict[str, tuple[dict, Any]] = {}
    for article in ranked:
        registry_entry = registry.lookup_id(
            pmid=article.get("pmid"),
            nct_id=article.get("nct_id"),
            doi=article.get("doi"),
            title=article.get("title"),
        )
        if registry_entry is None:
            continue  # no resolvable citation via pmid/nct_id/doi/title — drop
        key = _resolution_key(registry_entry)
        if key in resolvable:
            continue  # dedupe (support + contradiction passes can overlap)
        resolvable[key] = (article, registry_entry)

    if not resolvable:
        return []

    llm = build_llm(model_id or config.MODEL_CHEAP, user_key, user_provider)
    user_content = "\n".join(
        [
            f"Hypothesis: {hypothesis.statement}",
            f"Prediction: {hypothesis.prediction}",
            "Abstracts:",
            *[_article_prompt_block(key, article) for key, (article, _) in resolvable.items()],
        ]
    )
    parsed = await _ainvoke_json(llm, EVIDENCE_GRADER_SYSTEM_PROMPT, user_content)
    raw_items = _coerce_list(parsed, preferred_key="items")

    candidates: list[tuple[str, EvidenceItem, dict]] = []
    for item in raw_items:
        if not isinstance(item, dict):
            logger.warning("grade: skipping non-dict grading item %r", item)
            continue
        key = str(item.get("pmid", ""))
        pair = resolvable.get(key)
        if pair is None:
            logger.warning("grade: skipping item with unresolvable/invented id %r", key)
            continue
        article, registry_entry = pair

        if not _coerce_bool(item.get("bears_on_hypothesis")):
            logger.info(
                "grade: dropping off-topic abstract id=%s (bears_on_hypothesis=false) — "
                "never emitted as supporting or contradicting",
                key,
            )
            continue

        item = dict(item)
        _normalize_enum_field(item, "grade", _GRADE_VALUES)
        grade = item.get("grade")
        if grade not in _GRADE_VALUES:
            logger.warning("grade: skipping item %r with invalid grade %r", key, item.get("grade"))
            continue

        claim_fragment = str(item.get("claim_fragment") or "").strip() or (article.get("title") or "")[:200]
        supports = _coerce_bool(item.get("supports"))

        reference = Reference(
            source=registry_entry.source,
            title=registry_entry.title,
            year=int(registry_entry.year) if str(registry_entry.year or "").isdigit() else None,
            url=registry_entry.url,
            pmid=registry_entry.pmid,
            grade=grade,
        )
        evidence_item = EvidenceItem(
            claim_fragment=claim_fragment,
            reference=reference,
            supports=supports,
            entailment="unclear",  # provisional — real entailment pass is Phase 4 (rigor.py)
            grade=grade,
        )
        claim_dict = {
            "text": claim_fragment,
            "source": reference.source,
            "pmid": reference.pmid,
            "confidence": "moderate",
            "_key": key,  # internal correlation id -- validate_citations ignores unknown keys
        }
        candidates.append((key, evidence_item, claim_dict))

    if not candidates:
        return []

    response_data = {
        "references": [
            {
                "pmid": ev.reference.pmid,
                "url": ev.reference.url,
                "source": ev.reference.source,
                "title": ev.reference.title,
                "year": ev.reference.year,
                "_key": key,  # internal correlation id (see _resolution_key) -- NCT/DOI-only
                # items have reference.pmid=None, so `pmid` alone can't be the reconciliation
                # key below; validate_citations only reads/writes the standard fields above
                # and passes an unrecognized extra key straight through untouched.
            }
            for key, ev, _ in candidates
        ],
        "content_items": [claim for _, _, claim in candidates],
    }
    validate_citations(response_data, "evidence", fetched_data=fd)

    # Honor in-place mutations: drop any claim flagged __drop__ (generic,
    # correct handling — inert for query_type="evidence" today, see docstring).
    after_refs_by_key = {r["_key"]: r for r in response_data["references"]}

    kept: list[EvidenceItem] = []
    for key, evidence_item, claim_dict in candidates:
        if claim_dict.get("__drop__"):
            continue
        updated_ref = after_refs_by_key.get(key)
        if updated_ref is None:
            continue  # validate_citations removed it (e.g. unverified/hallucinated pmid)
        if updated_ref.get("url") != evidence_item.reference.url:
            # Propagate any URL neutralization (e.g. unsafe domain -> None) back onto the Reference.
            evidence_item = evidence_item.model_copy(
                update={"reference": evidence_item.reference.model_copy(update={"url": updated_ref.get("url")})}
            )
        # Mark the backing registry entry as actually-cited (Phase 5,
        # BUILD_SPEC.md §5 agent 8: "references via registry.to_reference_
        # list()") -- that method's own contract sorts cited entries first
        # via each RegistryArticle's `used_inline` flag, which is otherwise
        # never set anywhere in this codebase (this repo never calls
        # Iatronix's prompt-assembly path that normally sets it). Using
        # `resolvable[key][1]` (the exact RegistryArticle this evidence item
        # was resolved from, agent 4's own match) rather than re-deriving it
        # keeps this a single, correct source of truth for "was this article
        # actually used as evidence in the final brief".
        registry.mark_used(resolvable[key][1])
        kept.append(evidence_item)

    return kept


# ---------------------------------------------------------------------------
# Agent 7 — Experiment Designer (Sonnet, config.MODEL_REASONING)
# ---------------------------------------------------------------------------

# BUILD_SPEC.md §5's "Grounding rule for datasets" names the recognized hosts
# a `dataset_pointer` may resolve to (CELLxGENE / Tabula Sapiens, NCBI GEO,
# ArrayExpress) plus allows a bare DOI ("its URL/DOI"). Hostname-suffix
# match (exact or subdomain), https-only. Deliberately does NOT make a live
# network request to confirm the id truly resolves -- that would add real
# network flakiness/latency/cost to every experiment-design call (this repo
# runs live PubMed calls already; adding a second live dependency here for a
# structural check is the wrong trade). The guarantee this function provides
# is "well-formed and points at a real, recognized public dataset host";
# never-fabricate is additionally enforced by the FALLBACK behavior below
# (see :func:`run_design_experiment`), not by this function alone.
_RECOGNIZED_DATASET_HOSTS = (
    "cellxgene.cziscience.com",
    "tabula-sapiens-portal.ds.czbiohub.org",
    "ncbi.nlm.nih.gov",  # NCBI GEO, e.g. www.ncbi.nlm.nih.gov/geo/query/acc.cgi?acc=GSE...
    "ebi.ac.uk",  # ArrayExpress / BioStudies, e.g. www.ebi.ac.uk/biostudies/arrayexpress/studies/E-MTAB-...
    "doi.org",  # DOI resolver -- BUILD_SPEC.md §5: "dataset_pointer is its URL/DOI"
)


def _is_recognized_dataset_pointer(url: Any) -> bool:
    """True iff ``url`` is a well-formed ``https`` URL whose hostname is (or
    is a subdomain of) one of :data:`_RECOGNIZED_DATASET_HOSTS`. Never
    raises on malformed/non-string input — returns ``False``."""
    if not isinstance(url, str) or not url.strip():
        return False
    try:
        parsed = urlsplit(url.strip())
    except ValueError:
        return False
    if parsed.scheme != "https" or not parsed.netloc:
        return False
    host = (parsed.hostname or "").lower()
    return any(host == h or host.endswith(f".{h}") for h in _RECOGNIZED_DATASET_HOSTS)


_FALLBACK_FEASIBILITY_NOTE = (
    "Fell back to the pinned default substrate (Tabula Sapiens immune "
    "compartment) because the proposed dataset could not be verified to "
    "actually BE the dataset it claimed to be (BUILD_SPEC.md §5: never emit "
    "a fabricated/guessed accession; Phase 5/7 verify dataset_pointer "
    "actually resolves to a matching dataset, not merely to *a* record)."
)

# ---------------------------------------------------------------------------
# Dataset CONTENT verification (Opus verification finding, post-Phase-5-v1):
# a host-only / reachability-only check is NOT enough -- an accession can
# resolve (HTTP 200, recognized host) while describing a COMPLETELY
# DIFFERENT dataset than what the plan claims. Confirmed live, TWICE,
# independently: this repo's own earlier flagship run named GSE200257 as
# "Bulk RNA-seq of human adrenal cortex tissue... aldosterone-producing
# adenomas" when the REAL record is "Single-cell RNA-sequencing of blood and
# tonsillar CD4+ CD57+ and CD57- T cells"; Opus's verifier run named
# GSE200827 as "KPMP human kidney single-nucleus RNA-seq, ~50 donors, CKD
# 1-5" when the REAL record is "Gene expression profiles during the process
# of differentiation of HL-60 [leukemia] cells into neutrophils or
# eosinophils" (an expression-microarray SuperSeries, 16 samples). Neither
# was caught by a host-only check because both accessions genuinely
# resolve, on a genuinely recognized host. This section fetches the REAL GEO
# record and checks it is actually consistent with what the plan claims
# before ever accepting a model-named accession.
# ---------------------------------------------------------------------------

_GEO_ACCESSION_RE = re.compile(r"\bGSE\d+\b", re.IGNORECASE)
_GEO_SOFT_FIELD_RE = re.compile(r"^!(\w+)\s*=\s*(.*)$")
_GEO_QUICK_VIEW_URL = "https://www.ncbi.nlm.nih.gov/geo/query/acc.cgi?acc={acc}&targ=self&form=text&view=quick"
_GEO_FETCH_TIMEOUT = 10.0  # seconds -- bounded so a slow/hung NCBI call can never stall a run indefinitely


def _extract_geo_accession(dataset: str, dataset_pointer: str) -> Optional[str]:
    """Pulls a GSEnnn... accession out of ``dataset_pointer`` (preferred --
    it's the URL that will actually be used) or, failing that, ``dataset``
    (a model occasionally names the accession only in the dataset string).
    Returns ``None`` if neither contains a recognizable GEO Series accession
    (e.g. a CELLxGENE/Tabula Sapiens/ArrayExpress/DOI pointer -- full content
    verification for those hosts is out of scope here; see
    :func:`_verify_dataset_pointer`'s docstring for the documented scope
    boundary).
    """
    for text in (dataset_pointer, dataset):
        m = _GEO_ACCESSION_RE.search(text or "")
        if m:
            return m.group(0).upper()
    return None


def _parse_geo_soft_text(text: str) -> Optional[dict[str, list[str]]]:
    """Parses GEO SOFT ``!Field = value`` lines into ``{field: [values...]}``
    (a field, e.g. ``Series_overall_design`` or ``Series_type``, can
    legitimately repeat across multiple lines -- confirmed live for both).
    Returns ``None`` (unresolved) if the text carries no ``Series_title`` at
    all -- the confirmed-live signature of NCBI's HTML "GEO Accession
    viewer" error page for an accession that does not exist (status 200,
    but no SOFT fields at all), as opposed to a real record (which always
    has this field).
    """
    fields: dict[str, list[str]] = {}
    for line in (text or "").splitlines():
        m = _GEO_SOFT_FIELD_RE.match(line.strip())
        if m:
            fields.setdefault(m.group(1), []).append(m.group(2).strip())
    if not fields.get("Series_title"):
        return None
    return fields


async def _fetch_geo_soft_record(accession: str) -> Optional[dict[str, list[str]]]:
    """Fetches + parses the REAL GEO SOFT quick-view text record for
    ``accession`` -- a plain, unauthenticated NCBI GET (NOT an Iatronix
    call; this repo's own httpx dependency, per BUILD_SPEC.md §1). Appends
    ``config.PUBMED_API_KEY`` as ``&api_key=`` when set (raises NCBI rate
    limits; harmless no-op if this specific endpoint ignores it).

    Retries EXACTLY ONCE on a transient-looking failure (timeout/connection
    error) -- matching this codebase's established "transient infra blips
    get one retry, not silent unlimited retries, not zero retries either"
    philosophy (mirrors the test-level Anthropic-500 retry-once pattern) --
    then gives up and returns ``None``. Never raises: an unverifiable
    dataset is treated exactly like a confirmed-nonexistent one and falls
    back to the guaranteed-safe pinned default, which is the conservative,
    correct choice for a "never fabricate/mismatch" safety gate (better to
    over-trigger the safe fallback on a flaky NCBI blip than to ever accept
    an unverified accession).
    """
    url = _GEO_QUICK_VIEW_URL.format(acc=accession)
    if config.PUBMED_API_KEY:
        url += f"&api_key={config.PUBMED_API_KEY}"

    last_exc: Optional[BaseException] = None
    for attempt in range(2):  # 1 try + 1 retry
        try:
            async with httpx.AsyncClient(timeout=_GEO_FETCH_TIMEOUT) as client:
                response = await client.get(url)
            if response.status_code != 200:
                logger.warning("design_experiment: GEO fetch for %s returned HTTP %s", accession, response.status_code)
                return None  # a real HTTP error status is not transient -- do not retry
            return _parse_geo_soft_text(response.text)
        except (httpx.TimeoutException, httpx.TransportError) as exc:
            last_exc = exc
            if attempt == 0:
                await asyncio.sleep(0.5)
    logger.warning("design_experiment: GEO fetch for %s failed after retry: %s", accession, last_exc)
    return None


# Generic English/omics-boilerplate words filtered out before computing
# keyword overlap -- without this, "human gene expression profiling" alone
# would spuriously "match" ANY two unrelated GEO records, defeating the
# whole check. Calibrated against REAL fetched records: a true positive
# (GSE121862/kidney) and two confirmed-live true negatives (GSE200257/
# T-cells claimed-as-adrenal, GSE200827/HL-60 claimed-as-kidney) -- see
# tests/test_experiment_phase5.py.
_GEO_GENERIC_WORDS = frozenset(
    {
        "a", "an", "the", "and", "or", "but", "nor", "so", "yet", "as", "at", "by",
        "for", "from", "in", "into", "of", "on", "to", "with", "within", "across",
        "among", "between", "this", "that", "these", "those", "is", "are", "was",
        "were", "be", "been", "being", "has", "have", "had", "will", "would", "can",
        "could", "may", "might", "must", "shall", "should", "not", "no", "all",
        "each", "both", "either", "neither", "via", "using", "used", "based", "than",
        "human", "humans", "homo", "sapiens", "sapien", "gene", "genes", "genetic",
        "genomic", "genomics", "expression", "expressed", "profiling", "profile",
        "profiles", "analysis", "analyses", "study", "studies", "data", "dataset",
        "datasets", "sample", "samples", "sampling", "cell", "cells", "cellular",
        "tissue", "tissues", "sequencing", "sequence", "sequenced", "seq", "rna",
        "dna", "high", "throughput", "level", "levels", "type", "types", "series",
        "single", "bulk", "model", "models", "method", "methods", "approach",
        "result", "results", "experiment", "experiments", "experimental",
        "molecular", "biological", "clinical", "patient", "patients", "disease",
        "diseases", "condition", "conditions", "health", "healthy", "normal",
        "control", "controls", "group", "groups", "comparison", "compared",
        "differential", "differentially", "identify", "identified",
        "identification", "reveal", "revealed", "demonstrate", "demonstrated",
        "provide", "provides", "understand", "understanding", "publicly",
        "available", "public", "accession", "atlas", "cohort", "donor", "donors",
    }
)
_GEO_WORD_RE = re.compile(r"[A-Za-z][A-Za-z0-9\-]{2,}")


def _geo_content_words(text: str) -> set[str]:
    return {w.lower() for w in _GEO_WORD_RE.findall(text or "") if w.lower() not in _GEO_GENERIC_WORDS}


_MIN_SHARED_KEYWORDS = 2
_MIN_SHARED_KEYWORD_RATIO = 0.15


def _verify_geo_record_matches_claim(record: dict[str, list[str]], claimed_text: str) -> tuple[bool, str]:
    """Checks the REAL GEO record against what the plan CLAIMED about it.

    (a) Organism: at least one of ``Series_platform_organism``/
        ``Series_sample_organism`` (or, defensively, ANY field at all) must
        mention "Homo sapien[s]" (case-insensitive substring -- covers a
        genuine "Homo sapien" truncation observed live in one real record).
    (b) Keyword overlap: content words (English/omics-boilerplate filtered,
        :func:`_geo_content_words`) shared between ``claimed_text`` and the
        record's ``Series_title``/``Series_summary``/``Series_overall_
        design``/``Series_type`` must clear BOTH an absolute floor
        (:data:`_MIN_SHARED_KEYWORDS`) and a floor relative to how much
        claimed text there was (:data:`_MIN_SHARED_KEYWORD_RATIO`) -- cheap,
        explainable, and calibrated against real fetched records to catch
        an egregious organ/topic mismatch (e.g. a claimed kidney dataset
        that is really an HL-60 leukemia-cell-line microarray, or a claimed
        adrenal-cortex dataset that is really a T-cell immunology dataset)
        without requiring exact wording.

    Returns ``(True, "")`` if both pass, else ``(False, <human-readable
    reason, includes the real title for feasibility_notes/logging>)``.
    """
    organism_fields = record.get("Series_platform_organism", []) + record.get("Series_sample_organism", [])
    organism_text = " ".join(organism_fields) or " ".join(v for values in record.values() for v in values)
    if "homo sapien" not in organism_text.lower():
        real_title = (record.get("Series_title") or [""])[0]
        return False, f"organism could not be confirmed human (real record: {real_title!r})"

    real_text = " ".join(
        record.get("Series_title", [])
        + record.get("Series_summary", [])
        + record.get("Series_overall_design", [])
        + record.get("Series_type", [])
    )
    claimed_words = _geo_content_words(claimed_text)
    real_words = _geo_content_words(real_text)
    shared = claimed_words & real_words
    ratio = len(shared) / max(1, len(claimed_words))
    if len(shared) < _MIN_SHARED_KEYWORDS or ratio < _MIN_SHARED_KEYWORD_RATIO:
        real_title = (record.get("Series_title") or [""])[0]
        return False, (
            f"claimed dataset content does not match the real GEO record "
            f"(real title: {real_title!r}; shared keywords: {sorted(shared) or 'none'})"
        )
    return True, ""


async def _verify_dataset_pointer(dataset: str, dataset_pointer: Any, claimed_text: str) -> tuple[bool, str]:
    """The main verify-then-fallback gate for agent 7 (Opus verification
    finding, post-Phase-5-v1): checks not just that ``dataset_pointer`` is a
    well-formed URL to a recognized host (:func:`_is_recognized_dataset_
    pointer`, a fast pre-filter) but that it actually RESOLVES to a REAL
    record consistent with what the plan claims about it.

    GEO accessions (the common case -- BUILD_SPEC.md's own preferred
    example, and what this tool's flagship scenarios have converged on
    live) get FULL content verification via :func:`_fetch_geo_soft_record` +
    :func:`_verify_geo_record_matches_claim`.

    Non-GEO recognized hosts (CELLxGENE/Tabula Sapiens/ArrayExpress/DOI) get
    a bounded, DELIBERATELY LIGHTER reachability-only check (a plain HTTP GET
    that must return 200 with a non-trivial body). This repo has no per-host
    content-metadata parser for those APIs (a documented scope boundary, not
    an oversight) -- a live-reachable, recognized-host URL is the best
    available signal there.

    Returns ``(True, "")`` if verified, else ``(False, <human-readable
    reason>)``. Never raises -- any unexpected error is treated as
    "unverified" (fails closed, triggering the guaranteed-safe fallback).
    """
    if not dataset or not _is_recognized_dataset_pointer(dataset_pointer):
        return False, "dataset_pointer was missing or not a well-formed https URL to a recognized public dataset host"
    pointer = str(dataset_pointer).strip()

    accession = _extract_geo_accession(dataset, pointer)
    if accession is not None:
        record = await _fetch_geo_soft_record(accession)
        if record is None:
            return False, f"GEO accession {accession} does not resolve to a real record"
        return _verify_geo_record_matches_claim(record, claimed_text)

    # Non-GEO recognized host: reachability-only (see docstring scope note).
    last_exc: Optional[BaseException] = None
    for attempt in range(2):
        try:
            async with httpx.AsyncClient(timeout=_GEO_FETCH_TIMEOUT, follow_redirects=True) as client:
                response = await client.get(pointer)
            if response.status_code == 200 and len(response.content) > 0:
                return True, ""
            return False, f"dataset_pointer returned HTTP {response.status_code}"
        except (httpx.TimeoutException, httpx.TransportError) as exc:
            last_exc = exc
            if attempt == 0:
                await asyncio.sleep(0.5)
    return False, f"dataset_pointer was unreachable: {last_exc}"


# BUILD_SPEC.md §5's own VERBATIM Experiment Designer prompt text (frozen,
# reproduced character-for-character in EXPERIMENT_DESIGNER_SYSTEM_PROMPT)
# names "method"/"protocol_steps"/"confirm_if"/"refute_if"/
# "feasibility_notes"/"claude_science_prompt" explicitly in its own prose,
# but never spells out "question" (or "hypothesis_id") as a literal JSON key
# the way agents 1/4/6's prompts enumerate every field in a trailing "STRICT
# JSON {...}" clause -- it only says "STRICT JSON = ExperimentPlan." Observed
# live (real Sonnet call, flagship run): the model produced an otherwise
# excellent, fully-grounded, real-GEO-accession response but used "title"
# instead of "question" for the one field the prompt never names -- a
# reasonable synonym, not a malformed response. Per this codebase's
# established philosophy (`_normalize_enum_field` for casing, `_coerce_bool`
# for string-vs-bool, `bears_on_hypothesis` structural enforcement): handle
# a realistic LLM habit structurally in code rather than editing the frozen,
# spec-verbatim prompt text. Tried in priority order; first non-empty wins.
_QUESTION_FALLBACK_KEYS = ("question", "research_question", "study_question", "title", "experiment_title")


def _first_present_str(item: dict, keys: tuple[str, ...]) -> str:
    for key in keys:
        value = str(item.get(key) or "").strip()
        if value:
            return value
    return ""


def _design_experiment_user_content(
    candidate: Hypothesis,
    evidence: list[EvidenceItem],
    *,
    force_tabula_sapiens: bool,
    rejection_reason: str = "",
) -> str:
    """Builds agent 7's per-call user content. Shared by the model's free
    -choice attempt (``force_tabula_sapiens=False``) and the Tabula-Sapiens
    -LOCKED retry (``force_tabula_sapiens=True``) that :func:`run_design_
    experiment` makes when the free-choice attempt's dataset is rejected
    (either structurally unrecognized, or content-verified to be a
    MISMATCH) -- see that function's docstring for the full flow.
    """
    lines = [
        f"Hypothesis id: {candidate.id}",
        f"Axis: {candidate.axis}",
        f"Statement: {candidate.statement}",
        f"Prediction: {candidate.prediction}",
        f"Rationale: {candidate.rationale}",
    ]
    supporting = [ev for ev in evidence if ev.entailment == "supports"]
    if supporting:
        lines.append("Grounded supporting evidence (the basis for this hypothesis's eligibility):")
        for ev in supporting:
            cite = ev.reference.pmid or ev.reference.url or "unresolved"
            lines.append(f"- id={cite} grade={ev.grade}: {ev.claim_fragment}")
    else:
        lines.append("No supporting evidence items were provided (design conservatively).")

    if force_tabula_sapiens:
        lines.append(
            f"IMPORTANT: a dataset you previously proposed for this SAME hypothesis was "
            f"REJECTED because {rejection_reason}. For THIS response you MUST use the "
            f"pinned default substrate verbatim — do not propose any other dataset or "
            f"accession: dataset={config.DEFAULT_DATASET!r}, dataset_pointer="
            f"{config.DEFAULT_DATASET_POINTER!r}. Tabula Sapiens is a whole-body human "
            f"single-cell atlas spanning many compartments (kidney, immune, vascular, "
            f"endocrine, and others) — name the SPECIFIC compartment/cell-type population "
            f"within Tabula Sapiens most relevant to this hypothesis, and design "
            f"method/protocol_steps/confirm_if/refute_if/claude_science_prompt entirely "
            f"around analyzing that compartment of Tabula Sapiens. Never reference the "
            f"rejected dataset anywhere in your response."
        )
    else:
        lines.append(
            f"Pinned default fallback substrate — use verbatim as dataset/dataset_pointer if "
            f"you are not certain another named accession/atlas actually resolves AND "
            f"matches its own claimed content: dataset={config.DEFAULT_DATASET!r}, "
            f"dataset_pointer={config.DEFAULT_DATASET_POINTER!r}."
        )
    lines.append(f"Set \"hypothesis_id\" to exactly {candidate.id!r} in your JSON output.")
    # Defense-in-depth (this is per-call USER content, not the frozen system
    # prompt): spells out every required JSON key by name. The system
    # prompt's own BUILD_SPEC.md-verbatim text names most fields in prose
    # but never literally says "question" as a JSON key -- observed live to
    # make the model substitute a reasonable synonym ("title") for exactly
    # that one field. `question` is still parsed defensively either way
    # (:data:`_QUESTION_FALLBACK_KEYS`); this line just reduces how often
    # that fallback is needed at all, for this and any other field.
    lines.append(
        "Your STRICT JSON response must use exactly these top-level keys: "
        '"hypothesis_id", "question", "dataset", "dataset_pointer", "method", '
        '"protocol_steps", "confirm_if", "refute_if", "feasibility_notes", '
        '"claude_science_prompt".'
    )
    return "\n".join(lines)


def _parse_design_experiment_fields(item: dict) -> dict[str, Any]:
    """Extracts + normalizes the ExperimentPlan-shaped fields out of ONE
    parsed LLM response dict -- shared by the free-choice attempt and the
    Tabula-Sapiens-locked retry in :func:`run_design_experiment`, so both go
    through identical extraction logic. Returns a plain dict (not yet an
    ``ExperimentPlan``) keyed exactly by the schema's own field names, plus
    ``dataset_description`` (an extra, non-schema field observed live --
    some models add it; harmless, and useful signal for dataset-content
    verification, see :func:`run_design_experiment`).
    """
    protocol_steps_raw = item.get("protocol_steps")
    protocol_steps = (
        [str(s).strip() for s in protocol_steps_raw if str(s).strip()]
        if isinstance(protocol_steps_raw, list)
        else []
    )
    return {
        "dataset": str(item.get("dataset") or "").strip(),
        "dataset_pointer": item.get("dataset_pointer"),
        "dataset_description": str(item.get("dataset_description") or "").strip(),
        "feasibility_notes": str(item.get("feasibility_notes") or "").strip(),
        "question": _first_present_str(item, _QUESTION_FALLBACK_KEYS),
        "method": str(item.get("method") or "").strip(),
        "protocol_steps": protocol_steps,
        "confirm_if": str(item.get("confirm_if") or "").strip(),
        "refute_if": str(item.get("refute_if") or "").strip(),
        "claude_science_prompt": str(item.get("claude_science_prompt") or "").strip(),
    }


async def run_design_experiment(candidate: Hypothesis, evidence: list[EvidenceItem], llm) -> ExperimentPlan:
    """Agent 7 — Experiment Designer (BUILD_SPEC.md §5, §8;
    ``EXPERIMENT_DESIGNER_SYSTEM_PROMPT``). Designs a single computational
    experiment to confirm/refute ``candidate`` — the hypothesis
    :func:`transbench.rigor.select_experiment_candidate` already picked
    (already ``open_question`` AND ``grounded``; this function is only ever
    invoked by the caller for an ELIGIBLE candidate — BUILD_SPEC.md §5 agent
    7: "for the top open_question+grounded hypothesis" — it does not
    re-check eligibility itself).

    Only ``entailment=="supports"`` items from ``evidence`` are shown to the
    model — the grounded evidence that actually motivated this candidate's
    eligibility (contradicting/unclear items are legitimate context for
    grading/novelty, but showing them here would invite the model to hedge
    or design around evidence that isn't what grounded this hypothesis).

    Framing (BUILD_SPEC.md §0.5 / this task): the prompt already forbids
    clinical claims/wet-lab-only designs; this function adds no patient
    -directed language of its own — ``question``/``confirm_if``/
    ``refute_if`` are always about the EXPERIMENTAL criterion (does the data
    confirm or refute the mechanistic claim), never a recommendation to
    treat, prescribe, or act on any specific patient.

    Verify-then-fallback for datasets (BUILD_SPEC.md §5/§8, Opus
    verification finding — a host-only check is NOT enough, see
    :func:`_verify_dataset_pointer`'s module comment for the two confirmed
    -live mismatches that motivated this): makes ONE free-choice Sonnet
    call, then:
      1. If ``dataset`` is empty or ``dataset_pointer`` fails the fast
         structural pre-filter (:func:`_is_recognized_dataset_pointer`) —
         REJECT immediately, no network call needed.
      2. Else, ``await`` :func:`_verify_dataset_pointer` — for a GEO
         accession, fetches the REAL record and checks it is actually
         consistent with what the plan claims (organism + keyword overlap);
         for a non-GEO recognized host, a reachability-only check (see that
         function's documented scope boundary).
      3. If EITHER check rejects, makes a SECOND Sonnet call with
         ``force_tabula_sapiens=True`` (:func:`_design_experiment_user_
         content`) — explicitly told the prior dataset was rejected (and
         why) and REQUIRED to use the pinned default, asked to name the
         MOST RELEVANT Tabula Sapiens compartment for this hypothesis and
         design method/protocol_steps/confirm_if/refute_if/claude_science_
         prompt entirely around it. ``dataset``/``dataset_pointer`` are then
         HARD-OVERRIDDEN to ``config.DEFAULT_DATASET``/``config.
         DEFAULT_DATASET_POINTER`` regardless of what this retry itself
         returns for those two fields specifically — the retry is NEVER
         trusted a second time to name its own accession, only to write a
         coherent, hypothesis-tailored protocol around the pinned,
         guaranteed-resolvable substrate. This is how the rejected
         accession can never leak into the FINAL ``claude_science_prompt``/
         ``protocol_steps``/``method`` (the prior version of this function
         only rewrote ``dataset``/``dataset_pointer``/``feasibility_notes``
         on fallback, silently leaving stale, now-wrong accession-specific
         protocol text behind — fixed here).

    This can NEVER produce an unverified ``dataset_pointer``: either the
    model's own is independently confirmed to both resolve AND match its
    claimed content, or the guaranteed-resolvable pinned default is used
    (and everything else rewritten to match it).

    ``ExperimentPlan`` (BUILD_SPEC.md §4, frozen) has no ``Literal``/enum
    -valued field, so ``_normalize_enum_field`` has nothing to normalize for
    this agent's output (checked against the schema directly — this is a
    deliberate no-op, not an oversight).

    ``hypothesis_id`` in the returned ``ExperimentPlan`` is ALWAYS
    ``candidate.id`` — never trusted from the model's own JSON echo (the
    same "never trust an id round-tripped through free-form generation"
    discipline ``run_grade``/``rigor.run_entailment`` already apply to
    citation ids).

    Raises :class:`TransBenchLLMError` if the (possibly-retried) response is
    missing any other required field (``question``/``method``/
    ``protocol_steps``/``confirm_if``/``refute_if``/``claude_science_
    prompt``) — unlike ``run_assemble``'s cosmetic ``uncertainty_note``, a
    genuinely broken experiment design must surface loudly (this is the
    tool's "money moment" output, BUILD_SPEC.md §8), not be silently patched
    over.
    """
    user_content = _design_experiment_user_content(candidate, evidence, force_tabula_sapiens=False)
    parsed = await _ainvoke_json(llm, EXPERIMENT_DESIGNER_SYSTEM_PROMPT, user_content)
    item = _coerce_dict(parsed, preferred_key="ExperimentPlan")
    fields = _parse_design_experiment_fields(item)

    rejection_reason: Optional[str] = None
    if not fields["dataset"] or not _is_recognized_dataset_pointer(fields["dataset_pointer"]):
        rejection_reason = (
            "its dataset_pointer was missing or not a well-formed https URL to a "
            "recognized public dataset host"
        )
    else:
        claimed_text = " ".join(
            filter(
                None,
                [
                    fields["dataset"],
                    fields["dataset_description"],
                    fields["method"],
                    fields["feasibility_notes"],
                    fields["question"],
                    candidate.statement,
                    candidate.prediction,
                ],
            )
        )
        verified, reason = await _verify_dataset_pointer(
            fields["dataset"], fields["dataset_pointer"], claimed_text
        )
        if not verified:
            rejection_reason = reason

    if rejection_reason is not None:
        logger.warning(
            "design_experiment: rejecting model-proposed dataset for hypothesis %s "
            "(dataset=%r dataset_pointer=%r): %s -- retrying with the pinned default "
            "substrate forced",
            candidate.id,
            fields["dataset"] or None,
            fields["dataset_pointer"],
            rejection_reason,
        )
        retry_user_content = _design_experiment_user_content(
            candidate, evidence, force_tabula_sapiens=True, rejection_reason=rejection_reason
        )
        retry_parsed = await _ainvoke_json(llm, EXPERIMENT_DESIGNER_SYSTEM_PROMPT, retry_user_content)
        retry_item = _coerce_dict(retry_parsed, preferred_key="ExperimentPlan")
        fields = _parse_design_experiment_fields(retry_item)
        # HARD-enforced regardless of what the retry itself returns for
        # these two fields -- the whole point is to NEVER trust a second
        # unverified guess; only the pinned, guaranteed-resolvable default
        # is ever used once the free-choice attempt has been rejected.
        fields["dataset"] = config.DEFAULT_DATASET
        fields["dataset_pointer"] = config.DEFAULT_DATASET_POINTER
        fields["feasibility_notes"] = (
            f"{fields['feasibility_notes']} {_FALLBACK_FEASIBILITY_NOTE} "
            f"(Original proposal rejected: {rejection_reason}.)"
        ).strip()
    else:
        fields["dataset_pointer"] = str(fields["dataset_pointer"]).strip()

    required = {
        "question": fields["question"],
        "method": fields["method"],
        "protocol_steps": fields["protocol_steps"],
        "confirm_if": fields["confirm_if"],
        "refute_if": fields["refute_if"],
        "claude_science_prompt": fields["claude_science_prompt"],
    }
    missing = [name for name, value in required.items() if not value]
    if missing:
        raise TransBenchLLMError(
            502,
            "llm_bad_output",
            f"Experiment designer response missing required field(s) {missing} "
            f"(after {'a Tabula-Sapiens-forced retry' if rejection_reason else 'the first attempt'}): {fields!r}",
        )

    try:
        return ExperimentPlan(
            hypothesis_id=candidate.id,
            question=fields["question"],
            dataset=fields["dataset"],
            dataset_pointer=fields["dataset_pointer"],
            method=fields["method"],
            protocol_steps=fields["protocol_steps"],
            confirm_if=fields["confirm_if"],
            refute_if=fields["refute_if"],
            feasibility_notes=fields["feasibility_notes"] or _FALLBACK_FEASIBILITY_NOTE,
            claude_science_prompt=fields["claude_science_prompt"],
        )
    except ValidationError as exc:
        raise TransBenchLLMError(
            502, "llm_bad_output", f"Experiment designer produced an invalid ExperimentPlan: {exc}"
        ) from exc


# ---------------------------------------------------------------------------
# Agent 8 — Brief Assembler (Haiku, config.MODEL_CHEAP)
# ---------------------------------------------------------------------------

# BUILD_SPEC.md §4's TransBrief.top_experiment is a REQUIRED ExperimentPlan
# (no Optional/default) -- schemas.py is frozen verbatim from the spec and
# must never be changed to work around this. When
# rigor.select_experiment_candidate returns None (every hypothesis this run
# was either 'established' or ungrounded), run_assemble substitutes this
# explicit, honestly-labeled SENTINEL ExperimentPlan instead -- schema-valid,
# never a fabricated experiment, and its own prose says exactly why no real
# design was produced so a reader (or the MCP client / Claude Science) can
# never mistake it for a real recommendation.
_NO_CANDIDATE_HYPOTHESIS_ID = "none"


def _no_eligible_experiment_plan() -> ExperimentPlan:
    return ExperimentPlan(
        hypothesis_id=_NO_CANDIDATE_HYPOTHESIS_ID,
        question=(
            "No experiment was designed this run: no hypothesis was both "
            "'open_question' and grounded (BUILD_SPEC.md §6(3) novelty "
            "guard) -- every generated hypothesis was either judged "
            "'established' (a textbook fact, not novel) or had zero "
            "grounded supporting evidence."
        ),
        dataset=config.DEFAULT_DATASET,
        dataset_pointer=config.DEFAULT_DATASET_POINTER,
        method="N/A — no eligible hypothesis this run.",
        protocol_steps=[
            "N/A — no eligible hypothesis this run. Re-run with a different "
            "observation, or review each hypothesis's own novelty/grounded "
            "fields below to see why none qualified."
        ],
        confirm_if="N/A — no experiment was designed this run.",
        refute_if="N/A — no experiment was designed this run.",
        feasibility_notes=(
            "No hypothesis satisfied the novelty guard (open_question AND "
            "grounded) this run, so no dataset/protocol was designed. See "
            "each hypothesis's own novelty/novelty_reason/grounded fields "
            "for why."
        ),
        claude_science_prompt="N/A — no experiment was designed this run.",
    )


def _build_references(
    graded_hypotheses: list[GradedHypothesis], registry_by_hyp_id: dict[str, Any]
) -> list[Reference]:
    """BUILD_SPEC.md §5 agent 8: "references via registry.to_reference_
    list()". Each hypothesis retrieved (and built its own registry)
    independently (BUILD_SPEC.md §3 fans retrieval out per-hypothesis), so
    this merges every hypothesis's own ``ArticleRegistry.to_reference_
    list()`` output, in hypothesis order, deduped by pmid (falling back to
    url when pmid is absent -- an NCT/DOI-only item; the registry's own
    "hard URL guarantee" means url is always present). Within EACH
    hypothesis's own list, cited entries sort first (Iatronix's own
    ``to_reference_list()`` contract) -- real, not merely requested, because
    ``run_grade`` (agent 4, Phase 3/5) now calls ``registry.mark_used(...)``
    for every article that actually became a kept ``EvidenceItem``.

    Attaches ``grade`` from a matching graded ``EvidenceItem`` when one
    exists across ANY hypothesis (the registry itself carries no grade --
    that is agent 4's per-claim output, not a registry-level property) --
    ``None`` for a retrieved-but-never-cited registry entry.
    """
    grade_by_key: dict[str, str] = {}
    for gh in graded_hypotheses:
        for ev in gh.evidence:
            key = ev.reference.pmid or ev.reference.url
            if key:
                grade_by_key.setdefault(key, ev.grade)

    seen: set[str] = set()
    references: list[Reference] = []
    for gh in graded_hypotheses:
        registry = (registry_by_hyp_id or {}).get(gh.hypothesis.id)
        if registry is None:
            continue
        for entry in registry.to_reference_list():
            pmid = entry.get("pmid")
            url = entry.get("url")
            dedup_key = str(pmid) if pmid else str(url or "")
            if not dedup_key or dedup_key in seen:
                continue
            seen.add(dedup_key)
            year_raw = entry.get("year")
            year = int(year_raw) if str(year_raw or "").isdigit() else None
            references.append(
                Reference(
                    source=str(entry.get("source") or "Unknown"),
                    title=entry.get("title"),
                    year=year,
                    url=url,
                    pmid=str(pmid) if pmid else None,
                    grade=grade_by_key.get(dedup_key),
                )
            )
    return references


def _collect_contradictions(graded_hypotheses: list[GradedHypothesis]) -> list[str]:
    """BUILD_SPEC.md §5 agent 8: "collect contradictions" -- one entry per
    ``EvidenceItem`` whose dedicated entailment pass (``rigor.run_entailment``,
    agent 6) classified ``"refutes"``, across EVERY hypothesis (not only the
    selected experiment candidate) -- a contradiction surfaced anywhere in
    the run is reported, per BUILD_SPEC.md §0.6 ("grounded or it doesn't
    ship") / §6(3) (auditability).
    """
    contradictions: list[str] = []
    for gh in graded_hypotheses:
        for ev in gh.evidence:
            if ev.entailment == "refutes":
                cite = ev.reference.pmid or ev.reference.url or "unresolved citation"
                contradictions.append(f"[{gh.hypothesis.id}] {ev.claim_fragment} (grade={ev.grade}, {cite})")
    return contradictions


def _fallback_uncertainty_note(graded_hypotheses: list[GradedHypothesis], contradictions: list[str]) -> str:
    """Deterministic, computed ``uncertainty_note`` used when agent 8's own
    Haiku call fails or returns unusable JSON (see :func:`run_assemble`) --
    still honest and specific (never a generic placeholder), built entirely
    from already-real, already-validated pipeline output."""
    established = sum(1 for gh in graded_hypotheses if gh.novelty == "established")
    open_q = sum(1 for gh in graded_hypotheses if gh.novelty == "open_question")
    unsupported = sum(1 for gh in graded_hypotheses if gh.novelty == "unsupported")
    ungrounded = sum(1 for gh in graded_hypotheses if not gh.grounded)

    parts = [
        f"Of {len(graded_hypotheses)} generated hypothes{'is' if len(graded_hypotheses) == 1 else 'es'}, "
        f"{open_q} were classified open_question, {established} established (textbook, not novel), "
        f"and {unsupported} unsupported by retrieved evidence."
    ]
    if ungrounded:
        parts.append(
            f"{ungrounded} hypothesis(es) had zero grounded supporting evidence and were "
            f"excluded from experiment design."
        )
    if contradictions:
        parts.append(
            f"{len(contradictions)} contradicting evidence item(s) were surfaced during retrieval "
            f"and should be weighed against any supporting evidence."
        )
    parts.append("All findings are preliminary and require expert review before any downstream use.")
    return " ".join(parts)


async def run_assemble(state: dict, llm) -> TransBrief:
    """Agent 8 — Brief Assembler (BUILD_SPEC.md §5/§8; Haiku,
    ``config.MODEL_CHEAP``). Assembles the REAL final ``TransBrief`` from the
    full pipeline's already-computed, real output (agents 1-7).

    ``state`` keys read (all via ``.get`` with safe defaults — this function
    never assumes a specific caller type; ``graph.py``'s ``TransBenchState``
    TypedDict is a plain ``dict`` at runtime and satisfies this contract
    directly, which is why the parameter is a plain ``dict`` rather than a
    bespoke dataclass):
      ``observation``, ``focus_drug``, ``axes``, ``graded_hypotheses``
      (``list[GradedHypothesis]``, already built by ``graph.py``'s
      ``_design_node``), ``top_experiment`` (an ``ExperimentPlan`` or
      ``None``), ``registry_by_hyp_id``, ``retrieval_manifest_by_hyp_id``,
      ``model_reasoning``, ``model_cheap``, ``max_hypotheses``,
      ``retrieval_snapshot`` (the run's INPUT snapshot, if any),
      ``run_started_at``.

    Builds, in pure code (no LLM): ``references`` (:func:`_build_references`
    — every hypothesis's ``registry.to_reference_list()``, merged + deduped)
    and ``contradictions_surfaced`` (:func:`_collect_contradictions` — every
    ``entailment=="refutes"`` item across all hypotheses). Makes exactly ONE
    Haiku call (``BRIEF_ASSEMBLER_SYSTEM_PROMPT``) for the single prose
    field, ``uncertainty_note`` — if that ONE call raises OR returns no
    usable text, this is caught broadly and logged (never propagated), and
    :func:`_fallback_uncertainty_note` computes a deterministic, still
    -honest replacement instead. Rationale (long-term, not a shortcut): by
    the time assembly runs, the pipeline has already made ~10+ real,
    expensive, successfully-completed LLM calls (decompose, hypothesize,
    every hypothesis's grade + entailment + novelty, and design) — discarding
    that entire real ``TransBrief`` because the LAST, purely-cosmetic
    summary sentence hit a transient JSON hiccup would be a strictly worse
    failure mode than a computed-but-honest fallback sentence; the failure
    is still logged, so it remains observable, never silently hidden.

    ``TransBrief.top_experiment`` (BUILD_SPEC.md §4, frozen) is a REQUIRED
    field, never ``Optional`` — when ``state["top_experiment"]`` is ``None``
    (no hypothesis cleared the novelty guard this run), this substitutes
    :func:`_no_eligible_experiment_plan`'s explicit sentinel
    (``hypothesis_id="none"``) rather than fabricating a fake design or
    breaking schema validation.

    ``run_manifest`` is filled with models/temperature/caps/concurrency/
    ``focus_drug``/the run's resolved ``condition_anchor`` (Phase 8, domain
    -universalization — the real PubMed retrieval anchor every hypothesis's
    query shared this run, for auditability; may legitimately be ``""`` if
    neither the decomposer nor the heuristic fallback found one)/whether a
    ``retrieval_snapshot`` was supplied on input/the per-hypothesis
    retrieval snapshot actually captured this run (neutral + pubmed queries
    and full abstracts, i.e. PMIDs, BUILD_SPEC.md §9)/the selected
    experiment's hypothesis id and ``dataset_pointer``/``run_started_at``/
    ``generated_at``/:func:`current_token_spend`'s running total (already
    includes this function's own ``uncertainty_note`` call, since that call
    happens before ``run_manifest`` is built below).
    """
    observation = state.get("observation", "")
    focus_drug = state.get("focus_drug")
    axes = state.get("axes") or []
    graded_hypotheses: list[GradedHypothesis] = state.get("graded_hypotheses") or []
    top_experiment: Optional[ExperimentPlan] = state.get("top_experiment")
    registry_by_hyp_id = state.get("registry_by_hyp_id") or {}
    retrieval_manifest_by_hyp_id = state.get("retrieval_manifest_by_hyp_id") or {}

    references = _build_references(graded_hypotheses, registry_by_hyp_id)
    contradictions = _collect_contradictions(graded_hypotheses)

    user_lines = ["Graded hypotheses:"]
    for gh in graded_hypotheses:
        h = gh.hypothesis
        user_lines.append(
            f"- {h.id} [{h.axis}] novelty={gh.novelty} confidence={gh.confidence} "
            f"grounded={gh.grounded} supporting={gh.supporting_count} "
            f"contradicting={gh.contradicting_count}: {h.statement}"
        )
        user_lines.append(f"  novelty_reason: {gh.novelty_reason}")
    if contradictions:
        user_lines.append("Contradictions surfaced during retrieval:")
        for c in contradictions:
            user_lines.append(f"- {c}")
    else:
        user_lines.append("No contradicting evidence was surfaced during retrieval.")
    if top_experiment is not None:
        user_lines.append(f"An experiment was designed for hypothesis {top_experiment.hypothesis_id}.")
    else:
        user_lines.append("No hypothesis was eligible for experiment design this run.")
    user_content = "\n".join(user_lines)

    uncertainty_note = ""
    try:
        parsed = await _ainvoke_json(llm, BRIEF_ASSEMBLER_SYSTEM_PROMPT, user_content)
        item = _coerce_dict(parsed)
        uncertainty_note = str(item.get("uncertainty_note") or "").strip()
    except Exception:  # noqa: BLE001 -- deliberate: see run_assemble's docstring rationale
        logger.exception(
            "run_assemble: uncertainty_note LLM call failed or returned unusable JSON -- "
            "falling back to a deterministic, computed note rather than discarding an "
            "otherwise-complete, already-expensive brief"
        )
    if not uncertainty_note:
        uncertainty_note = _fallback_uncertainty_note(graded_hypotheses, contradictions)

    if top_experiment is None:
        top_experiment = _no_eligible_experiment_plan()

    generated_at = _dt.datetime.now(_dt.timezone.utc).isoformat()
    run_manifest: dict[str, Any] = {
        "reuse_source": REUSE_SOURCE,
        "model_reasoning": state.get("model_reasoning", config.MODEL_REASONING),
        "model_deep": state.get("model_deep", config.MODEL_DEEP),
        "model_cheap": state.get("model_cheap", config.MODEL_CHEAP),
        "temperature": config.TEMPERATURE,
        "max_hypotheses": state.get("max_hypotheses", config.MAX_HYPOTHESES),
        "abstract_cap": config.ABSTRACT_CAP,
        "concurrency": config.CONCURRENCY,
        "focus_drug": focus_drug,
        "condition_anchor": state.get("condition_anchor") or "",
        "retrieval_snapshot_provided": state.get("retrieval_snapshot") is not None,
        "retrieval_snapshot": retrieval_manifest_by_hyp_id,
        "selected_experiment_hypothesis_id": top_experiment.hypothesis_id,
        "dataset_pointer": top_experiment.dataset_pointer,
        "run_started_at": state.get("run_started_at"),
        "generated_at": generated_at,
        "token_spend": current_token_spend(),
    }

    return TransBrief(
        request_echo=observation,
        axes=axes,
        hypotheses=graded_hypotheses,
        top_experiment=top_experiment,
        references=references,
        contradictions_surfaced=contradictions,
        uncertainty_note=uncertainty_note,
        run_manifest=run_manifest,
    )
