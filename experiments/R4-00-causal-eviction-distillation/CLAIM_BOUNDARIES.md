# Claim boundaries — Research 4 Phase 0 (CFDC)

## What Phase 0 establishes
- A frozen protocol: splits, feature vocabulary, teacher, two model definitions, comparators, endpoint, thresholds, classification order and anti-rescue rule.
- That the requested VALIDATION corpus (DEV seed-43) is a near-duplicate of TRAIN under greedy decoding, and the replacement split (R2-11 corpus) with its prior exposure.

## What Phase 0 does not establish
- Any Research 4 outcome. No replay, label extraction, training or scoring was run; EVAL and VALIDATION trace files were not read.
- Anything about runtime value. Even a later `R4_CFDC_CHEAP_VIABLE` would authorize only a standalone cost microbenchmark.

## Scope of any later Research 4 claim
- One model (Qwen3-30B-A3B-Q4_K_M routing traces), one budget (1.5 GiB), demand admission, event-atomic multi-key events, greedy decoding, the frozen splits (4 TRAIN, 12 VALIDATION, 4 EVAL traces).
- EVAL has 4 workloads (one per category): no family-level generalization and no inferential test; the 25% and +3 pp thresholds are applied to point estimates.
- Capture is relative to the frozen BeladyGlobal comparator and the b_selected reference; Belady-style is not proven optimal for multi-key, two-size events, so capture can exceed 1 in principle.
- ms/token values use the Research 3 association κ_low, measured on another stream; they are secondary and not a causal per-byte law.

## Research 3
Unchanged: `R3_CLOSED_NEGATIVE_PREDICTIVE_RETENTION_NOT_VIABLE_IN_TESTED_REGIME`. NUE_X's 8.79% is a Research 3 value on the Research 3 canonical stream; Research 4 re-scores NUE_X on its own splits and does not reinterpret that result.

## Prior exposure of the splits
- TRAIN (DEV seed-42): used for R2-09A/R2-10 development and for Research 3 NUE selection.
- VALIDATION (R2-11): opened for b_selected and predicted_protect miss bytes; `r2-11-coding-v1` has the same prompt as the Research 3 canonical stream, on which NUE_X was scored.
- EVAL (R2-09 EVAL): opened only for R2-10 predictor recall@k. Under the R2-09 contract's own rule, a post-EVAL method change makes an opened EVAL "observed" for a new method generation; Research 4 treats it as sealed with respect to eviction outcomes, which have never been computed on it, and states this limitation.
