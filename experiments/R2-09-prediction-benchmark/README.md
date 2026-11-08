# R2-09 -- Prediction Benchmark (II-C prerequisite: R2-09B contract frozen v1.1)

**Phase:** II-C
**Depends on:** R2-08 (completed)
**R2-09B state:** `R2_09B_CONTRACT_FROZEN_WITH_RUNTIME_MATERIALITY_GATE` (contract/preregistration v1.1 hardened before R2-09A)
**R2-09A readiness:** `R2_09A_READY` (DEV-only, under frozen contract)

## Current state

- **R2-09B benchmark/evaluation contract:** frozen/amended to v1.1 in this directory. v1.0 digest preserved (`2f4c0859c6ce3f589142cd8b1c6cb3c3a47946f1b222a5a2e875495cccaa6f5d`); v1.1 digest in `contract.json`.
- **R2-09A causal baseline tournament:** **NOT STARTED** (may proceed DEV-only under frozen v1.1 contract).
- **R2-10/R2-11/R2-12:** **NOT STARTED**.
- **Held-out EVAL:** acquisition contract frozen; **no EVAL traces captured/opened/inspected**. EVAL **results** `EVAL_LOCKED`. Spec visibility: `EVAL_PROMPTS_VISIBLE_BUT_RESULTS_HELD_OUT` (tracked specs/hashes for provenance; **not fully blinded**; prompt-specific engineering prohibited).

**No R2-09 experiment result exists.** No prediction, retention, latency, throughput, or engineering-materiality claim should be inferred from this directory.

## v1.1 hardening (preregistration ambiguities resolved)

1. **EVAL scope:** 4 held-out workload *instances* (1 per nominal category: coding/reasoning/summarization/instruction), option A retained. Primary confirmatory claims limited to these 4 frozen instances; **family/category-level generalization unmeasured**.
2. **Visibility:** `EVAL_PROMPTS_VISIBLE_BUT_RESULTS_HELD_OUT` (not `EVAL_SPEC_HIDDEN`); EVAL results remain locked.
3. **B_selected aggregation:** `Score(B,M)=mean_i(miss_bytes(B,M,i)/decode_steps(i))`, equal weight per DEV instance, decode-step normalization, frozen tie-breaks; DEV corpus frozen in R2-09A manifest before selection.
4. **Primary recall cutoff:** `future_requested_expert_recall_at_k` at `k_tl` = router-requested cardinality from trace metadata/event (expected 8 for canonical Qwen3-30B-A3B); exactly `k_tl` output/cutoff; deterministic tie-break; anti-gaming fixed cardinality.
5. **Cross-layer causal boundary:** intrastep cross-layer causal context allowed (layers `< l` of same step `t`); target `E_{t,l}`, layers `> l` of same step, and future steps forbidden.
6. **Baseline semantics:** fully frozen for all candidate families (including 2Q byte partition/ghost/A1in/Am/A1out rules, LFU/LRU/LRU-K/aged tie-breaks/details); no implementation-spec deferral.

## Unchanged scientific constraints

CASE C (`O_B` not byte-optimal; `RecoveryOfBeladyGap` signed, negative/>1 allowed), EXP-025 P2 unsupported for arbitrary policy streams/no direct bytes→seconds, threshold `PENDING_POLICY_STREAM_CALIBRATION`, stochastic profile `UNRESOLVED`, primary budgets 1.5/4/8 GiB, EVAL results locked, DEV-only selection, LFU auxiliary DEV evidence referenced not rerun, R2-01..R2-08 evidence and II-A+II-B paper unchanged.

## Next execution order (frozen)

1. R2-09B contract freeze/hardening (**done, v1.1**)
2. R2-09A DEV causal-baseline tournament
3. Freeze `B_selected` / baseline configuration (DEV-only)
4. R2-10 DEV predictor benchmark and predictor freeze
5. Acquire/open held-out EVAL according to frozen contract (EVAL remains sealed/uninspected until method freeze)
6. Evaluate frozen predictor/baselines on EVAL
7. R2-11 predictor-driven retention
8. Performance-model calibration gate if required
9. R2-12 real runtime validation
