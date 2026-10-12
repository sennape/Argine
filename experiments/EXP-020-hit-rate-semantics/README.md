# EXP-020 — What the runtime's hit rate counts

| field | value |
|---|---|
| ID | `EXP-020-hit-rate-semantics` |
| Status | **SUPPORTED** |
| Period | 2026-10-03 |
| Origin | Argine2 |
| Related questions | RQ-010 |
| Supersedes / superseded | — |

## Research question

Is Argine's reported cache hit rate the expert-level hit rate?

## Hypothesis

[HYPOTHESIS] No: for every layer, the gate, up and down MUL_MAT_ID nodes each request the slices of all three kinds (argine-experts.cpp loads the whole layer in the first node); the second and third requests are guaranteed hits, so the runtime counter over-states the hit rate.

## Setup

End-to-end fixture run of EXP-019; every slice access attributed to the node whose residency window contains it (`rawlog.node_attributed_outcomes`).

## Commands

```bat
argine research iotimeline experiments/EXP-019-instrumentation-validation/raw/e2e-fixture
```

## Raw data

Same raw log as EXP-019.

## Result

[MEASURED, fixture] 71 misses, all in gate nodes; up and down nodes: 1023 accesses each, 0 misses. Runtime-style hit rate 97.7%; gate-node (expert-level) hit rate 93.1%.

[MEASURED, Windows smoke run, 30B Q4_K_M, 4 GiB, coding dev, not publication-quality] runtime-style hit rate 90.06% vs expert-level 70.17% (all records); misses 9.94% vs 29.83%, ratio 3.00. Workload-only (warm-up excluded, from re-processing the raw log): 90.17% vs 70.51%.

## Interpretation

[DERIVED] runtime_miss_rate ≈ expert_miss_rate / 3. Applied to Argine's 80B figure (91.2% runtime hits): expert-level ≈ 74%. The qualitative conclusion of EXP-006 (an 11.7% cache serving far more than 11.7% of requests) survives; the number does not.

## Limitations

[SUPPORTED, EXP-021] Confirmed record-for-record on the nine 30B runs: all misses are in gate nodes; the runtime's `expert_level_hit_rate` is gate-node slice hits over prefill + decode, workload only (prefill gate-slice hit ≈ 9%). This is the metric any replay must reproduce (and the reason the old replay looked 3.5-4.8 pp too optimistic).

[LIMITATION] Mechanism measured on the fixture; the factor 3 assumes misses only in the first node of each layer (true on the fixture, to be verified on real models in EXP-007 runs, whose summaries report both rates).

## Status history

2026-10-03 SUPPORTED (mechanism, fixture). 2026-10-03 (later) factor 3.00 observed on the 30B in a Windows smoke run; publication-quality confirmation pending.
