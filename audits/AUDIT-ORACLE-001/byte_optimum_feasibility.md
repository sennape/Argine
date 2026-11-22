# True Offline Byte Optimum Feasibility (AUDIT-ORACLE-001)

## Question

For the canonical finite trace (R2-01 `run-00-4G`, decode v2, 146,304 expert requests / 48 layers / 128 experts per layer in R2-02 replay context, 4 GiB byte budget, object granularity `(layer,expert)` with per-layer byte sizes), can `O*` — the true minimum miss-byte cost achievable by an unrestricted offline/clairvoyant policy under exactly the same workload, byte capacity, request grouping, admission and cache semantics — be exactly computed or certified/bounded?

## Assessment

**POSSIBLE_BUT_EXPENSIVE** (with exact feasibility not established by this audit).

## Reasoning

- The problem is finite-trace offline caching with byte capacity and variable object sizes. Classical equal-size Belady gives the unweighted optimum in the standard model; variable-size/weighted objectives are materially harder. Depending on exact formulation (miss count vs miss bytes, admission constraints, multi-key event protection, object-size structure), exact offline optimal variable-size caching can be computationally expensive or require specialized algorithms/solvers.
- Trace/population scale is moderate (48 layers × 128 experts = 6,144 possible objects; R2-02 reports 146,304 accesses over decode events at 4 GiB with 35,529 Global-LRU misses and 14,754 O_B misses). This does not rule out DP/IP/flow formulations, but also does not make exact optimality trivial without implementing and validating a formulation against the exact R2 replay semantics.
- The R2 semantics add constraints that any exact formulation must match: per-event multi-key member protection, byte-valued capacity, per-layer (not per-expert) sizes, same event ordering/phase/semantics, and the same miss-byte accounting (`miss_bytes += misses_in_event_or_object * bytes_of(layer)`).
- No existing in-repo solver or certified bound for `O*` under these semantics was found during this audit.

## Possible formulations considered (not implemented)

- Integer programming / min-cost flow / interval graph formulations for offline caching (feasibility depends on exact objective and constraints).
- Dynamic programming on compressed traces (may be expensive given state space).
- Branch-and-bound or certified LP relaxations for bounds (could yield `FEASIBLE_BOUNDED` if formulated correctly).
- Exact validation on smaller/hypothetical traces to sanity-check any solver (not a substitute for canonical-trace `O*`).

## Recommendation

Do **not** silently replace the historical `Oracle-Global-Belady`/`O_B` comparator with an unvalidated `O*` claim. If CASE C requires a true bound in future, pursue a separate, explicitly scoped effort to formulate and validate an exact or certified-bound `O*` computation under the exact R2 semantics, with deterministic reproducibility and evidence. Until then, treat `O_B` as a clairvoyant comparator/reference, not as a proven byte-optimal bound.
