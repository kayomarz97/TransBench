"""reuse.py — the SINGLE seam between TransBench and Iatronix (BUILD_SPEC.md §2).

Path A (locked in Phase 0, see PLAN.md): the Iatronix backend is installed
editable + ``--no-deps`` into this repo's own venv, so ``from app.services...
import ...`` resolves to the live, read-only Iatronix source tree. Path B
(vendoring) is the fallback below — inert today (no ``vendored/`` package
exists in this repo; Python only evaluates the ``except`` body if the ``try``
raises ``ImportError``) — kept for spec fidelity and as the seam that absorbs
a future Iatronix breakage without touching any other file in this repo.

Only DB-free leaf functions/containers are imported here — see KICKOFF.md
non-negotiable rule 3 / BUILD_SPEC.md §0.3. ``run_search_graph``,
``semantic_cache``, and ``vector_search`` are NEVER imported anywhere in this
repo (they require pgvector/redis).

Two contract gotchas every caller of this module MUST honor (BUILD_SPEC.md
§2, confirmed by reading Iatronix source in Phase 0/Phase −1):
1. ``has_minimum_evidence``, ``ensure_evidence``, and ``build_article_registry``
   all consume a ``FetchedData`` container, NOT the raw ``EvidenceFetchResult``
   that ``fetch_evidence_data`` returns. Always wrap first:
   ``fd = FetchedData(query_type="evidence", evidence_data=result)``.
2. ``rank_article_list`` needs the raw abstract ``dict`` objects (they carry
   ``abstract``/``pub_types``/``year``/``pmid``), not
   ``ArticleRegistry.to_reference_list()`` output.
"""
from __future__ import annotations

try:
    from app.services.article_registry import build_article_registry
    from app.services.citation_validator import validate_citations
    from app.services.data_fetcher import (
        EvidenceFetchResult,
        FetchedData,
        fetch_drug_data,
        fetch_evidence_data,
        init_http_client,
        shutdown_http_client,
    )
    from app.services.evidence_floor import (
        EvidenceFloorError,
        ensure_evidence,
        has_minimum_evidence,
    )
    from app.services.grounding_gate import (
        grounded_ratio,
        grounding_stats,
        strip_ungrounded,
    )
    from app.services.llm_factory import create_llm
    from app.services.provider_registry import get_registry
    from app.services.ranking import rank_article_list
    from app.services.stance_neutralizer import neutralize_query

    REUSE_SOURCE = "installed_iatronix"
except ImportError:
    from vendored import (  # type: ignore[import-not-found]  # noqa: F401
        EvidenceFetchResult,
        EvidenceFloorError,
        FetchedData,
        build_article_registry,
        create_llm,
        ensure_evidence,
        fetch_drug_data,
        fetch_evidence_data,
        grounded_ratio,
        grounding_stats,
        has_minimum_evidence,
        init_http_client,
        neutralize_query,
        rank_article_list,
        shutdown_http_client,
        strip_ungrounded,
        validate_citations,
    )
    from vendored.app.services.provider_registry import get_registry  # type: ignore[import-not-found]

    REUSE_SOURCE = "vendored"


def model_supports_temperature(model_id: str) -> bool:
    """True unless ``model_id``'s registry meta sets ``supports_temperature:
    false`` (e.g. Claude Opus 4.8, whose Anthropic API returns HTTP 400 on a
    ``temperature`` request param). Reads ``model_meta`` (present on BOTH the
    installed-Iatronix and vendored registries) rather than a registry method,
    so the flag resolves regardless of the active reuse path. Fail-open: if the
    registry cannot answer, assume temperature is accepted, so this can never
    newly break a model that already worked."""
    try:
        meta = get_registry().model_meta(model_id) or {}
        val = meta.get("supports_temperature")
        return True if val is None else bool(val)
    except Exception:  # pragma: no cover -- a registry hiccup must not disable LLM calls
        return True


__all__ = [
    "REUSE_SOURCE",
    "model_supports_temperature",
    # data_fetcher
    "EvidenceFetchResult",
    "FetchedData",
    "fetch_drug_data",
    "fetch_evidence_data",
    "init_http_client",
    "shutdown_http_client",
    # article_registry
    "build_article_registry",
    # ranking
    "rank_article_list",
    # grounding_gate
    "grounding_stats",
    "strip_ungrounded",
    "grounded_ratio",
    # evidence_floor
    "has_minimum_evidence",
    "ensure_evidence",
    "EvidenceFloorError",
    # citation_validator
    "validate_citations",
    # llm_factory
    "create_llm",
    # stance_neutralizer
    "neutralize_query",
]
