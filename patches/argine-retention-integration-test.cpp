// R2-12: integration test of the retention-policy wiring against the REAL
// compiled argine-store.cpp + argine-retention-policy.cpp, driven against a
// REAL .psxs store file (not a synthetic/simulated one). No llama.cpp/ggml
// involved: this exercises the actual new code paths (psx_cache_attach_
// retention_policy, servi_evento_con_politica inside psx_cache_ensure_many)
// directly, same spirit as the pre-existing argine-store-test.cpp.
#include "argine-store.h"
#include <cstdio>
#include <cstdlib>
#include <cstring>
#include <vector>
#include <string>

static int fallimenti = 0;
static void check(bool ok, const char * cosa) {
    printf("  [%s] %s\n", ok ? "ok" : "NO", cosa);
    if (!ok) fallimenti++;
}

static psx_store * STORE = nullptr;

// Esegue un evento: un layer, un insieme di expert, i kind gate+up+down per
// ciascuno (3 slice per expert, come il dispatch reale).
static int evento(struct psx_cache * c, uint32_t layer, const std::vector<uint32_t> & experts) {
    std::vector<struct psx_region *> regions_store; // not used directly; regions come from psx entries
    (void) regions_store;
    std::vector<struct psx_region*> regions;
    std::vector<size_t> offsets;
    std::vector<uint32_t> layers, exps;
    std::vector<enum psx_kind> kinds;
    static std::vector<struct psx_region*> owned_regions; // keep regions alive across the whole test run

    for (uint32_t e : experts) {
        const struct psx_entry * ent = psx_get(STORE, layer, e);
        if (!ent) { fprintf(stderr, "psx_get null for layer=%u expert=%u\n", layer, e); return 0; }
        for (int k = 0; k < (int) PSX_N_KINDS; k++) {
            // one region per (layer,expert,kind): big enough for the slice,
            // one "slot" since we only ever need offset 0 in it here.
            size_t need = psx_slice_read_bytes(STORE, layer, e, (enum psx_kind) k);
            struct psx_region * r = psx_region_new(need, need, 1);
            owned_regions.push_back(r);
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

    // --- 1. Nessuna politica richiesta: comportamento di default invariato -
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

    // --- 2. Identificatore non riconosciuto: fail-closed -------------------
    printf("2. politica invalida -> fail-closed\n");
    setenv("ARGINE_RETENTION_POLICY", "non_esiste", 1);
    {
        struct psx_cache * c = psx_cache_new_n(per_expert_bytes * 3, 1);
        const char * err = nullptr;
        int ok = psx_cache_attach_retention_policy(c, &err);
        check(ok == 0, "identificatore sconosciuto rifiutato");
        check(err != nullptr && err[0] != '\0', "messaggio di errore non vuoto");
        printf("    -> %s\n", err ? err : "(null)");
        check(psx_cache_has_retention_policy(c) == 0, "nessuna politica rimane collegata dopo il rifiuto");
        psx_cache_free(c);
    }

    // --- 3. Settori != 1 con politica richiesta: fail-closed ---------------
    printf("3. settori != 1 con politica richiesta -> fail-closed\n");
    setenv("ARGINE_RETENTION_POLICY", "b_selected", 1);
    setenv("ARGINE_RETENTION_DECAY", "0.999", 1);
    setenv("ARGINE_RETENTION_INTERVAL", "1", 1);
    {
        struct psx_cache * c = psx_cache_new_n(per_expert_bytes * 3, 2);  // 2 settori: sbagliato
        const char * err = nullptr;
        int ok = psx_cache_attach_retention_policy(c, &err);
        check(ok == 0, "2 settori con politica attiva rifiutato");
        check(err != nullptr, "messaggio di errore presente");
        printf("    -> %s\n", err ? err : "(null)");
        psx_cache_free(c);
    }

    // --- 4. Configurazione valida: sfratti multipli e riammissione ---------
    printf("4. b_selected valida: sfratti multipli e riammissione\n");
    {
        // budget per ~3 oggetti (expert) residenti insieme
        struct psx_cache * c = psx_cache_new_n(per_expert_bytes * 3 + 4096, 1);
        const char * err = nullptr;
        int ok = psx_cache_attach_retention_policy(c, &err);
        check(ok == 1, "attach con configurazione valida riesce");
        check(psx_cache_has_retention_policy(c) == 1, "politica collegata");

        struct psx_retention_stats rst0;
        psx_cache_get_retention_stats(c, &rst0);
        check(std::string(rst0.policy_id) == "b_selected", "policy_id corretto");
        check(rst0.decay_factor == 0.999, "decay_factor corretto");
        check(rst0.aging_interval_steps == 1, "aging_interval_steps corretto");

        // evento 1: 3 expert nuovi, tutti entrano (budget esatto)
        check(evento(c, 0, {0, 1, 2}) == 1, "evento 1: 3 expert nuovi, nessuno sfratto necessario");
        struct psx_cache_stats s1; psx_cache_get_stats(c, &s1);
        check(s1.evictions == 0, "evento 1: zero sfratti");
        check(s1.violazioni == 0, "evento 1: nessuna violazione del budget");

        // evento 2: 2 expert nuovi (3,4) -> servono sfratti multipli (il budget
        // tiene solo 3 oggetti, 3 sono gia' residenti)
        check(evento(c, 0, {3, 4}) == 1, "evento 2: 2 expert nuovi, sfratti multipli");
        struct psx_cache_stats s2; psx_cache_get_stats(c, &s2);
        printf("    dopo evento 2: evictions totali=%llu violazioni=%llu\n",
               (unsigned long long) s2.evictions, (unsigned long long) s2.violazioni);
        check(s2.evictions >= 2, "evento 2: almeno due sfratti (sfratto multiplo verificato)");
        check(s2.violazioni == 0, "evento 2: nessuna violazione del budget");

        // evento 3: richiedi di nuovo l'expert 0 (quasi certamente sfrattato
        // da tempo) -> riammissione dopo sfratto (edge case della sezione 5)
        check(evento(c, 0, {0}) == 1, "evento 3: riammissione di un oggetto precedentemente sfrattato");
        struct psx_cache_stats s3; psx_cache_get_stats(c, &s3);
        check(s3.violazioni == 0, "evento 3: nessuna violazione del budget");

        struct psx_retention_stats rst3;
        psx_cache_get_retention_stats(c, &rst3);
        check(rst3.events_served == 3, "tre eventi serviti dalla politica");
        printf("    events_served=%u hits=%llu misses=%llu evictions=%llu resident_bytes=%llu\n",
               rst3.events_served, (unsigned long long) rst3.hits, (unsigned long long) rst3.misses,
               (unsigned long long) rst3.evictions, (unsigned long long) rst3.resident_bytes);

        psx_cache_free(c);
    }

    // --- 5. Stesso evento, layer misti: rifiutato ---------------------------
    printf("5. layer misti dentro un evento -> rifiutato\n");
    {
        struct psx_cache * c = psx_cache_new_n(per_expert_bytes * 3, 1);
        const char * err = nullptr;
        check(psx_cache_attach_retention_policy(c, &err) == 1, "attach riuscito");
        // costruisco a mano un blocco con layer misti (bypassando evento())
        struct psx_region * r0 = psx_region_new(4096, 4096, 1);
        struct psx_region * r1 = psx_region_new(4096, 4096, 1);
        struct psx_region * regs[2] = { r0, r1 };
        size_t offs[2] = { 0, 0 };
        uint32_t lays[2] = { 0, 1 };  // misti
        uint32_t exps[2] = { 0, 0 };
        enum psx_kind kinds[2] = { PSX_GATE, PSX_GATE };
        int ok = psx_cache_ensure_many(c, STORE, 2, regs, offs, lays, exps, kinds);
        check(ok == 0, "blocco con layer misti sotto una politica attiva viene rifiutato");
        psx_cache_free(c);
    }

    printf("\n%s: %d fallimenti\n", fallimenti == 0 ? "RISULTATO OK" : "RISULTATO FALLITO", fallimenti);
    return fallimenti == 0 ? 0 : 1;
}
