# EXP-023 — Routing under workload transitions

| field | value |
|---|---|
| ID | `EXP-023-workload-drift` |
| Status | **PLANNED** |
| Period | — |
| Origin | Argine2 |
| Related questions | RQ-004, RQ-006 |
| Supersedes / superseded | — |

## Research question

How do working sets, entropy and cache behaviour change when the workload switches (natural language → code → math → JSON), and how fast does a policy adapt?

## Hypothesis

[HYPOTHESIS] Transitions change the per-layer expert distribution measurably (JSD > within-segment noise) and cause a transient miss excess that adaptive allocation shortens.

## Setup

`workloads/v1/transition.json` (4 x 300 tokens, one conversation), both models; adaptation metrics from `replay.adaptation_metrics`.

## Commands

```bat
argine research trace MODEL --expert-store --budgets 2G --workload transition --split dev --out experiments/EXP-023-workload-drift/raw
argine research analyze TRACE --budgets 4G,2G,1.5G --out experiments/EXP-023-workload-drift/processed
```

## Raw data

Pending.

## Result

No result of this experiment yet (both models, dev and eval, are pending).

[DERIVED, early evidence on the pre-registered hypothesis, from the EXP-007 30B transition DEV trace, one session, counterfactual replay, 2026-10-04] Write-up: [docs/research/TRANSITION-ADAPTIVE-GAIN.md](../../docs/research/TRANSITION-ADAPTIVE-GAIN.md).
- **Transitions change the routing measurably:** EWS90 is 0.27–0.43 per segment and 0.55 over the session; segment-optimal layer splits differ by 5–15% of the budget.
- **Every policy, static ones included, shows a transient miss excess after a boundary.** For example, NL → code at 4 GiB: +1,465 misses in 64 steps for the frozen split.
- **The adaptive controller does not shorten it** (+1,539). [FALSIFIED for this controller on this trace]. Hot-set pinning lengthens it (+2,196 / +2,340).

## Interpretation

—

## Limitations

One session per model; dev/eval sessions are different texts.

## Status history

2026-10-03 PLANNED.
