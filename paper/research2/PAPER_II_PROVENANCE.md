# Paper II Provenance Manifest (Research II-C claims)

Machine-readable form: `PAPER_II_PROVENANCE.json`. Every quantitative claim added to `paper/research2/manuscript.tex` for Research II-C traces to a committed artifact and commit SHA below. II-A/II-B provenance is governed separately by `paper/research2/claims/claim_ledger.md`.

| Claim | Table/Figure | Source artifact | Commit |
|---|---|---|---|
| DEV margin +0.0326 (Category B) | — | `experiments/R2-10-working-set-predictor/PHASE2-SELECTION-RECORD.json` | `9ac0aba` |
| EVAL recall 0.449817 / +12.04% / +28.71% | Table c2, Fig c1 | `.../R2-10-working-set-predictor/PHASE3D-EVAL-RESULTS.json` | `e13d89f` |
| R2-BRIDGE-01: -57784.861 bytes/step, BRIDGE_POSITIVE | Table c3, Fig c2 | `.../R2-BRIDGE-01-retention-feasibility/BRIDGE_AGGREGATE_COMPARISON.json` | `58b36a1` |
| R2-11 primary: -87476.85, CI, R2_11_PASS, 72/72 | Table c3, Fig c3 | `.../R2-11-predictive-retention/PER-BUDGET-COMPARISON.json`, `BOOTSTRAP-RESULT.json` | `0ccd36d` |
| R2-12 primary: -0.171721 TPS, R2_12_FAIL | Table c4, Fig c4/c5 | `.../R2-12-runtime-validation/R2_12_4GIB_PRIMARY_FORMAL_ANALYSIS.json` | `51420d3`, `3356845` |
| R2-12 secondary 1.5/8 GiB | Table c4, Fig c4/c5 | `.../R2_12_1_5GIB_SECONDARY_FORMAL_ANALYSIS.json`, `R2_12_8GIB_SECONDARY_FORMAL_ANALYSIS.json` | `0bc3388`, `5e9d93d` |
| 144/144 launches, closure | Table c1 | `.../R2-12-runtime-validation/R2_12_FINAL_CLOSURE.json` | `3356845` |

Frozen contract digest cited throughout: `3c09433d4b81546f9ae9f2bbdf2217ebc82225e50c9a7545d5f706a0db4913ad`.
