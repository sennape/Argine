// Argine Research 5 (ETS, "exposed transfer stall") -- causal deferred-wait scheduling.
//
// Header-only and free of ggml/llama.cpp so that the exact code the runtime hook
// runs can be exercised by model-free tests (patches/argine-ets-test.cpp).
//
// What it does. The expert-store hook runs on every MUL_MAT_ID node. The first
// expert node of a layer (one "event": same layer, kinds not yet seen) issues the
// reads of ALL weight kinds (gate, up, down) of the experts the router of THIS
// layer selected. In the baseline arm A (ARGINE_EXPERT_WAIT unset or "layer",
// existing code path in argine-experts.cpp, not this file) every thread then waits
// for all of them before any expert compute; later nodes release and re-acquire
// the slices as hits. The two R5 arms implemented here keep everything the
// baseline requests, in the same order, and change only the wait placement:
//
//   layer_touch (C0, bookkeeping control): pins held for the whole layer event;
//       later nodes LRU-touch instead of re-acquiring; every thread still waits
//       for ALL its reads at the first node (baseline wait placement).
//   kind (C, the R5 scheduler): same bookkeeping as layer_touch, but each node
//       waits only for the reads of the kind it consumes; reads of later kinds
//       stay in flight while earlier nodes compute.
//
// Information used: the router decision of the CURRENT layer, already used by
// the baseline. No prediction, no lookahead across layers, no new thread, no
// tunable parameter. In-flight slices are reserved (ready == false) and pinned,
// so they cannot be evicted; the pinned footprint is the layer's slices, as in
// the baseline. See experiments/R5-00-exposed-transfer-stall for the protocol.

#ifndef ARGINE_ETS_H
#define ARGINE_ETS_H

#include "argine-store.h"

#include <algorithm>
#include <atomic>
#include <cstring>
#include <utility>
#include <vector>

enum psx_wait_mode {
    PSX_WAIT_LAYER       = 0,  // A: baseline (argine-experts.cpp existing path)
    PSX_WAIT_LAYER_TOUCH = 1,  // C0: bookkeeping control
    PSX_WAIT_KIND        = 2,  // C: deferred wait per kind
};

inline const char * psx_wait_mode_name(psx_wait_mode m) {
    switch (m) {
        case PSX_WAIT_LAYER_TOUCH: return "layer_touch";
        case PSX_WAIT_KIND:        return "kind";
        default:                   return "layer";
    }
}

// ARGINE_EXPERT_WAIT. Unset or empty -> layer (explicit = 0, the unchanged
// baseline). Exactly "layer", "layer_touch" or "kind" -> that mode (explicit = 1).
// Anything else -> 0 (invalid: the caller must abort, never fall back).
inline int psx_wait_mode_parse(const char * v, psx_wait_mode * out, int * explicit_out) {
    *explicit_out = 0;
    *out          = PSX_WAIT_LAYER;
    if (!v || !*v) {
        return 1;
    }
    *explicit_out = 1;
    if (strcmp(v, "layer") == 0) {
        *out = PSX_WAIT_LAYER;
        return 1;
    }
    if (strcmp(v, "layer_touch") == 0) {
        *out = PSX_WAIT_LAYER_TOUCH;
        return 1;
    }
    if (strcmp(v, "kind") == 0) {
        *out = PSX_WAIT_KIND;
        return 1;
    }
    return 0;
}

// Fail-closed configuration check. Returns 1 if the combination is a valid
// scientific arm, 0 with *err set otherwise.
//   * any explicit ARGINE_EXPERT_WAIT requires the legacy predictive prefetcher
//     off (ARGINE_PREFETCH unset or "0"): R5 never combines prediction with it;
//   * layer_touch and kind require the asincrono read path and no retention
//     policy (the frozen R5 baseline is the asincrono LRU path).
inline int psx_wait_mode_check(psx_wait_mode m, int explicit_mode, int read_mode_is_async, int has_policy,
                               const char * prefetch_env, const char ** err) {
    *err = nullptr;
    if (explicit_mode && prefetch_env && *prefetch_env && strcmp(prefetch_env, "0") != 0) {
        *err = "ARGINE_EXPERT_WAIT richiede ARGINE_PREFETCH non impostato o 0: il prefetch predittivo "
               "non e' un braccio R5 e non va combinato con l'attesa differita";
        return 0;
    }
    if (m != PSX_WAIT_LAYER && !read_mode_is_async) {
        *err = "ARGINE_EXPERT_WAIT=layer_touch|kind richiede ARGINE_EXPERT_READ=asincrono (il percorso "
               "di base congelato da R5)";
        return 0;
    }
    if (m != PSX_WAIT_LAYER && has_policy) {
        *err = "ARGINE_EXPERT_WAIT=layer_touch|kind non e' compatibile con ARGINE_RETENTION_POLICY: R5 "
               "cambia solo il punto di attesa del percorso LRU asincrono";
        return 0;
    }
    return 1;
}

// One weight kind of the current layer, as the hook's `dove` table: the kinds
// are listed with the current node's kind first, then the others in enum order.
struct psx_ets_slice {
    psx_region *  region;
    size_t        t_off;
    size_t        slab;
    enum psx_kind kind;
};

// Process-wide conformance counters (atomics: every thread updates them).
struct psx_ets_counters {
    std::atomic<uint64_t> first_nodes{ 0 };          // events started (counted once per thread 0 call)
    std::atomic<uint64_t> followup_nodes{ 0 };
    std::atomic<uint64_t> deferred_reads{ 0 };       // reads left in flight past the first node's wait
    std::atomic<uint64_t> deferred_bytes{ 0 };
    std::atomic<uint64_t> orphan_pending{ 0 };       // reads still pending when a new event started (must be 0)
    std::atomic<uint64_t> pending_at_event_end{ 0 }; // reads pending after the last consuming node (must be 0)
    std::atomic<uint64_t> touches{ 0 };              // LRU touches in place of baseline re-acquisitions
    std::atomic<uint64_t> issued_followup{ 0 };      // reads issued at a later node (kind not known at first node)
    std::atomic<uint64_t> followup_mismatch{ 0 };    // later node whose experts / slices differ from the first node's (fatal)
    std::atomic<uint64_t> touch_missing{ 0 };        // touched slice not in the cache (fatal)
};

// Per-thread state. Every thread executes every node, so each thread classifies
// the node sequence itself (deterministic, no synchronisation needed).
struct psx_ets_thread {
    psx_semantic_event_tracker                    evt;
    uint32_t                                      covered  = 0;  // kinds issued for the current event
    uint32_t                                      consumed = 0;  // kinds whose node has run
    std::vector<std::pair<psx_region *, size_t>>  pins;          // this thread's pins for the event
    std::vector<uint32_t>                         experts;       // the first node's experts (fail-closed check)
    psx_ets_slice                                 slices[PSX_N_KINDS];  // the event's slice table by kind
    uint32_t                                      known    = 0;  // kinds with an entry in slices[]
};

inline uint32_t psx_ets_kinds_of(const psx_ets_slice * sl, int n) {
    uint32_t m = 0;
    for (int i = 0; i < n; i++) {
        m |= PSX_KIND_BIT(sl[i].kind);
    }
    return m;
}

// Before the hook's first barrier. Classifies the node; on the first node of a
// new event, waits for any read still pending from the previous event (counted:
// conformance requires zero) and only then releases the previous event's pins,
// so no in-flight slice is ever unpinned. Returns 1 for a first node, 0 for a
// follow-up; *ok = 0 if an orphan read failed (fatal for the caller).
inline int psx_ets_pre(psx_ets_thread & t, psx_cache * c, psx_store * s, uint32_t layer, enum psx_kind kind,
                       psx_ets_counters & cnt, int * ok) {
    *ok = 1;
    const psx_semantic_event_tracker::Result r = t.evt.classify(layer, (uint32_t) kind);
    if (!r.first_node) {
        return 0;
    }
    uint64_t       b      = 0;
    const uint32_t orphan = psx_cache_pending_reads(PSX_ALL_KINDS, &b);
    if (orphan) {
        cnt.orphan_pending.fetch_add(orphan, std::memory_order_relaxed);
        struct psx_wait_report rep;
        if (!psx_cache_attendi_kinds(c, s, PSX_ALL_KINDS, &rep)) {
            *ok = 0;
            return 1;
        }
    }
    for (const auto & p : t.pins) {
        psx_cache_unpin(c, p.first, p.second);
    }
    t.pins.clear();
    t.covered  = 0;
    t.consumed = 0;
    return 1;
}

// After the hook's first barrier: issue / touch / wait for one node.
//   first node: psx_cache_avvia for every (kind, expert) in the baseline's claim
//               order (shared counter, dynamic distribution), pins kept for the event;
//   later node: kinds covered at the first node are LRU-touched in the same claim
//               order (no pin, no wait); a kind not covered then (its tensor
//               position was unknown) is issued here, as the baseline does;
//   wait:       layer_touch -> every kind on the first node (baseline placement);
//               kind        -> only this node's kind (plus any kind first issued here).
// `counter` must have been reset to 0 by one thread before the first barrier.
// Returns 1 on success, 0 if a read failed (fatal for the node: abort).
inline int psx_ets_io(psx_ets_thread & t, psx_wait_mode mode, psx_cache * c, psx_store * s, uint32_t layer,
                      enum psx_kind node_kind, const psx_ets_slice * sl, int n_sl, const uint32_t * experts,
                      uint32_t n_experts, std::atomic<uint32_t> & counter, int first_node, psx_ets_counters & cnt,
                      struct psx_wait_report * rep) {
    const uint32_t all_here    = psx_ets_kinds_of(sl, n_sl);
    const uint32_t covered_pre = first_node ? 0u : t.covered;
    const uint32_t total       = n_experts * (uint32_t) n_sl;
    int            ok          = 1;
    // Fail-closed consistency (the retention-policy path has the same guard): a later
    // node of the event must ask for exactly the first node's experts and, for every
    // kind already issued, exactly the same slices. Otherwise touching would let a node
    // compute on memory that was never read for it.
    if (first_node) {
        t.experts.assign(experts, experts + n_experts);
        t.known = 0;
    } else {
        bool same = t.experts.size() == n_experts && std::equal(t.experts.begin(), t.experts.end(), experts);
        for (int q = 0; q < n_sl && same; q++) {
            const uint32_t b = PSX_KIND_BIT(sl[q].kind);
            if ((covered_pre & b) && (t.known & b)) {
                const psx_ets_slice & o = t.slices[sl[q].kind];
                same = o.region == sl[q].region && o.t_off == sl[q].t_off && o.slab == sl[q].slab;
            }
        }
        if (!same) {
            cnt.followup_mismatch.fetch_add(1, std::memory_order_relaxed);
            if (rep) {
                *rep = psx_wait_report{};
            }
            return 0;
        }
    }
    for (int q = 0; q < n_sl; q++) {
        if (!(t.known & PSX_KIND_BIT(sl[q].kind))) {
            t.slices[sl[q].kind] = sl[q];
            t.known |= PSX_KIND_BIT(sl[q].kind);
        }
    }
    for (;;) {
        const uint32_t k = counter.fetch_add(1, std::memory_order_relaxed);
        if (k >= total) {
            break;
        }
        const psx_ets_slice & d   = sl[k / n_experts];
        const uint32_t        x   = experts[k % n_experts];
        const size_t          off = d.t_off + (size_t) x * d.slab;
        if (covered_pre & PSX_KIND_BIT(d.kind)) {
            if (!psx_cache_touch_checked(c, d.region, off)) {   // pinned since the first node: must be there
                cnt.touch_missing.fetch_add(1, std::memory_order_relaxed);
                ok = 0;
                break;
            }
            cnt.touches.fetch_add(1, std::memory_order_relaxed);
            continue;
        }
        if (psx_cache_avvia(c, s, d.region, off, layer, x, d.kind) == 0) {
            ok = 0;
            break;
        }
        t.pins.emplace_back(d.region, off);
        if (!first_node) {
            cnt.issued_followup.fetch_add(1, std::memory_order_relaxed);
        }
    }
    const uint32_t newly = all_here & ~covered_pre;  // identical on every thread
    t.covered |= newly;
    uint32_t wait_mask;
    if (mode == PSX_WAIT_KIND) {
        wait_mask = PSX_KIND_BIT(node_kind) | (first_node ? 0u : newly);
    } else {
        wait_mask = first_node ? PSX_ALL_KINDS : (newly | PSX_KIND_BIT(node_kind));
    }
    struct psx_wait_report r{};
    if (!psx_cache_attendi_kinds(c, s, wait_mask, &r)) {
        ok = 0;
    }
    t.consumed |= PSX_KIND_BIT(node_kind);
    if (first_node) {
        cnt.deferred_reads.fetch_add(r.pending_after, std::memory_order_relaxed);
        uint64_t b = 0;
        psx_cache_pending_reads(PSX_ALL_KINDS, &b);
        cnt.deferred_bytes.fetch_add(b, std::memory_order_relaxed);
    }
    if ((t.consumed & t.covered) == t.covered && r.pending_after) {
        // Every issued kind has had its node and something is still pending:
        // a conformance failure. Count it and wait, so nothing leaves the event in flight.
        cnt.pending_at_event_end.fetch_add(r.pending_after, std::memory_order_relaxed);
        struct psx_wait_report r2;
        if (!psx_cache_attendi_kinds(c, s, PSX_ALL_KINDS, &r2)) {
            ok = 0;
        }
        r.pending_after = 0;
    }
    if (rep) {
        *rep = r;
    }
    return ok;
}

#endif  // ARGINE_ETS_H
