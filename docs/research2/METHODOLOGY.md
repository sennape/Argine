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
