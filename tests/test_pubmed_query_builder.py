"""Offline unit test for the PubMed query builder (KICKOFF.md Phase 3 /
BUILD_SPEC.md §3 — DEFECT 1 fix, Opus review).

Zero API cost, zero network — pure functions only (``_entity_pubmed_query``,
``_condition_anchor``). Deliberately NOT added to ``test_retrieval_phase3.py``
because that file's module-level ``pytestmark`` skips everything without a
working ``ANTHROPIC_API_KEY``, which would incorrectly skip an offline test
too — this file has no such marker and always runs.

Covers >=4 representative real hypothesis phrasings (the exact failure modes
measured live: a T-cell/perivascular immune hypothesis, a transporter-gene
pharmacogenomic hypothesis, an inflammasome hypothesis, and an
endothelial-dysfunction hypothesis) with EMPTY ``stance_entities`` — matching
the observed real failure mode where ``neutralize_query``'s internal
800ms-timeout heuristic fallback yields weak-to-no entities. Asserts each
built query CONTAINS the gene/pathway symbol AND the disease anchor, and
EXCLUDES every generic filler word identified during live debugging. This
must hold regardless of day-to-day LLM hypothesis-generation variation, since
none of it depends on a real LLM or PubMed call.
"""
from __future__ import annotations

from transbench.agents import _condition_anchor, _entity_pubmed_query

# Generic filler words observed live consuming a query slot ahead of the
# real signal (BUILD_SPEC.md §3 DEFECT 1, both rounds of the fix).
_GENERIC_FILLER_WORDS = [
    # Round 1 (Phase 3 initial fix)
    "chronic", "acute", "persistent", "sustained", "elevated", "increased",
    "decreased", "reduced", "severe", "mild", "moderate", "significant",
    "marked", "direct", "indirect", "primary", "secondary", "dependent",
    "independent", "mediated", "driven", "non", "via",
    # Round 2 (this fix)
    "clonally", "expanded", "infiltrating", "role", "effector", "memory",
    "loss", "function", "variant", "encoding", "activation", "accumulation",
    "hyperactivation", "downregulation",
]

# (name, hypothesis-statement-like text, gene/pathway symbol(s) that MUST
# appear in the built query)
_CASES: list[tuple[str, str, list[str]]] = [
    (
        "CD8_perivascular_T_cells",
        "Clonally expanded CD8+ effector-memory T cells infiltrating the "
        "perivascular adventitia drive sustained vascular inflammation "
        "contributing to resistant hypertension.",
        ["CD8"],
    ),
    (
        "SLC12A3_thiazide_NCC",
        "A loss-of-function variant in SLC12A3 (encoding the thiazide-"
        "sensitive NCC cotransporter) paradoxically coexists with a "
        "gain-of-function variant of WNK4 kinase.",
        ["SLC12A3"],
    ),
    (
        "NLRP3_inflammasome",
        "Chronic activation of the NLRP3 inflammasome in macrophages "
        "drives IL-1beta-mediated vascular inflammation contributing to "
        "resistant hypertension.",
        ["NLRP3"],
    ),
    (
        "ADMA_DDAH2_eNOS",
        "Asymmetric dimethylarginine (ADMA) accumulation secondary to "
        "DDAH2 downregulation inhibits endothelial nitric oxide synthase "
        "(eNOS).",
        ["ADMA"],
    ),
]


def test_entity_pubmed_query_contains_symbol_and_anchor_excludes_filler() -> None:
    """Each of the >=4 representative phrasings must build a query that
    CONTAINS its gene/pathway symbol AND the disease anchor, and EXCLUDES
    every generic filler word — with EMPTY stance_entities (the observed
    real failure mode), so the fix is proven independent of whether
    neutralize_query's own extraction happens to succeed that run."""
    anchor = "hypertension"
    for name, statement, required_symbols in _CASES:
        query = _entity_pubmed_query([], statement, anchor)
        query_words_lower = {w.lower() for w in query.split()}

        assert anchor in query_words_lower, (
            f"[{name}] disease anchor {anchor!r} missing from query {query!r} "
            f"(DEFECT 1: the anchor must be GUARANTEED a slot)"
        )
        for symbol in required_symbols:
            assert symbol.lower() in query_words_lower, (
                f"[{name}] expected gene/pathway symbol {symbol!r} in query {query!r}"
            )
        hit_filler = query_words_lower & {w.lower() for w in _GENERIC_FILLER_WORDS}
        assert not hit_filler, (
            f"[{name}] query {query!r} contains generic filler word(s) {hit_filler} "
            f"that should never consume a slot"
        )


def test_entity_pubmed_query_prefers_symbols_over_sentence_order_generic_words() -> None:
    """Regression test for the exact live failure: a hypothesis whose first
    few SENTENCE-ORDER words are generic filler ("Clonally expanded...")
    must still produce a query anchored on the real gene symbol + disease
    term, not on those early generic words."""
    statement = (
        "Clonally expanded CD8+ effector-memory T cells infiltrating the "
        "perivascular adventitia drive sustained vascular inflammation "
        "contributing to resistant hypertension."
    )
    query = _entity_pubmed_query([], statement, "hypertension")
    words_lower = query.lower().split()
    assert "clonally" not in words_lower
    assert "expanded" not in words_lower
    assert "infiltrating" not in words_lower
    assert "cd8" in words_lower
    assert "hypertension" in words_lower


def test_entity_pubmed_query_does_not_duplicate_symbol_fragments() -> None:
    """Regression test: a digit-bearing symbol (e.g. "SLC12A3") must not
    also spawn a separate, meaningless letters-only fragment of itself (e.g.
    "SLC") via the plain-content-word fallback pass."""
    query = _entity_pubmed_query(
        [], "A variant in SLC12A3 encoding the NCC cotransporter.", "hypertension"
    )
    words_lower = query.lower().split()
    assert "slc12a3" in words_lower
    assert "slc" not in words_lower  # bare prefix fragment of SLC12A3 -- must not appear

    query2 = _entity_pubmed_query([], "Activation of the NLRP3 inflammasome.", "hypertension")
    words2_lower = query2.lower().split()
    assert "nlrp3" in words2_lower
    assert "nlrp" not in words2_lower  # bare prefix fragment of NLRP3


def test_entity_pubmed_query_max_terms_respected() -> None:
    """The built query never exceeds max_terms, however many candidate
    symbols/entities/content-words are available."""
    long_statement = (
        "SLC12A3 WNK4 NCC SPAK NKCC2 ENaC CYP11B2 TET2 DNMT3A NLRP3 IL18 "
        "ADMA DDAH2 eNOS CD8 chymase aldosterone hypertension mechanism."
    )
    query = _entity_pubmed_query([], long_statement, "hypertension", max_terms=3)
    assert len(query.split()) <= 3


def test_condition_anchor_detects_hypertension_terms() -> None:
    assert _condition_anchor("58F, resistant hypertension despite ACEi.") == "hypertension"
    assert _condition_anchor("Patient shows elevated blood pressure on exam.") == "hypertension"
    assert _condition_anchor("BP remains poorly controlled on triple therapy.") == "hypertension"


def test_condition_anchor_defaults_to_hypertension_when_not_mentioned() -> None:
    """BUILD_SPEC.md's tool is explicitly scoped to antihypertensive-drug
    observations (its own one-line description, §0) -- an observation with
    no literal condition keyword still defaults in-domain, never a guess."""
    assert _condition_anchor("Patient shows an unusual response to losartan.") == "hypertension"
    assert _condition_anchor("") == "hypertension"
    assert _condition_anchor(None) == "hypertension"  # type: ignore[arg-type]


def test_entity_pubmed_query_real_stance_entities_still_work() -> None:
    """When neutralize_query's own extraction DOES succeed (non-empty,
    high-quality entities), those are used too -- the fix does not regress
    the case where entity extraction works well."""
    query = _entity_pubmed_query(["SLC12A3", "thiazide"], "irrelevant filler text", "hypertension")
    words_lower = query.lower().split()
    assert "hypertension" in words_lower
    assert "slc12a3" in words_lower
    assert "thiazide" in words_lower
