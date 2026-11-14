# R2-11 -- Prediction-Driven Retention (Prospective Confirmatory Protocol)

**Phase:** II-C
**Depends on:** R2-10, R2-BRIDGE-01

## Scientific purpose

Confirm, on a larger and entirely fresh 12-workload corpus, whether the
already-selected causal prediction-driven retention mechanism
(PredictedProtectPolicy, frozen decayed_transition predictor, decay=0.99)
produces a robust reduction in expert-memory miss traffic relative to the
frozen strong causal retention baseline B_selected(M). This is a retention
experiment, not a runtime-speed experiment.

## Current state

`R2_11_PROTOCOL_FROZEN` / `R2_11_ACQUISITION_PLANNED` (captured: False,
evaluated: False). Phase-0 contract machinery, all 12 workload
specifications, the freshness audit, the acquisition plan, and the
complete prospective decision rule (including the paired bootstrap
procedure and the R2-12 gate) are frozen in this directory. **No R2-11
trace has been captured. No R2-11 result exists.**

## Why R2-11 exists now

R2-10's own closure (`experiments/R2-10-working-set-predictor/PHASE3L-FINAL-CLOSURE-RECORD.json`)
recorded the historical decision **"R2-11 NOT PROPOSED"** under the original
frozen R2-10 rules. That historical statement is unchanged and is not
rewritten by this directory's existence. R2-11 exists only because a
*separate*, later, prospective protocol -- R2-BRIDGE-01 -- produced a
**BRIDGE_POSITIVE** classification on fresh held-out evidence
(`experiments/R2-BRIDGE-01-retention-feasibility/BRIDGE_EVALUATED_CLOSED.json`),
whose own frozen decision rule states: *"R2-11 MAY NOW BE PROPOSED UNDER
NEW PROSPECTIVE BRIDGE EVIDENCE."* See `provenance.json` for the full chain.

## History of this directory

This directory originally held only a `PLANNED` stub (created by commit
`77544a2`, R2-M0.1 infrastructure bootstrap) stating "No result exists
yet for R2-11." That stub contained no result and made no scientific
claim. It has been evolved in this Phase-0 freeze into the full
prospective confirmatory protocol described above; its original
provenance is preserved in `provenance.json`.

## Inputs

12 fresh confirmatory EVAL workloads (3 coding, 3 reasoning, 3
summarization, 3 instruction-following) under
`research2/workloads/r2_11/`, independently freshness-audited against
R2-10 DEV, R2-10 EVAL, and R2-BRIDGE-01's holdout corpus (zero
violations; see `freshness_audit.json`).

## Planned outputs (future phases, not performed yet)

- R2-11 captured traces (12 workloads, sealed)
- R2_11_EVAL_OPENED provenance record (separate commit, before scoring)
- Held-out confirmatory evaluation results (72 primary cells: 12 x 3
  budgets x 2 policies)
- Paired-bootstrap confidence interval and final
  R2_11_PASS / R2_11_INCONCLUSIVE / R2_11_FAIL / R2_11_BLOCKED_INVALID
  classification
- R2-12 gate decision (prospective only; R2-12 is never started
  automatically)

## Explicit statement

**No result exists yet for R2-11.** This Phase-0 freeze designs, audits,
and implements the contract machinery, authors and freezes the fresh
workload corpus and acquisition plan, and freezes the complete
prospective confirmatory protocol. It does not execute R2-11, capture
the new corpus, or compute any retention result. No scientific
conclusion, measurement, or claim should be inferred from the presence
of this directory beyond what `contract.json`, `decision_rules.json`,
and `provenance.json` themselves state.
