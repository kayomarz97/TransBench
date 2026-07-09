"""fixtures.py — the 3 demo observations (BUILD_SPEC.md §8): flagship + 2
backups, PLUS 4 cross-domain observations (Phase 8, domain-universalization)
proving TransBench is not hardcoded to hypertension/antihypertensive drugs.

Sourcing note: BUILD_SPEC.md §8 gives the **flagship** observation as a
literal quoted sentence — reproduced verbatim below, unmodified. It gives the
2 **backup** demos only as short topic labels ("Backup 1 — off-target/
repurposing (ARB pleiotropy)"; "Backup 2 — pharmacogenomic non-response
(thiazide/Na-transport variation → GWAS/eQTL)"), not full vignette text — so
their ``observation`` strings below are authored to match those exact topics
(same clipped clinical-shorthand register as the flagship), not copied from
the spec (there is nothing to copy for them). ``DEMO_OBSERVATIONS`` below is
UNCHANGED (still exactly these 3, in this order) — every existing live-call
budget in the test suite that parametrizes over it is undisturbed.

Cross-domain observations (new, Phase 8): deliberately span disease areas
with NO overlap with hypertension's own biology (autoimmune/rheumatologic,
oncology/immuno-oncology, infectious/microbiome, metabolic/endocrine) — each
authored in the same clipped clinical-shorthand register as the flagship, so
they exercise the SAME decomposer/hypothesis-generator/retrieval/grading
pipeline on genuinely different mechanisms. ``AUTOIMMUNE_OBSERVATION`` is the
PROVEN regression case (a live run before this task's fix built PubMed
queries like "hypertension Th17 Methotrexate" / "hypertension MTX
Inadequate" for this exact scenario — the disease anchor was hardcoded to
"hypertension" for every run regardless of the observation's own condition —
grounding zero real evidence; see ``agents.py``'s ``_condition_anchor``/
``prompts.py``'s module docstring for the full fix). These 4 are standalone
constants, NOT added to ``DEMO_OBSERVATIONS`` — they are exercised by
``tests/test_universal_domains.py`` (a dedicated, explicitly-bounded set of
live calls), not by ``test_schema.py``'s existing parametrization, so no
existing test file's live-call budget grows.
"""
from __future__ import annotations

# --- Flagship: resistant hypertension / immune axis (BUILD_SPEC.md §8, verbatim) ---
FLAGSHIP_OBSERVATION = (
    "58F, resistant hypertension despite ACEi + CCB + thiazide at max dose; "
    "elevated hs-CRP; poor response to RAAS blockade."
)

# --- Backup 1: off-target/repurposing (ARB pleiotropy) — authored to spec topic ---
BACKUP_1_OBSERVATION = (
    "62M on losartan for hypertension shows a marked reduction in urinary "
    "albumin-to-creatinine ratio and improved fasting insulin sensitivity "
    "beyond what blood-pressure lowering alone would predict; suspected "
    "AT1-receptor-independent (off-target) pleiotropic activity worth "
    "characterizing for repurposing beyond blood-pressure control."
)

# --- Backup 2: pharmacogenomic non-response (thiazide/Na-transport, GWAS/eQTL) ---
BACKUP_2_OBSERVATION = (
    "Two 55-year-old patients, matched for BMI, sodium intake, and baseline "
    "blood pressure, are started on hydrochlorothiazide at the same dose; "
    "one achieves robust natriuresis and blood-pressure control within "
    "weeks, the other shows minimal natriuretic response and persistent "
    "hypertension despite confirmed adherence — suspected pharmacogenomic "
    "variation in renal sodium-transporter genes."
)

DEMO_OBSERVATIONS: list[dict] = [
    {
        "name": "flagship_resistant_hypertension_immune_axis",
        "observation": FLAGSHIP_OBSERVATION,
        "focus_drug": None,
    },
    {
        "name": "backup1_arb_pleiotropy",
        "observation": BACKUP_1_OBSERVATION,
        "focus_drug": "losartan",
    },
    {
        "name": "backup2_thiazide_pharmacogenomics",
        "observation": BACKUP_2_OBSERVATION,
        "focus_drug": "hydrochlorothiazide",
    },
]

# ---------------------------------------------------------------------------
# Cross-domain observations (Phase 8, domain-universalization) — see module
# docstring. NOT part of DEMO_OBSERVATIONS; used by
# tests/test_universal_domains.py.
# ---------------------------------------------------------------------------

# --- Autoimmune/rheumatologic — THE PROVEN 0-grounding regression case ---
AUTOIMMUNE_OBSERVATION = (
    "52F, rheumatoid arthritis with inadequate response to methotrexate "
    "(MTX) monotherapy at maximal tolerated dose; persistent synovitis "
    "despite therapy; elevated Th17-associated cytokines; anti-CCP positive."
)

# --- Oncology/immuno-oncology — checkpoint-inhibitor resistance ---
ONCOLOGY_OBSERVATION = (
    "61M, metastatic melanoma progressing on pembrolizumab (anti-PD-1) "
    "monotherapy after an initial partial response; tumor biopsy shows an "
    "increased frequency of exhausted CD8+ T cells with elevated TIM-3 "
    "expression; low tumor mutational burden."
)

# --- Infectious disease/microbiome — recurrent C. difficile ---
INFECTIOUS_OBSERVATION = (
    "74M, third recurrence of Clostridioides difficile infection within 6 "
    "months despite an appropriate vancomycin taper; stool microbiome shows "
    "markedly reduced diversity with depleted secondary-bile-acid-producing "
    "Clostridia."
)

# --- Metabolic/endocrine — T2D inadequately controlled on metformin ---
METABOLIC_OBSERVATION = (
    "49M, type 2 diabetes with persistent postprandial hyperglycemia "
    "despite metformin at maximal dose and confirmed adherence; elevated "
    "fasting glucagon; blunted GLP-1 response to mixed-meal testing."
)

CROSS_DOMAIN_OBSERVATIONS: list[dict] = [
    {
        "name": "autoimmune_ra_methotrexate_inadequate_response",
        "observation": AUTOIMMUNE_OBSERVATION,
        "focus_drug": "methotrexate",
    },
    {
        "name": "oncology_melanoma_checkpoint_inhibitor_resistance",
        "observation": ONCOLOGY_OBSERVATION,
        "focus_drug": "pembrolizumab",
    },
    {
        "name": "infectious_recurrent_c_difficile",
        "observation": INFECTIOUS_OBSERVATION,
        "focus_drug": "vancomycin",
    },
    {
        "name": "metabolic_t2d_metformin_inadequate_response",
        "observation": METABOLIC_OBSERVATION,
        "focus_drug": "metformin",
    },
]
