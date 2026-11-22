# R2-02 -- Oracle vs Retention Baselines

**Phase:** II-A
**Depends on:** R2-01

## Scientific purpose

On the fixed, already-validated R2-01 real candidate trace, at the fixed
4 GiB expert-memory budget: how much better can a non-causal,
future-aware (Oracle) retention policy perform than causal retention
baselines? This is an offline retention/replay comparison quantifying the
theoretical Oracle gap under identical trace, expert sizes, byte capacity,
initial state, and event ordering.

R2-02 is explicitly **not** a runtime-performance experiment. It makes no
latency, throughput, wall-clock, energy, or hardware-performance claim
anywhere. It also does not sweep budgets (R2-03's job) and does not make
the final "recoverable margin" claim (R2-04's job) -- it only produces the
measurements R2-04 will later consume.

## Implementation status (NOT a result)

**IMPLEMENTED.** The policy set, generalized deterministic replay
contract, Oracle-gap/sharding-decomposition metrics, and orchestration
described below are implemented and unit-tested against synthetic
fixtures (`tests/test_r2_02_oracle_baselines.py`). **No real candidate
scientific run has been executed against this repository's committed
state.** This README describes what the implementation does, not what
any run found. See "Running" below, and the experiment's own run evidence
(outside Git, under the Research Part 2 evidence root) for any actual
scientific outcome once a human runs it.

## What this experiment does NOT do

- Does not perform a new 30B capture, mutate the R2-01 trace, or silently
  switch to a different trace discovered elsewhere.
- Does not sweep the budget (R2-03's job); the budget is a single fixed
  4 GiB constant.
- Does not make the final "recoverable margin" claim (R2-04's job).
- Does not invent an underspecified LFU variant, or a shard-constrained
  Oracle that does not already exist, validated, in Research Part 1.
- Does not load any model weights or invoke any runtime binary.
- Does not modify or overwrite R2-01's own canonical PASS evidence, and
  does not modify `argine/research2/r2_01/replay_contract.py`.

## The R2-01 PASS prerequisite and trace-SHA binding

`argine.research2.r2_02.prerequisite.require_r2_01_pass()` is the
fail-closed gate every R2-02 run passes through first: it reads R2-01's
own registry (read-only), finds R2-01's **latest PASS** run, reads that
run's own `source-trace.json` evidence to learn exactly which trace file
it validated (path + sha256 + byte size), and **re-hashes that file now**.
If R2-01 has never run, has not reached PASS, its evidence is missing, the
trace file is gone, or the file's current hash no longer matches what
R2-01's PASS run recorded, R2-02 refuses to run at all (outcome
`R2_01_PREREQUISITE_NOT_MET`) -- no run directory, no registry entry, no
evidence of any kind is created for a run that never began. No trace
SHA-256 is hard-coded anywhere in this module: R2-02 binds dynamically to
whichever trace R2-01's own latest PASS run actually validated.

## The declared policy set

Reuses Research Part 1's existing, already-validated policy classes
verbatim (`argine.research.policies`, `argine.research.oracle`) -- no new
policy or replay semantics are invented. See
`argine/research2/r2_02/policy_set.py` for the full rationale.

| Label | Causal | Reuses |
| --- | --- | --- |
| `Runtime-Sharded-LRU` | yes | `ShardedLRU(shards=16, seed=0)` -- shard-COUNT match to the real runtime's own 16-shard architecture at 4 GiB (R2-01B), not an exact address-hash mapping match |
| `Global-LRU` | yes | `GlobalLRU()` |
| `LFU` | yes | `LFU(halflife=0, in_cache_only=False)` -- fully deterministic semantics already validated in Part 1 |
| `Oracle-Global-Belady` | **no** | `BeladyGlobal(trace, phase=decode)` -- built from the trace's FULL future event list at construction time; never deployable |

No additional, underspecified LFU variant is included merely to raise the
policy count (section 2's own instruction: "prefer scientific clarity over
baseline count").

## Sharding treatment (read before interpreting any Oracle-gap number)

Research Part 1 defines a **global** Belady oracle and a **per-layer**
one, but no **per-shard** oracle. R2-02 therefore cannot build a true
Sharded-LRU-vs-Sharded-Oracle / Sharded-Oracle-vs-Global-Oracle
decomposition without inventing an unvalidated oracle -- which it
deliberately does not do. Instead, `comparison.sharding_decomposition()`
uses **Global-LRU as the shared pivot** between two gaps that are reported
**separately, never merged**:

- **structural (sharding) gap** = `Runtime-Sharded-LRU` vs `Global-LRU`
  (both causal, both zero future knowledge -- isolates the cost of the
  runtime's own sharded architecture alone);
- **policy-information gap** = `Global-LRU` vs `Oracle-Global-Belady`
  (both global cache scope -- isolates the cost of imperfect, causal
  prediction alone);
- **total observed upper-bound gap** = `Runtime-Sharded-LRU` vs
  `Oracle-Global-Belady` (the full observed gap against the real
  runtime's own architecture).

Any number here that does not clearly separate these three is being
misread -- the whole point of this decomposition is to prevent attributing
the runtime's own sharding cost to "prediction headroom."

## Predeclared metrics

**Primary, per policy:** total expert requests, hits, misses, miss rate,
miss bytes, evictions, peak resident bytes, budget violations (always 0 --
a nonzero value is structurally impossible since the replay itself raises
on any invariant violation), deterministic replay digest.

**Primary, Oracle gap vs. each causal baseline:** miss reduction, relative
miss reduction, miss-byte reduction, relative miss-byte reduction. Miss
bytes are treated as at least as important as miss count, since expert
sizes differ across layers.

**Predeclared limitation:** `argine.research.replay.ReplayResult` has no
eviction-BYTES field, only an eviction COUNT; `eviction_bytes` is reported
as `null` everywhere, never estimated or invented.

## Scientific outcome criteria (defined before any candidate run)

- **PASS**: the trace is R2-01's validated candidate (prerequisite +
  SHA-binding hold), every declared policy replays deterministically
  (verified by two independent replays per policy), zero budget
  violations, the Oracle is non-causal by construction and every baseline
  is structurally causal, and the Oracle is no worse than every causal
  baseline it theoretically dominates. **A tiny or zero Oracle gap is
  still PASS** -- PASS never requires a large gap, and a null result is
  never hidden or reinterpreted.
- **INCONCLUSIVE**: a baseline's semantics cannot be unambiguously
  defined, trace metadata needed for a fair comparison is missing (e.g.
  layers without byte-level expert-capacity metadata -- the exact caveat
  R2-01B already flagged as relevant here), or the Oracle comparison is
  not valid under the current replay model.
- **FAIL**: deterministic replay breaks, the capacity invariant is
  violated, a causal baseline leaks future information, the Oracle
  produces logically inconsistent results (worse than a baseline it
  theoretically dominates), repeated identical replay produces different
  digests, or a validated R2-01 assumption is contradicted (e.g. an
  unexplained trace-SHA mismatch).

## Running

```
argine.bat research2 run R2-02
```

Automatically verifies R2-01 PASS, binds the validated R2-01 trace,
replays every declared policy at the fixed 4 GiB budget, computes Oracle-
gap and sharding-decomposition metrics, classifies the outcome, and
writes evidence (`summary.json`, `policy_results.json`, `comparison.json`,
`provenance.json`) under the Research2 evidence root. Loads no model
weights and invokes no runtime binary.

```
argine.bat research2 analyse R2-02
```

Regenerates the summary from the latest completed run's existing
evidence; never reruns the comparison.

## Explicit statement

**No result exists yet for R2-02.** No scientific conclusion, measurement,
or claim should be inferred from the presence of this directory or its
implementation. R2-03 (budget sweep) and R2-04 (recoverable-margin claim)
remain locked until a human runs R2-02 and reviews its evidence.
