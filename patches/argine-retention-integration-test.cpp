// R2-12: integration test of the retention-policy wiring against the REAL
// compiled argine-store.cpp + argine-retention-policy.cpp, driven against a
// REAL .psxs store file. No llama.cpp/ggml involved.
//
// Two real findings from this round are deliberately exercised here:
//
// 1. Regions must be allocated ONCE per (layer, expert, kind) and reused
//    across every event, exactly like the real runtime (argine-experts.cpp
//    keeps one stable psx_region per tensor slot for the whole process via
//    c.posizioni). A fresh-region-per-call test fixture makes every repeated
//    access look like a brand-new CacheKey even on a policy hit -- a
//    test-fixture bug, not a property of the real system.
//
// 2. The frozen Python reference (AgedFrequency.access) does NOT exclude
//    members of the CURRENT event from victim selection in later iterations
//    of that same event -- verified by replaying an identical event
//    step-by-step through both the real compiled C++ module and the actual
//    Python reference (see RETENTION-POLICY-TRACE-INSTRUMENTATION.md). This
//    is harmless for the Python simulation but unsafe for the real runtime,
//    which needs every member of one event resident simultaneously.
//    servi_evento_con_politica now detects this and fails closed with a
//    specific diagnostic instead of silently admitting the evicted member
//    anyway (which would desync the policy's logical state from physical
//    reality). Section 6 below exercises this path directly, with a budget
//    deliberately too tight for one event's own working set.
#include "argine-store.h"
#include <cstdio>
#include <cstdlib>
#include <cstring>
#include <vector>
#include <string>
#include <map>

static int fallimenti = 0;
static void check(bool ok, const char * cosa) {
    printf("  [%s] %s\n", ok ? "ok" : "NO", cosa);
    if (!ok) fallimenti++;
}

static psx_store * STORE = nullptr;
static std::map<uint64_t, struct psx_region *> REGION_CACHE;
static uint64_t region_key(uint32_t layer, uint32_t expert, int kind) {
    return (((uint64_t) layer << 40) | ((uint64_t) expert << 8) | (uint64_t) kind);
}
static struct psx_region * region_for(uint32_t layer, uint32_t expert, enum psx_kind kind) {
    const uint64_t key = region_key(layer, expert, (int) kind);
    auto it = REGION_CACHE.find(key);
    if (it != REGION_CACHE.end()) {
        return it->second;
    }
    size_t need = psx_slice_read_bytes(STORE, layer, expert, kind);
    struct psx_region * r = psx_region_new(need, need, 1);
    REGION_CACHE[key] = r;
    return r;
}

static int evento(struct psx_cache * c, uint32_t layer, const std::vector<uint32_t> & experts) {
    std::vector<struct psx_region*> regions;
    std::vector<size_t> offsets;
    std::vector<uint32_t> layers, exps;
    std::vector<enum psx_kind> kinds;
    for (uint32_t e : experts) {
        const struct psx_entry * ent = psx_get(STORE, layer, e);
        if (!ent) { fprintf(stderr, "psx_get null for layer=%u expert=%u\n", layer, e); return 0; }
        for (int k = 0; k < (int) PSX_N_KINDS; k++) {
            struct psx_region * r = region_for(layer, e, (enum psx_kind) k);
            regions.push_back(r);
            offsets.push_back(0);
            layers.push_back(layer);
            exps.push_back(e);
            kinds.push_back((enum psx_kind) k);
        }
    }
    int ok = psx_cache_ensure_many(c, STORE, regions.size(), regions.data(), offsets.data(),
                                    layers.data(), exps.data(), kinds.data());
    if (ok) {
        for (size_t i = 0; i < regions.size(); i++) {
            psx_cache_unpin(c, regions[i], offsets[i]);
        }
    }
    return ok;
}

int main(int argc, char ** argv) {
    if (argc < 2) { fprintf(stderr, "uso: %s <store.psxs>\n", argv[0]); return 2; }
    STORE = psx_open(argv[1], 0);
    if (psx_error(STORE)) { fprintf(stderr, "errore apertura: %s\n", psx_error(STORE)); return 1; }

    const uint64_t per_expert_bytes = psx_slice_read_bytes(STORE, 0, 0, PSX_GATE)
                                     + psx_slice_read_bytes(STORE, 0, 0, PSX_UP)
                                     + psx_slice_read_bytes(STORE, 0, 0, PSX_DOWN);
    printf("per_expert_bytes=%llu\n", (unsigned long long) per_expert_bytes);

    printf("1. nessuna politica -> comportamento invariato\n");
    unsetenv("ARGINE_RETENTION_POLICY");
    {
        struct psx_cache * c = psx_cache_new_n(per_expert_bytes * 3, 1);
        const char * err = nullptr;
        int ok = psx_cache_attach_retention_policy(c, &err);
        check(ok == 1, "attach con env assente ritorna successo (no-op)");
        check(psx_cache_has_retention_policy(c) == 0, "nessuna politica collegata");
        check(evento(c, 0, {0, 1}) == 1, "evento normale servito senza politica");
        psx_cache_free(c);
    }

    printf("2. politica invalida -> fail-closed\n");
    setenv("ARGINE_RETENTION_POLICY", "non_esiste", 1);
    {
        struct psx_cache * c = psx_cache_new_n(per_expert_bytes * 3, 1);
        const char * err = nullptr;
        int ok = psx_cache_attach_retention_policy(c, &err);
        check(ok == 0, "identificatore sconosciuto rifiutato");
        check(err != nullptr && err[0] != '\0', "messaggio di errore non vuoto");
        check(psx_cache_has_retention_policy(c) == 0, "nessuna politica rimane collegata dopo il rifiuto");
        psx_cache_free(c);
    }

    printf("3. settori != 1 con politica richiesta -> fail-closed\n");
    setenv("ARGINE_RETENTION_POLICY", "b_selected", 1);
    setenv("ARGINE_RETENTION_DECAY", "0.999", 1);
    setenv("ARGINE_RETENTION_INTERVAL", "1", 1);
    {
        struct psx_cache * c = psx_cache_new_n(per_expert_bytes * 3, 2);
        const char * err = nullptr;
        int ok = psx_cache_attach_retention_policy(c, &err);
        check(ok == 0, "2 settori con politica attiva rifiutato");
        check(err != nullptr, "messaggio di errore presente");
        psx_cache_free(c);
    }

    // --- 4. Configurazione valida, budget SUFFICIENTE per il working set
    // di ogni singolo evento (6 oggetti: non si richiedono mai piu' di 2
    // expert nuovi per evento in questo scenario, quindi un budget da 6
    // oggetti non forza mai un'auto-sfratto -- il caso "budget troppo
    // piccolo" e' esercitato separatamente alla sezione 6).
    printf("4. b_selected valida: sfratti multipli, riammissione e HIT ripetuto\n");
    {
        REGION_CACHE.clear();
        struct psx_cache * c = psx_cache_new_n(per_expert_bytes * 6 + 4096, 1);
        const char * err = nullptr;
        int ok = psx_cache_attach_retention_policy(c, &err);
        check(ok == 1, "attach con configurazione valida riesce");
        check(psx_cache_has_retention_policy(c) == 1, "politica collegata");

        struct psx_retention_stats rst0;
        psx_cache_get_retention_stats(c, &rst0);
        check(std::string(rst0.policy_id) == "b_selected", "policy_id corretto");
        check(rst0.decay_factor == 0.999, "decay_factor corretto");
        check(rst0.aging_interval_steps == 1, "aging_interval_steps corretto");

        check(evento(c, 0, {0, 1, 2}) == 1, "evento 1: 3 expert nuovi, budget ampio, nessuno sfratto necessario");
        struct psx_cache_stats s1; psx_cache_get_stats(c, &s1);
        check(s1.evictions == 0, "evento 1: zero sfratti");
        check(s1.violazioni == 0, "evento 1: nessuna violazione del budget");

        check(evento(c, 0, {0, 1, 2}) == 1, "evento 1b: richiesta ripetuta degli stessi expert (hit puro)");
        struct psx_cache_stats s1b; psx_cache_get_stats(c, &s1b);
        check(s1b.evictions == 0, "evento 1b: zero sfratti aggiuntivi");
        check(s1b.hits >= 3, "evento 1b: almeno 3 hit fisici registrati (3 kind x 3 expert)");

        check(evento(c, 0, {3, 4}) == 1, "evento 2: 2 expert nuovi, ancora dentro al budget, nessuno sfratto");
        struct psx_cache_stats s2; psx_cache_get_stats(c, &s2);
        check(s2.evictions == 0, "evento 2: zero sfratti (5 oggetti, budget per 6)");
        check(s2.violazioni == 0, "evento 2: nessuna violazione del budget");

        check(evento(c, 0, {5}) == 1, "evento 2b: sesto expert nuovo, budget esattamente pieno");
        check(evento(c, 0, {6}) == 1, "evento 3: settimo expert nuovo -> ora servono sfratti");
        struct psx_cache_stats s3; psx_cache_get_stats(c, &s3);
        printf("    dopo evento 3: evictions totali=%llu violazioni=%llu\n",
               (unsigned long long) s3.evictions, (unsigned long long) s3.violazioni);
        check(s3.evictions >= 1, "evento 3: almeno un sfratto (budget pieno)");
        check(s3.violazioni == 0, "evento 3: nessuna violazione del budget");

        check(evento(c, 0, {0}) == 1, "evento 4: riammissione di un oggetto probabilmente sfrattato");
        struct psx_cache_stats s4; psx_cache_get_stats(c, &s4);
        check(s4.violazioni == 0, "evento 4: nessuna violazione del budget");

        struct psx_retention_stats rst4;
        psx_cache_get_retention_stats(c, &rst4);
        check(rst4.events_served == 6, "sei eventi serviti dalla politica");
        printf("    events_served=%u hits=%llu misses=%llu evictions=%llu resident_bytes=%llu\n",
               rst4.events_served, (unsigned long long) rst4.hits, (unsigned long long) rst4.misses,
               (unsigned long long) rst4.evictions, (unsigned long long) rst4.resident_bytes);

        psx_cache_free(c);
    }

    printf("5. layer misti dentro un evento -> rifiutato\n");
    {
        struct psx_cache * c = psx_cache_new_n(per_expert_bytes * 3, 1);
        const char * err = nullptr;
        check(psx_cache_attach_retention_policy(c, &err) == 1, "attach riuscito");
        struct psx_region * r0 = psx_region_new(4096, 4096, 1);
        struct psx_region * r1 = psx_region_new(4096, 4096, 1);
        struct psx_region * regs[2] = { r0, r1 };
        size_t offs[2] = { 0, 0 };
        uint32_t lays[2] = { 0, 1 };
        uint32_t exps[2] = { 0, 0 };
        enum psx_kind kinds[2] = { PSX_GATE, PSX_GATE };
        int ok = psx_cache_ensure_many(c, STORE, 2, regs, offs, lays, exps, kinds);
        check(ok == 0, "blocco con layer misti sotto una politica attiva viene rifiutato");
        psx_cache_free(c);
    }

    // --- 6. Budget sotto il working set di UN SOLO evento: il riferimento
    // Python puo' scegliere di sfrattare un membro dell'evento corrente
    // prima di ammetterlo fisicamente. Deve fallire in modo esplicito e
    // diagnostico, non ammettere comunque in silenzio (che disallineerebbe
    // stato logico e fisico).
    printf("6. budget sotto il working set di un evento -> fail-closed esplicito, non ammissione silenziosa\n");
    {
        REGION_CACHE.clear();
        // budget per soli 3 oggetti; evento 0 ne richiede 3 (ok, li riempie
        // esattamente); poi un hit ripetuto (sposta i punteggi); poi un
        // evento da 2 NUOVI expert, che forza un auto-sfratto interno
        // all'evento stesso -- lo stesso scenario isolato e verificato
        // contro il riferimento Python in questo round.
        struct psx_cache * c = psx_cache_new_n(per_expert_bytes * 3 + 4096, 1);
        const char * err = nullptr;
        check(psx_cache_attach_retention_policy(c, &err) == 1, "attach riuscito (il rifiuto e' a livello di evento, non di attach)");
        check(evento(c, 0, {0, 1, 2}) == 1, "evento 1: riempie esattamente il budget");
        check(evento(c, 0, {0, 1, 2}) == 1, "evento 1b: hit ripetuto, sposta i punteggi");
        int ok2 = evento(c, 0, {3, 4});
        check(ok2 == 0, "evento 2: fallisce in modo esplicito invece di ammettere in silenzio");
        if (!ok2) {
            printf("    -> %s\n", psx_error(STORE) ? psx_error(STORE) : "(null)");
            check(strstr(psx_error(STORE), "membro dell'evento CORRENTE") != nullptr,
                  "il messaggio nomina esplicitamente la causa (auto-sfratto nello stesso evento)");
        }
        struct psx_cache_stats s6; psx_cache_get_stats(c, &s6);
        check(s6.violazioni == 0, "nessuna violazione del budget nonostante il fallimento dell'evento");
        psx_cache_free(c);
    }

    printf("\n%s: %d fallimenti\n", fallimenti == 0 ? "RISULTATO OK" : "RISULTATO FALLITO", fallimenti);
    return fallimenti == 0 ? 0 : 1;
}
