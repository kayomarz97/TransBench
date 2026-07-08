"""fixtures.py — the 3 demo observations (BUILD_SPEC.md §8): flagship + 2 backups.

Sourcing note: BUILD_SPEC.md §8 gives the **flagship** observation as a
literal quoted sentence — reproduced verbatim below, unmodified. It gives the
2 **backup** demos only as short topic labels ("Backup 1 — off-target/
repurposing (ARB pleiotropy)"; "Backup 2 — pharmacogenomic non-response
(thiazide/Na-transport variation → GWAS/eQTL)"), not full vignette text — so
their ``observation`` strings below are authored to match those exact topics
(same clipped clinical-shorthand register as the flagship), not copied from
the spec (there is nothing to copy for them).
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
