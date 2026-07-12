# Golden-test results — TransBench inside Claude Science

*Captured 2026-07-12 from a run on this device. This folder is a self-contained, offline record of
one end-to-end golden test: a bedside observation went into **TransBench** (our MCP connector), came
back as a grounded, testable brief, and **Claude Science then actually ran the experiment the brief
proposed** — producing a figure and statistics on real single-cell data.*

---

## In plain language (read this first)

Think of it as a relay race with two legs, both captured here:

1. **Leg 1 — the idea becomes a plan.** We handed TransBench a de-identified lupus (SLE) case: a patient
   whose regulatory T-cells (“Tregs”, the immune system’s brakes) are depleted and who isn’t responding
   to standard immunosuppression (MMF/HCQ). TransBench returned a **brief**: three competing, *falsifiable*
   hypotheses for *why*, each tied to real published papers (PMIDs), and it picked one concrete experiment
   to run first. *(This is the “golden” brief — a pre-captured, instant answer we use for clean demos.)*

2. **Leg 2 — the plan gets executed.** Inside Claude Science, that proposed experiment was actually carried
   out on a **public human T-cell dataset (GEO GSE278572)**: ~250,000 cells filtered down to unperturbed
   controls, cell types identified, and the key gene (**PTPN2**) tested in Tregs vs effector T-cells, with
   proper statistics and a figure. **The headline held up:** PTPN2 is expressed more in Tregs, and within
   Tregs it tracks *inversely* with the interferon-response signature — consistent with the brief’s mechanism.

So this folder is the evidence that TransBench doesn’t just *describe* an experiment — paired with Claude
Science, it produces one that runs and yields a real, statistically-backed result.

---

## The arc, at a glance

```
   de-identified SLE observation
              │
              ▼
   ┌──────────────────────────┐   FRAME 63e58999…
   │  TransBench (MCP tool)    │   → generate_experiment
   │  "golden" brief           │   → 3 hypotheses w/ PMIDs, 1 chosen experiment
   └──────────────────────────┘      (frame-01-transbench-brief/)
              │  proposes: test PTPN2 / STAT5 in Tregs
              ▼
   ┌──────────────────────────┐   FRAME 250935af…
   │  Claude Science runs it   │   → PTPN2 / type-I-IFN analysis
   │  on GSE278572 (real data) │   → figure + statistics table
   └──────────────────────────┘      (frame-02-ptpn2-experiment/)
```

---

## What’s in here

| Path | What it is |
|---|---|
| `frame-01-transbench-brief/` | The **golden brief** as Claude Science received it from TransBench. |
| &nbsp;&nbsp;`transbench_SLE_brief.md` / `.json` | Human-readable and structured brief: biological axes, the 3 hypotheses, chosen experiment, references. |
| &nbsp;&nbsp;`transbench_evidence.csv` / `transbench_references.csv` | The supporting evidence rows and the 41-reference list (38 with PMIDs). |
| &nbsp;&nbsp;`mcp_tool_result_1.txt` / `_2.txt` | The **raw MCP tool output** — the exact text `generate_experiment` streamed back into Claude Science. |
| `frame-02-ptpn2-experiment/` | Claude Science **executing** the proposed experiment. |
| &nbsp;&nbsp;`ANALYSIS_REPORT.md` | The methods, results table, interpretation, and caveats — written by the analysis run. |
| &nbsp;&nbsp;`figure_ptpn2_composite.png` | The main composite figure. Individual rendered images are in `figures/` (see `FIGURES.md`). |
| &nbsp;&nbsp;`results/` | Every results file: `statistics_table.csv` (19 FDR-corrected tests), per-test JSON, UMAP/metadata CSVs, co-expression tables. |
| &nbsp;&nbsp;`method/` | The extraction script (`extract.awk`) + log that stream-filtered the 3.85 GB matrix down to controls. |
| &nbsp;&nbsp;`inputs/` | The small filtered 10x trio + small raw GEO inputs (barcodes/features/protospacer calls). |
| &nbsp;&nbsp;`plan_ptpn2_ifn_analysis.json` | The plan artifact that bridges Leg 1 → Leg 2. |
| &nbsp;&nbsp;`mcp_tool_result.txt` | Tool output captured in this frame. |
| `provenance/` | Artifact manifests + the host-call tape recorded by Claude Science for this project. |
| `MANIFEST.md` | Every file with its **sha256** and size (integrity record). |
| `raw-data-pointers.md` | The **multi-GB raw inputs** (3.85 GB matrix, 1.45 GB `.h5ad`) — referenced by path + size + GEO accession, **not** copied. |
| `FIGURES.md` | Legend for the content-addressed figure images. |

---

## Key result (from `frame-02-ptpn2-experiment/results/statistics_table.csv`)

| Test | Result | FDR |
|---|---|---|
| **PTPN2**, Treg vs Teff (per-cell, Wilcoxon) | log2FC **+0.171** [95% CI 0.070, 0.280]; detected in 74.3% of Tregs vs 45.7% of Teffs | **9.8×10⁻¹²** |
| **PTPN2**, Treg vs Teff (lane pseudobulk, paired) | 7/8 lanes Treg > Teff | 0.037 |
| **PTPN2 ~ IFN score**, in **Tregs** (Spearman) | ρ **−0.035** [−0.061, −0.008] | 0.013 |
| **PTPN2 ~ IFN score**, in **Teffs** (negative control) | ρ −0.017 (n.s.) | 0.54 |

*Interpretation (from the report): PTPN2 — a known negative regulator of JAK-STAT / interferon signaling —
is expressed more, and far more frequently, in Tregs than in strictly-defined effector T-cells, and within
Tregs it correlates weakly but significantly **negatively** with the type-I-IFN response. Effect sizes are
small (expected for sparse single-cell data) but robust after FDR correction. The report is candid about
confounds (activation state, lane-level batch structure) — see its Caveats section.*

---

## How this maps back to the repo

- `frame-01-transbench-brief/transbench_SLE_brief.json` is **byte-identical** to the committed golden snapshot
  `snapshots/autoimmune_sle_treg_golden_brief.json` (same `request_echo`, same 3 hypotheses, same 41 references).
  That is the point of **golden mode**: TransBench serves this exact brief instantly and offline when the
  matching observation is pasted (see `scripts/record-golden.sh`), so a live demo never waits on the ~60–120 s pipeline.
- The brief’s chosen mechanism (H2: PTPN2-mediated STAT5 dephosphorylation in Tregs) is precisely what Frame 2 tested.

## Provenance

- **Claude Science project:** `proj_704392f92d90`
- **Frames:** `63e58999-d42e-469a-9862-fc747080adff` (brief) · `250935af-d80c-48cd-983b-f8ee3fb6dd58` (experiment)
- **TransBench run manifest:** `reuse_source = installed_iatronix`, `model_reasoning = claude-sonnet-4-6`,
  `model_cheap = claude-haiku-4-5`, `temperature = 0`, `max_hypotheses = 3`, `condition_anchor = systemic lupus erythematosus`.
- **Dataset:** GEO **GSE278572** — *Centralized control of dynamic gene regulatory circuits governs human
  T-cell rest and activation* (Perturb-CITE-seq, Marson lab); 36,807 features × 249,799 cells → 21,943 NTC controls.

## Integrity & reproduction

- Verify any file against `MANIFEST.md`: `sha256sum <file>` and compare.
- Re-run **Leg 1** offline: golden mode (`scripts/record-golden.sh`) replays this exact brief with no API key.
- Re-run **Leg 2**: fetch GSE278572 from GEO, then follow `frame-02-ptpn2-experiment/method/` and `ANALYSIS_REPORT.md`.

## Privacy note

The observation is **de-identified** (it matches the already-public golden snapshot; no PHI). `raw-data-pointers.md`
records **local absolute paths** that include a Claude Science org identifier — harmless, but if this folder is
ever pushed to the **public** repo, consider shortening those to `~/.claude-science/…` first. Secrets/PII sweep of
all copied files came back clean; run the `secret-scanner` agent before any commit as the working agreement requires.
