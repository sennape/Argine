# Research issues to open on GitHub

When the repository is public, each line becomes an issue (title as written). They let GitHub work as a distributed research notebook.

| title | body pointer |
|---|---|
| `[RQ-001] Out-of-sample comparison of layer-aware vs uniform allocation` | OPEN_QUESTIONS.md#RQ-001; DEV and EVAL traces of both models exist. [2026-10-11] EXP-010 is complete and FALSIFIED for the adaptive-over-static retrospective-holdout sub-question (leakage B: EVAL adaptive outcomes had already been inspected in EXP-007). A genuinely untouched confirmation would require new EVAL2 prompts and traces. |
| `[RQ-003] Bootstrap confidence intervals for per-layer entropy and EWS` | code contribution in `argine/research/metrics.py` |
| `[RQ-009] Record the first router trace of Qwen3-Next-80B-A3B` | EXP-007, EXP-026 |
| `[RQ-010] Report runtime-style vs expert-level hit rate on real models` | EXP-020; any `research trace` run |
| `[RQ-011] Replay fidelity with 16 shards on a real run` | EXP-021 |
| `[RQ-012] Measure instrumentation overhead (interleaved on/off, 5 reps)` | EXP-024 |
| `[RQ-014] Thread sweep on the current non-OpenMP build` | EXP-001 |
| `[RQ-015] Log CPU frequency, temperature and load per run` | EXP-018 |
| `[RQ-017] Check the published definition of Least-Stale and fix the replay variant` | policies.LeastStale |
| `[RQ-018] Count partial (slice-level) evictions in instrumented runs` | evictions.csv |
| `[REPLICATION] EXP-005 30B budget sweep on different hardware` | label as other machine |
| `[REPLICATION] EXP-006 80B sweep on different hardware` | label as other machine |
| `[BASELINE] Implement ARC replay policy` | policies.py + budget-invariant test |
| `[BASELINE] Implement LeCaR / LFRU replay policy` | policies.py |
| `[ANALYSIS] Compare EWS95 with expert-level hit rate across budgets` | EXP-008 |
| `[ANALYSIS] Failure envelope map model × workload × budget` | RQ-008 |
| `[INSTRUMENTATION] Validate the Windows overlapped-I/O completion stamps` | EXP-019 limitation |
