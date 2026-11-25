# Research 1 — observation and characterization

**State: CLOSED.** Experiments `EXP-001`..`EXP-027`, run on one consumer PC (16 GB RAM, NVMe) with an instrumented `llama.cpp` expert store.

**Central question ([RQ-001](../../OPEN_QUESTIONS.md)).** Is expert residency better modelled as an online resource-allocation problem across layers than as a cache-replacement problem?

**Outcome.** The planned campaign is closed with 27 experiments: 13 SUPPORTED, 7 FALSIFIED, 1 SUPERSEDED, 6 INCONCLUSIVE. Layer-aware allocation beat global LRU in most constrained regimes and the gain grew with memory pressure, but most of the gain under pressure came from per-layer partitioning itself, it was model- and workload-dependent, and it stayed far from the oracle. The adaptive controller (EXP-010, replay only) was falsified against a DEV-fitted static allocation. Open questions are kept open in [`OPEN_QUESTIONS.md`](../../OPEN_QUESTIONS.md); falsified hypotheses are in [`FAILED_HYPOTHESES.md`](../../FAILED_HYPOTHESES.md).

## Where the evidence is

| what | path |
|---|---|
| full Research 1 record (preserved README of the earlier repository) | [`RECORD.md`](RECORD.md) |
| experiments | [`experiments/EXP-*`](../../experiments/INDEX.md) (registry: [`INDEX.md`](../../experiments/INDEX.md), [`index.json`](../../experiments/index.json)) |
| manuscript | [`paper/main.tex`](../../paper/main.pdf), [`paper/sections/`](../../paper/sections) — see [`papers/research-1`](../../papers/research-1/README.md) |
| research notes | [`ARGINE2-MEMORY-CHARACTERIZATION.md`](../../ARGINE2-MEMORY-CHARACTERIZATION.md), [`docs/research/`](../../docs/research) |
| toolchain | [`argine/research/`](../../argine/research), [`tools/`](../../tools), [`tests/`](../../tests) |
| historical raw data and the external raw inventory | [`data/`](../../data), [`data/external_raw_manifest.csv`](../../data/external_raw_manifest.csv) |
| Research 1 history | tags `legacy/argine2/*` ([`manifests/legacy-repositories.json`](../../manifests/legacy-repositories.json)) |

## Verification

```
python tools/index_experiments.py --check
python strumenti/controlla_readme.py
python tools/check_links.py
python -m unittest discover -s tests
```

## Experiments

| id | title | status | questions |
|---|---|---|---|
| [EXP-001-thread-sweep](../../experiments/EXP-001-thread-sweep/README.md) | Generation threads on a hybrid CPU | INCONCLUSIVE | RQ-014 |
| [EXP-002-repack](../../experiments/EXP-002-repack/README.md) | llama.cpp weight repack buffer on a CPU without AMX | SUPPORTED | — |
| [EXP-003-direct-io](../../experiments/EXP-003-direct-io/README.md) | Explicit unbuffered reads vs memory-mapped page faults | SUPPORTED | RQ-008 |
| [EXP-004-async-store](../../experiments/EXP-004-async-store/README.md) | Three read layers of the expert store on one binary | SUPPORTED | RQ-008, RQ-016 |
| [EXP-005-30b-budget-sweep](../../experiments/EXP-005-30b-budget-sweep/README.md) | Speed vs expert budget on Qwen3-30B-A3B | SUPPORTED | RQ-002, RQ-008, RQ-009 |
| [EXP-006-80b-budget-sweep](../../experiments/EXP-006-80b-budget-sweep/README.md) | Qwen3-Next-80B-A3B: prediction, measurement, budget sweep | FALSIFIED | RQ-002, RQ-009, RQ-010 |
| [EXP-007-router-characterization](../../experiments/EXP-007-router-characterization/README.md) | Per-layer routing characterization | SUPPORTED | RQ-001, RQ-002, RQ-003, RQ-004, RQ-005, RQ-006, RQ-007, RQ-009, RQ-010 |
| [EXP-008-working-set](../../experiments/EXP-008-working-set/README.md) | Effective Working Set (EWS) as a predictor | INCONCLUSIVE | RQ-002, RQ-003 |
| [EXP-009-policy-replay](../../experiments/EXP-009-policy-replay/README.md) | Counterfactual replay of cache and allocation policies | INCONCLUSIVE | RQ-001, RQ-005, RQ-007, RQ-017 |
| [EXP-010-adaptive-allocation](../../experiments/EXP-010-adaptive-allocation/README.md) | AdaptiveResidencyController (replay prototype) and ablations | FALSIFIED | RQ-001, RQ-006, RQ-007, RQ-008 |
| [EXP-011-binary-rebuild](../../experiments/EXP-011-binary-rebuild/README.md) | Matched model and runtime plan, different binaries: the observed 1.74x factor | SUPPORTED | RQ-014, RQ-015 |
| [EXP-012-dense-14b-control](../../experiments/EXP-012-dense-14b-control/README.md) | Does a bigger compressed MoE know more than a smaller intact dense model? | FALSIFIED | — |
| [EXP-013-quality-per-bit](../../experiments/EXP-013-quality-per-bit/README.md) | Perplexity vs expert bits per weight | SUPPORTED | — |
| [EXP-014-conversation-prefix-reuse](../../experiments/EXP-014-conversation-prefix-reuse/README.md) | TTFT in a growing conversation | SUPPORTED | — |
| [EXP-015-pretouch](../../experiments/EXP-015-pretouch/README.md) | PRETOUCH: page zeroing moved, not added | SUPPORTED | RQ-015 |
| [EXP-016-coactivation-reorder](../../experiments/EXP-016-coactivation-reorder/README.md) | Reordering the GGUF by expert co-activation | FALSIFIED | RQ-018 |
| [EXP-017-calibration-text](../../experiments/EXP-017-calibration-text/README.md) | A too-easy test text produced the wrong quality verdict | SUPERSEDED | — |
| [EXP-018-reproducibility-audit](../../experiments/EXP-018-reproducibility-audit/README.md) | Run-to-run and session-to-session dispersion in the raw logs | FALSIFIED | RQ-015 |
| [EXP-019-instrumentation-validation](../../experiments/EXP-019-instrumentation-validation/README.md) | Research instrumentation: correctness and completeness | SUPPORTED | RQ-011, RQ-012 |
| [EXP-020-hit-rate-semantics](../../experiments/EXP-020-hit-rate-semantics/README.md) | What the runtime's hit rate counts | SUPPORTED | RQ-010 |
| [EXP-021-replay-fidelity](../../experiments/EXP-021-replay-fidelity/README.md) | Does replay reproduce the runtime? | SUPPORTED | RQ-011, RQ-010, RQ-018, RQ-005 |
| [EXP-022-extreme-pressure](../../experiments/EXP-022-extreme-pressure/README.md) | Extreme memory pressure: where streaming stops being useful | INCONCLUSIVE | RQ-008 |
| [EXP-023-workload-drift](../../experiments/EXP-023-workload-drift/README.md) | Routing under workload transitions | FALSIFIED | RQ-004, RQ-006 |
| [EXP-024-tracing-overhead](../../experiments/EXP-024-tracing-overhead/README.md) | Cost of the research instrumentation | INCONCLUSIVE | RQ-012 |
| [EXP-025-performance-model](../../experiments/EXP-025-performance-model/README.md) | Interpretable per-token performance model | SUPPORTED | RQ-013 |
| [EXP-026-30b-vs-80b-regimes](../../experiments/EXP-026-30b-vs-80b-regimes/README.md) | Two experimental regimes: 30B-A3B vs Next-80B-A3B | INCONCLUSIVE | RQ-002, RQ-009 |
| [EXP-027-current-thread-sweep](../../experiments/EXP-027-current-thread-sweep/README.md) | Generation thread count on the current research runtime | FALSIFIED | RQ-014 |
