# Research 4, Phase 1: CFDC Training and Prospective Validation

**Reader-facing name:** CFDC Training and Prospective Validation (Causal Future-Distance Distillation, Phase 1).
**State:** `R4_CFDC_PHASE1_VALIDATED`. VALIDATION was scored exactly once. EVAL is `LOCKED` and was never read. The final Research 4 class is not computed here.

Phase 1 implements the protocol frozen in `../R4-00-causal-eviction-distillation/` (digest `fa0ff8d4c93b463a99bdba4a65625251fa00c8102f5b2d788f8f22e73a20d64e`). It does not change any Phase 0 contract.

## What this phase does (and only this)
1. Implements the frozen replay semantics (`engine.py`): exact integer byte accounting, event-atomic demand admission, current-event shield with total fallback, the 16 causal features, the teacher, and the policies A, NUE_X, DECAYED_COUNT, CHEAP, RICH. BELADY is the frozen `argine.research.oracle.BeladyGlobal` driven through an adapter.
2. Proves replay fidelity (`REPLAY_FIDELITY.json`): exact integer equality with the frozen historical values on the streams where they were produced.
3. Extracts TRAIN teacher examples (`TEACHER_MANIFEST.json`, `TRAIN_MANIFEST.json`).
4. Fits the one frozen RICH model and the one frozen CHEAP model on TRAIN only (`models.py`, `RICH_MODEL.json`, `CHEAP_MODEL.json`, `CHEAP_RULE.md`).
5. Evaluates the frozen candidates on VALIDATION exactly once, after a separate opening commit (`VALIDATION_OPENED.json`), and hash-locks the result.

No EVAL trace is read and no EVAL metric is computed. The final Research 4 classification is **not** computed here; it needs EVAL (Phase 2).

## Commit structure
- Commit A (`db84d70`): implementation, fitted policies, fidelity evidence, validation plan (no validation outcome).
- Commit B (`07e3970`): `VALIDATION_OPENED.json` only (hashes, no outcome).
- Commit C: validation results, decision, provenance, Phase 1 freeze.

## Files
`engine.py` replay engine, features, teacher, policies. `models.py` CART (CHEAP) and GBRT (RICH), numpy only; `_rich_infer.c` is an optional inference helper with a bit-identical numpy fallback. `phase1.py` orchestration and the frozen validation scorer. `tests/test_phase1.py` tests.

## Reproduce (read-only on the source traces; no model inference)
```
python experiments/R4-00-causal-eviction-distillation/phase0.py verify
python experiments/R4-01-cfdc-training-validation/phase1.py gate
python experiments/R4-01-cfdc-training-validation/phase1.py verify
python -m pytest experiments/R4-01-cfdc-training-validation/tests -q
```
Source traces are located through `ARGINE_RESEARCH_ROOT` (or `%LOCALAPPDATA%\Argine3\research`), hash-verified against `R4-00/DATA_SPLITS.json`, and never copied into the repository.

## VALIDATION result (12 R2-11 traces, 1.5 GiB, decode miss bytes, pooled exact)
Reference A 3,641,656,946,688 B; BELADY 2,125,721,751,552 B (denominator 1,515,935,195,136 B).

| arm | capture | note |
|---|---|---|
| CHEAP | -36.04% | gate (>= 25%) NOT met; negative on all 12 workloads |
| RICH (diagnostic) | +15.45% | below 25% |
| NUE_X (Research 3 comparator) | +7.42% | |
| DECAYED_COUNT (decay 0.99) | -5.60% | |

Excluding `r2-11-coding-v1` (non-gating sensitivity): CHEAP -35.42%, RICH +15.52%, NUE_X +7.40%, DECAYED_COUNT -6.14%. Nothing was changed after the score. Per-workload values, secondary metrics and raw result hashes are in `VALIDATION_RESULTS.json`; the mechanical gate result is in `VALIDATION_DECISION.json`. This is a VALIDATION result only: the Research 4 classification requires the single EVAL opening (Phase 2), which is not authorized here.

## Execution note (honest account)
A shell call is limited to three minutes, so the RICH arm (about five minutes per trace) was executed in resumable chunks by the harness in `execution_harness/` (`drv2.py` runs the light arms, `drv3.py` is a resumable copy of the `Sim.run` loop, `sched.py` schedules them; lf-sha256 prefixes 3b46178854d7, 3e1ae7a01b59, ac71964aa538). The harness changes no policy code: it imports the frozen `engine.py`, `models.py` and `phase1.assemble`. Its chunked loop was verified to return results identical to the monolithic `Sim.run` for CHEAP on a real VALIDATION trace (before the RICH arm was run) and, afterwards, for RICH, CHEAP and DECAYED_COUNT on a synthetic stream. The `phase1.py score` command then assembled the cached per-trace results into the frozen result files. One earlier invocation of `phase1.py score` and two harness calls (one of them the first RICH attempt) were cut off by the time limit; they produced no output and nothing from them was inspected. Every per-trace result that exists was computed once and cached.
