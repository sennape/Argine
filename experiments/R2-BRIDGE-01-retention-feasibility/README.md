# R2-BRIDGE-01 -- Retention Feasibility Bridge

A new, separate, prospective protocol created **after** R2-10's formal
closure (commit `e13d89f`, branch `r2-main`). It does not reopen,
reinterpret, or modify R2-10.

## Why this exists

R2-10's held-out EVAL established (Category B, SUPPORTED by held-out
evidence, not promoted to Category C) that the frozen causal predictor
`decayed_transition/decay=0.99` has a measurable future-expert-recall
advantage over trivial baselines. That advantage was never evaluated as a
*retention* signal -- R2-10 only ever scored prediction quality
(recall@k against the next routing event), never whether using the
prediction to decide what stays resident actually reduces `miss_bytes`
against a real eviction policy.

R2-BRIDGE-01 asks exactly that bridging question:

> Does using the frozen causal predictor `decayed_transition/decay=0.99`
> as an online retention signal reduce expert-memory traffic relative to
> the frozen strong causal baseline `B_selected(M)`?

This is a **replay-level feasibility question**, not a runtime-speed
question.

## Status: Phase 0 only

This directory currently holds the **frozen Phase-0 contract** plus the
retention-mechanism implementation and its correctness tests. It does
**not** contain any BRIDGE holdout trace, any BRIDGE result, or any
tournament/execution output. See `contract.json` for the exact
in-scope / out-of-scope boundary.

## What stays true regardless of this bridge's outcome

- R2-10's DEV classification remains **Category B**.
- The original frozen R2-10 decision rules produced **R2-11: NOT
  PROPOSED**. That historical statement is never rewritten by this
  bridge, whatever this bridge eventually finds.
- A positive bridge result may justify *prospectively proposing* a new,
  separate R2-11 protocol -- it cannot retroactively satisfy the original
  one.

## Files

- `contract.json` -- top-level Phase-0 contract (scientific question,
  predictor freeze, baseline binding, budgets, scope).
- `retention_policy_space.json` -- the two mechanism families
  (`predicted_protect`, `predictor_retention_score`) and their finite,
  intentionally tiny grid (12 mechanism-config cells total).
- `decision_rules.json` -- primary/secondary metrics and the frozen
  `BRIDGE_NEGATIVE` / `BRIDGE_POSITIVE_BUT_WEAK` / `BRIDGE_POSITIVE`
  decision gate, defined before any result exists.
- `corpus_acquisition_contract.json` -- the new, not-yet-captured BRIDGE
  holdout corpus specification (4 instances: coding, reasoning,
  summarization, instruction). Explicitly forbids reusing the R2-10 EVAL
  set.
- `dev_holdout_separation.json` -- what existing R2-10 DEV traces may and
  may not be used for during mechanism development.
- `leakage_contract.json` -- the causality, memory-correctness,
  determinism, and control-equivalence invariants and their tests.
- `provenance.json` -- repository-state reconstruction performed before
  this contract was authored.
- `audit.json` -- Phase-0 self-audit (frozen-digest re-verification,
  scope compliance).
- `PHASE0-FREEZE.json` -- the freeze record and contract digest.
- Code: `argine/research2/r2_bridge01/` (`retention_policies.py`,
  `contract.py`).
- Tests: `tests/test_r2_bridge01_leakage.py`,
  `tests/test_r2_bridge01_memory_correctness.py`,
  `tests/test_r2_bridge01_control_equivalence.py`,
  `tests/test_r2_bridge01_contract.py`.
