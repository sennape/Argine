# R3-02 — Calibration and Mechanism Attribution (Phase 0 freeze)

Frozen acquisition protocol for the R3 mechanism-attribution campaign that must precede any
cost-aware retention design. **Phase 0 only**: no model was loaded, no acquisition has run, and no
result exists in this directory.

- Protocol: [`PROTOCOL.md`](PROTOCOL.md)
- Machine-readable preregistration: `PREREGISTRATION.json`
- Frozen launch plan: `ACQUISITION_PLAN.json` (52 launches; 49 scientific)
- Freeze record: `FREEZE.json`

## At a glance

| | |
|---|---|
| question | overhead of predictive bookkeeping at equivalent retention; expert traffic per budget; whether a later cost model is supportable |
| controls | A `b_selected`, B `predicted_protect_sham`, C `predicted_protect` (8 GiB only) |
| workload | `r2-11-coding-v1` (DEV only) |
| budgets | 1.5 / 4 / 8 GiB |
| matrix | 7 configurations · 7 TRACE_COUNT · 7 warmups · 35 counted TIMING · 35 CELLs = 49 launches |
| preflights | 3 non-scientific integration launches (A/B/C at 8 GiB) → 52 total |
| runtime | `3f094f5b…` (new MSVC R3 identity; R2 MinGW runtime untouched and never compared in absolute timing) |
| statistics | paired medians, 10 000-resample bootstrap CI, no p-values, no trimming |
| classification | `MECHANISM_OBSERVABLE` vs `MECHANISM_INSUFFICIENT` only |

## Reproduce the plan

```bash
python tools/r3_02_plan.py validate   # invariants: counts, ordering, identities, no result data
python tools/r3_02_plan.py plan       # regenerate PREREGISTRATION.json + ACQUISITION_PLAN.json
python tools/r3_02_plan.py digest     # protocol digest
python -m unittest discover -s experiments/R3-02-calibration-mechanism/tests
```

Phase 1 (the 52 launches) is **not** part of this directory's scope and must not be run without a
separate authorisation.
