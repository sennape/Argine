# Publication Provenance Manifest

Edition: **final-corrected-edition**, superseding commit `0348e04` editorially (R-12 host-condition wording; Paper I-length abstract; layout). No claim, experiment identifier or canonical artifact mapping changed.

Machine-readable form: `PAPER_II_PROVENANCE.json`. This file keeps its historical name for repository continuity; the manuscript it documents carries no numbered-paper framing. Reader-facing identifiers are R-01 ... R-12, R-09A/R-09B and R-BRIDGE-01; the canonical repository identifiers are unchanged and appear once in the mapping table.

## Publication-ID mapping

| Publication ID | Canonical repository ID |
|---|---|
| R-01 | R2-01 |
| R-02 | R2-02 |
| R-03 | R2-03 |
| R-04 | R2-04 |
| R-05 | R2-05 |
| R-06 | R2-06 |
| R-07 | R2-07 |
| R-08 | R2-08 |
| R-09A | R2-09A |
| R-09B | R2-09B |
| R-10 | R2-10 |
| R-BRIDGE-01 | R2-BRIDGE-01 |
| R-11 | R2-11 |
| R-12 | R2-12 |

## Claim provenance

| Claim | Table/Figure | Source artifact | Commit |
|---|---|---|---|
| DEV margin +0.0326 (Category B) | — | `experiments/R2-10-working-set-predictor/PHASE2-SELECTION-RECORD.json` | `9ac0aba` |
| EVAL recall 0.449817 / +12.04% / +28.71% | Table 13, Fig. 7 | `.../R2-10-working-set-predictor/PHASE3D-EVAL-RESULTS.json` | `e13d89f` |
| R-BRIDGE-01: -57784.861 bytes/step, BRIDGE_POSITIVE | Table 14, Fig. 8 | `.../R2-BRIDGE-01-retention-feasibility/BRIDGE_AGGREGATE_COMPARISON.json` | `58b36a1` |
| R-11 primary: -87476.85, CI [-154123.32,-27178.93], R2_11_PASS, 72/72 | Table 14, Fig. 9 | `.../R2-11-predictive-retention/PER-BUDGET-COMPARISON.json`, `BOOTSTRAP-RESULT.json` | `0ccd36d` |
| R-12 primary: -0.171721 TPS, R2_12_FAIL | Table 15, Fig. 10/11 | `.../R2-12-runtime-validation/R2_12_4GIB_PRIMARY_FORMAL_ANALYSIS.json` | `51420d3`, `3356845` |
| R-12 secondary 1.5/8 GiB | Table 15, Fig. 10/11 | `.../R2_12_1_5GIB_SECONDARY_FORMAL_ANALYSIS.json`, `R2_12_8GIB_SECONDARY_FORMAL_ANALYSIS.json` | `0bc3388`, `5e9d93d` |
| 144/144 launches, closure | Table 12 | `.../R2-12-runtime-validation/R2_12_FINAL_CLOSURE.json` | `3356845` |

## Hash registry (printed once, Appendix A.2)

- `H-TRACE-R01` — validated coding trace (R-01; reused by R-05 to R-07) — `e6c16f2bcdb4ef841770ff16ac404c91312297112bcd7e653438cc22b8d7b2f1`
- `H-R05-CANONICAL` — R-05 canonical digest — `4a1b72cdfcd37144446fc1a106b6fa8d559d10bc0729e1baf16dda7c1fa620df`
- `H-R06-CANONICAL` — R-06 canonical digest — `a727edf42735286420d326baad481687167d3d0aba0444a527183c084140926e`
- `H-R07-CANONICAL` — R-07 canonical digest — `ad36e615970b196b64ba3a3fdc2559e252cc8627e5d17d0d830d16742f59281a`
- `H-R08-CANONICAL` — R-08 canonical digest — `f5db4e0bf43ce4243edb303441e82ceefa5a0aeb5754143292f1230f220527c3`
- `H-MODEL-30B` — Qwen3-30B-A3B-Q4_K_M model artifact — `a015794bfb1d69cb03dbb86b185fb2b9b339f757df5f8f9dd9ebdab8f6ed5d32`
- `H-RUNTIME-R08` — R-08 capture runtime binary — `a0063720ec4bb5d1ba3e4f7c0387dd1bc6a239b0ce038ba3d755351dab3f5cf3`
- `H-R12-CONTRACT` — R-12 frozen Phase-0 contract bundle — `3c09433d4b81546f9ae9f2bbdf2217ebc82225e50c9a7545d5f706a0db4913ad`

The frozen contract digest cited throughout the R-12 material is `3c09433d4b81546f9ae9f2bbdf2217ebc82225e50c9a7545d5f706a0db4913ad` (H-R12-CONTRACT).
