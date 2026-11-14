# R2-12 -- Real Runtime Validation

**Phase:** II-C
**Depends on:** R2-11 (R2_11_PASS, commit 0ccd36d)

## Scientific purpose

Validate the R2-11 predictive-retention result against real runtime
behavior, not only in replay: does `PredictedProtectPolicy` (the frozen
`decayed_transition`/decay=0.99 predictor) produce a measurable real
decode-throughput benefit relative to `B_selected(M)` on the target
consumer hardware, while preserving output equivalence and memory
correctness? No speedup is assumed.

## Current state

`R2_12_PROTOCOL_FROZEN`. The complete prospective runtime-validation
protocol is frozen (this directory's JSON artifacts + the
`argine.research2.r2_12` code module + tests). **No runtime measurement
has been performed. No result of any kind exists.**

## Why R2-12 exists

Solely because R2-11's frozen `r2_12_gate` (applied in R2-11's closure
commit `0ccd36d`) evaluated to "R2-12 MAY BE PROPOSED FOR PROSPECTIVE
RUNTIME VALIDATION" given the `R2_11_PASS` classification. R2-12 does not
reinterpret R2-10 (Category B), the historical R2-11-not-proposed
decision, R2-BRIDGE-01 (BRIDGE_POSITIVE), or R2-11 (`R2_11_PASS`) -- all
four are preserved exactly.

## Scope control

R2-12 is **not** a second retention-selection study: the predictor
(`decayed_transition`, decay=0.99) and policy (`PredictedProtectPolicy`)
are frozen inputs, never retuned, never tournamented, and never changed
based on runtime results.

## Frozen protocol components

See `contract.json` for the full component list. Highlights:

- **Runtime corpus**: 4 workloads (one per R2-11 category: coding,
  instruction, reasoning, summarization), selected by a fixed,
  outcome-independent position rule ("v1" of each category) -- see
  `runtime_corpus.json` for the explicit non-cherry-picking disclosure.
- **Budgets**: 4 GiB (primary), 1.5 GiB (stress), 8 GiB (null/control,
  per R2-11's exact retention tie at that budget).
- **Repetitions**: 5 paired reps per (workload, budget, policy) cell,
  plus 1 discarded warmup -- 120 counted + 24 warmup = 144 total runs.
- **Ordering**: ABBA paired blocks, opening policy alternated across
  cells to avoid systematically running one policy first.
- **Decision rule**: `R2_12_PASS` / `R2_12_INCONCLUSIVE` / `R2_12_FAIL` /
  `R2_12_BLOCKED_INVALID`, frozen in `decision_rules.json` before any
  result exists.

## State machine

```
R2_12_PROTOCOL_FROZEN (here)
  -> R2_12_RUNTIME_READY   (real-host pre-run gate passes; not yet reached)
  -> R2_12_RUNTIME_OPENED  (separate explicit commit, strictly pre-scoring)
  -> R2_12_RUNTIME_COMPLETE
  -> R2_12_EVALUATED_CLOSED
```

## Explicit statement

**No result exists yet for R2-12.** No scientific conclusion,
measurement, or claim should be inferred from the presence of this
directory or any file in it. Do not run runtime benchmarks, measure
tokens/s, or open `R2_12_RUNTIME_OPENED` based on this freeze alone --
that requires a separate, explicit authorization and a passing real-host
pre-run gate.
