# Raw-data pointers (referenced, NOT copied)

These inputs/intermediates are **multi-hundred-MB to multi-GB** and deliberately excluded from the repo
(git/GitHub are the wrong store for them; GitHub rejects >100 MB blobs). They live on the machine that ran
the golden test, under the Claude Science data dir. All derive from public GEO dataset **GSE278572**.

| logical file | on-disk path | size |
|---|---|---:|
| `GSE278572_matrix.mtx.gz` | `/root/.claude-science/orgs/67724343-31eb-4d19-be89-e7b265d05301/workspaces/250935af-d80c-48cd-983b-f8ee3fb6dd58/ntc10x/GSE278572_matrix.mtx.gz` | 3.6G |
| `va1a13e0b_GSE278572_matrix.mtx.gz` | `/root/.claude-science/orgs/67724343-31eb-4d19-be89-e7b265d05301/artifacts/proj_704392f92d90/f0e620e7-7786-4b73-9fe3-a1f33fcee6a3/va1a13e0b_GSE278572_matrix.mtx.gz` | 3.6G |
| `vcb618bd9_GSE278572_matrix.mtx.gz` | `/root/.claude-science/orgs/67724343-31eb-4d19-be89-e7b265d05301/artifacts/proj_704392f92d90/f0e620e7-7786-4b73-9fe3-a1f33fcee6a3/vcb618bd9_GSE278572_matrix.mtx.gz` | 3.6G |
| `v149e1d70_ntc_annotated.h5ad` | `/root/.claude-science/orgs/67724343-31eb-4d19-be89-e7b265d05301/artifacts/proj_704392f92d90/4cf191df-4fd7-406d-8a0a-e0883e33b696/v149e1d70_ntc_annotated.h5ad` | 1.4G |
| `vaf9d857e_ntc_annotated.h5ad` | `/root/.claude-science/orgs/67724343-31eb-4d19-be89-e7b265d05301/artifacts/proj_704392f92d90/5deae653-4752-46a8-94a8-62a882f51be5/vaf9d857e_ntc_annotated.h5ad` | 1.4G |
| `v7840584c_matrix.mtx.gz` | `/root/.claude-science/orgs/67724343-31eb-4d19-be89-e7b265d05301/artifacts/proj_704392f92d90/4cf191df-4fd7-406d-8a0a-e0883e33b696/v7840584c_matrix.mtx.gz` | 271M |
| `vc2d8ba60_matrix.mtx.gz` | `/root/.claude-science/orgs/67724343-31eb-4d19-be89-e7b265d05301/artifacts/proj_704392f92d90/4cf191df-4fd7-406d-8a0a-e0883e33b696/vc2d8ba60_matrix.mtx.gz` | 271M |

**How to re-obtain:** the full expression matrix, barcodes, features, and protospacer calls are public at
NCBI GEO accession GSE278572 (*Centralized control of dynamic gene regulatory circuits governs human T cell
rest and activation*, Perturb-CITE-seq, Marson lab). The `ntc_annotated.h5ad` is the analysis's own
intermediate (NTC-filtered, annotated AnnData) — regenerate it by re-running the pipeline in
`frame-02-ptpn2-experiment/method/` against the GEO inputs.
