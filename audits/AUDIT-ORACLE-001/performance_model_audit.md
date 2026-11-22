# EXP-025 Performance Model Audit (AUDIT-ORACLE-001)

## Source inspected

- `experiments/EXP-025-performance-model/README.md` (Status SUPPORTED; primary predictive claim supported on frozen 30B budget sweep)
- `experiments/EXP-025-performance-model/config.json`, `protocol_freeze.md`, `results.csv`, `processed/prediction.json` (existence/provenance context)

## What EXP-025 models

EXP-025 tests an interpretable per-token model for the frozen Qwen3-30B-A3B EXP-022 natural-language diagnostic configuration:

`T_token = T_compute + T_unhidden_IO + T_sync + T_controller`

with four non-negative fitted coefficients `(c,s,a,b) >= 0`, using decode-only numerators normalized to the frozen generated-token denominator (384). Primary features: `bytes_missed_per_token` (decode-attributed IORecord bytes / 384), `reads_per_token` (decode-attributed I/O records / 384), `nodes_per_token` (workload decode Node records / 384), `qd` (time-weighted mean queue depth while decode-attributed I/O queue is busy, using `argine.research.iotimeline.queue_depth` semantics; may be `upper_bound`), `controller_ms_per_token` (0 for the static controller path).

The I/O term uses missed bytes divided by the cost-model bandwidth curve at `qd` (frozen `CostModel.bandwidth()`/clamping semantics) plus a per-read term; it is not a single constant `c * bytes` over arbitrary streams.

## Dependencies on miss-stream properties

Modeled output can depend on:
- aggregate missed bytes (numerator in I/O-bytes term)
- reads count / read-size distribution implied by bytes and reads (explicit `reads_per_token` term)
- queue depth `qd` (time-weighted; depends on decode-attributed I/O timing/stream and completion-timestamp exactness)
- decode attribution / node structure (nodes constant in primary dataset; not policy-independent in general without evidence)
- cost-model bandwidth curve/clamping
- frozen target/domain semantics (generated-token denominator, decode-only attribution, workload/runtime)

Not equivalent to measured end-to-end latency, time-to-final-answer, or throughput (R2-12 runtime quantities).

## Performance-model class

**P2 — FULL STREAM / DISTRIBUTION MODEL** (material dependence on miss-stream properties beyond aggregate bytes alone).

Rationale: bytes, reads, and `qd`/bandwidth context all participate; equal bytes with different reads/queue behavior can yield different modeled time. P1 is invalid for arbitrary streams; P3 (`T=c(stream)*bytes`) is too narrow because of the explicit reads term and `qd`-dependent bandwidth.

## Can two policies with equal total miss bytes produce materially different modeled service time?

**YES.** The model includes `reads_per_token` and `io_bytes_over_bw(qd)`; equal total bytes can correspond to different read counts/patterns and different time-weighted queue depth.

## Policy-induced miss-stream transferability

**UNSUPPORTED** for arbitrary policy-induced miss streams under the audited evidence.

EXP-025 established held-out predictive accuracy on a preregistered 30B budget sweep for one workload/runtime/model with fixed protocol; it did not validate transfer to different retention policies at the same budget producing different miss streams. Future policy comparisons using EXP-025 must either validate transfer or label the conversion approximate/unsupported.

## Correct future form

Use `DeltaT_model = PerformanceModel(MissStream_B_best) - PerformanceModel(MissStream_P)`. Do not use a single shared coefficient over aggregate bytes unless independently validated. Do not equate modeled I/O/service time with measured latency/TTFA/throughput. No numeric practical-effect threshold is preregistered by this audit.
