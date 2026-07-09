"""vendored — self-contained copy of the Iatronix leaf functions TransBench reuses.

This package is `reuse.py`'s **Path B** (BUILD_SPEC.md §2): when the live Iatronix
backend is NOT importable (`from app.services... import ...` raises ImportError —
e.g. a fresh clone that has no `/root/projects/med-ai-project`), `reuse.py` falls
back to `from vendored import (...)`, and this module supplies the identical
symbols. That makes TransBench self-hostable with nothing but a clone + a BYOK
`ANTHROPIC_API_KEY` in `.env` (no external path dependency).

PROVENANCE / SYNC: `vendored/app/**` is a **verbatim mirror** of
`med-ai-project/backend/app/**` — the exact runtime closure of the reused
functions (18 files), with only their internal `app.` imports mechanically
rewritten to `vendored.app.`. It is NOT hand-edited logic and must not be
reimplemented: Iatronix stays the source of truth. If Iatronix changes those
files, re-vendor (copy + rewrite imports) rather than patching here. See
`scratchpad-vendoring-plan.md` for the exact closure + rewrite procedure.

This `__init__` re-exports the 17 names `reuse.py`'s `except ImportError` block
imports — same names, same modules, so Path A (`installed_iatronix`) and Path B
(`vendored`) are behaviorally identical to every caller.
"""
from __future__ import annotations

# data_fetcher — evidence/drug retrieval + the shared async httpx client lifecycle
from vendored.app.services.data_fetcher import (
    EvidenceFetchResult,
    FetchedData,
    fetch_drug_data,
    fetch_evidence_data,
    init_http_client,
    shutdown_http_client,
)

# evidence_floor — minimum-evidence gating
from vendored.app.services.evidence_floor import (
    EvidenceFloorError,
    ensure_evidence,
    has_minimum_evidence,
)

# article_registry — PMID/citation registry construction
from vendored.app.services.article_registry import build_article_registry

# ranking — relevance ranking of raw abstract dicts
from vendored.app.services.ranking import rank_article_list

# grounding_gate — grounded-ratio stats + ungrounded-claim stripping
from vendored.app.services.grounding_gate import (
    grounded_ratio,
    grounding_stats,
    strip_ungrounded,
)

# llm_factory — the BYOK Anthropic client factory (create_llm(..., user_key=...))
from vendored.app.services.llm_factory import create_llm

# citation_validator — citation validation
from vendored.app.services.citation_validator import validate_citations

# stance_neutralizer — neutralize a stance-laden query before retrieval
from vendored.app.services.stance_neutralizer import neutralize_query

__all__ = [
    "EvidenceFetchResult",
    "FetchedData",
    "fetch_drug_data",
    "fetch_evidence_data",
    "init_http_client",
    "shutdown_http_client",
    "EvidenceFloorError",
    "ensure_evidence",
    "has_minimum_evidence",
    "build_article_registry",
    "rank_article_list",
    "grounded_ratio",
    "grounding_stats",
    "strip_ungrounded",
    "create_llm",
    "validate_citations",
    "neutralize_query",
]
