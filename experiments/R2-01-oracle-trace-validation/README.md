# R2-01 -- Oracle Trace Contract and Replay Validation

**Phase:** II-A
**Depends on:** (none; first experiment of Phase II-A)

## Scientific purpose

Establish and validate the trace/replay CONTRACT that later experiments
R2-02..R2-04 will rely on. The question this experiment answers: can the
expert-routing evidence available from Argine be transformed into a
deterministic, semantically well-defined offline replay representation
that preserves the information required for later expert-retention
evaluation?

R2-01 concludes ONLY about trace sufficiency/integrity, replay determinism
and semantic validity, and the contract's suitability for later
experiments. It does NOT, and must never be read to, conclude anything
about Oracle/LRU/LFU performance, routing/working-set predictability, or
latency/throughput improvement -- that is R2-02's job.

## Implementation status (NOT a result)

**IMPLEMENTED.** The contract, resolver, and replay-validation pipeline
described below are implemented and unit-tested against synthetic
fixtures (`tests/test_r2_01.py`). **No real candidate scientific run has
been executed against this repository's committed state.** This README
describes what the implementation does, not what any run found. See
"Current state" below for the precise state-machine status, and the
experiment's own run evidence (outside Git, under the Research Part 2
evidence root) for any actual scientific outcome once a human runs it.

## What this experiment does NOT do

- Does not implement R2-02 or any Oracle/LRU/LFU comparison.
- Does not implement a new eviction/caching policy; reuses Research Part 1's
  `argine.research.{policies,oracle,replay,storereplay}` for policy and
  replay semantics rather than reimplementing them (see METHODOLOGY.md).
- Does not run the 30B model, and does not require it to pass its own tests.
- Does not modify Argine2 (the frozen Research Part 1 baseline) or HoPi.

## R2Trace v1 (the contract)

See `docs/research2/METHODOLOGY.md#r2trace-v1` for the full contract
definition. Summary: R2-01 normalizes a source "argine-router-trace" v1
file (Argine2's existing `argine.research.trace.Trace`/`Step` format,
reused as-is, never flattened) into a schema-validated, digest-stable
"R2Trace v1" via `argine.research2.r2_01.trace_contract.normalize()`, then
validates it with a deterministic offline replay
(`argine.research2.r2_01.replay_contract.run_contract_replay()`, built on
Research Part 1's own `argine.research.replay.replay()` and `GlobalLRU`
policy) and a causal/non-causal policy-interface check
(`argine.research2.r2_01.policy_interface`).

## Trace source resolution

`argine.research2.r2_01.trace_source.resolve()` implements the required
precedence: (1) explicit `--trace-source` CLI argument, (2) Research2
local state (a previously resolved source recorded for this experiment),
(3) a documented canonical historical trace **only if exactly one
plausible real candidate exists**, (4) otherwise `TRACE_SOURCE_MISSING` or
`TRACE_SOURCE_AMBIGUOUS` with every candidate and its plausibility
reasoning. On this machine, discovery found no single unambiguous
candidate (see BLOCKERS in the implementation's final report): the one
source Research Part 1's own documentation names as canonical
(`EXP-019`'s publication-quality Windows validation run) is not physically
present locally, leaving only an explicitly-labelled synthetic fixture and
several explicitly non-publication-quality SMOKE traces -- correctly
producing `TRACE_SOURCE_AMBIGUOUS`/`TRACE_SOURCE_MISSING` by default rather
than guessing.

## Running

```
argine.bat research2 run R2-01
```

If a trace source cannot be unambiguously resolved, this command
terminates cleanly with the blocker and remediation (pass an explicit
`--trace-source PATH`, or place exactly one real candidate where the
resolver expects it) -- no evidence directory is created for a run that
never began. If a source is resolved, this single command runs the full
pipeline (normalize -> validate -> replay -> classify -> write evidence
-> update state) and prints a human-readable summary.

```
argine.bat research2 analyse R2-01
```

Regenerates the deterministic analysis/summary from the latest completed
run's existing raw evidence (or a run named with `--run-id`); never
reruns the model, never mutates the original raw trace.

## Scientific outcome criteria (defined before any candidate run)

- **PASS**: evidence normalizes without unexplained loss, routing-step
  semantics are preserved, deterministic replay is demonstrated, all
  invariants hold, and the contract is sufficient for R2-02.
- **INCONCLUSIVE**: the infrastructure works, but the available real trace
  lacks material information (e.g. no byte-level capacity metadata for
  some layer, or no recorded initial-residency/warmup segment) that cannot
  be resolved without new instrumentation or a fresh capture.
- **FAIL**: the trace/replay representation demonstrably violates required
  semantics, loses material information, is nondeterministic, or produces
  unreconcilable state.

A missing or ambiguous source trace is explicitly **not** a scientific
FAIL -- it is a run blocker (`TRACE_SOURCE_MISSING` / `TRACE_SOURCE_AMBIGUOUS`),
distinct from and prior to any scientific verdict.

## R2-01A: canonical trace recovery / fresh-capture readiness

A follow-up task (R2-01A) searched for the canonical historical trace this
README originally pointed `run R2-01` at. Findings, narrowly scoped to
repository-documented locations (never an uncontrolled disk scan):

- The exact canonical identity is unambiguous: Research Part 1's own
  `experiments/EXP-019-instrumentation-validation/README.md` documents the
  2026-10-12 publication-quality Windows validation run
  (`raw/windows-validation-2026-10-12-r2/run-00-4G/trace.jsonl.gz`,
  `publication_quality = true`), with its exact SHA-256 and byte size
  recorded in `data/external_raw_manifest.csv`.
- That file is **not present on this machine**: its local `raw/` directory
  is empty (0 bytes). Argine2's own README states its external raw
  corpus's "archival download location will be added to the release
  metadata when the external archive is published" -- i.e. no resolvable
  external location exists yet, by the repository's own authority.
- No other locally-present trace is a scientifically equivalent
  substitute: every SMOKE run is explicitly documented as not
  publication-quality, and no other experiment (EXP-007/008/021/024/026)
  has any local raw evidence either.

**Decision: FRESH_CAPTURE_READY.** `argine research2 capture R2-01`
(`argine/research2/r2_01/capture.py`) reconstructs the documented EXP-019
protocol -- model `Qwen3-30B-A3B-Q4_K_M`, 4 GiB expert budget, `coding` v1
workload (DEV split, verified byte-identical to the historical run via its
SHA-256), seed 42, 4 generation threads / 12 batch threads, ctx 4096 --
and drives it through Argine2's own existing `argine research trace`
command (never a reimplementation of tracing). Nothing not explicitly
documented was invented; where the README does not pin a value (batch/
ubatch size, read mode, PRETOUCH), the current CLI's own default already
matches the documented behaviour, so nothing needed to be guessed.

```
argine.bat research2 capture R2-01 --model C:\path\to\Qwen3-30B-A3B-Q4_K_M.gguf
argine.bat research2 run R2-01
```

The first command (add `--dry-run` to preview the exact command without
starting the model) runs the real capture and, on success, registers the
produced trace in local Research2 state (outside Git) so the second
command resolves it automatically -- no `--trace-source` needed. Neither
command was executed by the R2-01A implementation task itself; both are
left ready for one human to run.

## R2-01B: Level-D behavioral evidence

See `docs/research2/METHODOLOGY.md` ('R2-01B: closing the Level-D behavioral-observability gap') for the full contract. In short: the existing `argine research trace` capture already writes a format-v2 `raw.tsv` alongside `trace.jsonl.gz`, and Research Part 1's own EXP-021 fidelity tooling (`argine.research.fidelity`, `tools/validate_replay_fidelity.py`) already defines a predeclared, zero-tolerance runtime-vs-replay comparison. `behavioral_fidelity.evaluate()` reuses that tooling directly; no runtime change, no second real capture.

## Current state

See `argine.bat research2 status` for the live state-machine status (now
also reporting R2-01's trace-source readiness: `trace_source_status`,
`trace_source_type`, `trace_source_runnable`, `trace_source_blocker`). As
of this implementation commit: `IMPLEMENTED`/`READY`, trace source
`TRACE_SOURCE_MISSING` or `TRACE_SOURCE_AMBIGUOUS` pending a human running
the capture command above. No candidate scientific run has been recorded
against this repository's committed state. **No scientific conclusion,
measurement, or claim should be inferred from the presence of this README
update** -- only from an actual run's recorded evidence.
