# R5-01 — ETS runtime realisation (Phase 1a)

**State: `R5_ETS_PHASE1A_IMPLEMENTED`.**
- Implementation gate: see [`GATE.json`](GATE.json).
- Host part of gate G1: **PENDING**, next step. The first host attempt hit a Windows heap-corruption defect in the model-free test and is **not canonical**: see [`G1-ATTEMPT-1-INCIDENT.md`](G1-ATTEMPT-1-INCIDENT.md). The defect is corrected and the build in force is `llama-research-r5-ets-2`.
- Headroom gate G2: **NOT RUN**.
- Evaluation: **LOCKED**.

No evaluation prompt was opened, and no canonical timing or mechanism metric exists. The only model launches so far are those of the interrupted, noncanonical first host attempt (development prompts only). Its outputs stay outside the repository and are excluded from G1.

The frozen protocol is [`R5-00-exposed-transfer-stall`](../R5-00-exposed-transfer-stall/README.md). This stage makes the three frozen arms real in the runtime. It is selected with `ARGINE_EXPERT_WAIT`, which fails closed:

| arm | value | wait placement | bookkeeping at later expert nodes |
|---|---|---|---|
| A (baseline) | unset or `layer` | all kinds, at the first expert node of the layer | release and re-acquire the slices as hits (unchanged code) |
| C0 (control) | `layer_touch` | all kinds, at the first expert node | pins held for the layer event; LRU-touch instead of re-acquire |
| C (scheduler) | `kind` | each node waits only for the kind it consumes | as C0 |

Where read/compute overlap can now occur: only in `kind`. Once the current layer's router has selected the experts, the first expert node issues every read, exactly as the baseline does. The reads of the second and third kinds stay in flight while the earlier nodes compute. Windows OVERLAPPED I/O needs no CPU thread for this. Nothing is issued earlier than in the baseline, and nothing is predicted.

| file | content |
|---|---|
| [`IMPLEMENTATION.json`](IMPLEMENTATION.json) | arm semantics, invariants, the implementation choices frozen with the code, and source hashes |
| [`INSTRUMENTATION_SCHEMA.json`](INSTRUMENTATION_SCHEMA.json) | `T` record, counters line, identity line, and the critical-path reconstruction of every frozen mechanism metric |
| [`MODEL_FREE_CONFORMANCE.json`](MODEL_FREE_CONFORMANCE.json) | model-free test results: Linux, ThreadSanitizer, ASan/UBSan, existing store regressions on frozen and R5 sources, independent review |
| [`GATE.json`](GATE.json) | the Phase 1a implementation gate and its criteria |
| `phase1a.py` | builder and verifier (`python phase1a.py verify`) |
| `ets_trace.py` | trace analysis: `structure()` (completeness only, Phase 1a) and `metrics()` (Phase 1b/2) |
| `host_conformance.py` | the development-only Windows host probe for gate G1 (next step); mandatory prerequisites fail closed before any model launch |
| [`G1-ATTEMPT-1-INCIDENT.md`](G1-ATTEMPT-1-INCIDENT.md) | the first host attempt (noncanonical): heap corruption in `argine-ets-test.exe`, root cause, correction |
| [`V5-RECOVERY.md`](V5-RECOVERY.md), [`V5-RECOVERY.json`](V5-RECOVERY.json), `v5-recovery/` | reconstruction of the lost llama-research-v5 comparator of G1 from frozen inputs, and the single identity the probe accepts besides the historical hash |
| `tests/` | verifier, trace-analysis, driver, prerequisite-gate and v5-recovery tests |

Runtime sources are in [`patches/r5-ets/`](../../patches/r5-ets/README.md), an additive copy; the frozen `patches/` files are unchanged. The build identity in force is [`llama-research-r5-ets-2/BUILD-INFO.json`](../../llama-research-r5-ets-2/BUILD-INFO.json); the first build's record [`llama-research-r5-ets/BUILD-INFO.json`](../../llama-research-r5-ets/BUILD-INFO.json) is kept unchanged. The binaries themselves are not in Git.

Verify:

    python shared/tooling/run_frozen_verifier.py experiments/R5-01-ets-implementation/phase1a.py verify
