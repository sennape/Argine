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
    // prima di ammetterlo fisicamente -- confermato FEDELE al riferimento
    // Python congelato (RETENTION-POLICY-TRACE-INSTRUMENTATION.md sezione
    // 4; RetentionPolicy::access() stesso), non un bug. R2-12 Phase F
    // (sfratto reale sul layer 22, host Windows): questo NON deve piu'
    // fallire -- il nodo reale ha comunque bisogno dei pesi dell'oggetto
    // auto-sfrattato SOLO per la durata di questo evento (transient, vedi
    // Shard::Slot::transient); una volta rilasciato l'ultimo pin,
    // psx_cache_unpin() lo rimuove fisicamente, cosi' la residenza fisica
    // finale coincide con quella che la politica ha gia' deciso, senza
    // alcun sfratto sostitutivo inventato e senza toccare RetentionPolicy.
    printf("6. budget sotto il working set di un evento -> auto-sfratto realizzato in modo transazionale, non piu' un fallimento\n");
    {
        REGION_CACHE.clear();
        // budget per soli 3 oggetti; evento 0 ne richiede 3 (ok, li riempie
        // esattamente); poi un hit ripetuto (sposta i punteggi); poi un
        // evento da 2 NUOVI expert, che forza un auto-sfratto interno
        // all'evento stesso -- lo stesso scenario isolato e verificato
        // contro il riferimento Python in questo round (evicted=[(0,0),
        // (0,3)]: 0 e' una vittima "sicura" sfrattata subito, 3 e' la
        // vittima transient -- ammessa e sfrattata nella stessa chiamata
        // atomica, contributo netto zero ai byte della politica).
        struct psx_cache * c = psx_cache_new_n(per_expert_bytes * 3 + 4096, 1);
        const char * err = nullptr;
        check(psx_cache_attach_retention_policy(c, &err) == 1, "attach riuscito (il rifiuto e' a livello di evento, non di attach)");
        check(evento(c, 0, {0, 1, 2}) == 1, "evento 1: riempie esattamente il budget");
        check(evento(c, 0, {0, 1, 2}) == 1, "evento 1b: hit ripetuto, sposta i punteggi");
        int ok2 = evento(c, 0, {3, 4});
        check(ok2 == 1, "evento 2: realizzato con successo (auto-sfratto transazionale, non un fallimento)");
        struct psx_cache_stats s6; psx_cache_get_stats(c, &s6);
        check(s6.violazioni == 0, "nessuna violazione del budget durante la realizzazione transazionale");
        struct psx_retention_stats rs6; psx_cache_get_retention_stats(c, &rs6);
        check(rs6.policy_same_event_deferred_evictions >= 1,
              "almeno uno sfratto rimandato e' stato davvero realizzato (il meccanismo e' stato esercitato, non evitato)");
        // Stato finale atteso, identico a quello che la politica ha deciso:
        // {1,2,4} residenti (3 oggetti, budget pieno), 0 e 3 fuori.
        check(s6.bytes_resident <= per_expert_bytes * 3 + 4096, "residenza fisica finale entro il budget");
        psx_cache_free(c);
    }

    // --- 7-10. ARGINE_RETENTION_TRACE attraverso il VERO punto di ingresso
    // dell'inferenza reale (psx_cache_ensure_many via evento(), la stessa
    // funzione chiamata da servi_evento_con_politica nel runtime realizzato
    // llama-research-v3/v4 quando ARGINE_EXPERT_READ=blocco). Queste quattro
    // sezioni esistono perche' un run reale sull'host Windows ha mostrato un
    // file di traccia aperto correttamente ma rimasto a 0 byte: la causa
    // reale (ARGINE_EXPERT_READ=asincrono di default, che non chiama mai
    // questa funzione) e' ora un fail-closed in argine-experts.cpp e non e'
    // esercitabile da QUESTO file (che parla con psx_cache direttamente, non
    // con Contesto::inizializza() -- richiederebbe l'intero contesto
    // ggml/llama.cpp per istanziare quel controllo). Qui si verifica invece
    // che, una volta raggiunto il vero punto di ingresso, lo scrittore di
    // traccia produca davvero righe non vuote, una per evento, nessuna
    // quando non richiesta, e segnali un fallimento di scrittura invece di
    // ignorarlo in silenzio.
    printf("7. ARGINE_RETENTION_TRACE attraverso psx_cache_ensure_many: un evento -> una riga JSONL non vuota\n");
    {
        REGION_CACHE.clear();
        const char * trace_path = "/tmp/argine-retention-test-trace-7.jsonl";
        remove(trace_path);
        setenv("ARGINE_RETENTION_TRACE", trace_path, 1);
        struct psx_cache * c = psx_cache_new_n(per_expert_bytes * 6 + 4096, 1);
        const char * err = nullptr;
        int ok = psx_cache_attach_retention_policy(c, &err);
        check(ok == 1, "attach con traccia richiesta riesce");

        struct psx_retention_stats rst_pre;
        psx_cache_get_retention_stats(c, &rst_pre);
        check(rst_pre.trace_requested == 1, "trace_requested riportato vero");
        check(rst_pre.trace_opened == 1, "trace_opened riportato vero (fopen riuscita)");
        check(rst_pre.trace_rows_written == 0, "zero righe scritte prima di qualunque evento reale");

        check(evento(c, 0, {0, 1, 2}) == 1, "evento reale servito attraverso psx_cache_ensure_many");

        struct psx_retention_stats rst_post;
        psx_cache_get_retention_stats(c, &rst_post);
        check(rst_post.events_served == 1, "esattamente un evento osservato dalla politica");
        check(rst_post.trace_rows_attempted == 1, "esattamente una riga tentata dal writer");
        check(rst_post.trace_rows_written == 1, "esattamente una riga scritta senza errori di stream");
        check(rst_post.trace_write_failed == 0, "nessun fallimento di scrittura riportato");

        psx_cache_free(c);
        FILE * f = fopen(trace_path, "r");
        check(f != nullptr, "il file di traccia esiste dopo la chiusura della cache");
        if (f) {
            char buf[4096];
            size_t n = fread(buf, 1, sizeof(buf) - 1, f);
            buf[n] = '\0';
            check(n > 0, "il file di traccia non e' vuoto (il bug reale esercitato: 0 byte)");
            check(strstr(buf, "\"policy_id\"") != nullptr || strstr(buf, "b_selected") != nullptr,
                  "la riga scritta contiene davvero i campi della politica, non e' un file vuoto spacciato per pieno");
            fclose(f);
        }
        remove(trace_path);
    }

    printf("8. piu' eventi reali -> piu' righe JSONL (una per evento, non una per processo)\n");
    {
        REGION_CACHE.clear();
        const char * trace_path = "/tmp/argine-retention-test-trace-8.jsonl";
        remove(trace_path);
        setenv("ARGINE_RETENTION_TRACE", trace_path, 1);
        struct psx_cache * c = psx_cache_new_n(per_expert_bytes * 6 + 4096, 1);
        const char * err = nullptr;
        check(psx_cache_attach_retention_policy(c, &err) == 1, "attach con traccia riesce");

        check(evento(c, 0, {0, 1}) == 1, "evento 1");
        check(evento(c, 0, {2, 3}) == 1, "evento 2");
        check(evento(c, 0, {4, 5}) == 1, "evento 3");

        struct psx_retention_stats rst;
        psx_cache_get_retention_stats(c, &rst);
        check(rst.events_served == 3, "tre eventi osservati");
        check(rst.trace_rows_attempted == 3, "tre righe di decisione tentate, una per evento");
        check(rst.trace_rows_written == 3, "tre righe di decisione scritte, una per evento");
        // R2-12 Phase F: additive -- ogni evento riuscito scrive ANCHE una
        // riga di completamento separata (dopo Fase 1), quindi il file ha
        // ora 2 righe per evento (decisione + completamento), non 1. Questo
        // e' il comportamento voluto (vedi il commento sopra
        // policy_trace_completions_written in argine-store.cpp), non una
        // regressione: il conteggio qui sotto riflette il nuovo totale.
        check(rst.trace_completions_written == 3, "tre righe di completamento scritte, una per evento");

        psx_cache_free(c);
        FILE * f = fopen(trace_path, "r");
        check(f != nullptr, "il file esiste");
        if (f) {
            int righe = 0;
            int righe_complete = 0;
            char linebuf[4096];
            while (fgets(linebuf, sizeof(linebuf), f)) {
                if (linebuf[0] != '\0' && linebuf[0] != '\n') {
                    righe++;
                    if (strstr(linebuf, "\"complete\":1") != nullptr) { righe_complete++; }
                }
            }
            check(righe == 6, "sei righe non vuote nel file: 3 di decisione + 3 di completamento");
            check(righe_complete == 3, "esattamente 3 di quelle sei righe sono righe di completamento");
            fclose(f);
        }
        remove(trace_path);
    }

    printf("9. ARGINE_RETENTION_TRACE non impostata -> nessun file creato, politica comunque attiva\n");
    {
        REGION_CACHE.clear();
        const char * trace_path = "/tmp/argine-retention-test-trace-9.jsonl";
        remove(trace_path);
        unsetenv("ARGINE_RETENTION_TRACE");
        struct psx_cache * c = psx_cache_new_n(per_expert_bytes * 6 + 4096, 1);
        const char * err = nullptr;
        check(psx_cache_attach_retention_policy(c, &err) == 1, "attach riesce senza traccia richiesta");

        struct psx_retention_stats rst_pre;
        psx_cache_get_retention_stats(c, &rst_pre);
        check(rst_pre.trace_requested == 0, "trace_requested falso: nessuna variabile d'ambiente impostata");
        check(rst_pre.trace_opened == 0, "trace_opened falso: nessun fopen tentato");

        check(evento(c, 0, {0, 1}) == 1, "la politica serve comunque l'evento senza traccia");

        struct psx_retention_stats rst_post;
        psx_cache_get_retention_stats(c, &rst_post);
        check(rst_post.events_served == 1, "l'evento e' stato osservato dalla politica anche senza traccia");
        check(rst_post.trace_rows_attempted == 0, "zero righe tentate: non c'e' uno stream a cui scrivere");

        psx_cache_free(c);
        FILE * f = fopen(trace_path, "r");
        check(f == nullptr, "nessun file di traccia creato quando la variabile non e' impostata");
        if (f) fclose(f);
    }

    printf("10. scrittura di traccia fallita -> segnalata (trace_write_failed), non ignorata in silenzio\n");
    {
        REGION_CACHE.clear();
        // /dev/full: fopen("w") riesce (e' un carattere device valido), ma
        // ogni write successivo fallisce con ENOSPC -- esattamente il tipo
        // di fallimento silenzioso che un fflush() senza controllo del
        // valore di ritorno nasconderebbe (il bug che questa sezione
        // esercita direttamente, non per analogia).
        setenv("ARGINE_RETENTION_TRACE", "/dev/full", 1);
        struct psx_cache * c = psx_cache_new_n(per_expert_bytes * 6 + 4096, 1);
        const char * err = nullptr;
        int ok = psx_cache_attach_retention_policy(c, &err);
        check(ok == 1, "attach riesce: /dev/full si apre correttamente in scrittura");

        struct psx_retention_stats rst_pre;
        psx_cache_get_retention_stats(c, &rst_pre);
        check(rst_pre.trace_opened == 1, "trace_opened vero: il fallimento e' nella scrittura, non nell'apertura");

        check(evento(c, 0, {0, 1}) == 1, "l'evento reale e' comunque servito dalla politica nonostante lo scrittore fallisca");

        struct psx_retention_stats rst_post;
        psx_cache_get_retention_stats(c, &rst_post);
        check(rst_post.events_served == 1, "l'evento e' stato osservato dalla politica");
        check(rst_post.trace_rows_attempted == 1, "una riga e' stata tentata");
        check(rst_post.trace_write_failed == 1, "il fallimento di scrittura e' segnalato nelle statistiche");
        check(rst_post.trace_rows_written == 0, "la riga fallita non e' contata come scritta con successo");

        psx_cache_free(c);
    }
    unsetenv("ARGINE_RETENTION_TRACE");

    // --- 11-12. Riproduzione della forma esatta del fallimento reale
    // riportato dall'host Windows: 15 expert su 128 richiesti per il layer 0,
    // budget 4096 MiB ampiamente sufficiente, eppure "impossibile rendere
    // residenti" con solo 9-12 MiB residenti. La causa reale NON era il
    // budget (vedi RUNTIME-REALIZATION-PROVENANCE-V5-ADMISSION-FIX.md):
    // ARGINE_EXPERT_READ=blocco senza :N usa c.blocco=2 di default
    // (leggi_modo in argine-experts.cpp), e OGNI thread partecipante chiama
    // psx_cache_ensure_many sulla SUA sotto-fetta -- quindi anche con
    // c.blocco grande, un nodo da 15 expert veniva comunque frammentato in
    // piu' chiamate psx_cache_ensure_many indipendenti, ciascuna un "evento"
    // distinto per la politica, mentre le slice ammesse da chiamate
    // PRECEDENTI dello stesso nodo restavano bloccate (pins>0, sbloccate
    // solo alla fine del nodo reale). Una chiamata successiva poteva quindi
    // scegliere come vittima un oggetto del proprio STESSO nodo logico ma
    // non del proprio sotto-blocco -- il controllo "membro dell'evento
    // CORRENTE" non lo vede (guarda solo gli expert di QUESTA chiamata), e
    // il fallimento emerge invece dall'invariante "pins>0" di
    // servi_evento_con_politica.
    //
    // Sezione 11 e' la garanzia positiva richiesta esplicitamente: un nodo
    // da 15 expert su un budget ampiamente sufficiente, servito con UNA
    // sola chiamata psx_cache_ensure_many (la disciplina imposta dal fix in
    // argine-experts.cpp), deve ammettere senza alcun autosfratto. Sezione
    // 12 riproduce il MECCANISMO del bug originale -- la stessa richiesta
    // frammentata in piu' chiamate separate con i pin della chiamata
    // precedente ancora attivi -- per dimostrare che la causa individuata e'
    // davvero sufficiente a spiegare il sintomo reale, non solo plausibile.
    printf("11. nodo MoE reale: 15 expert su layer 0 (store da 128), budget ampio, UNA chiamata -> ammesso senza autosfratto\n");
    {
        REGION_CACHE.clear();
        unsetenv("ARGINE_RETENTION_TRACE");
        setenv("ARGINE_RETENTION_POLICY", "b_selected", 1);
        setenv("ARGINE_RETENTION_DECAY", "0.99", 1);
        setenv("ARGINE_RETENTION_INTERVAL", "64", 1);
        // Budget ampiamente sufficiente per 15 expert, nello stesso spirito
        // del run reale (4096 MiB per un nodo che ne chiede 15 su 128): qui
        // 40 volte il bisogno di un solo expert, contro i 15 richiesti.
        struct psx_cache * c = psx_cache_new_n(per_expert_bytes * 40, 1);
        const char * err = nullptr;
        int ok = psx_cache_attach_retention_policy(c, &err);
        check(ok == 1, "attach riesce con budget ampio");

        std::vector<uint32_t> quindici;
        for (uint32_t e = 0; e < 15; e++) quindici.push_back(e);
        // cache FREDDA: nessun expert residente prima di questa singola
        // chiamata -- esattamente come il primo nodo del primo layer di un
        // run reale appena avviato.
        struct psx_cache_stats s_pre; psx_cache_get_stats(c, &s_pre);
        check(s_pre.bytes_resident == 0, "cache fredda prima dell'evento (0 byte residenti)");

        const int ok15 = evento(c, 0, quindici);
        check(ok15 == 1, "i 15 expert richiesti sono ammessi in UNA chiamata, senza autosfratto");
        if (!ok15) {
            printf("    -> %s\n", psx_error(STORE) ? psx_error(STORE) : "(null)");
        }

        struct psx_cache_stats s_post; psx_cache_get_stats(c, &s_post);
        check(s_post.evictions == 0, "zero sfratti: il budget basta abbondantemente per 15 expert");
        check(s_post.violazioni == 0, "nessuna violazione del budget");
        check(s_post.bytes_resident >= per_expert_bytes * 15,
              "almeno i byte dei 15 expert richiesti sono effettivamente residenti");

        struct psx_retention_stats rst; psx_cache_get_retention_stats(c, &rst);
        check(rst.events_served == 1, "UN solo evento servito dalla politica per l'intero nodo");

        psx_cache_free(c);
    }

    printf("12. stesso nodo frammentato come il vero c.blocco=2 (ordine per KIND, non per expert) -> riproduce esattamente il meccanismo del bug originale\n");
    {
        REGION_CACHE.clear();
        setenv("ARGINE_RETENTION_POLICY", "b_selected", 1);
        setenv("ARGINE_RETENTION_DECAY", "0.99", 1);
        setenv("ARGINE_RETENTION_INTERVAL", "64", 1);
        // L'ordine REALE con cui argine-experts.cpp costruisce il blocco
        // (ramo LETTURA_BLOCCO, k da 0 a totale-1) e':
        //   d = dove[k / n_serve];   x = da_leggere[k % n_serve];
        // cioe' PER KIND, non per expert: tutti gli n_serve expert della
        // stessa kind (es. tutti e 15 i "gate") prima di passare alla kind
        // successiva. Con c.blocco=2 (il default quando ARGINE_EXPERT_READ
        // =blocco e' dato senza :N -- vedi leggi_modo), il PRIMO sotto-
        // blocco e' quindi {expert0-gate, expert1-gate}: due expert DIVERSI
        // della STESSA kind, non due kind dello stesso expert.
        //
        // RetentionPolicy::access() (argine-retention-policy.cpp) traccia
        // la residenza per Obj{layer,expert} -- non per (layer,expert,kind)
        // -- e usa `bytes_of_layer` (le kind DISTINTE viste in QUESTA
        // chiamata, qui solo "gate") come il "peso" dell'intero oggetto
        // quando lo marca resident. Dopo questo primo sotto-blocco, la
        // politica crede expert0 ed expert1 GIA' interamente residenti,
        // pur avendone fisicamente ammessa solo la kind "gate". Un
        // sotto-blocco SUCCESSIVO che richiede expert0-up (kind diversa,
        // stesso expert) lo trova un HIT logico (find_resident non-null),
        // ma fisicamente quella CacheKey non e' mai stata indicizzata.
        //
        // R2-12 Phase F: questo e' esattamente il meccanismo dietro il
        // crash host reale su v5 (STATUS_FATAL_APP_EXIT via GGML_ABORT,
        // mappato all'RVA Windows 0x6df7cc dentro ggml_abort() -- vedi
        // patches/argine-partial-kind-admission-test.cpp e BUILD-INFO.json
        // di llama-research-v7). servi_evento_con_politica non rifiuta piu'
        // questa incoerenza con un abort fatale: la guarisce fisicamente
        // come il miss che davvero e' (psx_region_commit + lettura +
        // indicizzazione), senza toccare nessuno stato della politica.
        // Questa sezione ora verifica la guarigione, non il vecchio
        // fallimento esplicito -- che resta riprodotto e provato contro il
        // codice pre-fix in argine-partial-kind-admission-test.cpp.
        struct psx_cache * c = psx_cache_new_n(per_expert_bytes * 40, 1);
        const char * err = nullptr;
        const int ok_attach12 = psx_cache_attach_retention_policy(c, &err);
        check(ok_attach12 == 1, "attach riesce");
        if (!ok_attach12) printf("    -> attach err: %s\n", err ? err : "(null)");

        const uint32_t n_serve12 = 15;  // stesso nodo di sezione 11: 15 expert su layer 0
        std::vector<enum psx_kind> dove12 = { PSX_GATE, PSX_UP, PSX_DOWN };  // ordine "dove[]" reale
        const uint32_t totale12 = n_serve12 * (uint32_t) dove12.size();
        bool fallito_in_frammento = false;
        const char * motivo = nullptr;
        for (uint32_t base = 0; base < totale12 && !fallito_in_frammento; base += 2) {
            std::vector<struct psx_region*> regions;
            std::vector<size_t> offsets;
            std::vector<uint32_t> layers, exps;
            std::vector<enum psx_kind> kinds;
            for (uint32_t k = base; k < std::min<uint32_t>(base + 2, totale12); k++) {
                const enum psx_kind kind = dove12[k / n_serve12];
                const uint32_t      x    = k % n_serve12;
                const struct psx_entry * ent = psx_get(STORE, 0, x);
                if (!ent) { continue; }
                struct psx_region * r = region_for(0, x, kind);
                regions.push_back(r); offsets.push_back(0);
                layers.push_back(0); exps.push_back(x); kinds.push_back(kind);
            }
            if (regions.empty()) continue;
            const int ok_frammento = psx_cache_ensure_many(c, STORE, regions.size(), regions.data(),
                                                            offsets.data(), layers.data(), exps.data(),
                                                            kinds.data());
            if (!ok_frammento) {
                fallito_in_frammento = true;
                motivo = psx_error(STORE);
                break;
            }
            // Pin NON rilasciati fra sotto-blocchi, esattamente come il nodo
            // reale (pin_miei si scarica solo all'inizio del nodo SUCCESSIVO
            // in argine-experts.cpp, non fra i sotto-blocchi di LETTURA_BLOCCO
            // dello stesso nodo).
        }
        check(!fallito_in_frammento,
              "R2-12 Phase F: la frammentazione per kind (come il vero c.blocco=2) NON fallisce piu' -- "
              "guarita fisicamente invece di GGML_ABORT");
        if (fallito_in_frammento) {
            printf("    -> %s\n", motivo ? motivo : "(null)");
        } else {
            struct psx_cache_stats s_post12; psx_cache_get_stats(c, &s_post12);
            check(s_post12.violazioni == 0, "nessuna violazione del budget nonostante la guarigione");
            struct psx_retention_stats rst12; psx_cache_get_retention_stats(c, &rst12);
            check(rst12.policy_hit_sans_index_healed > 0,
                  "almeno una kind e' stata guarita come hit-logico-senza-indice-fisico "
                  "(il meccanismo del bug originale e' stato davvero esercitato, non evitato)");
        }
        psx_cache_free(c);
    }
    unsetenv("ARGINE_RETENTION_POLICY");
    unsetenv("ARGINE_RETENTION_DECAY");
    unsetenv("ARGINE_RETENTION_INTERVAL");

    printf("\n%s: %d fallimenti\n", fallimenti == 0 ? "RISULTATO OK" : "RISULTATO FALLITO", fallimenti);
    return fallimenti == 0 ? 0 : 1;
}
