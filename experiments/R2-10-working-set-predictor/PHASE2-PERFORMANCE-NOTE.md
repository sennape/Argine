# R2-10 Phase 2 — performance note (implementation-only, non-scientific)

This note documents an internal performance fix made to
`argine/research2/r2_10/predictors.py` and `runner.py` during Phase 2
Step 3 (full DEV tournament execution). It changes no frozen contract
file, no scientific semantics, and no result.

## What was found

Initial per-cell benchmarking against the real canonical DEV trace (381
decode steps, 48 layers, 128 experts) showed:

- `markov_transition`: ~12s/cell (dense `n_experts x n_experts` matrix
  allocated and rebuilt from scratch on every `predict()` call).
- `decayed_transition`: did not complete even ONE config within a 170s
  budget (the per-step full-matrix decay rescale made this quadratically
  worse than `markov_transition`).

At this cost, the full 128-cell / 16-config-per-cell tournament (with 4
of the 8 DEV cells at 511 decode steps rather than 381) was not feasible
within the available tool-call time budget.

## Root cause

Both `MarkovTransitionPredictor._transition_counts` and
`DecayedTransitionPredictor._decayed_transition_counts` recomputed the
*entire* transition history from scratch on every single `predict()`
call, giving O(history_length^2) total work per layer walk, compounded
in the decayed case by an O(n_experts^2) full-matrix rescale at every
step.

## Fix

1. Replaced the dense `n_experts x n_experts` matrix with a sparse dict
   keyed by `(c, e)` (at most `top_k^2` cells touched per step, vs.
   `n_experts^2` allocated).
2. Added an **incremental cache** to both predictor classes: when
   `predict()` is called in the same strictly-increasing-position order
   the runner always uses (one new history entry per call, within one
   layer's walk), only the ONE new transition is folded in — O(top_k^2)
   per call instead of O(i * top_k^2). A call that is NOT a valid
   continuation (non-monotonic history, as in the Phase-1 unit tests, or
   the first call of a fresh walk/layer) safely falls back to an exact
   full from-scratch recompute — **correctness never depends on call
   order, only speed does.**
3. For `decayed_transition`, decay is applied *lazily*, only to cells
   that are actually touched or read, using the algebraic identity
   `decay_factor**a * decay_factor**b == decay_factor**(a+b)` (constant
   per-step decay telescopes), instead of rescaling every cell of a dense
   matrix at every step.
4. `RecencyFrequencyTransitionBlendPredictor` benefits transparently
   since it reuses the same `MarkovTransitionPredictor._transition_counts`
   instance and call pattern.
5. `runner.run_checkpointed` gained an optional cooperative
   `deadline_monotonic` parameter: it checks the clock only at cell
   boundaries (never mid-cell, so every write stays atomic) and, once
   past the deadline, stops cleanly and persists a `paused_for_deadline`
   manifest event instead of being hard-killed mid-cell by the external
   tool-call timeout. A call with no deadline behaves exactly as before.

## Correctness verification

- Differential test against the original dense implementations on
  synthetic histories: max absolute difference `0.0` (markov) and
  `~1e-15` (decayed, floating-point roundoff only) for both monotonic
  (incremental fast path) and non-monotonic (fallback path) access
  patterns.
- All 61 Phase-1 tests (`test_r2_10_contract.py`, `test_r2_10_leakage.py`,
  `test_r2_10_predictors.py`, `test_r2_10_runner_smoke.py`) pass
  unchanged before and after the optimization.
- The 7 DEV cells already checkpointed under the OLD dense implementation
  (before this fix) were not recomputed or invalidated — they remain
  valid evidence, since the two implementations are verified
  mathematically equivalent.

## Result

Real-trace per-cell cost for the 10 "expensive" configs
(`markov_transition` x1, `decayed_transition` x3,
`recency_frequency_transition_blend` x6) dropped from
12s-to-non-terminating to ~2.6s-4.8s each. The full 128-cell tournament
completed in under 15 minutes of actual compute, chunked across 7
`device_bash` calls using the existing checkpoint/resume architecture.

This file is intentionally outside `r2_10.contract.COMPONENT_FILES` and
is not part of any frozen digest.
