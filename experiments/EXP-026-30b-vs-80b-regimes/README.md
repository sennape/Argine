# EXP-026 — Two experimental regimes: 30B-A3B vs Next-80B-A3B

| field | value |
|---|---|
| ID | `EXP-026-30b-vs-80b-regimes` |
| Status | **PLANNED** |
| Period | — |
| Origin | Argine2 |
| Related questions | RQ-002, RQ-009 |
| Supersedes / superseded | — |

## Research question

Why does the 80B behave differently from the 30B (requirement section 56, question 10)?

## Hypothesis

[HYPOTHESIS] Competing explanations, to be discriminated: (a) routing concentration (smaller EWS fraction); (b) smaller slices (440-704 KiB vs 930 KiB) raise queue depth per byte; (c) the 80B is bookkeeping-bound (commit/decommit/lock 56% of its I/O bucket) rather than read-bound.

## Setup

Same workloads and budgets on both models; characterization + I/O timeline + node timing + replay.

## Commands

```bat
argine research trace ... on both models
argine research analyze ...
```

## Raw data

Pending.

## Result

No result yet.

## Interpretation

—

## Limitations

The 80B uses Gated DeltaNet attention in 36 of 48 layers: compute differs too.

## Status history

2026-10-03 PLANNED.
