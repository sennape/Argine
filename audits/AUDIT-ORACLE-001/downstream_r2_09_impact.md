# Downstream Impact on R2-09 (AUDIT-ORACLE-001)

## Final verdict context

**BLOCKED_FOR_SCIENTIFIC_AMENDMENT** (CASE C). Implemented `Oracle-Global-Belady` (`BeladyGlobal`, `O_B`) is a clairvoyant furthest-next-use comparator under variable object sizes; miss-byte optimality is not established. `O* <= O_B` holds, but `O_B` and `C*` have no guaranteed ordering.

## Meanings

| Quantity | Meaning under CASE C |
|---|---|
| `B_best - O_B` | Measured miss-byte gap between selected causal baseline and implemented clairvoyant comparator `O_B`. It is **not** a proven upper or lower bound on true maximum causal recovery `B_best - C*`; it is not the proven gap to true offline optimum except through `B_best - O_B <= B_best - O*`. |
| `Recovery_vs_best_causal = (B_best - P) / (B_best - O_B)` | **Comparator-relative recovery of the O_B gap** only, unless `O_B = O*` is later established. Must remain signed (no clipping when `P > B_best`). `> 1` is mathematically possible if `P` beats `O_B`; that means `P` beat the comparator, **not** that `P` beat `O*`. Do not call "fraction of maximum recoverable headroom" unless the denominator is justified as the required optimum/bound. |
| "Oracle headroom" | **Not valid as written** for the miss-byte bound claim. Use comparator-specific terminology (e.g., Belady/comparator gap, clairvoyant-comparator gap) until/unless a valid bound is established. |
| Practical-effect threshold derivation | May proceed **only after** scientific semantics/amendment define the metric denominator/interpretation and EXP-025 applicability is handled (P2, policy-stream transferability UNSUPPORTED without validation). No numeric threshold is preregistered by this audit. |
| R2-09 start | **BLOCKED**. R2-09 may not start until paper/scientific semantics are amended and the evaluation contract is aligned with the audited comparator/objective semantics. |

## Prerequisites before R2-09

1. **Scientific/paper amendment** (not done in this audit): correct title/abstract/definitions/R2-02/03/04/discussion/bridge/II-B synthesis/II-C placeholder/conclusion/claim ledger/freeze provenance semantics per `claim_impact_map.md`; project owner makes final editorial title choice.
2. **Evaluation contract clarification**: define `O_B` as comparator unless `O*` is established/bounded; freeze recovery-metric naming/semantics (`RecoveryOfBeladyGap` or justified alternative) and signed behavior.
3. **EXP-025 applicability decision**: do not assume arbitrary policy-induced miss-stream validity; use `DeltaT_model = PerformanceModel(MissStream_B_best) - PerformanceModel(MissStream_P)` only with justified/validated scope or explicit approximation labeling.
4. **DEV/EVAL contract**: preregister held-out EVAL roles/eligibility; no retuning on EVAL; `BEST_ON_DEV` vs `ORACLE_OVER_BASELINES_ON_EVAL` separated.
5. **R2-09B preregistration**: freeze DEV/EVAL, budgets (1.5/4/8 GiB candidates only until frozen), baseline selection, signal families, deterministic/stochastic conditions, metrics, comparator semantics, EXP-025 applicability, practical-effect threshold derivation, falsification criteria.
6. **Optional but valuable**: investigate `O*` exact/bounded feasibility if a true byte-optimal bound becomes necessary (`POSSIBLE_BUT_EXPENSIVE` per this audit). Do not silently replace historical `O_B`.

## R2-09/10/11/12 boundary (preserved)

R2-09 Prediction Benchmark; R2-09A Causal Baseline Tournament; R2-09B Benchmark Contract/preregistration; R2-10 Causal Working-Set Predictor; R2-11 Prediction-Driven Retention; R2-12 Real Runtime Validation. R2-09 contains no final R2-10 predictor result.
