# EXP-024 — Cost of the research instrumentation

| field | value |
|---|---|
| ID | `EXP-024-tracing-overhead` |
| Status | **PLANNED** |
| Period | — |
| Origin | Argine2 |
| Related questions | RQ-012 |
| Supersedes / superseded | — |

## Research question

How much does ARGINE_RESEARCH_TRACE slow generation?

## Hypothesis

[HYPOTHESIS] Under 2% at every budget.

## Setup

Same binary, trace on/off interleaved (A B B A ...), 5 repetitions, 30B at 4 GiB and 1.5 GiB.

## Commands

```bat
argine benchmark MODEL --expert-store --expert-budget 4G (with and without ARGINE_RESEARCH_TRACE set)
```

## Raw data

Pending.

## Result

No result yet. (Fixture smoke test: 2.6 vs 2.6 tok/s on an oversubscribed 2-core container — not a measurement.)

## Interpretation

—

## Limitations

—

## Status history

2026-10-03 PLANNED.
