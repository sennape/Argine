# R3-02 Phase 2 — Frozen Mechanism Analysis and Classification

Result: **MECHANISM_INSUFFICIENT** (condition 6, the 4 GiB precision criterion, failed).
Everything else the frozen rule requires passed.

## Acquisition audited

`C:\Users\alpel\Desktop\Argine4-r3runs\R3-02-p4` — 52 launches SUCCEEDED, 7/7 TRACE_COUNT, 35/35 CELLs, 87 admissions,
104 logs. Protocol digest `f8d4b3199c4c74555353e5f796eaf7d7c3687da97883ee07f7d1c81233c9469c`.

## What passed

- **7/7 traces admissible**, all COMPLETE, budget violations 0, 512 tokens each.
- **35/35 cells admissible**, unique (configuration, repetition) identity.
- **A/B trajectories byte-identical at 1.5, 4 and 8 GiB** after excluding the three
  policy-internal fields (`policy`, `policy_params`, `protected_before`): 148,032 rows per
  comparison, zero mismatches. Counters identical at every budget.
- **Predictive bookkeeping ran**: 73,968 of 74,016 sham decision rows carry a non-empty
  predicted set; `b_selected` carries none.
- **Traffic present at every budget**: 6.43e8, 3.10e8 and 8.25e7 miss bytes per token at
  1.5/4/8 GiB; evictions per decision step 1.54 / 0.72 / 0.16.
- Identities, contract digest, schema and budget invariants all match.

## What failed

**Condition 6 — 4 GiB precision.** The 95% paired bootstrap CI half-width of the sham
overhead is **0.030396 s/token**, while 1% of the median
`b_selected` step time is **0.002665 s/token** — the
estimate is ~11.4× too wide. The frozen criterion is not met; it was fixed before the data
and is not changed here.

## Observed timing (median paired differences, B − A)

| budget | median A (s/token) | median B | difference | relative | 95% CI |
|---|---|---|---|---|---|
| 1.5 GiB | 0.558282 | 0.600592 | 0.066469 | 12.58% | [0.039863, 0.193757] |
| 4 GiB | 0.266464 | 0.320852 | 0.066749 | 25.25% | [0.025269, 0.086061] |
| 8 GiB | 0.129269 | 0.183934 | 0.057946 | 44.83% | [0.049942, 0.065582] |

Control C (predicted_protect, 8 GiB) median step time 0.183967 s/token,
essentially the sham's (0.183934).

## Interpretation boundaries

The evidence supports: identical retention under predictive bookkeeping, real prediction
activity, non-zero expert traffic, and a measurable sham slowdown (+25.3% at 4 GiB). It does
**not** explain the R2 throughput regression, does not establish causal I/O attribution, does
not predict a cost-aware improvement, and does not generalize beyond this host/model/workload.

## Cost-model gate

**COST_MODEL_DESIGN_NOT_YET_ALLOWED** — the frozen gate requires MECHANISM_OBSERVABLE.

## Files

`ANALYSIS.json`, `CLASSIFICATION.json`, `ANALYSIS_RAW.json`, `tests/test_phase2.py`, `FREEZE.json`.
Raw traces stay outside Git and are referenced by logical/archive identity.
