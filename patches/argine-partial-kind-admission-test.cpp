// R2-12 Phase F: regressione contro il codice di PRODUZIONE
// (argine-store.cpp/argine-retention-policy.cpp, compilati reali, nessun
// finto) che riproduce il crash host reale: STATUS_FATAL_APP_EXIT
// (0x40000015) su entrambe le policy, 321 righe di traccia, morte al passo
// 320/layer 10, stderr apparentemente vuoto.
//
// Catena causale dimostrata, mappando l'RVA Windows 0x6df7cc dentro
// ggml_abort() sull'eseguibile v5 reale (nm: nearest symbol below e'
// ggml_abort+0x8c, esattamente l'istruzione immediatamente dopo la call ad
// abort() -- la firma standard di un STATUS_FATAL_APP_EXIT da abort() su
// questa UCRT, non un access violation):
//
//   argine-experts.cpp: dove[] include una kind diversa da quella del nodo
//   corrente SOLO se c.posizioni la conosce gia' (vedi il commento sopra
//   "struct Dove" in argine-experts.cpp). Alla primissima ammissione di un
//   layer, se up/down non sono ancora state registrate, n_dove < 3 e
//   bytes_of_layer (qui sotto in servi_evento_con_politica) riflette SOLO i
//   kind presenti in QUESTA chiamata -- comportamento gia' documentato
//   come intenzionale per i tensori sul percorso normale, ma che qui
//   produce un sotto-conteggio TRANSITORIO.
//
//   RetentionPolicy::access() (congelata, fedele a AgedFrequency Python: un
//   hit aggiorna solo score/last_ref, MAI ObjState.bytes) marca per sempre
//   l'Obj{layer,expert} residente con quel peso piu' piccolo.
//
//   Un evento SUCCESSIVO per lo stesso layer/expert che richiede una kind
//   mai vista prima (es. up, dopo che e' stata finalmente registrata in
//   c.posizioni) e' un hit logico (la politica pensa l'oggetto gia'
//   residente) ma non ha MAI un indice fisico per quella kind -- prima:
//   s->fail() + GGML_ABORT; ora: guarito come miss fisico (vedi il
//   commento sopra la correzione in servi_evento_con_politica).
//
// Questo test guida esattamente i due eventi che riproducono la sequenza,
// contro lo store reale e la politica reale compilata, SENZA passare da
// ggml/llama.cpp (non necessario: il meccanismo e' tutto qui).
#include "argine-store.h"
#include <cstdio>
#include <cstdlib>
#include <cstring>
#include <vector>
#include <map>
#include <string>

static int fallimenti = 0;
static void check(bool ok, const char * cosa) {
    printf("  [%s] %s\n", ok ? "ok" : "NO", cosa);
    if (!ok) fallimenti++;
}

static psx_store * STORE = nullptr;

// Stesso pattern di argine-retention-integration-test.cpp: una regione
// STABILE per (layer,expert,kind), riusata fra eventi -- esattamente come
// c.posizioni mantiene una psx_region per tensore per tutto il processo
// reale. Una regione nuova a ogni chiamata farebbe apparire ogni accesso
// come una CacheKey mai vista, mascherando il meccanismo del bug.
static std::map<uint64_t, struct psx_region *> REGION_CACHE;
static uint64_t region_key(uint32_t layer, uint32_t expert, int kind) {
    return (((uint64_t) layer << 40) | ((uint64_t) expert << 8) | (uint64_t) kind);
}
static struct psx_region * region_for(uint32_t layer, uint32_t expert, enum psx_kind kind) {
    const uint64_t key = region_key(layer, expert, (int) kind);
    auto it = REGION_CACHE.find(key);
    if (it != REGION_CACHE.end()) return it->second;
    size_t need = psx_slice_read_bytes(STORE, layer, expert, kind);
    struct psx_region * r = psx_region_new(need, need, 1);
    REGION_CACHE[key] = r;
    return r;
}

// Un evento REALE ma PARZIALE: solo le kind elencate in `kinds_presenti`,
// per tutti gli expert dati -- esattamente cio' che argine-experts.cpp
// costruisce in dove[]/da_leggere quando c.posizioni non conosce ancora
// tutte le kind di questo layer.
static int evento_parziale(struct psx_cache * c, uint32_t layer,
                            const std::vector<uint32_t> & experts,
                            const std::vector<enum psx_kind> & kinds_presenti) {
    std::vector<struct psx_region *> regions;
    std::vector<size_t> offsets;
    std::vector<uint32_t> layers, exps;
    std::vector<enum psx_kind> kinds;
    for (uint32_t e : experts) {
        for (enum psx_kind k : kinds_presenti) {
            regions.push_back(region_for(layer, e, k));
            offsets.push_back(0);
            layers.push_back(layer);
            exps.push_back(e);
            kinds.push_back(k);
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
    // Budget ampio: niente pressione di sfratto a confondere il risultato.
    // L'obiettivo qui e' isolare SOLO il meccanismo logico/fisico, non
    // ripetere i test di budget/sfratto gia' coperti altrove.
    const uint64_t budget = per_expert_bytes * 50;

    const std::vector<uint32_t> experts = { 10, 11, 12, 13, 14 };
    const uint32_t layer = 0;

    printf("1. prima ammissione del layer 0 con SOLO gate (dove[] parziale, come alla "
           "primissima visita di un layer prima che c.posizioni registri up/down)\n");
    setenv("ARGINE_RETENTION_POLICY", "b_selected", 1);
    setenv("ARGINE_RETENTION_NEXPERTS", "128", 1);
    setenv("ARGINE_RETENTION_DECAY", "0.999", 1);
    setenv("ARGINE_RETENTION_INTERVAL", "1", 1);
    struct psx_cache * c = psx_cache_new_n(budget, 1);
    const char * err = nullptr;
    check(psx_cache_attach_retention_policy(c, &err) == 1, "attach riuscito");
    check(evento_parziale(c, layer, experts, { PSX_GATE }) == 1,
          "evento 1 (solo gate, 5 expert) ammesso: la politica marca "
          "Obj{layer=0,expert} residente pesando SOLO il gate");

#if !defined(ARGINE_PHASE_F_PRE_FIX_DEMO)
    psx_retention_stats st1;
    psx_cache_get_retention_stats(c, &st1);
    check(st1.policy_hit_sans_index_healed == 0, "nessuna guarigione ancora necessaria dopo l'evento 1");
#else
    psx_retention_stats st1;
    psx_cache_get_retention_stats(c, &st1);
#endif

    printf("2. secondo evento, STESSI expert, ora gate+up (come quando c.posizioni ha "
           "finalmente registrato 'up' e dove[] cresce): gate e' un hit fisico vero "
           "(stessa regione dell'evento 1), up non e' MAI stato indicizzato -- la "
           "politica lo considera comunque un hit logico (stesso Obj{layer,expert})\n");
    int ok2 = evento_parziale(c, layer, experts, { PSX_GATE, PSX_UP });

#if defined(ARGINE_PHASE_F_PRE_FIX_DEMO)
    // Build con -DARGINE_PHASE_F_PRE_FIX_DEMO contro il codice PRIMA della
    // correzione (git show 6d07f4c:patches/argine-store.cpp): qui l'evento 2
    // deve fallire in modo esplicito con esattamente la diagnosi
    // "incoerenza fra stato logico e fisico" -- la riproduzione del
    // meccanismo che su Windows produce GGML_ABORT -> abort() ->
    // STATUS_FATAL_APP_EXIT (0x40000015), con il messaggio scritto nel
    // buffer di stderr ma mai sincronizzato perche' abort() non fa fflush.
    check(ok2 == 0, "PRE-FIX: evento 2 (gate+up) fallisce in modo esplicito, non ammette in "
                    "silenzio e non va in crash muto");
    const char * motivo = psx_error(STORE);
    check(motivo != nullptr && strstr(motivo, "incoerenza fra stato logico e fisico") != nullptr,
          "PRE-FIX: il motivo e' esattamente l'incoerenza logico/fisico diagnosticata sul run host "
          "reale, non un problema di budget");
    printf("    -> %s\n", motivo ? motivo : "(nessun motivo riportato)");
#else
    // Codice CORRETTO (questo pass): l'evento 2 non deve piu' fallire --
    // 'up' viene servito fisicamente come il miss fisico che davvero e',
    // senza toccare nessuno stato della politica (score, residency, budget,
    // predictor: tutti intonsi).
    check(ok2 == 1, "POST-FIX: evento 2 (gate+up) e' servito correttamente invece di abortire");
    psx_retention_stats st2;
    psx_cache_get_retention_stats(c, &st2);
    check(st2.policy_hit_sans_index_healed == experts.size(),
          "POST-FIX: esattamente le 5 kind 'up' mai indicizzate sono state guarite come miss "
          "fisici, una per expert");
    check(st2.hits == st1.hits + experts.size(),
          "POST-FIX: la politica ha ancora visto 5 hit logici per l'evento 2 (stato della "
          "politica non toccato dalla guarigione fisica)");

    printf("3. terzo evento, stessi gate+up: ora deve essere un hit fisico pieno, nessuna "
           "nuova guarigione -- la correzione converge, non si ripete all'infinito\n");
    int ok3 = evento_parziale(c, layer, experts, { PSX_GATE, PSX_UP });
    check(ok3 == 1, "evento 3 servito");
    psx_retention_stats st3;
    psx_cache_get_retention_stats(c, &st3);
    check(st3.policy_hit_sans_index_healed == st2.policy_hit_sans_index_healed,
          "nessuna guarigione aggiuntiva: gate e up sono ora entrambi indicizzati fisicamente");
#endif

    psx_cache_free(c);

    printf(fallimenti == 0 ? "RISULTATO OK: 0 fallimenti\n" : "RISULTATO FALLITO: %d fallimenti\n",
           fallimenti);
    return fallimenti == 0 ? 0 : 1;
}
