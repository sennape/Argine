// R3-03A: separazione fra ENSURE FISICO e EVENTO SEMANTICO di politica.
//
// Test deterministico, nessun modello, nessun llama-server: store sintetico
// (tests/make_tiny_model.py) + codice di PRODUZIONE reale (argine-store.cpp,
// argine-retention-policy.cpp). Riproduce il protocollo per-nodo di
// argine-experts.cpp (compute_forward) senza ggml:
//   primo nodo dell'evento  -> psx_cache_ensure_many_event(tutti i kind) + pin d'evento
//   nodi gate/up/down successivi -> nessuna politica: i pin d'evento restano,
//                                   psx_cache_note_followup_node()
//   primo nodo dell'evento successivo -> rilascia i pin dell'evento precedente.
//
//   g++ -std=c++17 -I. argine-store.cpp argine-retention-policy.cpp
//       argine-semantic-event-test.cpp -o semtest -lpthread   (una sola riga)
//   ./semtest selftest <store.psxs>
//   ./semtest drive <store.psxs> <stream.txt> <budget_experts> [legacy]
// (la politica si configura con le ARGINE_RETENTION_* come nel runtime)
#include "argine-store.h"

#include <algorithm>
#include <cstdio>
#include <cstdlib>
#include <cstring>
#include <fstream>
#include <map>
#include <sstream>
#include <string>
#include <vector>

static int fallimenti = 0;
static void check(bool ok, const char * cosa) {
    printf("  [%s] %s\n", ok ? "ok" : "NO", cosa);
    if (!ok) fallimenti++;
}

static psx_store * STORE = nullptr;
static std::map<uint64_t, struct psx_region *> REGIONS;
static struct psx_region * region_for(uint32_t layer, uint32_t expert, enum psx_kind kind) {
    const uint64_t key = ((uint64_t) layer << 40) | ((uint64_t) expert << 8) | (uint64_t) kind;
    auto it = REGIONS.find(key);
    if (it != REGIONS.end()) return it->second;
    const size_t need = psx_slice_read_bytes(STORE, layer, expert, kind);
    struct psx_region * r = psx_region_new(need, need, 1);
    REGIONS[key] = r;
    return r;
}

struct Req {
    std::vector<struct psx_region *> r;
    std::vector<size_t> o;
    std::vector<uint32_t> l, e;
    std::vector<enum psx_kind> k;
};
// Come argine-experts.cpp: l'ordine e' PER KIND (k / n_serve = kind), poi expert.
static Req build_req(uint32_t layer, const std::vector<uint32_t> & experts,
                     const std::vector<enum psx_kind> & kinds) {
    Req q;
    for (enum psx_kind kd : kinds) {
        for (uint32_t x : experts) {
            q.r.push_back(region_for(layer, x, kd));
            q.o.push_back(0);
            q.l.push_back(layer);
            q.e.push_back(x);
            q.k.push_back(kd);
        }
    }
    return q;
}
static void unpin_all(struct psx_cache * c, Req & q) {
    for (size_t i = 0; i < q.r.size(); i++) psx_cache_unpin(c, q.r[i], q.o[i]);
    q = Req();
}
// confronta i byte residenti con la lettura di riferimento dallo store
static bool bytes_ok(const Req & q) {
    for (size_t i = 0; i < q.r.size(); i++) {
        const size_t need = psx_slice_read_bytes(STORE, q.l[i], q.e[i], q.k[i]);
        void * ref = psx_alloc_aligned(STORE, need);
        const size_t got = psx_read_slice(STORE, q.l[i], q.e[i], q.k[i], ref);
        const bool same = got > 0 && memcmp(ref, (const char *) psx_region_base(q.r[i]) + q.o[i], got) == 0;
        psx_free_aligned(ref, need);
        if (!same) return false;
    }
    return true;
}

static const std::vector<enum psx_kind> ALL3 = { PSX_GATE, PSX_UP, PSX_DOWN };

static void selftest() {
    printf("A. tracker strutturale (psx_semantic_event_tracker)\n");
    {
        psx_semantic_event_tracker t;
        int first = 0; std::vector<uint64_t> passes;
        for (int pass = 0; pass < 2; pass++)
            for (uint32_t layer = 0; layer < 3; layer++)
                for (uint32_t kind = 0; kind < 3; kind++) {
                    auto r = t.classify(layer, kind);
                    first += r.first_node ? 1 : 0;
                    passes.push_back(r.pass);
                    if (kind == 0) check(r.first_node, "gate apre l'evento");
                    else check(!r.first_node, "up/down sono nodi successivi dello stesso evento");
                }
        check(first == 6, "6 eventi per 2 pass x 3 layer (non 18 nodi)");
        check(passes.front() == 0 && passes[8] == 0 && passes[9] == 1 && passes.back() == 1,
              "pass 0 poi 1: avanza quando il layer non sale");
    }
    {   // modello con UN solo layer MoE: il layer non scende mai, ma il kind si ripete
        psx_semantic_event_tracker t;
        int first = 0; uint64_t last_pass = 0;
        for (int i = 0; i < 4; i++)
            for (uint32_t kind = 0; kind < 3; kind++) {
                auto r = t.classify(0, kind);
                first += r.first_node ? 1 : 0;
                last_pass = r.pass;
            }
        check(first == 4 && last_pass == 3, "un solo layer: 4 forward pass = 4 eventi, pass 0..3");
    }
    {   // kind mancante (tensore sul percorso normale): up non arriva mai
        psx_semantic_event_tracker t;
        int first = 0;
        for (int i = 0; i < 3; i++) {
            first += t.classify(5, 0).first_node ? 1 : 0;
            first += t.classify(5, 2).first_node ? 1 : 0;
            first += t.classify(6, 0).first_node ? 1 : 0;
        }
        check(first == 6, "layer con soli gate+down: 1 evento per layer per pass");
    }

    setenv("ARGINE_RETENTION_POLICY", "predicted_protect", 1);
    setenv("ARGINE_RETENTION_DECAY", "0.99", 1);
    setenv("ARGINE_RETENTION_INTERVAL", "64", 1);
    setenv("ARGINE_RETENTION_PREDICTOR_DECAY", "0.99", 1);
    setenv("ARGINE_RETENTION_TOPK", "2", 1);
    setenv("ARGINE_RETENTION_NEXPERTS", "8", 1);
    const uint64_t per_expert = psx_slice_read_bytes(STORE, 0, 0, PSX_GATE) + psx_slice_read_bytes(STORE, 0, 0, PSX_UP) +
                                psx_slice_read_bytes(STORE, 0, 0, PSX_DOWN);

    printf("B. un solo evento di politica per (pass, layer); nodi successivi solo fisici\n");
    {
        struct psx_cache * c = psx_cache_new_n(per_expert * 50, 1);
        const char * err = nullptr;
        check(psx_cache_attach_retention_policy(c, &err) == 1, "attach");
        const std::vector<uint32_t> ex = { 1, 3 };
        Req ev = build_req(0, ex, ALL3);
        check(psx_cache_ensure_many_event(c, STORE, ev.r.size(), ev.r.data(), ev.o.data(), ev.l.data(),
                                          ev.e.data(), ev.k.data(), 0, PSX_GATE) == 1, "primo nodo: evento (0,0) servito");
        psx_cache_stats s1; psx_cache_get_stats(c, &s1);
        psx_retention_stats r1; psx_cache_get_retention_stats(c, &r1);
        check(r1.events_served == 1 && r1.semantic_events_served == 1,
              "1 evento semantico servito dalla politica");
        for (int n = 0; n < 2; n++) {
            psx_cache_note_followup_node(c);
            psx_cache_stats s2; psx_cache_get_stats(c, &s2);
            check(s2.misses == s1.misses && s2.bytes_read == s1.bytes_read && s2.evictions == s1.evictions,
                  "nodo successivo: nessun miss, nessuna lettura, nessuno sfratto");
            check(bytes_ok(ev), "nodo successivo: gate/up/down degli expert richiesti residenti e byte-identici");
        }
        psx_retention_stats r2; psx_cache_get_retention_stats(c, &r2);
        check(r2.events_served == 1 && r2.followup_nodes_skipped == 2,
              "dopo 3 nodi: ancora 1 evento di politica, 2 nodi successivi contati");
        check(psx_cache_ensure_many_event(c, STORE, ev.r.size(), ev.r.data(), ev.o.data(), ev.l.data(),
                                          ev.e.data(), ev.k.data(), 0, PSX_UP) == 0,
              "evento duplicato (pass 0, layer 0): RIFIUTATO (fail-closed)");
        const char * m = psx_error(STORE);
        check(m && strstr(m, "gia' servito"), "il motivo e' esplicito: evento semantico gia' servito");
        psx_retention_stats r3; psx_cache_get_retention_stats(c, &r3);
        check(r3.duplicate_semantic_events_refused == 1 && r3.events_served == 1 && r3.misses == r2.misses && r3.hits == r2.hits,
              "duplicato conteggiato, stato della politica NON toccato");
        unpin_all(c, ev);
        psx_cache_free(c);
    }

    printf("C. percorso legacy (senza identita') invariato: ogni chiamata e' un evento\n");
    {
        struct psx_cache * c = psx_cache_new_n(per_expert * 50, 1);
        const char * err = nullptr;
        psx_cache_attach_retention_policy(c, &err);
        for (int i = 0; i < 3; i++) {
            Req q = build_req(0, { 2 }, ALL3);
            check(psx_cache_ensure_many(c, STORE, q.r.size(), q.r.data(), q.o.data(), q.l.data(), q.e.data(), q.k.data()) == 1,
                  "ensure_many legacy servito");
            unpin_all(c, q);
        }
        psx_retention_stats r; psx_cache_get_retention_stats(c, &r);
        check(r.events_served == 3 && r.semantic_events_served == 0, "legacy: 3 chiamate = 3 eventi, nessuna identita'");
        psx_cache_free(c);
    }

    printf("D. primo nodo parziale (kind non ancora registrato): il nodo successivo e' servito fisicamente, senza politica\n");
    {
        struct psx_cache * c = psx_cache_new_n(per_expert * 50, 1);
        const char * err = nullptr;
        psx_cache_attach_retention_policy(c, &err);
        const std::vector<uint32_t> ex = { 0, 4, 5 };
        Req first = build_req(1, ex, { PSX_GATE });
        check(psx_cache_ensure_many_event(c, STORE, first.r.size(), first.r.data(), first.o.data(), first.l.data(),
                                          first.e.data(), first.k.data(), 7, PSX_GATE) == 1, "primo nodo (solo gate)");
        Req nxt = build_req(1, ex, { PSX_GATE, PSX_UP });
        Req solo_up = build_req(1, ex, { PSX_UP });
        psx_retention_stats a; psx_cache_get_retention_stats(c, &a);
        check(psx_cache_ensure_event_followup(c, STORE, solo_up.r.size(), solo_up.r.data(), solo_up.o.data(),
                                              solo_up.l.data(), solo_up.e.data(), solo_up.k.data()) == 1,
              "slice 'up' non coperte servite fisicamente");
        psx_cache_note_followup_node(c);
        psx_retention_stats b; psx_cache_get_retention_stats(c, &b);
        check(b.events_served == a.events_served && b.hits == a.hits && b.misses == a.misses,
              "nessun evento di politica aggiuntivo");
        check(b.policy_hit_sans_index_healed == a.policy_hit_sans_index_healed + ex.size(),
              "le 3 slice 'up' contate come guarigione fisica");
        check(bytes_ok(nxt), "gate+up residenti e byte-identici");
        psx_cache_stats s; psx_cache_get_stats(c, &s);
        check(s.evictions == 0 && s.violazioni == 0, "nessuno sfratto, nessuna violazione di budget");
        unpin_all(c, first); unpin_all(c, solo_up);
        psx_cache_free(c);
    }
}

// drive: protocollo per-nodo sul flusso "pass layer e1 e2 ..." (uno per riga).
static int drive(const char * stream_path, uint64_t budget_experts, bool legacy) {
    const uint64_t per_expert = psx_slice_read_bytes(STORE, 0, 0, PSX_GATE) + psx_slice_read_bytes(STORE, 0, 0, PSX_UP) +
                                psx_slice_read_bytes(STORE, 0, 0, PSX_DOWN);
    struct psx_cache * c = psx_cache_new_n(per_expert * budget_experts + 4096, 1);
    const char * err = nullptr;
    if (!psx_cache_attach_retention_policy(c, &err)) { fprintf(stderr, "attach fallito: %s\n", err ? err : ""); return 2; }
    std::ifstream in(stream_path);
    std::string line;
    psx_semantic_event_tracker tracker;
    Req pins;
    uint64_t events = 0, nodes = 0, bad_followups = 0;
    while (std::getline(in, line)) {
        if (line.empty()) continue;
        std::istringstream ss(line);
        uint32_t pass, layer; ss >> pass >> layer;
        std::vector<uint32_t> ex; uint32_t x;
        while (ss >> x) ex.push_back(x);
        std::sort(ex.begin(), ex.end()); ex.erase(std::unique(ex.begin(), ex.end()), ex.end());
        for (enum psx_kind nk : ALL3) {
            nodes++;
            if (legacy) {
                unpin_all(c, pins);
                Req q = build_req(layer, ex, ALL3);
                if (!psx_cache_ensure_many(c, STORE, q.r.size(), q.r.data(), q.o.data(), q.l.data(), q.e.data(), q.k.data())) {
                    fprintf(stderr, "legacy ensure fallito: %s\n", psx_error(STORE)); return 3;
                }
                pins = q; events++;
                continue;
            }
            const auto r = tracker.classify(layer, (uint32_t) nk);
            if (r.first_node) {
                unpin_all(c, pins);
                Req q = build_req(layer, ex, ALL3);
                if (!psx_cache_ensure_many_event(c, STORE, q.r.size(), q.r.data(), q.o.data(), q.l.data(), q.e.data(),
                                                 q.k.data(), r.pass, (uint32_t) nk)) {
                    fprintf(stderr, "ensure_event fallito: %s\n", psx_error(STORE)); return 3;
                }
                pins = q; events++;
            } else {
                psx_cache_stats a; psx_cache_get_stats(c, &a);
                psx_cache_note_followup_node(c);
                psx_cache_stats b; psx_cache_get_stats(c, &b);
                if (b.misses != a.misses || b.bytes_read != a.bytes_read || !bytes_ok(pins)) bad_followups++;
            }
        }
    }
    psx_cache_stats st; psx_cache_get_stats(c, &st);
    psx_retention_stats rs; psx_cache_get_retention_stats(c, &rs);
    printf("{\"nodes\":%llu,\"events\":%llu,\"events_served\":%u,\"semantic_events_served\":%llu,"
           "\"followup_nodes_skipped\":%llu,\"predictor_decisions\":%llu,\"policy_hits\":%llu,\"policy_misses\":%llu,"
           "\"policy_evictions\":%llu,\"cache_hits\":%llu,\"cache_misses\":%llu,\"cache_evictions\":%llu,"
           "\"violations\":%llu,\"bad_followups\":%llu,\"healed\":%llu,\"duplicate_refused\":%llu,\"physical_margin_evictions\":%llu,\"timing_enabled\":%d,\"t_calls\":%llu,\"t_total\":%llu,\"t_pred\":%llu,\"t_victim\":%llu,\"t_victim_calls\":%llu,\"t_lock_wait\":%llu,\"t_locked\":%llu,\"t_hist_sum\":%llu}\n",
           (unsigned long long) nodes, (unsigned long long) events, rs.events_served,
           (unsigned long long) rs.semantic_events_served, (unsigned long long) rs.followup_nodes_skipped,
           (unsigned long long) rs.predictor_decisions, (unsigned long long) rs.hits, (unsigned long long) rs.misses,
           (unsigned long long) rs.evictions, (unsigned long long) st.hits, (unsigned long long) st.misses,
           (unsigned long long) st.evictions, (unsigned long long) st.violazioni, (unsigned long long) bad_followups,
           (unsigned long long) rs.policy_hit_sans_index_healed, (unsigned long long) rs.duplicate_semantic_events_refused,
           (unsigned long long) rs.policy_same_event_physical_margin_evictions,
           rs.policy_timing_enabled, (unsigned long long) rs.policy_timing_calls, (unsigned long long) rs.ns_policy_total,
           (unsigned long long) rs.ns_policy_prediction_update, (unsigned long long) rs.ns_policy_victim_selection,
           (unsigned long long) rs.policy_victim_calls, (unsigned long long) rs.ns_event_lock_wait,
           (unsigned long long) rs.ns_event_locked_total, (unsigned long long) [&]{ unsigned long long z = 0; for (int i = 0; i < 40; i++) z += rs.policy_timing_hist_log2[i]; return z; }());
    unpin_all(c, pins);
    psx_cache_free(c);
    return 0;
}

int main(int argc, char ** argv) {
    if (argc < 3) { fprintf(stderr, "uso: %s selftest <store> | drive <store> <stream> <budget_experts> [legacy]\n", argv[0]); return 2; }
    STORE = psx_open(argv[2], 0);
    if (psx_error(STORE)) { fprintf(stderr, "errore apertura: %s\n", psx_error(STORE)); return 1; }
    if (!strcmp(argv[1], "selftest")) {
        selftest();
        printf(fallimenti == 0 ? "RISULTATO OK: 0 fallimenti\n" : "RISULTATO FALLITO: %d fallimenti\n", fallimenti);
        return fallimenti == 0 ? 0 : 1;
    }
    if (!strcmp(argv[1], "drive") && argc >= 5) {
        return drive(argv[3], strtoull(argv[4], nullptr, 10), argc >= 6 && !strcmp(argv[5], "legacy"));
    }
    return 2;
}
