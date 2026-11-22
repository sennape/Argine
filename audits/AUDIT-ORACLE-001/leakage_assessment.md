# DEV/EVAL and Leakage Assessment (AUDIT-ORACLE-001)

## Scope

Documented role and actual usage audit for Research2-relevant traces/workloads (R2-01, R2-08) and EXP-007 context. Based on versioned provenance/manifests/READMEs/freeze manifests. No retroactive role assignment.

## Documented role (section 23)

| Artifact | documented_role | role_source |
|---|---|---|
| R2-01 canonical trace (coding, seed 42) | DEV | R2-01 capture manifest `capture_protocol.split=dev`; R2-08 `workload_manifest.json` coding-v1 `split=dev` |
| R2-08 coding-v1/seed-43, reasoning-v1 seeds 42/43, summarization-v1 seeds 42/43, instruction-v1 seeds 42/43 | DEV | R2-08 `workload_manifest.json` entries `split=dev`; capture matrix/workload metrics |
| EXP-007 cells | DEV and EVAL by cell as documented | `experiments/EXP-007-router-characterization/README.md` (explicit DEV/EVAL splits; in-sample caveats noted) |

No Research2 II-B canonical cell is explicitly documented as EVAL in the inspected R2-08 provenance.

## Actual usage (section 24)

- **R2-01 trace**: used for R2-01 validation, R2-02/03/04 replay comparisons (oracle/baseline measurement, not predictor tuning), R2-05/06/07 descriptive characterization, and reused as R2-08 coding-v1/seed-42 characterization input. Reported in II-A/II-B.
- **R2-02/03/04**: computed retention/replay comparisons on the R2-01 trace; no predictor, no hyperparameter tuning for prediction, no model selection.
- **R2-05/06/07**: descriptive characterization; no predictor training/tuning.
- **R2-08**: descriptive characterization (temporal/replicate/cross-workload variability) across 8 valid cells; no predictor, no baseline tournament, no threshold selection for II-C.
- **EXP-007**: contains DEV/EVAL design and in-sample analyses/ablations as documented by EXP-007 itself; Research2 II-C has not used EXP-007 for predictor tuning.

## Leakage matrix

See `dev_eval_inventory.csv`. Summary:
- Research2 R2-01/R2-08 artifacts used for characterization/reporting: `leakage_status=NOT_APPLICABLE` with respect to II-C predictor/baseline-tournament tuning (II-C not started).
- No `CONFIRMED_LEAKAGE` or `POTENTIAL_LEAKAGE` identified for II-C by this audit, because no II-C tuning/selection/evaluation loop exists yet.
- EVAL evidence availability for future Research2 II-C is **not established** by existing R2-08 dev-split captures alone; future R2-09B must define and freeze DEV/EVAL roles and eligibility before selection/reporting.

## Future leakage-safe contract (R2-09/R2-10)

Before R2-09A/R2-09B/R2-10:
- define/preregister DEV and EVAL sets from versioned provenance; do not retroactively label unclassified/dev-only artifacts as held-out EVAL merely for convenience;
- perform policy-family tuning, aging decay selection, LRU-K K selection, predictor windows/transition parameters/architecture selection on DEV only;
- keep EVAL untouched until selection is frozen; do not retune on EVAL and report as held-out;
- distinguish `BEST_ON_DEV` from `ORACLE_OVER_BASELINES_ON_EVAL` (hindsight diagnostic only, not deployable selected baseline).
- maintain `(M, W)` primary reporting granularity (budget × workload family/held-out evaluation workload); aggregates secondary.
