// Conformance-harness driver (R2-12 realization/conformance phase).
//
// Legge una traccia sintetica/registrata in formato JSON-lines molto
// semplice (una riga per evento: {"step":N,"layer":L,"experts":[...]}),
// la fa passare attraverso il modulo C++ (argine-retention-policy.{h,cpp})
// e stampa, per ogni evento, lo stato di decisione in JSON-lines, nello
// stesso formato che lo script Python di riferimento produce con le CLASSI
// FROZEN vere (non una reimplementazione) -- cosi' il confronto e' riga per
// riga, bit per bit.
//
// Uso:
//   g++ -O2 -std=c++17 argine-retention-policy.cpp \
//       argine-retention-conformance-main.cpp -o retention_conformance
//   ./retention_conformance <policy_id> <decay> <interval> \
//       [<predictor_decay> <top_k> <n_experts>] < trace.jsonl > decisions.jsonl
//
// Non apre nessun modello, nessun file reale .psxs, nessuna rete: legge solo
// stdin e scrive solo stdout. Nessuna esecuzione reale di R2-12 avviene qui.
#include "argine-retention-policy.h"

#include <algorithm>
#include <cstdio>
#include <memory>
#include <cstdlib>
#include <iostream>
#include <sstream>
#include <string>
#include <vector>

using namespace argine_retention;

namespace {

// Parser JSON-lines minimale e deliberatamente rigido: il formato di
// input/output e' nostro (non un'interfaccia pubblica), quindi niente
// libreria esterna. Accetta solo la forma esatta prodotta dal generatore
// di tracce Python gemello.
struct Event {
    uint32_t step;
    uint32_t layer;
    std::vector<uint32_t> experts;
    uint64_t bytes_of_layer;
    uint64_t budget_override = 0;  // 0 = usa il budget globale della riga di comando
};

bool parse_event_line(const std::string & line, Event & ev) {
    // Formato rigido: {"step":N,"layer":L,"experts":[a,b,c],"bytes_of_layer":B}
    auto find_num = [&](const char * key) -> long long {
        auto pos = line.find(key);
        if (pos == std::string::npos) { return -1; }
        pos = line.find(':', pos);
        if (pos == std::string::npos) { return -1; }
        return std::strtoll(line.c_str() + pos + 1, nullptr, 10);
    };
    long long step = find_num("\"step\"");
    long long layer = find_num("\"layer\"");
    long long bol = find_num("\"bytes_of_layer\"");
    if (step < 0 || layer < 0 || bol < 0) { return false; }
    ev.step = (uint32_t) step;
    ev.layer = (uint32_t) layer;
    ev.bytes_of_layer = (uint64_t) bol;
    auto lb = line.find('[');
    auto rb = line.find(']', lb);
    if (lb == std::string::npos || rb == std::string::npos) { return false; }
    std::string inner = line.substr(lb + 1, rb - lb - 1);
    std::stringstream ss(inner);
    std::string tok;
    while (std::getline(ss, tok, ',')) {
        if (tok.empty()) { continue; }
        ev.experts.push_back((uint32_t) std::strtol(tok.c_str(), nullptr, 10));
    }
    return true;
}

std::string json_u32_array(const std::vector<uint32_t> & v) {
    std::string s = "[";
    for (size_t i = 0; i < v.size(); i++) { s += std::to_string(v[i]); if (i + 1 < v.size()) s += ","; }
    s += "]";
    return s;
}

std::string json_obj_array(const std::vector<Obj> & v) {
    std::string s = "[";
    for (size_t i = 0; i < v.size(); i++) {
        s += "[" + std::to_string(v[i].layer) + "," + std::to_string(v[i].expert) + "]";
        if (i + 1 < v.size()) s += ",";
    }
    s += "]";
    return s;
}

}  // namespace

int main(int argc, char ** argv) {
    if (argc < 4) {
        std::fprintf(stderr,
            "uso: %s <policy_id> <decay> <interval> [<predictor_decay> <top_k> <n_experts>] "
            "<budget_bytes> < trace.jsonl > decisions.jsonl\n", argv[0]);
        return 2;
    }
    std::string policy_id = argv[1];
    double decay = std::atof(argv[2]);
    uint32_t interval = (uint32_t) std::atoi(argv[3]);
    double pred_decay = 0.0; uint32_t top_k = 0, n_experts = 0;
    uint64_t budget_bytes = 0;
    int next = 4;
    if (policy_id == "predicted_protect") {
        if (argc < 8) { std::fprintf(stderr, "predicted_protect richiede predictor_decay top_k n_experts budget_bytes\n"); return 2; }
        pred_decay = std::atof(argv[4]);
        top_k = (uint32_t) std::atoi(argv[5]);
        n_experts = (uint32_t) std::atoi(argv[6]);
        budget_bytes = (uint64_t) std::strtoull(argv[7], nullptr, 10);
        next = 8;
    } else {
        if (argc < 5) { std::fprintf(stderr, "b_selected richiede budget_bytes\n"); return 2; }
        budget_bytes = (uint64_t) std::strtoull(argv[4], nullptr, 10);
        next = 5;
    }
    (void) next;

    RetentionConfig cfg = make_retention_config(policy_id, decay, interval, pred_decay, top_k, n_experts);
    if (cfg.kind == PolicyKind::INVALID) {
        std::fprintf(stderr, "config REJECTED (fail-closed): %s\n", cfg.rejection_reason.c_str());
        return 3;
    }
    if (cfg.kind == PolicyKind::NONE) {
        std::fprintf(stderr, "policy_id vuoto non valido per questo harness\n");
        return 2;
    }

    std::unique_ptr<RetentionPolicy> policy;
    if (cfg.kind == PolicyKind::B_SELECTED) {
        policy.reset(new BSelectedPolicy(cfg.base));
    } else {
        policy.reset(new PredictedProtectPolicy(cfg.base, cfg.predictor_decay, cfg.top_k, cfg.n_experts));
    }
    policy->start(budget_bytes);

    std::string line;
    while (std::getline(std::cin, line)) {
        if (line.empty()) { continue; }
        Event ev;
        if (!parse_event_line(line, ev)) {
            std::fprintf(stderr, "riga di traccia non valida: %s\n", line.c_str());
            return 4;
        }
        EventOutcome outcome = policy->access(ev.step, ev.layer, ev.experts, ev.bytes_of_layer);
        RetentionPolicy::Inspection insp = policy->inspect(ev.layer);

        std::string protected_json = "[]";
        if (cfg.kind == PolicyKind::PREDICTED_PROTECT) {
            auto * pp = static_cast<PredictedProtectPolicy *>(policy.get());
            RetentionStats st = pp->stats_with_id();
            (void) st;
        }

        std::vector<Obj> resident_sorted = insp.resident;
        std::sort(resident_sorted.begin(), resident_sorted.end(), [](const Obj&a, const Obj&b){
            if (a.layer != b.layer) return a.layer < b.layer;
            return a.expert < b.expert;
        });

        std::printf(
            "{\"step\":%u,\"layer\":%u,\"missed\":%s,\"evicted\":%s,\"resident\":%s,\"resident_bytes\":%llu}\n",
            ev.step, ev.layer,
            json_u32_array(outcome.missed).c_str(),
            json_obj_array(outcome.evicted).c_str(),
            json_obj_array(resident_sorted).c_str(),
            (unsigned long long) policy->stats().resident_bytes);
    }

    RetentionStats final_stats = (cfg.kind == PolicyKind::B_SELECTED)
        ? static_cast<BSelectedPolicy*>(policy.get())->stats_with_id()
        : static_cast<PredictedProtectPolicy*>(policy.get())->stats_with_id();
    std::fprintf(stderr,
        "FINAL hits=%llu misses=%llu evictions=%llu resident_bytes=%llu policy_id=%s "
        "predictor_decisions=%llu protected_fallbacks=%llu\n",
        (unsigned long long) final_stats.hits, (unsigned long long) final_stats.misses,
        (unsigned long long) final_stats.evictions, (unsigned long long) final_stats.resident_bytes,
        final_stats.policy_id.c_str(),
        (unsigned long long) final_stats.predictor_decisions,
        (unsigned long long) final_stats.protected_fallbacks);
    return 0;
}
