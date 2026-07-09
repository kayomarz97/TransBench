"""
Deterministic URL enrichment for Reference objects.

All URLs are generated from structured metadata already fetched from authoritative
sources (PMIDs, DOIs, source names). The LLM is never trusted to produce URLs.
"""

from __future__ import annotations

import re
from urllib.parse import quote, urlparse

# Safe chars for percent-encoding DOI path segments (RFC 3986 unreserved + DOI-allowed)
_DOI_SAFE_CHARS = "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789-._~/"

# ── Patterns (same as rag_pipeline, kept local to avoid circular import) ──────
_PMID_RE = re.compile(r"PMID[:\s]*(\d{6,9})", re.I)
_DOI_RE = re.compile(r'(10\.\d{4,9}/[^\s,;"\'\)\]]+)')

MAX_URL_LENGTH = 500

# ── Domain allowlist ──────────────────────────────────────────────────────────
ALLOWED_DOMAINS: frozenset[str] = frozenset(
    {
        "pubmed.ncbi.nlm.nih.gov",
        "doi.org",
        "www.nice.org.uk",
        "www.fda.gov",
        "www.accessdata.fda.gov",
        "www.cochranelibrary.com",
        "www.who.int",
        "www.escardio.org",
        "www.ahajournals.org",
        "www.acc.org",
        "www.idsociety.org",
        "www.nccn.org",
        "www.ema.europa.eu",
        "www.bmj.com",
        "www.nejm.org",
        "www.thelancet.com",
        "jamanetwork.com",
        "www.goldcopd.org",
        "kdigo.org",
        "www.diabetes.org",
        "www.acog.org",
        "www.isid.org",
        "www.sccm.org",
        "www.thoracic.org",
        "www.ersnet.org",
        "www.asm.org",
        "www.aasld.org",
        "easl.eu",
        "www.eshonline.org",
        "www.isth.org",
        # Major medical journals
        "www.annals.org",
        "journal.chestnet.org",
        "www.atsjournals.org",
        "academic.oup.com",
        "www.nature.com",
        "www.cell.com",
        "www.sciencedirect.com",
        "link.springer.com",
        "onlinelibrary.wiley.com",
        "www.mdpi.com",
        "www.frontiersin.org",
        "journals.plos.org",
        "www.ncbi.nlm.nih.gov",
        "www.uptodate.com",
        "bestpractice.bmj.com",
        "bnf.nice.org.uk",
        "www.medicines.org.uk",
        "dailymed.nlm.nih.gov",
        "www.rxlist.com",
        "www.drugs.com",
        "medlineplus.gov",
        # Clinical trials and NIH resources
        "clinicaltrials.gov",
        "www.guidelines.gov",
        # PubMed Central
        "pmc.ncbi.nlm.nih.gov",
        # Additional specialty society journals
        "www.gastrojournal.org",
        "www.journal-of-hepatology.eu",
        "erj.ersjournals.com",
        "thorax.bmj.com",
        "gut.bmj.com",
        "heart.bmj.com",
        "ard.bmj.com",
        "www.blood.org",
        "ashpublications.org",
        "www.haematologica.org",
        # AI research and aggregators
        "www.semanticscholar.org",
        "www.ncbi.nlm.nih.gov",  # NCBI Books
    }
)

# Source-name → homepage table removed (May 2026): homepages without article IDs
# are useless to clinicians. Article-level URLs are constructed from validated
# IDs (PMID/NCT/DOI/setid) by article_registry.py.


# ── Public API ────────────────────────────────────────────────────────────────


def is_safe_url(url: str | None) -> bool:
    """Return True only if the URL is https:// and from an allowed domain."""
    if not url or not isinstance(url, str):
        return False
    if len(url) > MAX_URL_LENGTH:
        return False
    if not url.startswith("https://"):
        return False
    try:
        parsed = urlparse(url)
        # Exact frozenset membership — do NOT change to substring/endswith;
        # that would allow subdomain confusion (e.g. "evil.pubmed.ncbi.nlm.nih.gov").
        return parsed.netloc in ALLOWED_DOMAINS
    except Exception:
        return False


def build_pmid_index(fetched_data) -> dict[str, str]:
    """Build a normalised-title → PMID mapping from all abstract lists in fetched_data."""
    index: dict[str, str] = {}
    if fetched_data is None:
        return index

    abstract_lists: list[list] = []
    for attr in (
        "drug_data",
        "disease_data",
        "procedure_data",
        "evidence_data",
    ):
        obj = getattr(fetched_data, attr, None)
        if obj is None:
            continue
        for list_attr in (
            "guideline_abstracts",
            "systematic_review_abstracts",
            "clinical_trial_abstracts",
            "practice_guideline_abstracts",
        ):
            lst = getattr(obj, list_attr, None)
            if lst:
                abstract_lists.append(lst)

    for lst in abstract_lists:
        for abstract in lst:
            if not isinstance(abstract, dict):
                continue
            pmid = abstract.get("pmid")
            title = abstract.get("title", "")
            if pmid and title:
                index[title.strip().lower()] = str(pmid)

    return index


def build_nctid_index(fetched_data) -> dict[str, str]:
    """Build a normalised-title → NCT ID mapping from all clinical trial abstracts in fetched_data."""
    index: dict[str, str] = {}
    if fetched_data is None:
        return index

    abstract_lists: list[list] = []
    for attr in (
        "drug_data",
        "disease_data",
        "procedure_data",
        "evidence_data",
    ):
        obj = getattr(fetched_data, attr, None)
        if obj is None:
            continue
        lst = getattr(obj, "clinical_trial_abstracts", None)
        if lst:
            abstract_lists.append(lst)

    for lst in abstract_lists:
        for abstract in lst:
            if not isinstance(abstract, dict):
                continue
            nct_id = abstract.get("nct_id")
            title = abstract.get("title", "")
            if nct_id and title:
                index[title.strip().lower()] = str(nct_id)

    return index


def sanitize_response_pmids(data: dict, fetched_data=None) -> None:
    """Null out every PMID in the response that was not actually fetched.

    The LLM hallucinates PMID numbers. Only PMIDs present in build_pmid_index
    (i.e., retrieved from real PubMed abstracts) are trusted. Call this before
    enrich_references so broken PMID→URL links are never built.
    """
    valid_pmids = set(build_pmid_index(fetched_data).values())
    if not valid_pmids:
        return
    _null_unrecognized_pmids(data, valid_pmids)


def _null_unrecognized_pmids(obj: object, valid_pmids: set) -> None:
    if isinstance(obj, dict):
        if "pmid" in obj and obj["pmid"] is not None:
            if str(obj["pmid"]) not in valid_pmids:
                obj["pmid"] = None
        for v in obj.values():
            _null_unrecognized_pmids(v, valid_pmids)
    elif isinstance(obj, list):
        for item in obj:
            _null_unrecognized_pmids(item, valid_pmids)


def enrich_references(data: dict, fetched_data=None) -> None:
    """
    Mutate data in-place: fill Reference.url using deterministic rules only.

    Priority order:
      1. Existing url — validated against allowed URLs from fetched_data; nulled if not found.
      2. PMID lookup from fetched_data abstracts (title match) — only for PubMed sources.
      3. PMID inline in source/title text.
      4. DOI inline in source/title text.
      5. NCT ID lookup from fetched_data abstracts (clinical trials only).
      6. Source-name pattern → known base URL.
      7. null (no invented URLs).
    """
    from vendored.app.services.prompt_engine import build_ref_map

    pmid_index = build_pmid_index(fetched_data)
    nctid_index = build_nctid_index(fetched_data)
    valid_pmids = set(pmid_index.values())
    pmid_to_title = {v: k.title() for k, v in pmid_index.items()}
    refs = data.get("references")
    if not refs:
        return

    allowed_urls = set()
    if fetched_data:
        try:
            ref_map = build_ref_map(fetched_data)
            allowed_urls = {art.get("url") for art in ref_map.values() if art.get("url")}
        except Exception:
            pass

    NON_PUBMED_SOURCES = {
        "fda", "nice", "cochrane", "who", "esc", "aha", "acc",
        "clinicaltrials", "clinicaltrials.gov", "ema", "gold",
        "kdigo", "ada", "acog", "idsa", "nccn", "medlineplus",
        "ncbi books", "dailymed"
    }

    for ref in refs:
        # Step 0: backfill title if missing but PMID is present
        pmid = ref.get("pmid")
        if not ref.get("title") and pmid and str(pmid) in pmid_to_title:
            ref["title"] = pmid_to_title[str(pmid)]

        existing = ref.get("url")

        # Step 0.5: direct PMID field → guaranteed article-level URL (no validation needed)
        ref_pmid_direct = str(ref.get("pmid") or "").strip()
        if not existing and ref_pmid_direct and ref_pmid_direct.isdigit():
            candidate = f"https://pubmed.ncbi.nlm.nih.gov/{ref_pmid_direct}/"
            ref["url"] = candidate
            continue

        # Step 1: validate existing URL against allowed URLs from fetched_data
        if existing:
            if allowed_urls and existing not in allowed_urls:
                ref["url"] = None
            elif not allowed_urls and not is_safe_url(existing):
                ref["url"] = None
            else:
                ref["url"] = existing
            if ref["url"]:
                continue

        title = (ref.get("title") or "").strip()
        source = (ref.get("source") or "").strip()
        combined = f"{source} {title}".strip()
        source_lower = source.lower()

        # Step 2: title match against fetched PMID index — ONLY for PubMed sources
        is_non_pubmed = any(s in source_lower for s in NON_PUBMED_SOURCES)
        if title and not is_non_pubmed:
            pmid = pmid_index.get(title.lower())
            # Fuzzy fallback: strip punctuation and try prefix matching
            if not pmid:
                normalized = re.sub(r"[^\w\s]", "", title.lower()).split()
                for idx_title, idx_pmid in pmid_index.items():
                    idx_words = re.sub(r"[^\w\s]", "", idx_title).split()
                    if normalized and idx_words and normalized[:6] == idx_words[:6]:
                        pmid = idx_pmid
                        break
            if pmid:
                candidate = f"https://pubmed.ncbi.nlm.nih.gov/{pmid}/"
                ref["url"] = candidate if is_safe_url(candidate) else None
                if ref["url"]:
                    continue

        # Step 3: PMID inline in source/title text — only if PMID is from fetched data
        pmid_match = _PMID_RE.search(combined)
        if pmid_match and pmid_match.group(1) in valid_pmids:
            candidate = f"https://pubmed.ncbi.nlm.nih.gov/{pmid_match.group(1)}/"
            ref["url"] = candidate if is_safe_url(candidate) else None
            if ref["url"]:
                continue

        # Step 4: DOI inline — percent-encode the path to prevent CRLF/fragment injection
        doi_match = _DOI_RE.search(combined)
        if doi_match:
            safe_doi = quote(doi_match.group(1), safe=_DOI_SAFE_CHARS)
            candidate = f"https://doi.org/{safe_doi}"
            ref["url"] = candidate if is_safe_url(candidate) else None
            if ref["url"]:
                continue

        # Step 5: NCT ID lookup for clinical trials
        nct_id = ref.get("nct_id")
        if not nct_id and title:
            nct_id = nctid_index.get(title.lower())
        if nct_id:
            candidate = f"https://clinicaltrials.gov/study/{nct_id}"
            ref["url"] = candidate if is_safe_url(candidate) else None
            if ref["url"]:
                continue

        # Step 5.5: Direct DOI field fallback — build from structured doi field
        doi = (ref.get("doi") or "").strip()
        if doi and not ref.get("url"):
            safe_doi = quote(doi, safe=_DOI_SAFE_CHARS)
            candidate = f"https://doi.org/{safe_doi}"
            ref["url"] = candidate if is_safe_url(candidate) else None
            if ref["url"]:
                continue

        # Step 6: source-name pattern homepages — REMOVED. Homepages (pubmed.ncbi.nlm.nih.gov/) without article-level URLs are useless.
        # If a source has a meaningful article URL, it will be in the data block and handled by Steps 0.5–5.

        # Step 7: NEVER null existing URL — preserve what caller built. Only null if genuinely unfixable.
        # (Removed ref["url"] = None — this was nuking URLs from _build_complete_references)


# ── Internal helpers ──────────────────────────────────────────────────────────


def _match_source_pattern(source_lower: str, title: str) -> str | None:
    for keyword, base_url in _SOURCE_URL_MAP:
        if keyword in source_lower:
            if keyword == "pubmed" and title:
                # Steps 2/3 already handle PubMed via PMID; base URL is useless here
                return None
            return base_url
    return None
