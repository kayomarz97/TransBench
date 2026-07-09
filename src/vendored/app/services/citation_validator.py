import logging
from datetime import datetime
from urllib.parse import urlparse

from vendored.app.services.url_builder import is_safe_url

logger = logging.getLogger(__name__)

APPROVED_SOURCES = {
    # Guidelines
    "nice",
    "aha",
    "acc",
    "aha/acc",
    "esc",
    "who",
    "idsa",
    "nccn",
    "acog",
    "gold",
    "kdigo",
    "ada",
    # Regulatory
    "fda",
    "ema",
    "mhra",
    # Databases
    "uptodate",
    "bmj best practice",
    "cochrane library",
    "cochrane",
    "pubmed",
    # Pharmacology
    "fda drug labels",
    "fda drug label",
    "bnf",
    "micromedex",
    # Indian regulatory
    "cdsco",
    "indian pharmacopoeia",
    "indian local",
    "medindia",
    "schedule h",
    # Critical care / infectious disease societies
    "isid",
    "ssc",
    "sccm",
    "ats",
    "ers",
    "asm",
    "aasld",
    "easl",
    "esh",
    "isth",
    # Other — "Expert opinion" is the coerced default for missing sources;
    # do NOT add generic terms like "clinical guidelines" here as they
    # would let hallucinated citations pass validation.
    "expert consensus",
}

CURRENT_YEAR = datetime.now().year
MIN_SOURCE_YEAR = 1990

LOE_VALUES = {"I", "II-1", "II-2", "II-3", "III"}
COR_VALUES = {"I", "IIa", "IIb", "III-no-benefit", "III-harm"}


def _is_approved_source(source: str) -> bool:
    """Check if source matches approved list (case-insensitive fuzzy)."""
    source_lower = source.lower().strip()
    if source_lower in APPROVED_SOURCES:
        return True
    for approved in APPROVED_SOURCES:
        if approved in source_lower or source_lower in approved:
            return True
    return False


def validate_citations(response_data: dict, query_type: str, fetched_data=None, fetched_source_labels: set[str] | None = None) -> list[str]:
    """
    Validate citations in a response. Returns list of validation warnings.
    Non-blocking — warnings are informational, response is still returned.
    For query_type='complex', enforces strict source validation against fetched_source_labels.
    """
    warnings = []
    claims = _extract_claims(response_data, query_type)
    # Strict label-matching stays scoped to complex/procedure. For other medical types
    # the precise grounding_gate (rag_pipeline) keeps title-sourced claims and strips only
    # ungrounded ones — extending strict here false-dropped legitimate title-cited claims.
    strict = query_type in ("complex", "procedure")
    fetched_labels_lower = {s.lower() for s in (fetched_source_labels or set())}

    low_confidence_count = 0
    missing_citation_count = 0
    total_claims = len(claims)
    seen_unverified: set = set()

    for claim in claims:
        source = claim.get("source", "")
        source_year = claim.get("source_year")
        confidence = claim.get("confidence", "")
        loe = claim.get("loe", "")
        cor = claim.get("cor", "")

        if not source:
            missing_citation_count += 1
            claim_text = claim.get("value") or claim.get("text") or ""
            warnings.append(
                f"Missing citation for claim: '{_truncate(claim_text)}'"
            )
            continue

        # Strict-mode check (complex/procedure): a claim must either carry a real
        # identifier (PMID/NCT/DOI/URL) OR name a source matching the fetched data
        # labels. The identifier guard prevents false-dropping legitimate citations
        # whose `source` is a journal/title rather than the category name.
        if strict and fetched_labels_lower:
            _has_identifier = bool(
                claim.get("pmid") or claim.get("nct_id") or claim.get("doi") or claim.get("url")
            )
            if not _has_identifier and not any(
                label in (source or "").lower() for label in fetched_labels_lower
            ):
                warnings.append(
                    f"Strict-mode dropped claim with source '{source}' not in fetched data."
                )
                claim["__drop__"] = True
                continue

        if not _is_approved_source(source) and source not in seen_unverified:
            seen_unverified.add(source)
            warnings.append(f"Unverified source: {source}")

        if source_year is not None:
            if source_year > CURRENT_YEAR:
                warnings.append(f"Future source year: {source_year} for '{source}'")
            elif source_year < MIN_SOURCE_YEAR:
                warnings.append(f"Very old source: {source} ({source_year})")
            # Guideline recency warning — if cited guideline is >2 years old, likely superseded
            elif "guideline" in source.lower() and CURRENT_YEAR - source_year > 2:
                warnings.append(
                    f"Older guideline: {source} ({source_year}) — newer version may exist. "
                    f"Verify against current standards."
                )

        if loe and loe not in LOE_VALUES:
            warnings.append(f"Invalid LOE value: '{loe}'")

        if cor and cor not in COR_VALUES:
            warnings.append(f"Invalid COR value: '{cor}'")

        if confidence == "low":
            low_confidence_count += 1

    # Evidence confidence check — higher threshold for complex queries (70% vs 50% for simple)
    if total_claims > 0:
        low_ratio = (low_confidence_count + missing_citation_count) / total_claims
        threshold = 0.7 if strict else 0.5  # strict mode = complex queries
        if low_ratio > threshold:
            msg = (
                "This response has a mixed evidence base — some claims rely on lower-quality sources. "
                "Please verify critical claims with primary sources."
                if strict
                else "This response has limited evidence support. Please verify with primary sources."
            )
            warnings.insert(0, msg)

    # Reference URL and PMID validation
    from vendored.app.services.url_builder import build_pmid_index
    valid_pmids = set(build_pmid_index(fetched_data).values()) if fetched_data else set()
    
    valid_refs = []
    for ref in response_data.get("references", []):
        if fetched_data:
            pmid = ref.get("pmid")
            if pmid and str(pmid) not in valid_pmids:
                warnings.append(f"Removed hallucinated reference with unverified PMID: {pmid}")
                continue

        url = ref.get("url")
        if url is not None:
            if not url.startswith("https://"):
                ref["url"] = None
                warnings.append(
                    f"Reference URL uses non-HTTPS scheme (insecure) — removed: '{url[:80]}'"
                )
            elif not is_safe_url(url):
                try:
                    domain = urlparse(url).netloc
                except Exception:
                    domain = url[:40]
                ref["url"] = None
                warnings.append(
                    f"Reference URL domain not in approved list — removed: '{domain}'"
                )
        # url=None is fine — frontend builds PubMed link from PMID when url is absent.
        valid_refs.append(ref)

    response_data["references"] = valid_refs

    return warnings


def _extract_claims(data: dict, query_type: str) -> list[dict]:
    """Extract all evidenced claim objects from response data."""
    claims = []

    def _walk(obj):
        if isinstance(obj, dict):
            is_value_claim = ("value" in obj) and (
                "loe" in obj or "source" in obj or "confidence" in obj
            )
            # Adaptive schema uses content_items[].text instead of value.
            is_adaptive_claim = ("text" in obj) and any(
                k in obj for k in ("source", "loe", "cor", "pmid")
            )
            if is_value_claim or is_adaptive_claim:
                claims.append(obj)
            elif "evidence" in obj and isinstance(obj["evidence"], dict):
                claims.append(obj["evidence"])
            for v in obj.values():
                _walk(v)
        elif isinstance(obj, list):
            for item in obj:
                _walk(item)

    _walk(data)
    return claims


def _truncate(text: str, max_len: int = 80) -> str:
    if len(text) <= max_len:
        return text
    return text[:max_len] + "..."
