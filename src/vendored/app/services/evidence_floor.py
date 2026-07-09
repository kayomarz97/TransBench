"""Evidence Floor — guarantees at least one citable source before LLM synthesis.

Called from _expand_retrieval_if_needed when the main 3-pass retrieval returns
insufficient evidence. Tries up to 5 progressive broadening strategies, each
with a 2.5 s timeout, total wall-clock bounded to ~12.5 s.

Returns FetchedData with fallback_to_llm=False, or raises EvidenceFloorError
which must be caught in process_query() to return the structured no_evidence response.
"""
from __future__ import annotations

import asyncio
import logging
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from vendored.app.services.data_fetcher import FetchedData

logger = logging.getLogger(__name__)

_PER_ATTEMPT_TIMEOUT = 2.5  # seconds per strategy


class EvidenceFloorError(Exception):
    """All evidence strategies exhausted — no citable source found for this query."""


# ── Minimum evidence check ────────────────────────────────────────────────────


def _abstract_list_has_url_bearing(abstracts: list) -> bool:
    """True iff any abstract dict has a pmid, nct_id, or doi that can form a URL."""
    return any(
        bool(a.get("pmid") or a.get("nct_id") or a.get("doi"))
        for a in (abstracts or [])
        if isinstance(a, dict)
    )


def has_minimum_evidence(fetched_data: "FetchedData | None") -> bool:
    """True iff fetched_data contains at least one item with a resolvable article URL.

    Checks all sub-result types that produce URL-bearing identifiers (PMID, NCT ID,
    DOI, NICE URL, FDA label URL). Returns False for None or empty data.
    """
    if fetched_data is None:
        return False

    if fetched_data.drug_data:
        d = fetched_data.drug_data
        for lst in (
            d.guideline_abstracts,
            d.systematic_review_abstracts,
            d.clinical_trial_abstracts,
        ):
            if _abstract_list_has_url_bearing(lst):
                return True
        if d.label_url:
            return True

    for disease in (fetched_data.disease_data, fetched_data.condition_data):
        if disease:
            for lst in (disease.guideline_abstracts, disease.systematic_review_abstracts):
                if _abstract_list_has_url_bearing(lst):
                    return True
            if any(r.get("url") for r in (disease.nice_recommendations or [])):
                return True

    if fetched_data.procedure_data:
        p = fetched_data.procedure_data
        for lst in (p.guideline_abstracts, p.practice_guideline_abstracts):
            if _abstract_list_has_url_bearing(lst):
                return True

    for ev in (fetched_data.evidence_data, fetched_data.comparative_evidence):
        if ev:
            for lst in (
                ev.clinical_trial_abstracts,
                ev.systematic_review_abstracts,
                ev.guideline_abstracts,
            ):
                if _abstract_list_has_url_bearing(lst):
                    return True

    for drug_entry in fetched_data.comparative_drug_data or []:
        for attr in ("guideline_abstracts", "systematic_review_abstracts", "clinical_trial_abstracts"):
            if _abstract_list_has_url_bearing(getattr(drug_entry, attr, [])):
                return True

    for comorbidity in fetched_data.comorbidity_data or []:
        for lst in (
            getattr(comorbidity, "guideline_abstracts", []),
            getattr(comorbidity, "systematic_review_abstracts", []),
        ):
            if _abstract_list_has_url_bearing(lst):
                return True

    return False


# ── Merge helpers ─────────────────────────────────────────────────────────────


def _merge_evidence_result(existing, new_result) -> object:
    """Merge a new EvidenceFetchResult into an existing one (or return new if None)."""
    if existing is None:
        return new_result
    for attr in ("clinical_trial_abstracts", "systematic_review_abstracts", "guideline_abstracts"):
        existing_list = getattr(existing, attr, []) or []
        new_list = getattr(new_result, attr, []) or []
        setattr(existing, attr, existing_list + new_list)
    return existing


def _merge_disease_result(existing, new_result) -> object:
    """Merge a new DiseaseFetchResult into existing (or return new if None)."""
    if existing is None:
        return new_result
    for attr in ("guideline_abstracts", "systematic_review_abstracts"):
        existing_list = getattr(existing, attr, []) or []
        new_list = getattr(new_result, attr, []) or []
        setattr(existing, attr, existing_list + new_list)
    if not getattr(existing, "nice_recommendations", None):
        existing.nice_recommendations = getattr(new_result, "nice_recommendations", [])
    return existing


# ── Core function ─────────────────────────────────────────────────────────────


async def ensure_evidence(
    fetched_data: "FetchedData",
    query: str,
    query_type: str,
) -> "FetchedData":
    """Try up to 5 progressive broadening strategies to guarantee ≥1 citable source.

    Strategies (each capped at 2.5 s):
      1. Broad evidence search with original query
      2. Simplified query (first 3 tokens only)
      3. Disease-type fetch — includes MedlinePlus + NCBI Books + NICE
      4. Drug-type fetch — includes openFDA/DailyMed label URL
      5. Single first keyword — most permissive PubMed search

    Returns fetched_data with fallback_to_llm=False on success.
    Raises EvidenceFloorError when all strategies are exhausted.
    """
    from vendored.app.config import settings
    from vendored.app.services.data_fetcher import (
        fetch_disease_data,
        fetch_drug_data,
        fetch_evidence_data,
    )

    if not settings.evidence_floor_enabled:
        fetched_data.fallback_to_llm = False
        logger.debug("evidence_floor: disabled — skipping (fallback_to_llm cleared)")
        return fetched_data

    if has_minimum_evidence(fetched_data):
        fetched_data.fallback_to_llm = False
        return fetched_data

    logger.info(
        "evidence_floor: entering for query=%r type=%s",
        query[:80],
        query_type,
    )

    tokens = query.split()
    simplified = " ".join(tokens[:3])
    main_term = " ".join(tokens[:4])
    first_word = tokens[0] if tokens else query

    # Strategy 1: broad evidence search with original query
    try:
        r1 = await asyncio.wait_for(fetch_evidence_data(query), timeout=_PER_ATTEMPT_TIMEOUT)
        if r1.fetch_success:
            fetched_data.evidence_data = _merge_evidence_result(fetched_data.evidence_data, r1)
            if has_minimum_evidence(fetched_data):
                logger.info("evidence_floor: strategy 1 (broad evidence) succeeded")
                fetched_data.fallback_to_llm = False
                return fetched_data
    except (asyncio.TimeoutError, Exception) as exc:
        logger.debug("evidence_floor: strategy 1 failed: %s", exc)

    # Strategy 2: simplified query (first 3 tokens)
    if simplified != query and len(simplified) > 3:
        try:
            r2 = await asyncio.wait_for(
                fetch_evidence_data(simplified), timeout=_PER_ATTEMPT_TIMEOUT
            )
            if r2.fetch_success:
                fetched_data.evidence_data = _merge_evidence_result(fetched_data.evidence_data, r2)
                if has_minimum_evidence(fetched_data):
                    logger.info("evidence_floor: strategy 2 (simplified query) succeeded")
                    fetched_data.fallback_to_llm = False
                    return fetched_data
        except (asyncio.TimeoutError, Exception) as exc:
            logger.debug("evidence_floor: strategy 2 failed: %s", exc)

    # Strategy 3: disease-type fetch (NCBI Bookshelf + NICE + MedlinePlus + PubMed)
    try:
        r3 = await asyncio.wait_for(
            fetch_disease_data(main_term), timeout=_PER_ATTEMPT_TIMEOUT
        )
        if r3.fetch_success:
            fetched_data.disease_data = _merge_disease_result(fetched_data.disease_data, r3)
            if has_minimum_evidence(fetched_data):
                logger.info("evidence_floor: strategy 3 (disease/NCBI/NICE fetch) succeeded")
                fetched_data.fallback_to_llm = False
                return fetched_data
    except (asyncio.TimeoutError, Exception) as exc:
        logger.debug("evidence_floor: strategy 3 failed: %s", exc)

    # Strategy 4: drug-type fetch (openFDA/DailyMed — produces label_url)
    try:
        r4 = await asyncio.wait_for(
            fetch_drug_data(main_term), timeout=_PER_ATTEMPT_TIMEOUT
        )
        if r4.fetch_success and r4.label_url:
            if fetched_data.drug_data is None:
                fetched_data.drug_data = r4
            if has_minimum_evidence(fetched_data):
                logger.info("evidence_floor: strategy 4 (drug/FDA fetch) succeeded")
                fetched_data.fallback_to_llm = False
                return fetched_data
    except (asyncio.TimeoutError, Exception) as exc:
        logger.debug("evidence_floor: strategy 4 failed: %s", exc)

    # Strategy 5: single first keyword — most permissive PubMed search
    if len(first_word) > 3 and first_word.lower() != simplified.lower():
        try:
            r5 = await asyncio.wait_for(
                fetch_evidence_data(first_word), timeout=_PER_ATTEMPT_TIMEOUT
            )
            if r5.fetch_success:
                fetched_data.evidence_data = _merge_evidence_result(fetched_data.evidence_data, r5)
                if has_minimum_evidence(fetched_data):
                    logger.info("evidence_floor: strategy 5 (single keyword) succeeded")
                    fetched_data.fallback_to_llm = False
                    return fetched_data
        except (asyncio.TimeoutError, Exception) as exc:
            logger.debug("evidence_floor: strategy 5 failed: %s", exc)

    logger.warning(
        "evidence_floor: all strategies exhausted for query=%r — raising EvidenceFloorError",
        query[:80],
    )
    raise EvidenceFloorError(
        f"No citable sources found across PubMed, NCBI Bookshelf, MedlinePlus, "
        f"openFDA, and NICE for query: {query[:100]!r}"
    )
