# PTPN2 expression and type I IFN response in NTC CD4⁺ Tregs vs Teffs

**Dataset:** GSE278572 — *Centralized control of dynamic gene regulatory circuits governs human T cell rest and activation* (Perturb-CITE-seq, Marson lab). 2 donors, resting + stimulated CD4⁺ Tregs and Teffs, HTO-hashed, 8 lanes.

## Methods

- **Source files:** 10x matrix (`barcodes/features/matrix`, 36,807 features × 249,799 cells, cellranger-7.1.0) and `protospacer_calls_per_cell.csv.gz`.
- **NTC selection:** kept cells with a **single non-targeting guide** (`Non-Targeting_1..9_CRISPRi`) so endogenous expression is unperturbed → **21,943 cells**. Because the full matrix (3.86 GB gzipped, 1.03B nonzeros) exceeds available RAM, it was stream-filtered over HTTP to the NTC columns, producing a compact 10x trio (36,601 Gene-Expression genes × 21,943 cells, 90.8M nonzeros). Counts verified all-integer.
- **Preprocessing:** light QC (min 200 genes/cell, genes in ≥3 cells → 21,922 × 25,650). Raw counts kept in a layer; `normalize_total(target_sum=1e4)` + `log1p`.
- **Cell-type gating (on detection, count>0):** CD4⁺ T = CD3E>0 & CD4>0 & CD8A==0 (n=14,310). **Treg** = +FOXP3>0 & IL2RA>0 (n=5,972); **Teff** = +FOXP3==0 & IL2RA==0 (n=1,886).
- **Type I IFN score:** `score_genes` over [IFIT1, IFIT3, MX1, OAS1, ISG15, IFI44L]; a memory-safe reimplementation (control-bin sampling identical to scanpy) validated at r=0.993 vs `sc.tl.score_genes`.
- **Statistics:** Mann-Whitney (my p matches `sc.tl.rank_genes_groups` exactly); canonical scanpy log2FC = log2(expm1(mean_Treg)/expm1(mean_Teff)); rank-biserial = 2·AUC−1; Spearman for correlations; paired Wilcoxon signed-rank for lane pseudobulk. 95% CIs by 2,000× bootstrap. BH-FDR across the family of 19 tests.

## Key results

| Test | Result | FDR |
|---|---|---|
| PTPN2 Treg vs Teff (per-cell, Wilcoxon) | log2FC **+0.171** [95% CI 0.070, 0.280]; AUC 0.553; rank-biserial +0.107; detection 74.3% vs 45.7% | **9.8×10⁻¹²** |
| PTPN2 Treg vs Teff (lane pseudobulk, signed-rank) | 7/8 lanes Treg>Teff; mean paired diff +0.043 | **0.037** |
| PTPN2 ~ IFN score, **Treg** (Spearman) | ρ **−0.035** [−0.061, −0.008] | **0.013** |
| PTPN2 ~ IFN score, **Teff** (neg. control) | ρ −0.017 [−0.061, +0.031] | 0.54 (ns) |
| Co-expr JAK1~FOXP3 (Treg) | ρ +0.237 | 6.8×10⁻⁷⁶ |
| Co-expr PTPN2~JAK1 / PTPN2~FOXP3 (Treg) | ρ −0.069 each | 3.0×10⁻⁷ |

**Interpretation.** PTPN2 is expressed at modestly higher levels and far more frequently in Tregs than in strictly-defined Teffs, robust to lane-level batch structure. Within Tregs — but not Teffs — PTPN2 correlates weakly *negatively* with the type I IFN-response signature, consistent with PTPN2's established role as a negative regulator of JAK-STAT/IFN signaling. Effect magnitudes are small (typical of sparse single-cell data with dropout) but statistically robust after FDR correction.

## Caveats

1. **Donor not recoverable.** The 4 HTO hashtags encode *condition* (Resting/Stimulated × Treg/Teff), not donor, and no genotypes are deposited for genetic demultiplexing. Step 7 pseudo-bulk therefore uses the **8 lanes** as the replicate unit (labeled lane-level, not genetically-resolved donor-level).
2. **Activation confound.** The strict marker definitions tie cell type to activation state: IL2RA (CD25) is an activation marker, so `IL2RA==0` Teffs are enriched for *resting* cells while `IL2RA>0` Tregs include many *stimulated* cells (cross-tab vs HTO condition in `ntc_annotated.h5ad`). The PTPN2 Treg>Teff contrast is thus partly confounded with stimulation. The IFN-correlation analysis is *within* each cell type, so it is not affected by this between-type confound.
3. The strict Teff definition (FOXP3==0 & IL2RA==0) is deliberately conservative; 6,452 CD4⁺ cells are neither Treg nor Teff ("other") and are shown but excluded from the two-group tests.
