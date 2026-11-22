# R2-12 runtime realization: v5 -- per-kind event fragmentation fix

Additive to `RUNTIME-REALIZATION-PROVENANCE.md` (v3) and
`RUNTIME-REALIZATION-PROVENANCE-V4-TRACE-FIX.md` (v4). This document
covers only what changed between `llama-research-v4` and
`llama-research-v5`. v3 and v4 are left unchanged on disk and their
provenance documents are not edited.

## Trigger

A real Windows-host run of `llama-research-v4`
(sha256 `b035d3913e2b88a58860fe4120f912c908f1fd7be37f8e4341089bfad2ad4d2e`)
with `ARGINE_EXPERT_READ=blocco`, `ARGINE_EXPERT_SHARDS=1`,
`ARGINE_RETENTION_POLICY=b_selected`, and a 4 GiB expert budget reached the
real blocking retention-policy path (the v4 fix worked: no more
asynchronous-mode bypass) but aborted on the first real MoE node:

```
argine: impossibile rendere residenti gli expert del layer 0
(blk.0.ffn_gate_exps.weight). Residenti 9 MiB su un budget di 4096 MiB,
questo nodo ne chiede 15 su 128. Alza ARGINE_EXPERT_BUDGET: il minimo e'
lo spazio per tutti gli expert che un singolo ubatch puo' toccare.
```

Retries showed the same failure at roughly 12 MiB resident. 9-12 MiB
against a 4096 MiB budget, for a request of 15 of 128 experts, does not
look like genuine capacity exhaustion.

## Root cause

`ARGINE_EXPERT_READ=blocco` given without `:N` defaults `c.blocco` to 2
slices (`leggi_modo()` in `patches/argine-experts.cpp`). The real
`LETTURA_BLOCCO` dispatch loop fills sub-blocks of `c.blocco` slices at a
time from the flat `dove x da_leggere` sequence -- and that sequence is
ordered **per kind**, not per expert:

```cpp
const Dove &   d = dove[k / n_serve];   // kind changes every n_serve steps
const uint32_t x = da_leggere[k % n_serve];  // expert cycles fastest
```

So the first sub-block of a 15-expert node is `{expert0-gate,
expert1-gate}` -- two *different experts* of the *same* tensor kind, not
two kinds of the same expert.

`RetentionPolicy::access()` (`patches/argine-retention-policy.cpp`,
**unchanged** by this fix) tracks residency per `Obj{layer,expert}`, not
per `(layer,expert,kind)`, and uses `bytes_of_layer` (the kinds *seen in
that one call*) as the whole object's byte weight when marking it
resident:

```cpp
if (resident_bytes_ + bytes_of_layer <= M_ && bytes_of_layer <= M_) {
    ObjState st{bytes_of_layer, prev + 1.0, (int64_t) t_};
    resident_.push_back({obj, st});
    resident_bytes_ += bytes_of_layer;
}
```

After the first `{expert0-gate, expert1-gate}` sub-block, the policy
believes expert0 and expert1 are already **fully resident**, having
admitted only their "gate" tensor. A later sub-block requesting
expert0's "up" tensor is then a logical **hit** (`find_resident()`
non-null), but that tensor's `CacheKey` was never physically indexed.
`servi_evento_con_politica()` (`patches/argine-store.cpp`) has its own
consistency check for exactly this:

```cpp
if (!expert_is_missed(experts[i])) {
    snprintf(buf, ...,
             "retention policy: (layer=%u, expert=%u, kind=%u) non e' "
             "nell'indice ma la politica non lo elenca come mancante -- "
             "incoerenza fra stato logico e fisico", ...);
    s->fail(buf);
    return 0;
}
```

This is the exact failure that reached `GGML_ABORT` -- with **no budget
problem at all**: `requested_bytes_total` for 15 experts is a small
fraction of a 4 GiB budget, which is exactly why resident bytes were tiny
when the abort fired.

A second, related but secondary risk from the same fragmentation: slices
admitted by an earlier sub-block of the same node stay pinned until the
whole node finishes (pins are released only at the *next* node, in
`argine-experts.cpp`), so they can also become victim candidates for a
later sub-block's eviction decision, tripping the existing
"membro dell'evento corrente" / `pins>0` invariants. Both risks share the
same root enabler: one real MoE node's working set was never passed to
the policy as one atomic unit.

**Also discovered**: `LETTURA_BLOCCO` further fragments by **thread**, not
just by `c.blocco`. Every participating thread independently races the
shared `prossima_slice` counter and calls `psx_cache_ensure_many` on its
own sub-batch -- so even a large `c.blocco` would still split one real
node into up to *N* concurrent policy "events" under *N* threads.

Every alternative explanation on the user's checklist was considered and
ruled out by this analysis: no bytes-vs-MiB unit mismatch, no
per-tensor-vs-per-expert double counting (the opposite: under-counting,
since `bytes_of_layer` is the whole object's claimed weight from only a
partial kind set), no "test against all 128 experts" mistake, no
already-full-cache assumption -- the mechanism is specifically the
per-expert residency flag being set from a per-kind-partial admission.

## Fix

`patches/argine-experts.cpp`: under an active retention policy,
`LETTURA_BLOCCO` no longer sub-chunks by `c.blocco`, and no longer lets
every thread race the shared slice counter. Only `ith == 0` collects
**all** `totale` slices for the node (every kind, every requested expert)
and issues exactly **one** `psx_cache_ensure_many()` call, restoring the
atomicity `servi_evento_con_politica()`'s own comments already documented
as required. Other threads skip this section entirely; the
`ggml_barrier()` that already follows (unchanged) is what synchronizes
"thread 0 has admitted and pinned the data" with "everyone may now read
it," same as before. A new shared `std::atomic<bool> nodo_fallito_policy`
field lets the other threads' local `guaio` reflect thread 0's outcome
(belt-and-suspenders; `GGML_ABORT` already terminates the whole process
via `abort()`, so this mainly avoids a brief window of reading
not-yet-resident memory).

The non-policy LRU path (`LETTURA_BLOCCO` without a retention policy) is
**completely untouched**: same `c.blocco` sub-chunking across threads,
same measured I/O-overlap behavior.

No retention-policy semantics changed: B_selected, PredictedProtectPolicy,
predictor behavior, budgets, and eviction decisions are all untouched.
`argine-retention-policy.{h,cpp}` was not modified at all -- the bug was
entirely on the caller side (how slices were batched into calls), not in
the frozen policy math.

## Diagnostics added

Independent of the JSONL trace and of `psx_error()`'s plain text, per the
explicit request for ten specific fields:

`patches/argine-store.h` gains `struct psx_admission_failure_diag`:
`layer`, `budget_bytes`, `resident_bytes_before`, `requested_bytes_total`,
`requested_missing_bytes`, `reclaimable_bytes`, `requested_count`,
`protected_count`, `candidate_victim_count`, `smallest_expert_bytes`,
`largest_expert_bytes`, and the exact failure `reason` string -- plus
`psx_cache_get_last_admission_failure()` to read it.

`servi_evento_con_politica()` populates this struct, via a local
`popola_diag` lambda, immediately before **every** `return 0` admission
failure: the overflow check, the pre-scan self-eviction check, the
object-not-indexed-for-eviction branch, the pin-violated-eviction check,
the newly-relevant logical/physical mismatch check (the actual cause
here), the `psx_get` failure, the `psx_region_commit` failure, and the
read failure. Zero cost on the success path -- the lambda is only ever
invoked at an actual failure.

The outer `GGML_ABORT` in `argine-experts.cpp` now also calls
`psx_error(c.store)` (previously **never read at all** at this call site --
the exact reason the original message was always generic) and, when
available, prints the full diagnostic struct, ending with an explicit
statement of whether the budget is actually the problem:

> "Se requested_bytes_total e' ben sotto budget_bytes, NON e' un problema
> di budget: alzare ARGINE_EXPERT_BUDGET non lo risolverebbe."

## Tests

`patches/argine-retention-integration-test.cpp`, new sections 11-12,
against the real `Qwen_Qwen3-30B-A3B-Q4_K_M.psxs` store (128 experts per
layer, matching the real shape):

11. **Positive guarantee** (explicitly requested): 15 experts on layer 0,
    a budget 40x a single expert's footprint (same ample-headroom ratio
    as the real 4096 MiB vs. tiny request), a cold/near-empty cache,
    served with **one** `psx_cache_ensure_many()` call. Asserts admission
    succeeds, zero evictions, zero budget violations, and exactly one
    policy event for the whole node.
12. **Negative reproduction**: the same 15-expert node, fragmented into
    2-slice sub-blocks in the *real* per-kind order (not per-expert),
    with pins held across sub-blocks exactly as the real node does.
    Asserts the failure is specifically the logical/physical
    inconsistency message (`"incoerenza fra stato logico e fisico"`), not
    a budget message -- closing the loop between the diagnosis and a
    deterministic, real-store-driven reproduction.

All 12 sections (51 checks total, including the pre-existing sections
1-10) pass:

```
g++ -O1 -g -std=c++17 argine-store.cpp argine-retention-policy.cpp \
    argine-research.cpp argine-retention-integration-test.cpp -o arxretest
./arxretest Qwen_Qwen3-30B-A3B-Q4_K_M.psxs
...
RISULTATO OK: 0 fallimenti
```

## Commit

`34997c2` on `r2-main`: "R2-12: fix per-kind event fragmentation under
LETTURA_BLOCCO, add admission diagnostics". Touches only
`patches/argine-store.{h,cpp}`, `patches/argine-experts.cpp`, and
`patches/argine-retention-integration-test.cpp` -- nothing under
`experiments/`, nothing in `argine-retention-policy.{h,cpp}`.

## New runtime: llama-research-v5

Rebuilt incrementally from the same configured `build-win/` CMake tree
used for v3/v4 (same pinned llama.cpp commit, same MinGW-w64 toolchain,
same `_WIN32_WINNT` deviation): only `ggml-cpu/argine-store.cpp` and
`ggml-cpu/argine-experts.cpp` recompiled, `ggml-cpu.a` relinked, all four
binaries relinked against it.

Located at `C:\Users\alpel\Desktop\Argine2\llama-research-v5\` (outside
the git repo; `BUILD-INFO.json` mirrored into the repo at
`llama-research-v5/BUILD-INFO.json`). `llama-research-v4` at
`C:\Users\alpel\Desktop\Argine2\llama-research-v4\` is untouched --
verified its `llama-server.exe` sha256 still matches
`b035d3913e2b88a58860fe4120f912c908f1fd7be37f8e4341089bfad2ad4d2e` after
the v5 build.

| binary | sha256 |
|---|---|
| llama-server.exe | `65f445c42ac145765735984582691964181dd4dd8019f4519edac1fa315df26c` |
| llama-imatrix.exe | `dadca756289645d63bc183b86ceed478d87c56902d1a64868c0775d68cb84f7f` |
| llama-perplexity.exe | `4dabf0dc0790603d789fe7a358fa0e2d699e0c1e9b076dfefaa92e74d2b166ab` |
| llama-quantize.exe | `869668b9244132b320539f8a5af43668d9b61f7a659812923e585737dda47bba` |

Statically verified (`strings`, since it cannot run in this Linux
environment): the new "incoerenza fra stato logico e fisico" message and
the new "Motivo esatto... Diagnostica..." `GGML_ABORT` message (with all
ten new fields) are present in `llama-server.exe`; `objdump` import-table
DLL names unchanged per-binary from v3/v4.

**Not run** in this environment (PE32+ Windows binary). No benchmark. No
warmups. `R2_12_RUNTIME_POLICY_CONFORMANCE_PASS` not created. Benchmark
remains 0/120.

## Expected host path and diagnostics on the next run

With the same configuration (`ARGINE_EXPERT_READ=blocco`,
`ARGINE_EXPERT_SHARDS=1`, `ARGINE_RETENTION_POLICY=b_selected`, 4 GiB
budget):

1. Startup line unchanged: `argine: retention policy attiva: b_selected
   (decay=0.99, interval=64) -- R2-12 runtime policy integration`.
2. The first real MoE node (layer 0, `blk.0.ffn_gate_exps.weight`, 15 of
   128 experts) should now be served by **one** `psx_cache_ensure_many()`
   call covering all 15 experts' gate/up/down tensors together, and admit
   successfully -- no abort.
3. If a genuinely different admission failure ever occurs (a real budget
   problem, a disk read failure, etc.), the `GGML_ABORT` message will now
   include `psx_error()`'s exact text plus the full diagnostic line
   (`budget_bytes=... requested_bytes_total=... reclaimable_bytes=...`
   etc.), so the message itself will say whether raising
   `ARGINE_EXPERT_BUDGET` would actually help, instead of always
   suggesting it.
4. The retention trace diagnostic at shutdown (from the v4 fix) remains:
   `argine: retention trace diagnostica: ...` with matching non-zero
   counters when `ARGINE_RETENTION_TRACE` is set.
