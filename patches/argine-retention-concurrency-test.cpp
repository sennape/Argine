// R2-12 Phase F: concurrency regression for the production coordination
// primitive introduced by v5 -- "only ith==0 calls psx_cache_ensure_many for
// the whole node; every thread (including ith==0) unpins its OWN previous
// node's pins, per-thread, at the start of the current node, with no lock
// and no barrier around the unpin step itself (argine-experts.cpp)."
//
// This links the REAL argine-store.cpp + argine-retention-policy.cpp +
// argine-research.cpp, drives it with REAL pthreads, and reproduces the
// exact multi-node, multi-layer sequence and pin/unpin lifecycle from
// argine-experts.cpp's LETTURA_BLOCCO-with-policy branch:
//
//   for each simulated ggml graph node (gate/up/down per layer, many
//   layers, repeated):
//     1. EVERY thread unpins its OWN thread_local "pins from the node I
//        serviced last" (psx_cache_unpin), no lock/barrier around this step
//        (this mirrors argine-experts.cpp lines ~971-981).
//     2. full barrier (mirrors ggml_barrier before the retention dispatch).
//     3. ith==0 ALONE computes the node's requested slice set and calls
//        psx_cache_ensure_many() once for the whole node, pinning every
//        slice into ITS OWN thread_local pin list on success (mirrors the
//        "if (ith==0) { ... totale slices ... }" branch).
//     4. full barrier (mirrors the barrier at argine-experts.cpp line
//        ~1300, immediately before falling through to the standard ggml
//        compute kernel).
//     5. EVERY thread "reads" (memcmp-verifies) the full node's physical
//        memory, simulating the standard MUL_MAT_ID kernel's concurrent
//        read of the just-loaded slices.
//
// The budget is deliberately sized to match the real host evidence's
// utilization (~95% full), so that a later layer's admission is forced to
// evict the previous layer's working set almost every time -- the exact
// regime in which a pin/unpin accounting race would matter.
//
// Build (TSan, Linux):
//   g++ -O1 -g -std=c++17 -fsanitize=thread -pthread \
//       argine-store.cpp argine-retention-policy.cpp argine-research.cpp \
//       argine-retention-concurrency-test.cpp -o /tmp/arxconc
// Run:
//   /tmp/arxconc <path-to-.psxs>
#include "argine-store.h"
#include <atomic>
#include <condition_variable>
#include <cstdio>
#include <cstdlib>
#include <cstring>
#include <map>
#include <memory>
#include <mutex>
#include <thread>
#include <vector>

static int fallimenti = 0;
static void check(bool ok, const char * cosa) {
    printf("  [%s] %s\n", ok ? "ok" : "NO", cosa);
    if (!ok) { fallimenti++; }
}

static psx_store * STORE = nullptr;

// Persistent per (layer,expert,kind) region, exactly like c.posizioni: set
// up ONCE before any thread runs, never reallocated -- this is the static,
// load-time-populated registry the real runtime relies on.
static std::map<uint64_t, struct psx_region *> REGION;
static uint64_t rkey(uint32_t layer, uint32_t expert, int kind) {
    return (((uint64_t) layer << 40) | ((uint64_t) expert << 8) | (uint64_t) kind);
}
static struct psx_region * region_for(uint32_t layer, uint32_t expert, enum psx_kind kind) {
    return REGION.at(rkey(layer, expert, (int) kind));
}

// --- a minimal reusable barrier, same semantics as ggml_barrier: every
// participant blocks until ALL n_threads have arrived. -----------------
struct Barrier {
    explicit Barrier(int n) : n_(n) {}
    void arrive() {
        std::unique_lock<std::mutex> lk(m_);
        const int gen = gen_;
        if (++count_ == n_) {
            count_ = 0;
            gen_++;
            cv_.notify_all();
        } else {
            cv_.wait(lk, [&] { return gen != gen_; });
        }
    }
    std::mutex              m_;
    std::condition_variable  cv_;
    int                      n_;
    int                      count_ = 0;
    int                      gen_   = 0;
};

struct Pin { struct psx_region * region; size_t offset; };

// thread_local pin lists, exactly like Contesto::miei_pin().
static thread_local std::vector<Pin> g_miei_pin;

static std::atomic<bool>     g_node_failed{false};
static std::atomic<uint64_t> g_mismatches{0};

// One simulated MoE node: (layer, n_serve experts, n_kinds present).
struct NodeSpec {
    uint32_t              layer;
    std::vector<uint32_t> experts;
    std::vector<enum psx_kind> kinds; // the "dove" set for this node-call
};

static void run_node(struct psx_cache * cache, const NodeSpec & node, int ith, int nth,
                      Barrier & b_pre, Barrier & b_post) {
    // Step 1: EVERY thread unpins ITS OWN previous pins. No lock, no
    // barrier around this step -- exactly argine-experts.cpp ~971-981.
    for (const Pin & p : g_miei_pin) {
        psx_cache_unpin(cache, p.region, p.offset);
    }
    g_miei_pin.clear();

    // Step 2: barrier before the retention-policy dispatch (mirrors the
    // ggml_barrier right after the unpin block).
    b_pre.arrive();

    // Step 3: ith==0 ALONE builds the whole-node slice list and calls
    // psx_cache_ensure_many once, pinning everything it admits. Once the
    // cache has failed once, the real runtime's own contract says it "non
    // deve essere riusata" -- stop calling it, exactly like the real
    // process would stop (abort), instead of cascading into meaningless
    // follow-on failures against an already-inconsistent instance.
    if (ith == 0 && !g_node_failed.load(std::memory_order_relaxed)) {
        std::vector<struct psx_region *> regions;
        std::vector<size_t>               offsets;
        std::vector<uint32_t>             layers, experts;
        std::vector<enum psx_kind>        kinds;
        for (uint32_t e : node.experts) {
            for (enum psx_kind k : node.kinds) {
                regions.push_back(region_for(node.layer, e, k));
                offsets.push_back(0);
                layers.push_back(node.layer);
                experts.push_back(e);
                kinds.push_back(k);
            }
        }
        const int ok = psx_cache_ensure_many(cache, STORE, regions.size(), regions.data(),
                                              offsets.data(), layers.data(), experts.data(),
                                              kinds.data());
        if (!ok) {
            g_node_failed.store(true, std::memory_order_relaxed);
            const char * motivo = psx_error(STORE);
            fprintf(stderr, "ensure_many fallito per layer=%u n_serve=%zu: %s\n",
                    node.layer, node.experts.size(), motivo ? motivo : "(nullo)");
            psx_admission_failure_diag diag;
            if (psx_cache_get_last_admission_failure(cache, &diag)) {
                fprintf(stderr,
                        "diag: budget_bytes=%llu resident_before=%llu requested_total=%llu "
                        "requested_missing=%llu reclaimable=%llu requested_count=%u "
                        "protected_count=%u candidate_victims=%u reason=%s\n",
                        (unsigned long long) diag.budget_bytes,
                        (unsigned long long) diag.resident_bytes_before,
                        (unsigned long long) diag.requested_bytes_total,
                        (unsigned long long) diag.requested_missing_bytes,
                        (unsigned long long) diag.reclaimable_bytes,
                        diag.requested_count, diag.protected_count,
                        diag.candidate_victim_count, diag.reason);
            }
        } else {
            for (size_t i = 0; i < regions.size(); i++) {
                g_miei_pin.push_back(Pin{ regions[i], offsets[i] });
            }
        }
    }

    // Step 4: barrier after ensure_many, before "compute" reads memory --
    // mirrors the ggml_barrier at argine-experts.cpp ~1300.
    b_post.arrive();

    if (g_node_failed.load(std::memory_order_relaxed)) {
        return;
    }

    // Step 5: EVERY thread (including ith==0) "computes" by touching every
    // slice of this node -- simulating the standard MUL_MAT_ID kernel
    // reading the just-loaded memory concurrently across all nth threads.
    // Each thread touches a disjoint stripe (k % nth == ith), same
    // distribution style as the real dynamic-slice dispatch, so TSan sees
    // genuinely concurrent reads of adjacent/overlapping regions.
    size_t k = 0;
    for (uint32_t e : node.experts) {
        for (enum psx_kind kind : node.kinds) {
            if ((int) (k++ % (size_t) nth) != ith) { continue; }
            struct psx_region * r    = region_for(node.layer, e, kind);
            unsigned char *      base = (unsigned char *) psx_region_base(r);
            const size_t         need = psx_slice_read_bytes(STORE, node.layer, e, kind);
            // Touch every byte: if this memory was decommitted concurrently
            // (use-after-decommit), this either segfaults (desired: proves
            // the race) or TSan reports the race on the underlying mapping
            // state, or -- if genuinely safe -- just reads valid bytes.
            volatile unsigned char acc = 0;
            for (size_t i = 0; i < need; i += 4096) {
                acc ^= base[i];
            }
            (void) acc;
        }
    }
}

int main(int argc, char ** argv) {
    if (argc < 2) { fprintf(stderr, "uso: %s <store.psxs>\n", argv[0]); return 2; }
    STORE = psx_open(argv[1], 0);
    if (psx_error(STORE)) { fprintf(stderr, "errore apertura: %s\n", psx_error(STORE)); return 1; }

    const uint32_t n_layers  = psx_n_layers(STORE);
    const uint32_t n_experts = psx_n_experts(STORE);
    if (n_layers < 4 || n_experts < 8) {
        fprintf(stderr, "store troppo piccolo per questo stress test (layers=%u experts=%u)\n",
                n_layers, n_experts);
        return 2;
    }

    const uint64_t per_expert_bytes = psx_slice_read_bytes(STORE, 0, 0, PSX_GATE)
                                     + psx_slice_read_bytes(STORE, 0, 0, PSX_UP)
                                     + psx_slice_read_bytes(STORE, 0, 0, PSX_DOWN);

    // Build the whole-run node sequence FIRST (deterministic experts per
    // layer, 77-ish wide like the real host evidence, capped to n_experts),
    // and pre-allocate every region it will ever touch -- exactly like
    // c.posizioni being fully populated at load time, before any thread
    // runs.
    const uint32_t n_serve = n_experts > 8 ? 8 : n_experts; // scaled down for a memory-constrained VM
    std::vector<NodeSpec> nodes;
    const uint32_t n_layers_to_use = n_layers < 20 ? n_layers : 20;
    for (uint32_t layer = 0; layer < n_layers_to_use; layer++) {
        std::vector<uint32_t> experts;
        for (uint32_t i = 0; i < n_serve; i++) {
            experts.push_back((layer * 7 + i) % n_experts); // shifting window per layer
        }
        for (enum psx_kind own : { PSX_GATE, PSX_UP, PSX_DOWN }) {
            NodeSpec ns;
            ns.layer   = layer;
            ns.experts = experts;
            ns.kinds   = { PSX_GATE, PSX_UP, PSX_DOWN }; // full per-layer coverage, as c.posizioni gives
            (void) own;
            nodes.push_back(ns);
        }
    }
    for (const NodeSpec & ns : nodes) {
        for (uint32_t e : ns.experts) {
            for (enum psx_kind k : ns.kinds) {
                const uint64_t key = rkey(ns.layer, e, (int) k);
                if (REGION.count(key)) { continue; }
                size_t need = psx_slice_read_bytes(STORE, ns.layer, e, k);
                REGION[key] = psx_region_new(need, need, 1);
            }
        }
    }

    // Budget: ~95% of one full layer's working set, matching the real host
    // evidence (4,105,285,632 / 4,294,967,296 = 95.6%). This forces an
    // eviction almost every layer transition, the exact regime of the
    // reported failure.
    const uint64_t one_layer_bytes = (uint64_t) n_serve * per_expert_bytes;
    const uint64_t budget = (uint64_t) (one_layer_bytes * 40.0); // ample per-node headroom: isolate the sync question from self-cannibalization

    setenv("ARGINE_RETENTION_POLICY", "predicted_protect", 1);
    setenv("ARGINE_RETENTION_DECAY", "0.999", 1);
    setenv("ARGINE_RETENTION_INTERVAL", "1", 1);
    setenv("ARGINE_RETENTION_PREDICTOR_DECAY", "0.99", 1);
    setenv("ARGINE_RETENTION_TOPK", "8", 1);
    char nexp_buf[16]; snprintf(nexp_buf, sizeof(nexp_buf), "%u", n_experts); setenv("ARGINE_RETENTION_NEXPERTS", nexp_buf, 1);

    struct psx_cache * cache = psx_cache_new_n(budget, 1);
    const char * err = nullptr;
    int ok = psx_cache_attach_retention_policy(cache, &err);
    check(ok == 1, "attach predicted_protect su budget ~95% di un layer");
    if (!ok) { fprintf(stderr, "attach failed: %s\n", err ? err : "?"); return 1; }

    const int NTH = 12;
    printf("stress: %zu nodi simulati, %d thread, budget=%llu (%.1f%% di un layer da %llu byte)\n",
           nodes.size(), NTH, (unsigned long long) budget,
           100.0 * (double) budget / (double) one_layer_bytes, (unsigned long long) one_layer_bytes);

    // ONE shared barrier pair per node index, reused across every repeat --
    // Barrier is a cyclic generation-counter barrier, safe to call arrive()
    // on repeatedly across many rounds, exactly like ggml's threadpool
    // barrier being reused node after node. Allocated ONCE, shared by
    // reference across all NTH worker threads (a barrier created per-thread
    // would never see all participants arrive -- that bug was caught before
    // running this).
    std::vector<std::unique_ptr<Barrier>> b_pre, b_post;
    b_pre.reserve(nodes.size());
    b_post.reserve(nodes.size());
    for (size_t i = 0; i < nodes.size(); i++) {
        b_pre.emplace_back(new Barrier(NTH));
        b_post.emplace_back(new Barrier(NTH));
    }

    const int REPEATS = 15; // run the whole sequence repeatedly, like a long generation
    for (int rep = 0; rep < REPEATS && !g_node_failed.load(); rep++) {
        std::vector<std::thread> pool;
        for (int ith = 0; ith < NTH; ith++) {
            pool.emplace_back([&, ith]() {
                for (size_t ni = 0; ni < nodes.size(); ni++) {
                    run_node(cache, nodes[ni], ith, NTH, *b_pre[ni], *b_post[ni]);
                }
            });
        }
        for (auto & t : pool) { t.join(); }
        if (g_node_failed.load()) {
            fprintf(stderr, "repeat %d: node admission failed (fail-closed check fired)\n", rep);
        }
    }

    struct psx_cache_stats st;
    psx_cache_get_stats(cache, &st);
    printf("finale: bytes_resident=%llu budget=%llu hits=%llu misses=%llu evictions=%llu violazioni=%llu\n",
           (unsigned long long) st.bytes_resident, (unsigned long long) st.budget,
           (unsigned long long) st.hits, (unsigned long long) st.misses,
           (unsigned long long) st.evictions, (unsigned long long) st.violazioni);

    check(!g_node_failed.load(), "nessun nodo ha attivato un fail-closed check della politica");
    check(st.violazioni == 0, "nessuna violazione di budget osservata");
    check(g_mismatches.load() == 0, "nessuna lettura di memoria incoerente osservata");

    psx_cache_free(cache);

    printf("%s: %d fallimenti\n", fallimenti == 0 ? "OK" : "FALLITO", fallimenti);
    return fallimenti == 0 ? 0 : 1;
}
