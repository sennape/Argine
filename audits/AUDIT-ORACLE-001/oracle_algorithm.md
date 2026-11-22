# AUDIT-ORACLE-001 — Oracle Algorithm Audit

## Canonical implementation

- Module: `argine/research/oracle.py`
- Class: `BeladyGlobal` (name `belady-global`, `family="oracle"`, `needs_future=True`)
- Base: `_BeladyBase` builds full future event list and next-use map at construction.
- R2-02 declaration: `argine/research2/r2_02/policy_set.py` labels it `Oracle-Global-Belady`, `causal=False`, `BeladyGlobal(trace, phase=PHASE_DECODE, semantics="v2")`.
- R2-02/03/04 execution: `argine/research2/r2_02/replay_multi.py` → `argine.research.replay.replay(...)` with `make_context(trace,budget,sizes)` and `ctx.bytes_of(layer)`.

The implementation's own docstring states: "Events here are multi-key (all members pinned together) and sizes may differ between layers; farthest-next-use is then a strong reference but NOT proven optimal. Results are labelled 'Belady-style'." R2-02/paper framing used "Oracle-Global-Belady"/"Belady-optimal oracle"/"Oracle-Bounded" terminology for the same comparator.

## Cache model / semantics

| Property | Value / behavior | Evidence |
|---|---|---|
| Cache object key | `(layer, expert)` | `BeladyGlobal._access`: `k = (layer, e)`; `replay.replay` serves per-layer expert selections from `st.experts(l)`. |
| Object granularity | One expert object per layer (all selected experts in a step/layer event are requested as members of that event). Not slice-level StoreReplay for R2-02/03/04. | `replay.replay`, `oracle.event_list`, R2-02 `replay_multi.py`. |
| Capacity units | Bytes (expert-memory budget). | `ctx.budget` in bytes; `resident_bytes + b <= self.ctx.budget`; R2-02 fixed budget 4 GiB = 4294967296 bytes. |
| Object size source | `b = self.ctx.bytes_of(layer)` = `trace.meta.expert_bytes[layer]` (per layer). | `replay.make_context`, trace meta; canonical trace has two sizes across 48 layers. |
| Request granularity | Event = `(step_index, layer, experts_tuple)`; replay processes each served step/layer event. `access(step, layer, experts)` handles all experts in that event. | `oracle.event_list` uses `served_steps`/`st.experts(l)`; `replay.replay` calls `policy.access(i,l,ex)`. |
| top-k / simultaneous requests | All members of the current event are protected during eviction (`members = {(layer,e) for e in experts}`; candidates in `members` are pushed to `held` and not evicted). Requested-later in the same event not evicted before earlier misses in that event's handling loop; members are pinned logically by membership check. | `BeladyGlobal._access`. |
| Future-information representation | Full event list `self.events = event_list(trace, phase, semantics)` and `self.nu = next_uses(self.events)` computed at construction (`_BeladyBase.__init__`). | `oracle.py`. |
| Next-use calculation | For event `j`, `nu[e] = nxt.get((l,e), INF)` = index of next event using `(layer,e)` in the same future event sequence, else `INF`. `nxt` updated by scanning events backwards. | `next_uses`. |
| Admission semantics | On miss, if object fits after evictions: insert as resident with `self.res[k]=b`, `resident_bytes += b`, push heap entry. On failure to make space (all remaining residents are current members): object not inserted (`overflow_misses += 1`). | `BeladyGlobal._access`. |
| Eviction semantics | While `resident_bytes + b > budget`, pop heap candidates (stale-version filtered; current-event members held aside) until finding first non-member victim; evict that victim (`resident_bytes -= res.pop(victim)`, `evictions += 1`). One object evicted per iteration; repeats until space available or no victim possible. | `BeladyGlobal._access`. |
| Evicted object count per admission | One or more objects, as needed to satisfy byte capacity; selected one at a time. | Loop in `_access`. |
| Size participates in eviction scoring | **No.** Victim selection is by farthest next use only: heap key is `(-nu[e], v, k)`; smallest `-nu[e]` (largest `nu[e]`, farthest future / INF) is evicted first among eligible non-member residents. Object byte size is used for capacity/miss-byte accounting, not for victim ranking. | `BeladyGlobal._access`. |
| Tie-breaking | Heap orders by `(-next_use, version, key)`; Python heapq compares tuples. Equal next-use ties break by version then key (implementation detail), not by size. | `BeladyGlobal._access`. |
| Compulsory/cold-miss treatment | No special case beyond ordinary miss when not resident. | `_access`. |
| Future distance per object | Yes: per `(layer, expert)` key in the future event index space (`nxt[(l,e)] = j`). | `next_uses`. |
| Optimization objective explicit? | **No explicit byte/miss-count optimization objective in code.** Implements farthest-next-use replacement; comment states "Belady-style" and not proven optimal when sizes differ. | `oracle.py` module/class docstrings/comments. |

## Pseudocode (equivalent precise description)

```text
init:
  events = list of (step, layer, experts) in served decode/v2 order
  nu[j][e] = next event index using (layer,e) after j, else +∞
  heap = min-heap by (-next_use, version, key)
  res = resident map key -> bytes, resident_bytes = 0

access(step, layer, experts):
  assert current event matches precomputed events[j]; nu = nu[j]; j++
  b = bytes_of(layer); members = {(layer,e) for e in experts}
  for e in experts:
    k=(layer,e); version[k]++
    if k resident:
      push(heap, (-nu[e], version[k], k)); continue
    miss
    while resident_bytes + b > budget:
      pop eligible heap entries with valid version, not stale, not in members
      if none: overflow miss for this object; break
      evict victim (resident_bytes -= bytes(victim))
    if space made:
      admit k (resident_bytes += b); push heap (-nu[e], version[k], k)
```

## Classification

**O2 — classical furthest-next-use Belady under variable object sizes.**

Rationale: eviction is the standard farthest-in-future (MIN/Belady-style) rule; cache capacity is byte-valued and cacheable expert objects have variable byte sizes across layers in the canonical R2 trace. The algorithm is not O1 because objects are not exactly equal-sized. It is not O3 because no size-aware clairvoyant scoring/optimization rule is used for eviction. It is not another explicitly proven byte-optimal variable-size clairvoyant algorithm (O4 would require stating an exact proven byte-optimal rule; the implementation states non-optimality when sizes differ).

## Objective/optimality implication

Farthest-next-use is optimal for the classical equal-size paging model under the appropriate assumptions. It is **not** generally optimal for variable-size objects under byte capacity for miss count or miss bytes. This implementation provides no proof of miss-count optimality or miss-byte optimality for the actual R2 semantics (multi-key events, byte budget, per-layer variable object sizes). Therefore miss-count and miss-byte optimality are **NOT_PROVEN_OPTIMAL** from this implementation. CASE C applies.