# R2-12 -- real-runtime retention-policy conformance traces

This directory holds `ARGINE_RETENTION_TRACE` JSONL output from
non-counted, non-benchmark conformance runs only: real-model runs whose
sole purpose is checking that the real runtime's retention/eviction
decisions match the frozen Python reference
(`tools/r2_12_real_trace_conformance.py`), per
RETENTION-POLICY-TRACE-INSTRUMENTATION.md.

**This directory is never read by any R2-12 benchmark tool.**
`tools/r2_12_execution_driver.py` (the real R2-12 counted-benchmark
driver, not yet executable -- `_execute_cell_NOT_IMPLEMENTED()` always
raises) only reads/writes `runtime_results/`, a sibling directory. Nothing
in this directory is a counted run, a warmup, or scientific evidence of
runtime performance; it is purely a decision-conformance check.

## Naming convention

One file per policy per conformance pass:

```
conformance_traces/conformance-b_selected-<YYYYMMDD>.jsonl
conformance_traces/conformance-predicted_protect-<YYYYMMDD>.jsonl
```

## How a trace here gets checked

```sh
python3 tools/r2_12_real_trace_conformance.py conformance_traces/conformance-b_selected-<date>.jsonl
python3 tools/r2_12_real_trace_conformance.py conformance_traces/conformance-predicted_protect-<date>.jsonl
```

`MATCH` = every decision field (hit/miss/evicted/resident_after/
protected_before/bytes) agreed exactly with the real frozen Python
reference, for every event. `MISMATCH` = a genuine divergence, reported
at the first occurrence. `BLOCKED_INVALID` = the trace itself is
malformed, incomplete, or internally inconsistent -- a problem with the
RUN, not a conformance verdict.

## What does NOT belong here

- Anything written by `tools/r2_12_execution_driver.py` or
  `tools/r2_12_readiness_driver.py` (those write to `runtime_results/`).
- `R2_12_RUNTIME_POLICY_CONFORMANCE_PASS` or any other PASS/benchmark
  artifact -- conformance traces inform that decision but are not
  themselves that artifact.
