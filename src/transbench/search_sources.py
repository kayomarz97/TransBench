"""search_sources.py — extra literature backends (Phase 9, multi-database).

The reused Iatronix ``fetch_evidence_data`` is a CLINICAL-evidence fetcher
(PubMed clinical filters + ClinicalTrials.gov), so it under-retrieves basic
*mechanism* papers — the cell-biology literature (T-cell exhaustion: TCF7/TOX,
TGF-β/SMAD; etc.) a mechanistic hypothesis needs to be grounded. These backends
broaden grounding to the general scientific literature:

- **Europe PMC** — full-text, relevance-ranked search over MEDLINE + PMC +
  preprints; no API key. The primary new source (measured: finds the exact
  exhaustion/CAR-T mechanism papers PubMed keyword-AND misses).
- **Semantic Scholar** — semantic relevance ranking; OPTIONAL, gated on
  ``SEMANTIC_SCHOLAR_API_KEY`` (its unauthenticated pool returns HTTP 429 almost
  immediately, so it is skipped entirely without a key).

Both return abstract ``dict``s in the SAME shape the Iatronix pipeline consumes
— ``rank_article_list`` / ``build_article_registry`` read ``pmid``/``doi``/
``pmcid``/``title``/``abstract``/``year``/``pub_types`` via ``.get`` — so results
merge straight into an ``EvidenceFetchResult`` bucket and inherit the existing
rank → grade → ground → cite pipeline. A paper with a ``pmid`` and no ``nct_id``
is registered with ``source_type="pubmed"`` (a valid PubMed URL); ``build_
article_registry`` also resolves ``doi``-only preprints.

Never raises: any network/parse error (or a disabled/keyless source) yields
``[]`` — retrieval must never crash on an optional backend.
"""
from __future__ import annotations

import asyncio
import html
import logging
import os
import re
from typing import Any

import httpx

logger = logging.getLogger("transbench.search_sources")

_TIMEOUT: float = float(os.environ.get("SEARCH_SOURCE_TIMEOUT", "20"))
_TAG_RE = re.compile(r"<[^>]+>")


def _clean(text: str) -> str:
    """Strip HTML tags/entities Europe PMC leaves in titles/abstracts
    (e.g. ``CD8&lt;sup&gt;+&lt;/sup&gt;`` -> ``CD8+``)."""
    return _TAG_RE.sub("", html.unescape(text or "")).strip()


def _enabled(source: str) -> bool:
    """A source is on unless ``TRANSBENCH_ENABLE_<SOURCE>`` is explicitly falsey."""
    return os.environ.get(f"TRANSBENCH_ENABLE_{source.upper()}", "1").strip().lower() not in ("0", "false", "no", "off", "")


async def fetch_europepmc(query: str, retmax: int = 15) -> list[dict[str, Any]]:
    """Relevance-ranked Europe PMC search -> abstract dicts (pipeline shape).
    No API key. Keeps only results with a resolvable id (pmid or doi)."""
    if not query or not _enabled("europepmc"):
        return []
    try:
        async with httpx.AsyncClient(timeout=_TIMEOUT) as client:
            resp = await client.get(
                "https://www.ebi.ac.uk/europepmc/webservices/rest/search",
                params={
                    "query": query,
                    "format": "json",
                    "pageSize": retmax,
                    "resultType": "core",
                    # NB: Europe PMC's DEFAULT sort is already relevance; passing
                    # sort="relevance" is invalid syntax and silently returns 0
                    # results (HTTP 200, empty) — so we omit sort entirely.
                },
            )
            resp.raise_for_status()
            results = (resp.json().get("resultList") or {}).get("result") or []
    except Exception:  # noqa: BLE001 -- optional backend; a failure must yield [] not crash retrieval
        logger.debug("europepmc fetch failed for %r", query, exc_info=True)
        return []

    out: list[dict[str, Any]] = []
    for a in results:
        pmid = str(a.get("pmid") or "").strip() or None
        doi = str(a.get("doi") or "").strip() or None
        title = _clean(a.get("title") or "")
        if not title or not (pmid or doi):
            continue  # need a title + a resolvable id to cite/ground
        journal = ((a.get("journalInfo") or {}).get("journal") or {}).get("title")
        pub_types = (a.get("pubTypeList") or {}).get("pubType") or []
        out.append({
            "pmid": pmid,
            "doi": doi,
            "pmcid": a.get("pmcid"),
            "nct_id": None,
            "title": title,
            "abstract": _clean(a.get("abstractText") or ""),
            "year": a.get("pubYear"),
            "journal": journal if isinstance(journal, str) and journal else "Europe PMC",
            "source": "Europe PMC",
            "pub_types": pub_types if isinstance(pub_types, list) else [],
            "source_db": "europepmc",
        })
    return out


async def fetch_semantic_scholar(query: str, retmax: int = 15) -> list[dict[str, Any]]:
    """Semantic Scholar relevance search -> abstract dicts. OPTIONAL: requires
    ``SEMANTIC_SCHOLAR_API_KEY`` (the keyless pool 429s immediately). Graceful
    ``[]`` on missing key / 429 / any error."""
    key = os.environ.get("SEMANTIC_SCHOLAR_API_KEY", "").strip()
    if not query or not _enabled("semantic_scholar") or not key:
        return []
    try:
        async with httpx.AsyncClient(timeout=_TIMEOUT) as client:
            resp = await client.get(
                "https://api.semanticscholar.org/graph/v1/paper/search",
                params={
                    "query": query,
                    "limit": retmax,
                    "fields": "title,abstract,year,venue,externalIds,citationCount,publicationTypes",
                },
                headers={"x-api-key": key},
            )
            if resp.status_code == 429:
                logger.debug("semantic_scholar rate-limited (429) for %r", query)
                return []
            resp.raise_for_status()
            data = resp.json().get("data") or []
    except Exception:  # noqa: BLE001 -- optional backend; never crash retrieval
        logger.debug("semantic_scholar fetch failed for %r", query, exc_info=True)
        return []

    out: list[dict[str, Any]] = []
    for a in data:
        ext = a.get("externalIds") or {}
        pmid = str(ext.get("PubMed") or "").strip() or None
        doi = str(ext.get("DOI") or "").strip() or None
        title = _clean(a.get("title") or "")
        if not title or not (pmid or doi):
            continue
        out.append({
            "pmid": pmid,
            "doi": doi,
            "pmcid": None,
            "nct_id": None,
            "title": title,
            "abstract": _clean(a.get("abstract") or ""),
            "year": a.get("year"),
            "journal": a.get("venue") or "Semantic Scholar",
            "source": "Semantic Scholar",
            "pub_types": a.get("publicationTypes") or [],
            "citation_count": a.get("citationCount"),
            "source_db": "semantic_scholar",
        })
    return out


async def gather_extra_sources(query: str, retmax: int = 15) -> list[dict[str, Any]]:
    """Europe PMC + Semantic Scholar for ONE query, concurrently, deduped by
    pmid (else doi). EXTRA sources only — the caller still runs the Iatronix
    ``fetch_evidence_data`` itself. Never raises (failed sources contribute
    nothing)."""
    if not query:
        return []
    results = await asyncio.gather(
        fetch_europepmc(query, retmax),
        fetch_semantic_scholar(query, retmax),
        return_exceptions=True,
    )
    merged: list[dict[str, Any]] = []
    seen: set[str] = set()
    for res in results:
        if isinstance(res, BaseException) or not res:
            continue
        for a in res:
            key = (str(a.get("pmid") or "").strip() or str(a.get("doi") or "").strip().lower())
            if key and key in seen:
                continue
            if key:
                seen.add(key)
            merged.append(a)
    return merged
