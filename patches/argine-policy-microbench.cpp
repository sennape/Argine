// R3-03B authoritative offline microbenchmark of the REAL retention-policy code (argine-retention-policy.cpp).
// Implements experiments/R3-03B-direct-cost-attribution/PROTOCOL.json (authoritative_microbenchmark). No model,
// no store, no file I/O inside any timed region.
//
// usage: argine-policy-microbench <stream.txt> <out_dir> <process_index> <reps> <budget_bytes> <decay> <interval>
//                                 <predictor_decay> <top_k> <n_experts>
// out_dir must already exist and be empty of this process' files (the caller guarantees a fresh directory).
// Files written to out_dir (all prefixed p<process_index>_):
//   calibration_pairs.u32  1,000,000 back-to-back clock-read deltas (ns)
//   calibration_ticks.u32  1,000,000 minimum-nonzero-tick probes (ns)
//   replays.jsonl          one JSON line per replay (warmup replays flagged "warmup":1)
//   calls_<policy>_<mode>.u32  per-call durations (ns, uint32) for modes outer and inner, repetition-major
//   meta.json              identity, flags, validity verdict
// Modes: null (vector copy only), bulk (one timer pair, policy timing OFF), outer (timer per access, timing OFF),
//        inner (timer per access, internal PolicyTiming ON).
#include "argine-retention-policy.h"

#include <algorithm>
#include <chrono>
#include <cinttypes>
#include <cstdint>
#include <cstdio>
#include <cstdlib>
#include <cstring>
#include <fstream>
#include <memory>
#include <sstream>
#include <string>
#include <vector>

using namespace argine_retention;

struct Ev { uint32_t step, pass, layer; uint64_t bytes; uint32_t n_evicted; std::vector<uint32_t> ex; };

static inline uint64_t now_ns() {
    return (uint64_t) std::chrono::duration_cast<std::chrono::nanoseconds>(
               std::chrono::steady_clock::now().time_since_epoch()).count();
}

static std::vector<Ev> load(const char * path) {
    std::ifstream f(path);
    if (!f) { fprintf(stderr, "cannot open %s\n", path); exit(2); }
    std::vector<Ev> v; std::string line;
    while (std::getline(f, line)) {
        if (!line.empty() && line.back() == '\r') line.pop_back();
        if (line.empty() || line[0] == '#') continue;
        std::istringstream is(line); Ev e; std::string ex;
        is >> e.step >> e.pass >> e.layer >> e.bytes >> e.n_evicted >> ex;
        std::stringstream ss(ex); std::string t;
        while (std::getline(ss, t, ',')) if (!t.empty()) e.ex.push_back((uint32_t) std::stoul(t));
        v.push_back(std::move(e));
    }
    return v;
}

static void fail(const char * why) { fprintf(stderr, "MICROBENCH_INVALID: %s\n", why); exit(3); }

static std::unique_ptr<RetentionPolicy> make(int which, uint64_t budget, double decay, uint32_t interval,
                                             double pdecay, uint32_t topk, uint32_t nexp) {
    AgedFrequencyParams base{decay, interval};
    std::unique_ptr<RetentionPolicy> p;
    if (which == 0) p = std::make_unique<BSelectedPolicy>(base);
    else p = std::make_unique<PredictedProtectPolicy>(base, pdecay, topk, nexp, which == 1 /*sham*/);
    p->start(budget);
    return p;
}

enum Mode { NUL = 0, BULK = 1, OUTER = 2, INNER = 3 };
static const char * kMode[4] = {"null", "bulk", "outer", "inner"};
static const char * kPol[3] = {"b_selected", "predicted_protect_sham", "predicted_protect"};

struct Ctx {
    const std::vector<Ev> * ev; uint64_t budget; double decay; uint32_t interval; double pdecay; uint32_t topk, nexp;
    FILE * jl; int proc; uint64_t logged_ev; std::vector<uint32_t> calls;
    uint64_t ref_hits[3] = {0, 0, 0}, ref_miss[3] = {0, 0, 0}, ref_evict[3] = {0, 0, 0}; bool have_ref[3] = {false, false, false};
    std::string dir;
    FILE * cf[3][4] = {};
};

// one replay; returns total ns of the timed region (bulk/null) or sum of per-call ns (outer/inner)
static void replay(Ctx & c, int pol, Mode mode, int rep, int order_idx, bool warmup) {
    const std::vector<Ev> & ev = *c.ev;
    std::vector<uint32_t> ex;
    uint64_t total = 0, hits = 0, misses = 0, evictions = 0, nst = 0, npred = 0, nvic = 0, vcalls = 0;
    if (mode == NUL) {
        const uint64_t t0 = now_ns();
        for (const Ev & e : ev) ex = e.ex;
        total = now_ns() - t0;
        if (!ex.empty() && ex[0] == 0xFFFFFFFFu) fprintf(stderr, " ");  // keep the copy loop alive
    } else {
        auto p = make(pol, c.budget, c.decay, c.interval, c.pdecay, c.topk, c.nexp);
        p->enable_timing(mode == INNER);
        if (mode == BULK) {
            const uint64_t t0 = now_ns();
            for (const Ev & e : ev) { ex = e.ex; p->access(e.step, e.layer, ex, e.bytes); }
            total = now_ns() - t0;
        } else {
            for (size_t i = 0; i < ev.size(); i++) {
                const Ev & e = ev[i]; ex = e.ex;
                const uint64_t t0 = now_ns();
                p->access(e.step, e.layer, ex, e.bytes);
                const uint64_t d = now_ns() - t0;
                if (d > 0xFFFFFFFFull) fail("per-call duration exceeds uint32");
                c.calls[i] = (uint32_t) d; total += d;
            }
            if (!warmup) fwrite(c.calls.data(), sizeof(uint32_t), c.calls.size(), c.cf[pol][mode]);
        }
        const RetentionStats st = p->stats(); hits = st.hits; misses = st.misses; evictions = st.evictions;
        if (mode == INNER) { const PolicyTiming & t = p->timing(); nst = t.ns_total; npred = t.ns_prediction_update;
                             nvic = t.ns_victim_selection; vcalls = t.victim_calls; }
        if ((pol == 0 || pol == 1) && evictions != c.logged_ev) fail("replay evictions differ from the frozen logged count");
        if (!c.have_ref[pol]) { c.have_ref[pol] = true; c.ref_hits[pol] = hits; c.ref_miss[pol] = misses; c.ref_evict[pol] = evictions; }
        else if (hits != c.ref_hits[pol] || misses != c.ref_miss[pol] || evictions != c.ref_evict[pol])
            fail("hits/misses/evictions not reproducible across replays");
    }
    fprintf(c.jl, "{\"proc\":%d,\"rep\":%d,\"warmup\":%d,\"policy\":\"%s\",\"mode\":\"%s\",\"order_idx\":%d,\"events\":%zu,"
            "\"total_ns\":%" PRIu64 ",\"hits\":%" PRIu64 ",\"misses\":%" PRIu64 ",\"evictions\":%" PRIu64
            ",\"ns_policy_total\":%" PRIu64 ",\"ns_prediction_update\":%" PRIu64 ",\"ns_victim_selection\":%" PRIu64
            ",\"victim_calls\":%" PRIu64 "}\n", c.proc, rep, warmup ? 1 : 0, mode == NUL ? "-" : kPol[pol], kMode[mode],
            order_idx, ev.size(), total, hits, misses, evictions, nst, npred, nvic, vcalls);
}

int main(int argc, char ** argv) {
    if (argc < 11) { fprintf(stderr, "usage: %s stream out_dir proc reps budget decay interval pdecay topk nexp\n", argv[0]); return 2; }
    const std::vector<Ev> ev = load(argv[1]);
    Ctx c; c.ev = &ev; c.dir = argv[2]; c.proc = atoi(argv[3]); const int reps = atoi(argv[4]);
    c.budget = strtoull(argv[5], nullptr, 10); c.decay = atof(argv[6]); c.interval = (uint32_t) atoi(argv[7]);
    c.pdecay = atof(argv[8]); c.topk = (uint32_t) atoi(argv[9]); c.nexp = (uint32_t) atoi(argv[10]);
    c.logged_ev = 0; for (const Ev & e : ev) c.logged_ev += e.n_evicted;
    c.calls.assign(ev.size(), 0);
    auto path = [&](const std::string & n) { return c.dir + "/p" + std::to_string(c.proc) + "_" + n; };
    c.jl = fopen(path("replays.jsonl").c_str(), "wb"); if (!c.jl) fail("cannot create replays.jsonl");

    // timer calibration (raw retained)
    { std::vector<uint32_t> a(1000000), b(1000000);
      for (int i = 0; i < 1000000; i++) { const uint64_t x = now_ns(); const uint64_t y = now_ns(); a[i] = (uint32_t) (y - x); }
      for (int i = 0; i < 1000000; i++) { const uint64_t x = now_ns(); uint64_t y = x; while (y == x) y = now_ns(); b[i] = (uint32_t) (y - x); }
      FILE * f = fopen(path("calibration_pairs.u32").c_str(), "wb"); if (!f) fail("calibration file"); fwrite(a.data(), 4, a.size(), f); fclose(f);
      f = fopen(path("calibration_ticks.u32").c_str(), "wb"); if (!f) fail("calibration file"); fwrite(b.data(), 4, b.size(), f); fclose(f); }

    for (int p = 0; p < 3; p++) for (int m = OUTER; m <= INNER; m++) {
        c.cf[p][m] = fopen(path(std::string("calls_") + kPol[p] + "_" + kMode[m] + ".u32").c_str(), "wb");
        if (!c.cf[p][m]) fail("cannot create per-call file");
    }
    // warmup: 2 unrecorded replays for every (policy, mode) combination, fixed order; null once-per-policy-independent
    for (int w = 0; w < 2; w++) {
        replay(c, 0, NUL, -1, -1, true);
        for (int p = 0; p < 3; p++) for (int m = BULK; m <= INNER; m++) replay(c, p, (Mode) m, -1, -1, true);
    }
    // recorded repetitions: rotating policy order (reversed on odd reps); null once per repetition; mode order alternates
    for (int r = 0; r < reps; r++) {
        replay(c, 0, NUL, r, 0, false);
        int order[3] = {r % 3, (r + 1) % 3, (r + 2) % 3};
        if (r & 1) std::swap(order[0], order[2]);
        for (int oi = 0; oi < 3; oi++) {
            const int p = order[oi];
            const int mo[3] = {BULK, OUTER, INNER};
            for (int k = 0; k < 3; k++) replay(c, p, (Mode) mo[(r & 1) ? 2 - k : k], r, oi, false);
        }
    }
    fclose(c.jl);
    for (int p = 0; p < 3; p++) for (int m = OUTER; m <= INNER; m++) fclose(c.cf[p][m]);
    FILE * mf = fopen(path("meta.json").c_str(), "wb"); if (!mf) fail("meta.json");
#if defined(_MSC_VER)
    const long cc = (long) _MSC_FULL_VER; const char * cname = "msvc";
#else
    const long cc = 0; const char * cname = __VERSION__;
#endif
    fprintf(mf, "{\"schema\":\"r3-03b-microbench-meta-v1\",\"verdict\":\"VALID\",\"proc\":%d,\"reps\":%d,\"warmup_replays_per_combo\":2,"
            "\"events\":%zu,\"logged_evictions\":%" PRIu64 ",\"budget_bytes\":%" PRIu64 ",\"decay\":%.6g,\"interval\":%u,"
            "\"predictor_decay\":%.6g,\"top_k\":%u,\"n_experts\":%u,\"compiler\":\"%s\",\"msc_full_ver\":%ld,"
            "\"reference_counts\":{\"b_selected\":[%" PRIu64 ",%" PRIu64 ",%" PRIu64 "],\"predicted_protect_sham\":[%" PRIu64 ",%" PRIu64 ",%" PRIu64
            "],\"predicted_protect\":[%" PRIu64 ",%" PRIu64 ",%" PRIu64 "]}}\n",
            c.proc, reps, ev.size(), c.logged_ev, c.budget, c.decay, c.interval, c.pdecay, c.topk, c.nexp, cname, cc,
            c.ref_hits[0], c.ref_miss[0], c.ref_evict[0], c.ref_hits[1], c.ref_miss[1], c.ref_evict[1], c.ref_hits[2], c.ref_miss[2], c.ref_evict[2]);
    fclose(mf);
    printf("MICROBENCH_VALID proc=%d reps=%d\n", c.proc, reps);
    return 0;
}
