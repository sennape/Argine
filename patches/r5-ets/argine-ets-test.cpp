// Research 5 (ETS) -- model-free conformance tests for the deferred-wait arms.
//
// Exercises the exact scheduler code the runtime hook runs (argine-ets.h) against
// the real expert store (argine-store.cpp) on a small synthetic .psxs store, with a
// persistent thread team that mimics the ggml thread pool (every thread runs every
// node, with the hook's two barriers). The baseline arm A is reproduced by
// node_baseline(), a line-by-line emulation of the LETTURA_ASINCRONA block of
// argine-experts.cpp (unpin own pins, barrier, dynamic avvia loop, psx_cache_attendi,
// barrier), which is the code path the R5 build leaves unchanged.
//
// Deterministic: on POSIX, psx_debug_set_posix_deferred_reads(1) keeps every issued
// read "in flight" until a wait performs it, so which bytes exist at which node is a
// fact, not a timing accident. No sleeps are used to create ordering.
//
//   g++ -O1 -g -std=c++17 -pthread -I. argine-store.cpp argine-research.cpp argine-ets-test.cpp -o etstest
//   ./etstest <scratch-dir>
//
// On Windows (cross-compiled) the deferred backend does not exist; the checks that
// need it are skipped and reported as such, the others run against real OVERLAPPED I/O.
#include "argine-ets.h"
#include "argine-research.h"
#include "argine-store.h"
#include "argine-tls.h"
#if defined(_WIN32)
#    include <malloc.h>
#    include <windows.h>
#endif

#include <algorithm>
#include <atomic>
#include <condition_variable>
#include <cstdint>
#include <cstdio>
#include <cstdlib>
#include <cstring>
#include <functional>
#include <map>
#include <mutex>
#include <set>
#include <string>
#include <thread>
#include <tuple>
#include <vector>

// ETS_TEST_NO_DEFERRED: run the Windows test plan on POSIX (immediate reads), to validate it before a host run.
#if defined(_WIN32) || defined(ETS_TEST_NO_DEFERRED)
static const bool kDeferredBackend = false;
#else
static const bool kDeferredBackend = true;
#endif

static int g_fail = 0, g_pass = 0, g_skip = 0;
static void check(bool ok, const std::string & what) {
    if (ok) {
        g_pass++;
    } else {
        g_fail++;
        printf("  FAIL: %s\n", what.c_str());
    }
}
static void skip(const std::string & what) {
    g_skip++;
    printf("  SKIP (needs the POSIX deferred backend): %s\n", what.c_str());
}

// ------------------------------------------------------------------ synthetic store
static const uint32_t kAlign = 4096;
static const uint32_t kSize[PSX_N_KINDS] = { 4096, 4096, 8192 };  // gate, up, down (down larger, like Q6_K)

static unsigned char pattern(uint32_t l, uint32_t x, uint32_t k, size_t i) {
    return (unsigned char) (1 + ((l * 31u + x * 7u + k * 3u + (uint32_t) i) % 251u));  // never 0
}

static std::string make_store(const std::string & dir, uint32_t L, uint32_t E) {
    const std::string path   = dir + "/r5-ets-synthetic.psxs";
    const uint64_t    lt     = 256;
    const uint64_t    gt     = (lt + (uint64_t) L * 4 + 7) / 8 * 8;
    const uint64_t    gt_end = gt + (uint64_t) L * E * 36;
    const uint64_t    data   = (gt_end + kAlign - 1) / kAlign * kAlign;
    uint64_t          off    = data;
    std::vector<unsigned char> table((size_t) L * E * 36, 0);
    std::vector<uint64_t>      offs((size_t) L * E * 3);
    for (uint32_t l = 0; l < L; l++)
        for (uint32_t x = 0; x < E; x++)
            for (uint32_t k = 0; k < 3; k++) {
                offs[((size_t) l * E + x) * 3 + k] = off;
                off += (kSize[k] + kAlign - 1) / kAlign * kAlign;
            }
    const uint64_t total = off;
    std::vector<unsigned char> file((size_t) total, 0);
    auto put32 = [&](size_t o, uint32_t v) { memcpy(&file[o], &v, 4); };
    auto put64 = [&](size_t o, uint64_t v) { memcpy(&file[o], &v, 8); };
    put32(0, PSX_MAGIC); put32(4, PSX_VERSION); put32(12, kAlign); put32(16, L); put32(20, E); put32(24, PSX_N_KINDS);
    put64(32, data); put64(40, total); put64(48, lt); put64(56, gt); put64(64, total);
    for (uint32_t l = 0; l < L; l++) put32((size_t) (lt + l * 4), l);
    for (uint32_t l = 0; l < L; l++)
        for (uint32_t x = 0; x < E; x++) {
            unsigned char * p = &file[(size_t) (gt + ((uint64_t) l * E + x) * 36)];
            for (uint32_t k = 0; k < 3; k++) {
                const uint64_t o = offs[((size_t) l * E + x) * 3 + k];
                memcpy(p + k * 8, &o, 8);
                memcpy(p + 24 + k * 4, &kSize[k], 4);
                for (size_t i = 0; i < kSize[k]; i++) file[(size_t) o + i] = pattern(l, x, k, i);
            }
        }
    FILE * f = fopen(path.c_str(), "wb");
    if (!f) { printf("cannot write %s\n", path.c_str()); exit(2); }
    fwrite(file.data(), 1, file.size(), f);
    fclose(f);
    return path;
}

// ------------------------------------------------------------------ thread team
struct Barrier {
    std::mutex m; std::condition_variable cv; int n, waiting = 0; uint64_t gen = 0;
    explicit Barrier(int n_) : n(n_) {}
    void wait() {
        std::unique_lock<std::mutex> lk(m);
        const uint64_t g = gen;
        if (++waiting == n) { waiting = 0; gen++; cv.notify_all(); return; }
        cv.wait(lk, [&] { return gen != g; });
    }
};

struct Team {
    int nth; Barrier bar; std::vector<std::thread> th; std::mutex m; std::condition_variable cv;
    std::function<void(int)> job; uint64_t epoch = 0; int done = 0; bool stop = false;
    explicit Team(int n) : nth(n), bar(n) {
        for (int i = 0; i < n; i++) th.emplace_back([this, i] { loop(i); });
    }
    void loop(int ith) {
        uint64_t seen = 0;
        for (;;) {
            std::function<void(int)> j;
            {
                std::unique_lock<std::mutex> lk(m);
                cv.wait(lk, [&] { return stop || epoch != seen; });
                if (stop) return;
                seen = epoch; j = job;
            }
            j(ith);
            std::lock_guard<std::mutex> lk(m);
            if (++done == nth) cv.notify_all();
        }
    }
    void run(std::function<void(int)> f) {
        {
            std::lock_guard<std::mutex> lk(m);
            job = std::move(f); done = 0; epoch++;
        }
        cv.notify_all();
        std::unique_lock<std::mutex> lk(m);
        cv.wait(lk, [&] { return done == nth; });
    }
    ~Team() {
        { std::lock_guard<std::mutex> lk(m); stop = true; }
        cv.notify_all();
        for (auto & t : th) t.join();
    }
};

// ------------------------------------------------------------------ rig
struct Rig {
    uint32_t L, E; int nth; psx_wait_mode mode;
    psx_store * s = nullptr; psx_cache * c = nullptr;
    std::vector<psx_region *> reg;  // [l*3+k]
    size_t slab[3];
    Team team;
    std::atomic<uint32_t> counter{ 0 };
    psx_ets_counters cnt;
    std::vector<psx_ets_thread> ets;                       // per team thread (== the hook's thread_local)
    std::vector<std::vector<std::pair<psx_region *, size_t>>> base_pins;  // baseline arm per thread
    std::vector<psx_wait_report> last_rep;                 // per thread, last node
    bool bytes_ok = true;
    Rig(const std::string & store, uint32_t L_, uint32_t E_, uint64_t budget, int nth_, psx_wait_mode m)
        : L(L_), E(E_), nth(nth_), mode(m), team(nth_), ets(nth_), base_pins(nth_), last_rep(nth_) {
        s = psx_open(store.c_str(), 0);
        if (psx_error(s)) { printf("store: %s\n", psx_error(s)); exit(2); }
        c = psx_cache_new_n(budget, 1);
        for (uint32_t k = 0; k < 3; k++) slab[k] = (kSize[k] + kAlign - 1) / kAlign * kAlign;
        for (uint32_t l = 0; l < L; l++)
            for (uint32_t k = 0; k < 3; k++) reg.push_back(psx_region_new(E * slab[k], slab[k], E));
    }
    ~Rig() {
        // release every pin so the cache can be freed cleanly
        team.run([&](int ith) {
            if (mode != PSX_WAIT_LAYER) {
                struct psx_wait_report r;
                psx_cache_attendi_kinds(c, s, PSX_ALL_KINDS, &r);
                for (auto & p : ets[ith].pins) psx_cache_unpin(c, p.first, p.second);
                ets[ith].pins.clear();
            } else {
                psx_cache_attendi(c, s);
                for (auto & p : base_pins[ith]) psx_cache_unpin(c, p.first, p.second);
                base_pins[ith].clear();
            }
            arx_flush_thread();
        });
        psx_cache_free(c);
        for (auto * r : reg) psx_region_free(r);
        psx_close(s);
    }
    // the hook's `dove`: this node's kind first, then the others in enum order
    int dove(uint32_t l, psx_kind node_kind, psx_ets_slice * out, uint32_t known_mask = PSX_ALL_KINDS) {
        int n = 0;
        out[n++] = psx_ets_slice{ reg[l * 3 + node_kind], 0, slab[node_kind], node_kind };
        for (uint32_t k = 0; k < 3; k++)
            if (k != (uint32_t) node_kind && (known_mask & PSX_KIND_BIT(k)))
                out[n++] = psx_ets_slice{ reg[l * 3 + k], 0, slab[k], (psx_kind) k };
        return n;
    }
    bool present(uint32_t l, uint32_t x, uint32_t k) {
        const unsigned char * p = (const unsigned char *) psx_region_base(reg[l * 3 + k]) + x * slab[k];
        for (size_t i = 0; i < kSize[k]; i += 509) if (p[i] != pattern(l, x, k, i)) return false;
        return p[kSize[k] - 1] == pattern(l, x, k, kSize[k] - 1);
    }
    // One expert node, as the hook runs it. Returns 1 if every thread succeeded.
    int node(uint32_t l, psx_kind kind, const std::vector<uint32_t> & experts, uint32_t known_mask = PSX_ALL_KINDS,
             std::function<void(int)> between = nullptr) {
        std::atomic<int> ok{ 1 };
        psx_ets_slice sl[3];
        const int     n_sl = dove(l, kind, sl, known_mask);
        team.run([&](int ith) {
            int first = 1;
            if (mode == PSX_WAIT_LAYER) {
                for (auto & p : base_pins[ith]) psx_cache_unpin(c, p.first, p.second);  // baseline: release at node start
                base_pins[ith].clear();
            } else {
                int pre_ok = 1;
                first = psx_ets_pre(ets[ith], c, s, l, kind, cnt, &pre_ok);
                if (!pre_ok) ok = 0;
            }
            if (ith == 0) counter.store(0);
            team.bar.wait();
            if (between) between(ith);
            struct psx_wait_report rep{};
            if (mode == PSX_WAIT_LAYER) {
                const uint32_t total = (uint32_t) experts.size() * (uint32_t) n_sl;
                for (;;) {
                    const uint32_t k = counter.fetch_add(1);
                    if (k >= total) break;
                    const psx_ets_slice & d = sl[k / experts.size()];
                    const uint32_t x = experts[k % experts.size()];
                    const size_t off = d.t_off + (size_t) x * d.slab;
                    if (psx_cache_avvia(c, s, d.region, off, l, x, d.kind) == 0) { ok = 0; break; }
                    base_pins[ith].emplace_back(d.region, off);
                }
                rep.n_awaited = psx_cache_pending_reads(PSX_ALL_KINDS, &rep.bytes_awaited);
                if (!psx_cache_attendi(c, s)) ok = 0;
            } else {
                if (!psx_ets_io(ets[ith], mode, c, s, l, kind, sl, n_sl, experts.data(), (uint32_t) experts.size(),
                                counter, first, cnt, &rep)) ok = 0;
            }
            last_rep[ith] = rep;
            team.bar.wait();
            arx_flush_thread();
        });
        if (ok)
            for (uint32_t x : experts)
                if (!present(l, x, kind)) bytes_ok = false;  // the "kernel" of this node reads only its kind
        return ok.load();
    }
    uint32_t pending_total() {
        std::atomic<uint32_t> n{ 0 };
        team.run([&](int) { n += psx_cache_pending_reads(PSX_ALL_KINDS, nullptr); });
        return n.load();
    }
    psx_cache_stats stats() { psx_cache_stats st; psx_cache_get_stats(c, &st); return st; }
};

static std::vector<uint32_t> route(uint32_t token, uint32_t layer, uint32_t E, uint32_t k) {
    std::vector<uint32_t> v;
    uint32_t h = token * 2654435761u + layer * 40503u + 12345u;
    while (v.size() < k) {
        h = h * 1103515245u + 12345u;
        const uint32_t x = (h >> 8) % E;
        if (std::find(v.begin(), v.end(), x) == v.end()) v.push_back(x);
    }
    return v;
}

// A decode-like workload: tokens x layers x (gate, up, down).
static int run_workload(Rig & r, uint32_t tokens, uint32_t topk, uint32_t token0 = 0) {
    int ok = 1;
    for (uint32_t t = token0; t < token0 + tokens; t++)
        for (uint32_t l = 0; l < r.L; l++) {
            const auto ex = route(t, l, r.E, topk);
            ok &= r.node(l, PSX_GATE, ex); ok &= r.node(l, PSX_UP, ex); ok &= r.node(l, PSX_DOWN, ex);
        }
    return ok;
}

// ------------------------------------------------------------------ trace parsing
struct TraceRec { char t; std::vector<std::string> f; };
static std::vector<TraceRec> read_trace(const std::string & path) {
    std::vector<TraceRec> out;
    FILE * f = fopen(path.c_str(), "rb");
    if (!f) return out;
    char line[8192];
    while (fgets(line, sizeof line, f)) {
        std::string s(line);
        while (!s.empty() && (s.back() == '\n' || s.back() == '\r')) s.pop_back();
        if (s.empty()) continue;
        TraceRec r; r.t = s[0];
        size_t p = s.find('\t');
        while (p != std::string::npos) {
            size_t q = s.find('\t', p + 1);
            r.f.push_back(s.substr(p + 1, q == std::string::npos ? std::string::npos : q - p - 1));
            p = q;
        }
        out.push_back(r);
    }
    fclose(f);
    return out;
}
// (outcome, layer, expert, kind) of misses, and (layer, expert, kind) of evictions, in log order (one thread => causal order)
using Key = std::tuple<int, int, int>;
static void misses_evictions(const std::vector<TraceRec> & tr, std::vector<Key> & m, std::vector<Key> & e) {
    for (const auto & r : tr) {
        if (r.t == 'A' && r.f.size() > 5 && r.f[5] == "m") m.emplace_back(atoi(r.f[2].c_str()), atoi(r.f[3].c_str()), atoi(r.f[4].c_str()));
        if (r.t == 'E' && r.f.size() > 3) e.emplace_back(atoi(r.f[1].c_str()), atoi(r.f[2].c_str()), atoi(r.f[3].c_str()));
    }
}

// ================================================================== tests
static void t_config() {
    printf("[1] mode parsing and fail-closed configuration\n");
    psx_wait_mode m; int ex;
    check(psx_wait_mode_parse(nullptr, &m, &ex) && m == PSX_WAIT_LAYER && ex == 0, "unset -> layer (default, not explicit)");
    check(psx_wait_mode_parse("", &m, &ex) && m == PSX_WAIT_LAYER && ex == 0, "empty -> layer (default)");
    check(psx_wait_mode_parse("layer", &m, &ex) && m == PSX_WAIT_LAYER && ex == 1, "layer");
    check(psx_wait_mode_parse("layer_touch", &m, &ex) && m == PSX_WAIT_LAYER_TOUCH && ex == 1, "layer_touch");
    check(psx_wait_mode_parse("kind", &m, &ex) && m == PSX_WAIT_KIND && ex == 1, "kind");
    for (const char * bad : { "Kind", "kind ", "layer-touch", "layertouch", "deferred", "1", "0", "none", "LAYER", "kind,layer" })
        check(!psx_wait_mode_parse(bad, &m, &ex), std::string("invalid value rejected: '") + bad + "'");
    const char * err;
    check(psx_wait_mode_check(PSX_WAIT_KIND, 1, 1, 0, nullptr, &err), "kind + asincrono + no policy + no prefetch: valid");
    check(psx_wait_mode_check(PSX_WAIT_KIND, 1, 1, 0, "0", &err), "ARGINE_PREFETCH=0 accepted");
    check(!psx_wait_mode_check(PSX_WAIT_KIND, 1, 1, 0, "2", &err) && err, "kind + ARGINE_PREFETCH=2 rejected");
    check(!psx_wait_mode_check(PSX_WAIT_LAYER, 1, 1, 0, "1", &err), "explicit layer + ARGINE_PREFETCH=1 rejected");
    check(psx_wait_mode_check(PSX_WAIT_LAYER, 0, 1, 0, "1", &err), "unset (legacy default) + prefetch: unchanged legacy behaviour");
    check(!psx_wait_mode_check(PSX_WAIT_KIND, 1, 0, 0, nullptr, &err), "kind without asincrono rejected");
    check(!psx_wait_mode_check(PSX_WAIT_LAYER_TOUCH, 1, 0, 0, nullptr, &err), "layer_touch without asincrono rejected");
    check(!psx_wait_mode_check(PSX_WAIT_KIND, 1, 1, 1, nullptr, &err), "kind with a retention policy rejected");
    check(psx_wait_mode_check(PSX_WAIT_LAYER, 1, 0, 1, nullptr, &err), "explicit layer keeps any baseline read mode");
}

static void t_wait_placement(const std::string & store) {
    printf("[2] wait placement: layer and layer_touch wait for every kind at the first node, kind defers\n");
    // Pending-read counts are deterministic on every backend: an issued read stays "pending" (unannounced,
    // ready == false) until the issuing thread waits for it, whatever the device did meanwhile.
    psx_debug_set_posix_deferred_reads(kDeferredBackend ? 1 : 0);
    const std::vector<uint32_t> ex = { 1, 4, 7 };
    for (psx_wait_mode m : { PSX_WAIT_LAYER, PSX_WAIT_LAYER_TOUCH, PSX_WAIT_KIND }) {
        const std::string nm = psx_wait_mode_name(m);
        Rig r(store, 2, 16, 0, 1, m);
        check(r.node(0, PSX_GATE, ex), nm + ": gate node ok");
        uint64_t b_up = 0, b_dn = 0;
        r.team.run([&](int) { psx_cache_pending_reads(PSX_KIND_BIT(PSX_UP), &b_up); psx_cache_pending_reads(PSX_KIND_BIT(PSX_DOWN), &b_dn); });
        bool up_there = true, dn_there = true;
        for (uint32_t x : ex) { up_there &= r.present(0, x, PSX_UP); dn_there &= r.present(0, x, PSX_DOWN); }
        if (m == PSX_WAIT_KIND) {
            check(b_up == 3 * kSize[PSX_UP] && b_dn == 3 * kSize[PSX_DOWN], nm + ": up and down reads still pending after the gate node");
            check(r.last_rep[0].n_awaited == 3 && r.last_rep[0].pending_after == 6 && r.last_rep[0].wait_mask == PSX_KIND_BIT(PSX_GATE),
                  nm + ": gate node waited for exactly its 3 reads (mask = gate), 6 left");
            if (kDeferredBackend) check(!up_there && !dn_there, nm + ": up/down bytes NOT yet in memory when gate computes (real deferral)");
            else skip(nm + ": byte-level absence of up/down while gate computes");
        } else {
            check(b_up == 0 && b_dn == 0, nm + ": nothing pending after the first node");
            check(up_there && dn_there, nm + ": all kinds in memory before the first node computes");
            check(r.last_rep[0].n_awaited == 9, nm + ": first node waited for all 9 reads");
        }
        check(r.node(0, PSX_UP, ex), nm + ": up node ok");
        bool dn2 = true; for (uint32_t x : ex) dn2 &= r.present(0, x, PSX_DOWN);
        if (m == PSX_WAIT_KIND) {
            check(r.last_rep[0].n_awaited == 3 && r.last_rep[0].pending_after == 3, nm + ": up node waited for its 3 reads only");
            if (kDeferredBackend) check(!dn2, nm + ": down still not in memory while up computes");
        } else {
            check(r.last_rep[0].n_awaited == 0, nm + ": later node waits for nothing");
        }
        check(r.node(0, PSX_DOWN, ex), nm + ": down node ok");
        check(r.bytes_ok, nm + ": every node found its own kind's bytes in memory");
        check(r.pending_total() == 0, nm + ": no read pending after the layer");
    }
    psx_debug_set_posix_deferred_reads(0);
}

static void t_same_requests(const std::string & dir, const std::string & store) {
    printf("[3] identical requests, bytes, residency, eviction sequence and LRU order across arms (1 thread, budget pressure)\n");
    std::map<int, std::vector<Key>> M, Ev;
    std::map<int, psx_cache_stats> S;
    const uint64_t budget = 9ull * 3 * 8192;  // forces evictions across layers and tokens
    for (int deferred = 0; deferred < (kDeferredBackend ? 2 : 1); deferred++) {
        psx_debug_set_posix_deferred_reads(deferred);
        for (psx_wait_mode m : { PSX_WAIT_LAYER, PSX_WAIT_LAYER_TOUCH, PSX_WAIT_KIND }) {
            const std::string tp = dir + "/t3-" + psx_wait_mode_name(m) + ".trace";
            arx_debug_open(tp.c_str());
            {
                Rig r(store, 4, 16, budget, 1, m);
                check(run_workload(r, 12, 4), std::string(psx_wait_mode_name(m)) + ": workload ok");
                check(r.bytes_ok, std::string(psx_wait_mode_name(m)) + ": bytes correct at every node");
                S[m] = r.stats();
                check(S[m].violazioni == 0, std::string(psx_wait_mode_name(m)) + ": no budget violation");
                check(r.cnt.orphan_pending == 0 && r.cnt.pending_at_event_end == 0, std::string(psx_wait_mode_name(m)) + ": no orphan / end-of-event pending read");
            }
            arx_debug_open(nullptr);
            auto tr = read_trace(tp);
            M[m].clear(); Ev[m].clear();
            misses_evictions(tr, M[m], Ev[m]);
        }
        const std::string tag = deferred ? " (deferred backend)" : " (immediate backend)";
        for (int m : { 1, 2 }) {
            const std::string nm = psx_wait_mode_name((psx_wait_mode) m);
            check(M[m] == M[0], nm + ": miss sequence (layer, expert, kind) identical to baseline" + tag);
            check(Ev[m] == Ev[0], nm + ": eviction sequence identical to baseline (same LRU order)" + tag);
            check(S[m].misses == S[0].misses && S[m].bytes_read == S[0].bytes_read && S[m].evictions == S[0].evictions,
                  nm + ": misses, bytes read and evictions identical" + tag);
            check(S[m].bytes_resident == S[0].bytes_resident, nm + ": resident bytes identical" + tag);
        }
        check(!Ev[0].empty(), "the workload does exercise evictions" + tag);
    }
    psx_debug_set_posix_deferred_reads(0);
}

static void t_multithread(const std::string & store) {
    printf("[4] multi-threaded (6 threads): correctness, budget, pending reads; totals equal without pressure\n");
    std::map<int, psx_cache_stats> S;
    for (int deferred = 0; deferred < (kDeferredBackend ? 2 : 1); deferred++) {
        psx_debug_set_posix_deferred_reads(deferred);
        for (psx_wait_mode m : { PSX_WAIT_LAYER, PSX_WAIT_LAYER_TOUCH, PSX_WAIT_KIND }) {
            const std::string nm = std::string(psx_wait_mode_name(m)) + (deferred ? " (deferred)" : "");
            {
                Rig r(store, 4, 16, 0, 6, m);
                check(run_workload(r, 10, 6), nm + ": workload ok");
                check(r.bytes_ok, nm + ": bytes correct at every node");
                S[m] = r.stats();
                check(r.pending_total() == 0 || m == PSX_WAIT_LAYER, nm + ": nothing pending after the last node");
                check(r.cnt.orphan_pending == 0 && r.cnt.pending_at_event_end == 0, nm + ": no orphan / end-of-event pending read");
            }
            {
                Rig r(store, 4, 16, 6ull * 3 * 8192 * 2, 6, m);  // tight: evictions with 6 threads racing
                check(run_workload(r, 10, 6, 100), nm + ": workload under pressure ok");
                check(r.bytes_ok, nm + ": bytes correct under pressure");
                check(r.stats().violazioni == 0, nm + ": no budget violation under pressure");
            }
        }
        for (int m : { 1, 2 })
            check(S[m].misses == S[0].misses && S[m].bytes_read == S[0].bytes_read,
                  std::string(psx_wait_mode_name((psx_wait_mode) m)) + ": same misses and bytes as baseline (no pressure)");
    }
    psx_debug_set_posix_deferred_reads(0);
}

static void t_pending_not_evicted(const std::string & dir, const std::string & store) {
    printf("[5] a read in flight is never evicted (budget pressure while up/down are pending)\n");
    // deterministic on every backend: an issued, unannounced slot is ready == false until waited for
    psx_debug_set_posix_deferred_reads(kDeferredBackend ? 1 : 0);
    const std::string tp = dir + "/t5.trace";
    arx_debug_open(tp.c_str());
    uint64_t ev_before_x = 0, ev_after_x = 0;
    {
        // budget: the layer-0 event (3 experts x 3 kinds) + one layer-1 expert (16 KiB):
        // while layer 0's up/down reads are in flight, the only evictable slices are layer 1's.
        const uint64_t layer_bytes = 3ull * (4096 + 4096 + 8192);
        Rig r(store, 2, 16, layer_bytes + 16384, 1, PSX_WAIT_KIND);
        const std::vector<uint32_t> ex = { 2, 5, 9 };
        check(r.node(1, PSX_DOWN, { 0 }), "Y (layer 1, expert 0, all kinds) admitted");
        check(r.node(0, PSX_GATE, ex), "gate node of layer 0 ok (up/down in flight)");
        uint64_t pend_b = 0;
        r.team.run([&](int) { psx_cache_pending_reads(PSX_ALL_KINDS, &pend_b); });
        check(pend_b == 3ull * (4096 + 8192), "layer-0 up/down reads are in flight");
        ev_before_x = r.stats().evictions;
        std::thread other([&] {   // another thread needs space for X (layer 1, expert 11, down)
            const size_t off = (size_t) 11 * r.slab[PSX_DOWN];
            const int rc = psx_cache_avvia(r.c, r.s, r.reg[1 * 3 + PSX_DOWN], off, 1, 11, PSX_DOWN);
            check(rc == 2, "X admitted (by evicting an evictable slice)");
            check(psx_cache_attendi(r.c, r.s) == 1, "X read");
            psx_cache_unpin(r.c, r.reg[1 * 3 + PSX_DOWN], off);
            arx_flush_thread();
        });
        other.join();
        ev_after_x = r.stats().evictions;
        check(r.node(0, PSX_UP, ex) && r.node(0, PSX_DOWN, ex), "up and down nodes complete");
        check(r.bytes_ok, "the in-flight slices arrived intact (not evicted, not overwritten)");
        check(r.stats().violazioni == 0, "no budget violation");
    }
    arx_debug_open(nullptr);
    std::vector<Key> m, e;
    misses_evictions(read_trace(tp), m, e);
    bool none_layer0 = true;
    for (auto & k : e) if (std::get<0>(k) == 0) none_layer0 = false;
    check(ev_after_x > ev_before_x, "X's admission did evict");
    check(!e.empty() && none_layer0, "every eviction during the layer-0 event was a layer-1 slice, never a slice in flight");
    // all-pinned case: no victim at all -> the request fails (after the store's bounded retry), nothing evicted
    {
        Rig r(store, 2, 16, 3ull * (4096 + 4096 + 8192), 1, PSX_WAIT_KIND);
        check(r.node(0, PSX_GATE, { 2, 5, 9 }), "gate node ok (budget exactly one layer)");
        std::thread other([&] {
            const int rc = psx_cache_avvia(r.c, r.s, r.reg[1 * 3 + PSX_DOWN], 0, 1, 0, PSX_DOWN);
            check(rc == 0, "no space and only pinned / in-flight slices: the request fails instead of evicting");
        });
        other.join();
        check(r.node(0, PSX_UP, { 2, 5, 9 }) && r.node(0, PSX_DOWN, { 2, 5, 9 }) && r.bytes_ok, "the in-flight reads still complete correctly");
        check(r.stats().evictions == 0, "nothing was evicted");
    }
    psx_debug_set_posix_deferred_reads(0);
}

static void t_hits_no_wait(const std::string & store) {
    printf("[6] hits introduce no wait\n");
    for (psx_wait_mode m : { PSX_WAIT_LAYER_TOUCH, PSX_WAIT_KIND }) {
        Rig r(store, 2, 16, 0, 3, m);
        const std::vector<uint32_t> ex = { 3, 8, 12 };
        r.node(0, PSX_GATE, ex); r.node(0, PSX_UP, ex); r.node(0, PSX_DOWN, ex);
        r.node(1, PSX_GATE, ex); r.node(1, PSX_UP, ex); r.node(1, PSX_DOWN, ex);
        uint32_t awaited = 0;
        for (psx_kind k : { PSX_GATE, PSX_UP, PSX_DOWN }) {
            r.node(0, k, ex);
            for (auto & rep : r.last_rep) awaited += rep.n_awaited;
        }
        check(awaited == 0, std::string(psx_wait_mode_name(m)) + ": all-hit layer waits for nothing");
        check(r.bytes_ok, std::string(psx_wait_mode_name(m)) + ": bytes correct");
    }
}

static void t_errors() {
    printf("[7] read errors are fatal at the node that waits for them (fail-closed)\n");
}

static void t_errors_impl(const std::string & store) {
    t_errors();
    // deterministic on every backend: the injected failure hits the first read that is WAITED for
    psx_debug_set_posix_deferred_reads(kDeferredBackend ? 1 : 0);
    {
        Rig r(store, 2, 16, 0, 1, PSX_WAIT_KIND);
        const std::vector<uint32_t> ex = { 1, 2 };
        check(r.node(0, PSX_GATE, ex) == 1, "kind: gate node ok");
        check(r.node(0, PSX_UP, ex) == 1, "kind: up node ok");
        psx_debug_fail_next_reads(1);
        check(r.node(0, PSX_DOWN, ex) == 0, "kind: a failed down read is reported at the down node");
        check(psx_error(r.s) != nullptr, "kind: the store reports the failure");
    }
    {
        Rig r(store, 2, 16, 0, 1, PSX_WAIT_LAYER_TOUCH);
        psx_debug_fail_next_reads(1);
        check(r.node(0, PSX_GATE, { 1, 2 }) == 0, "layer_touch: a failed read is reported at the first node");
    }
    psx_debug_fail_next_reads(0);
    psx_debug_set_posix_deferred_reads(0);
}

static void t_boundary_and_order(const std::string & store) {
    printf("[8] layer boundaries, node order, kinds unknown at the first node\n");
    if (kDeferredBackend) psx_debug_set_posix_deferred_reads(1);
    {   // up computed before gate (llama.cpp node order is not assumed)
        Rig r(store, 3, 16, 0, 2, PSX_WAIT_KIND);
        const std::vector<uint32_t> ex = { 0, 6, 15 };
        for (uint32_t l = 0; l < 3; l++) {
            check(r.node(l, PSX_UP, ex) && r.node(l, PSX_GATE, ex) && r.node(l, PSX_DOWN, ex), "order up, gate, down ok");
        }
        check(r.bytes_ok && r.pending_total() == 0, "order up, gate, down: bytes correct, nothing pending");
        check(r.cnt.orphan_pending == 0 && r.cnt.pending_at_event_end == 0, "no orphan read at any layer boundary");
        size_t pins = 0; for (auto & t : r.ets) pins += t.pins.size();
        check(pins == ex.size() * 3, "only the last layer's slices stay pinned (released at the next event, as the baseline)");
    }
    {   // down position unknown at the first node: issued at its own node, as the baseline does
        Rig r(store, 2, 16, 0, 1, PSX_WAIT_KIND);
        const std::vector<uint32_t> ex = { 4, 5 };
        check(r.node(0, PSX_GATE, ex, PSX_KIND_BIT(PSX_GATE) | PSX_KIND_BIT(PSX_UP)), "first node without down");
        check(r.node(0, PSX_UP, ex) && r.node(0, PSX_DOWN, ex), "later nodes ok");
        check(r.cnt.issued_followup == 2, "down issued at its own node (2 reads)");
        check(r.bytes_ok && r.pending_total() == 0, "bytes correct, nothing pending");
    }
    psx_debug_set_posix_deferred_reads(0);
}

static void t_instrumentation(const std::string & dir, const std::string & store) {
    printf("[9] instrumentation: coherent, and unable to change scheduling decisions\n");
    psx_debug_set_posix_deferred_reads(kDeferredBackend ? 1 : 0);
    std::vector<std::vector<uint32_t>> reps[2];
    psx_cache_stats st[2];
    for (int traced = 0; traced < 2; traced++) {
        const std::string tp = dir + "/t9.trace";
        if (traced) arx_debug_open(tp.c_str());
        {
            Rig r(store, 3, 16, 9ull * 3 * 8192, 1, PSX_WAIT_KIND);
            for (uint32_t t = 0; t < 6; t++)
                for (uint32_t l = 0; l < 3; l++) {
                    const auto ex = route(t, l, 16, 3);
                    for (psx_kind k : { PSX_GATE, PSX_UP, PSX_DOWN }) {
                        if (traced) r.team.run([&](int) { psx_cache_poll_pending(); });   // the hook's pre-barrier poll
                        r.node(l, k, ex);
                        reps[traced].push_back({ r.last_rep[0].n_awaited, r.last_rep[0].pending_after, r.last_rep[0].wait_mask });
                    }
                }
            st[traced] = r.stats();
        }
        if (traced) arx_debug_open(nullptr);
    }
    check(reps[0] == reps[1], "per-node wait decisions identical with and without the trace");
    check(st[0].misses == st[1].misses && st[0].evictions == st[1].evictions && st[0].bytes_read == st[1].bytes_read,
          "cache statistics identical with and without the trace");
    if (!kDeferredBackend) { skip("deterministic background completion before a wait"); psx_debug_set_posix_deferred_reads(0); return; }
    const std::string tp = dir + "/t9b.trace";
    arx_debug_open(tp.c_str());
    {
        Rig r(store, 2, 16, 0, 1, PSX_WAIT_KIND);
        const std::vector<uint32_t> ex = { 7, 8 };
        r.node(0, PSX_GATE, ex);
        check(r.last_rep[0].n_done_at_wait == 0, "gate reads were not complete before their wait");
        r.node(0, PSX_UP, ex);
        check(r.last_rep[0].n_done_at_wait == 0, "up reads not complete before their wait");
        // the device finishes the down reads while up computes; the hook's pre-barrier poll stamps them
        r.team.run([&](int) { psx_debug_complete_pending_reads(r.s, PSX_KIND_BIT(PSX_DOWN)); psx_cache_poll_pending(); });
        r.node(0, PSX_DOWN, ex);
        check(r.last_rep[0].n_awaited == 2 && r.last_rep[0].n_done_at_wait == 2 &&
                  r.last_rep[0].bytes_done_at_wait == 2ull * kSize[PSX_DOWN], "down reads complete before their wait are counted as hidden");
        check(r.last_rep[0].wait_start_ns <= r.last_rep[0].wait_end_ns, "wait start <= wait end");
        check(r.bytes_ok, "bytes correct");
    }
    arx_debug_open(nullptr);
    int io = 0, coherent = 0, hidden = 0;
    for (auto & rec : read_trace(tp)) {
        if (rec.t != 'I' || rec.f.size() < 11) continue;
        io++;
        const uint64_t issue = strtoull(rec.f[6].c_str(), 0, 10), done = strtoull(rec.f[7].c_str(), 0, 10);
        const uint64_t ws = strtoull(rec.f[8].c_str(), 0, 10), we = strtoull(rec.f[9].c_str(), 0, 10);
        if (issue <= done && ws <= we && done <= we) coherent++;
        if (atoi(rec.f[4].c_str()) == PSX_DOWN && done <= ws) hidden++;
    }
    check(io == 6 && coherent == io, "one coherent I record per read (issue <= completion <= wait end, wait start <= wait end)");
    check(hidden == 2, "the trace shows the two down reads completed before their wait began");
    psx_debug_set_posix_deferred_reads(0);
}

// [11] Regression test of the Phase 1a host incident (STATUS_HEAP_CORRUPTION, exit 0xC0000374): with mingw-w64
// GCC 10, the destructors of thread_local objects (the store's per-thread state, the trace buffer) ran on storage
// emutls had already freed when a thread exited (argine-tls.h). This test does what the earlier groups do many
// times -- threads that use the store and the trace and then exit -- and checks that every per-thread object is
// destroyed exactly once, on live storage, and (Windows) that the process heap is intact afterwards.
namespace {
std::atomic<int> g_probe_ctor{ 0 }, g_probe_dtor{ 0 }, g_probe_dead_dtor{ 0 };
struct ProbeTag;
struct Probe {
    static constexpr uint64_t kAlive = 0xA11CE5A11CE5ull, kDead = 0xDEADDEADDEADull;
    uint64_t           magic = kAlive;
    std::vector<int>   payload = std::vector<int>(64, 7);   // owns heap memory, like StatoThread's deque
    Probe() { g_probe_ctor++; }
    ~Probe() {
        if (magic != kAlive || payload.size() != 64 || payload[63] != 7) g_probe_dead_dtor++;
        magic = kDead;
        g_probe_dtor++;
    }
};
bool heap_intact() {
#if defined(_WIN32)
    return HeapValidate(GetProcessHeap(), 0, nullptr) != 0 && _heapchk() == _HEAPOK;
#else
    return true;   // POSIX: the same churn runs under ASan in the conformance build (MODEL_FREE_CONFORMANCE.json)
#endif
}
}  // namespace

static void t_thread_exit(const std::string & dir, const std::string & store) {
    printf("[11] thread exit: per-thread state destroyed once, on live storage (Windows heap-corruption regression)\n");
    const uint64_t c0 = g_argine_tls_created.load(), d0 = g_argine_tls_destroyed.load();
    const int      n_threads = 48;
    {
        std::vector<std::thread> th;
        for (int i = 0; i < n_threads; i++)
            th.emplace_back([] { Probe & p = argine_thread_object<Probe, ProbeTag>(); p.payload[0] = 1; });
        for (auto & t : th) t.join();
    }
    check(g_probe_ctor.load() == n_threads && g_probe_dtor.load() == n_threads, "one per-thread object per thread, each destroyed exactly once at thread exit");
    check(g_probe_dead_dtor.load() == 0, "every per-thread destructor ran on live storage");
    // the store and the trace from short-lived threads (StatoThread with a multi-node deque, ThreadBuf)
    const std::string tp = dir + "/t11.trace";
    arx_debug_open(tp.c_str());
    bool bytes_ok = true;
    std::atomic<bool> ok{ true };
    int  rounds_done = 0;
    for (int round = 0; round < 6; round++) {
        Rig r(store, 4, 16, 0, 6, (psx_wait_mode) (round % 3));
        if (!run_workload(r, 4, 6, 300 + (uint32_t) round)) ok = false;
        bytes_ok &= r.bytes_ok;
        // plus short-lived threads, each with 8 reads in flight at a time (a multi-node deque of pending reads).
        // Every thread owns a disjoint set of 24 slices, so no thread waits for another thread's read (the hook
        // likewise hands out each slice of a node to exactly one thread).
        std::vector<std::thread> th;
        for (int t = 0; t < 8; t++)
            th.emplace_back([&r, &ok, t] {
                std::vector<std::pair<psx_region *, size_t>> pins;
                for (int i = 0; i < 24; i++) {
                    const uint32_t j = (uint32_t) (t * 24 + i);
                    const uint32_t l = j % 4, x = (j / 4) % 16, k = j / 64;
                    const size_t   off = (size_t) x * r.slab[k];
                    if (psx_cache_avvia(r.c, r.s, r.reg[l * 3 + k], off, l, x, (psx_kind) k) == 0) { ok = false; continue; }
                    pins.emplace_back(r.reg[l * 3 + k], off);
                    if (i % 8 == 7 && !psx_cache_attendi(r.c, r.s)) ok = false;
                }
                for (auto & p : pins) psx_cache_unpin(r.c, p.first, p.second);
                arx_flush_thread();
            });
        for (auto & t : th) t.join();
        rounds_done++;
    }   // every Rig joins its team: 6 x 6 team threads plus 6 x 8 reader threads have exited
    arx_debug_open(nullptr);
    const uint64_t dc = g_argine_tls_created.load() - c0, dd = g_argine_tls_destroyed.load() - d0;
    check(ok && bytes_ok && rounds_done == 6, "6 rounds of thread churn through the store: workload ok, bytes correct");
    check(dc > 0 && dc == dd, "per-thread runtime state (store, trace, probes): created " + std::to_string(dc) + ", destroyed " + std::to_string(dd));
    check(psx_debug_reads_drained_at_thread_exit() == 0, "no thread exited with a read in flight");
    int io = 0;
    for (auto & rec : read_trace(tp)) io += rec.t == 'I';
    check(io > 0, "trace records written by exited threads were flushed");
    check(heap_intact(), "process heap intact after the churn (Windows: HeapValidate and _heapchk)");
}

static void t_review_cases(const std::string & dir, const std::string & store) {
    printf("[10] orphan reads, follow-up mismatch (fail-closed), synchronous completions never hidden\n");
    psx_debug_set_posix_deferred_reads(kDeferredBackend ? 1 : 0);
    {   // a covered kind that never gets its node: its reads are waited for (and counted) before the pins go
        Rig r(store, 2, 16, 0, 1, PSX_WAIT_KIND);
        const std::vector<uint32_t> ex = { 3, 6 };
        check(r.node(0, PSX_GATE, ex) && r.node(0, PSX_UP, ex), "gate and up nodes of layer 0 (no down node)");
        check(r.node(1, PSX_GATE, ex), "next event starts");
        check(r.cnt.orphan_pending == 2, "the 2 down reads left in flight are counted as orphans and waited for");
        bool down_ok = true; for (uint32_t x : ex) down_ok &= r.present(0, x, PSX_DOWN);
        check(down_ok, "the orphan reads completed into their slots before the pins were released");
        check(r.node(1, PSX_UP, ex) && r.node(1, PSX_DOWN, ex) && r.bytes_ok, "the next event is unaffected");
    }
    {   // a later node asking for different experts than the first node: fail-closed, no touch
        Rig r(store, 2, 16, 0, 1, PSX_WAIT_KIND);
        check(r.node(0, PSX_GATE, { 1, 2 }), "first node experts {1,2}");
        check(r.node(0, PSX_UP, { 1, 3 }) == 0 && r.cnt.followup_mismatch == 1, "later node with experts {1,3} is refused (fail-closed)");
    }
    {   // synchronous completions (POSIX immediate reads) are never counted as done in the background
        psx_debug_set_posix_deferred_reads(0);
        const std::string tp = dir + "/t10.trace";
        arx_debug_open(tp.c_str());
        {
            Rig r(store, 2, 16, 0, 1, PSX_WAIT_KIND);
            const std::vector<uint32_t> ex = { 9, 10 };
            r.node(0, PSX_GATE, ex); r.node(0, PSX_UP, ex);
            r.team.run([&](int) { psx_cache_poll_pending(); });
            r.node(0, PSX_DOWN, ex);
            if (!kDeferredBackend) skip("synchronous POSIX completion check (Windows completions depend on the device)");
            else check(r.last_rep[0].n_awaited == 2 && r.last_rep[0].n_done_at_wait == 0, "synchronously completed reads are not counted as done-in-background");
        }
        arx_debug_open(nullptr);
    }
    psx_debug_set_posix_deferred_reads(0);
}

int main(int argc, char ** argv) {
    if (argc < 2) { fprintf(stderr, "usage: %s <scratch-dir>\n", argv[0]); return 2; }
    setvbuf(stdout, nullptr, _IONBF, 0);   // a crash must not lose the lines already printed (host incident)
    const std::string dir = argv[1];
    const std::string store = make_store(dir, 4, 16);
    printf("R5 ETS model-free conformance (%s backend)\n", kDeferredBackend ? "POSIX deferred + immediate" : "Windows OVERLAPPED");
    t_config();
    t_wait_placement(store);
    t_same_requests(dir, store);
    t_multithread(store);
    t_pending_not_evicted(dir, store);
    t_hits_no_wait(store);
    t_errors_impl(store);
    t_boundary_and_order(store);
    t_instrumentation(dir, store);
    t_review_cases(dir, store);
    t_thread_exit(dir, store);
    printf("\nRESULT: %d passed, %d failed, %d skipped\n", g_pass, g_fail, g_skip);
    // Windows runs skip exactly the checks that need the POSIX deferred backend; any other count is a failure.
    const int expected_skips = kDeferredBackend ? 0 : 1 + 1 + 1;
    if (g_skip != expected_skips) {
        printf("UNEXPECTED SKIP COUNT: %d (expected %d)\n", g_skip, expected_skips);
        return 1;
    }
    return g_fail ? 1 : 0;
}
