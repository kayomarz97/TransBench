"""Article Registry — single source of truth for fetched articles.

Built once per query, immediately after fetch. Wraps the prompt-engine
build_ref_map() (which determines the cached prompt-prefix bytes) and adds:
  - O(1) lookup indexes (by_pmid, by_nct, by_doi, by_norm_title, by_token)
  - Complete reference enumeration including sources that build_ref_map skips
    (semantic_papers, comorbidity_data, comparative_drug_data, comparative_evidence,
     NCBI Books items, MedlinePlus, ClinicalTrials.gov standalone trials)
  - Hard URL guarantees: every registry entry has a validated article-level URL.
    Entries that cannot be URL-resolved are excluded from the registry.

Cache safety: this module never alters the LLM-facing prompt bytes. It only
provides post-processing data structures used by rag_pipeline after generation.
"""
from __future__ import annotations

import math
import re
from dataclasses import dataclass, field
from typing import Any, Optional

from vendored.app.services.url_builder import is_safe_url


# ── URL constructors (deterministic, validated) ───────────────────────────────

_NCT_RE = re.compile(r"^NCT\d{8}$")
_NBK_RE = re.compile(r"^NBK\d+$")


def pubmed_url(pmid: Any) -> Optional[str]:
    s = str(pmid or "").strip()
    if s.isdigit():
        url = f"https://pubmed.ncbi.nlm.nih.gov/{s}/"
        return url if is_safe_url(url) else None
    return None


def clinicaltrials_url(nct_id: Any) -> Optional[str]:
    s = str(nct_id or "").strip().upper()
    if _NCT_RE.match(s):
        url = f"https://clinicaltrials.gov/study/{s}"
        return url if is_safe_url(url) else None
    return None


def doi_url(doi: Any) -> Optional[str]:
    s = str(doi or "").strip()
    if s and s.startswith("10."):
        from urllib.parse import quote
        safe = quote(s, safe="ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789-._~/")
        url = f"https://doi.org/{safe}"
        return url if is_safe_url(url) else None
    return None


def ncbi_books_url(nbk_id: Any) -> Optional[str]:
    s = str(nbk_id or "").strip().upper()
    if _NBK_RE.match(s):
        url = f"https://www.ncbi.nlm.nih.gov/books/{s}/"
        return url if is_safe_url(url) else None
    return None


def semantic_scholar_url(paper_id: Any) -> Optional[str]:
    s = str(paper_id or "").strip()
    if s and re.match(r"^[A-Za-z0-9]{6,}$", s):
        url = f"https://www.semanticscholar.org/paper/{s}"
        return url if is_safe_url(url) else None
    return None


def medlineplus_topic_url(raw_url: Any) -> Optional[str]:
    s = str(raw_url or "").strip()
    if s and s.startswith("https://medlineplus.gov/") and is_safe_url(s):
        return s
    return None


def _norm_title(t: Any) -> str:
    s = str(t or "").lower()
    s = re.sub(r"\W+", " ", s).strip()
    return s


def _best_url_for_item(item: dict, source_type: str) -> Optional[str]:
    """Return article-level URL or None. No homepages."""
    for key, builder in (
        ("pmid", pubmed_url),
        ("nct_id", clinicaltrials_url),
        ("doi", doi_url),
        ("nbk_id", ncbi_books_url),
        ("paper_id", semantic_scholar_url),
    ):
        if item.get(key):
            url = builder(item[key])
            if url:
                return url
    raw = item.get("url") or item.get("label_url")
    if raw and is_safe_url(str(raw)):
        return str(raw)
    if source_type == "medlineplus":
        return medlineplus_topic_url(item.get("url"))
    return None


# ── Registry data classes ─────────────────────────────────────────────────────

@dataclass
class RegistryArticle:
    ref_token: str
    title: str
    source: str
    source_type: str  # "pubmed" | "clinical_trial" | "nice" | "fda_label" | "dailymed"
                      # | "ncbi_books" | "medlineplus" | "semantic_scholar" | "guideline"
    pmid: Optional[str]
    nct_id: Optional[str]
    doi: Optional[str]
    url: str  # always non-empty (registry rejects items without URL)
    year: Optional[Any]
    origin_section: str
    used_inline: bool = False


@dataclass
class ArticleRegistry:
    items: list[RegistryArticle] = field(default_factory=list)
    by_token: dict[str, RegistryArticle] = field(default_factory=dict)
    by_pmid: dict[str, RegistryArticle] = field(default_factory=dict)
    by_nct: dict[str, RegistryArticle] = field(default_factory=dict)
    by_doi: dict[str, RegistryArticle] = field(default_factory=dict)
    by_norm_title: dict[str, RegistryArticle] = field(default_factory=dict)

    def lookup_token(self, token: str) -> Optional[RegistryArticle]:
        return self.by_token.get(token.upper())

    def lookup_id(self, pmid: Any = None, nct_id: Any = None, doi: Any = None,
                  title: Any = None) -> Optional[RegistryArticle]:
        if pmid:
            r = self.by_pmid.get(str(pmid).strip())
            if r:
                return r
        if nct_id:
            r = self.by_nct.get(str(nct_id).strip().upper())
            if r:
                return r
        if doi:
            r = self.by_doi.get(str(doi).strip().lower())
            if r:
                return r
        if title:
            r = self.by_norm_title.get(_norm_title(title))
            if r:
                return r
        return None

    def best_match(self, claim_text: str, source_hint: str = "") -> Optional[RegistryArticle]:
        """Tiered match for backfill.
        Tier 1: ID substring in claim_text → that article.
        Tier 2: title token Jaccard >= 0.30 → highest-scoring article.
        Tier 3: source_hint authority match → first article with same source_type.
        """
        if not self.items:
            return None
        text = (claim_text or "")
        text_l = text.lower()
        # Tier 1: explicit IDs in claim text
        for r in self.items:
            if r.pmid and r.pmid in text:
                return r
            if r.nct_id and r.nct_id in text.upper():
                return r
            if r.doi and r.doi.lower() in text_l:
                return r
        # Tier 2: token Jaccard
        claim_tokens = set(re.findall(r"\b\w+\b", text_l))
        if claim_tokens:
            best: Optional[RegistryArticle] = None
            best_score = 0.0
            for r in self.items:
                title_tokens = set(re.findall(r"\b\w+\b", r.title.lower()))
                if not title_tokens:
                    continue
                inter = len(claim_tokens & title_tokens)
                union = len(claim_tokens | title_tokens)
                score = inter / union if union else 0.0
                if score > best_score:
                    best_score = score
                    best = r
            if best is not None and best_score >= 0.30:
                return best
        # Tier 3: authority hint
        hint = (source_hint or "").lower()
        AUTH = {
            "pubmed": "pubmed", "nice": "nice", "fda": "fda_label",
            "dailymed": "dailymed", "clinicaltrials": "clinical_trial",
            "medlineplus": "medlineplus", "ncbi books": "ncbi_books",
            "semantic": "semantic_scholar",
        }
        for needle, st in AUTH.items():
            if needle in hint:
                for r in self.items:
                    if r.source_type == st:
                        return r
        return None

    def mark_used(self, article: RegistryArticle) -> None:
        article.used_inline = True

    def to_reference_list(self) -> list[dict]:
        """Return all registry entries as plain dicts.
        Cited entries first (used_inline=True), then retrieved-but-unused."""
        SOURCE_TYPE_PRIORITY = {
            "guideline": 0, "nice": 1, "fda_label": 2, "dailymed": 3,
            "clinical_trial": 4, "pubmed": 5, "ncbi_books": 6,
            "medlineplus": 7, "semantic_scholar": 8,
        }
        def k(r: RegistryArticle) -> tuple:
            return (
                0 if r.used_inline else 1,
                SOURCE_TYPE_PRIORITY.get(r.source_type, 99),
                r.ref_token,
            )
        out: list[dict] = []
        for r in sorted(self.items, key=k):
            out.append({
                "title": r.title,
                "source": r.source,
                "source_type": r.source_type,
                "pmid": r.pmid,
                "nct_id": r.nct_id,
                "doi": r.doi,
                "url": r.url,
                "year": r.year,
                "ref_token": r.ref_token,
                "used_inline": r.used_inline,
            })
        return out

    def attach_orphans_to_references(self, parsed: dict) -> None:
        """Ensure every fetched article appears in the final reference list.

        For each RegistryArticle in self.items, check if it's already in
        parsed["references"]. If not, add it (marked used_inline=False).
        Uses the same normalization as _norm_title to avoid duplicates.

        This rescues fetched articles that weren't cited inline.
        """
        if not parsed or "references" not in parsed:
            return

        existing_refs = parsed["references"] or []
        if not isinstance(existing_refs, list):
            return

        # Build a set of existing references by normalized title + ID for dedup
        existing_dedup = set()
        for ref in existing_refs:
            if not isinstance(ref, dict):
                continue
            pmid = ref.get("pmid")
            nct_id = ref.get("nct_id")
            doi = ref.get("doi")
            title = ref.get("title")
            norm_title = _norm_title(title) if title else ""
            dedup_key = (pmid, nct_id, doi, norm_title)
            existing_dedup.add(dedup_key)

        # Add registry items that aren't already in references
        for article in self.items:
            norm_title = _norm_title(article.title)
            dedup_key = (article.pmid, article.nct_id, article.doi, norm_title)
            if dedup_key not in existing_dedup:
                new_ref = {
                    "title": article.title,
                    "source": article.source,
                    "source_type": article.source_type,
                    "pmid": article.pmid,
                    "nct_id": article.nct_id,
                    "doi": article.doi,
                    "url": article.url,
                    "year": article.year,
                    "ref_token": article.ref_token,
                    "used_inline": False,
                }
                existing_refs.append(new_ref)
                existing_dedup.add(dedup_key)

        parsed["references"] = existing_refs

    def best_match_min_jaccard(
        self, claim_text: str, min_jaccard: float = 0.30
    ) -> Optional[RegistryArticle]:
        """Match claim_text against registry titles using Jaccard similarity.

        Returns the article with the highest Jaccard score, or None if < min_jaccard.
        """
        if not self.items or not claim_text:
            return None

        claim_tokens = set(re.findall(r"\b\w+\b", claim_text.lower()))
        if not claim_tokens:
            return None

        best: Optional[RegistryArticle] = None
        best_score = 0.0
        for r in self.items:
            title_tokens = set(re.findall(r"\b\w+\b", r.title.lower()))
            if not title_tokens:
                continue
            inter = len(claim_tokens & title_tokens)
            union = len(claim_tokens | title_tokens)
            score = inter / union if union else 0.0
            if score > best_score:
                best_score = score
                best = r

        return best if best_score >= min_jaccard else None


# ── Builder ───────────────────────────────────────────────────────────────────

# Source-type priority used for stable sort (matches build_ref_map ordering for
# items it covers; new source_types are appended at the bottom so cache prefix
# is preserved for the prompt-facing build_ref_map output).
_SOURCE_PRIORITY = {
    "pubmed": 0,
    "clinical_trial": 1,
    "nice": 2,
    "fda_label": 3,
    "dailymed": 4,
    "ncbi_books": 5,
    "medlineplus": 6,
    "semantic_scholar": 7,
    "guideline": 0,
}


def _add(seen: set, items: list, entry: dict, source_type: str, origin: str) -> None:
    title = (entry.get("title") or "").strip()
    if not title:
        return
    pmid = str(entry.get("pmid") or "").strip() or None
    nct_id = (str(entry.get("nct_id") or "").strip().upper() or None)
    doi = (str(entry.get("doi") or "").strip().lower() or None)
    norm = _norm_title(title)
    dedup = (pmid, nct_id, doi, norm)
    if dedup in seen:
        return
    url = _best_url_for_item(entry, source_type)
    if not url:
        return  # registry guarantee: every entry has an article-level URL
    seen.add(dedup)
    items.append({
        "title": title,
        "source": entry.get("source") or entry.get("journal") or entry.get("collective_name") or source_type,
        "source_type": source_type,
        "pmid": pmid,
        "nct_id": nct_id,
        "doi": doi,
        "url": url,
        "year": entry.get("year"),
        "origin_section": origin,
    })


def _walk_abstracts(obj: Any, origin: str, seen: set, items: list) -> None:
    for list_attr in (
        "guideline_abstracts",
        "systematic_review_abstracts",
        "clinical_trial_abstracts",
        "practice_guideline_abstracts",
    ):
        for a in getattr(obj, list_attr, None) or []:
            if not isinstance(a, dict):
                continue
            st = "clinical_trial" if (list_attr == "clinical_trial_abstracts" or a.get("nct_id")) else "pubmed"
            _add(seen, items, a, st, f"{origin}.{list_attr}")


def build_article_registry(fetched_data: Any) -> ArticleRegistry:
    """Build the registry. Walks every source category in fetched_data."""
    if fetched_data is None:
        return ArticleRegistry()

    seen: set = set()
    items: list[dict] = []

    # 1. Single-shot result objects
    for attr in ("drug_data", "disease_data", "condition_data",
                 "procedure_data", "evidence_data", "comparative_evidence"):
        obj = getattr(fetched_data, attr, None)
        if obj is None:
            continue
        _walk_abstracts(obj, attr, seen, items)
        # NICE recommendations
        for rec in getattr(obj, "nice_recommendations", None) or []:
            if isinstance(rec, dict):
                _add(seen, items, {**rec, "source": "NICE"}, "nice", f"{attr}.nice_recommendations")
        # Semantic Scholar
        for paper in getattr(obj, "semantic_papers", None) or []:
            if isinstance(paper, dict):
                _add(seen, items, paper, "semantic_scholar", f"{attr}.semantic_papers")
        # NCBI Books (field name: ncbi_books or books — check both)
        for book in (getattr(obj, "ncbi_books", None) or getattr(obj, "books", None) or []):
            if isinstance(book, dict):
                _add(seen, items, book, "ncbi_books", f"{attr}.ncbi_books")
        # MedlinePlus topic page (single optional dict)
        ml = getattr(obj, "medlineplus_topic", None)
        if isinstance(ml, dict):
            _add(seen, items, ml, "medlineplus", f"{attr}.medlineplus_topic")

    # 2. FDA / DailyMed label (one per drug)
    drug = getattr(fetched_data, "drug_data", None)
    if drug:
        label_url = getattr(drug, "label_url", None)
        if label_url and is_safe_url(label_url):
            drug_name = getattr(drug, "brand_name", None) or getattr(drug, "generic_name", None) or "Drug"
            st = "dailymed" if "dailymed.nlm.nih.gov" in label_url else "fda_label"
            entry = {"title": f"{drug_name} — Drug Label", "url": label_url,
                     "source": "DailyMed" if st == "dailymed" else "FDA"}
            _add(seen, items, entry, st, "drug_data.label_url")

    # 3. Comorbidity-cascade abstracts (list of DiseaseFetchResult)
    for i, com in enumerate(getattr(fetched_data, "comorbidity_data", None) or []):
        _walk_abstracts(com, f"comorbidity_data[{i}]", seen, items)
        for rec in getattr(com, "nice_recommendations", None) or []:
            if isinstance(rec, dict):
                _add(seen, items, {**rec, "source": "NICE"}, "nice", f"comorbidity_data[{i}].nice")

    # 4. Comparative drug per-entity abstracts
    for i, cdr in enumerate(getattr(fetched_data, "comparative_drug_data", None) or []):
        _walk_abstracts(cdr, f"comparative_drug_data[{i}]", seen, items)
        cdr_label = getattr(cdr, "label_url", None)
        if cdr_label and is_safe_url(cdr_label):
            name = getattr(cdr, "brand_name", None) or getattr(cdr, "generic_name", None) or f"Drug {i+1}"
            st = "dailymed" if "dailymed.nlm.nih.gov" in cdr_label else "fda_label"
            entry = {"title": f"{name} — Drug Label", "url": cdr_label,
                     "source": "DailyMed" if st == "dailymed" else "FDA"}
            _add(seen, items, entry, st, f"comparative_drug_data[{i}].label_url")

    # 5. Sort deterministically (matches build_ref_map for shared items)
    def sort_key(e: dict) -> tuple:
        st_priority = _SOURCE_PRIORITY.get(e["source_type"], 99)
        pmid = e.get("pmid")
        pmid_int = int(pmid) if pmid and pmid.isdigit() else math.inf
        nct = e.get("nct_id") or ""
        return (st_priority, pmid_int, nct, _norm_title(e["title"]))

    items.sort(key=sort_key)

    registry = ArticleRegistry()
    for i, e in enumerate(items, start=1):
        ra = RegistryArticle(
            ref_token=f"REF_{i}",
            title=e["title"],
            source=e["source"],
            source_type=e["source_type"],
            pmid=e["pmid"],
            nct_id=e["nct_id"],
            doi=e["doi"],
            url=e["url"],
            year=e["year"],
            origin_section=e["origin_section"],
        )
        registry.items.append(ra)
        registry.by_token[ra.ref_token] = ra
        if ra.pmid:
            registry.by_pmid[ra.pmid] = ra
        if ra.nct_id:
            registry.by_nct[ra.nct_id] = ra
        if ra.doi:
            registry.by_doi[ra.doi] = ra
        nt = _norm_title(ra.title)
        if nt:
            registry.by_norm_title[nt] = ra

    return registry
