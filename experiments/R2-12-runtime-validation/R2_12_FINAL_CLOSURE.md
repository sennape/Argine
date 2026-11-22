# R2-12 FINAL CLOSURE

**Primary classification:** `R2_12_FAIL`

**Reason:** primary 4 GiB mean delta_tps <= 0 (directionally worse or no improvement): observed_mean_delta_tps=-0.171721. Per decision_rules.json.primary_decision_rule.R2_12_FAIL, this alone is sufficient; the separate overhead-dominance clause need not be evaluated.


## Secondary budgets (descriptive only, do not replace primary)

{
  "1_5gib_stress": {
    "role": "descriptive stress-condition evidence only",
    "no_R2_12_classification_label_assigned": true,
    "observed_mean_delta_tps": -0.06883093594753013
  },
  "8gib_null_control": {
    "role": "descriptive null/control condition only",
    "no_R2_12_classification_label_assigned": true,
    "observed_mean_delta_tps": -0.4373259353774341,
    "retention_traffic_evidence_available": false,
    "overhead_attribution_status": "HYPOTHESIS ONLY -- expert_bytes_read/expert_read_count/expert_miss_bytes and expert_io_best_effort sub-fields are all None in the real 8 GiB canonical records for both policies, so no retention-traffic evidence exists to support attributing the observed 8 GiB delta to non-retention (e.g. prediction-policy bookkeeping) overhead. That explanation remains an unverified hypothesis, not a finding."
  },
  "statement": "Per decision_rules.json.budget_specific_interpretive_stance_frozen_before_execution, neither the 1.5 GiB stress budget nor the 8 GiB null-control budget is used, or is permitted to be used, to override, replace, or re-derive the R2_12_FAIL primary classification above, regardless of their own effect sizes or direction."
}

## Cross-phase evidence distinctions


### R2_10_working_set_predictor
- Finding: POSITIVE (prediction-quality signal only)
- Detail: DEV-tournament decayed_transition predictor measurably and consistently exceeded both trivial baselines on recall@k (+0.0326 absolute, ~8% relative, present across all DEV cells and workloads), classified Category B (MEASURABLE_IMPROVEMENT_INSUFFICIENT_FOR_RETENTION). SUPPORTED, not promoted, by held-out EVAL evidence.
- Scope: Prediction quality only -- no retention or runtime claim was made or implied by R2-10.
- Source: `experiments/R2-10-working-set-predictor/PHASE2-SELECTION-RECORD.json, PHASE3L-FINAL-CLOSURE-RECORD.json`


### R2_BRIDGE_01_retention_feasibility
- Finding: POSITIVE (BRIDGE_POSITIVE)
- Detail: Small but non-zero, direction-consistent retention-proxy effect across workloads and budgets, comparable in order of magnitude to the ~0.032486% relative DEV-level selection effect. No runtime/latency/speedup claim made; no cross-model/cross-vendor generalization claimed.
- Scope: Justified PROPOSING a new, separate R2-11 protocol prospectively. Did not retroactively alter R2-10's Category B or any other historical statement.
- Source: `experiments/R2-BRIDGE-01-retention-feasibility/BRIDGE_FINAL_INTERPRETATION.json`


### R2_11_predictive_retention
- Finding: POSITIVE (R2_11_PASS)
- Detail: At the primary 4 GiB budget, PredictedProtectPolicy reduced miss_bytes_per_decode_step by a mean of 87,476.85 bytes/decode-step vs B_selected(M) across 12 fresh workloads, 95% paired bootstrap CI [-154123.32, -27178.93] entirely below zero. Budget-dependent: larger magnitude at 1.5 GiB, an exact tie (delta=0.0) at 8 GiB.
- Scope: Retention evidence only -- miss_bytes_per_decode_step is a memory-access-pattern metric, not a runtime/latency/throughput measurement. No tokens/s, latency, or speedup claim was made or implied by R2-11.
- Source: `experiments/R2-11-predictive-retention/FINAL-INTERPRETATION.json`


### R2_12_real_runtime_validation
- Finding: NEGATIVE (R2_12_FAIL) -- real-runtime decode throughput did NOT improve
- Detail: At the primary 4 GiB budget, observed mean delta_tps = -0.171721 (PredictedProtect minus B_selected), directionally worse or no improvement. Per decision_rules.json, this alone is sufficient for R2_12_FAIL.
- Scope: Real-runtime decode throughput (tokens/second) only.
- Source: `experiments/R2-12-runtime-validation/R2_12_4GIB_PRIMARY_FORMAL_ANALYSIS.json`


## Required explicit statements

- R2-10 predictor-quality evidence is POSITIVE and is NOT invalidated, weakened, retracted, or reclassified by the R2-12 runtime result. R2-10 never claimed a runtime effect; R2-12's negative runtime result cannot invalidate a claim R2-10 did not make.

- R2-BRIDGE-01 and R2-11 replay/retention-proxy evidence (miss_bytes_per_decode_step) are POSITIVE and are NOT invalidated, weakened, retracted, or reclassified by the R2-12 runtime result, for the same reason: they measured retention/memory-access behavior under replay, not real end-to-end decode throughput.

- R2-12 real-runtime throughput DID NOT IMPROVE at the frozen primary 4 GiB budget. The primary R2-12 result is NEGATIVE (R2_12_FAIL). No runtime speedup is claimed anywhere in this closure or in any R2-12 artifact.

- No predictor-quality or retention result from R2-10, R2-BRIDGE-01, or R2-11 is invalidated merely because real-runtime throughput failed at R2-12. Prediction quality, replay-level retention improvement, and real-runtime decode throughput are three distinct, separately-scoped measurements; a negative result on the third does not imply a negative result on the first two, and this closure does not claim otherwise.

- Causal attribution of the R2-12 runtime deficit does not exceed available evidence. In particular, at the 8 GiB null-control budget, no retention-traffic telemetry exists in the real canonical records (expert_bytes_read / expert_read_count / expert_miss_bytes and the expert_io_best_effort sub-fields are all None for both policies), so any non-retention-overhead explanation for the observed 8 GiB delta remains an explicitly unverified HYPOTHESIS, not a finding, and is reported as such.


## Scientific conclusion

R2-12 FINAL STATUS: R2_12_FAIL. The frozen primary 4 GiB real-runtime decode-throughput endpoint shows PredictedProtectPolicy did not improve, and directionally underperformed, B_selected(M) (observed mean delta_tps = -0.171721, 95% CI entirely at or below zero per the already-committed primary analysis). This negative runtime result stands on its own and does not retroactively alter the positive, differently-scoped findings of R2-10 (prediction quality), R2-BRIDGE-01 (retention-proxy feasibility), or R2-11 (replay-level retention improvement). No runtime speedup is claimed. Any explanation of the runtime deficit beyond what the 4 GiB primary data directly shows -- including any non-retention-overhead account of the 8 GiB null-control result -- remains hypothesis, not established finding, for lack of retention-traffic telemetry in the real acquisition. Research 3 is explicitly not started by this closure.


## Campaign totals

{
  "physical_canonical_launches": 144,
  "expected_physical_canonical_launches": 144,
  "per_budget": {
    "4.0": 48,
    "8.0": 48,
    "1.5": 48
  }
}