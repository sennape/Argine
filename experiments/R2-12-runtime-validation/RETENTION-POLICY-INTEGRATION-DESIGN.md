# R2-12 retention-policy runtime integration -- design (not yet wired into the hot path)

## Status

`patches/argine-retention-policy.{h,cpp}` is a complete, standalone,
llama.cpp/ggml-independent module implementing the two R2-12 frozen
conditions bit-for-bit against their Python reference classes (verified by
33 passing tests in `tests/test_r2_12_retention_policy_conformance.py`,
including cross-language conformance across all three frozen B_selected(M)
budgets, causality, fail-closed configuration, tie-breaking, and memory
correctness -- see `RETENTION-POLICY-CONFORMANCE-EVIDENCE.json` for the run
record).

It is **not yet wired into `patches/argine-store.cpp` / `patches/argine-experts.cpp`**,
i.e. the real `llama-server.exe` binary cannot yet select between the two
policies. This document specifies exactly how that wiring must be done, so
the real integration can be built and validated on the real host (this
module cannot be compiled against `ggml-backend.h`/llama.cpp internals from
this sandbox, which has neither a llama.cpp checkout nor the ggml headers;
`argine-store.cpp`'s standalone build already requires only a C++17
compiler and nothing from llama.cpp, so the *module itself* builds and
tests here, but the *integration* into the two hot-path files does not).

## Why this is not a drop-in change to the existing eviction loop

`patches/argine-store.cpp`'s cache (`Shard`, `prenota()`) is SHARDED: the
budget is split across N independent per-shard LRU instances
(`c->shard_of(key)` hashes `(region, offset)` into one of N shards), each
with its own mutex and its own `std::list<Slot> order`. This is explicitly
documented as an accepted approximation for the *default* LRU path ("tetto
complessivo resta rigido: e' la somma di tetti rigidi... con chiavi
distribuite uniformemente la differenza si perde nel rumore").

The frozen Python reference (`AgedFrequency`, `PredictedProtectPolicy`)
assumes a SINGLE global resident set, a SINGLE global causal ordinal
(`self._t`), and a SINGLE global budget `M`. Running either policy
per-shard, against only the fraction of objects that hash into that shard,
with an independent ordinal per shard, is NOT equivalent to the frozen
semantics -- it would silently change the scientific baseline being
compared. Per "do not 'simplify' semantics unless equivalence is proven",
this cannot be accepted as a transparent substitution.

Additionally, `argine-experts.cpp`'s calling code dispatches reads across
threads via `c.prossima_slice.fetch_add(1, memory_order_relaxed)` -- a
racing atomic counter, not a deterministic ascending-expert-id order. The
frozen Python reference processes each event's members "in ascending
expert id" (`argine/research/policies.py`'s own module docstring), which the
real multi-threaded dispatch order does not guarantee.

## Required integration (specification for the real-host build)

1. **Single global (non-sharded) cache when a retention policy is active.**
   At `psx_cache_new_n()`, if `ARGINE_RETENTION_POLICY` resolves to a
   non-NONE `RetentionConfig`, force `shards = 1` regardless of the
   requested/auto shard count, and record this override in
   `psx_cache_stats`/the report line so it is visible, not silent. The
   default path (policy unset) is completely untouched: zero risk to
   existing behavior.

2. **A single, explicit `argine_retention::RetentionPolicy` instance owned
   by `psx_cache`** (constructed once, in `psx_cache_new_n`, from the parsed
   `RetentionConfig`; destroyed in `psx_cache_free`). This instance is the
   sole source of truth for which (layer, expert) objects are resident and
   which to evict -- the existing `Shard::order`/`index` bookkeeping
   continues to track physical slots (region/offset/pins/ready) exactly as
   today, but which slot is chosen for eviction is now delegated to this
   instance instead of "the back of `order`" when policy mode is active.

3. **A new, isolated admission path, not a branch inside the hot
   `prenota()` function.** Add `prenota_evento_policy(...)` in
   `argine-store.cpp`: given the full set of (layer, experts[]) needed for
   one MoE node (exactly the same set `argine-experts.cpp` already computes
   as `da_leggere[]` before dispatching reads), this function:
   a. Takes the single global mutex (not a per-shard mutex -- there is only
      one shard in policy mode).
   b. Sorts the expert list ascending (mirroring `sorted(experts)` in the
      Python reference) and calls `RetentionPolicy::access(step, layer,
      sorted_experts, bytes_of_layer)` ONCE for the whole event, exactly as
      `AgedFrequency.access()` is one call per event, not per expert.
   c. For every `Obj` the call reports evicted, decommits and removes the
      *three* slices (gate, up, down) at that (layer, expert) from the
      physical shard's `index`/`order` -- never just one slice, because the
      Python model's own documented approximation is that the three slices
      move together ("the gate node loads all three, so they are loaded
      together"); this three-slices-as-one-unit assumption must be
      mechanically enforced here (physically evicting one kind at a time
      but all three kinds for one evicted object before returning).
   d. For every missed expert the call reports, commits memory and inserts
      new `Shard::Slot` entries (not-yet-`ready`) for its three slices,
      exactly as the existing miss path does, so the subsequent (possibly
      parallel) read dispatch in `argine-experts.cpp` sees them as pending
      reads rather than re-deciding anything.
   e. Releases the mutex. The actual disk reads for newly-inserted slots
      then proceed through the EXISTING `psx_cache_avvia`/`psx_cache_ensure_many`
      machinery unchanged -- this function only changes *which* decision is
      made and *when* (once, up front, in sorted order, under one lock), not
      how bytes are physically fetched.

4. **`argine-experts.cpp`'s call site**: when `ARGINE_RETENTION_POLICY` is
   active (checked once per process via the same env parsing, cached in
   `Contesto`), call `prenota_evento_policy(...)` for the full `da_leggere[]`
   set from thread `ith == 0` ONLY, before the existing parallel dispatch
   loop runs (all other threads wait at the existing `ggml_barrier`), then
   let the existing per-thread dispatch loop proceed exactly as today --
   every slot it looks up will already be indexed (either as a pre-existing
   hit or a pending miss), so no thread needs to make an eviction decision
   itself while policy mode is active.

5. **Comparability, not bias.** This serializes admission decisions for
   BOTH frozen conditions identically when policy mode is active; it does
   not run only for one of A/B. Any throughput cost of serialization is
   therefore common-mode across the A-vs-B comparison, not something that
   favors one policy over the other.

6. **Provenance.** `psx_cache_get_stats` gains the fields in
   `argine_retention::RetentionStats` (policy_id, decay_factor,
   aging_interval_steps, predictor_decay, predictor_decisions,
   protected_fallbacks) alongside the existing hit/miss/eviction/bytes
   counters, so every run's provenance record can show which policy was
   active and what it did -- never used to report a speedup at this phase.

## What remains before `R2_12_RUNTIME_POLICY_CONFORMANCE_PASS`

- Items 1-6 above must be implemented IN `argine-store.cpp`/`argine-experts.cpp`
  and built against the real llama.cpp fork on the real host (this sandbox
  has neither llama.cpp nor ggml headers, so this cannot be compiled or
  tested here).
- Output-equivalence (same generated token sequence, no regression) must
  then be checked on the real host with the real model, non-timed --
  this is new evidence this phase cannot fabricate or run from the sandbox,
  exactly as the R2-12 readiness gate itself had to be run on the real host
  and independently re-verified afterward, not assumed.
- Only after that host-side build + non-timed correctness check succeeds can
  `R2_12_RUNTIME_POLICY_CONFORMANCE_PASS` be honestly declared.
