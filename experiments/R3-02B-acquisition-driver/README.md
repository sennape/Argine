# R3-02B — Acquisition Driver Realization and Dry-Run Validation

Status: **PASS_ACQUISITION_DRIVER_READY (hotfix 3)** — infrastructure only. No model was loaded, no
preflight was executed, no scientific acquisition occurred.

`tools/r3_02_acquire.py` implements the executor required by the frozen
[R3-02 Phase 0 protocol](../R3-02-calibration-mechanism/PROTOCOL.md). It consumes
`ACQUISITION_PLAN.json` verbatim and refuses to run if any frozen property differs.

## CLI

```powershell
python tools/r3_02_acquire.py --plan experiments/R3-02-calibration-mechanism/ACQUISITION_PLAN.json --stage preflight
python tools/r3_02_acquire.py --plan experiments/R3-02-calibration-mechanism/ACQUISITION_PLAN.json --stage scientific
python tools/r3_02_acquire.py ... --dry-run            # full validation, starts nothing
python tools/r3_02_acquire.py ... --check-imports      # repository-local argine resolution
python tools/r3_02_acquire.py ... --check-storage      # NTFS compression + free-space gate
```

Execution additionally requires `--attest-clean-environment`.

## Hotfix 3: live evidence completeness

- **Canonical prompt.** The workload is read as `items[0].prompt` — the same construction as the
  validated R2-12 `resolve_workload` — and the file SHA and `n_predict` are re-verified. An
  unsupported schema fails closed instead of sending an empty prompt.
- **Evidence persistence.** Every launch now writes R3-01 records atomically under the run
  directory (outside Git): `records/`, `admissions/`, `cells/`, `logs/`. TRACE_COUNT records carry
  the trace reference (path/SHA/bytes), counters, identity, output-token digest and admission;
  TIMING records carry ttft/decode/cpu/tokens and admission; counted TIMING runs are paired through
  the frozen R3-01 `build_cell` and persisted with ADMISSIBLE/INADMISSIBLE and the exact failed rule
  ids. stdout/stderr are persisted per launch with SHA-256 and byte sizes.
- **Preflight provenance v2.** The preflight must persist `output_token_digest`, clean exit status,
  budget invariant and stderr provenance (schema 2). The old 3/3 preflight state cannot authorize
  the scientific stage: **a new preflight is required**.

## Storage

`NTFS_COMPRESSION_TEST.json` records the measured, lossless NTFS compression of a 1.79 GiB trace:
logical SHA identical, frozen parser counters identical, new files inherit compression
automatically, ratio **0.4757** (52.4 % physical saving). The runtime writer needs no change.

`STORAGE_MODEL.json` applies that ratio to the frozen acquisition: expected physical peak
**73.0 GiB**, conservative **82.5 GiB**, against **65.06 GiB** free. Even the expected peak does not
fit today, and after maximal safe cleanup only the expected case fits, without the conservative
margin. Classification: **STORAGE_REDESIGN_REQUIRED** — the minimum scientific change is a
trace-schema revision that stops emitting `resident_before` per row (derivable from the previous
row), which halves the payload. That change is **not** made here.

## Tests

58 mock/offline tests, including the live-path and import-resolution regressions, canonical prompt,
record/CELL/stderr persistence, digest-mismatch inadmissibility, old-preflight rejection, NTFS
compression equivalence, storage gate behaviour and the no-process guarantee.

## Not in scope

Phase 1 execution, inference, scientific analysis. Even with the driver repaired, scientific
acquisition remains blocked by the storage classification above until the protocol decision is made.

## Hotfix 5: preflight shutdown semantics

The preflight persists `schema: 3` evidence with explicit shutdown fields (`request_completed_successfully`,
`server_return_code`, `shutdown_mode`, `intentional_shutdown`, `shutdown_verified`, `kill_used`,
`process_alive_before_shutdown`, `stderr_error_markers`). On Windows the driver stops the server with
`TerminateProcess`, whose exit code is 1 even after a fully successful request; that code is accepted as a
clean launch **only** when the request completed, the admission is ADMISSIBLE, the shutdown was a verified
intentional terminate with no kill escalation and no stderr error marker. Any other nonzero code is a
failure. The stage summary is verified against the canonical predicate after a state reload, so a stage
cannot report SUCCEEDED while failing to authorize the next stage. `--authorize-preflight` evaluates the
predicate offline against an existing run directory.
