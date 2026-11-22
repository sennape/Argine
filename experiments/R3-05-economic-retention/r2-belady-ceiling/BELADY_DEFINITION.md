# BELADY (clairvoyant reference) — exact semantics used by R3-05 R2

**Implementation:** the repository's historical `argine.research.oracle.BeladyGlobal._access` (the "Oracle-Global-Belady / O_B" reference of R2-02..R2-04), reused unmodified. It is driven directly from the frozen R1 canonical stream (the original needs a `Trace` object only to build its event list; the same `next_uses` function and `_access` logic are used on the stream's events). No new Belady variant was written. It matches the Phase 0 definition: *global Belady MIN over the same stream, same per-layer byte sizes, same budget, same atomic-event rule*.

**What it is.** On a miss the policy evicts the resident (layer, expert) whose **next use (event index, over the whole stream including prefill) is farthest in the future**; never-used-again experts go first. Ties break on the internal version counter (older first) and then the (layer, expert) key — deterministic.

**Atomic-event rule.** Within one event, members are processed in ascending expert id; **no member of the current event can be chosen as victim** (they are "held"). If an event cannot fit even after evicting all non-members, the excess members are counted as overflow misses (0 occurred at every budget).

**Future knowledge is used ONLY for the eviction choice.** The stream's events, order, requested expert sets, byte sizes and the budget are inputs, not outputs: the policy cannot skip, reorder or alter them. `_expect` aborts the run if the replayed event differs from the oracle's event list; the replay asserts every event is consumed.

**Accounting.** Same as the A baseline and the R1 replay: decode = events with index >= 96 (passes >= 2, 512 tokens); misses, hits, miss bytes (misses x bytes_of_layer) and evictions are integers; per-token numbers are exact fractions over 512. Budget enforced: resident bytes are checked after every event (0 violations).

**Caveats (inherited from the historical docstring).** Events are multi-key and sizes differ slightly between layers (bytes_of_layer = 3,059,712 + layer), so farthest-next-use is a strong reference but **not proven optimal**; the result is labelled *Belady-style*. It is not deployable, not causal, and not a candidate.

**Baseline A.** The frozen Python reference `AgedFrequency` (b_selected) with the frozen per-budget parameters: 1.5 GiB decay 0.999 / interval 1; 4 GiB 0.99 / 64; 8 GiB 0.999 / 16. At 1.5 GiB it reproduces the R1/Gate 2 counters exactly (test).
