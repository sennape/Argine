# R2-09 -- Prediction Benchmark (II-C prerequisite: R2-09B contract frozen)

**Phase:** II-C
**Depends on:** R2-08 (completed)
**R2-09B state:** `FROZEN_WITH_RUNTIME_MATERIALITY_GATE` (contract/preregistration only)

## Current state

- **R2-09B benchmark/evaluation contract:** frozen in this directory (machine-readable JSON + preregistration). See `PREREGISTRATION.md`, `contract.json`, `eval_manifest.json`, `baseline_space.json`, `prediction_contract.json`, `metrics_contract.json`, `decision_rules.json`.
- **R2-09A causal baseline tournament:** **NOT STARTED** (may proceed only under the frozen R2-09B contract; DEV-only selection).
- **R2-10/R2-11/R2-12:** **NOT STARTED**.
- **Held-out EVAL:** acquisition contract frozen; **no EVAL traces captured/opened/inspected**. `EVAL_LOCKED`.

**No R2-09 experiment result exists.** No prediction, retention, latency, throughput, or engineering-materiality claim should be inferred from this directory.

## What R2-09B freezes

DEV/EVAL firewall; EVAL acquisition/design contract (not execution); primary budgets (1.5/4/8 GiB); causal baseline families and hyperparameter search spaces; `B_selected` DEV-only rule; prediction target/horizons/causal features; prediction and retention metrics; deterministic condition (temp 0.0, top_k 1); stochastic status (`UNRESOLVED`, blocks stochastic robustness claims only); AUDIT-ORACLE-001 CASE C comparator semantics (`RecoveryOfBeladyGap` signed, may be negative/>1); EXP-025 applicability (P2, arbitrary policy-stream transferability unsupported, no direct bytes→seconds); practical-effect threshold `PENDING_POLICY_STREAM_CALIBRATION` (blocks R2-11 engineering-materiality claims, not R2-09A); falsification/leakage/immutability/visibility rules; primary `(M,W_class,deterministic)` hypotheses.

## Existing DEV evidence (do not rerun)

Canonical R2-03 DEV sweep (`d957077073374f1e9fd1e101a002946f`) already contains Global-LRU, LFU (`LFU_SWEEP_CANONICAL_AUXILIARY`), Runtime-Sharded-LRU, and `O_B` at 1.5/2/4/8/16 GiB. R2-09A begins from this evidence.

## Next execution order (frozen)

1. R2-09B contract freeze (**done by this contract**)
2. R2-09A DEV causal-baseline tournament
3. Freeze `B_selected` / baseline configuration (DEV-only)
4. R2-10 DEV predictor benchmark and predictor freeze
5. Acquire/open held-out EVAL according to frozen contract (EVAL remains sealed/uninspected until method freeze)
6. Evaluate frozen predictor/baselines on EVAL
7. R2-11 predictor-driven retention
8. Performance-model calibration gate if required
9. R2-12 real runtime validation
