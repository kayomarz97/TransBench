"""Grounding gate — guarantees the RENDERED answer is evidence-grounded.

A medical answer must be backed by retrieved sources, never the model's training
knowledge. After all citation post-processing (token resolution, backfill,
quarantine) this module:

  1. ``strip_ungrounded`` — removes content items that have no real source
     (``"Expert opinion"``, generic fallbacks, empty, unresolved token) and drops
     any section left with no claims.
  2. ``grounding_stats`` / ``grounded_ratio`` — measure how much of the answer is
     backed by real sources, for the floor decision and audit logging.

These are **pure functions** (no I/O), so behaviour is identical across the
streaming and non-streaming pipelines and is safe under horizontal scaling.

Design note: a claim is grounded if it carries a resolvable identifier
(PMID / NCT / DOI / URL) **or** names a *specific* real source. Generic or
sourceless labels are treated as ungrounded — this is what stops training-data
claims (and NA-fill laundering like ``"Medical literature"``) from being shown.
"""
from __future__ import annotations

import logging

logger = logging.getLogger(__name__)

# Source labels that do NOT constitute grounding on their own. Lower-cased.
# Includes the coerced "Expert opinion" default, consensus variants, the
# NA-fill generic fallbacks, and the unresolved-token sentinel.
GENERIC_OR_UNGROUNDED: frozenset[str] = frozenset(
    {
        "",
        "expert opinion",
        "expert consensus",
        "clinical consensus",
        "clinical guidelines",
        "medical literature",
        "n/a",
        "na",
        "n.a.",
        "not available",
        "unknown",
        "none",
        "__unresolved_token__",
    }
)


def _is_grounded(item: object) -> bool:
    """True iff a content item is backed by a real, attributable source."""
    if not isinstance(item, dict):
        return False
    # A resolvable identifier or URL is definitive grounding.
    if item.get("pmid") or item.get("nct_id") or item.get("doi") or item.get("url"):
        return True
    # Otherwise a *specific* named source counts; generic/sourceless does not.
    source = (item.get("source") or "").strip().lower()
    return bool(source) and source not in GENERIC_OR_UNGROUNDED


def _sections(response: dict) -> list:
    secs = response.get("sections")
    return secs if isinstance(secs, list) else []


def grounding_stats(response: dict) -> tuple[int, int]:
    """Return ``(grounded_claims, total_claims)`` across all section content items."""
    grounded = 0
    total = 0
    for sec in _sections(response):
        if not isinstance(sec, dict):
            continue
        for item in sec.get("content_items", []) or []:
            total += 1
            if _is_grounded(item):
                grounded += 1
    return grounded, total


def grounded_ratio(response: dict) -> float:
    grounded, total = grounding_stats(response)
    return (grounded / total) if total else 0.0


def strip_ungrounded(response: dict) -> int:
    """Remove ungrounded content items in place; drop sections left empty.

    Returns the number of claims removed. Sections that had no content items to
    begin with (e.g. prose-only) are preserved untouched.
    """
    removed = 0
    kept_sections: list = []
    for sec in _sections(response):
        if not isinstance(sec, dict):
            kept_sections.append(sec)
            continue
        items = sec.get("content_items", []) or []
        if not items:
            kept_sections.append(sec)
            continue
        grounded_items = [it for it in items if _is_grounded(it)]
        removed += len(items) - len(grounded_items)
        if grounded_items:
            sec["content_items"] = grounded_items
            kept_sections.append(sec)
        # else: every claim in this section was ungrounded → drop the section
    response["sections"] = kept_sections
    return removed
