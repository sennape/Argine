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
  compare replay against. `classify_outcome()` treats the absence of that
  state as a factor that, combined with any other limitation, drives
  `INCONCLUSIVE` rather than `PASS` -- structural fidelity (A-C) alone is
  not treated as sufficient for `PASS`. As of R2-01B (below), Level D is
  `OBSERVED_PASS` / `OBSERVED_FAIL` whenever the resolved trace source's
  own capture directory carries a format-v2 `raw.tsv` sibling, and
  `NOT_OBSERVABLE` otherwise (format v1, or no `raw.tsv` at all -- e.g. the
  synthetic fixture and SMOKE runs).

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


## R2-01B: closing the Level-D behavioral-observability gap

R2-01's first real candidate run (`c9d55e46733c4ac584ea74c12f1cc606`,
source capture `286a41477ce54d5fb9ccc24f4e508f22`) reached Levels A-C at
PASS-quality but Level D `NOT_OBSERVABLE`, because `classify_outcome()`
had no behavioral evidence wired in at all (`behavioral_observable` was a
hardcoded `False`). R2-01B closes this gap using existing instrumentation
and existing, already-validated tooling -- no runtime source change, no
second real 30B capture.

### What was actually missing

Nothing in the runtime or in capture. `argine research trace` (the exact
command `capture.py` already invokes for R2-01) already writes, in the
same run directory as `trace.jsonl.gz`:

- `raw.tsv` -- the runtime's own raw research log (`argine-raw` format;
  `argine2-research-v2` carries an exact per-shard recorded mapping and
  sequence numbers, never a statistical hash-based approximation);
- `evictions.csv`, `markers.jsonl`, `io.csv`, `nodes.csv`,
  `graph_timing.csv`, `summary.json` -- all produced by
  `argine.research.runner` (Research Part 1), independently of the route
  trace replay is validated against.

Research Part 1 also already contains a complete, independent
runtime-vs-replay fidelity checker (`argine.research.fidelity`, fed by
`argine.research.rawlog`/`storereplay`/`replay`), exercised by its own
EXP-021 closure tool, `tools/validate_replay_fidelity.py`. That tool reads
exactly this directory layout and predeclares its own acceptance
criteria (`WORKLOAD`, `FULL_TRACE` tuples) -- zero tolerance, defined
before R2-01 ever existed, for EXP-021's own purposes. Classification:
**EXISTING_TELEMETRY_SUFFICIENT.** The gap was purely that nothing in
Research Part 2 looked at these sibling files; `behavioral_fidelity.py`
is the whole fix.

### Independence / non-circularity

Levels A-C validate the trace/replay *contract* (does a deterministic
replay of the recorded route trace reconcile and digest stably?) using a
simplified `GlobalLRU` counterfactual policy -- this was never intended to
reproduce the real cache's architecture, only to exercise the replay
mechanism itself. Level D instead compares the REAL runtime's own
recorded cache events (`raw.tsv`'s S/A/I/E records: what the C++ expert
cache actually did) against an INDEPENDENT `StoreReplay` prediction fed
only the runtime's own request stream and its exact recorded 16-shard
mapping (`ShardedLRU(16)` / `RecordedShardMap`, matching
`argine.research.storereplay.runtime_shard_count()`'s real
`clamp(budget // 32MiB, 1, 16)` architecture -- see "16 cache shards"
below). Route information alone never enters this comparison as the
thing being validated; the replay's own predicted hit/miss/evict/victim
decisions are checked against the runtime's own INDEPENDENTLY recorded
decisions, event by event. This is not circular.

### 16 cache shards

The real runtime shards its expert cache (`runtime_shard_count`: up to 16,
`budget // 32MiB` clamped), while Part 2's own `GlobalLRU` (Levels A-C)
models a single global cache. This is not a mismatch Part 2 hides: Levels
A-C and Level D are deliberately different checks on different axes
(contract/structural vs. real-behavioral), and Level D uses Part 1's
already shard-correct `ShardedLRU`/`RecordedShardMap`, never `GlobalLRU`.

### Level-D acceptance criteria (predeclared, reused verbatim)

`behavioral_fidelity.evaluate()` imports `tools.validate_replay_fidelity
.one()` and its `WORKLOAD` criterion tuple directly -- never redefines or
loosens them:

```
reconstruct_inconsistencies == 0
shards_with_replay_relevant_seq_gaps == 0
incomplete_nodes_in_workload == 0
wl_disagreements == 0
wl_victim_disagreements == 0
wl_skip_count_disagreements == 0
pre_workload_disagreements == 0
pre_workload_skip_count_disagreements == 0
wl_rt_misses == wl_rp_misses
wl_rt_miss_bytes == wl_rp_miss_bytes
wl_rt_evictions == wl_rp_evictions
rp_budget_violations == 0
wl_rp_failures == 0
pre_workload_rp_failures == 0
```

These were predeclared by Research Part 1 for EXP-021's own closure,
before this R2-01 capture ever existed -- R2-01B does not choose or tune
any threshold after seeing a result. The committed `exp021_closure` field
additionally requires zero *unexplained* tail disagreement after the
measured workload (`tail_disagreements_unexplained == 0`); R2-01B reuses
that exact field as its own strict PASS gate, matching this contract's own
zero-tolerance policy (discrete state/event validation, not a noisy
measurement).

- **`OBSERVED_PASS`**: `raw.tsv` is format v2 and `exp021_closure == "PASS"`.
- **`OBSERVED_FAIL`**: `raw.tsv` is format v2 and evaluation completed but
  `exp021_closure != "PASS"` (any criterion above violated, or an
  unexplained tail disagreement).
- **`NOT_OBSERVABLE`**: no `raw.tsv`/`trace.jsonl.gz` sibling, format v1
  (statistical shard mapping only -- insufficient for exact event-level
  comparison), or the evaluation itself raised (malformed telemetry) --
  always fails closed, never guessed.

### Classification change in `classify_outcome()`

- `level_d` `NOT_OBSERVABLE` (or absent): unchanged pre-existing behavior
  (`INCONCLUSIVE`, with the original explanatory reason).
- `level_d` `OBSERVED_FAIL`: always `FAIL`, appended to any existing A-C
  reasons -- a genuine behavioral disagreement is fatal regardless of how
  clean Levels A-C were.
- `level_d` `OBSERVED_PASS` with no other A-C reasons: `PASS`.
- `level_d` `OBSERVED_PASS` but A-C still carries a structural caveat
  (e.g. `layers_missing_expert_bytes`, `unknown_cold_assumed`):
  `INCONCLUSIVE`, not `PASS` -- an independently observed behavioral match
  does not retroactively fix an unrelated structural-metadata gap, and
  R2-01B never forces `PASS` by relaxing a limitation it did not actually
  close.

### Provenance

`behavioral_fidelity.evaluate()`'s full result is written into
`validation.json`/`summary.json` under `level_d` for every R2-01 run
(`NOT_OBSERVABLE` included) so a run's Level-D status is always visible,
not just implied by the outcome. No second real capture is created or
required by R2-01B: it reads the already-registered capture's own sibling
files, so there is no new `capture_run_id` to bind -- the existing
`capture_provenance` binding (R2-01A) already identifies which capture
produced both `trace.jsonl.gz` and the `raw.tsv` R2-01B reads.

## R2-02: Oracle vs Retention Baselines

An offline retention/replay comparison, on R2-01's already-validated real
candidate trace at a fixed 4 GiB expert-memory budget: how much better can
a non-causal, future-aware (Oracle) retention policy perform than causal
retention baselines. Implemented in `argine/research2/r2_02/`. See
`experiments/R2-02-oracle-baselines/README.md` for the full scientific
design, policy definitions, sharding-decomposition treatment, metrics, and
outcome rules; this section records the implementation-level contract.

### R2-01 PASS prerequisite and trace-SHA binding

`r2_02.prerequisite.require_r2_01_pass()` reads R2-01's own registry
(read-only -- R2-01's evidence is never modified) to find R2-01's latest
`PASS` run, reads that run's `source-trace.json` to learn the exact trace
file (path/sha256/byte_size) it validated, and re-hashes that file now.
Any divergence (no R2-01 run, R2-01 not `PASS`, ambiguous latest `PASS`,
missing evidence, missing trace file, or a hash/size mismatch against
what R2-01 recorded) returns a non-`OK` `R201Binding` and R2-02's own
`run()` refuses atomically (`R2_01_PREREQUISITE_NOT_MET`) -- no run
directory, no registry entry, no state change. No trace SHA-256 literal
is hard-coded anywhere in this module; the "validated trace" is always
whichever one R2-01's own latest PASS run actually bound to.

### Generalized deterministic replay contract

`r2_01.replay_contract.py` is R2-01's own canonical-evidence code path,
hardcoded to exactly one policy (`GlobalLRU`), and remains completely
unmodified (R2-01 immutability). `r2_02.replay_multi.run_policy_replay()`
reuses its `_digest`/`_deterministic_subset`/`DeterministicReplayResult`
building blocks directly (import, never copy) and adds the one thing
R2-01's version does not need: an arbitrary policy parameter, with the
causal/non-causal assertion (`r2_01.policy_interface`) appropriate to that
policy's own declared metadata.

### Determinism verification

Section 16's "two independent analysis runs produce byte-identical
canonical digests" requirement is verified **within a single R2-02 run**:
`orchestrator.run()` replays every declared policy **twice**, from two
freshly constructed policy instances, and requires the two
`deterministic_replay_digest` values to match exactly before accepting
that policy's result row. A mismatch is `FAIL` ("repeated identical
replay produces different metrics/digests" -- section 14).

### Outcome classification

`orchestrator.classify_outcome()` applies section 14's criteria
mechanically: reconciliation problems or a replay/policy-interface error
or non-determinism all force `FAIL`; a missing policy result (malformed
output) forces `INCONCLUSIVE`; a hits+misses accounting mismatch or a
capacity violation forces `FAIL`; an Oracle that performs worse than a
causal baseline it theoretically dominates forces `FAIL`
("logically inconsistent results"); missing byte-level expert-capacity
metadata or an undocumented cold-start assumption force `INCONCLUSIVE`
(the exact caveat R2-01B already flagged as relevant to R2-02's own
byte-based metrics); otherwise `PASS` -- **never contingent on the size of
the Oracle gap**, so a null (zero-gap) result is reported as a valid PASS,
never hidden or reinterpreted.

### Evidence

Each run writes `manifest.json`, `provenance.json` (the R2-01 binding plus
the normalized trace identity), `policy_results.json`, `comparison.json`
(Oracle gaps + sharding decomposition), `summary.json`, and `summary.md`
under the Research2 evidence root, and appends one registry entry --
exactly the same evidence architecture R2-01 established, no new one
invented.

## R2-03: Expert-Memory Budget Sensitivity

Replays R2-02's declared policy set, on the same R2-01-validated real
trace, across a small, fixed, predeclared set of expert-memory budgets,
to test whether the Oracle-vs-causal-retention gap R2-02 observed at
4 GiB is robust. Implemented in `argine/research2/r2_03/`. See
`experiments/R2-03-budget-sensitivity/README.md` for the full scientific
design, the chosen budgets and their rationale, metrics, and outcome
rules; this section records the implementation-level contract.

### R2-01 + R2-02 prerequisite and trace-binding consistency

`r2_03.prerequisite.require_r2_01_and_r2_02_pass()` reuses (never
duplicates) `r2_02.prerequisite.require_r2_01_pass()` for the R2-01 half
of the check, then separately verifies R2-02's own latest run reached
`PASS` and that R2-02's own recorded trace binding (from its
`provenance.json`) agrees with R2-01's current one. Any divergence
refuses the run atomically (`R2_01_OR_R2_02_PREREQUISITE_NOT_MET`).

### Predeclared budget sweep

`r2_03.budget_sweep.FIXED_BUDGETS_BYTES` is a fixed, five-point,
strictly-increasing tuple of exact byte capacities (module-level
constants, asserted unique and sorted at import time): 1.5 / 2 / 4 / 8 /
16 GiB. 4 GiB is `REPRODUCTION_BUDGET_BYTES`, asserted to be a member of
the sweep. Nothing in `orchestrator.run()` accepts a budget parameter --
the sweep is not swept further at runtime.

### R2-02 reproduction check

`r2_03.reproduction.check()` compares the sweep's own 4 GiB point against
R2-02's own registered `policy_results.json` (read dynamically via the
R2-02 run id the prerequisite binding resolved, never a hard-coded
expected-value literal), for `misses` and `miss_bytes` on `Global-LRU`
and `Oracle-Global-Belady`. `orchestrator.classify_outcome()` evaluates
this check FIRST: any mismatch is `FAIL` and short-circuits every other
rule, per the task's own "any mismatch blocks interpretation of the
sweep" instruction.

### Replay and curves

Reuses `r2_02.replay_multi.run_policy_replay()` and
`r2_02.comparison.{policy_row,oracle_gap}` verbatim for every
(budget, policy) pair, each replayed twice (the same independent-replay
determinism check R2-02 established). `r2_03.curves.py` reshapes the
resulting rows into the three predeclared curves (primary: relative
Oracle miss-byte reduction vs budget; secondary: Global-LRU and Oracle
miss rate vs budget) -- no new metric semantics.

### Evidence

Each run writes `manifest.json`, `provenance.json` (the R2-01+R2-02
binding plus the normalized trace identity), `budget_results.json`,
`comparison.json` (reproduction check + gap curves), `summary.json`,
`summary.md`, and a flat `budget_results.csv` for later plotting, under
the Research2 evidence root, and appends one registry entry -- the same
evidence architecture R2-01/R2-02 established, no new one invented.

## R2-04: Recoverable-Margin Analysis

A pure offline SYNTHESIS over already-completed R2-01/R2-02/R2-03
evidence -- no new replay, no model/runtime invocation, no new trace
capture. Decomposes R2-03's Oracle-vs-causal-retention gap, at every
R2-03-declared budget, into a STRUCTURAL SHARDING GAP (Runtime-Sharded-
LRU vs Global-LRU) and a POLICY-INFORMATION HEADROOM (Global-LRU vs
Oracle-Global-Belady); their sum is the TOTAL OBSERVED UPPER-BOUND GAP
(Runtime-Sharded-LRU vs Oracle-Global-Belady). Implemented in
`argine/research2/r2_04/`. See
`experiments/R2-04-recoverable-margin/README.md` for the full scientific
design and terminology contract; this section records the
implementation-level contract.

**Critical distinction, enforced throughout:** the POLICY-INFORMATION
HEADROOM is a THEORETICAL/INFORMATIONAL upper bound, never a demonstrated
causally recoverable performance figure. No causal predictor exists yet
(II-C's job). "Causally recovered margin" is always reported literally as
`NOT_YET_MEASURED -- II-C`.

### R2-01 + R2-02 + R2-03 prerequisite and binding consistency

`r2_04.prerequisite.require_r2_01_r2_02_r2_03_pass()` reuses (never
duplicates) `r2_03.prerequisite.require_r2_01_and_r2_02_pass()` for the
R2-01+R2-02 half, then separately verifies R2-03's own latest run reached
`PASS`, that R2-03's own recorded binding (`provenance.json`'s
`binding.trace_sha256` / `r2_01_run_id` / `r2_02_run_id`) agrees with the
CURRENT R2-01/R2-02 binding, and that R2-03's own registered
`comparison.json.reproduction_check.ok` still reads `true`. Any
divergence refuses the run atomically
(`R2_01_R2_02_OR_R2_03_PREREQUISITE_NOT_MET`), with no evidence written.
`fixed_budgets_bytes` and `reproduction_budget_bytes` are read from R2-03's
own `manifest.json`/`provenance.json` -- never from today's
`r2_03.budget_sweep` module constants -- so R2-04 binds to what the
CANONICAL R2-03 RUN actually used.

### Decomposition

`r2_04.decomposition.decompose_budget()` computes all three gaps by
calling `r2_02.comparison.oracle_gap()` three times per budget:
`oracle_gap(Sharded, GlobalLRU)` (structural), `oracle_gap(GlobalLRU,
Oracle)` (information), `oracle_gap(Sharded, Oracle)` (total) -- no new
numeric formula is invented. The absolute miss-count and miss-byte
differences are asserted to sum exactly (`structural + information ==
total`); a violation raises `ImpossibleValuesError`, an unconditional
R2-04 `FAIL`. The relative fractions (section 8) are intentionally never
summed, since their denominators differ (a documented, not hidden,
property). The structural gap may legitimately be negative (sharding
itself reducing misses relative to Global-LRU, observed at the real
evidence's 1.5/2 GiB points) -- this is a valid result, not an error.

### Regime classification

`r2_04.regimes.py`: `capacity_constrained_budgets()` (Global-LRU
evictions > 0), `effective_residency_budget()` (first tested budget,
ascending, where Global-LRU and Oracle have equal misses, equal miss
bytes, and zero evictions each -- reported only as the first *tested*
budget exhibiting convergence, never claimed minimal), `peak_opportunity()`
(four maxima over the information-headroom gap, determined independently:
max relative/absolute miss-byte headroom, max relative/absolute miss
headroom -- never assumed to coincide), `headroom_persistence()` (whether
Oracle strictly improves over Global-LRU at every capacity-constrained
budget, excluding the effective-residency budget per its own exclusion
rule), and `classify_curve_shape()` (a deterministic classifier over the
primary relative-information-headroom-fraction curve: plateau ->
monotonic increasing/decreasing -> collapse-at-effective-residency (the
curve rises to its predecessor then collapses to its global minimum
exactly at the observed effective-residency budget) -> single/multiple
interior maxima -> other/non-monotonic). No regime or curve shape is
assumed ahead of time; the classifier is applied mechanically to whatever
the fixed tested points actually show.

### LFU (secondary only)

`r2_04.lfu_summary.lfu_vs_global_lru()` reports, per budget, whether LFU
beats, loses to, or ties Global-LRU on miss count. LFU plays no role in
the structural/information/total decomposition.

### Claims and the II-C future metric

`r2_04.claims.build_claims()` produces an explicit `supported_now` /
`not_yet_supported` split (section 20), plus `FUTURE_METRIC_II_C`: the
exact, documented-but-never-computed formula
`fraction_of_oracle_headroom_recovered = (causal_baseline_miss_bytes -
predictor_miss_bytes) / (causal_baseline_miss_bytes - oracle_miss_bytes)`.
`claims.check_no_forbidden_claims()` is an automated, defense-in-depth
scan of every `supported_now` claim's text against a forbidden-phrase
list (e.g. "can be recovered", "faster inference", "lower latency") --
a violation raises `ForbiddenClaimError`, an unconditional `FAIL`
(section 18: "a generated claim incorrectly presents Oracle headroom as
demonstrated causal recovery").

### Determinism

`orchestrator._synthesize()` is a pure function of
`(budget_results, fixed_budgets_bytes, reproduction_budget_bytes)`.
`orchestrator.run()` calls it twice against the identical loaded R2-03
evidence and compares a canonical SHA-256 digest (sorted-key JSON, no
volatile run ids/timestamps included in the digested payload); a mismatch
is an immediate `FAIL` ("nondeterministic synthesis") -- the same
determinism-by-duplication pattern R2-02/R2-03 apply to live replay,
applied here to the synthesis step instead.

### Outcome rules

`orchestrator.classify_outcome()`: any synthesis error (malformed
upstream row, `ImpossibleValuesError`, a forbidden claim) forces `FAIL`;
nondeterminism forces `FAIL`; any R2-03-declared budget missing a
required policy row forces `INCONCLUSIVE`; otherwise `PASS` -- never
contingent on the size of the headroom, so a null (zero-headroom) result
is a valid `PASS`.

### Evidence

Each run writes `manifest.json`, `provenance.json` (the four-way binding
plus the canonical digest), `recoverable_margin.json` (decomposition by
budget, regimes, curve shape, LFU summary, figure data A/B/C),
`claims.json`, `margin_by_budget.csv`, `summary.json`, `summary.md` under
the Research2 evidence root, and appends one registry entry -- the same
evidence architecture R2-01/R2-02/R2-03 established, no new one invented.

### Phase II-A closure

`research2/roadmap.json` carries no literal phase-state field (phases are
immutable, tracked planning metadata only -- see `roadmap.py`'s own
docstring); "II-A CLOSED/PASS" is a narrative conclusion derived from
R2-01/R2-02/R2-03/R2-04 all reaching the `COMPLETED`/`PASS` experiment
state, not something any orchestrator writes as a separate field. A real
R2-04 `PASS` run does **not** automatically advance II-B's state --
II-B's experiments (R2-05..R2-08) remain `PLANNED`/`FUTURE` until a
separate "paper-freeze" step completes, per R2-04's own task instructions.

## R2-05: Expert Frequency and Concentration

The first canonical Research II-B experiment. A fully offline
DESCRIPTIVE CHARACTERIZATION of layer-local expert-routing frequency on
the validated R2-01 trace -- no new replay, no model/runtime invocation,
no new trace capture. Implemented in `argine/research2/r2_05/`. See
`experiments/R2-05-expert-frequency-concentration/README.md` for the full
scientific design. R2-05 answers "how concentrated is expert routing on
the validated real 30B MoE trace?"; it does not answer transition
structure (R2-06), reuse distance (R2-07), temporal drift (R2-08), or
predictability (II-C), and must not be read as doing so.

### R2-01 + R2-04 prerequisite and candidate-status binding

`r2_05.prerequisite.require_r2_01_pass_and_r2_04_pass()` reuses (never
duplicates) `r2_02.prerequisite.require_r2_01_pass()` for the trace-
identity half (re-hashes the trace file R2-01's latest PASS run
validated; refuses if it has changed on disk). It additionally requires
a R2-04 PASS run to exist (Research II-A must be CLOSED/PASS before any
II-B experiment runs), confirms that R2-04's own recorded
`provenance.json.binding.r2_01_run_id` agrees with the CURRENT R2-01
binding, and -- specific to R2-05 -- confirms the R2-01 PASS run's own
recorded `manifest.json.git.candidate_status` is `CANDIDATE`, refusing to
bind to historical evidence that was itself recorded under a dirty/
unverified tree even if its scientific outcome was PASS. Any divergence
refuses the run atomically (`R2_01_OR_R2_04_PREREQUISITE_NOT_MET`), with
no evidence written.

### Request unit (section 6's canonical, documented choice)

`r2_05.request_unit.REQUEST_UNIT_DEFINITION`: one counted expert request
= one occurrence of one expert id within one token's selected-expert
tuple, via `Trace.token_selections(layer, phase)` -- the trace module's
own documented routing-statistics accessor, distinct from the per-step-
per-layer UNION (`Step.experts(layer)`) that R2-01..R2-04 use for cache-
residency accounting. Duplicate expert ids within one token's tuple, if
any, are each counted separately, never deduplicated. A second,
independently-coded computation (`independent_count_per_layer`, walking
`Step.routes` directly) is run and compared against the primary count for
every layer as an explicit reconciliation check (section 19's "request
counts do not reconcile" FAIL condition) -- not merely a repeat of the
same function.

### Frequency table and concentration metrics

`r2_05.frequency.py` builds the primary per-(layer, expert) frequency
table with deterministic ordering (rank descending by count, expert_id
ascending as the tie-breaker). `r2_05.metrics.py` computes, per layer:
Shannon entropy, normalized entropy (against the NOMINAL expert count
when `trace.meta.n_experts` provides one, per section 8's explicit
preference; a missing nominal count makes the whole run `INCONCLUSIVE`
rather than silently falling back to an observed-only N for the headline
result), effective expert count/fraction, HHI, Gini (zero-padded to the
nominal population when nominal N is known, so never-observed experts
correctly contribute to inequality), predeclared top-{1,4,8,16,32} shares
filtered to valid k <= N, the theoretical uniform-routing baseline, and
the Pareto-style `experts_for_{50,80,90,95}pct` concentration counts. No
Zipfian/power-law/heavy-tailed label is ever produced (see Claims below).

### Global summary

`r2_05.summary.global_summary()` reports LAYER-MEDIAN (and min/max) and
REQUEST-WEIGHTED aggregate statistics as explicitly distinct fields
(section 9), plus the most/least concentrated layer (by normalized
entropy -- the single axis documented as the primary concentration
measure, since it alone is bounded to [0, 1] and comparable across
layers regardless of each layer's own N). `layer_heterogeneity()` reports
only a descriptive spread classification, with no causal explanation and
no temporal/workload-drift claim (that is R2-08's).

### Claims and the LFU bridge (sections 15-20)

`r2_05.claims.build_claims()` splits the output into SUPPORTED NOW vs
NOT YET SUPPORTED, with a forbidden-phrase guard
(`check_no_forbidden_claims`) scanning only `supported_now` (mirroring
R2-04's own guard design) for: "zipfian"/"power-law"/"heavy-tailed",
"temporally stable"/"remain hot over time", "frequency solves
retention", "explains the oracle gap", "frequency alone is sufficient".
The LFU bridge note explicitly connects to R2-02: LFU performed WORSE
than Global-LRU at 4 GiB, so even strong observed frequency concentration
does NOT imply "frequency solves retention" -- frequency alone may not
capture the timing information the Oracle exploits, which is exactly
what motivates R2-06/R2-07.

### Outcome classification and determinism

PASS requires: trace identity held, layer-local expert identity
preserved throughout, frequency counts reconciled against the
independent second computation, probability mass per layer summing to
1.0 within tolerance, deterministic rank/tie-breaking, and two
independent in-process syntheses over the identically loaded trace
producing an identical canonical digest (volatile run ids/timestamps
excluded) -- mirroring R2-04's own double-synthesis determinism check. A
uniform or near-uniform result, or a highly concentrated one, are both
valid PASS science; PASS never requires strong concentration.
INCONCLUSIVE covers an unavailable authoritative nominal expert count. A
synthesis error, a digest mismatch between the two syntheses, or a
generated claim tripping the forbidden-phrase guard are each an
unconditional FAIL.

### Evidence and automation

`argine.bat research2 run R2-05` is the single human-facing command: it
verifies the prerequisite, loads the validated R2-01 trace, computes
everything above, classifies the outcome, and persists
`summary.json`/`summary.md`, `expert_frequency.csv`,
`layer_concentration.csv`, `concentration.json`, `provenance.json`,
`claims.json`, and `figure_data.json` under the Research2 evidence root.
No manual data extraction step exists. R2-06/R2-07/R2-08 remain LOCKED
(not auto-started) after a R2-05 PASS; II-C remains FUTURE.
