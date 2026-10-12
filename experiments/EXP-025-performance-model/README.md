# EXP-025 — Interpretable per-token performance model

| field | value |
|---|---|
| ID | `EXP-025-performance-model` |
| Status | **PLANNED** |
| Period | — |
| Origin | Argine2 |
| Related questions | RQ-013 |
| Supersedes / superseded | — |

## Research question

Can T_token = T_compute + T_unhidden_IO + T_sync + T_controller predict measured ms/token within 10-15% median error?

## Hypothesis

[HYPOTHESIS] Four physically meaningful coefficients, fitted by non-negative least squares, predict leave-one-out ms/token within 15%.

## Setup

Features from EXP-007/022 runs (bytes missed, reads, nodes, queue depth per token from per-request timestamps, with its exact/upper-bound status); `argine research predict`.

## Commands

```bat
argine research predict runs.csv --out prediction.json
```

## Raw data

Pending.

## Result

No result yet. [DERIVED, rough consistency only] The default cost model predicts 72 ms/token of stall for the pilot trace at 4 GiB; the measured gap between 4 GiB (≈159 ms/token) and full residency (≈104 ms/token) on the 30B is ≈55 ms.

## Interpretation

—

## Limitations

Needs ≥ 5 configurations.

## Status history

2026-10-03 PLANNED.
