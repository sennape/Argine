// Regression probe: proves RetentionPolicy::protected_for() is purely
// observational -- calling it (zero times, once, or many times, in any
// pattern) between events never changes the eviction decision sequence a
// policy instance produces for an identical input event stream.
//
// Method: run the SAME frozen PredictedProtectPolicy class (not a
// reimplementation) over the SAME event sequence twice, from fresh
// identical construction, via the standalone probe harness already used
// for policy_probe.cpp. Run A never calls protected_for(). Run B calls
// protected_for() for every layer touched, several times per event,
// including before AND after access(), and discards every return value.
// If the two runs' evicted/resident sequences ever diverge, the accessor
// is not observational and this probe fails loudly.
#include "argine-retention-policy.h"
#include <cstdio>
#include <cstdlib>
#include <vector>

using namespace argine_retention;

static AgedFrequencyParams PARAMS{0.99, 64};

struct RunResult {
    std::vector<std::vector<Obj>> evicted_per_event;
    std::vector<std::vector<uint32_t>> missed_per_event;
};

static RunResult run(bool call_accessor, uint64_t budget, uint32_t layer,
                      const std::vector<std::vector<uint32_t>> & events,
                      uint64_t bytes_of_layer, double predictor_decay,
                      uint32_t top_k, uint32_t n_experts) {
    PredictedProtectPolicy policy(PARAMS, predictor_decay, top_k, n_experts);
    policy.start(budget);
    RunResult r;
    uint32_t step = 0;
    for (const auto & experts : events) {
        if (call_accessor) {
            // Call it repeatedly, in positions a real integration never
            // would (before AND after access(), for a layer never even
            // touched, for random layer ids), and discard every result --
            // the point is to prove NONE of this perturbs the decision.
            volatile size_t sink = 0;
            sink += policy.protected_for(layer).size();
            sink += policy.protected_for(layer + 1000).size();
            sink += policy.protected_for(layer).size();
            (void) sink;
        }
        EventOutcome out = policy.access(step, layer, experts, bytes_of_layer);
        if (call_accessor) {
            volatile size_t sink = 0;
            sink += policy.protected_for(layer).size();
            sink += policy.protected_for(999).size();
            (void) sink;
        }
        r.evicted_per_event.push_back(out.evicted);
        r.missed_per_event.push_back(out.missed);
        step++;
    }
    return r;
}

static bool same(const RunResult & a, const RunResult & b) {
    if (a.evicted_per_event.size() != b.evicted_per_event.size()) return false;
    for (size_t i = 0; i < a.evicted_per_event.size(); i++) {
        if (a.evicted_per_event[i].size() != b.evicted_per_event[i].size()) return false;
        for (size_t j = 0; j < a.evicted_per_event[i].size(); j++) {
            if (a.evicted_per_event[i][j].layer != b.evicted_per_event[i][j].layer) return false;
            if (a.evicted_per_event[i][j].expert != b.evicted_per_event[i][j].expert) return false;
        }
        if (a.missed_per_event[i] != b.missed_per_event[i]) return false;
    }
    return true;
}

int main() {
    int failures = 0;

    // Scenario 1: repeated hits + predictor-driven eviction pressure
    // (the same shape that originally surfaced the self-eviction finding).
    {
        std::vector<std::vector<uint32_t>> events = {
            {0, 1, 2}, {0, 1}, {0, 1}, {3, 4}, {0, 1}, {5, 6}, {0, 1},
        };
        uint64_t budget = 6ull * 100000 + 4096;
        RunResult a = run(false, budget, 0, events, 100000, 0.99, 2, 16);
        RunResult b = run(true, budget, 0, events, 100000, 0.99, 2, 16);
        if (!same(a, b)) {
            printf("[FAIL] scenario 1: protected_for() calls changed the decision sequence\n");
            failures++;
        } else {
            printf("[ok] scenario 1: identical decision sequence with/without accessor calls (%zu events)\n",
                   a.evicted_per_event.size());
        }
    }

    // Scenario 2: tight budget forcing frequent eviction, different top_k.
    {
        std::vector<std::vector<uint32_t>> events = {
            {10, 11, 12}, {10, 11}, {13}, {14}, {10, 11}, {15}, {16}, {10, 11},
        };
        uint64_t budget = 3ull * 50000 + 4096;
        RunResult a = run(false, budget, 2, events, 50000, 0.99, 1, 32);
        RunResult b = run(true, budget, 2, events, 50000, 0.99, 1, 32);
        if (!same(a, b)) {
            printf("[FAIL] scenario 2: protected_for() calls changed the decision sequence\n");
            failures++;
        } else {
            printf("[ok] scenario 2: identical decision sequence with/without accessor calls (%zu events)\n",
                   a.evicted_per_event.size());
        }
    }

    // Scenario 3: cold-start only (no eviction pressure at all) -- proves
    // the accessor doesn't alter the admit-only path either.
    {
        std::vector<std::vector<uint32_t>> events = { {1}, {2}, {3} };
        uint64_t budget = 10ull * 100000;
        RunResult a = run(false, budget, 5, events, 100000, 0.99, 1, 8);
        RunResult b = run(true, budget, 5, events, 100000, 0.99, 1, 8);
        if (!same(a, b)) {
            printf("[FAIL] scenario 3: protected_for() calls changed the decision sequence\n");
            failures++;
        } else {
            printf("[ok] scenario 3: identical decision sequence with/without accessor calls (%zu events)\n",
                   a.evicted_per_event.size());
        }
    }

    printf(failures == 0 ? "RISULTATO OK: 0 fallimenti\n" : "RISULTATO FALLITO: %d fallimenti\n", failures);
    return failures == 0 ? 0 : 1;
}
