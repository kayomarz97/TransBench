"""
data_fetcher.py — Parallel API fetching from free medical databases.

Fetches authoritative raw data (FDA labels, PubMed guidelines, MedlinePlus, etc.)
so the LLM only needs to FORMAT, not generate knowledge from scratch.
All API calls are async, fire-and-forget, and silent on failure.
"""

import asyncio
import contextlib
import contextvars
import json
import logging
import os
import re
import time
from urllib.parse import quote_plus
import xml.etree.ElementTree as ET
from dataclasses import dataclass, field
from typing import Optional

# Request-scoped user email for Unpaywall lookups — set by fetch_data_for_query
_user_email_ctx: contextvars.ContextVar[str | None] = contextvars.ContextVar(
    "_user_email_ctx", default=None
)

# Request-scoped NCBI API key for PubMed rate limit bypass — set by fetch_data_for_query
_ncbi_key_ctx: contextvars.ContextVar[str | None] = contextvars.ContextVar(
    "_ncbi_key_ctx", default=None
)

import httpx

from vendored.app.config import settings

logger = logging.getLogger(__name__)

# PubMed rate limiter: without key (3 req/sec), with key (10 req/sec)
# Use semaphores + minimum gap enforcement to stay within NCBI limits
_PUBMED_SEM_NO_KEY = asyncio.Semaphore(1)
_PUBMED_SEM_WITH_KEY = asyncio.Semaphore(8)
_PUBMED_LAST_REQUEST: float = 0.0
_PUBMED_MIN_GAP_NO_KEY = 0.4
_PUBMED_MIN_GAP_WITH_KEY = 0.1

# Evidence search start year — 25-year window to capture foundational landmark trials
# (HOPE 2000, ALLHAT 2002, ADVANCE 2008, ACCORD 2008, early KDIGO editions, etc.)
_EVIDENCE_SEARCH_START_YEAR: int = 2000

# ------------------------------------------------------------------
# Journal registry (loaded once at import time)
# ------------------------------------------------------------------

_JOURNAL_REGISTRY: dict = {}


def _load_journal_registry() -> None:
    global _JOURNAL_REGISTRY
    try:
        path = os.path.join(
            os.path.dirname(__file__), "..", "..", "data", "medical_journals.json"
        )
        path = os.path.normpath(path)
        if os.path.exists(path):
            with open(path, encoding="utf-8") as f:
                _JOURNAL_REGISTRY = json.load(f)
    except Exception:
        logger.warning("Failed to load medical_journals.json", exc_info=True)


_load_journal_registry()


# Mapping from query specialty keywords to journal registry keys
_SPECIALTY_MAP: dict[str, list[str]] = {
    "heart": ["cardiology"],
    "cardiac": ["cardiology"],
    "cardio": ["cardiology"],
    "hypertension": ["cardiology"],
    "arrhythmia": ["cardiology"],
    "cancer": ["oncology"],
    "tumor": ["oncology"],
    "neoplasm": ["oncology"],
    "lymphoma": ["oncology", "hematology"],
    "leukemia": ["oncology", "hematology"],
    "brain": ["neurology"],
    "neuro": ["neurology"],
    "stroke": ["neurology"],
    "epilepsy": ["neurology"],
    "seizure": ["neurology"],
    "diabetes": ["endocrinology"],
    "thyroid": ["endocrinology"],
    "insulin": ["endocrinology"],
    "lung": ["pulmonology"],
    "asthma": ["pulmonology"],
    "copd": ["pulmonology"],
    "pneumonia": ["pulmonology", "infectious_disease"],
    "infection": ["infectious_disease"],
    "antibiotic": ["infectious_disease"],
    "hiv": ["infectious_disease"],
    "tuberculosis": ["infectious_disease", "tropical_medicine"],
    "malaria": ["tropical_medicine", "infectious_disease"],
    "liver": ["gastroenterology"],
    "hepatitis": ["gastroenterology", "infectious_disease"],
    "gastric": ["gastroenterology"],
    "bowel": ["gastroenterology"],
    "kidney": ["nephrology"],
    "renal": ["nephrology"],
    "dialysis": ["nephrology"],
    "arthritis": ["rheumatology"],
    "lupus": ["rheumatology"],
    "autoimmune": ["rheumatology"],
    "depression": ["psychiatry"],
    "anxiety": ["psychiatry"],
    "schizophrenia": ["psychiatry"],
    "bipolar": ["psychiatry"],
    "child": ["pediatrics"],
    "neonatal": ["pediatrics"],
    "pediatric": ["pediatrics"],
    "surgery": ["surgery"],
    "surgical": ["surgery"],
    "fracture": ["orthopedics"],
    "joint": ["orthopedics"],
    "spine": ["orthopedics"],
    "pregnancy": ["obstetrics_gynecology"],
    "obstetric": ["obstetrics_gynecology"],
    "gynecol": ["obstetrics_gynecology"],
    "skin": ["dermatology"],
    "dermat": ["dermatology"],
    "psoriasis": ["dermatology"],
    "eye": ["ophthalmology"],
    "retina": ["ophthalmology"],
    "glaucoma": ["ophthalmology"],
    "icu": ["critical_care"],
    "sepsis": ["critical_care", "infectious_disease"],
    "ventilat": ["critical_care"],
    "anesthes": ["anesthesiology"],
    "pain": ["anesthesiology"],
    "blood": ["hematology"],
    "anemia": ["hematology"],
    "coagul": ["hematology"],
    "thrombosis": ["hematology"],
    "pulmonary": ["pulmonology", "cardiology"],
    "pah": ["pulmonology", "cardiology"],
    "pulmonary hypertension": ["pulmonology", "cardiology"],
    "right heart": ["cardiology", "pulmonology"],
    "prostate": ["urology"],
    "bladder": ["urology"],
    "urinary": ["urology"],
}


# Medical term synonyms for PubMed OR expansion (improves recall on complex queries)
_MEDICAL_TERM_SYNONYMS: dict[str, list[str]] = {
    "renal impairment": ["renal impairment", "CKD", "chronic kidney disease", "kidney disease", "renal disease"],
    "renal failure": ["renal failure", "acute kidney injury", "AKI", "kidney failure"],
    "liver failure": ["liver failure", "hepatic failure", "cirrhosis", "hepatic cirrhosis"],
    "heart failure": ["heart failure", "CHF", "congestive heart failure", "cardiac failure"],
    "kidney disease": ["kidney disease", "renal disease", "CKD", "chronic kidney disease"],
    "diabetes": ["diabetes", "diabetes mellitus", "DM"],
    "hypertension": ["hypertension", "high blood pressure", "HTN"],
    "arrhythmia": ["arrhythmia", "cardiac arrhythmia", "atrial fibrillation", "AFib"],
    "afib": ["AFib", "atrial fibrillation", "arrhythmia"],
    "copd": ["COPD", "chronic obstructive pulmonary disease"],
    "infection": ["infection", "infectious disease", "sepsis"],
    "stroke": ["stroke", "cerebrovascular accident", "CVA"],
}


# Guideline societies and their expected update cycles
GUIDELINE_SOCIETIES: dict[str, dict[str, int]] = {
    "American Diabetes Association": {"update_years": 1, "key_journals": ["Diabetes Care"]},
    "American Heart Association": {"update_years": 2, "key_journals": ["Circulation"]},
    "American College of Cardiology": {"update_years": 2, "key_journals": ["Journal of the American College of Cardiology"]},
    "National Institute for Health and Care Excellence": {"update_years": 3, "key_journals": ["NICE Guidelines"]},
    "American Academy of Pediatrics": {"update_years": 2, "key_journals": ["Pediatrics"]},
    "American Thoracic Society": {"update_years": 2, "key_journals": ["American Journal of Respiratory and Critical Care Medicine"]},
    "Infectious Diseases Society of America": {"update_years": 3, "key_journals": ["Clinical Infectious Diseases"]},
}


def _expand_medical_term(term: str) -> str:
    """Expand a medical term with OR-joined synonyms for better PubMed recall.

    Example: "renal impairment" → "(renal impairment OR CKD OR chronic kidney disease OR ...)"
    """
    term_lower = term.lower()
    for key, synonyms in _MEDICAL_TERM_SYNONYMS.items():
        if key in term_lower:
            # Create OR expression with all synonyms
            or_expr = " OR ".join(f'"{s}"' for s in synonyms)
            return f"({or_expr})"
    return f'"{term}"'  # fallback: quoted term


def _get_journal_filter(entity: str) -> str:
    """Build a PubMed journal filter string (TA field) for the entity's specialty.

    Returns top-5 ranked journal abbreviations as OR-joined TA filters,
    or empty string if no specialty match.
    """
    entity_lower = entity.lower()
    matched_specialties: set[str] = set()

    for keyword, specialties in _SPECIALTY_MAP.items():
        if keyword in entity_lower:
            matched_specialties.update(specialties)

    if not matched_specialties:
        return ""

    abbrevs: list[str] = []
    for spec in matched_specialties:
        journals = _JOURNAL_REGISTRY.get(spec, [])
        for j in journals[:5]:  # top 5 per specialty
            abbr = j.get("abbrev")
            if abbr and j.get("pubmed"):
                abbrevs.append(abbr)

    if not abbrevs:
        return ""

    # Deduplicate while preserving order
    seen: set[str] = set()
    unique: list[str] = []
    for a in abbrevs:
        if a not in seen:
            seen.add(a)
            unique.append(a)

    # Limit to 8 journals to keep query reasonable
    ta_parts = [f'"{a}"[TA]' for a in unique[:8]]
    return "(" + " OR ".join(ta_parts) + ")"


# ------------------------------------------------------------------
# Data classes
# ------------------------------------------------------------------


@dataclass
class DrugFetchResult:
    generic_name: Optional[str] = None
    brand_name: Optional[str] = None
    drug_class: Optional[str] = None
    drug_class_rxnorm: Optional[str] = None
    indications_raw: Optional[str] = None
    dosing_raw: Optional[str] = None
    contraindications_raw: Optional[str] = None
    warnings_raw: Optional[str] = None
    adverse_reactions_raw: Optional[str] = None
    drug_interactions_raw: Optional[str] = None
    pharmacokinetics_raw: Optional[str] = None
    special_populations_raw: Optional[str] = None
    mechanism_raw: Optional[str] = None
    fda_label_source_year: Optional[int] = None
    top_adverse_events: list = field(default_factory=list)
    rxcui: Optional[str] = None
    chembl_mechanism: Optional[str] = None
    chembl_atc_class: Optional[str] = None
    drug_interaction_pairs: list = field(default_factory=list)  # RxNorm DDI pairs
    guideline_abstracts: list = field(default_factory=list)
    systematic_review_abstracts: list = field(default_factory=list)
    clinical_trial_abstracts: list = field(default_factory=list)
    data_source: str = "unknown"
    fetch_success: bool = False
    data_sources: list = field(default_factory=list)
    label_url: Optional[str] = None  # human-readable label page URL from fetch (DailyMed or FDA)


@dataclass
class DiseaseFetchResult:
    disease_name: Optional[str] = None
    guideline_abstracts: list = field(default_factory=list)
    guideline_summary: Optional[str] = None
    systematic_review_abstracts: list = field(default_factory=list)
    nice_recommendations: list = field(default_factory=list)
    medlineplus_summary: Optional[str] = None
    semantic_papers: list = field(default_factory=list)
    ncbi_structured: Optional[str] = None
    book_monographs: list = field(default_factory=list)  # StatPearls/Bookshelf full chapters: {title,url,text,source,nbk_id}
    fetch_success: bool = False
    data_sources: list = field(default_factory=list)


@dataclass
class ProcedureFetchResult:
    guideline_abstracts: list = field(default_factory=list)
    practice_guideline_abstracts: list = field(default_factory=list)
    fetch_success: bool = False
    data_sources: list = field(default_factory=list)


@dataclass
class EvidenceFetchResult:
    clinical_trial_abstracts: list = field(default_factory=list)
    systematic_review_abstracts: list = field(default_factory=list)
    guideline_abstracts: list = field(default_factory=list)
    fetch_success: bool = False
    data_sources: list = field(default_factory=list)


@dataclass
class FetchedData:
    query_type: str
    drug_data: Optional[DrugFetchResult] = None
    disease_data: Optional[DiseaseFetchResult] = None
    condition_data: Optional[DiseaseFetchResult] = (
        None  # disease guidelines for drug-in-condition queries
    )
    procedure_data: Optional[ProcedureFetchResult] = None
    evidence_data: Optional[EvidenceFetchResult] = None
    comparative_evidence: Optional[EvidenceFetchResult] = None
    comparative_drug_data: list = field(default_factory=list)
    drug_interactions: list = field(default_factory=list)  # RxNorm DDI pairs for 2-drug queries
    comorbidity_data: list = field(default_factory=list)  # list[DiseaseFetchResult] — one per comorbidity for query_type="complex"
    evidence_tier: str = "unknown"  # "guideline" | "rct" | "review" | "case_report" | "drug_class" | "unknown"
    cascade_log: list = field(default_factory=list)  # human-readable trace of which cascade tier produced abstracts
    total_fetch_time_ms: int = 0
    fallback_to_llm: bool = False
    data_sources: list = field(default_factory=list)
    images: list = field(default_factory=list)  # [{url, caption, license, source}]
    patient_context: dict = field(default_factory=dict)  # extracted from query: age, renal, hepatic, weight, concurrent_drugs, pregnancy


# Local Indian drug repositories were intentionally removed.
# Drug-brand resolution must happen against live online sources.


# ------------------------------------------------------------------
# HTTP helpers
# ------------------------------------------------------------------


# ------------------------------------------------------------------
# Persistent HTTP client — shared across all requests for connection reuse
# ------------------------------------------------------------------

_HTTP_CLIENT: httpx.AsyncClient | None = None


async def init_http_client() -> None:
    """Initialize the shared HTTP client at application startup."""
    global _HTTP_CLIENT
    _HTTP_CLIENT = httpx.AsyncClient(
        timeout=httpx.Timeout(connect=3.0, read=8.0, write=3.0, pool=3.0),
        follow_redirects=True,
        limits=httpx.Limits(
            max_connections=30,
            max_keepalive_connections=15,
            keepalive_expiry=60,
        ),
    )


async def shutdown_http_client() -> None:
    """Close the shared HTTP client at application shutdown."""
    global _HTTP_CLIENT
    if _HTTP_CLIENT is not None:
        await _HTTP_CLIENT.aclose()
        _HTTP_CLIENT = None


def _get_client() -> httpx.AsyncClient:
    """Return the shared HTTP client. Falls back to creating a temporary client if not initialized."""
    if _HTTP_CLIENT is not None:
        return _HTTP_CLIENT
    # Fallback for tests or early startup — create a one-off client
    return httpx.AsyncClient(
        timeout=settings.api_fetch_timeout_seconds,
        follow_redirects=True,
        limits=httpx.Limits(max_connections=20, max_keepalive_connections=10),
    )


@contextlib.asynccontextmanager
async def _make_client():  # type: ignore[return]
    """Async context manager for HTTP client access.

    When the persistent client is initialized (production), yields it WITHOUT closing on exit.
    Falls back to creating a temporary client (tests / early startup) that IS closed on exit.
    """
    if _HTTP_CLIENT is not None:
        yield _HTTP_CLIENT
    else:
        async with httpx.AsyncClient(
            timeout=settings.api_fetch_timeout_seconds,
            follow_redirects=True,
            limits=httpx.Limits(max_connections=20, max_keepalive_connections=10),
        ) as client:
            yield client


async def _safe_get(
    client: httpx.AsyncClient,
    url: str,
    params: dict | None = None,
    headers: dict | None = None,
) -> dict | None:
    """GET → JSON. Returns None on any failure."""
    try:
        resp = await client.get(url, params=params, headers=headers)
        if resp.status_code == 200:
            return resp.json()
    except httpx.TimeoutException:
        logger.debug("API timeout: %s", url)
    except httpx.HTTPStatusError as e:
        logger.debug("API HTTP %s: %s", e.response.status_code, url)
    except Exception:
        logger.debug("API fetch failed: %s", url, exc_info=True)
    return None


async def _safe_get_text(
    client: httpx.AsyncClient, url: str, params: dict | None = None
) -> str | None:
    """GET → plain text. Returns None on any failure."""
    try:
        resp = await client.get(url, params=params)
        if resp.status_code == 200:
            return resp.text
    except httpx.TimeoutException:
        logger.debug("API timeout (text): %s", url)
    except httpx.HTTPStatusError as e:
        logger.debug("API HTTP %s (text): %s", e.response.status_code, url)
    except Exception:
        logger.debug("API text fetch failed: %s", url, exc_info=True)
    return None


async def _ncbi_eutils_get(
    client: httpx.AsyncClient, url: str, params: dict | None = None
) -> dict | None:
    """Throttled eUtils GET (JSON) — shares the NCBI rate-limit semaphore + min-gap used by
    PubMed calls, so book/StatPearls lookups never trip NCBI's 3 req/s (no key) limit when
    they run alongside or right after the PubMed gather."""
    api_key = _ncbi_key_ctx.get(None) or settings.pubmed_api_key
    sem = _PUBMED_SEM_WITH_KEY if api_key else _PUBMED_SEM_NO_KEY
    async with sem:
        await asyncio.sleep(0.15 if api_key else 0.4)
        data = await _safe_get(client, url, params=params)
        if data is None:  # transient 429/error — one bounded retry inside the throttle slot
            await asyncio.sleep(0.5)
            data = await _safe_get(client, url, params=params)
        return data


# ------------------------------------------------------------------
# Helpers
# ------------------------------------------------------------------


def _first(lst: list | None) -> Optional[str]:
    if lst and isinstance(lst, list) and lst:
        return str(lst[0])
    return None


def _truncate(text: Optional[str], max_chars: int) -> Optional[str]:
    if not text:
        return None
    return text[:max_chars] + ("..." if len(text) > max_chars else "")


def _cap_abstracts(abstracts: list, max_total_chars: int = 3000) -> list:
    """Keep as many abstracts as fit within the total character budget (newest first)."""
    sorted_abs = sorted(abstracts, key=lambda x: x.get("year") or 0, reverse=True)
    result, total = [], 0
    for a in sorted_abs:
        text = a.get("abstract", "") or ""
        if total + len(text) > max_total_chars:
            break
        result.append(a)
        total += len(text)
    return result


# ------------------------------------------------------------------
# OpenFDA drug label
# ------------------------------------------------------------------


async def _fetch_fda_label(client: httpx.AsyncClient, drug_name: str) -> dict | None:
    base_url = "https://api.fda.gov/drug/label.json"
    extra = {"api_key": settings.openfda_api_key} if settings.openfda_api_key else {}
    name_lower = drug_name.lower().strip()

    def _best_result(data: dict | None) -> dict | None:
        """Return a synthetic single-result dict preferring exact single-ingredient matches."""
        if not data or not data.get("results"):
            return None
        results = data["results"]
        ranked: list[tuple[int, dict]] = []
        for r in results:
            names = [n.lower().strip() for n in r.get("openfda", {}).get("generic_name", [])]
            brands = [n.lower().strip() for n in r.get("openfda", {}).get("brand_name", [])]
            score = 0
            for n in names:
                if n == name_lower:
                    score = max(score, 100)
                elif any(part.strip() == name_lower for part in re.split(r"\s*(?:/|,| and )\s*", n)):
                    score = max(score, 85)
                elif n.startswith(name_lower + " "):
                    score = max(score, 70)
            if any(b == name_lower for b in brands):
                score = max(score, 90)
            ingredient_penalty = 0
            if names:
                min_parts = min(
                    len([part for part in re.split(r"\s*(?:/|,| and )\s*", n) if part.strip()])
                    for n in names
                )
                ingredient_penalty = max(0, min_parts - 1) * 10
            ranked.append((score - ingredient_penalty, r))
        ranked.sort(key=lambda item: item[0], reverse=True)
        if ranked and ranked[0][0] > 0:
            return {"results": [ranked[0][1]]}
        return {"results": [results[0]]}

    # Try generic name search first (fetch 3 to find best match)
    # Filter for actively marketed drugs: HUMAN PRESCRIPTION DRUG or OTC DRUG
    data = await _safe_get(
        client,
        base_url,
        params={
            "search": f'openfda.generic_name:"{drug_name}" AND (openfda.product_type:"HUMAN+PRESCRIPTION+DRUG" OR openfda.product_type:"OTC+DRUG")',
            "limit": 10,
            **extra,
        },
    )
    best = _best_result(data)
    if best:
        return best

    # Fallback: brand name (with same product type filter)
    data = await _safe_get(
        client,
        base_url,
        params={
            "search": f'openfda.brand_name:"{drug_name}" AND (openfda.product_type:"HUMAN+PRESCRIPTION+DRUG" OR openfda.product_type:"OTC+DRUG")',
            "limit": 1,
            **extra,
        },
    )
    if data and data.get("results"):
        return data

    # Fallback: free text (with same product type filter)
    data = await _safe_get(
        client,
        base_url,
        params={
            "search": f'"{drug_name}" AND (openfda.product_type:"HUMAN+PRESCRIPTION+DRUG" OR openfda.product_type:"OTC+DRUG")',
            "limit": 1,
            **extra,
        },
    )
    return data


def _merge_fda_label(result: DrugFetchResult, data: dict | None) -> None:
    if not data or not data.get("results"):
        return
    r = data["results"][0]
    openfda = r.get("openfda", {})

    result.generic_name = _first(openfda.get("generic_name")) or result.generic_name
    result.brand_name = _first(openfda.get("brand_name")) or result.brand_name
    result.drug_class = _first(openfda.get("pharm_class_epc")) or result.drug_class

    # Store FDA application number URL if not already set (DailyMed takes priority)
    if not result.label_url:
        app_raw = _first(openfda.get("application_number"))
        if app_raw:
            app_num = re.sub(r"[^0-9]", "", app_raw)
            if app_num:
                result.label_url = (
                    f"https://www.accessdata.fda.gov/scripts/cder/daf/"
                    f"index.cfm?event=overview.process&ApplNo={app_num}"
                )

    result.mechanism_raw = _truncate(_first(r.get("mechanism_of_action")), 400)
    result.indications_raw = _truncate(_first(r.get("indications_and_usage")), 800)
    result.dosing_raw = _truncate(_first(r.get("dosage_and_administration")), 800)
    result.contraindications_raw = _truncate(_first(r.get("contraindications")), 600)

    warnings = (
        r.get("warnings_and_cautions") or r.get("warnings") or r.get("boxed_warning")
    )
    result.warnings_raw = _truncate(_first(warnings), 500)

    result.adverse_reactions_raw = _truncate(_first(r.get("adverse_reactions")), 600)
    result.drug_interactions_raw = _truncate(_first(r.get("drug_interactions")), 600)

    # Pharmacokinetics is often inside clinical_pharmacology
    clin_pharm = r.get("clinical_pharmacology")
    result.pharmacokinetics_raw = _truncate(_first(clin_pharm), 400)
    if not result.mechanism_raw:
        result.mechanism_raw = _truncate(_first(clin_pharm), 400)

    result.special_populations_raw = _truncate(
        _first(r.get("use_in_specific_populations")), 400
    )

    effective_time = r.get("effective_time", "")
    if effective_time and len(str(effective_time)) >= 4:
        try:
            result.fda_label_source_year = int(str(effective_time)[:4])
        except (ValueError, TypeError):
            pass

    result.data_source = "fda"
    result.fetch_success = bool(result.indications_raw or result.dosing_raw)


# ------------------------------------------------------------------
# OpenFDA adverse events (FAERS)
# ------------------------------------------------------------------


async def _fetch_fda_events(client: httpx.AsyncClient, drug_name: str) -> list:
    extra = {"api_key": settings.openfda_api_key} if settings.openfda_api_key else {}
    data = await _safe_get(
        client,
        "https://api.fda.gov/drug/event.json",
        params={
            "search": f'patient.drug.medicinalproduct:"{drug_name}"',
            "count": "patient.reaction.reactionmeddrapt.exact",
            "limit": 10,
            **extra,
        },
    )
    if not data or not data.get("results"):
        return []
    return [item["term"].title() for item in data["results"][:8]]


# ------------------------------------------------------------------
# RxNorm
# ------------------------------------------------------------------


async def _fetch_rxnorm_cui(client: httpx.AsyncClient, drug_name: str) -> Optional[str]:
    data = await _safe_get(
        client,
        "https://rxnav.nlm.nih.gov/REST/rxcui.json",
        params={
            "name": drug_name,
            "search": 1,
        },
    )
    if not data:
        return None
    ids = data.get("idGroup", {}).get("rxnormId", [])
    return ids[0] if ids else None


async def _fetch_rxnorm_class(client: httpx.AsyncClient, rxcui: str) -> Optional[str]:
    data = await _safe_get(
        client,
        "https://rxnav.nlm.nih.gov/REST/rxclass/class/byRxcui.json",
        params={
            "rxcui": rxcui,
            "relaSource": "ATC",
        },
    )
    if not data:
        return None
    concepts = data.get("rxclassMinConceptList", {}).get("rxclassMinConcept", [])
    return concepts[0].get("className") if concepts else None


async def _fetch_rxnorm_class_chain(
    client: httpx.AsyncClient, drug_name: str
) -> Optional[str]:
    """Chain CUI lookup → class lookup in a single coroutine for parallel execution.

    Replaces the sequential post-Phase-1 RxNorm class call with one that can run
    alongside FDA/PubMed calls in the Phase 1 gather.
    """
    rxcui = await _fetch_rxnorm_cui(client, drug_name)
    if not rxcui:
        return None
    return await _fetch_rxnorm_class(client, rxcui)


async def _fetch_rxnorm_approximate_rxcui(
    client: httpx.AsyncClient, drug_name: str
) -> Optional[str]:
    data = await _safe_get(
        client,
        "https://rxnav.nlm.nih.gov/REST/approximateTerm.json",
        params={
            "term": drug_name,
            "maxEntries": 1,
        },
    )
    candidates = data.get("approximateGroup", {}).get("candidate", []) if data else []
    if not candidates:
        return None
    return candidates[0].get("rxcui")


async def _fetch_rxnorm_name(client: httpx.AsyncClient, rxcui: str) -> Optional[str]:
    data = await _safe_get(
        client,
        f"https://rxnav.nlm.nih.gov/REST/rxcui/{rxcui}/properties.json",
    )
    props = data.get("properties") if data else None
    if not props:
        return None
    return props.get("name")


async def _fetch_rxnorm_ingredient(client: httpx.AsyncClient, rxcui: str) -> Optional[str]:
    """Resolve a brand RxCUI to its ingredient (salt) RxCUI.

    Uses /related.json?tty=IN — returns the active ingredient rxcui for brand names.
    Example: Lipitor rxcui → atorvastatin rxcui.
    """
    data = await _safe_get(
        client,
        f"https://rxnav.nlm.nih.gov/REST/rxcui/{rxcui}/related.json",
        params={"tty": "IN"},
    )
    if not data:
        return None
    concept_group = data.get("relatedGroup", {}).get("conceptGroup", [])
    for group in concept_group:
        if group.get("tty") == "IN":
            props = group.get("conceptProperties", [])
            if props:
                return props[0].get("rxcui")
    return None


async def _fetch_chembl_identity(client: httpx.AsyncClient, drug_name: str) -> tuple[str | None, float]:
    """Resolve drug name via ChEMBL API. Returns (canonical_name, confidence).

    Returns (None, 0.0) if no match found.
    """
    try:
        search = await _safe_get(
            client,
            "https://www.ebi.ac.uk/chembl/api/data/molecule.json",
            params={"pref_name__iexact": drug_name, "format": "json", "limit": 1},
        )
        if search and search.get("molecules"):
            mol = search["molecules"][0]
            pref_name = mol.get("pref_name")
            if pref_name and pref_name.lower() != drug_name.lower():
                return pref_name, 0.65
    except Exception:
        pass
    return None, 0.0


async def _fetch_pubchem_identity(client: httpx.AsyncClient, drug_name: str) -> tuple[str | None, float]:
    """Resolve drug name via PubChem CID API. Returns (IUPAC_name, confidence).

    Returns (None, 0.0) if no match found.
    """
    try:
        # PubChem Identifier API to get CID
        cid_response = await _safe_get(
            client,
            f"https://pubchem.ncbi.nlm.nih.gov/rest/pug/compound/name/{drug_name}/cids/JSON",
            timeout=5,
        )
        if cid_response and cid_response.get("IdentifierList", {}).get("CID"):
            cid = cid_response["IdentifierList"]["CID"][0]
            # Now fetch the compound data
            compound = await _safe_get(
                client,
                f"https://pubchem.ncbi.nlm.nih.gov/rest/pug/compound/cid/{cid}/JSON",
                params={"c1ccc2c(c1)oc(=O)c3[nH]c(=O)[nH]c23": "O"},
            )
            if compound:
                props = compound.get("PC_Compounds", [{}])[0].get("props", [])
                for prop in props:
                    if prop.get("urn", {}).get("label") == "IUPAC Name":
                        iupac = prop.get("value", {}).get("sval")
                        if iupac and iupac.lower() != drug_name.lower():
                            return iupac, 0.60
    except Exception:
        pass
    return None, 0.0


async def _fetch_ema_identity(client: httpx.AsyncClient, drug_name: str) -> tuple[str | None, float]:
    """Resolve drug name via EMA (European Medicines Agency) API. Returns (authorized_name, confidence).

    Returns (None, 0.0) if no match found.
    """
    try:
        # EMA API to search medicines
        search = await _safe_get(
            client,
            "https://www.ema.europa.eu/api/medicines/search",
            params={"q": drug_name, "limit": 1},
        )
        if search and search.get("medicines"):
            med = search["medicines"][0]
            auth_name = med.get("name") or med.get("active_substance")
            if auth_name and auth_name.lower() != drug_name.lower():
                return auth_name, 0.68
    except Exception:
        pass
    return None, 0.0


async def _fetch_who_inn(client: httpx.AsyncClient, drug_name: str) -> tuple[str | None, float]:
    """Resolve drug name via WHO INN (International Nonproprietary Name) database.

    Returns (INN_name, confidence) if found, (None, 0.0) otherwise.
    """
    try:
        # WHO INN searchable list
        response = await _safe_get(
            client,
            f"https://www.who.int/medicines/services/inn/SCP_INN.pdf",
            timeout=10,
        )
        # Note: PDF parsing would require pdfplumber, which is already in deps
        # For now, return None — this is a placeholder for direct WHO API if available
    except Exception:
        pass
    return None, 0.0


async def _resolve_drug_name_online(client: httpx.AsyncClient, drug_name: str) -> dict:
    """Resolve a brand or misspelled drug name to its generic (salt) name.

    Resolution order:
    1. RxNorm exact match → follow /related?tty=IN to get ingredient rxcui
    2. RxNorm approximate match → same ingredient lookup
    3. OpenFDA brand search → extract openfda.generic_name directly
    4. ChEMBL identity search (non-FDA drugs)
    5. PubChem IUPAC lookup (non-FDA drugs)
    6. EMA drug register (non-FDA drugs)
    7. Fallback to original name (confidence 0.0 ensures raw name used in PubMed)
    """
    exact_rxcui, approx_rxcui = await asyncio.gather(
        _fetch_rxnorm_cui(client, drug_name),
        _fetch_rxnorm_approximate_rxcui(client, drug_name),
        return_exceptions=True,
    )
    exact_rxcui = exact_rxcui if not isinstance(exact_rxcui, Exception) else None
    approx_rxcui = approx_rxcui if not isinstance(approx_rxcui, Exception) else None
    rxcui = exact_rxcui or approx_rxcui

    # Try to resolve to ingredient (salt) rxcui — this converts brand → generic
    ingredient_rxcui = None
    if rxcui:
        ingredient_rxcui = await _fetch_rxnorm_ingredient(client, rxcui)

    final_rxcui = ingredient_rxcui or rxcui
    resolved_name = await _fetch_rxnorm_name(client, final_rxcui) if final_rxcui else None
    confidence = 0.95 if exact_rxcui else (0.75 if approx_rxcui else 0.0)

    # Fallback: OpenFDA brand search → extract generic_name directly (filter for marketed drugs)
    if not resolved_name or resolved_name.lower() == drug_name.lower():
        fda_data = await _safe_get(
            client,
            "https://api.fda.gov/drug/label.json",
            params={
                "search": f'openfda.brand_name:"{drug_name}" AND (openfda.product_type:"HUMAN+PRESCRIPTION+DRUG" OR openfda.product_type:"OTC+DRUG")',
                "limit": 1,
                **({"api_key": settings.openfda_api_key} if settings.openfda_api_key else {}),
            },
        )
        if fda_data and fda_data.get("results"):
            generic = _first(
                fda_data["results"][0].get("openfda", {}).get("generic_name")
            )
            if generic and generic.lower() != drug_name.lower():
                resolved_name = generic
                confidence = 0.82

    # Non-FDA fallbacks for drugs not found in US registries (EU/UK/India drugs)
    if not resolved_name or resolved_name.lower() == drug_name.lower():
        # Try ChEMBL, PubChem, EMA in parallel
        chembl_result, pubchem_result, ema_result = await asyncio.gather(
            _fetch_chembl_identity(client, drug_name),
            _fetch_pubchem_identity(client, drug_name),
            _fetch_ema_identity(client, drug_name),
            return_exceptions=True,
        )

        candidates = []
        if isinstance(chembl_result, tuple) and chembl_result[0]:
            candidates.append(chembl_result)
        if isinstance(pubchem_result, tuple) and pubchem_result[0]:
            candidates.append(pubchem_result)
        if isinstance(ema_result, tuple) and ema_result[0]:
            candidates.append(ema_result)

        if candidates:
            # Pick highest confidence candidate
            resolved_name, confidence = max(candidates, key=lambda x: x[1])

    if resolved_name and resolved_name.lower() != drug_name.lower():
        return {
            "query_name": drug_name,
            "resolved_name": resolved_name,
            "rxcui": final_rxcui,
            "confidence": confidence,
        }
    # CRITICAL: Always return at least the original drug_name with confidence 0.0
    # This ensures raw drug_name is passed to PubMed even when resolution fails
    return {
        "query_name": drug_name,
        "resolved_name": drug_name,
        "rxcui": rxcui,
        "confidence": 0.0,
    }


# ------------------------------------------------------------------
# PubMed Entrez (guidelines + systematic reviews)
# ------------------------------------------------------------------


async def _fetch_pubmed_abstracts(
    client: httpx.AsyncClient, entity: str, pub_type: str = "guideline", extra_journal_filter: str | None = None
) -> tuple[list, set[str]]:
    """Fetch PubMed guideline or systematic review abstracts for an entity.

    Searches the broad PubMed corpus first. Journal registry is used only to boost
    priority sources, never to restrict the evidence base.

    Returns: (articles, sources_used)
    """
    from datetime import datetime as _dt
    cur_year = _dt.now().year

    if pub_type == "guideline":
        pt_filter = "(Practice Guideline[pt] OR Guideline[pt])"
        retmax = 16
        broad_fallback = (
            f"{entity}[Title/Abstract] AND (guideline OR consensus OR recommendation) "
            f"AND {_EVIDENCE_SEARCH_START_YEAR}:{cur_year}[dp]"
        )
    else:
        pt_filter = "(Systematic Review[pt] OR Meta-Analysis[pt])"
        retmax = 12
        broad_fallback = (
            f"{entity}[Title/Abstract] AND (systematic review OR meta-analysis OR review) "
            f"AND 2010:{cur_year}[dp]"
        )

    term = f"{entity}[Title/Abstract] AND {pt_filter} AND {_EVIDENCE_SEARCH_START_YEAR}:{cur_year}[dp]"
    journal_filter = extra_journal_filter or _get_journal_filter(entity)
    journal_term = (
        f"{entity}[Title/Abstract] AND {journal_filter} AND {_EVIDENCE_SEARCH_START_YEAR}:{cur_year}[dp]"
        if journal_filter
        else None
    )
    search_tasks = [
        _pubmed_esearch_throttled(client, term, retmax, sort="pub_date"),
        _pubmed_esearch_throttled(client, broad_fallback, max(retmax // 2, 6)),
    ]
    if journal_term:
        search_tasks.append(_pubmed_esearch_throttled(client, journal_term, 6))

    search_results = await asyncio.gather(*search_tasks, return_exceptions=True)

    all_ids: list[str] = []
    seen_ids: set[str] = set()
    for result in search_results:
        if isinstance(result, Exception) or not result:
            continue
        for pid in result:
            if pid not in seen_ids:
                seen_ids.add(pid)
                all_ids.append(pid)

    if not all_ids:
        return [], set()

    articles, sources = await _pubmed_efetch(client, all_ids)
    return articles, sources


async def _pubmed_esearch(
    client: httpx.AsyncClient, term: str, retmax: int, sort: str = "relevance"
) -> list[str]:
    """PubMed esearch → list of PMID strings with exponential-backoff retry."""
    params: dict = {
        "db": "pubmed",
        "term": term,
        "retmax": retmax,
        "retmode": "json",
        "sort": sort,
    }
    api_key = _ncbi_key_ctx.get(None) or settings.pubmed_api_key
    if api_key:
        params["api_key"] = api_key

    for attempt in range(3):
        search_data = await _safe_get(
            client,
            "https://eutils.ncbi.nlm.nih.gov/entrez/eutils/esearch.fcgi",
            params=params,
        )
        if search_data:
            return search_data.get("esearchresult", {}).get("idlist", [])
        if attempt < 2:
            await asyncio.sleep(2 ** attempt)  # 1s, 2s
    logger.warning("PubMed esearch failed after 3 attempts: %s", term[:80])
    return []


async def _pubmed_esearch_throttled(
    client: httpx.AsyncClient, term: str, retmax: int, sort: str = "relevance"
) -> list[str]:
    """PubMed esearch with rate limiting: respects NCBI's 3 req/s (no key) or 10 req/s (with key)."""
    api_key = _ncbi_key_ctx.get(None) or settings.pubmed_api_key
    sem = _PUBMED_SEM_WITH_KEY if api_key else _PUBMED_SEM_NO_KEY
    gap = _PUBMED_MIN_GAP_WITH_KEY if api_key else _PUBMED_MIN_GAP_NO_KEY

    async with sem:
        _delay = 0.15 if api_key else 0.4
        await asyncio.sleep(_delay)
        return await _pubmed_esearch(client, term, retmax, sort=sort)


async def _pubmed_efetch(client: httpx.AsyncClient, ids: list[str], *, skip_snowball: bool = False) -> tuple[list, set[str]]:
    """PubMed efetch → parsed article list, enriched with PMC full text or Unpaywall PDF.

    Returns: (articles, sources_used) where sources_used is a set like
    {'PMC (full text)', 'Unpaywall (open access PDF)'}
    """
    if not ids:
        return [], set()
    fetch_params: dict = {
        "db": "pubmed",
        "id": ",".join(ids),
        "rettype": "abstract",
        "retmode": "xml",
    }
    if settings.pubmed_api_key:
        fetch_params["api_key"] = settings.pubmed_api_key

    xml_text = await _safe_get_text(
        client,
        "https://eutils.ncbi.nlm.nih.gov/entrez/eutils/efetch.fcgi",
        params=fetch_params,
    )
    if not xml_text:
        return [], set()
    articles = _parse_pubmed_xml(xml_text)
    # Enrich with PMC full text (free) and Unpaywall PDF (free, needs user email from context)
    articles, sources = await _enrich_abstracts_with_fulltext(
        client, articles, user_email=_user_email_ctx.get()
    )

    # Reference snowballing: one extra efetch for cited PMIDs from primary article set
    if settings.snowball_enabled and not skip_snowball and articles:
        articles, _snow_sources = await _snowball_references(
            client, articles, max_refs=settings.snowball_max_refs
        )
        sources |= _snow_sources

    return articles, sources


async def _snowball_references(
    client: httpx.AsyncClient,
    articles: list,
    *,
    max_refs: int = 15,
) -> tuple[list, set[str]]:
    """Fetch cited articles referenced by the primary article set (one extra PubMed call).
    Called only from _pubmed_efetch with skip_snowball=True to prevent infinite recursion.
    """
    primary_pmids: set[str] = {a["pmid"] for a in articles if a.get("pmid")}
    candidates: list[str] = []
    seen_candidates: set[str] = set()
    for art in articles:
        for pid in (art.get("ref_pmids") or []):
            if pid not in primary_pmids and pid not in seen_candidates:
                seen_candidates.add(pid)
                candidates.append(pid)
    if not candidates:
        return articles, set()
    snow_articles, snow_sources = await _pubmed_efetch(
        client, candidates[:max_refs], skip_snowball=True  # CRITICAL: prevents infinite recursion
    )
    merged_pmids: set[str] = set(primary_pmids)
    merged: list = list(articles)
    for sa in snow_articles:
        pid = sa.get("pmid")
        if pid and pid not in merged_pmids:
            merged_pmids.add(pid)
            merged.append(sa)
    return merged, snow_sources


async def _fetch_pubmed_classification(client: httpx.AsyncClient, entity: str) -> tuple[list, set[str]]:
    """Fetch PubMed abstracts for classification/staging systems (e.g. WHO groups, NYHA).

    Returns: (articles, sources_used)
    """
    from datetime import datetime

    current_year = datetime.now().year
    term = f"{entity} classification[Title/Abstract] AND 2010:{current_year}[dp]"
    ids = await _pubmed_esearch_throttled(client, term, 4)
    articles, sources = await _pubmed_efetch(client, ids)
    return articles, sources


async def _pubmed_esearch_recent_guidelines(
    client: httpx.AsyncClient, entity: str, retmax: int = 8
) -> list[str]:
    """Search PubMed for recent guidelines, sorted by publication date (most recent first).

    Searches 2-year window for guidelines from authoritative organizations.
    Returns PMIDs ordered by publication date descending.
    """
    from datetime import datetime

    yr = datetime.now().year
    term = (
        f"{entity}[Title/Abstract] AND "
        f"(Practice Guideline[pt] OR Guideline[pt] OR guideline[ti]) AND "
        f"{yr - 2}:{yr}[dp]"
    )
    params = {
        "db": "pubmed",
        "term": term,
        "retmax": retmax,
        "retmode": "json",
        "sort": "pub_date",
    }
    if settings.pubmed_api_key:
        params["api_key"] = settings.pubmed_api_key
    data = await _safe_get(client, "https://eutils.ncbi.nlm.nih.gov/entrez/eutils/esearch.fcgi", params=params)
    return (data or {}).get("esearchresult", {}).get("idlist", [])


def _parse_pubmed_xml(xml_text: str) -> list:
    try:
        root = ET.fromstring(xml_text)
    except ET.ParseError:
        logger.debug("PubMed XML parse error")
        return []

    results = []
    for article in root.findall(".//PubmedArticle"):
        try:
            pmid_el = article.find(".//PMID")
            pmid = pmid_el.text if pmid_el is not None else None

            title_el = article.find(".//ArticleTitle")
            title = "".join(title_el.itertext()) if title_el is not None else ""

            # Structured abstracts have multiple AbstractText elements
            abstract_parts = article.findall(".//AbstractText")
            abstract = " ".join("".join(p.itertext()) for p in abstract_parts).strip()

            year = None
            for year_xpath in (
                ".//ArticleDate/Year",
                ".//PubDate/Year",
                ".//PubDate/MedlineDate",
            ):
                year_el = article.find(year_xpath)
                if year_el is not None and year_el.text:
                    try:
                        year = int(year_el.text[:4])
                        break
                    except (ValueError, TypeError):
                        pass

            collective_el = article.find(".//CollectiveName")
            collective = collective_el.text if collective_el is not None else None

            journal_el = article.find(".//Journal/Title")
            journal = journal_el.text if journal_el is not None else None

            # Extract DOI for Unpaywall/open access lookup
            doi_el = article.find(".//ArticleId[@IdType='doi']")
            doi = doi_el.text if doi_el is not None else None

            # Extract PMCID for PMC full text
            pmcid_el = article.find(".//ArticleId[@IdType='pmc']")
            pmcid = pmcid_el.text if pmcid_el is not None else None

            # Extract cited PMIDs for reference snowballing
            ref_pmids: list[str] = []
            for _ref in article.findall(".//ReferenceList/Reference"):
                for _art_id in _ref.findall(".//ArticleId[@IdType='pubmed']"):
                    if _art_id.text and _art_id.text.strip().isdigit():
                        ref_pmids.append(_art_id.text.strip())
            if ref_pmids:
                result_dict = {
                    "pmid": pmid,
                    "title": title,
                    "abstract": abstract if abstract else "(No abstract available)",
                    "year": year,
                    "collective_name": collective,
                    "journal": journal,
                    "doi": doi,
                    "pmcid": pmcid,
                    "ref_pmids": ref_pmids,
                }
            else:
                result_dict = {
                    "pmid": pmid,
                    "title": title,
                    "abstract": abstract if abstract else "(No abstract available)",
                    "year": year,
                    "collective_name": collective,
                    "journal": journal,
                    "doi": doi,
                    "pmcid": pmcid,
                }

            if title:
                results.append(result_dict)
        except Exception:
            continue

    return results


async def _check_for_superseding_guideline(
    client: httpx.AsyncClient, article: dict
) -> dict | None:
    """Check if there's a newer version of a guideline from the same society.

    If a newer version is found, return it. Otherwise return None.
    Used to replace older guidelines with newer ones automatically.
    """
    article_year = article.get("year", 0)
    article_title = article.get("title", "").lower()
    article_journal = article.get("journal", "").lower()

    # Try to identify the guideline society from the journal or title
    society_name = None
    for society in GUIDELINE_SOCIETIES.keys():
        if society.lower() in article_journal or society.lower() in article_title:
            society_name = society
            break

    if not society_name:
        return None  # Can't identify society, can't check for newer version

    expected_update_years = GUIDELINE_SOCIETIES[society_name]["update_years"]
    from datetime import datetime
    current_year = datetime.now().year

    # If the article is very recent (< 1 year old), unlikely there's a newer version
    if current_year - article_year < 1:
        return None

    # Search for newer guidelines from the same society
    search_term = f"{society_name} guideline {current_year - expected_update_years}:{current_year}[dp]"
    newer_ids = await _pubmed_esearch_throttled(client, search_term, 3, sort="pub_date")
    if not newer_ids:
        return None

    # Fetch the newest result
    articles, _ = await _pubmed_efetch(client, [newer_ids[0]], skip_snowball=True)
    if articles:
        return articles[0]
    return None


# ------------------------------------------------------------------
# PMC full text + Unpaywall open-access enrichment
# ------------------------------------------------------------------


async def _fetch_pmc_fulltext(client: httpx.AsyncClient, pmcid: str) -> str | None:
    """Fetch full article text from PubMed Central OA (free, no key needed).

    pmcid should be like 'PMC12345' or just '12345'.
    Returns plain text of the article body, or None on failure.
    """
    numeric_id = pmcid.lstrip("PMCpmc")
    params: dict = {
        "db": "pmc",
        "id": numeric_id,
        "rettype": "xml",
        "retmode": "xml",
    }
    if settings.pubmed_api_key:
        params["api_key"] = settings.pubmed_api_key

    xml_text = await _safe_get_text(
        client,
        "https://eutils.ncbi.nlm.nih.gov/entrez/eutils/efetch.fcgi",
        params=params,
    )
    if not xml_text:
        return None
    try:
        root = ET.fromstring(xml_text)
        parts: list[str] = []
        # Extract all paragraph text from article body
        for tag in ("abstract", "body", "sec", "p"):
            for el in root.iter(tag):
                text = "".join(el.itertext()).strip()
                if text and len(text) > 50:
                    parts.append(text)
        return " ".join(parts)[:8000] if parts else None
    except Exception:
        return None


async def _fetch_unpaywall(
    client: httpx.AsyncClient,
    doi: str,
    user_email: str,
) -> str | None:
    """Look up a free PDF via Unpaywall (free API, requires user email).

    Returns extracted article text or None if unavailable.
    """
    if not doi or not user_email:
        return None

    data = await _safe_get(
        client,
        f"https://api.unpaywall.org/v2/{doi}",
        params={"email": user_email},
    )
    if not data:
        return None

    oa_location = data.get("best_oa_location") or {}
    pdf_url = oa_location.get("url_for_pdf") or oa_location.get("url")
    if not pdf_url:
        return None

    try:
        resp = await client.get(pdf_url, timeout=10, follow_redirects=True)
        if resp.status_code != 200:
            return None
        import io
        import pdfplumber
        with pdfplumber.open(io.BytesIO(resp.content)) as pdf:
            pages = [p.extract_text() or "" for p in pdf.pages[:12]]
            text = "\n".join(pages).strip()
        return text[:8000] if text else None
    except Exception:
        return None


async def _enrich_abstracts_with_fulltext(
    client: httpx.AsyncClient,
    articles: list,
    user_email: str | None = None,
) -> tuple[list, set[str]]:
    """Try to upgrade each article from abstract → full text using PMC then Unpaywall.

    Runs all lookups in parallel. Original article dicts are updated in-place with
    a 'full_text' key when richer content is found.

    Returns: (enriched_articles, sources_used) where sources_used is a set like
    {'PMC (full text)', 'Unpaywall (open access PDF)'}
    """
    if not articles:
        return articles, set()

    sources_used: set[str] = set()

    async def _enrich_one(article: dict) -> None:
        nonlocal sources_used
        pmcid = article.get("pmcid")
        doi = article.get("doi")
        full_text: str | None = None

        # Priority 1: PMC free full text (no auth needed)
        if pmcid:
            full_text = await _fetch_pmc_fulltext(client, pmcid)
            if full_text:
                sources_used.add("PMC (full text)")

        # Priority 2: Unpaywall PDF (needs user email, completely free)
        if not full_text and doi and user_email:
            full_text = await _fetch_unpaywall(client, doi, user_email)
            if full_text:
                sources_used.add("Unpaywall (open access PDF)")

        if full_text:
            article["full_text"] = full_text

    await asyncio.gather(*[_enrich_one(a) for a in articles], return_exceptions=True)
    return articles, sources_used


# ------------------------------------------------------------------
# NICE API (optional — skipped if no key)
# ------------------------------------------------------------------


async def _fetch_nice(client: httpx.AsyncClient, entity: str) -> list:
    if not settings.nice_api_key:
        return []

    headers = {"apikey": settings.nice_api_key}
    search_data = await _safe_get(
        client,
        "https://api.nice.org.uk/v1/search",
        params={"q": entity, "type": "guidance", "size": 3},
        headers=headers,
    )
    if not search_data:
        return []

    items = search_data.get("items") or search_data.get("results") or []
    recommendations = []

    for item in items[:2]:
        item_id = item.get("id") or item.get("guidanceId")
        if not item_id:
            continue
        rec_data = await _safe_get(
            client,
            f"https://api.nice.org.uk/v1/guidance/{item_id}/recommendations",
            headers=headers,
        )
        if not rec_data:
            continue
        for r in (rec_data.get("recommendations") or [])[:5]:
            text = r.get("text") or r.get("recommendationText") or ""
            if text:
                pub_date = item.get("publicationDate") or ""
                recommendations.append(
                    {
                        "title": item.get("title", ""),
                        "text": text[:400],
                        "year": pub_date[:4] if pub_date else None,
                        "url": f"https://www.nice.org.uk/guidance/{item_id}",
                        "recommendation": text[:400],
                    }
                )

    return recommendations


# ------------------------------------------------------------------
# MedlinePlus Connect
# ------------------------------------------------------------------


async def _fetch_medlineplus(client: httpx.AsyncClient, entity: str) -> Optional[str]:
    data = await _safe_get(
        client,
        "https://connect.medlineplus.gov/service",
        params={
            "mainSearchCriteria.v.dn": entity,
            "knowledgeResponseType": "application/json",
        },
    )
    if not data:
        return None
    try:
        entries = data.get("feed", {}).get("entry", [])
        if entries:
            summary = entries[0].get("summary", {})
            if isinstance(summary, dict):
                return (summary.get("$") or "")[:500]
            if isinstance(summary, str):
                return summary[:500]
    except Exception:
        pass
    return None


# ------------------------------------------------------------------
# Semantic Scholar (open-access papers)
# ------------------------------------------------------------------


async def _fetch_semantic_scholar(client: httpx.AsyncClient, entity: str) -> list:
    data = await _safe_get(
        client,
        "https://api.semanticscholar.org/graph/v1/paper/search",
        params={
            "query": f"{entity} clinical guideline",
            "fields": "title,abstract,year,openAccessPdf,publicationTypes",
            "limit": 5,
        },
    )
    if not data:
        return []

    papers = []
    for p in data.get("data", []):
        if p.get("openAccessPdf") and p.get("abstract"):
            papers.append(
                {
                    "title": p.get("title", ""),
                    "abstract": (p.get("abstract") or "")[:400],
                    "year": p.get("year"),
                }
            )
    return papers[:3]


# ------------------------------------------------------------------
# DailyMed (fallback for drugs not in OpenFDA)
# ------------------------------------------------------------------


async def _fetch_dailymed(
    client: httpx.AsyncClient, drug_name: str
) -> Optional[DrugFetchResult]:
    search = await _safe_get(
        client,
        "https://dailymed.nlm.nih.gov/dailymed/services/v2/spls.json",
        params={"drug_name": drug_name, "pagesize": 1},
    )
    if not search or not search.get("data"):
        return None

    setid = search["data"][0].get("setid")
    if not setid:
        return None

    spl = await _safe_get(
        client,
        f"https://dailymed.nlm.nih.gov/dailymed/services/v2/spls/{setid}.json",
    )
    if not spl:
        return None

    result = DrugFetchResult(generic_name=drug_name, data_source="dailymed")
    result.label_url = f"https://dailymed.nlm.nih.gov/dailymed/drugInfo.cfm?setid={setid}"

    # DailyMed SPL sections are keyed by LOINC code
    SECTION_MAP = {
        "34067-9": "indications_raw",
        "34068-7": "dosing_raw",
        "34070-3": "contraindications_raw",
        "34071-1": "warnings_raw",
        "34084-4": "adverse_reactions_raw",
        "34073-7": "drug_interactions_raw",
        "43679-0": "mechanism_raw",
        "34090-1": "pharmacokinetics_raw",
        "43684-0": "special_populations_raw",
    }
    MAX_CHARS = {
        "indications_raw": 800,
        "dosing_raw": 800,
        "contraindications_raw": 600,
        "warnings_raw": 500,
        "adverse_reactions_raw": 600,
        "drug_interactions_raw": 600,
        "mechanism_raw": 400,
        "pharmacokinetics_raw": 400,
        "special_populations_raw": 400,
    }

    for section in (spl.get("data") or {}).get("sections") or []:
        code = section.get("code", "")
        field_name = SECTION_MAP.get(code)
        if field_name and section.get("text"):
            setattr(
                result,
                field_name,
                _truncate(section["text"], MAX_CHARS.get(field_name, 500)),
            )

    result.fetch_success = bool(result.indications_raw or result.dosing_raw)
    return result


# ------------------------------------------------------------------
# MedIndia HTML fallback
# ------------------------------------------------------------------


def _build_medindia_url(drug_name: str) -> str:
    """Build a safe MedIndia URL with proper percent-encoding."""
    return f"https://www.medindia.net/drugs/drug_info.asp?drug_name={quote_plus(drug_name)}"


async def _fetch_medindia(
    client: httpx.AsyncClient, drug_name: str
) -> Optional[DrugFetchResult]:
    url = _build_medindia_url(drug_name)
    text = await _safe_get_text(client, url)
    if not text:
        return None

    result = DrugFetchResult(generic_name=drug_name, data_source="medindia")

    # Extract drug class
    class_match = re.search(
        r"(?:Drug Class|Pharmacological Class)[^>]*>.*?([A-Za-z][^<]{10,100})",
        text,
        re.IGNORECASE | re.DOTALL,
    )
    if class_match:
        result.drug_class = class_match.group(1).strip()[:100]

    # Extract uses/indications
    uses_match = re.search(
        r"(?:Uses|Indications).*?<p[^>]*>\s*([^<]{50,600})",
        text,
        re.IGNORECASE | re.DOTALL,
    )
    if uses_match:
        result.indications_raw = uses_match.group(1).strip()[:600]

    # Extract side effects
    se_match = re.search(
        r"(?:Side Effect|Adverse).*?<[pu][^>]*>\s*([^<]{30,500})",
        text,
        re.IGNORECASE | re.DOTALL,
    )
    if se_match:
        result.adverse_reactions_raw = se_match.group(1).strip()[:400]

    result.fetch_success = bool(result.indications_raw or result.drug_class)
    return result


# ------------------------------------------------------------------
# PMC / StatPearls
# ------------------------------------------------------------------


async def _fetch_pmc_statpearls(client: httpx.AsyncClient, entity: str) -> Optional[str]:
    """Fetch StatPearls content from PMC for detailed clinical pharmacology."""
    # Search PMC for StatPearls articles about this entity
    search_data = await _safe_get(
        client,
        "https://eutils.ncbi.nlm.nih.gov/entrez/eutils/esearch.fcgi",
        params={
            "db": "pmc",
            "term": f"{entity}[Title] AND StatPearls[journal]",
            "retmax": 3,
            "retmode": "json",
            **({"api_key": settings.pubmed_api_key} if settings.pubmed_api_key else {}),
        },
    )
    if not search_data:
        return None
    ids = search_data.get("esearchresult", {}).get("idlist", [])
    if not ids:
        # Fallback: broader search
        search_data = await _safe_get(
            client,
            "https://eutils.ncbi.nlm.nih.gov/entrez/eutils/esearch.fcgi",
            params={
                "db": "pmc",
                "term": f"{entity} dosing pharmacology[Title/Abstract] AND StatPearls[journal]",
                "retmax": 2,
                "retmode": "json",
                **({"api_key": settings.pubmed_api_key} if settings.pubmed_api_key else {}),
            },
        )
        if search_data:
            ids = search_data.get("esearchresult", {}).get("idlist", [])
    if not ids:
        return None
    # Fetch abstract/summary from PMC
    fetch_data = await _safe_get_text(
        client,
        "https://eutils.ncbi.nlm.nih.gov/entrez/eutils/efetch.fcgi",
        params={
            "db": "pmc",
            "id": ",".join(ids[:2]),
            "rettype": "abstract",
            "retmode": "text",
            **({"api_key": settings.pubmed_api_key} if settings.pubmed_api_key else {}),
        },
    )
    if fetch_data:
        return fetch_data[:1200]
    return None


# ------------------------------------------------------------------
# NCBI Disease Structured
# ------------------------------------------------------------------


async def _fetch_ncbi_disease_structured(
    client: httpx.AsyncClient, disease_name: str
) -> Optional[str]:
    """Fetch StatPearls monograph sections + MeSH/ClinVar for disease."""
    search_data = await _safe_get(
        client,
        "https://eutils.ncbi.nlm.nih.gov/entrez/eutils/esearch.fcgi",
        params={
            "db": "pmc",
            "term": f"{disease_name}[Title] AND StatPearls[journal]",
            "retmax": 2,
            "retmode": "json",
            **({"api_key": settings.pubmed_api_key} if settings.pubmed_api_key else {}),
        },
    )
    if not search_data:
        return None
    ids = search_data.get("esearchresult", {}).get("idlist", [])
    if not ids:
        return None
    fetch_data = await _safe_get_text(
        client,
        "https://eutils.ncbi.nlm.nih.gov/entrez/eutils/efetch.fcgi",
        params={
            "db": "pmc",
            "id": ",".join(ids[:2]),
            "rettype": "full",
            "retmode": "xml",
            **({"api_key": settings.pubmed_api_key} if settings.pubmed_api_key else {}),
        },
    )
    if not fetch_data:
        return None
        
    try:
        import xml.etree.ElementTree as ET
        root = ET.fromstring(fetch_data)
        sections = []
        for sec in root.findall(".//sec"):
            title_el = sec.find(".//title")
            if title_el is not None and title_el.text:
                title = "".join(title_el.itertext()).strip()
                if any(k in title.lower() for k in ["etiology", "pathophysiology", "history", "evaluation", "treatment", "management", "prognosis", "complication"]):
                    text = " ".join(" ".join(p.itertext()).strip() for p in sec.findall(".//p"))
                    if text:
                        sections.append(f"### {title}\\n{text}")
        if sections:
            return "\\n\\n".join(sections)[:4000]
    except Exception:
        pass
    return None


async def _fetch_ncbi_books(
    client: httpx.AsyncClient,
    query: str,
    max_results: int = 3,
) -> str | None:
    """Fetch relevant content from NCBI Books (StatPearls, Harrison's, GeneReviews, etc).

    Uses NCBI eUtils esearch on db=books, then efetch to retrieve text sections.
    Complements _fetch_ncbi_disease_structured (which uses db=pmc) with
    broader NCBI book content. Returns truncated text or None on any failure.
    """
    try:
        search_data = await _safe_get(
            client,
            "https://eutils.ncbi.nlm.nih.gov/entrez/eutils/esearch.fcgi",
            params={
                "db": "books",
                "term": query,
                "retmax": max_results,
                "retmode": "json",
                **({"api_key": settings.pubmed_api_key} if settings.pubmed_api_key else {}),
            },
        )
        if not search_data:
            return None
        ids = search_data.get("esearchresult", {}).get("idlist", [])
        if not ids:
            return None

        fetch_text = await _safe_get_text(
            client,
            "https://eutils.ncbi.nlm.nih.gov/entrez/eutils/efetch.fcgi",
            params={
                "db": "books",
                "id": ",".join(ids[:max_results]),
                "rettype": "abstract",
                "retmode": "text",
                **({"api_key": settings.pubmed_api_key} if settings.pubmed_api_key else {}),
            },
        )
        if fetch_text and len(fetch_text.strip()) > 50:
            return fetch_text.strip()[:2000]
        return None
    except Exception:
        logger.debug("_fetch_ncbi_books failed for %r", query, exc_info=True)
        return None


async def _fetch_book_monographs(
    client: httpx.AsyncClient, term: str, *, max_chapters: int = 2, char_cap: int = 16000
) -> list[dict]:
    """Fetch FULL StatPearls / NCBI Bookshelf chapter text with citable NBK URLs.

    Returns a list of ``{title, url, text, source, nbk_id}`` — the ENTIRE chapter
    body (not section snippets) so the LLM has complete grounding for broad
    overview sections (pathophysiology, epidemiology, clinical features, etc.).

    Robust chapter selection (NCBI book search alone mis-ranks): search db=books,
    collect distinct candidate chapters in relevance order, fetch the top few
    chapter pages in parallel, and keep the chapters whose <h1> title best
    token-overlaps the query term. Always returns the best-available real
    source(s); never raises (silent [] on failure). LLM-agnostic (plain text + URL).
    """
    import re as _re
    from collections import Counter  # noqa: F401  (kept for future relevance tuning)

    _ESEARCH = "https://eutils.ncbi.nlm.nih.gov/entrez/eutils/esearch.fcgi"
    _ESUMMARY = "https://eutils.ncbi.nlm.nih.gov/entrez/eutils/esummary.fcgi"
    _api = {"api_key": settings.pubmed_api_key} if settings.pubmed_api_key else {}
    _UA = {"User-Agent": "Mozilla/5.0 (compatible; IatronixBot/1.0; +https://med.debkay.com)"}
    try:
        # 1. Candidate chapters. PRECISE [title]-field match first — this returns the
        #    exact disease chapter; body-text search mis-ranks to tangential chapters
        #    (a chapter that merely shares one word). Then broad fallbacks.
        ids: list[str] = []
        _precise = False
        # StatPearls is OPTIONAL enrichment (abstracts alone ground answers). Fail FAST — a
        # single throttled attempt per variant, NO retry loop. Retrying here held the shared
        # NCBI semaphore and starved the main PubMed gather → empty data → cards.
        for _q in (
            f"{term}[title] AND statpearls[book]",   # precise: chapter title match
            f"{term} AND statpearls[book]",          # broad StatPearls
            term,                                     # broad Bookshelf
        ):
            sd = await _ncbi_eutils_get(client, _ESEARCH, params={
                "db": "books", "term": _q, "retmax": 20, "retmode": "json", **_api})
            ids = ((sd or {}).get("esearchresult", {}) or {}).get("idlist", []) or []
            if ids:
                _precise = _q.startswith(f"{term}[title]")
                break
        if not ids:
            return []

        # 2. esummary -> distinct chapter accessions (NBK...) in relevance order.
        su = await _ncbi_eutils_get(client, _ESUMMARY, params={
            "db": "books", "id": ",".join(ids[:20]), "retmode": "json", **_api})
        summ = (su or {}).get("result", {}) or {}
        seen_acc: set[str] = set()
        candidates: list[str] = []
        for i in ids:
            res = summ.get(i, {}) or {}
            acc = res.get("chapteraccessionid") or res.get("bookaccessionid") or res.get("accessionid")
            if acc and str(acc).startswith("NBK") and acc not in seen_acc:
                seen_acc.add(acc)
                candidates.append(acc)
        if not candidates:
            return []

        # 3. Fetch top candidate chapter pages in parallel; score H1 title vs term.
        from lxml import html as _LH

        async def _get_page(acc: str):
            try:
                resp = await client.get(f"https://www.ncbi.nlm.nih.gov/books/{acc}/", headers=_UA, timeout=8.0)
                return acc, (resp.content if resp.status_code == 200 else None)
            except Exception:
                return acc, None

        pages = await asyncio.gather(*[_get_page(a) for a in candidates[:4]])
        term_tokens = set(_re.findall(r"[a-z0-9]+", term.lower()))
        scored: list[tuple] = []
        for acc, content in pages:
            if not content:
                continue
            try:
                doc = _LH.fromstring(content)  # bytes — lxml rejects str with encoding decl
            except Exception:
                continue
            h1 = _re.sub(r"\s+", " ", " ".join(doc.xpath("//h1//text()"))).strip()
            h1 = _re.sub(r"^\s*Bookshelf\s*", "", h1).strip()
            title_tokens = set(_re.findall(r"[a-z0-9]+", h1.lower()))
            score = len(term_tokens & title_tokens)
            scored.append((score, candidates.index(acc), acc, h1, doc))
        if not scored:
            return []
        # Best title-overlap first; break ties by original relevance rank.
        scored.sort(key=lambda x: (-x[0], x[1]))
        # Require a real title match so we never surface a chapter that merely shares
        # one word. Precise [title] hits are already correct (min 1); the broad
        # fallback must clear at least half the disease's title tokens.
        min_overlap = 1 if _precise else max(1, (len(term_tokens) + 1) // 2)
        scored = [s for s in scored if s[0] >= min_overlap]
        if not scored:
            return []

        out: list[dict] = []
        for score, _rank, acc, h1, doc in scored[:max_chapters]:
            for bad in doc.xpath(
                "//script|//style|//nav|//header|//footer|//aside|//form|//button"
            ):
                par = bad.getparent()
                if par is not None:
                    par.remove(bad)
            main_el = doc.xpath("//div[@id='maincontent']") or doc.xpath("//main")
            target = main_el[0] if main_el else doc
            text = _re.sub(r"\s+", " ", " ".join(target.itertext())).strip()
            if len(text) < 400:
                continue
            out.append({
                "title": h1 or "StatPearls",
                "url": f"https://www.ncbi.nlm.nih.gov/books/{acc}/",
                "text": text[:char_cap],
                "source": "StatPearls",
                "nbk_id": acc,
            })
        return out
    except Exception:
        logger.debug("_fetch_book_monographs failed for %r", term, exc_info=True)
        return []


async def _fetch_clinicaltrials(
    client: httpx.AsyncClient,
    query: str,
    max_results: int = 5,
) -> list[dict] | None:
    """Fetch recent completed clinical trial summaries from ClinicalTrials.gov v2 API.

    ClinicalTrials.gov is in ALLOWED_DOMAINS. Returns a list of study summary
    dicts or None on failure. Each dict has: nct_id, title, summary, conditions,
    interventions, status, source='clinicaltrials.gov'.
    """
    try:
        data = await _safe_get(
            client,
            "https://clinicaltrials.gov/api/v2/studies",
            params={
                "query.term": query,
                "pageSize": max_results,
                "filter.overallStatus": "COMPLETED",
                "fields": "NCTId,BriefTitle,BriefSummary,Condition,InterventionName,OverallStatus",
            },
        )
        if not data:
            return None
        studies = data.get("studies") or []
        results: list[dict] = []
        for s in studies:
            proto = s.get("protocolSection") or {}
            id_mod = proto.get("identificationModule") or {}
            desc_mod = proto.get("descriptionModule") or {}
            cond_mod = proto.get("conditionsModule") or {}
            arms_mod = proto.get("armsInterventionsModule") or {}
            status_mod = proto.get("statusModule") or {}
            nct_id = id_mod.get("nctId") or ""
            title = id_mod.get("briefTitle") or ""
            summary = (desc_mod.get("briefSummary") or "")[:600]
            conditions = cond_mod.get("conditions") or []
            interventions = [
                i.get("name") or ""
                for i in (arms_mod.get("interventions") or [])
                if isinstance(i, dict)
            ]
            status = status_mod.get("overallStatus") or ""
            if title:
                results.append({
                    "pmid": None,
                    "title": title,
                    "abstract": summary,
                    "year": None,
                    "journal": "ClinicalTrials.gov",
                    "doi": None,
                    "pmcid": None,
                    "nct_id": nct_id,
                    "conditions": conditions,
                    "interventions": interventions,
                    "status": status,
                    "pub_types": ["Clinical Trial"],
                    "source": "clinicaltrials.gov",
                })
        return results if results else None
    except Exception:
        logger.debug("_fetch_clinicaltrials failed for %r", query, exc_info=True)
        return None

# ------------------------------------------------------------------
# ChEMBL — additional drug pharmacology source
# ------------------------------------------------------------------


async def _fetch_chembl(client: httpx.AsyncClient, drug_name: str) -> Optional[dict]:
    """Fetch drug mechanism, drug class, and targets from ChEMBL. Redis-cached for 7 days."""
    # Check Redis cache first
    try:
        import redis.asyncio as aioredis
        _r = aioredis.from_url(settings.redis_url, decode_responses=True)
        cache_key = f"chembl:{drug_name.lower().strip()}"
        cached = await _r.get(cache_key)
        await _r.aclose()
        if cached is not None:
            return json.loads(cached) if cached != "null" else None
    except Exception:
        _r = None

    result = await _fetch_chembl_remote(client, drug_name)

    # Store in Redis (7 days TTL — ChEMBL data rarely changes)
    try:
        _r2 = aioredis.from_url(settings.redis_url, decode_responses=True)
        await _r2.setex(cache_key, 604800, json.dumps(result))
        await _r2.aclose()
    except Exception:
        pass

    return result


async def _fetch_chembl_remote(client: httpx.AsyncClient, drug_name: str) -> Optional[dict]:
    """Internal: actually fetch from ChEMBL API."""
    search = await _safe_get(
        client,
        "https://www.ebi.ac.uk/chembl/api/data/molecule.json",
        params={"pref_name__iexact": drug_name, "format": "json", "limit": 1},
    )
    if not search or not search.get("molecules"):
        # Fallback: case-insensitive contains search
        search = await _safe_get(
            client,
            "https://www.ebi.ac.uk/chembl/api/data/molecule.json",
            params={"pref_name__icontains": drug_name, "format": "json", "limit": 1},
        )
    if not search or not search.get("molecules"):
        return None

    mol = search["molecules"][0]
    chembl_id = mol.get("molecule_chembl_id")
    if not chembl_id:
        return None

    # Fetch mechanism of action
    mech_data = await _safe_get(
        client,
        f"https://www.ebi.ac.uk/chembl/api/data/mechanism.json",
        params={"molecule_chembl_id": chembl_id, "format": "json", "limit": 5},
    )
    mechanisms = []
    if mech_data and mech_data.get("mechanisms"):
        for m in mech_data["mechanisms"][:3]:
            mech_str = m.get("mechanism_of_action") or ""
            target = m.get("target_chembl_id") or ""
            if mech_str:
                mechanisms.append(mech_str)

    drug_class = None
    if mol.get("molecule_properties"):
        # ChEMBL stores therapeutic flags
        pass
    atc = mol.get("atc_classifications", [])
    if atc:
        drug_class = atc[0]  # ATC code

    if not mechanisms and not drug_class:
        return None

    return {
        "chembl_id": chembl_id,
        "mechanism": "; ".join(mechanisms[:2]) if mechanisms else None,
        "atc_class": drug_class,
    }


# ------------------------------------------------------------------
# RxNorm Drug-Drug Interaction API
# ------------------------------------------------------------------


async def _fetch_rxnorm_interactions(
    client: httpx.AsyncClient, rxcui1: str, rxcui2: str
) -> list[dict]:
    """Fetch drug-drug interactions between two drugs using RxNav interaction API.

    Returns a list of interaction dicts with severity, description, drugs involved.
    Endpoint: rxnav.nlm.nih.gov/REST/interaction/list.json?rxcuis=rxcui1+rxcui2
    """
    data = await _safe_get(
        client,
        "https://rxnav.nlm.nih.gov/REST/interaction/list.json",
        params={"rxcuis": f"{rxcui1} {rxcui2}"},
    )
    if not data:
        return []

    interactions = []
    full_interaction_type_group = data.get("fullInteractionTypeGroup", [])
    for group in full_interaction_type_group:
        source = group.get("sourceName", "RxNorm")
        for fit in group.get("fullInteractionType", []):
            for pair in fit.get("interactionPair", []):
                severity = pair.get("severity", "").lower()
                description = pair.get("description", "")
                drugs = [
                    c.get("minConceptItem", {}).get("name", "")
                    for c in pair.get("interactionConcept", [])
                    if c.get("minConceptItem")
                ]
                if description:
                    interactions.append({
                        "severity": severity or "moderate",
                        "description": description[:500],
                        "drugs": [d for d in drugs if d],
                        "source": source,
                    })
    return interactions[:10]


# ------------------------------------------------------------------
# High-level fetch orchestrators
# ------------------------------------------------------------------


async def fetch_drug_data(drug_name: str, *, extra_pubmed_terms: list[str] | None = None, extra_journal_filter: str | None = None) -> DrugFetchResult:
    """Fetch drug data using live online resolution rather than local repositories."""
    result = DrugFetchResult()

    async with _make_client() as client:
        resolution = await _resolve_drug_name_online(client, drug_name)
        search_name = resolution.get("resolved_name") or drug_name
        result.generic_name = search_name
        if search_name.lower() != drug_name.lower():
            result.brand_name = drug_name
        if resolution.get("rxcui"):
            result.rxcui = resolution["rxcui"]

        # Build main search tasks
        _tasks = [
            _fetch_fda_label(client, search_name),
            _fetch_fda_events(client, search_name),
            _fetch_rxnorm_class_chain(client, search_name),
            _fetch_pubmed_abstracts(client, search_name, "guideline", extra_journal_filter),
            _fetch_pubmed_abstracts(client, search_name, "systematic_review", extra_journal_filter),
            _pubmed_esearch_recent_guidelines(client, search_name, 8),
            _fetch_dailymed(client, search_name),
            _fetch_medlineplus(client, search_name),
            _fetch_pmc_statpearls(client, search_name),
            _fetch_chembl(client, search_name),
            _fetch_ncbi_books(client, search_name + " pharmacology dosing"),
        ]

        # LLM-expanded MeSH terms — additive on top of hardcoded searches above
        if extra_pubmed_terms:
            import datetime as _dt
            _cur_year = _dt.datetime.now().year
            _date_suffix = f" AND {_EVIDENCE_SEARCH_START_YEAR}:{_cur_year}[dp]"
            for _t in extra_pubmed_terms[:3]:  # cap at 3 extra terms per call
                if _t and len(_t) > 10 and "[dp]" not in _t:  # skip empty/malformed/already-dated
                    _tasks.append(
                        _pubmed_esearch_throttled(client, _t + _date_suffix, 8)
                    )

        (
            fda_label,
            fda_events,
            rxnorm_class,
            guidelines_result,
            sysreviews_result,
            recent_guidelines_ids,
            dailymed_result,
            medlineplus_drug,
            statpearls_text,
            chembl_data,
            ncbi_books_drug,
            *_extra_esearch_results,
        ) = await asyncio.gather(*_tasks, return_exceptions=True)

        # Unpack guidelines and sysreviews results (now tuples with sources)
        guidelines = []
        guidelines_sources = set()
        if isinstance(guidelines_result, tuple):
            guidelines, guidelines_sources = guidelines_result
        elif isinstance(guidelines_result, list):
            guidelines = guidelines_result

        # Fetch recent guidelines if any — prepend so they survive the _cap_abstracts budget
        if isinstance(recent_guidelines_ids, list) and recent_guidelines_ids:
            recent_articles, recent_sources = await _pubmed_efetch(client, recent_guidelines_ids)
            guidelines = recent_articles + guidelines
            for src in recent_sources:
                guidelines_sources.add(src)

        # Fetch expanded PubMed search results from LLM-generated terms
        if _extra_esearch_results:
            _extra_pmids = []
            for _res in _extra_esearch_results:
                if isinstance(_res, list):
                    _extra_pmids.extend(_res[:8])  # take first 8 from each extra search
            if _extra_pmids:
                _extra_articles, _extra_sources = await _pubmed_efetch(client, _extra_pmids)
                guidelines = guidelines + _extra_articles
                for src in _extra_sources:
                    guidelines_sources.add(src)

        sysreviews = []
        sysreviews_sources = set()
        if isinstance(sysreviews_result, tuple):
            sysreviews, sysreviews_sources = sysreviews_result
        elif isinstance(sysreviews_result, list):
            sysreviews = sysreviews_result

        _merge_fda_label(
            result, fda_label if not isinstance(fda_label, Exception) else None
        )
        if isinstance(fda_label, dict) and fda_label.get("results"):
            if "FDA" not in result.data_sources:
                result.data_sources.append("FDA")

        result.top_adverse_events = fda_events if isinstance(fda_events, list) else []
        if result.top_adverse_events:
            if "FDA" not in result.data_sources:
                result.data_sources.append("FDA")

        guidelines_list = _cap_abstracts(guidelines if guidelines else [], 3000)
        result.guideline_abstracts = guidelines_list
        if guidelines_list and "PubMed" not in result.data_sources:
            result.data_sources.append("PubMed")
        # Add PMC/Unpaywall sources from enrichment
        for src in guidelines_sources:
            if src not in result.data_sources:
                result.data_sources.append(src)

        sysreviews_list = _cap_abstracts(sysreviews if sysreviews else [], 3000)
        result.systematic_review_abstracts = sysreviews_list
        if sysreviews_list and "PubMed" not in result.data_sources:
            result.data_sources.append("PubMed")
        # Add PMC/Unpaywall sources from enrichment
        for src in sysreviews_sources:
            if src not in result.data_sources:
                result.data_sources.append(src)
        # Apply RxNorm class from chain result (no sequential round-trip needed)
        if (
            isinstance(rxnorm_class, str)
            and rxnorm_class
            and not result.drug_class_rxnorm
        ):
            result.drug_class_rxnorm = rxnorm_class
            if "RxNorm" not in result.data_sources:
                result.data_sources.append("RxNorm")

        if isinstance(medlineplus_drug, str) and medlineplus_drug:
            if result.indications_raw:
                result.indications_raw = result.indications_raw + f"\n[MedlinePlus]: {medlineplus_drug[:300]}"
            else:
                result.indications_raw = medlineplus_drug[:400]
            if "MedlinePlus" not in result.data_sources:
                result.data_sources.append("MedlinePlus")

        if isinstance(statpearls_text, str) and statpearls_text:
            if result.dosing_raw:
                result.dosing_raw = result.dosing_raw + f"\n[StatPearls]: {statpearls_text[:400]}"
            else:
                result.dosing_raw = statpearls_text[:600]
            if "PMC (full text)" not in result.data_sources:
                result.data_sources.append("PMC (full text)")

        # NCBI Books drug content — append to StatPearls if available
        if isinstance(ncbi_books_drug, str) and ncbi_books_drug.strip():
            if result.mechanism_raw:
                result.mechanism_raw = result.mechanism_raw + "\n\n[NCBI Books]\n" + ncbi_books_drug[:600]
            else:
                result.mechanism_raw = "[NCBI Books]\n" + ncbi_books_drug[:600]
            if "NCBI Books" not in result.data_sources:
                result.data_sources.append("NCBI Books")

        # ChEMBL: enrich mechanism and drug class
        if isinstance(chembl_data, dict) and chembl_data:
            if chembl_data.get("mechanism") and not result.mechanism_raw:
                result.mechanism_raw = f"[ChEMBL]: {chembl_data['mechanism']}"
            elif chembl_data.get("mechanism"):
                result.mechanism_raw += f"\n[ChEMBL]: {chembl_data['mechanism']}"
            if chembl_data.get("atc_class") and not result.drug_class:
                result.drug_class = chembl_data["atc_class"]
            result.chembl_mechanism = chembl_data.get("mechanism")
            result.chembl_atc_class = chembl_data.get("atc_class")

        # OpenFDA→RxNorm fallback (item 20): if no indications, try DailyMed before giving up
        if not result.indications_raw and not result.dosing_raw:
            logger.info(
                "OpenFDA returned no label for '%s' — trying DailyMed fallback", search_name
            )

        if result.fetch_success:
            return result

        dm = dailymed_result if not isinstance(dailymed_result, Exception) else None
        if dm and dm.fetch_success:
            if search_name.lower() != drug_name.lower():
                dm.brand_name = drug_name
                dm.generic_name = search_name
            dm.rxcui = result.rxcui
            dm.guideline_abstracts = result.guideline_abstracts
            dm.systematic_review_abstracts = result.systematic_review_abstracts
            dm.top_adverse_events = result.top_adverse_events
            dm.data_sources = result.data_sources.copy()
            if "DailyMed" not in dm.data_sources:
                dm.data_sources.append("DailyMed")
            return dm

        mi = await _fetch_medindia(client, drug_name)
        if (not mi or not mi.fetch_success) and search_name.lower() != drug_name.lower():
            mi = await _fetch_medindia(client, search_name)
        if mi and mi.fetch_success:
            if search_name.lower() != drug_name.lower():
                mi.brand_name = drug_name
                mi.generic_name = search_name
            mi.rxcui = result.rxcui
            mi.guideline_abstracts = result.guideline_abstracts
            mi.systematic_review_abstracts = result.systematic_review_abstracts
            mi.top_adverse_events = result.top_adverse_events
            mi.data_sources = result.data_sources.copy()
            if "Medindia" not in mi.data_sources:
                mi.data_sources.append("Medindia")
            return mi

    return result


def _as_pmid_list(value) -> list[str]:
    """Coerce a PubMed esearch result into a list of PMID strings.

    Drops any non-string item. Guards the guideline/review id-merge against
    sources that return dicts (e.g. ClinicalTrials.gov) being swept in by index
    drift — a dict inside ``set(...)`` raises ``TypeError: unhashable type:
    'dict'`` and previously crashed the entire disease fetch (silently, via
    ``fetch_node``), leaving disease answers ungrounded.
    """
    if not isinstance(value, list):
        return []
    out: list[str] = []
    for item in value:
        if isinstance(item, str) and item.strip():
            out.append(item.strip())
        elif isinstance(item, int):
            out.append(str(item))
    return out


async def fetch_disease_data(disease_name: str, *, extra_pubmed_terms: list[str] | None = None, extra_journal_filter: str | None = None) -> DiseaseFetchResult:
    """Fetch disease data from PubMed (guidelines + reviews + classification), NICE,
    MedlinePlus, and Semantic Scholar — all in parallel.

    Speed optimization: runs all PubMed esearch + non-PubMed sources in parallel
    (Phase 1), then batches all PMIDs into a single efetch call (Phase 2).
    This cuts 3 sequential efetch calls down to 1.
    """
    result = DiseaseFetchResult(disease_name=disease_name)

    from datetime import datetime as _dt
    cur_year = _dt.now().year
    journal_filter = extra_journal_filter or _get_journal_filter(disease_name)

    # Build PubMed search terms
    guideline_term = f"{disease_name}[Title/Abstract] AND (Practice Guideline[pt] OR Guideline[pt]) AND {_EVIDENCE_SEARCH_START_YEAR}:{cur_year}[dp]"
    review_term = f"{disease_name}[Title/Abstract] AND (Systematic Review[pt] OR Meta-Analysis[pt]) AND {_EVIDENCE_SEARCH_START_YEAR}:{cur_year}[dp]"
    classification_term = (
        f"{disease_name} classification[Title/Abstract] AND {_EVIDENCE_SEARCH_START_YEAR}:{cur_year}[dp]"
    )
    broad_guideline_term = (
        f"{disease_name}[Title/Abstract] AND (guideline OR consensus OR recommendation) AND {_EVIDENCE_SEARCH_START_YEAR}:{cur_year}[dp]"
    )
    broad_review_term = (
        f"{disease_name}[Title/Abstract] AND (systematic review OR meta-analysis OR review) AND {_EVIDENCE_SEARCH_START_YEAR}:{cur_year}[dp]"
    )
    journal_term = (
        f"{disease_name}[Title/Abstract] AND {journal_filter} AND {_EVIDENCE_SEARCH_START_YEAR}:{cur_year}[dp]"
        if journal_filter
        else None
    )

    async with _make_client() as client:
        # StatPearls/Bookshelf full chapter — kicked off CONCURRENTLY so it OVERLAPS the main
        # gather (no added critical-path latency; a sequential fetch pushed total past the
        # 31s fetch timeout → empty data → cards). Safe to run concurrently because its eUtils
        # calls go through _ncbi_eutils_get (shared NCBI rate-limit semaphore → no 429).
        _book_task = asyncio.ensure_future(_fetch_book_monographs(client, disease_name))
        # Phase 1: ALL esearch calls + non-PubMed sources in parallel
        # NOTE: Limited to 4 esearch calls to avoid NCBI rate limits (3 req/s without key, 10 req/s with)
        tasks: list = [
            _pubmed_esearch_throttled(client, guideline_term, 16, sort="pub_date"),
            _pubmed_esearch_throttled(client, review_term, 12, sort="pub_date"),
            _pubmed_esearch_throttled(client, broad_guideline_term, 8, sort="pub_date"),
            _pubmed_esearch_throttled(client, broad_review_term, 8, sort="pub_date"),
            _pubmed_esearch_recent_guidelines(client, disease_name, 8),
            _fetch_nice(client, disease_name),
            _fetch_medlineplus(client, disease_name),
            _fetch_semantic_scholar(client, disease_name),
            # Dead UN-THROTTLED NCBI calls removed — they bypassed the rate semaphore (raw
            # _safe_get) and triggered 429s, and returned nothing now that _fetch_book_monographs
            # (throttled) handles StatPearls/Bookshelf. Kept as no-op placeholders to preserve the
            # positional result[] indices below (results[8]=None, results[9]=None).
            asyncio.sleep(0, result=None),  # was _fetch_ncbi_disease_structured (db=pmc — never matched)
            asyncio.sleep(0, result=None),  # was _fetch_ncbi_books (efetch rettype unsupported)
            _fetch_clinicaltrials(client, disease_name),
        ]

        # LLM-expanded MeSH terms — additive on top of hardcoded searches above
        if extra_pubmed_terms:
            _cur_year = _dt.now().year
            _date_suffix = f" AND {_EVIDENCE_SEARCH_START_YEAR}:{_cur_year}[dp]"
            for _t in extra_pubmed_terms[:3]:  # cap at 3 extra terms per call
                if _t and len(_t) > 10 and "[dp]" not in _t:  # skip empty/malformed/already-dated
                    tasks.append(
                        _pubmed_esearch_throttled(client, _t + _date_suffix, 8)
                    )

        # Use asyncio.wait so partial results are recovered if some calls time out
        task_objs = [asyncio.ensure_future(coro) for coro in tasks]
        done, pending = await asyncio.wait(
            task_objs, timeout=settings.api_fetch_timeout_seconds
        )
        for t in pending:
            t.cancel()
        results = []
        for t in task_objs:
            if t.done() and not t.cancelled():
                try:
                    results.append(t.result())
                except Exception as e:
                    results.append(e)
            else:
                results.append(None)

        guideline_ids = results[0] if isinstance(results[0], list) else []
        review_ids = results[1] if isinstance(results[1], list) else []
        broad_guideline_ids = results[2] if isinstance(results[2], list) else []
        broad_review_ids = results[3] if isinstance(results[3], list) else []
        recent_guideline_ids = results[4] if isinstance(results[4], list) else []
        nice_recs = results[5] if isinstance(results[5], list) else []
        medlineplus = results[6] if isinstance(results[6], str) else None
        semantic = results[7] if isinstance(results[7], list) else []
        ncbi = results[8] if len(results) > 8 and isinstance(results[8], str) else None
        ncbi_books = results[9] if len(results) > 9 and isinstance(results[9], str) else None
        ct_studies = results[10] if len(results) > 10 and isinstance(results[10], list) else []

        # Extract expanded PubMed search results from LLM-generated terms.
        # Extra term searches are appended AFTER the 11 fixed sources (indices
        # 0..10), so they begin at index 11. _as_pmid_list is a second guard: any
        # source that is not a list of PMID strings (e.g. ClinicalTrials.gov dicts
        # at results[10]) is filtered out so it can never crash the set() merge.
        extra_guideline_ids: list[str] = []
        for _res in results[11:]:
            extra_guideline_ids.extend(_as_pmid_list(_res)[:8])  # first 8 PMIDs per extra search

        # Merge and deduplicate PMIDs (recent guidelines takes priority)
        unique_guideline_ids = list(
            set(_as_pmid_list(recent_guideline_ids))
            | set(_as_pmid_list(guideline_ids))
            | set(_as_pmid_list(broad_guideline_ids))
            | set(extra_guideline_ids)
        )
        all_review_ids = list(set(_as_pmid_list(review_ids)) | set(_as_pmid_list(broad_review_ids)))
        all_pmids = set(unique_guideline_ids) | set(all_review_ids)
        all_ids = list(all_pmids)
        if all_ids:
            all_abstracts, enrichment_sources = await _pubmed_efetch(client, all_ids)
            for src in enrichment_sources:
                if src not in result.data_sources:
                    result.data_sources.append(src)
        else:
            all_abstracts = []

        # Tag abstracts by which search found them (by PMID membership)
        guideline_set = set(unique_guideline_ids)
        review_set = set(all_review_ids)
        guideline_abstracts = []
        review_abstracts = []
        for a in all_abstracts:
            pmid = a.get("pmid")
            if pmid in guideline_set:
                guideline_abstracts.append(a)
            if pmid in review_set:
                review_abstracts.append(a)

        result.guideline_abstracts = _cap_abstracts(guideline_abstracts, 12000)
        result.systematic_review_abstracts = _cap_abstracts(review_abstracts, 7000)
        if result.guideline_abstracts:
            guideline_titles = [
                str(a.get("title", "")).strip()
                for a in result.guideline_abstracts[:3]
                if isinstance(a, dict) and str(a.get("title", "")).strip()
            ]
            if guideline_titles:
                result.guideline_summary = "\n".join(f"- {title}" for title in guideline_titles)
        if result.guideline_abstracts or result.systematic_review_abstracts:
            if "PubMed" not in result.data_sources:
                result.data_sources.append("PubMed")
        result.nice_recommendations = nice_recs
        if nice_recs and "NICE" not in result.data_sources:
            result.data_sources.append("NICE")
        result.medlineplus_summary = medlineplus
        if medlineplus and "MedlinePlus" not in result.data_sources:
            result.data_sources.append("MedlinePlus")
        result.semantic_papers = semantic
        result.ncbi_structured = ncbi

        # NCBI Books — use as fallback if StatPearls/PMC found nothing
        if ncbi_books and not result.ncbi_structured:
            result.ncbi_structured = ncbi_books
        elif ncbi_books and result.ncbi_structured:
            # Append book content if both exist (truncated to avoid prompt bloat)
            result.ncbi_structured = result.ncbi_structured + "\n\n" + ncbi_books[:800]

        # ClinicalTrials.gov — add completed trials as guideline-level abstracts
        if ct_studies:
            if "ClinicalTrials.gov" not in result.data_sources:
                result.data_sources.append("ClinicalTrials.gov")
            # Add to guideline_abstracts so they're included in evidence scoring
            result.guideline_abstracts = result.guideline_abstracts + ct_studies

        # Await the concurrent StatPearls/Bookshelf fetch (it overlapped the gather above).
        try:
            result.book_monographs = await asyncio.wait_for(_book_task, timeout=14.0) or []
        except Exception:
            _book_task.cancel()
            result.book_monographs = []
        if result.book_monographs and "StatPearls" not in result.data_sources:
            result.data_sources.append("StatPearls")

    result.fetch_success = bool(
        result.guideline_abstracts
        or result.systematic_review_abstracts
        or result.medlineplus_summary
        or result.book_monographs
    )

    # Fallback: if no PubMed results, retry without [pt] filter
    if not result.guideline_abstracts and not result.systematic_review_abstracts:
        fallback_term = f"{disease_name}[Title/Abstract] AND (guideline OR consensus OR recommendation) AND 2005:{_dt.now().year}[dp]"
        async with _make_client() as client:
            fallback_ids = await _pubmed_esearch_throttled(client, fallback_term, 8)
            if fallback_ids:
                fallback_abstracts, fallback_sources = await _pubmed_efetch(client, fallback_ids)
                for src in fallback_sources:
                    if src not in result.data_sources:
                        result.data_sources.append(src)
                result.guideline_abstracts = _cap_abstracts(fallback_abstracts, 8000)
                result.fetch_success = bool(result.guideline_abstracts)

    return result


async def fetch_procedure_data(procedure_name: str, *, extra_pubmed_terms: list[str] | None = None, extra_journal_filter: str | None = None) -> ProcedureFetchResult:
    """Fetch procedure/guideline data from PubMed practice guidelines."""
    result = ProcedureFetchResult()

    async with _make_client() as client:
        # Build main search tasks
        _tasks = [
            _fetch_pubmed_abstracts(client, procedure_name, "guideline", extra_journal_filter),
            _fetch_pubmed_procedure_guidelines(client, procedure_name),
            _fetch_pubmed_abstracts(client, procedure_name, "systematic_review", extra_journal_filter),
            _fetch_pmc_statpearls(client, procedure_name),
        ]

        # LLM-expanded MeSH terms — additive on top of hardcoded searches above
        if extra_pubmed_terms:
            import datetime as _dt
            _cur_year = _dt.datetime.now().year
            _date_suffix = f" AND 2010:{_cur_year}[dp]"
            for _t in extra_pubmed_terms[:3]:  # cap at 3 extra terms per call
                if _t and len(_t) > 10 and "[dp]" not in _t:  # skip empty/malformed/already-dated
                    _tasks.append(
                        _pubmed_esearch_throttled(client, _t + _date_suffix, 8)
                    )

        guidelines_result, practice_result, reviews_result, statpearls, *_extra_esearch_results = await asyncio.gather(
            *_tasks,
            return_exceptions=True,
        )

        # Unpack results from _fetch_pubmed_abstracts and _fetch_pubmed_procedure_guidelines (now tuples)
        guidelines = []
        guidelines_sources = set()
        if isinstance(guidelines_result, tuple):
            guidelines, guidelines_sources = guidelines_result
        elif isinstance(guidelines_result, list):
            guidelines = guidelines_result

        practice = []
        practice_sources = set()
        if isinstance(practice_result, tuple):
            practice, practice_sources = practice_result
        elif isinstance(practice_result, list):
            practice = practice_result

        reviews = []
        reviews_sources = set()
        if isinstance(reviews_result, tuple):
            reviews, reviews_sources = reviews_result
        elif isinstance(reviews_result, list):
            reviews = reviews_result

        # Fetch expanded PubMed search results from LLM-generated terms
        extra_guidelines = []
        if _extra_esearch_results:
            _extra_pmids = []
            for _res in _extra_esearch_results:
                if isinstance(_res, list):
                    _extra_pmids.extend(_res[:8])  # take first 8 from each extra search
            if _extra_pmids:
                _extra_articles, _extra_sources = await _pubmed_efetch(client, _extra_pmids)
                extra_guidelines = _extra_articles
                for src in _extra_sources:
                    guidelines_sources.add(src)

        # Collect all sources
        all_sources = guidelines_sources | practice_sources | reviews_sources
        for src in all_sources:
            if src not in result.data_sources:
                result.data_sources.append(src)

        result.guideline_abstracts = _cap_abstracts(
            (guidelines if isinstance(guidelines, list) else []) +
            (practice if isinstance(practice, list) else []) +
            (reviews if isinstance(reviews, list) else []) +
            extra_guidelines,
            6000
        )
        if result.guideline_abstracts and "PubMed" not in result.data_sources:
            result.data_sources.append("PubMed")

        result.practice_guideline_abstracts = []  # merged above
        if isinstance(statpearls, str) and statpearls:
            # Add StatPearls as a synthetic abstract entry
            result.guideline_abstracts.insert(0, {
                "pmid": "",
                "title": f"StatPearls: {procedure_name}",
                "abstract": statpearls[:600],
                "year": 2024,
                "journal": "StatPearls",
            })
            if "PMC (full text)" not in result.data_sources:
                result.data_sources.append("PMC (full text)")

    result.fetch_success = bool(
        result.guideline_abstracts or result.practice_guideline_abstracts
    )
    return result


async def _fetch_pubmed_procedure_guidelines(
    client: httpx.AsyncClient, entity: str
) -> tuple[list, set[str]]:
    """Fetch PubMed practice guidelines specifically for procedures.

    Returns: (articles, sources_used)
    """
    from datetime import datetime

    current_year = datetime.now().year
    term = (
        f"{entity}[Title/Abstract] AND "
        "(Practice Guideline[pt] OR Consensus Development Conference[pt]) "
        f"AND 2015:{current_year}[dp]"
    )
    ids = await _pubmed_esearch_throttled(client, term, 5)
    articles, sources = await _pubmed_efetch(client, ids)
    return articles, sources


async def _cascade_pubmed_for_complex(
    drug: str,
    primary_disease: str,
    comorbidities: list[str],
    extra_pubmed_terms: list[str] | None = None,
    extra_journal_filter: str | None = None,
) -> tuple[EvidenceFetchResult, str, list[str]]:
    """Cascade PubMed queries from most-specific to drug-class-level.

    Returns (EvidenceFetchResult, evidence_tier, cascade_log).
    Guarantee: if PubMed has ANY content on the drug or its class, this returns at least one abstract.
    The cascade order (all tiers now require ≥1 hit, lowered from ≥3 for better recall):
        1. drug + primary_disease + ALL comorbidities                → tier "guideline" if ≥1 hit
        2. drug + primary_disease + first comorbidity                → tier "rct"        if ≥1 hit
        3. drug + primary_disease                                    → tier "review"     if ≥1 hit
        4. drug + first comorbidity                                  → tier "case_report" if ≥1 hit
        5. drug alone                                                → tier "case_report" if ≥1 hit
        6. drug RxNorm class (already provided by fetch_drug_data)   → tier "drug_class"
    Tier names are CONSUMED by the prompt builder to set `confidence` and to surface
    a "Evidence is limited — based on …" prefix in the rendered section text.
    """
    cascade_log: list[str] = []
    # Expand medical terms in comorbidities for better PubMed recall
    expanded_comorbidities = [_expand_medical_term(c) for c in (comorbidities or [])]

    layers: list[tuple[str, str]] = [
        ("guideline", " ".join([drug, primary_disease, *expanded_comorbidities]).strip()),
        ("rct", " ".join([drug, primary_disease, expanded_comorbidities[0] if expanded_comorbidities else ""]).strip()),
        ("review", f"{drug} {primary_disease}".strip()),
        ("case_report", f"{drug} {expanded_comorbidities[0]}" if expanded_comorbidities else drug),
        ("case_report", drug),
    ]

    last_result: EvidenceFetchResult | None = None
    for tier, q in layers:
        if not q:
            continue
        try:
            ev = await fetch_evidence_data(
                q,
                extra_pubmed_terms=extra_pubmed_terms,
                extra_journal_filter=extra_journal_filter,
            )
        except Exception as e:
            cascade_log.append(f"{tier}:{q!r} → exception {e!s}")
            continue
        last_result = ev
        hits = (
            len(ev.clinical_trial_abstracts)
            + len(ev.systematic_review_abstracts)
            + len(ev.guideline_abstracts)
        )
        cascade_log.append(f"{tier}:{q!r} → {hits} abstracts")
        # Acceptance threshold: 1 for all tiers (lowered from 3 for high tiers to improve recall on complex queries)
        if hits >= 1:
            return ev, tier, cascade_log

    # Final fall-through: return whatever the last attempt returned (may be empty).
    # We deliberately do NOT raise — the caller will use drug_class abstracts as the safety net.
    return (last_result or EvidenceFetchResult()), "drug_class", cascade_log


async def _fetch_comorbidities(
    comorbidities: list[str],
    extra_pubmed_terms: list[str] | None = None,
    extra_journal_filter: str | None = None,
) -> list[DiseaseFetchResult]:
    """Fetch one DiseaseFetchResult per comorbidity in parallel. Caps at 4 comorbidities to bound fan-out."""
    if not comorbidities:
        return []
    capped = comorbidities[:4]
    results = await asyncio.gather(
        *[
            fetch_disease_data(c, extra_pubmed_terms=extra_pubmed_terms, extra_journal_filter=extra_journal_filter)
            for c in capped
        ],
        return_exceptions=True,
    )
    return [r for r in results if isinstance(r, DiseaseFetchResult)]


async def fetch_evidence_data(query: str, *, extra_pubmed_terms: list[str] | None = None, extra_journal_filter: str | None = None) -> EvidenceFetchResult:
    """Fetch evidence for drug+condition questions (clinical trials + reviews).

    Speed optimization: parallel esearch → single batch efetch.
    """
    result = EvidenceFetchResult()
    import datetime as _dt
    current_year = _dt.datetime.now().year

    trial_term = (
        f"{query}[Title/Abstract] AND "
        "(Clinical Trial[pt] OR Randomized Controlled Trial[pt]) "
        f"AND {_EVIDENCE_SEARCH_START_YEAR}:{current_year}[dp]"
    )
    review_term = f"{query}[Title/Abstract] AND (Systematic Review[pt] OR Meta-Analysis[pt]) AND {_EVIDENCE_SEARCH_START_YEAR}:{current_year}[dp]"
    guideline_term = f"{query}[Title/Abstract] AND (Practice Guideline[pt] OR Guideline[pt]) AND {_EVIDENCE_SEARCH_START_YEAR}:{current_year}[dp]"
    broad_evidence_term = (
        f"{query}[Title/Abstract] AND (guideline OR review OR trial OR consensus OR recommendation) AND {_EVIDENCE_SEARCH_START_YEAR}:{current_year}[dp]"
    )

    async with _make_client() as client:
        # Phase 1: all esearch in parallel (guideline searches forced to pub_date sort for recency)
        _tasks = [
            _pubmed_esearch_throttled(client, trial_term, 12),
            _pubmed_esearch_throttled(client, review_term, 8),
            _pubmed_esearch_throttled(client, guideline_term, 10, sort="pub_date"),
            _pubmed_esearch_throttled(client, broad_evidence_term, 12, sort="pub_date"),
            _fetch_clinicaltrials(client, query),
        ]

        # LLM-expanded MeSH terms — additive on top of hardcoded searches above
        if extra_pubmed_terms:
            _date_suffix = f" AND {_EVIDENCE_SEARCH_START_YEAR}:{current_year}[dp]"
            for _t in extra_pubmed_terms[:3]:  # cap at 3 extra terms per call
                if _t and len(_t) > 10 and "[dp]" not in _t:  # skip empty/malformed/already-dated
                    _tasks.append(
                        _pubmed_esearch_throttled(client, _t + _date_suffix, 8)
                    )

        results = await asyncio.gather(*_tasks, return_exceptions=True)
        trial_ids, review_ids, guideline_ids, broad_ids = results[0], results[1], results[2], results[3]
        trial_ids = trial_ids if isinstance(trial_ids, list) else []
        review_ids = review_ids if isinstance(review_ids, list) else []
        guideline_ids = guideline_ids if isinstance(guideline_ids, list) else []
        broad_ids = broad_ids if isinstance(broad_ids, list) else []
        ct_studies_ev = results[4] if len(results) > 4 and isinstance(results[4], list) else []

        # Remaining LLM-expanded terms start at index 5 now (was 4)
        extra_ids = []
        if len(results) > 5:
            for _res in results[5:]:
                if isinstance(_res, list):
                    extra_ids.extend(_res[:8])  # take first 8 from each extra search

        # Phase 2: single batch efetch
        all_ids = list(set(trial_ids + review_ids + guideline_ids + broad_ids + extra_ids))
        if all_ids:
            all_abstracts, enrichment_sources = await _pubmed_efetch(client, all_ids)
            for src in enrichment_sources:
                if src not in result.data_sources:
                    result.data_sources.append(src)
        else:
            all_abstracts = []

        trial_set = set(trial_ids)
        review_set = set(review_ids)
        guideline_set = set(guideline_ids)
        broad_set = set(broad_ids)
        extra_set = set(extra_ids)
        for a in all_abstracts:
            pmid = a.get("pmid")
            if pmid in trial_set:
                result.clinical_trial_abstracts.append(a)
            if pmid in review_set:
                result.systematic_review_abstracts.append(a)
            if pmid in guideline_set:
                result.guideline_abstracts.append(a)
            elif pmid in broad_set:
                result.guideline_abstracts.append(a)
            elif pmid in extra_set:
                result.guideline_abstracts.append(a)

        result.clinical_trial_abstracts = _cap_abstracts(
            result.clinical_trial_abstracts, 6000
        )
        result.systematic_review_abstracts = _cap_abstracts(
            result.systematic_review_abstracts, 5000
        )
        result.guideline_abstracts = _cap_abstracts(result.guideline_abstracts, 4000)

        if ct_studies_ev:
            result.clinical_trial_abstracts = result.clinical_trial_abstracts + ct_studies_ev
            result.clinical_trial_abstracts = _cap_abstracts(result.clinical_trial_abstracts, 6000)
            if "ClinicalTrials.gov" not in result.data_sources:
                result.data_sources.append("ClinicalTrials.gov")

        if result.clinical_trial_abstracts or result.systematic_review_abstracts or result.guideline_abstracts:
            if "PubMed" not in result.data_sources:
                result.data_sources.append("PubMed")

    result.fetch_success = bool(
        result.clinical_trial_abstracts
        or result.systematic_review_abstracts
        or result.guideline_abstracts
    )
    return result


async def _fetch_pubmed_clinical_trials(client: httpx.AsyncClient, query: str) -> tuple[list, set[str]]:
    """Fetch clinical trial and RCT abstracts from PubMed.

    Returns: (articles, sources_used)
    """
    from datetime import datetime

    current_year = datetime.now().year
    term = (
        f"{query}[Title/Abstract] AND "
        "(Clinical Trial[pt] OR Randomized Controlled Trial[pt]) "
        f"AND 2010:{current_year}[dp]"
    )
    ids = await _pubmed_esearch_throttled(client, term, 8)
    articles, sources = await _pubmed_efetch(client, ids)
    return articles, sources


def _fire_and_forget_index(abstracts: list) -> None:
    """No-op. The PDF/abstract vector-indexing pipeline (ingestion.py) was removed —
    it was a disabled, half-migrated feature (local MiniLM -> Supabase/BYOK). Kept as a
    no-op so the existing call sites stay valid; re-wire when the vector pipeline is rebuilt."""
    return


_IMAGE_ELIGIBLE_TYPES = {"disease", "procedure", "evidence", "comparative"}


async def _fetch_wikimedia_image(entity: str, query_type: str) -> list[dict]:
    """Fetch open-source medical illustrations from Wikipedia/Wikimedia Commons.

    Uses the Wikipedia REST API thumbnail for the entity page. Falls back silently
    on any network or parsing error. Returns max 2 images with CC license metadata.
    Only runs for disease, procedure, evidence, and comparative query types.
    """
    if query_type not in _IMAGE_ELIGIBLE_TYPES:
        return []

    images: list[dict] = []
    slug = entity.replace(" ", "_")
    try:
        async with _make_client() as client:
            data = await _safe_get(
                client,
                f"https://en.wikipedia.org/api/rest_v1/page/summary/{slug}",
                params={},
            )
            if data:
                thumb = data.get("thumbnail") or data.get("originalimage")
                if thumb and thumb.get("source"):
                    images.append(
                        {
                            "url": thumb["source"],
                            "caption": data.get("description") or entity,
                            "license": "CC BY-SA",
                            "source": "Wikipedia",
                        }
                    )
    except Exception:
        pass

    return images[:2]


async def fetch_data_for_query(
    query_type: str,
    entities: list,
    condition_context: Optional[str] = None,
    user_email: Optional[str] = None,
    ncbi_api_key: Optional[str] = None,
    pubmed_expansion_terms: dict | None = None,
) -> FetchedData:
    """Top-level orchestrator called by the pipeline.

    Args:
        query_type: Classified query type (drug, disease, comparative, procedure, evidence).
        entities: Extracted entity names (drug names, disease names, etc.).
        condition_context: For drug-in-condition queries, the condition name to fetch
            management guidelines for in parallel (e.g., "atrial fibrillation" for "digoxin in AF").
        user_email: Authenticated user's email — used for Unpaywall open-access PDF lookup (free).
        ncbi_api_key: User's free NCBI API key for PubMed rate limit increase (3→10 req/s).
    """
    _user_email_ctx.set(user_email)  # available to all _pubmed_efetch calls in this request
    _ncbi_key_ctx.set(ncbi_api_key)  # available to all _pubmed_esearch_throttled calls in this request

    # Extract per-category expansion terms (None if no LLM expansion was done)
    _guideline_terms: list[str] | None = None
    _review_terms: list[str] | None = None
    _trial_terms: list[str] | None = None
    _all_terms: list[str] | None = None
    _llm_journal_filter: str | None = None
    if pubmed_expansion_terms:
        _guideline_terms = pubmed_expansion_terms.get("guideline") or []
        _review_terms = pubmed_expansion_terms.get("review") or []
        _trial_terms = pubmed_expansion_terms.get("trial") or []
        _all_terms = _guideline_terms + _review_terms + _trial_terms
        _llm_journal_filter = pubmed_expansion_terms.get("journal_filter") or None

    start = time.time()
    fetched = FetchedData(query_type=query_type)

    try:
        if query_type == "drug" and entities:
            if condition_context:
                # Fetch drug data AND condition management guidelines in parallel (B6)
                drug_result, condition_result, evidence_result = await asyncio.gather(
                    fetch_drug_data(entities[0], extra_pubmed_terms=_guideline_terms if pubmed_expansion_terms else None, extra_journal_filter=_llm_journal_filter),
                    fetch_disease_data(condition_context, extra_pubmed_terms=(_guideline_terms + _review_terms) if pubmed_expansion_terms else None, extra_journal_filter=_llm_journal_filter),
                    fetch_evidence_data(f"{entities[0]} {condition_context}", extra_pubmed_terms=_all_terms if pubmed_expansion_terms else None, extra_journal_filter=_llm_journal_filter),
                    return_exceptions=True,
                )
                fetched.drug_data = (
                    drug_result
                    if not isinstance(drug_result, Exception)
                    else DrugFetchResult()
                )
                fetched.condition_data = (
                    condition_result
                    if not isinstance(condition_result, Exception)
                    else None
                )
                if (
                    fetched.drug_data
                    and isinstance(evidence_result, EvidenceFetchResult)
                    and evidence_result.fetch_success
                ):
                    fetched.drug_data.clinical_trial_abstracts = _cap_abstracts(
                        evidence_result.clinical_trial_abstracts, 5000
                    )
                    fetched.drug_data.systematic_review_abstracts = _cap_abstracts(
                        (fetched.drug_data.systematic_review_abstracts or [])
                        + evidence_result.systematic_review_abstracts,
                        5000,
                    )
                    fetched.drug_data.guideline_abstracts = _cap_abstracts(
                        (fetched.drug_data.guideline_abstracts or [])
                        + evidence_result.guideline_abstracts,
                        5000,
                    )
                fetched.fallback_to_llm = not fetched.drug_data.fetch_success
                _fire_and_forget_index(fetched.drug_data.guideline_abstracts)
                _fire_and_forget_index(fetched.drug_data.clinical_trial_abstracts)
                if fetched.condition_data:
                    _fire_and_forget_index(fetched.condition_data.guideline_abstracts)
            else:
                fetched.drug_data = await fetch_drug_data(entities[0], extra_pubmed_terms=_guideline_terms if pubmed_expansion_terms else None, extra_journal_filter=_llm_journal_filter)
                fetched.fallback_to_llm = not fetched.drug_data.fetch_success
                _fire_and_forget_index(fetched.drug_data.guideline_abstracts)
                _fire_and_forget_index(fetched.drug_data.clinical_trial_abstracts)

        elif query_type == "disease" and entities:
            fetched.disease_data = await fetch_disease_data(entities[0], extra_pubmed_terms=(_guideline_terms + _review_terms) if pubmed_expansion_terms else None, extra_journal_filter=_llm_journal_filter)
            fetched.fallback_to_llm = not fetched.disease_data.fetch_success
            _fire_and_forget_index(fetched.disease_data.guideline_abstracts)
            _fire_and_forget_index(fetched.disease_data.systematic_review_abstracts)

        elif query_type == "comparative" and len(entities) >= 2:
            drug_results = await asyncio.gather(
                fetch_drug_data(entities[0], extra_pubmed_terms=_guideline_terms if pubmed_expansion_terms else None, extra_journal_filter=_llm_journal_filter),
                fetch_drug_data(entities[1], extra_pubmed_terms=_guideline_terms if pubmed_expansion_terms else None, extra_journal_filter=_llm_journal_filter),
                fetch_evidence_data(" vs ".join(entities[:2]), extra_pubmed_terms=_all_terms if pubmed_expansion_terms else None, extra_journal_filter=_llm_journal_filter),
                return_exceptions=True,
            )
            for r in drug_results[:2]:
                if not isinstance(r, Exception):
                    fetched.comparative_drug_data.append(r)
            comp_evidence = drug_results[2]
            if isinstance(comp_evidence, EvidenceFetchResult) and comp_evidence.fetch_success:
                fetched.comparative_evidence = comp_evidence
                _fire_and_forget_index(comp_evidence.clinical_trial_abstracts)
                _fire_and_forget_index(comp_evidence.systematic_review_abstracts)

            # Fetch drug-drug interaction data when both drugs have rxcui
            rxcui1 = fetched.comparative_drug_data[0].rxcui if len(fetched.comparative_drug_data) > 0 else None
            rxcui2 = fetched.comparative_drug_data[1].rxcui if len(fetched.comparative_drug_data) > 1 else None
            if rxcui1 and rxcui2:
                async with _make_client() as client:
                    ddi = await _fetch_rxnorm_interactions(client, rxcui1, rxcui2)
                    if ddi:
                        fetched.drug_interactions = ddi

            fetched.fallback_to_llm = not any(
                r.fetch_success for r in fetched.comparative_drug_data
            ) and not (
                fetched.comparative_evidence and fetched.comparative_evidence.fetch_success
            )

        elif query_type == "procedure" and entities:
            fetched.procedure_data = await fetch_procedure_data(entities[0], extra_pubmed_terms=_guideline_terms if pubmed_expansion_terms else None, extra_journal_filter=_llm_journal_filter)
            fetched.fallback_to_llm = not fetched.procedure_data.fetch_success
            _fire_and_forget_index(fetched.procedure_data.guideline_abstracts)

        elif query_type == "evidence" and entities:
            fetched.evidence_data = await fetch_evidence_data(" ".join(entities), extra_pubmed_terms=_all_terms if pubmed_expansion_terms else None, extra_journal_filter=_llm_journal_filter)
            fetched.fallback_to_llm = not fetched.evidence_data.fetch_success
            _fire_and_forget_index(fetched.evidence_data.clinical_trial_abstracts)

        elif query_type in ("complex", "general") and entities:
            # entities[0] = drug/intervention; entities[1] = primary disease (when extractor found it).
            drug_name = entities[0]
            primary_disease = entities[1] if len(entities) >= 2 else (condition_context or "")
            comorbidities: list[str] = []
            # comorbidity_list is propagated through pubmed_expansion_terms by rag_pipeline.
            if pubmed_expansion_terms and isinstance(pubmed_expansion_terms.get("comorbidity_list"), list):
                comorbidities = [c for c in pubmed_expansion_terms["comorbidity_list"] if isinstance(c, str) and c.strip()]

            drug_task = fetch_drug_data(
                drug_name,
                extra_pubmed_terms=_guideline_terms if pubmed_expansion_terms else None,
                extra_journal_filter=_llm_journal_filter,
            )
            disease_task = (
                fetch_disease_data(
                    primary_disease,
                    extra_pubmed_terms=(_guideline_terms + _review_terms) if pubmed_expansion_terms else None,
                    extra_journal_filter=_llm_journal_filter,
                )
                if primary_disease
                else asyncio.sleep(0, result=None)
            )
            comorbidity_task = _fetch_comorbidities(
                comorbidities,
                extra_pubmed_terms=(_guideline_terms + _review_terms) if pubmed_expansion_terms else None,
                extra_journal_filter=_llm_journal_filter,
            )
            cascade_task = _cascade_pubmed_for_complex(
                drug=drug_name,
                primary_disease=primary_disease,
                comorbidities=comorbidities,
                extra_pubmed_terms=_all_terms if pubmed_expansion_terms else None,
                extra_journal_filter=_llm_journal_filter,
            )

            drug_res, disease_res, comorbidity_res, cascade_res = await asyncio.gather(
                drug_task, disease_task, comorbidity_task, cascade_task, return_exceptions=True,
            )

            fetched.drug_data = drug_res if isinstance(drug_res, DrugFetchResult) else DrugFetchResult()
            if isinstance(disease_res, DiseaseFetchResult):
                fetched.condition_data = disease_res
            if isinstance(comorbidity_res, list):
                fetched.comorbidity_data = comorbidity_res

            if isinstance(cascade_res, tuple):
                ev, tier, cascade_log = cascade_res
                fetched.evidence_data = ev
                fetched.evidence_tier = tier
                fetched.cascade_log = cascade_log
                # Merge cascade abstracts back into drug_data so existing prompt builders see them.
                if fetched.drug_data and ev:
                    fetched.drug_data.clinical_trial_abstracts = _cap_abstracts(
                        (fetched.drug_data.clinical_trial_abstracts or []) + ev.clinical_trial_abstracts,
                        5000,
                    )
                    fetched.drug_data.systematic_review_abstracts = _cap_abstracts(
                        (fetched.drug_data.systematic_review_abstracts or []) + ev.systematic_review_abstracts,
                        5000,
                    )
                    fetched.drug_data.guideline_abstracts = _cap_abstracts(
                        (fetched.drug_data.guideline_abstracts or []) + ev.guideline_abstracts,
                        5000,
                    )

            # Final safety net: if every fetch came back empty, mark fallback_to_llm so
            # the legacy generate path takes over (it cites RxNorm class info instead of hallucinating).
            any_evidence = (
                (fetched.drug_data and fetched.drug_data.fetch_success)
                or (fetched.condition_data and fetched.condition_data.fetch_success)
                or any(c.fetch_success for c in fetched.comorbidity_data)
                or (fetched.evidence_data and fetched.evidence_data.fetch_success)
            )
            fetched.fallback_to_llm = not any_evidence

            _fire_and_forget_index((fetched.drug_data.guideline_abstracts if fetched.drug_data else []))
            if fetched.condition_data:
                _fire_and_forget_index(fetched.condition_data.guideline_abstracts)
            for c in fetched.comorbidity_data:
                _fire_and_forget_index(c.guideline_abstracts)

        else:
            fetched.fallback_to_llm = True

        # Fetch open-source medical illustration in parallel for eligible query types
        if entities and query_type in _IMAGE_ELIGIBLE_TYPES:
            try:
                fetched.images = await _fetch_wikimedia_image(entities[0], query_type)
            except Exception:
                pass

    except Exception:
        logger.error("Data fetch orchestration failed", exc_info=True)
        fetched.fallback_to_llm = True

    # Roll up data_sources from sub-results into top-level FetchedData
    _all_srcs: list[str] = []
    for _sub in (
        fetched.drug_data,
        fetched.disease_data,
        fetched.condition_data,
        fetched.procedure_data,
        fetched.evidence_data,
        fetched.comparative_evidence,
    ):
        if _sub and hasattr(_sub, "data_sources") and _sub.data_sources:
            for s in _sub.data_sources:
                if s not in _all_srcs:
                    _all_srcs.append(s)
    for _cdr in fetched.comparative_drug_data:
        if hasattr(_cdr, "data_sources") and _cdr.data_sources:
            for s in _cdr.data_sources:
                if s not in _all_srcs:
                    _all_srcs.append(s)
    fetched.data_sources = _all_srcs

    fetched.total_fetch_time_ms = int((time.time() - start) * 1000)
    return fetched
