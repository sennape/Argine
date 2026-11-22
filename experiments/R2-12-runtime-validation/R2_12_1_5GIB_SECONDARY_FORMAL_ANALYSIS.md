# R2-12 1.5 GiB Secondary (1.5gib-stress) -- Formal Analysis

Generated: 2026-11-15T22:56:52Z  |  Commit: 1c773229e7374340504df910248deed302f1c138

## Classification
**No R2_12_PASS/FAIL/INCONCLUSIVE label applies** -- decision_rules.json's primary_decision_rule is defined strictly in terms of the primary 4 GiB result; no secondary-budget classification scheme exists in the frozen contract. See `secondary_classification_determination` for the full reasoning.

## Official secondary statistics (frozen method, applied independently at this budget)
- PredictedProtectPolicy mean TPS: 2.252874
- B_selected mean TPS: 2.321705
- Aggregate (equal-weight mean of 4 per-workload mean deltas, PP - B): -0.068831
- 95% paired bootstrap CI: [-0.093256, -0.054608]
- Pair direction: PredictedProtect wins 2, B_selected wins 18, ties 0 (of 20)

## Per-workload mean delta_tps (PredictedProtect - B_selected)
- r2-11-coding-v1: -0.105590
- r2-11-instruction-v1: -0.060517
- r2-11-reasoning-v1: -0.052962
- r2-11-summarization-v1: -0.056255

## Output equivalence
4/4 cells matched exactly; comparison admissible: True

## Acquisition integrity summary
- Canonical records found: 48 / expected 48
- Warmup/counted: 8 / 40
- Counted per policy: {'b_selected': 20, 'predicted_protect': 20}
- No precanonical contamination: True
- Schedule order matches frozen plan: True

## Limitations
- prediction_overhead_seconds / retention_policy_overhead_seconds are None for every record, same as the 4 GiB primary analysis; not needed here since no PASS/FAIL overhead-dominance clause applies to a secondary budget.
- budget_violations is a constant 0 written by the driver, not an independently measured counter from runtime instrumentation.
- peak_resident_memory_bytes was checked only for gross sanity (range 2932686848-3362807808 bytes), not against the correct planner-derived allowance (requires the real model file, not reachable from this analysis environment).
- Runtime/model/store identity hashes are verified once at campaign start (host_preflight_gate), not independently re-verified per record in this analysis -- identical caveat to the 4 GiB primary analysis.

No model inference was performed by this analysis script.
