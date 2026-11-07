# Research Part 2 — methodology

This describes the rules the Research Part 2 infrastructure enforces, not
any experiment's scientific method (each experiment's own README states
that when it is designed).

## Provenance

Every future run records, at minimum: identity (experiment id, run id, UTC
start/end, outcome, schema version), Git (repo root, branch, commit,
clean/dirty), software (Python, Argine version, OS, runtime identity),
hardware (CPU, cores, RAM), artifacts (model/runtime-binary/expert-store
filename, size, SHA-256, where applicable), experiment (effective
parameters, seed, workload, baseline/policy id), and execution
(command, exit code, stdout/stderr paths, raw evidence paths). See
`argine/research2/provenance.py`.

Heavy artifact hashing (the real 30B/80B model, a multi-GiB runtime binary)
is never performed by `status`, `doctor`, or `audit`, and is not performed
by R2-M0.1 at all (nothing is implemented yet to hash for). It belongs only
to an actual experiment run or an explicit artifact-verification step.

## Clean-tree policy: candidate vs. non-candidate runs

A candidate scientific run refuses a dirty Git working tree by default
(`argine research2 run <ID>` returns `REFUSED_DIRTY_TREE`). Passing
`--allow-dirty` lets a dirty-tree run proceed for development/debugging,
but the run is then permanently marked `NON-CANDIDATE` in its provenance.
A dirty run must never silently become candidate scientific evidence.
`status`, `doctor`, and `audit` may inspect a dirty tree freely; only the
run path gates on it.

## Raw evidence separation

Tracked roadmap/protocol/baseline configuration under `research2/` is
immutable research planning. Mutable execution state (per-experiment state,
the run registry, run directories with raw evidence) lives outside Git,
under the evidence root (`%LOCALAPPDATA%\Argine3\research` by default,
overridable with `ARGINE3_RESEARCH_ROOT`). Normal experiment execution must
never dirty the repository.

## Reproducibility

Every run's provenance records enough to re-derive what produced a result
(commit, environment, parameters, artifact identity) without re-running the
30B/80B model, matching the Research Part 1 convention in
`argine/research/fingerprint.py`.

## Experiment state machine

States: `PLANNED, IMPLEMENTED, READY, RUNNING, COMPLETED, INCONCLUSIVE,
FAILED, BLOCKED` (`research2/protocol.json`). Transitions are validated
against an explicit table and fail closed: an edge not in the table (e.g.
`RUNNING -> PLANNED`) is rejected.

## Scientific PASS / FAIL / INCONCLUSIVE, and what COMPLETED does not mean

`COMPLETED` means the run's process finished without a fatal failure. It is
**not** a scientific verdict. `INCONCLUSIVE` is a first-class run outcome,
distinct from `FAILED` (a raw run/process failure). A scientific
PASS/FAIL/INCONCLUSIVE verdict is recorded separately by an experiment's own
analysis step and is never inferred from the state machine or from a
process exit code alone. No causal claim is drawn from observational
correlation alone; see each experiment's own README for how it guards
against that once designed.

## R2Trace v1 (introduced by R2-01)

R2-01 (`argine/research2/r2_01/`) defines "R2Trace v1": a versioned,
schema-validated canonical representation built directly on top of
Research Part 1's existing "argine-router-trace" v1 format
(`argine.research.trace.Trace`/`Step`), reused rather than replaced.
Reuse, not reimplementation, is deliberate throughout R2-01: Research
Part 1's `argine.research.{trace,replay,policies,oracle,storereplay}`
already implement event-atomic deterministic replay, byte-based unequal
expert-size capacity, causal/non-causal policy separation, and cold/warm
start handling, pinned by 18 existing fidelity tests
(`tests/test_fidelity.py` in Argine2) -- R2-01 does not duplicate any of
that logic.

### Routing-step unit and expert-order semantics

One `Step` is one forward-pass/graph/ubatch. `Step.experts(layer)` is the
**union** of experts a layer needs for that step (an "event"); members of
an event must be resident simultaneously. R2-01's normalizer
(`trace_contract.normalize`) never flattens the per-token expert
selections recorded inside `Step.routes[layer]` -- each token's selected
experts remain grouped exactly as recorded, and the union is only derived
at replay/analysis time, never stored as a substitute for the grouping.

### Capacity semantics

Byte-based (`trace.meta.expert_bytes[layer]`), not a uniform per-expert
count, matching the real retention problem. When a layer's byte metadata
is missing, R2-01 records that layer in
`NormalizationReport.layers_missing_expert_bytes` rather than inventing a
size or an "atomic load exceeds budget" exception; this is one of the
documented conditions that can make R2-01's outcome `INCONCLUSIVE`. The
replay engine (Research Part 1's `replay()`, reused) never permits
resident bytes to exceed the configured budget.

### Initial-residency semantics

Recorded explicitly as one of: `cold_start_documented` (the trace's first
segment is a `"warmup"` segment, matching Research Part 1's canonical
cold-start convention) or `unknown_cold_assumed` (no warmup segment is
present, so a true cold start cannot be distinguished from missing
information). The second case is never silently treated as equivalent to
the first; it is a documented limitation that can drive an
`INCONCLUSIVE` outcome.

### Normalization pipeline and trace integrity

`source trace -> validated source adapter -> canonical R2Trace v1 ->
deterministic replay`. Every normalization computes and records: source
path, SHA-256, byte size, source row count, normalized step count,
excluded-row count with categorized exclusion reasons (never a silent
drop), and the canonical normalized-trace digest (SHA-256 over
sorted-key JSON of the deterministic fields only). `reconcile()` fails
if source and normalized counts do not account for each other exactly.
Rows that should carry routing evidence but cannot be interpreted fail
closed (`ContractError`), including: malformed/unparseable source,
non-strictly-increasing step ordering, and any expert id outside
`[0, n_experts)`.

### Deterministic replay result contract

`replay_contract.run_contract_replay()` separates deterministic scientific
fields (schema versions, trace digest, policy id, budget, routing-step
count, expert-request count, hit/miss/eviction counts, bytes loaded,
maximum resident bytes, deterministic replay digest) from volatile
metadata (wall-clock timestamps). The deterministic fields are stable
across repeated executions on identical inputs; latency is never
fabricated from byte counts or an assumed bandwidth.

### Causal / non-causal policy separation

`policy_interface.assert_causal()` / `assert_non_causal()` structurally
verify Research Part 1's existing separation: a causal policy
(`needs_future=False`, e.g. `GlobalLRU`) is driven only by per-step
`access()` calls in strict step order and never receives future
information; a non-causal/Oracle policy (`needs_future=True`, e.g.
`BeladyGlobal`) receives the full event list and next-use map only at
construction time -- the single sanctioned path for future information.
R2-01 validates this separation exists and holds; it does not implement
the Oracle algorithm itself or compare it against anything (R2-02's job).

### Multi-level validation

- **Level A** (schema): synthetic fixtures -- valid traces accepted;
  malformed, unknown-schema, invalid-ordering, and invalid-expert-ID
  traces rejected.
- **Level B** (determinism): identical canonical trace + config yields an
  identical deterministic replay digest across repeated executions.
- **Level C** (normalization fidelity): source and normalized counts
  reconcile exactly, with every exclusion categorized.
- **Level D** (behavioral replay validation): performed only if a source
  trace carries independently recorded resident/hit/miss/eviction state to
  compare replay against. No locally available real candidate source
  carries that independent state, so Level D is `NOT_OBSERVABLE` for any
  run against those sources; `classify_outcome()` treats that as a factor
  that, combined with any other limitation, drives `INCONCLUSIVE` rather
  than `PASS` -- structural fidelity (A-C) alone is not treated as
  sufficient for `PASS`.

### Scientific outcome criteria

Defined in `orchestrator.classify_outcome()` before any candidate data is
read, and applied mechanically: PASS / INCONCLUSIVE / FAIL as described in
`experiments/R2-01-oracle-trace-validation/README.md`. A missing or
ambiguous trace source (`trace_source.TRACE_SOURCE_MISSING` /
`TRACE_SOURCE_AMBIGUOUS`) is a run blocker, never a scientific `FAIL`, and
creates no evidence directory.

### Known limitation: `candidate_status` fail-closed on unverifiable git state

`provenance.candidate_status()` and `runner.run_experiment()`'s dirty-tree
gate both treat an **unverifiable** git state (the `git status` subprocess
unavailable or timed out -- observed in practice to happen transiently
against this repository over the remote device bridge) the same as a
confirmed-dirty tree: never `CANDIDATE`, only `NON-CANDIDATE` (with
`--allow-dirty`) or `REFUSED` (without it). Only a tree **positively**
verified clean is ever marked `CANDIDATE`.

## R2-01A: trace-source recovery and fresh-capture readiness

Introduced to resolve R2-01's own documented blocker (no locally-present
canonical trace). The decision procedure is strict priority order, never
a guess:

1. **Recover the exact historical trace.** Only accepted if its
   experiment/run identity, byte size, and SHA-256 all match what the
   repository's own documentation records (`trace_source.
   DOCUMENTED_CANONICAL_EXP019`). `trace_source.register_source(...,
   source_type="historical_canonical")` enforces this exactly --
   `SourceRegistrationError` on any mismatch, never a silent accept.
2. **A scientifically equivalent alternative, if explicitly documented as
   such.** None exists in this repository today (every SMOKE run is
   explicitly labelled not publication-quality).
3. **A fresh capture**, only once 1 and 2 are exhausted. `capture.py`'s
   `PROTOCOL` dict reconstructs the documented EXP-019 protocol from
   `experiments/EXP-019-instrumentation-validation/README.md`;
   `validate_protocol()` fails closed (`CAPTURE_PROTOCOL_INCOMPLETE`) if
   any essential field is ever missing, rather than guessing a value.
   `plan_capture_command()` builds the exact `argine research trace`
   invocation; nothing about tracing, the retention algorithm, or HoPi is
   reimplemented.

### Source registration

A verified source (historical or freshly captured) is registered via
`argine.research2.store.set_trace_source_registration()` -- local state,
outside Git, merged into (never clobbering) the experiment's existing
state. `trace_source.register_source(..., source_type="fresh_capture")`
is self-verified (the capture hashes its own output immediately).
`orchestrator.run()` prefers a registration's path over the legacy plain
`trace_source` string when both are present, so a future `run R2-01`
needs no `--trace-source` argument once a source is registered. A smoke
run or the synthetic fixture can never reach this path: registration
itself verifies identity/size/hash before persisting anything, regardless
of what a caller claims about a file.

### Status extension

`status_report()` stays cheap (no hashing, no model/runtime startup):
each experiment may register a `EXPERIMENT_STATUS_EXTRA` callable (the
same deferred-import pattern as `EXPERIMENT_RUN_IMPLEMENTATIONS`); R2-01's
reports `trace_source_status`, `trace_source_type`, `trace_source_runnable`,
and `trace_source_blocker` by calling `trace_source.resolve()`, which only
stats local candidate paths and never hashes a multi-gigabyte trace.
