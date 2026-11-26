# Argine — memory-constrained Mixture-of-Experts inference research

**Argine** is one research program on memory-constrained Mixture-of-Experts (MoE) inference: expert offloading, residency and retention on a single consumer PC (16 GB RAM, NVMe storage, no discrete GPU), built around an instrumented `llama.cpp` runtime that streams experts from NVMe through a hard-capped expert store. This repository is the single canonical public record of the whole program, **Research 1 through Research 4**. Everything in it was frozen before evaluation, hashed, and committed separately; frozen evidence is never edited.

## Program status

| research | question | state | bounded outcome |
|---|---|---|---|
| **Research 1** — observation and characterization (`EXP-001`..`EXP-027`) | Is expert residency better modelled as online resource allocation across layers than as cache replacement? | **CLOSED** | 27 experiments: 13 supported, 7 falsified, 1 superseded, 6 inconclusive. Layer-aware allocation beats global LRU in most constrained regimes, mostly by partitioning; the adaptive controller did not beat a DEV-fitted static split on EVAL (EXP-010, falsified). |
| **Research 2** — oracle headroom, routing structure, causal prediction, predictive retention, runtime validation (`R2-*`) | Is there recoverable retention headroom, and can a causal predictor turn it into fewer miss bytes and faster decoding? | **CLOSED / FROZEN** | Clairvoyant headroom and routing structure exist; the causal predictor beat trivial baselines at horizon 1 and cut miss bytes in replay (72 of 72 cells); no real-runtime throughput gain at 4 GiB was recorded (`R2_12_FAIL`). |
| **Research 3** — can predictive information become real-runtime retention value? (`R3-*`) | Does the Research 2 predictor yield net economic value in the real runtime? | **CLOSED / PUBLISHED** | `R3_CLOSED_NEGATIVE_PREDICTIVE_RETENTION_NOT_VIABLE_IN_TESTED_REGIME`. Net loss of 15.83 ms/token at 1.5 GiB; best causal next-use eviction reached 8.11 ms/token before any computation cost, below the frozen 10 ms/token floor. |
| **Research 4** — causal future-distance distillation (`R4-*`) | Can a cheap causal rule, distilled from the clairvoyant eviction optimum, recover enough of the headroom? | **CLOSED** | `R4_CFDC_CAUSAL_STATE_INSUFFICIENT` (state `R4_CFDC_CLOSED`). On the prospective EVAL set the cheap rule captured −29.96% of the headroom (it incurred more miss bytes than the baseline) and the rich learner 19.11%, below the preregistered 25% bar. Manuscript: [`paper/research4/`](paper/research4/README.md). |
| **Research 5** — exposed transfer stall (`R5-*`) | Can a causal deferred-wait scheduler keep unavoidable expert-transfer latency off the decode critical path and lower decode time per token by at least 2%? | **OPEN — G1 and G2 PASSED (sealed); Phase 2 not opened** | Protocol frozen (`R5_ETS_PHASE0_FROZEN`); deferred-wait arms implemented (`R5_ETS_PHASE1A_IMPLEMENTED`); host conformance `R5_G1_HOST_CONFORMANCE_PASS` (17/17 criteria, 42/42 development launches) sealed; headroom gate `R5_G2_HEADROOM_GATE_PASS` (optimistic bound H = 0.0844 of decode time, threshold 0.04; an opening gate, not a gain) sealed; evaluation locked. No performance result yet. |

**Research 5 is open after G2:** the prospective protocol is frozen, the runtime arms pass host conformance and the one-shot headroom gate on development prompts; the evaluation is still locked and no performance result exists. See [`research/research-5`](research/research-5/README.md).

## Scientific trajectory

observation / characterization (Research 1) → prediction (Research 2) → real-runtime retention test (Research 3) → causal eviction/distillation test (Research 4) → bounded falsification of the tested retention family.

This is a record of successful falsification, not of a failed project. Research 3 and Research 4 close **specific retention approaches** — prediction-led retention and cheap causal distilled eviction — within the tested regime (one model, host, memory budget, workload set and runtime, and the frozen feature space and decision rules). They do not show that all memory-management, retention, prefetch, admission-control or latency-hiding approaches fail, and they do not show that other hardware or regimes behave the same.

## Where things are

| path | contents |
|---|---|
| [`research/`](research/README.md) | navigation layer: one folder per research with its question, status, experiment index, verifier commands and the omitted raw artifacts |
| [`papers/`](papers/README.md) | pointers to the manuscripts (Research 1 `paper/`, Research 2 `paper/research2/`, Research 3 `paper/research3/`) |
| [`experiments/`](experiments/INDEX.md) | the frozen scientific tree: `EXP-*` (Research 1), `R2-*`, `R3-*`, `R4-*`, `R5-*`; stays in place so every frozen hash and verifier path remains valid |
| `argine/`, `tools/`, `tests/`, `patches/`, `workloads/`, `data/`, `docs/`, `research2/` | shared runtime, tooling, tests, patches, frozen workloads, historical data and documentation |
| [`manifests/`](manifests) | consolidation manifests: legacy repositories, omitted raw artifacts, research layout map, file hashes, path-compatibility record |
| [`REPRODUCING.md`](REPRODUCING.md) | how to verify each research stage |
| [`CITATION.cff`](CITATION.cff), [`CONTRIBUTING.md`](CONTRIBUTING.md), [`THIRD_PARTY_NOTICES.md`](THIRD_PARTY_NOTICES.md) | citation, contribution rules, third-party notices |

## History

This repository consolidates the earlier Argine repositories. Their commits remain reachable as annotated tags `legacy/argine2/*`, `legacy/argine3/*` and `legacy/argine4/*` (see [`manifests/legacy-repositories.json`](manifests/legacy-repositories.json)). Four 1.9 GB raw conformance traces from Research 2 are not hosted on GitHub; they are identified by path, size and SHA-256 in [`manifests/raw-artifacts-omitted.json`](manifests/raw-artifacts-omitted.json). The Research 1 README of the earlier repository is preserved in [`research/research-1/RECORD.md`](research/research-1/RECORD.md).

## Contents

<!-- indice: generato da strumenti/controlla_readme.py, non modificare a mano -->

- [Program status](#program-status)
- [Scientific trajectory](#scientific-trajectory)
- [Where things are](#where-things-are)
- [History](#history)
- [Contents](#contents)

<!-- fine indice -->
