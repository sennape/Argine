# R2-12 — Real runtime policy integration (Commit A: implementation + integration tests)

Status: implementation complete and built against the real runtime. This
commit does NOT claim `R2_12_RUNTIME_POLICY_CONFORMANCE_PASS`; see the
separate conformance-evidence commit for that determination.

## What changed

Three files, diffed against the previous frozen state (`13b1ee9`):

- `patches/argine-store.h` — new public (pure C) API:
  `psx_retention_policy_requested`, `psx_cache_attach_retention_policy`,
  `psx_cache_has_retention_policy`, `struct psx_retention_stats`,
  `psx_cache_get_retention_stats`. Nothing existing was removed or
  renamed.
- `patches/argine-store.cpp` — purely additive (434 added lines, 0
  removed/changed, confirmed by diff against `13b1ee9`'s copy): new fields
  on `struct psx_cache` (`policy`, `policy_id`, `policy_step`,
  `policy_events`, `policy_trace_fp`, `policy_obj_slices`), the four new
  functions above, and a new static function `servi_evento_con_politica`
  that `psx_cache_ensure_many` calls instead of its existing body **only**
  when `c->policy` is set. With no policy attached, `psx_cache_ensure_many`
  runs the exact pre-existing code, unchanged.
- `patches/argine-experts.cpp` — one line changed (`const unsigned n` ->
  `unsigned n`, since the value is now conditionally forced to 1) plus
  additive wiring in `Contesto::inizializza()`: if
  `psx_retention_policy_requested()`, force `shards=1` (hard-aborting via
  `GGML_ABORT` if the user explicitly asked for a different
  `ARGINE_EXPERT_SHARDS` value at the same time), then call
  `psx_cache_attach_retention_policy`, aborting on any rejection, and
  printing a provenance line to stderr naming the exact policy and
  parameters loaded when one is active.

The frozen C++ policy core (`patches/argine-retention-policy.{h,cpp}`,
committed at `c2f9a68`) was **not modified**: `servi_evento_con_politica`
only calls its existing public interface (`RetentionPolicy::access`,
`::stats`, `::start`) and translates its decisions (`Obj` = (layer,
expert)) into the real cache's physical `(region, offset)` slices.

## The real eviction hot path, as actually wired

`argine-experts.cpp`'s per-node dispatch still gathers `da_leggere[]` and the
`dove[]` (gate/up/down) entries exactly as before; it calls
`psx_cache_ensure_many` exactly as before. The only change is *inside*
`psx_cache_ensure_many`: when a policy is attached, the call is routed to
`servi_evento_con_politica` instead of the pre-existing prenota()-driven
Fase-1/2/3 pipeline. `prenota()` itself is untouched and is simply never
called for any slice while a policy is active (there is exactly one shard in
that mode, and `psx_cache_ensure`/`psx_cache_ensure_many` are the only two
entry points that reach it; `psx_cache_ensure` delegates to
`psx_cache_ensure_many`, so there is one unified path).

## Design adaptations (section 3 of the handoff), and why

1. **Event-level granularity.** The frozen policy module reasons in
   `(layer, expert)` objects, one `access()` call per MoE node (one layer,
   its full requested-expert set). The real cache's existing API is
   slice-level (`psx_cache_ensure_many` receives up to 3 kind-slices per
   expert). `servi_evento_con_politica` deduplicates the batch's expert ids
   and calls `access()` once per batch (= once per node), matching the
   Python reference's event boundary exactly, then fans the single
   admit/evict decision back out across all of the batch's slices. A block
   whose slices span more than one layer is treated as a caller error and
   rejected (`GGML_ABORT`-equivalent `s->fail`), not silently handled,
   because the real dispatch is documented to always batch one layer at a
   time; this is covered by integration test case 5.

2. **`bytes_of_layer` is computed from the kinds *actually present* in the
   batch**, not assumed to always be 3. `argine-experts.cpp` documents that
   a kind can be absent for a given layer ("finished on the normal,
   non-Argine path"). Using a fixed assumption of 3 would silently
   misstate the unit size the policy budgets against whenever that
   happens. This does not touch the frozen decay/scoring/predictor math —
   only the externally-supplied "bytes of one unit" input, which the
   Python reference also receives from its caller rather than computing
   itself.

3. **Logical-to-physical translation.** `policy_obj_slices` (an
   `(layer,expert) -> up to 3 CacheKey` map, populated/consumed only when a
   policy is attached) lets a policy-chosen victim `Obj` be translated into
   the exact set of currently-resident physical slices to decommit, rather
   than assuming a fixed 3-for-3 correspondence.

4. **One critical section per event.** Per the thread-safety requirement
   (section 6): the policy's own internal state (`resident_`, `t_`,
   per-layer predictor state) is not safe under concurrent access, and the
   unit of residency spans up to 3 physical slices that must be decided
   together. `servi_evento_con_politica` takes `shards[0]->mtx` — the
   *existing* mutex that already serializes every access to that shard —
   once, and holds it for the policy decision, the physical eviction, the
   physical admission (`psx_region_commit`), **and** the synchronous disk
   read (`psx_read_many`) for the whole event, only releasing it when the
   event is fully resolved. This is a deliberate, documented departure from
   the generic path's performance optimization (which releases the lock
   between "reserve" and "read" specifically to overlap I/O across
   threads): under an active retention policy, I/O for one event is
   serialized with I/O for any other concurrent event on the same cache.
   The gate for this round is correctness, not performance (section 12:
   "Do NOT report runtime speedup"), and this trade-off removes an entire
   class of interleaving hazards (a second event observing a slice
   mid-load, or deciding against stale residency state) rather than
   requiring new synchronization primitives whose correctness could not be
   exercised against the real model in this round. It is reported here so
   it can be revisited explicitly in a future round, not discovered later
   as an undocumented side effect.

5. **Overflow is a hard failure, not a silent partial admission.** If the
   policy's own `EventOutcome` reports `overflow_events`/`overflow_misses`
   (the event does not fit even after evicting everything), that event is
   rejected (`s->fail`) rather than admitting part of it. No real-model
   trace in this round exercised this path; it is a conservative choice
   given the inability to validate the alternative against a real
   workload this round.

6. **No change to `prenota()`'s own LRU fallback.** It remains exactly as
   it was. It is simply unreachable while a policy is attached, because
   `psx_cache_ensure_many` never calls it in that mode. There is therefore
   no dual-decision-path risk to reconcile.

## Build result (section 8)

The real `llama-server` binary was rebuilt from the pinned llama.cpp commit
`254b177307b14d055bbe4c050c9c441a1e06d8a4` with the three Argine patches
applied and `argine-retention-policy.{h,cpp}` added to
`ggml/src/ggml-cpu/CMakeLists.txt`, using the documented recipe
(`README.md`). Toolchain: GCC 11.4.0 (g++/gcc), CMake 4.4.5 + Ninja 1.13.2
(installed via `pip3 install --user --break-system-packages cmake ninja`,
since neither was preinstalled and `sudo` is unavailable in this sandbox).
Build config: `-DCMAKE_BUILD_TYPE=Release -DGGML_NATIVE=OFF
-DGGML_OPENMP=OFF`. Target `llama-server` built clean, 15/15 steps, zero
errors or warnings from the modified files. `./build/bin/llama-server
--version` reports `version: 0.5.0-dev (build 1, commit 254b177), built
with GNU 11.4.0 for Linux x86_64`.

## Integration tests (section 9)

New file `patches/argine-retention-integration-test.cpp`, built and linked
directly against the real, modified `argine-store.cpp` +
`argine-retention-policy.cpp` + `argine-research.cpp` (no llama.cpp/ggml
involved, same autonomy as the pre-existing `argine-store-test.cpp`), run
against a REAL `.psxs` store file
(`Qwen_Qwen3-30B-A3B-Q4_K_M.psxs`, 48 layers x 128 experts, found mirrored
into this sandbox) -- not a synthetic/simulated store. All 18 checks
passed:

1. No policy requested -> `psx_cache_attach_retention_policy` is a no-op,
   `psx_cache_has_retention_policy` is false, `psx_cache_ensure_many`
   serves a normal event unchanged.
2. Unrecognized policy identifier -> rejected, non-empty error message,
   no policy left attached.
3. `ARGINE_EXPERT_SHARDS` requesting more than one shard while a policy is
   requested -> rejected with a clear message.
4. Valid `b_selected` configuration (decay=0.999, interval=1, one of the
   frozen pairs) against a budget sized for ~3 resident experts:
   - event 1 (3 new experts): admitted, zero evictions, zero budget
     violations;
   - event 2 (2 more new experts): admitted via **multiple** evictions in
     one event (6 physical slice evictions = 2 logical objects x 3 kinds),
     zero budget violations;
   - event 3 (re-request of an expert evicted two events earlier):
     re-admitted correctly (the re-admission edge case), zero budget
     violations;
   - provenance (`psx_cache_get_retention_stats`) reports the correct
     `policy_id`, `decay_factor`, `aging_interval_steps`, and
     `events_served == 3` after the three events.
5. A block whose slices span two different layers, under an active policy,
   is rejected rather than silently processed.

## Regression check against the existing suite

The pre-existing `patches/argine-store-test.cpp` (byte-fidelity test) was
compiled against the modified `argine-store.cpp` and links/builds clean,
confirming no compile-time regression; it was not re-run end-to-end this
round because its golden-comparison fixture (`atteso.bin`, produced by a
separate Python-side extraction step) is not present in this sandbox and
regenerating it is outside this round's scope. Byte-for-byte store-reader
fidelity is orthogonal to this round's change (the diff never touches
`psx_get`/`psx_slice_read_bytes`/`psx_read_many`'s reading logic); the
default (no-policy) behavioral path was independently exercised and found
unchanged both by direct diff inspection (0 removed/changed lines in
`argine-store.cpp`'s pre-existing code, 1 `const`-qualifier removal in
`argine-experts.cpp`) and by integration test case 1 above.

## What this commit does NOT establish

This commit does not run the real model, does not compare real runtime
events against the frozen Python reference trace, and does not check
real-model output/token-identity correctness. See the separate conformance
evidence artifact for why, and for the exact remaining blocker.
