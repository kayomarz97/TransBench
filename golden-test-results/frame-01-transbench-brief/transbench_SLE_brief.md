# TransBench Translational Research Brief

**Observation (de-identified, verbatim):**

> 32F with systemic lupus erythematosus and persistent moderate disease activity despite hydroxychloroquine and mycophenolate mofetil at target doses; peripheral blood shows reduced CD4+CD25+FOXP3+ regulatory T-cell frequency; incomplete response to standard immunosuppression.

*Pipeline models — reasoning/deep: `claude-sonnet-4-6`, cheap: `claude-haiku-4-5-20251001`; temperature 0; condition anchor: systemic lupus erythematosus; focus_drug: None.*

**Contents:** 6 biological axes · 3 falsifiable hypotheses · 41 references (38 with PMIDs) · 1 runnable experiment · 0 contradictions surfaced

---

## 1. Decomposed biological axes

### Axis 1. `regulatory_t_cell_dysfunction`

The core mechanistic finding is a reduced frequency of CD4+CD25+FOXP3+ Tregs in peripheral blood, directly implicating failure of peripheral immune tolerance as a driver of persistent SLE disease activity. This motivates investigation into Treg biology, FOXP3 stability, and IL-2 signaling deficits characteristic of SLE.

**Key entities:** CD4+CD25+FOXP3+ Tregs, FOXP3, IL-2, peripheral immune tolerance, Treg frequency

### Axis 2. `autoimmune_effector_imbalance`

Reduced Tregs in the context of active SLE implies a skewed Treg/Teffector ratio, favoring autoreactive Th17, Tfh, and other effector populations that drive autoantibody production and tissue inflammation. This axis motivates profiling of effector subsets and cytokine milieu (IL-17, IL-6, IFN-γ).

**Key entities:** Th17 cells, T follicular helper cells, autoreactive B cells, IL-17, IFN-γ, IL-6, Treg/Teff ratio

### Axis 3. `treatment_refractory_disease_mechanism`

Persistent moderate disease activity despite hydroxychloroquine and mycophenolate mofetil at target doses defines a refractory phenotype. The Treg deficit may mechanistically explain inadequate immunosuppressive response and motivates evaluation of alternative or add-on therapies targeting Treg restoration (e.g., low-dose IL-2, belimumab, voclosporin).

**Key entities:** hydroxychloroquine, mycophenolate mofetil, treatment refractoriness, low-dose IL-2, belimumab, disease activity

### Axis 4. `epigenetic_transcriptional_regulation`

FOXP3 expression and Treg lineage stability are governed by epigenetic mechanisms including DNA methylation of the FOXP3 TSDR (Treg-specific demethylated region). In SLE, aberrant methylation patterns can destabilize Tregs and convert them to pathogenic effectors, motivating epigenomic investigation.

**Key entities:** FOXP3 TSDR, DNA methylation, DNMT3A, Treg plasticity, epigenetic instability

### Axis 5. `cytokine_signaling_microenvironment`

Treg survival and expansion depend critically on IL-2 signaling, which is known to be deficient in SLE due to reduced IL-2 production by T cells and competitive consumption by activated effectors. Elevated type I IFN and IL-6 further suppress Treg function and promote Treg-to-Th17 conversion, linking the inflammatory milieu to the observed Treg deficit.

**Key entities:** IL-2, IL-2 receptor signaling, type I interferon, IL-6, JAK-STAT pathway, STAT5

### Axis 6. `biomarker_disease_monitoring`

Peripheral blood Treg frequency serves as a candidate immunological biomarker of disease activity and treatment response in SLE. This axis motivates longitudinal studies correlating Treg counts with SLEDAI scores, complement levels, and anti-dsDNA titers to validate Tregs as actionable monitoring tools.

**Key entities:** Treg frequency, SLEDAI, anti-dsDNA antibodies, complement C3/C4, flow cytometry, immunological biomarker

---

## 2. Falsifiable hypotheses

### H1 — axis: `epigenetic_transcriptional_regulation` · priority: **high**

- **Novelty:** `open_question`  |  **Confidence:** `low`  |  **Grounded:** `False`  |  supporting: 0 · contradicting: 0

**Statement.** In this patient, hypermethylation of the FOXP3 Treg-specific demethylated region (TSDR) in CD4+ T cells — driven by aberrant DNMT3A upregulation downstream of chronic type I IFN signaling — destabilizes the Treg lineage commitment program, causing existing Tregs to lose FOXP3 expression and convert toward a pathogenic IL-17-secreting phenotype, thereby reducing measured peripheral Treg frequency independently of impaired de novo Treg generation.

**Prediction.** If true: (1) Bisulfite pyrosequencing of sorted CD4+CD25+ T cells from this patient will show significantly higher FOXP3 TSDR CpG methylation percentage compared to HCQ/MMF-responsive SLE controls matched for age, sex, and ancestry; (2) DNMT3A protein expression will be elevated in CD4+ T cells and will correlate positively with TSDR methylation level and inversely with FOXP3 MFI; (3) A fraction of CD4+CD25-IL-17+ cells in peripheral blood will carry a partially demethylated TSDR signature consistent with Treg origin; and (4) ex vivo treatment of patient CD4+ T cells with the DNMT inhibitor 5-azacytidine will rescue FOXP3 expression and suppress IL-17 secretion in a dose-dependent manner, an effect that will be blunted if STAT1 (the IFN-driven DNMT3A inducer) is simultaneously knocked down.

**Rationale.** FOXP3 TSDR methylation is the primary epigenetic lock for stable Treg identity; DNMT3A is the methyltransferase most implicated in its re-methylation under inflammatory conditions. Type I IFN — characteristically elevated in SLE and known to activate STAT1, which can transcriptionally induce DNMT3A — provides a disease-specific upstream driver. This mechanism is genuinely open: it is not established whether IFN-driven DNMT3A is the dominant TSDR methylator in vivo in SLE versus other methyltransferases (DNMT1, DNMT3B), nor whether Treg-to-Th17 conversion quantitatively accounts for the observed frequency drop versus simple Treg apoptosis. The prediction distinguishes this hypothesis from a simple Treg generation defect by requiring evidence of lineage conversion.

**Novelty verdict.** The individual mechanistic components are each partially supported in the literature — FOXP3 TSDR methylation regulating Treg stability, DNMT3A involvement in Treg epigenetics, type I IFN signaling influencing T cell epigenetics, and Treg-to-Th17 plasticity in inflammatory/autoimmune contexts — but the specific causal chain proposed here (chronic type I IFN → aberrant DNMT3A upregulation → TSDR hypermethylation → Treg-to-IL-17 conversion as the dominant mechanism of reduced peripheral Treg frequency in this SLE patient) has not been established as a documented, validated pathway. The evidence provided confirms general Treg epigenetic plasticity and FOXP3 instability in inflammatory conditions but does not confirm this precise mechanistic sequence. The hypothesis is mechanistically plausible and generates testable, specific predictions, but remains unresolved and not well-documented as a unified pathway, making it a legitimate open question rather than an established finding or an unsupported speculation. (citing PMID 41677595, PMID 41987918, PMID 41970643)

**Grounding evidence (4 items):**

| PMID | Yr | Supports | Entailment | Grade | Title |
|------|----|----------|------------|-------|-------|
| [41677595](https://pubmed.ncbi.nlm.nih.gov/41677595/) | 2026 | True | unclear | systematic_review | Unraveling the Epigenetic Regulation of Regulatory T Cells in Cancer Immunity. |
| [41987918](https://pubmed.ncbi.nlm.nih.gov/41987918/) | 2026 | True | unclear | systematic_review | A stage-based framework to interpret regulatory T cell biology after heart transplantation. |
| [41970643](https://pubmed.ncbi.nlm.nih.gov/41970643/) | 2026 | True | unclear | systematic_review | Tumor microenvironment-induced epigenetic reprogramming of Tregs and its impact on immunotherapy. |
| [41294856](https://pubmed.ncbi.nlm.nih.gov/41294856/) | 2025 | True | unclear | systematic_review | Improvement of Treg Selectivity and Stability for Diabetes Mellitus Type 1 Treatment: Complex Approach for Perspective Technologies. |

### H2 — axis: `cytokine_signaling_microenvironment` · priority: **high**

- **Novelty:** `open_question`  |  **Confidence:** `high`  |  **Grounded:** `True`  |  supporting: 3 · contradicting: 0

**Statement.** Persistent moderate SLE disease activity in this patient is mechanistically sustained by a cell-intrinsic defect in STAT5 phosphorylation within CD4+CD25+ Tregs — caused by chronic upregulation of the phosphatase PTPN2 (TC-PTP) driven by elevated type I IFN — that renders existing Tregs refractory to available IL-2 concentrations, explaining both the functional Treg insufficiency and the failure of MMF/HCQ to restore tolerance despite not directly targeting IL-2/STAT5 signaling.

**Prediction.** If true: (1) Intracellular phospho-flow cytometry will show significantly reduced pSTAT5(Y694) in CD4+CD25+FOXP3+ Tregs from this patient relative to disease-matched HCQ/MMF responders, at identical exogenous IL-2 concentrations (100 IU/mL), ruling out IL-2 availability as the sole bottleneck; (2) PTPN2 protein expression will be elevated specifically in Tregs (not in CD4+CD25- conventional T cells) and will correlate with IFN score measured by a 6-gene type I IFN transcript signature; (3) siRNA-mediated knockdown of PTPN2 in patient Tregs ex vivo will rescue pSTAT5 levels and restore suppressive capacity (measured by CFSE-based Teff proliferation assay) to levels seen in healthy controls; and (4) this PTPN2 elevation will be more pronounced in patients carrying the SLE-associated PTPN22 R620W variant or in those of East Asian ancestry (where IFN-high SLE endotypes are enriched), introducing a pharmacogenomic modifier.

**Rationale.** IL-2/STAT5 signaling is essential for Treg survival and FOXP3 maintenance, and its deficiency in SLE is established at the level of IL-2 production. However, whether the defect is primarily in IL-2 availability versus receptor-proximal signaling capacity is unresolved. PTPN2 is a JAK1/JAK3 and STAT5 phosphatase induced by IFN-α/β via STAT1 and is expressed in T cells; its Treg-specific upregulation as a cell-intrinsic signaling brake has not been demonstrated in SLE. This hypothesis is falsifiable because it predicts a signaling defect detectable even when IL-2 is provided exogenously, distinguishing it from the simpler IL-2 scarcity model and directly explaining why MMF (which does not boost IL-2 signaling) fails.

**Novelty verdict.** The hypothesis assembles several individually supported but not yet mechanistically integrated findings: (1) Treg dysfunction in SLE is well-documented but the specific cell-intrinsic STAT5 phosphorylation defect in CD4+CD25+FOXP3+ Tregs as a dominant mechanism in MMF/HCQ non-responders is not established; (2) PTPN2 (TC-PTP) is a known negative regulator of JAK-STAT signaling and type I IFN can upregulate phosphatases, but the specific IFN→PTPN2→pSTAT5 axis selectively within Tregs (not conventional T cells) in SLE has not been demonstrated in published literature; (3) the pharmacogenomic modifier layer (PTPN22 R620W or East Asian ancestry as amplifiers) adds further specificity that is plausible but entirely untested in this context; (4) the evidence provided supports general roles of phosphatases in immune homeostasis, JAK-STAT in autoimmunity, and IFN dysregulation in SLE, but none of the cited studies directly address PTPN2-mediated STAT5 dephosphorylation in Tregs as a mechanism of MMF/HCQ refractoriness. The hypothesis is mechanistically coherent, falsifiable with the proposed experiments, and sits at the intersection of real but unconnected literature threads — making it a genuine open question rather than either established or unsupported. (citing PMID 42225638, PMID 41567805, PMID 42027582)

**Grounding evidence (7 items):**

| PMID | Yr | Supports | Entailment | Grade | Title |
|------|----|----------|------------|-------|-------|
| [42225638](https://pubmed.ncbi.nlm.nih.gov/42225638/) | 2026 | True | unclear | systematic_review | Regulatory T cells in cancer and inflammation. |
| [41567805](https://pubmed.ncbi.nlm.nih.gov/41567805/) | 2025 | True | unclear | systematic_review | Regulatory T cell dysfunction and immunotherapeutic breakthroughs in type 1 diabetes. |
| [42027582](https://pubmed.ncbi.nlm.nih.gov/42027582/) | 2026 | True | unclear | systematic_review | Th17/treg balance in Inflammatory Bowel Disease: the role of microbial, and genetic regulators in disease modulation. |
| [42148104](https://pubmed.ncbi.nlm.nih.gov/42148104/) | 2026 | True | unclear | systematic_review | Adapting CAR-T and CAR-Treg cancer therapies for autoimmunity: innovations and challenges. |
| [41438753](https://pubmed.ncbi.nlm.nih.gov/41438753/) | 2025 | True | supports | systematic_review | JAK/STAT in human diseases: a common axis in immunodeficiencies and hematological disorders. |
| [39944077](https://pubmed.ncbi.nlm.nih.gov/39944077/) | 2025 | True | supports | systematic_review | Protein phosphatases in systemic autoimmunity. |
| [41359111](https://pubmed.ncbi.nlm.nih.gov/41359111/) | 2025 | True | supports | systematic_review | Interferon signaling pathways in health and disease. |

### H3 — axis: `autoimmune_effector_imbalance` · priority: **medium**

- **Novelty:** `open_question`  |  **Confidence:** `high`  |  **Grounded:** `True`  |  supporting: 3 · contradicting: 0

**Statement.** The reduced peripheral Treg frequency in this patient reflects active Treg sequestration into inflamed tissues rather than a systemic numerical deficit, driven by CXCR3 upregulation on Tregs in response to the IFN-γ-rich inflammatory milieu; these tissue-infiltrating Tregs undergo local functional reprogramming into FOXP3+IL-17+ hybrid cells upon exposure to IL-6 and IL-1β, paradoxically amplifying local inflammation while depleting the peripheral Treg pool — a mechanism not addressed by MMF or HCQ.

**Prediction.** If true: (1) Absolute Treg counts in peripheral blood will be disproportionately low relative to total CD4+ T cell counts, but CXCR3 expression on residual circulating Tregs will be significantly higher than in MMF/HCQ responders, consistent with preferential egress of CXCR3hi Tregs to IFN-γ-rich sites; (2) If skin or renal biopsy tissue is available, immunofluorescence will reveal FOXP3+IL-17A+ double-positive cells at inflammatory foci, at a frequency exceeding that seen in treatment-responsive SLE biopsies; (3) Ex vivo culture of patient peripheral Tregs in IL-6 + IL-1β (but not IL-6 alone) will induce IL-17 secretion without complete loss of FOXP3, and this conversion will be more pronounced in Tregs with high CXCR3 expression; and (4) Serum CXCL10 (the dominant CXCR3 ligand, induced by IFN-γ) will correlate inversely with peripheral Treg frequency across a longitudinal series of this patient's visits, and this correlation will be stronger than the correlation between Treg frequency and SLEDAI score alone, providing a mechanistic biomarker link.

**Rationale.** Peripheral Treg frequency measured by flow cytometry conflates true numerical reduction with redistribution; this distinction has major mechanistic implications but is rarely tested in clinical SLE studies. CXCR3-mediated Treg homing to IFN-γ-rich sites is established in murine models and some human autoimmune diseases but not systematically characterized in SLE refractory to standard therapy. The Treg-to-Th17 hybrid state under IL-6/IL-1β is documented in vitro but its in vivo relevance in tissue-infiltrating SLE Tregs is an open question. This hypothesis is falsifiable because it makes specific predictions about CXCR3 expression, tissue co-localization, and CXCL10 correlation that would be absent if the deficit were purely generative or epigenetic.

**Novelty verdict.** The hypothesis integrates several partially supported but unresolved mechanisms: CXCR3-driven Treg tissue sequestration in IFN-γ-rich environments has indirect support (CXCL10/CXCR3 axis in inflammation, TNFRSF18+ Tregs in irAE contexts), and Treg-to-Th17 plasticity via IL-6/IL-1β is documented in the literature but not specifically validated in SLE with the CXCR3hi Treg subset as the preferential converter. The specific mechanistic chain — CXCR3-mediated egress → local FOXP3+IL-17+ reprogramming → peripheral Treg depletion → MMF/HCQ insensitivity — as a unified, patient-level explanation is not established in the evidence provided and represents a plausible but unresolved synthesis. The predictions are testable and specific. This qualifies as a good open question rather than established or unsupported. (citing PMID 42233009, PMID 41890756, PMID 42338761)

**Grounding evidence (7 items):**

| PMID | Yr | Supports | Entailment | Grade | Title |
|------|----|----------|------------|-------|-------|
| [42233009](https://pubmed.ncbi.nlm.nih.gov/42233009/) | 2026 | True | supports | systematic_review | Regulatory T cells in vitiligo: a review of functional disequilibrium between peripheral blood and lesional tissue. |
| [41890756](https://pubmed.ncbi.nlm.nih.gov/41890756/) | 2026 | True | unclear | systematic_review | Targeting the chemokine-Treg axes in tumor immune evasion: from mechanisms to therapeutic opportunities. |
| [42338761](https://pubmed.ncbi.nlm.nih.gov/42338761/) | 2026 | True | unclear | systematic_review | Artemisinin-Related Therapeutic Strategies for Autoimmune Thyroiditis: Chemokine-Receptor Networks, Spatial Thyroid Biology, and Molecular Mechanisms. |
| [42344912](https://pubmed.ncbi.nlm.nih.gov/42344912/) | 2026 | True | unclear | systematic_review | Single-cell transcriptomic insights into the immune heterogeneity of immune checkpoint inhibitors related organ toxicities. |
| [41884484](https://pubmed.ncbi.nlm.nih.gov/41884484/) | 2026 | True | supports | systematic_review | The Dual Role of IP-10/CXCL10 in Liver Injury: From Pathogenic Mediator to Clinical Biomarker and Therapeutic Target. |
| [42327747](https://pubmed.ncbi.nlm.nih.gov/42327747/) | 2026 | True | unclear | systematic_review | Mechanisms and biomarkers of immune checkpoint inhibitor-associated myocarditis: from T cell imbalance to multicellular crosstalk. |
| [42320987](https://pubmed.ncbi.nlm.nih.gov/42320987/) | 2026 | True | supports | observational | Comprehensive characterization of the inflammatory ecosystems in immunotherapy-induced adverse events versus chronic inflammatory diseases. |

---

## 3. Runnable computational experiment (`top_experiment`)

**Backing hypothesis:** H2

**Question.** Does PTPN2 show preferential upregulation in CD4+CD25+ regulatory T cells relative to conventional CD4+ T cells within the Gladstone GSE278572 CD4+ Treg/Teff dataset, and does its expression correlate with type I IFN-response gene signatures in a Treg-specific manner consistent with the proposed IFN→PTPN2→impaired STAT5 axis?

**Dataset.** Human CD4+ regulatory (Treg) & effector (Teff) T cells — Perturb-CITE-seq, Gladstone/Marson lab (GEO: GSE278572)  
**Pointer:** https://www.ncbi.nlm.nih.gov/geo/query/acc.cgi?acc=GSE278572

**Method.** Single-cell RNA-seq differential expression and co-expression analysis in the Gladstone GSE278572 CD4+ Treg/Teff Perturb-CITE-seq dataset (non-targeting-control cells), focusing on CD4+ T cell subsets (Tregs vs. conventional CD4+ T cells). We will (1) isolate Treg and Tconv cell clusters using canonical marker genes, (2) compare PTPN2 expression between these populations, (3) compute a type I IFN response gene score per cell and test its correlation with PTPN2 within Tregs, and (4) examine co-expression of PTPN2 with STAT5A/STAT5B and FOXP3 to assess whether the proposed signaling brake is plausible at the transcriptional level in healthy human Tregs as a baseline reference.

**Protocol steps:**

1. DATA ACQUISITION: Download GSE278572 supplementary files (10x matrix: barcodes.tsv.gz, features.tsv.gz, matrix.mtx.gz) and the guide/protospacer-calls table from https://www.ncbi.nlm.nih.gov/geo/query/acc.cgi?acc=GSE278572. Load the matrix into scanpy; sc.pp.normalize_total + sc.pp.log1p.
1. CONTROL-CELL RESTRICTION: Join the guide/protospacer calls; retain ONLY non-targeting-control (NTC) cells so endogenous PTPN2/STAT5 expression is read without CRISPR-perturbation confounding.
1. CELL SUBSET ISOLATION: Restrict to CD4+ T cells (CD3E+, CD4+, CD8A-). Define Tregs as FOXP3+ IL2RA(CD25)+; conventional/effector CD4+ T cells (Teff) as FOXP3- IL2RA-.
1. PTPN2 EXPRESSION COMPARISON: Wilcoxon rank-sum test of PTPN2 between Treg and Teff; report p-value and log2 fold-change.
1. TYPE I IFN SCORE: Compute a per-cell IFN-response score from [IFIT1, IFIT3, MX1, OAS1, ISG15, IFI44L] via sc.tl.score_genes.
1. CORRELATION WITHIN TREGS: Spearman correlation of per-cell PTPN2 vs IFN score within Tregs; repeat in Teff as a negative control.
1. PTPN2 vs STAT5 CO-EXPRESSION: Within Tregs, pairwise Spearman correlations among PTPN2, STAT5A, STAT5B, JAK1, JAK3, FOXP3; render a correlation heatmap.
1. DONOR-LEVEL AGGREGATION: Pseudo-bulk PTPN2 per donor per cell type; paired Treg vs Teff comparison (Wilcoxon signed-rank).
1. VISUALIZATION & STATS: Composite figure (UMAP by cell type; PTPN2 violin Treg vs Teff; IFN-score-vs-PTPN2 scatter within Tregs; STAT5 co-expression heatmap). Report effect sizes, FDR-corrected p-values, and 95% CIs.

**Confirm if.** The hypothesis is supported if: (1) PTPN2 expression is significantly higher in FOXP3+CD25+ Tregs than in Tconv cells (Wilcoxon p < 0.05, log2FC > 0.5); (2) IFN score correlates positively with PTPN2 within Tregs (Spearman rho > 0.2, p < 0.05) but not significantly in Tconv cells (rho < 0.1 or p > 0.1), indicating Treg-specific IFN-driven PTPN2 upregulation; (3) PTPN2 shows a negative correlation with STAT5A or STAT5B within Tregs (rho < -0.15), consistent with a phosphatase brake on STAT5 signaling; and (4) the donor-level paired analysis confirms Treg-enriched PTPN2 in ≥ 70% of individual donors.

**Refute if.** The hypothesis is refuted if: (1) PTPN2 expression is not significantly different between Tregs and Tconv cells (p > 0.05 or log2FC < 0.2); (2) IFN score does not correlate with PTPN2 in Tregs (rho < 0.1, p > 0.2); (3) PTPN2 correlates equally or more strongly with IFN score in Tconv than in Tregs, suggesting no Treg-specific mechanism; or (4) PTPN2 and STAT5A/STAT5B show no negative correlation or a positive correlation within Tregs, inconsistent with PTPN2 acting as a STAT5 phosphatase brake in this population.

**Feasibility notes.** This analysis uses the Gladstone/Marson human CD4+ Treg/Teff Perturb-CITE-seq dataset (GEO GSE278572) as substrate. Two honest caveats carry forward: (1) it profiles primary human T cells, not SLE patients, so a positive result establishes that the proposed PTPN2/IFN/STAT5 co-expression architecture exists in human Tregs — a necessary baseline for, but not confirmation of, the SLE-specific mechanism; (2) because GSE278572 is a pooled CRISPR (Perturb-CITE-seq) experiment, endogenous PTPN2/STAT5 expression is read only from non-targeting-control (NTC) cells to avoid perturbation confounding. GSE278572 is public and directly downloadable from GEO.

### `claude_science_prompt` (verbatim — ready to run in Claude Science)

```text
Using the Gladstone/Marson CD4+ Treg/Teff single-cell dataset (GEO GSE278572 — download the supplementary 10x matrix [barcodes.tsv.gz, features.tsv.gz, matrix.mtx.gz] and the guide/protospacer-calls file from https://www.ncbi.nlm.nih.gov/geo/query/acc.cgi?acc=GSE278572), perform the following in Python with scanpy, pandas, scipy, and matplotlib/seaborn, and produce one multi-panel figure:

1. Load the 10x matrix into scanpy (sc.read_10x_mtx), then sc.pp.normalize_total and sc.pp.log1p.
2. Attach the guide/protospacer calls; keep ONLY non-targeting-control (NTC) cells so endogenous expression is unperturbed.
3. Restrict to CD4+ T cells (CD3E>0, CD4>0, CD8A==0). Define Tregs as FOXP3>0 AND IL2RA>0; effector/conventional CD4+ T cells (Teff) as FOXP3==0 AND IL2RA==0.
4. Compare PTPN2 log-normalized expression between Treg and Teff (Wilcoxon rank-sum; report p and log2 fold-change).
5. Compute a per-cell type I IFN-response score from [IFIT1, IFIT3, MX1, OAS1, ISG15, IFI44L] with sc.tl.score_genes. Within Tregs, Spearman-correlate per-cell PTPN2 with IFN score; repeat in Teff as a negative control.
6. Within Tregs, compute pairwise Spearman correlations among PTPN2, STAT5A, STAT5B, JAK1, JAK3, FOXP3; render a correlation heatmap.
7. Pseudo-bulk PTPN2 per donor per cell type; plot donor-level Treg vs Teff as a paired comparison (Wilcoxon signed-rank).
8. Final composite figure: (A) UMAP colored by cell type; (B) violin of PTPN2 in Treg vs Teff; (C) scatter of IFN score vs PTPN2 within Tregs; (D) STAT5 co-expression heatmap. Report effect sizes, FDR-corrected p-values, and 95% CIs.
```

---

## 4. Uncertainty & contradictions

**Contradictions surfaced:** 0 (none)

**Uncertainty note.** All three hypotheses remain mechanistically plausible but unvalidated as unified causal chains in this patient's SLE context. H1 and H3 lack direct supporting evidence for their proposed sequences (IFN→DNMT3A→TSDR hypermethylation→Treg conversion, and CXCR3-driven sequestration→local IL-17 reprogramming, respectively), while H2—though grounded in real biology—has not been demonstrated specifically for PTPN2-mediated STAT5 dephosphorylation within Tregs as a mechanism of MMF/HCQ refractoriness. Critically, no evidence directly addresses why standard immunosuppression fails in this patient, and the relative contribution of each proposed mechanism (epigenetic instability, STAT5 signaling defects, or tissue sequestration) remains unknown. Readers should treat these as testable hypotheses rather than established explanations and recognize that the peripheral Treg deficit may reflect multiple simultaneous mechanisms not fully captured by any single model. Translational note: some supporting evidence may derive from the same mechanism established in other diseases, tissues, or model systems rather than this exact clinical context. That is expected and intended — a mechanism shown elsewhere is a testable hypothesis, not an established fact, for this patient; the proposed experiment is designed precisely to test whether it operates here.

---

## 5. References (41)

| PMID | Yr | Grade | Source | Title |
|------|----|-------|--------|-------|
| [—](https://clinicaltrials.gov/study/NCT02540395) |  |  | clinicaltrials.gov | Prospective Donor Specific T Response Measurment for IS Minimization in de Novo Renal Transplantation |
| [—](https://clinicaltrials.gov/study/NCT02779881) |  |  | clinicaltrials.gov | Epigenetic Features of FoxP3 in Children With Cow's Milk Allergy |
| [—](https://doi.org/10.64898/2026.03.18.712682) | 2026 |  | Europe PMC | Transient immune landscape remodelling shapes CD8 T-cell priming during infection |
| [41314617](https://pubmed.ncbi.nlm.nih.gov/41314617/) | 2026 |  | Europe PMC | Pharmacokinetics, lineage identity, and trafficking of ex vivo expanded polyclonal regulatory T cells in a prospective randomized clinical trial of kidney transplant recipients with allograft inflammation. |
| [41639687](https://pubmed.ncbi.nlm.nih.gov/41639687/) | 2026 |  | Europe PMC | From pathogenesis to therapy: the emerging role of regulatory T cells in amyotrophic lateral sclerosis. |
| [41677595](https://pubmed.ncbi.nlm.nih.gov/41677595/) | 2026 | systematic_review | Europe PMC | Unraveling the Epigenetic Regulation of Regulatory T Cells in Cancer Immunity. |
| [41692800](https://pubmed.ncbi.nlm.nih.gov/41692800/) | 2026 |  | Europe PMC | Lipid metabolism in homeostasis and disease. |
| [41800022](https://pubmed.ncbi.nlm.nih.gov/41800022/) | 2026 |  | Europe PMC | Regulatory T Cell Heterogeneity in the Steady State and Tumor. |
| [41884484](https://pubmed.ncbi.nlm.nih.gov/41884484/) | 2026 | systematic_review | Europe PMC | The Dual Role of IP-10/CXCL10 in Liver Injury: From Pathogenic Mediator to Clinical Biomarker and Therapeutic Target. |
| [41890756](https://pubmed.ncbi.nlm.nih.gov/41890756/) | 2026 | systematic_review | Europe PMC | Targeting the chemokine-Treg axes in tumor immune evasion: from mechanisms to therapeutic opportunities. |
| [41916981](https://pubmed.ncbi.nlm.nih.gov/41916981/) | 2026 |  | Europe PMC | CXCR3 is associated with T-cell-induced heart damage in acute rheumatic fever. |
| [41970643](https://pubmed.ncbi.nlm.nih.gov/41970643/) | 2026 | systematic_review | Europe PMC | Tumor microenvironment-induced epigenetic reprogramming of Tregs and its impact on immunotherapy. |
| [41986797](https://pubmed.ncbi.nlm.nih.gov/41986797/) | 2026 |  | Europe PMC | PTPN1/PTPN2 inhibition improves NK cancer therapy by enhancing IL-2 and mitigating TGFβ1 responses. |
| [41987918](https://pubmed.ncbi.nlm.nih.gov/41987918/) | 2026 | systematic_review | Europe PMC | A stage-based framework to interpret regulatory T cell biology after heart transplantation. |
| [42027582](https://pubmed.ncbi.nlm.nih.gov/42027582/) | 2026 | systematic_review | Europe PMC | Th17/treg balance in Inflammatory Bowel Disease: the role of microbial, and genetic regulators in disease modulation. |
| [42039205](https://pubmed.ncbi.nlm.nih.gov/42039205/) | 2026 |  | Europe PMC | Targeting regulatory T cells in glioblastoma: from mechanistic insights to novel immunotherapeutic strategies. |
| [42079605](https://pubmed.ncbi.nlm.nih.gov/42079605/) | 2026 |  | Europe PMC | Regulatory T cell therapies: biological foundations, engineering strategies, and clinical translation. |
| [42147444](https://pubmed.ncbi.nlm.nih.gov/42147444/) | 2026 |  | Europe PMC | Clinical development of tacrolimus-resistant regulatory T cells to enable simultaneous immunosuppression and immune regulation. |
| [42148104](https://pubmed.ncbi.nlm.nih.gov/42148104/) | 2026 | systematic_review | Europe PMC | Adapting CAR-T and CAR-Treg cancer therapies for autoimmunity: innovations and challenges. |
| [42153603](https://pubmed.ncbi.nlm.nih.gov/42153603/) | 2026 |  | Europe PMC | A Novel PTPN2 Isoform Differentially Regulates Immune Response. |
| [42183190](https://pubmed.ncbi.nlm.nih.gov/42183190/) | 2026 |  | Europe PMC | Regulatory T cell therapy in autoimmune and immune-mediated diseases: from basic research to clinical practice and future perspectives. |
| [42193429](https://pubmed.ncbi.nlm.nih.gov/42193429/) | 2026 |  | Europe PMC | Novel Mechanistic Insights into Primary Biliary Cholangitis: From Pathogenesis to Mesenchymal Stem Cell-Mediated Repair. |
| [42196608](https://pubmed.ncbi.nlm.nih.gov/42196608/) | 2026 |  | Europe PMC | Regulatory T Cells in Hepatocellular Carcinoma: Spatial Niches, Biomarkers, and Clinical Implications. |
| [42225638](https://pubmed.ncbi.nlm.nih.gov/42225638/) | 2026 | systematic_review | Europe PMC | Regulatory T cells in cancer and inflammation. |
| [42233009](https://pubmed.ncbi.nlm.nih.gov/42233009/) | 2026 | systematic_review | Europe PMC | Regulatory T cells in vitiligo: a review of functional disequilibrium between peripheral blood and lesional tissue. |
| [42293761](https://pubmed.ncbi.nlm.nih.gov/42293761/) | 2026 |  | Europe PMC | Immune dysregulation in gestational diabetes mellitus: placental downregulation of CXCL9 and IL1RL1 and altered immune cell infiltration. |
| [42320987](https://pubmed.ncbi.nlm.nih.gov/42320987/) | 2026 | observational | Europe PMC | Comprehensive characterization of the inflammatory ecosystems in immunotherapy-induced adverse events versus chronic inflammatory diseases. |
| [42327747](https://pubmed.ncbi.nlm.nih.gov/42327747/) | 2026 | systematic_review | Europe PMC | Mechanisms and biomarkers of immune checkpoint inhibitor-associated myocarditis: from T cell imbalance to multicellular crosstalk. |
| [42338761](https://pubmed.ncbi.nlm.nih.gov/42338761/) | 2026 | systematic_review | Europe PMC | Artemisinin-Related Therapeutic Strategies for Autoimmune Thyroiditis: Chemokine-Receptor Networks, Spatial Thyroid Biology, and Molecular Mechanisms. |
| [42344912](https://pubmed.ncbi.nlm.nih.gov/42344912/) | 2026 | systematic_review | Europe PMC | Single-cell transcriptomic insights into the immune heterogeneity of immune checkpoint inhibitors related organ toxicities. |
| [42358982](https://pubmed.ncbi.nlm.nih.gov/42358982/) | 2026 |  | Europe PMC | CD25+CD27+CD70- alloantigen-specific Tregs: promising stable immunotherapy for transplantation. |
| [39944077](https://pubmed.ncbi.nlm.nih.gov/39944077/) | 2025 | systematic_review | Europe PMC | Protein phosphatases in systemic autoimmunity. |
| [40918088](https://pubmed.ncbi.nlm.nih.gov/40918088/) | 2025 |  | Europe PMC | Promotion of Treg/Th17 balance in MRL/lpr mice by Jianpi-Zishen Formula via modulation of DNMT1-mediated Foxp3 methylation. |
| [41136770](https://pubmed.ncbi.nlm.nih.gov/41136770/) | 2025 |  | Europe PMC | In silico modeling guides identification of novel JAK1 variants associated with immune dysregulation. |
| [41294856](https://pubmed.ncbi.nlm.nih.gov/41294856/) | 2025 | systematic_review | Europe PMC | Improvement of Treg Selectivity and Stability for Diabetes Mellitus Type 1 Treatment: Complex Approach for Perspective Technologies. |
| [41359111](https://pubmed.ncbi.nlm.nih.gov/41359111/) | 2025 | systematic_review | Europe PMC | Interferon signaling pathways in health and disease. |
| [41438753](https://pubmed.ncbi.nlm.nih.gov/41438753/) | 2025 | systematic_review | Europe PMC | JAK/STAT in human diseases: a common axis in immunodeficiencies and hematological disorders. |
| [41567805](https://pubmed.ncbi.nlm.nih.gov/41567805/) | 2025 | systematic_review | Europe PMC | Regulatory T cell dysfunction and immunotherapeutic breakthroughs in type 1 diabetes. |
| [39028869](https://pubmed.ncbi.nlm.nih.gov/39028869/) | 2024 |  | Europe PMC | Haploinsufficiency in PTPN2 leads to early-onset systemic autoimmunity from Evans syndrome to lupus. |
| [36814922](https://pubmed.ncbi.nlm.nih.gov/36814922/) | 2023 |  | Europe PMC | TRAF3: Guardian of T lymphocyte functions. |
| [37670950](https://pubmed.ncbi.nlm.nih.gov/37670950/) | 2023 |  | Europe PMC | Protein tyrosine phosphatase non-receptor type 2 as the therapeutic target of atherosclerotic diseases: past, present and future. |

---

## Disclaimer

> Research hypothesis generation only. Not clinical, diagnostic, or prescribing advice.
