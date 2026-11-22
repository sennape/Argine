# Research 3 — can predictive expert information become real-runtime retention value?

**State: CLOSED / PUBLISHED.** Final classification: `R3_CLOSED_NEGATIVE_PREDICTIVE_RETENTION_NOT_VIABLE_IN_TESTED_REGIME`. Closure record: [`experiments/R3-final-closure/`](../../experiments/R3-final-closure/README.md).

Research 3 asked whether the predictive expert information established in Research 2 can be converted into useful real-runtime retention value, through prospectively frozen gates.

| Stage | Scientific question | Outcome | Repository ID |
|---|---|---|---|
| Runtime Semantic Equivalence | Does the corrected runtime realize the reference event semantics? | The earlier runtime issued 144 policy calls per decode token where the reference defines 48; after correction, conformance passed. | R3-03A |
| Predictor Cost Attribution | Does policy bookkeeping explain the sham-over-baseline overhead? | Yes (explained). | R3-03B |
| Real-Runtime Retention Evaluation | Does the real predictive policy beat the baseline end to end? | Net loss of 15.83 ms/token at 1.5 GiB (95% CI [10.46, 21.19]). | R3-04 |
| Offline Replay Fidelity | Does offline replay reproduce the runtime counters? | Yes, integer-exactly. | R3-05 R1 |
| Clairvoyant Retention Headroom | Is there clairvoyant headroom worth at least 10 ms/token? | Yes: a Belady-style comparator removes 40.673% of baseline misses (not a causal target). | R3-05 R2 |
| Current-Event Shielding | Is the original predictive mechanism's effect reproduced by a zero-cost shield? | Yes (0.559% vs 0.230% of the oracle byte headroom); the original mechanism is retired. | R3-05 R3 |
| Causal Next-Use Eviction | Does causal next-use eviction clear the zero-cost floor? | It captured up to 8.79% of the headroom. | R3-05 R4 |
| Prospective Viability Test | Does the best causal candidate clear the frozen 10 ms/token floor before any computation cost? | No: 8.11 ms/token at best. | R3-05 R4 (K4 rule) |

**Main conclusion.** Predictive structure in MoE expert routing was measurable, but within the tested regime (one model, host, memory budget, workload and runtime) it did not translate into economically useful prediction-led retention control. This is a bounded result: it is not a statement about all retention policies or all MoE inference, and routes such as oracle-distilled eviction (tested next, in Research 4), prefetch, compute/I/O overlap and admission control were not tested in Research 3.

## Where the evidence is

| what | path |
|---|---|
| experiments | `experiments/R3-*`, [`experiments/R3-05-economic-retention/`](../../experiments/R3-05-economic-retention) |
| final closure and its verifier | [`experiments/R3-final-closure/`](../../experiments/R3-final-closure/README.md) |
| published paper, claim audit, source map, verifier | [`paper/research3/`](../../paper/research3/README.md) — see [`papers/research-3`](../../papers/research-3/README.md) |
| Research 3 history and the published-paper tag | `legacy/argine4/research3-paper-v1`, `legacy/argine4/main` |

## Verification

```
python shared/tooling/run_frozen_verifier.py experiments/R3-final-closure/closure.py verify
python shared/tooling/run_frozen_verifier.py paper/research3/scripts/verify_paper.py
```
