# EXP-022 — Extreme memory pressure: where streaming stops being useful

| field | value |
|---|---|
| ID | `EXP-022-extreme-pressure` |
| Status | **PLANNED** |
| Period | — |
| Origin | Argine2 |
| Related questions | RQ-008 |
| Supersedes / superseded | — |

## Research question

What happens below 1.5 GiB (1 GiB, 768 MiB, 512 MiB), and where does each policy become non-interactive?

## Hypothesis

[HYPOTHESIS] Prediction written before measuring (from the EXP-009 pilot): on Qwen3-30B-A3B, below one token's expert footprint (~1.02 GiB, 384 experts) the runtime's sharded LRU collapses (pilot replay: 0-20% expert hits at 1 GiB, ~0-2% at 768 MiB), while frequency-based retention would keep ~30-45%. If the runtime keeps more than 30% expert-level hits at 768 MiB, the prediction is falsified.

## Setup

`argine research trace` with --budgets 1.5G,1G,768M,512M interleaved, 3 repetitions, on both models (80B footprint per token: 480 experts ≈ 0.67 GiB, so the 80B should collapse later).

## Commands

```bat
argine research trace MODEL --expert-store --budgets 1.5G,1G,768M,512M --repeat 3 --workload natural-language --out experiments/EXP-022-extreme-pressure/raw
```

## Raw data

Pending.

## Result

No result yet.

## Interpretation

—

## Limitations

Store minimum budget: a prompt ubatch can touch every expert of a layer; budgets below psx_cache_min_budget abort by design.

## Status history

2026-10-03 PLANNED (prediction recorded).
