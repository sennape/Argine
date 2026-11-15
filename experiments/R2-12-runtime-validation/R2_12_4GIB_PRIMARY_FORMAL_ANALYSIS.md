# R2-12 4 GiB Primary -- Formal Analysis

Generated: 2026-11-15T16:20:29Z  |  Commit: fce0abbfdb5df50690c68116b6f14db75665b3d8

## Classification
**R2_12_FAIL** -- primary 4 GiB mean delta_tps <= 0 (directionally worse or no improvement): observed_mean_delta_tps=-0.171721. Per decision_rules.json.primary_decision_rule.R2_12_FAIL, this alone is sufficient; the separate overhead-dominance clause need not be evaluated.

## Official primary statistics
- PredictedProtectPolicy mean TPS: 3.332153
- B_selected mean TPS: 3.503874
- Primary aggregate (equal-weight mean of 4 per-workload mean deltas, PP - B): -0.171721
- 95% paired bootstrap CI: [-0.259917, -0.091642]
- Pair direction: PredictedProtect wins 2, B_selected wins 18, ties 0 (of 20)

## Per-workload mean delta_tps (PredictedProtect - B_selected)
- r2-11-coding-v1: -0.196170
- r2-11-instruction-v1: -0.307429
- r2-11-reasoning-v1: -0.117379
- r2-11-summarization-v1: -0.065906

## Output equivalence
4/4 cells matched exactly; primary performance comparison admissible: True

## Continuation rule (1.5 GiB / 8 GiB)
A_mandatory_preregistered_components -- see run_order.json.across_cell_ordering.middle_loop ("budget_gib, in order: 4.0 (primary), 1.5 (stress), 8.0 (null/control)") and decision_rules.json.budget_specific_interpretive_stance_frozen_before_execution

## Limitations
- prediction_overhead_seconds / retention_policy_overhead_seconds are None for every record: the CPU-bookkeeping overhead isolation instrumentation_contract.json describes for the R2_12_FAIL overhead-dominance clause was not performed; it was not needed for this classification since the primary delta_tps<=0 clause alone already determines R2_12_FAIL.
- budget_violations is a constant 0 written by the driver, not an independently measured counter from runtime instrumentation; treat 'zero budget violations' as unverified-by-direct-instrumentation rather than a measured guarantee.
- peak_resident_memory_bytes was checked only for gross sanity (range 5562257408-6034964480 bytes), not against the correct planner-derived allowance (requires the real model file, not reachable from this analysis environment).
- Runtime/model/store identity hashes are verified once at campaign start (host_preflight_gate), not independently re-verified per record in this analysis.
- HARDWARE-IDENTITY-STAMP.json does not exist; the hardware-identity gate check passed on the 'first real run creates it' branch, which this analysis did not independently confirm against a second stamped run.

1.5 GiB and 8 GiB were **not** run in this analysis step. No model inference was performed.
