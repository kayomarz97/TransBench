"""agents.py — the 8 agents (BUILD_SPEC.md §5). Phase 2 implemented agents 1-2
(Decomposer, Hypothesis Generator). Phase 3 (this file, today) adds agents 3-4
(Evidence Retriever — no LLM; Evidence Grader — Haiku, batched). Agents 5-8
land in Phase 4-5.

Agents 1-2 follow the shape ``async run_<name>(payload: dict, llm) -> ...``
(BUILD_SPEC.md §5), taking a PRE-BUILT, temperature-0-bound client. Agents 3-4
have genuinely different contracts per BUILD_SPEC.md §3/§5 (retrieval has no
LLM at all; grading builds its own client from ``user_key`` since it needs
the registry/ranked-articles output of retrieval first) — see
:func:`run_retrieve` / :func:`run_grade` docstrings.
"""
from __future__ import annotations

import json
import logging
import re
from dataclasses import dataclass, field
from typing import Any, Optional, get_args

import json_repair
from fastapi import HTTPException
from langchain_core.messages import HumanMessage, SystemMessage
from pydantic import ValidationError

from transbench import config
from transbench.prompts import (
    DECOMPOSER_SYSTEM_PROMPT,
    EVIDENCE_GRADER_SYSTEM_PROMPT,
    HYPOTHESIS_GENERATOR_SYSTEM_PROMPT,
)
from transbench.reuse import (
    EvidenceFetchResult,
    EvidenceFloorError,
    FetchedData,
    build_article_registry,
    create_llm,
    ensure_evidence,
    fetch_evidence_data,
    has_minimum_evidence,
    neutralize_query,
    rank_article_list,
    validate_citations,
)
from transbench.schemas import (
    Axis,
    DecomposedAxis,
    EvidenceGrade,
    EvidenceItem,
    Hypothesis,
    Priority,
    Reference,
)

logger = logging.getLogger(__name__)

__all__ = [
    "TransBenchLLMError",
    "RetrievalResult",
    "build_llm",
    "run_decompose",
    "run_hypothesize",
    "run_retrieve",
    "run_grade",
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
_GRADE_VALUES = frozenset(get_args(EvidenceGrade))


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
    }
)
_WORD_RE = re.compile(r"[A-Za-z0-9][A-Za-z0-9\-]*")


def _shorten_for_pubmed(text: str, max_words: int = 6) -> str:
    """Derive a short, keyword-style PubMed query from a full sentence.

    Load-bearing fix, empirically validated during Phase 3 development.
    BUILD_SPEC.md §3's literal pseudocode passes the full ``neutral_
    clinical_question`` sentence directly into ``fetch_evidence_data``, but
    PubMed's ``[Title/Abstract]`` search is literal keyword/phrase matching,
    not semantic search: a full mechanistic-hypothesis SENTENCE (or
    ``neutralize_query``'s own rephrasing of one, which stays sentence-
    length) reliably returned ZERO PubMed hits in testing against 3 real
    flagship-style hypothesis statements. A short (~6-word), stopword- and
    hyphenated-compound-stripped query over the SAME underlying concepts
    reliably returned real, on-topic hits (2-3/3 same hypotheses, incl. a
    hit literally titled "Aldosterone breakthrough during therapy with
    angiotensin-converting enzyme inhibitors..." for the aldosterone-
    breakthrough hypothesis). Hyphenated compounds are stripped because
    hypothesis statements coin novel descriptive terms ("RAAS-resistant",
    "non-ACE-dependent", "effector-memory") that — almost by definition,
    since BUILD_SPEC.md §5 explicitly asks for genuinely OPEN/novel
    hypotheses — won't appear verbatim in EXISTING published abstracts; the
    plain nouns naming the actual biological entities (T cell, IL-17,
    aldosterone, WNK4, hypertension, ...) are what real papers use, and
    those are what this function keeps. A hypothesis about a genuinely
    sparse/novel gene combination can still legitimately return nothing —
    handled gracefully by ``run_retrieve``, not a bug in this function.

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


async def run_retrieve(
    hypothesis: Hypothesis,
    user_key: Optional[str],
    *,
    user_provider: str = "anthropic",
    model_id: Optional[str] = None,
) -> RetrievalResult:
    """Agent 3 — Evidence Retriever (BUILD_SPEC.md §3; no LLM call is made
    directly by this function — ``neutralize_query`` makes its own internal,
    self-contained Haiku call with its own 800ms timeout + heuristic
    fallback, so from this function's perspective it is just an async data
    transform).

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
       Empirically (measured against 3 real flagship-style hypothesis
       statements during Phase 3 development), this reliably returns ZERO
       (support pass) or IRRELEVANT (contradiction pass — e.g. multiple
       myeloma trials for a hypertension/IL-17 hypothesis) results, because
       (a) PubMed's ``[Title/Abstract]`` search is literal keyword matching,
       not semantic search, so long synthesized sentences rarely match
       verbatim, and (b) ``fetch_evidence_data`` wraps whatever string it's
       given as ``f"{query}[Title/Abstract] AND (...)"`` — a bare trailing
       ``OR`` breaks that field-tag's intended scope. Fix: derive
       ``pubmed_query = _shorten_for_pubmed(neutral)`` (short, keyword-style,
       deterministic, no LLM — see its docstring) and use it everywhere §3
       uses ``neutral`` for an actual PubMed call; PROPERLY parenthesize the
       contradiction-pass OR-group: ``f"{pubmed_query} (limitations OR
       negative OR no association)"``. The TRUE ``neutral_clinical_question``
       is still what's returned as ``RetrievalResult.neutral_query`` (used
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
    stance = await neutralize_query(
        hypothesis.statement, model_id or config.MODEL_CHEAP, user_key, user_provider
    )
    neutral = stance.neutral_clinical_question
    pubmed_query = _shorten_for_pubmed(neutral)

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


def _article_prompt_block(article: dict) -> str:
    pmid = article.get("pmid")
    year = article.get("year")
    title = (article.get("title") or "").strip()
    abstract = (article.get("abstract") or "").strip()[:1200]
    return f"- pmid={pmid} year={year}\n  title: {title}\n  abstract: {abstract}"


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
         ``registry.by_pmid[str(pmid)]``; drop any article with no registry
         match (no resolvable citation) — done HERE, not in agent 3, per
         BUILD_SPEC.md §5's division of labor.
      2. ONE batched Haiku call over all resolvable abstracts (never one call
         per abstract) -> ``supports``/``grade``/``claim_fragment`` per pmid.
         ``grade`` is case-insensitively normalized via the same
         schema-derived :func:`_normalize_enum_field` helper Phase 2 added.
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

    Never invents a pmid that wasn't in ``ranked``. Returns ``[]`` (not an
    error) if ``ranked`` is empty or nothing resolves/validates/grades —
    zero evidence for a hypothesis is a valid outcome, handled by the (future)
    rigor gate, not a crash here.
    """
    if not ranked:
        return []

    resolvable: dict[str, tuple[dict, Any]] = {}
    registry_by_pmid = getattr(registry, "by_pmid", {}) or {}
    for article in ranked:
        pmid = article.get("pmid")
        if not pmid:
            continue
        pmid = str(pmid)
        if pmid in resolvable:
            continue  # dedupe (support + contradiction passes can overlap)
        registry_entry = registry_by_pmid.get(pmid)
        if registry_entry is None:
            continue  # no resolvable citation — drop (BUILD_SPEC §5 agent 4)
        resolvable[pmid] = (article, registry_entry)

    if not resolvable:
        return []

    llm = build_llm(model_id or config.MODEL_CHEAP, user_key, user_provider)
    user_content = "\n".join(
        [
            f"Hypothesis: {hypothesis.statement}",
            f"Prediction: {hypothesis.prediction}",
            "Abstracts:",
            *[_article_prompt_block(article) for article, _ in resolvable.values()],
        ]
    )
    parsed = await _ainvoke_json(llm, EVIDENCE_GRADER_SYSTEM_PROMPT, user_content)
    raw_items = _coerce_list(parsed, preferred_key="items")

    candidates: list[tuple[EvidenceItem, dict]] = []
    for item in raw_items:
        if not isinstance(item, dict):
            logger.warning("grade: skipping non-dict grading item %r", item)
            continue
        pmid = str(item.get("pmid", ""))
        pair = resolvable.get(pmid)
        if pair is None:
            logger.warning("grade: skipping item with unresolvable/invented pmid %r", pmid)
            continue
        article, registry_entry = pair

        item = dict(item)
        _normalize_enum_field(item, "grade", _GRADE_VALUES)
        grade = item.get("grade")
        if grade not in _GRADE_VALUES:
            logger.warning("grade: skipping item %r with invalid grade %r", pmid, item.get("grade"))
            continue

        claim_fragment = str(item.get("claim_fragment") or "").strip() or (article.get("title") or "")[:200]
        supports = bool(item.get("supports"))

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
        }
        candidates.append((evidence_item, claim_dict))

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
            }
            for ev, _ in candidates
        ],
        "content_items": [claim for _, claim in candidates],
    }
    validate_citations(response_data, "evidence", fetched_data=fd)

    # Honor in-place mutations: drop any claim flagged __drop__ (generic,
    # correct handling — inert for query_type="evidence" today, see docstring).
    after_refs_by_pmid = {r.get("pmid"): r for r in response_data["references"] if r.get("pmid")}

    kept: list[EvidenceItem] = []
    for evidence_item, claim_dict in candidates:
        if claim_dict.get("__drop__"):
            continue
        pmid = evidence_item.reference.pmid
        updated_ref = after_refs_by_pmid.get(pmid)
        if updated_ref is None:
            continue  # validate_citations removed it (e.g. unverified/hallucinated pmid)
        if updated_ref.get("url") != evidence_item.reference.url:
            # Propagate any URL neutralization (e.g. unsafe domain -> None) back onto the Reference.
            evidence_item = evidence_item.model_copy(
                update={"reference": evidence_item.reference.model_copy(update={"url": updated_ref.get("url")})}
            )
        kept.append(evidence_item)

    return kept
