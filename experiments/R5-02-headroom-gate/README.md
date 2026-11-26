# R5 Phase 1b — overlap-headroom gate G2 (acquisition ready, not run)

**State: `R5_PHASE1B_ACQUISITION_READY`.** No Phase 1b launch has run and no H has been computed. The evaluation stays locked, and Phase 2 is not opened.

**Question:** does the frozen R5 runtime's baseline leave enough expert-transfer wait on the decode critical path to justify opening Phase 2? "Enough" means wait that could hide behind the compute after each layer's first expert node. Operationally, the R5-00 gate G2 asks whether the optimistic bound **H ≥ 4 %** of decode time, from exactly 8 B launches on the frozen binary. H is computed once. Below 4 % the label is `R5_NO_MEANINGFUL_OVERLAP_HEADROOM` and the evaluation is never opened. H is an opening gate, never a gain.

| file | role |
|---|---|
| `PROTOCOL_1B.json` | the pre-declared protocol. It quotes the frozen R5-00 rule texts verbatim and fixes the operational choices: estimator, aggregation, schedule, checks, outputs, estimates |
| `headroom.py` | the estimator (`launch_h`) and the exact-arithmetic G2 decision (`decide`). Strict: invalid telemetry gives no H |
| `acquire.py` | the host driver: `run` (fail-closed preconditions, 1 warmup + 8 counted B launches, then H once), `status` (never shows H), `verify` (re-derives the stored result from the kept traces) |
| `phase1b.py`, `READINESS.json` | the readiness freeze over the sources, the protocol and the G1 seal, and its verifier |
| `tests/` | synthetic-trace estimator tests, decision boundary, fail-closed driver, development prompts only |

Estimator, per decode layer event, as in ENDPOINTS `headroom_bound_H`:
- The first node's critical-path wait interval is the wait of the thread whose wait **ends last** (the node cannot exit before it).
- `T_tail` runs from when the first node's own-kind data are ready (for a read whose completion is only bounded, flag 2, its issue time, so a late poll never shrinks H) to the latest completion of a read issued at the first node that is not synchronous (flags 4, 8 and 16 are never hideable). It is clipped to that critical interval, so `H_layer` never exceeds the first node's exposed wait.
- `W` is the compute gap from the end of the first node's hook to the start of the layer's last expert node.
- `H_layer = min(T_tail, W)`: one value per event, so overlapping or simultaneous reads are never summed.
- `H` = Σ `H_layer` over the 8 launches (ms) / Σ their `predicted_ms` (the measured decode time). G2 passes at `H ≥ 1/25`, in exact arithmetic and inclusive.

Telemetry is validated after every launch, warmup included, without computing H. Each node needs 12 distinct thread ids, with the same set in every node, and every read must belong to one of those threads and to exactly one node window. Anything else stops the acquisition with no H.

Reruns follow the R5-00 blame rules. A failed B launch is attributed to the campaign. A slot can be rerun once, and only with `--rerun-infrastructure CAUSE --evidence FILE`. A second failure aborts the campaign. An interrupted attempt counts as a failure, and every restart begins with a new uncounted warmup. Counted traces are kept under attempt-numbered names and hashed into the log before any post-launch work. An attempt interrupted after its trace was kept cannot be rerun for `operator-interruption`. Every superseded trace is reported in `G2_RESULT.json` without deciding, and a missing or altered one blocks the gate. The raw trace is logged as received before it is reduced; a raw trace left behind is analyzed and reported, and any other unaccounted file in `traces/` blocks the gate. The readiness digest and the estimator, driver and protocol hashes are recorded at the start and checked again before H is computed. Model and store hashes are checked before the first launch and after every launch.

The Phase 1a draft in `ets_trace.py` clipped to the union of all threads' waits and let synchronous reads extend the tail. That draft is not used: both can exceed the critical-path wait.
