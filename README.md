# Argine3 — predictive expert retention under hard memory constraints

**An executable investigation of whether short-horizon routing prediction can improve expert retention in memory-constrained Mixture-of-Experts inference — and whether improvements observed in replay survive contact with the real runtime.**

Argine3 studies a narrower question than its predecessors.

[Argine](docs/history/ARGINE-V1-README.it.md) established that large sparse Mixture-of-Experts models can be executed on consumer hardware by streaming experts from NVMe through a hard-capped DRAM cache.

Argine2 then investigated expert residency as a resource-allocation problem across layers and showed that a substantial part of the remaining opportunity lies not only in **how much memory each layer receives**, but in **which experts are retained**.

Argine3 follows that result to its next testable question:

> **Can future expert use be predicted well enough to make better retention decisions, and do those decisions improve real end-to-end inference performance?**

The answer is deliberately split into three distinct levels:

1. **routing prediction quality**;
2. **retention quality under controlled replay / counterfactual evaluation**;
3. **real runtime throughput**.

They do not collapse into one another.

The central result of this repository is therefore mixed:

- future expert use is predictably structured;
- prediction-aware retention improves the measured retention objective in replay;
- the implementation is semantically conformant with the frozen Python reference;
- but the tested prediction-aware policy **does not improve real runtime throughput** over the selected baseline.

That negative runtime result is part of the result, not something removed from the artifact.

---

## Publication

The final paper is under:

[`paper/research2/`](paper/research2/)

**Title**

*Clairvoyant-Comparator Expert Retention, Routing Structure, and Causal Predictive-Retention Validation in Memory-Constrained Mixture-of-Experts Inference*

The publication-facing experiment identifiers are `R-01` through `R-12`, including `R-09A`, `R-09B`, and `R-BRIDGE-01`.

Internal repository identifiers such as `R2-*` are retained where necessary for provenance and frozen artifact compatibility.

---

## Main results

| Stage | Result | Interpretation |
|---|---:|---|
| Routing prediction | **SUPPORTED** | future expert use contains exploitable short-horizon structure |
| R-10 primary predictor | recall@k **0.449817** | selected `decayed_transition`, decay = 0.99 |
| R-10 vs last observation | **+12.04% relative** | 0.449817 vs 0.401473 |
| R-10 vs global frequency | **+28.71% relative** | 0.449817 vs 0.349480 |
| R-BRIDGE-01 | **BRIDGE POSITIVE** | prediction-aware protection improves the frozen retention proxy |
| R-11 | **PASS** | prospective replay validation supports the retention effect |
| R-12 | **FAIL** | the retention improvement does not translate into higher real runtime throughput |

### R-10 — prospective routing prediction

The selected predictor is:

```text
decayed_transition
decay = 0.99
```

On the held-out primary evaluation at horizon `h = 1`:

```text
decayed_transition     0.449817 recall@k
last_observation       0.401473
global_frequency       0.349480
```

Relative improvement:

```text
vs last_observation    +12.04%
vs global_frequency    +28.71%
```

The held-out evaluation meets or exceeds the DEV averages at all tested horizons.

The supported conclusion is limited to **predictive structure in expert routing**.

It is not, by itself, evidence of lower runtime latency or higher throughput.

---

## R-BRIDGE-01 — retention feasibility

A prospective bridge experiment tested whether the predictive signal could improve a retention objective before modifying the real runtime.

At the primary 4 GiB budget:

```text
B_selected
miss_bytes_per_decode_step = 250325713.409

PredictedProtectPolicy
miss_bytes_per_decode_step = 250267928.548

absolute improvement = 57784.861 bytes / decode step
relative improvement = 0.023084%
```

The direction was non-negative on all four fresh held-out workloads.

Classification:

```text
BRIDGE_POSITIVE
```

This result justified proceeding to the prospective retention experiment.

It did **not** justify a runtime-speedup claim.

---

## R-11 — prospective retention validation

R-11 tested the frozen prediction-aware policy on fresh traces under the pre-registered replay protocol.

Result:

```text
R-11 PASS
72 / 72 cells completed
0 budget violations
```

At the primary 4 GiB budget:

```text
delta miss bytes / decode step
PredictedProtect - B_selected

= -87,476.85 bytes / step

95% CI
[-154,123.32, -27,178.93]
```

The prediction-aware policy reduced miss traffic under the frozen replay abstraction.

Secondary budgets:

```text
1.5 GiB    -779,296.06 bytes / step
8 GiB         0.00 bytes / step
```

R-11 therefore supports the statement:

> **The predictive signal can be converted into a measurable retention improvement under the frozen replay semantics.**

It still does not establish end-to-end runtime speedup.

---

## R-12 — real runtime validation

R-12 moved the two frozen policies into the real C++ runtime and tested them end to end.

The runtime implementation includes:

- selectable retention policy in the real expert-cache hot path;
- deterministic policy configuration;
- event-level retention trace logging;
- C++ ↔ Python semantic replay comparison;
- strict protected-set comparison;
- real-model conformance validation;
- paired runtime benchmarking.

The implementation passed semantic conformance before benchmarking.

### Runtime campaign

```text
4 workloads
3 memory budgets
2 policies
5 counted repetitions per policy/cell
1 discarded warmup per policy/cell

144 / 144 physical launches completed
120 counted runs
24 warmups
0 unresolved protocol violations
```

Primary endpoint:

```text
4 GiB
```

### Primary 4 GiB result

```text
B_selected              3.503874 tokens/s
PredictedProtect        3.332153 tokens/s

delta
PredictedProtect - B_selected
= -0.171721 tokens/s

95% bootstrap CI
[-0.259917, -0.091642]

descriptive relative difference
-4.7734%

B_selected wins
18 / 20 paired comparisons
```

Classification:

```text
R-12 FAIL
```

The real runtime therefore falsifies the hypothesis that the tested prediction-aware retention policy improves throughput at the frozen primary endpoint.

### Secondary budgets

At 1.5 GiB:

```text
B_selected              2.321705 tokens/s
PredictedProtect        2.252874 tokens/s
delta                  -0.068831 tokens/s
95% CI                 [-0.093256, -0.054608]
descriptive difference -2.9238%
B_selected wins         18 / 20 pairs
```

At 8 GiB:

```text
B_selected              6.635572 tokens/s
PredictedProtect        6.198246 tokens/s
delta                  -0.4373259354 tokens/s
95% CI                 [-0.5315840344, -0.3666006098]
descriptive difference -6.5166%
B_selected wins         20 / 20 pairs
```

No independent classification is assigned to the secondary budgets.

The 8 GiB records do not contain retention-traffic telemetry, so the observed throughput deficit cannot be causally attributed to retention overhead, policy bookkeeping, or another runtime mechanism.

No monotonic causal relationship between memory budget and overhead is claimed.

---

## What the result means

The final evidence chain is:

```text
predictive routing structure
        ↓
        SUPPORTED

prediction-aware retention proxy
        ↓
        BRIDGE_POSITIVE

prospective replay retention
        ↓
        R-11 PASS

real runtime throughput
        ↓
        R-12 FAIL
```

These results are compatible.

A predictor can forecast future expert use.

A retention policy can use that forecast to reduce a replay-level miss-byte objective.

The same policy can still fail to improve end-to-end runtime throughput.

Argine3 therefore provides evidence for a distinction that is important in memory-tiered MoE systems:

> **better prediction is not equivalent to better retention, and better retention is not equivalent to faster inference.**

The runtime result does not invalidate the upstream prediction or replay results.

Likewise, the upstream results do not override the negative runtime result.

---

## Experimental platform

The core runtime-validation campaign uses:

| Component | Configuration |
|---|---|
| CPU | Intel Core i5-1340P |
| RAM | 16 GB class consumer system |
| GPU | no dedicated GPU; CPU inference |
| Storage | NVMe |
| OS | Windows |
| Model | Qwen3-30B-A3B Q4_K_M |
| MoE layers | 48 |
| Experts / layer | 128 |
| Active experts / token | 8 |
| Runtime | patched `llama-server` with Argine expert store and retention-policy integration |
| Runtime validation budgets | 1.5 / 4 / 8 GiB |
| Primary runtime endpoint | 4 GiB |

The research is intentionally scoped to the reference system.

No hardware-generalisation claim is made.

---

## Runtime conformance

Before runtime benchmarking, the C++ policy implementation was checked against the frozen Python policy reference.

The conformance trace records, among other fields:

```text
requested experts
hits
misses
resident state before / after
evictions
resident bytes
budget
policy parameters
scores
last-reference state
protected expert sets
```

Real-model traces for both policies were replayed through the Python comparator.

The C++ and Python decisions matched under the strict comparison used by the protocol.

Only after conformance passed was the runtime benchmark campaign executed.

---

## Repository structure

Key locations:

| Path | Purpose |
|---|---|
| [`paper/research2/`](paper/research2/) | final paper, figures, tables and publication artifacts |
| [`experiments/`](experiments/) | experimental protocols, evidence and frozen results |
| [`experiments/R2-10*`](experiments/) | predictive-routing evaluation |
| [`experiments/R2-BRIDGE*`](experiments/) | prospective bridge evaluation |
| [`experiments/R2-11*`](experiments/) | prospective retention validation |
| [`experiments/R2-12-runtime-validation/`](experiments/R2-12-runtime-validation/) | real-runtime implementation, conformance and benchmark evidence |
| [`tools/`](tools/) | analysis, validation, replay and benchmark tooling |
| [`PUBLIC_MIRROR_PROVENANCE.txt`](PUBLIC_MIRROR_PROVENANCE.txt) | provenance of the public GitHub mirror |
| [`PUBLIC_MIRROR_COMMIT_MAP.csv`](PUBLIC_MIRROR_COMMIT_MAP.csv) | canonical → public rewritten commit mapping |

Historical Argine / Argine2 material remains in the repository because this research is cumulative and because frozen scientific provenance must remain inspectable.

The current landing page is intentionally focused on the predictive-retention study rather than reproducing the earlier Argine2 narrative.

---

## Scientific discipline

The repository follows several evidence rules.

### Frozen protocols

Evaluation criteria are frozen before the corresponding evaluation is opened.

### DEV and EVAL separation

Predictor selection is performed on development data.

Held-out evaluation is opened only after selection is frozen.

### Negative results are retained

A failed hypothesis remains part of the repository.

R-12 is therefore reported as `FAIL`; it is not rewritten as a partial success because upstream metrics were positive.

### Proxy, replay and runtime are separated

The following are treated as different evidence layers:

```text
routing prediction
retention proxy
counterfactual / replay retention
real runtime throughput
```

Evidence at one layer is not promoted automatically to another.

### Frozen evidence is not rewritten

Corrections and later interpretations are additive.

Historical scientific artifacts remain attributable to their original commits.

---

## Clairvoyant comparator

The repository also uses a future-informed Belady-style comparator as a reference for remaining retention headroom.

It is a **clairvoyant comparator**, not a deployable policy.

It should also not be interpreted as a proof of byte-optimality for the general variable-size, byte-capacity, multi-expert event objective.

Its role is to measure how far practical policies remain from a strong future-informed reference.

---

## Large files and Git LFS

Several real-runtime conformance traces are approximately 1.8 GB each.

The public GitHub mirror therefore stores four large JSONL artifacts using Git LFS.

The scientific contents are unchanged, but Git history had to be rewritten in the public transport mirror so that the large blobs could be represented by LFS pointers.

The original local scientific repository remains the canonical history.

See:

- [`PUBLIC_MIRROR_PROVENANCE.txt`](PUBLIC_MIRROR_PROVENANCE.txt)
- [`PUBLIC_MIRROR_COMMIT_MAP.csv`](PUBLIC_MIRROR_COMMIT_MAP.csv)

for the mapping between canonical and public commits.

---

## Publication provenance

Canonical final scientific commit:

```text
4ce35fef61cd9c8155c3b88222cb6b52a3f62fb6
```

Equivalent commit in the GitHub LFS mirror:

```text
ebd8e50bb616bb15ed286a4d8e0408ddc0cfbe70
```

Frozen publication tag:

```text
research2-paper-final
```

Frozen PDF SHA-256:

```text
8972ed242c46d1e556eafa18e21103b2fe7701d3f6fc6a9e0a7fa02b2a5d1b72
```

The GitHub mirror contains an additional provenance commit above the frozen publication commit.

That commit documents the canonical-to-public mapping and does not alter the frozen scientific artifact.

---

## Reproducibility

The repository contains the code and evidence required to inspect:

- predictor selection;
- held-out predictor evaluation;
- bridge analysis;
- replay retention evaluation;
- policy implementation;
- C++ / Python conformance;
- runtime benchmark configuration;
- paired benchmark results;
- bootstrap analysis;
- publication tables and figures.

Large real-runtime traces require Git LFS.

The original GGUF model and local expert-store file are not distributed by this repository.

Reproducing the full runtime experiment therefore requires the corresponding model, compatible expert store, Windows runtime environment, and the frozen runtime configuration recorded in the experiment artifacts.

---

## Interpretation boundaries

This repository supports the following statements:

- short-horizon expert routing contains predictive structure;
- the selected predictor generalises to fresh held-out workloads;
- prediction-aware protection improves the frozen retention objective in the bridge experiment;
- the effect survives prospective replay validation;
- the C++ runtime implementation matches the frozen Python policy semantics under the conformance test;
- the tested policy does not improve real throughput at the frozen 4 GiB primary endpoint.

It does **not** establish:

- a runtime speedup from prediction-aware retention;
- generalisation across arbitrary hardware;
- universal superiority of the selected predictor;
- causal attribution of the observed runtime slowdown to a specific implementation mechanism;
- optimality of the clairvoyant comparator under the full byte-capacity objective.

---

## Status

The experimental campaign reported by the paper is closed.

```text
routing prediction            CLOSED
prospective bridge            CLOSED
prospective replay retention  CLOSED
runtime policy conformance    CLOSED
runtime validation            CLOSED
publication                   FROZEN
```

The repository is retained as an executable scientific artifact.

Future work may investigate policies that optimise the **runtime value of memory** rather than prediction accuracy or miss traffic alone, including combinations of future-use probability, reload cost, memory pressure, layer position and marginal byte value.

Those directions are not required to reproduce or interpret the results reported here.

---

## Citation

Until a DOI or archival citation is available, cite:

1. the paper title;
2. this repository;
3. the frozen publication tag or commit corresponding to the result used.

For exact scientific provenance, prefer the canonical commit identifiers documented in the paper and in `PUBLIC_MIRROR_COMMIT_MAP.csv`.