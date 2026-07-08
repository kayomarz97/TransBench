"""agents.py — the 8 agents (BUILD_SPEC.md §5). Phase 2 implements agents 1-2
(Decomposer, Hypothesis Generator) — the first REAL LLM calls in this repo.
Agents 3-8 land in Phases 3-5.

Every agent follows the same shape: ``async run_<name>(payload: dict, llm) -> ...``
(BUILD_SPEC.md §5). ``llm`` is always a PRE-BUILT, temperature-0-bound client —
callers (``graph.py``) construct it via :func:`build_llm`, choosing
``config.MODEL_CHEAP`` (mechanical agents) or ``config.MODEL_REASONING``
(reasoning agents) per BUILD_SPEC.md §5. Agent functions never call
``create_llm`` themselves.
"""
from __future__ import annotations

import json
import logging
import re
from typing import Any, Optional, get_args

import json_repair
from fastapi import HTTPException
from langchain_core.messages import HumanMessage, SystemMessage
from pydantic import ValidationError

from transbench import config
from transbench.prompts import (
    DECOMPOSER_SYSTEM_PROMPT,
    HYPOTHESIS_GENERATOR_SYSTEM_PROMPT,
)
from transbench.reuse import create_llm
from transbench.schemas import Axis, DecomposedAxis, Hypothesis, Priority

logger = logging.getLogger(__name__)

__all__ = [
    "TransBenchLLMError",
    "build_llm",
    "run_decompose",
    "run_hypothesize",
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


def build_llm(model_id: str, user_key: Optional[str], user_provider: str = "anthropic"):
    """Build a temperature-0 LangChain chat client for ``model_id``
    (BUILD_SPEC.md §5/§0.7 — call this ONCE per agent invocation, then reuse
    the returned client for that call).

    ``.bind(temperature=0)`` is REQUIRED here, chained immediately onto
    ``create_llm(...)``: setting ``LLM_TEMPERATURE=0`` in-process
    (``config.py``) is belt #1 but is only a *default* (``os.environ.
    setdefault``) — it does NOT retroactively correct an operator's own
    pre-exported non-zero ``LLM_TEMPERATURE``. ``.bind(temperature=0)``
    (belt #2) is what actually guarantees ``temperature=0`` is sent on every
    request regardless of ambient env (BUILD_SPEC.md §0.7).

    Raises:
        TransBenchLLMError: if ``create_llm`` raises ``fastapi.HTTPException``
            (missing/invalid key → 402/401; unsupported provider/model → 400;
            BUILD_SPEC.md §0.4).
    """
    try:
        return create_llm(model_id, user_key=user_key, user_provider=user_provider).bind(temperature=0)
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


# Valid values derived directly from the schema's own Literal definitions —
# single source of truth, never a duplicated string list that could drift.
_AXIS_VALUES = frozenset(get_args(Axis))
_PRIORITY_VALUES = frozenset(get_args(Priority))


def _normalize_enum_field(item: dict, field: str, valid_values: frozenset[str]) -> None:
    """Case-insensitively canonicalize ``item[field]`` to the schema's exact
    Literal casing, in place, IF a case-insensitive match exists.

    Long-term-fix rationale (not a one-off patch): confirmed empirically on
    the live flagship run that Sonnet returns ``"priority": "HIGH"`` /
    ``"MEDIUM"`` despite the STRICT-JSON prompt listing the field name in
    quotes — nothing in BUILD_SPEC.md §5's prompt text pins the exact value
    casing, and models are not perfectly obedient to an implicit lowercase-
    enum convention. Rejecting semantically-perfect hypotheses over pure
    casing would be fragile. This is a genuine *normalization* (schema-
    derived, case-insensitive exact match only) — not a guess: if there is
    no case-insensitive match at all, the value is left untouched and
    Pydantic's own validation rejects it with its normal clear error (so a
    truly invalid value, e.g. ``"axis": "cardiac"``, still fails loudly
    rather than being silently coerced to something plausible-looking).
    Reused by every future agent with an enum-valued LLM output field
    (grade/novelty/entailment/confidence, Phases 3-4) via the same helper.
    """
    value = item.get(field)
    if isinstance(value, str) and value not in valid_values:
        lowered = value.strip().lower()
        if lowered in valid_values:
            item[field] = lowered


async def _ainvoke_json(llm, system_prompt: str, user_content: str) -> Any:
    """Call the LLM with a system+user message pair and parse the response as
    JSON. Always ``await``s ``llm.ainvoke(...)`` — NEVER the blocking
    ``llm.invoke()`` (BUILD_SPEC.md §5: "Never call llm.invoke() in the async
    path", it would block the event loop)."""
    response = await llm.ainvoke(
        [SystemMessage(content=system_prompt), HumanMessage(content=user_content)]
    )
    return _parse_json(_response_text(response))


# ---------------------------------------------------------------------------
# Agent 1 — Decomposer (Haiku, config.MODEL_CHEAP)
# ---------------------------------------------------------------------------


async def run_decompose(payload: dict, llm) -> list[DecomposedAxis]:
    """Agent 1 — Decomposer (BUILD_SPEC.md §5). Splits a clinical observation
    about antihypertensive drugs into distinct biological axes.

    payload keys:
        observation (str, required): the clinical observation to decompose.
        focus_drug (str | None, optional).

    Returns only axes the observation actually motivates (never all 7). Items
    that fail schema validation are logged and skipped rather than failing
    the whole batch; if NOTHING validates, raises :class:`TransBenchLLMError`
    (zero axes is never treated as a silent success).
    """
    observation = payload["observation"]
    focus_drug = payload.get("focus_drug")

    user_content = f"Observation: {observation}"
    if focus_drug:
        user_content += f"\nFocus drug: {focus_drug}"

    parsed = await _ainvoke_json(llm, DECOMPOSER_SYSTEM_PROMPT, user_content)
    raw_axes = _coerce_list(parsed, preferred_key="axes")

    axes: list[DecomposedAxis] = []
    for item in raw_axes:
        if not isinstance(item, dict):
            logger.warning("decompose: skipping non-dict axis item %r", item)
            continue
        item = dict(item)
        _normalize_enum_field(item, "axis", _AXIS_VALUES)
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
    return axes


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
        _normalize_enum_field(item, "axis", _AXIS_VALUES)
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
