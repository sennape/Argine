# R2-03 -- Expert-Memory Budget Sensitivity

**Phase:** II-A
**Depends on:** R2-01, R2-02

## Scientific purpose

Is the Oracle-vs-causal-retention headroom R2-02 observed (at a fixed
4 GiB budget) robust across expert-memory budgets, or an artifact of that
one budget? R2-03 replays the exact same R2-02-declared policies on the
exact same R2-01-validated real trace across a small, fixed, predeclared
set of budgets and reports the actual observed shape of the gap -- it
does not assume the gap is monotonic in either direction.

R2-03 is still **not** a runtime-performance experiment (no latency/
throughput/wall-clock/energy/hardware claim anywhere), does not yet make
the final "recoverable margin" claim (R2-04's job -- R2-03 only emits the
canonical per-budget data R2-04 will consume), and does not implement
R2-04 or begin Phase II-B/II-C.

## Implementation status (NOT a result)

**IMPLEMENTED.** The budget sweep, prerequisite chain, reproduction
check, and orchestration described below are implemented and unit-tested
against synthetic fixtures (`tests/test_r2_03_budget_sensitivity.py`).
**No real candidate scientific run has been executed against this
repository's committed state.** This README describes what the
implementation does, not what any run found.

## What this experiment does NOT do

- Does not run llama-server, load the 30B model, or capture another trace.
- Does not implement R2-04, or begin Phase II-B/II-C.
- Does not select budget points after observing results -- the sweep
  below is fixed in code (`argine/research2/r2_03/budget_sweep.py`)
  before any real analysis, and nothing in `orchestrator.py` accepts a
  budget list parameter to sweep further.
- Does not tolerate any drift at the mandatory 4 GiB reproduction point.

## The predeclared budget sweep (chosen before seeing any real result)

Investigated, in order, before choosing:

1. `research2/protocol.json`, `research2/baselines.json`,
   `research2/workloads.json` -- none declares a budget convention (they
   are experiment-state-machine / identifier placeholders).
2. Research Part 1's own canonical budget convention -- used identically
   and repeatedly across EXP-007 (router characterization), EXP-008
   (working-set), EXP-019 (instrumentation validation) and EXP-021
   (replay fidelity) for the 30B model: **4 / 2 / 1.5 GiB**. Reused
   verbatim here, not reinvented.
3. The real R2-01-validated trace's own total expert-working-set size,
   computed once from the trace's own metadata (128 experts x 48 layers):
   **~17,553,162,240 bytes (~16.35 GiB)**. Part 1 never swept a budget
   above 4 GiB for this model, so there is no existing canonical higher
   point to reuse; two higher points were added using Part 1's own
   labeling convention (clean GiB values, doublings of the existing
   4 GiB point): **8 GiB** (roughly half of the real total working-set
   size -- still constrained, far less pressured than 4 GiB) and
   **16 GiB** (at approximately the real total working-set size -- the
   regime where nothing should need eviction, and any residual gap
   should collapse toward zero).

| Budget | Bytes | Source |
| --- | --- | --- |
| 1.5 GiB | 1,610,612,736 | Part 1 canonical (EXP-007/008/019/021) |
| 2 GiB | 2,147,483,648 | Part 1 canonical |
| **4 GiB** | **4,294,967,296** | Part 1 canonical; **mandatory R2-02 reproduction point** |
| 8 GiB | 8,589,934,592 | added: ~half the real total working-set size |
| 16 GiB | 17,179,869,184 | added: ~= the real total working-set size |

## Policies

Reuses R2-02's declared policy set verbatim
(`argine.research2.r2_02.policy_set.DECLARED_POLICIES`) -- no new policy
or replay semantics. All four are evaluated at every budget so the
primary `Global-LRU` vs `Oracle-Global-Belady` comparison is never
obscured by the additional baselines:

- `Global-LRU` (causal) -- the primary causal baseline.
- `Oracle-Global-Belady` (non-causal) -- the primary oracle.
- `Runtime-Sharded-LRU` (causal) -- where scientifically appropriate.
- `LFU` (causal) -- where scientifically appropriate.

## The R2-01 + R2-02 prerequisite and trace-identity binding

`argine.research2.r2_03.prerequisite.require_r2_01_and_r2_02_pass()`
verifies, every run: (1) R2-01's own latest PASS run via
`r2_02.prerequisite.require_r2_01_pass()` (reused, not duplicated --
re-hashes the trace now); (2) R2-02's own latest run has reached PASS;
(3) R2-02's own recorded trace binding (from its `provenance.json`)
agrees, byte-for-byte, with R2-01's CURRENT validated trace. Any
divergence refuses the run atomically (`R2_01_OR_R2_02_PREREQUISITE_NOT_MET`)
-- no run directory, no registry entry, no evidence of any kind.

## The mandatory R2-02 reproduction check

The 4 GiB point of R2-03's own fresh sweep must reproduce R2-02's
already-registered evidence EXACTLY (misses and miss bytes, for
`Global-LRU` and `Oracle-Global-Belady`) -- read dynamically from R2-02's
own `policy_results.json`, never a hard-coded expected-value literal. Any
mismatch is **FAIL** and blocks interpretation of the entire sweep, per
the task's own instruction -- evaluated first, before any other outcome
rule.

## Metrics

Per budget, per policy: requests, hits, misses, miss rate, miss bytes,
evictions, peak resident bytes, budget violations, deterministic digest
(eviction bytes reported as `null` -- the same predeclared `ReplayResult`
limitation R2-02 already documented). Per budget, Oracle vs each causal
baseline: absolute/relative miss reduction, absolute/relative miss-byte
reduction.

**Primary curve:** relative Oracle miss-byte reduction (vs Global-LRU) as
a function of budget. **Secondary:** Global-LRU miss rate vs budget;
Oracle miss rate vs budget. The actual observed shape is reported --
R2-03 does not assume the gap is monotonic, persistent, or collapsing
before seeing the data.

## Scientific outcome criteria (defined before any candidate run)

- **PASS**: the fixed sweep was predeclared, the same R2-01/R2-02-
  validated trace was used, every budget/policy pair replays
  deterministically (two independent replays each), zero capacity
  violations, the 4 GiB point exactly reproduces R2-02, and comparable
  Oracle/LRU metrics exist for every declared budget. **A small, zero,
  non-monotonic, or collapsing Oracle gap is still a valid PASS.**
- **INCONCLUSIVE**: a scientifically necessary budget point cannot be
  evaluated, expert metadata cannot support the sweep, or comparison
  semantics become inconsistent across budgets.
- **FAIL**: the 4 GiB point does not reproduce R2-02, determinism breaks,
  the budget invariant breaks, trace identity changes unexpectedly, or a
  causal policy receives future information.

## Running

```
argine.bat research2 run R2-03
```

Automatically verifies R2-01 PASS, verifies R2-02 PASS, binds trace
identity, loads the fixed sweep, replays every declared policy across
every budget, verifies the 4 GiB reproduction point, classifies the
outcome, and writes evidence (`summary.json`, `budget_results.json`,
`comparison.json`, `provenance.json`, `budget_results.csv`) under the
Research2 evidence root. Loads no model weights, invokes no runtime
binary, captures no new trace.

```
argine.bat research2 analyse R2-03
```

Regenerates the summary from the latest completed run's existing
evidence; never reruns the sweep.

## Explicit statement

**No result exists yet for R2-03.** No scientific conclusion,
measurement, or claim should be inferred from the presence of this
directory or its implementation. R2-04 (the recoverable-margin claim)
and Phase II-B/II-C remain locked until a human runs R2-03 and reviews
its evidence.
