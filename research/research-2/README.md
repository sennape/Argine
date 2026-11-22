# Research 2 — oracle headroom, routing structure, causal prediction, predictive retention

**State: CLOSED / FROZEN.** Historical result preserved exactly; it is not reinterpreted using later Research 3/4 knowledge ([`RESEARCH_2_FINAL_CLOSURE.md`](../../RESEARCH_2_FINAL_CLOSURE.md)).

Research 2 moved from oracle feasibility to real-runtime validation in thirteen stages (last column: repository identifiers, provenance only).

| # | Stage | Scientific question | Outcome | Repository ID |
|---|---|---|---|---|
| 1 | Oracle Trace Contract and Replay Validation | Does offline replay of a runtime trace agree with runtime telemetry? | Zero event disagreements on the measured coding trace. | R2-01 |
| 2 | Oracle vs Retention Baselines | How far is a causal LRU baseline from a clairvoyant comparator? | Comparator has 58.59% fewer miss bytes at 4 GiB. | R2-02 |
| 3 | Expert-Memory Budget Sensitivity | How does that gap depend on the memory budget? | 44.11% at 1.5 GiB, 70.66% at 8 GiB, none at 16 GiB. | R2-03 |
| 4 | Recoverable-Margin Analysis | Is the gap a recoverable margin? | Gap rises, then collapses at full residency; not a proven bound on causal recovery. | R2-04 |
| 5 | Expert Frequency and Concentration | Is layer-local routing non-uniform? | Yes. | R2-05 |
| 6 | Routing Transition Structure | Does the previous routing state inform the next? | Yes. | R2-06 |
| 7 | Reuse Distance and Working-Set Structure | Is short-horizon reuse stronger than chance? | Yes, against a shuffled null. | R2-07 |
| 8 | Routing Drift and Workload Dependence | Does routing change more across workloads than over time? | Cross-workload divergence exceeds temporal drift. | R2-08 |
| 9 | Prediction Benchmark | Which causal baselines set the bar for prediction? | Benchmark contract frozen; development tournament run. | R2-09 |
| 10 | Causal Working-Set Predictor | Does the selected causal predictor beat trivial baselines on sealed data? | Yes at horizon 1. | R2-10 |
| 11 | Retention Feasibility Bridge | Can predictions reduce miss bytes in prospective replay? | Positive (`BRIDGE_POSITIVE`). | R2-BRIDGE-01 |
| 12 | Prediction-Driven Retention | Does the predictor-driven reactive policy reduce miss bytes in confirmatory replay? | Yes (72 of 72 cells). | R2-11 |
| 13 | Real-Runtime Decode Throughput | Does that policy raise real decode throughput? | No improvement at 4 GiB as recorded in Research 2 (`R2_12_FAIL`); re-examined in Research 3 (Runtime Semantic Equivalence). | R2-12 |

## Where the evidence is

| what | path |
|---|---|
| experiments | `experiments/R2-*` (see the identifiers above), [`experiments/R2-12-runtime-validation/`](../../experiments/R2-12-runtime-validation) |
| closure record | [`RESEARCH_2_FINAL_CLOSURE.md`](../../RESEARCH_2_FINAL_CLOSURE.md), [`RESEARCH_2_FINAL_CLOSURE.json`](../../RESEARCH_2_FINAL_CLOSURE.json) |
| manuscript, claim ledger, provenance and freezes | [`paper/research2/`](../../paper/research2/README.md) — see [`papers/research-2`](../../papers/research-2/README.md) |
| protocol, baselines and workload configuration | [`research2/`](../../research2), [`docs/research2/`](../../docs/research2/README.md), [`workloads/`](../../workloads) |
| milestone code | [`argine/research2/`](../../argine/research2), [`tools/`](../../tools) (`r2_*`), [`tests/`](../../tests) (`test_r2_*`) |
| Research 2 history | tags `legacy/argine3/*` ([`manifests/legacy-repositories.json`](../../manifests/legacy-repositories.json)) |

## Omitted raw artifacts

Four R2-12 conformance traces of about 1.9 GB each are not hosted on GitHub (path, size and SHA-256 in [`manifests/raw-artifacts-omitted.json`](../../manifests/raw-artifacts-omitted.json)). The remaining R2-12 evidence, including [`CONFORMANCE-MANIFEST.json`](../../experiments/R2-12-runtime-validation/HOST-CONFORMANCE-PASS-V8-20261115/CONFORMANCE-MANIFEST.json), is present.

## Verification

```
python paper/research2/scripts/verify_paper_freeze.py
python -m unittest discover -s tests
```
