// Argine -- expert retention-policy module (R2-12 realization/conformance phase).
//
// Autonomo per scelta, come argine-store.{h,cpp}: non include niente di
// llama.cpp o di ggml, cosi' si compila e si collauda da solo contro tracce
// sintetiche o registrate, SENZA modello reale e SENZA avviare llama-server.
//
// Implementa, bit-per-bit secondo le classi Python frozen di riferimento, le
// due condizioni scientifiche congelate di R2-12:
//
//   A = B_selected(M)         -> argine.research2.r2_09a.policies.AgedFrequency
//   B = PredictedProtectPolicy -> argine.research2.r2_bridge01.retention_policies
//                                 .PredictedProtectPolicy, con il predittore
//                                 argine.research2.r2_10.predictors
//                                 .DecayedTransitionPredictor
//
// Questo modulo NON sceglie da solo quale politica usare per R2-12: si limita
// a esporre un meccanismo esplicito e verificabile. La scelta dei parametri
// (decay, interval, decay del predittore, top_k) resta quella congelata nei
// contratti Python; qui vengono accettati espliciti, mai dedotti o inventati,
// e validati contro la griglia congelata (fail-closed su qualunque valore
// fuori griglia o identificatore di politica non riconosciuto).
//
// Stato: in attesa di rete, nessuna rete.

#ifndef ARGINE_RETENTION_POLICY_H
#define ARGINE_RETENTION_POLICY_H

#include <cstdint>
#include <string>
#include <unordered_map>
#include <utility>
#include <vector>

namespace argine_retention {

// --- identificatori di politica --------------------------------------------

enum class PolicyKind {
    NONE,              // ARGINE_RETENTION_POLICY non impostata: nessun cambiamento
                        // di comportamento rispetto all'LRU esistente.
    B_SELECTED,        // aged_frequency, B_selected(M) -- condizione A.
    PREDICTED_PROTECT, // predicted_protect / decayed_transition -- condizione B.
    INVALID,           // identificatore non riconosciuto, o parametri fuori
                        // dalla griglia congelata: fail-closed, nessuna
                        // politica viene attivata.
};

// Le tre coppie (decay_factor, aging_interval_steps) di B_selected(M), una per
// ciascuno dei tre budget primari R2-09A/R2-12. Copiate verbatim da
// experiments/R2-09-prediction-benchmark/r2_09a/R2-09A-FREEZE.json via
// argine/research2/r2_bridge01/retention_policies.py
// B_SELECTED_PARAMS_BY_BUDGET. Non ritoccare: qualunque modifica qui e' una
// modifica di un parametro scientifico congelato, vietata da questa fase.
struct AgedFrequencyParams {
    double   decay_factor;
    uint32_t aging_interval_steps;
};

// Non e' un elenco esauriente di "tutte le coppie plausibili": e' esattamente
// l'insieme congelato, e la validazione a runtime richiede che la coppia
// fornita coincida esattamente con una di queste tre (confronto esatto sul
// decay_factor incluso -- nessuna tolleranza in virgola mobile, perche' i
// tre valori sono letterali decimali esatti nella sorgente Python/JSON e
// devono arrivare qui identici).
inline const AgedFrequencyParams FROZEN_B_SELECTED_1_5_GIB{0.999, 1};
inline const AgedFrequencyParams FROZEN_B_SELECTED_4_GIB{0.99, 64};
inline const AgedFrequencyParams FROZEN_B_SELECTED_8_GIB{0.999, 16};

// Griglia congelata del predittore decayed_transition (R2-10
// predictor_space.json DECAYED_TRANSITION_GRID). R2-12 usa solo 0.99; gli
// altri due valori sono qui solo per validare "e' nella griglia", non per
// offrire una scelta: selezionarne uno diverso da 0.99 per R2-12 sarebbe
// un ritocco di un parametro congelato e viene rifiutato a un livello piu'
// alto (il contratto R2-12), non da questa libreria, che si limita a
// rifiutare valori FUORI dalla griglia dei predittori R2-10 in generale.
inline const double DECAYED_TRANSITION_GRID[3] = {0.9, 0.99, 0.999};

struct RetentionConfig {
    PolicyKind kind = PolicyKind::NONE;
    AgedFrequencyParams base{0.0, 0};     // usato da entrambe le politiche non-NONE
    double   predictor_decay = 0.0;       // solo PREDICTED_PROTECT
    uint32_t top_k = 0;                   // solo PREDICTED_PROTECT (cardinalita' prevista)
    uint32_t n_experts = 0;               // solo PREDICTED_PROTECT (universo del predittore)
    std::string rejection_reason;         // non vuoto se kind == INVALID
};

// Legge ARGINE_RETENTION_POLICY, ARGINE_RETENTION_DECAY,
// ARGINE_RETENTION_INTERVAL, ARGINE_RETENTION_PREDICTOR_DECAY,
// ARGINE_RETENTION_TOPK, ARGINE_RETENTION_NEXPERTS dall'ambiente del
// processo e produce una RetentionConfig. Fail-closed: qualunque valore
// mancante, malformato, o fuori dalla griglia congelata produce
// kind == INVALID con rejection_reason non vuoto; IL CHIAMANTE deve trattare
// INVALID esattamente come un errore fatale di configurazione (si rifiuta di
// avviarsi con quella politica), MAI come "ricadi silenziosamente su NONE".
// L'assenza della variabile ARGINE_RETENTION_POLICY (non impostata o stringa
// vuota) e' l'UNICO caso che produce NONE: e' la scelta di non attivare
// affatto il meccanismo, non un errore.
RetentionConfig parse_retention_config_from_env();

// Variante esplicita (per i test, e per il chiamante che preferisce passare
// valori gia' letti invece di affidarsi a getenv direttamente).
RetentionConfig make_retention_config(
    const std::string & policy_id,
    double decay_factor,
    uint32_t aging_interval_steps,
    double predictor_decay,
    uint32_t top_k,
    uint32_t n_experts);

// --- il nucleo deterministico -----------------------------------------------
//
// Obj = (layer, expert). Un oggetto occupa `bytes_of(layer)` byte quando
// residente; le tre slice (gate, up, down) si muovono insieme, come il
// commento Python gia' annota: "modelling them as one unit is an
// approximation, exact when the three are always loaded and evicted
// together (the gate node loads all three, so they are loaded together)".
// Questo modulo quindi ragiona in unita' di (layer, expert), non di singola
// slice: chi lo integra nella cache reale deve tradurre ogni decisione in
// esattamente tre CacheKey (gate, up, down) impegnate o sfrattate insieme.
struct Obj {
    uint32_t layer;
    uint32_t expert;
    bool operator==(const Obj & o) const { return layer == o.layer && expert == o.expert; }
};

struct ObjHash {
    size_t operator()(const Obj & o) const {
        return (size_t) (((uint64_t) o.layer << 32) ^ (uint64_t) o.expert);
    }
};

// Risultato di un evento (un nodo MoE: un layer, il suo insieme di expert
// richiesti da UN forward pass). Rispecchia esattamente
// argine.research.policies.Policy.access(): un evento e' atomico, i suoi
// membri sono processati in ordine crescente di expert id (questo modulo lo
// fa internamente: il chiamante passa l'insieme non ordinato, qui viene
// ordinato prima di ogni decisione), e nessun membro dell'evento corrente
// viene sfrattato per farne spazio ad un altro membro dello STESSO evento
// (overflow: se un evento non entra nemmeno vuotando tutto, i membri in
// eccesso sono "overflow_misses", mai un crash o una decisione silenziosa).
struct EventOutcome {
    std::vector<uint32_t> missed;            // expert id del layer corrente, mancanti, ordine crescente
    std::vector<Obj>      evicted;           // oggetti sfrattati per fare spazio, nell'ordine in cui e' avvenuto
    uint32_t               overflow_events = 0;
    uint32_t               overflow_misses = 0;
};

// Contatori di provenienza/istrumentazione (sezione 9 della richiesta):
// bastano a dimostrare quale politica era attiva e cosa ha fatto, senza
// usarli per riportare uno speedup -- non misurano tempo.
struct RetentionStats {
    std::string policy_id;          // "b_selected" | "predicted_protect"
    double      decay_factor = 0.0;
    uint32_t    aging_interval_steps = 0;
    double      predictor_decay = 0.0;     // 0.0 se non applicabile (b_selected)
    uint64_t    hits = 0;
    uint64_t    misses = 0;
    uint64_t    evictions = 0;
    uint64_t    resident_bytes = 0;
    uint64_t    predictor_decisions = 0;   // numero di refresh del predittore eseguiti
    uint64_t    protected_fallbacks = 0;   // predicted_protect: volte in cui lo shield e' stato
                                            // derogato perche' tutti i residenti del layer erano protetti
};

// Interfaccia comune: entrambe le politiche condividono il nucleo
// score/last_ref/aging identico ad AgedFrequency; differiscono solo nella
// selezione della vittima (_victim_key / _choose_victim nel riferimento
// Python). Questa classe e' quel nucleo condiviso, con un punto di
// estensione virtuale esattamente dove il Python lo ha.
class RetentionPolicy {
public:
    virtual ~RetentionPolicy() = default;

    // ctx.budget (M, in byte) e bytes_of(layer) sono forniti dal chiamante,
    // esattamente come argine.research.policies.Context: questo modulo non
    // conosce la dimensione reale di un expert, la riceve.
    void start(uint64_t budget_bytes);

    // Un evento: UN layer, l'insieme (anche con ripetizioni, deduplicate
    // qui come fa Policy.access con sorted(set(experts))) di expert
    // richiesti. bytes_of_layer e' la dimensione in byte di UN expert di
    // QUESTO layer (gate+up+down insieme).
    EventOutcome access(uint32_t step, uint32_t layer,
                         const std::vector<uint32_t> & experts,
                         uint64_t bytes_of_layer);

    RetentionStats stats() const;

    // Sola lettura, per il conformance harness: espone lo stato interno
    // minimo necessario a confrontare ogni punto di decisione col
    // riferimento Python (resident set, protetti, punteggio, last_ref).
    struct Inspection {
        std::vector<Obj>    resident;          // ordine di inserimento della mappa interna (non significativo)
        std::vector<double> resident_scores;   // stesso ordine di `resident`
        std::vector<int64_t> resident_last_ref; // stesso ordine di `resident`
        std::vector<uint32_t> protected_experts_for_layer; // solo predicted_protect; vuoto altrimenti
    };
    Inspection inspect(uint32_t layer_for_protected_query) const;

    // Accessore AGGIUNTIVO, di SOLA osservazione -- autorizzato esplicitamente
    // dall'utente per chiudere il gap di visibilita' che inspect() non puo'
    // chiudere (inspect() e' non-virtuale sulla classe base e non puo'
    // raggiungere predicted_next_, che e' privato della classe derivata; vedi
    // RETENTION-POLICY-TRACE-INSTRUMENTATION.md). NON partecipa ad alcuna
    // decisione: nessun metodo di scelta della vittima, nessuna logica di
    // sfratto, predittore o budget chiama questo metodo o dipende dal suo
    // valore di ritorno -- choose_victim() continua a consultare
    // predicted_next_ direttamente, esattamente come prima di questa
    // aggiunta. Espone, in sola lettura, l'insieme protetto GIA' CALCOLATO
    // per `layer` dall'evento causalmente precedente sullo stesso layer
    // (dopo_evento/after_event di QUEL evento) -- lo stesso istante causale
    // che choose_victim() stesso consulta durante l'evento corrente, non un
    // valore ricalcolato o approssimato. La classe base ritorna sempre
    // l'insieme vuoto (AgedFrequency pura non ha alcun concetto di
    // protezione); solo PredictedProtectPolicy la sovrascrive.
    virtual std::vector<uint32_t> protected_for(uint32_t layer) const { (void) layer; return {}; }

protected:
    explicit RetentionPolicy(double decay_factor, uint32_t aging_interval_steps);

    // Punto di estensione: AgedFrequency pura lo lascia com'e' (tutti i
    // residenti sono candidati); PredictedProtectPolicy lo sovrascrive per
    // escludere, quando possibile, i membri protetti dello STESSO layer
    // dell'accesso corrente (vedi Python _choose_victim).
    virtual Obj choose_victim(uint32_t layer_of_current_access) const;

    // Chiamato DOPO che un evento e' stato completamente servito (dopo
    // l'ammissione/sfratto di tutti i suoi membri), esattamente come
    // Python chiama _refresh_prediction_after_observing alla fine di
    // access(). AgedFrequency pura non fa nulla; PredictedProtectPolicy
    // aggiorna il predittore del layer e il set protetto.
    virtual void after_event(uint32_t step, uint32_t layer,
                              const std::vector<uint32_t> & served_sorted);

    uint64_t M_ = 0;
    uint64_t resident_bytes_ = 0;
    double   decay_factor_;
    uint32_t aging_interval_steps_;
    uint64_t t_ = 0;  // ordinale causale globale, UNA sola sequenza per l'intera traccia

    // Stato residente: tre mappe parallele, esattamente come Python usa tre
    // dict separati (resident/scores/last_ref) sulla stessa chiave Obj.
    struct ObjState {
        uint64_t bytes;
        double   score;
        int64_t  last_ref;
    };
    std::vector<std::pair<Obj, ObjState>> resident_; // scansione lineare: gli insiemi per evento sono piccoli (top_k),
                                                       // e la fedelta' al riferimento conta piu' della costante.

    uint64_t hits_ = 0, misses_ = 0, evictions_ = 0;
    uint64_t overflow_events_ = 0, overflow_misses_ = 0;

    ObjState * find_resident(const Obj & o);
    const ObjState * find_resident(const Obj & o) const;
    void erase_resident(const Obj & o);
    void apply_aging_if_due();
    bool evict_one(uint32_t layer_of_current_access);
};

// Condizione A: B_selected(M) = aged_frequency, nessuna restrizione sulla
// vittima (choose_victim non viene sovrascritto).
class BSelectedPolicy final : public RetentionPolicy {
public:
    explicit BSelectedPolicy(const AgedFrequencyParams & p)
        : RetentionPolicy(p.decay_factor, p.aging_interval_steps) {}
    RetentionStats stats_with_id() const;
};

// Condizione B: PredictedProtectPolicy / decayed_transition. Un predittore
// causale indipendente per layer (mai condiviso, mai riusato da una
// traccia precedente -- stesso vincolo del riferimento Python), aggiornato
// strettamente DOPO aver servito l'evento corrente, cosi' la previsione
// puo' influenzare solo un accesso FUTURO sullo stesso layer (il confine
// causale che la sezione 6 richiede di preservare).
class PredictedProtectPolicy final : public RetentionPolicy {
public:
    PredictedProtectPolicy(const AgedFrequencyParams & base, double predictor_decay,
                            uint32_t top_k, uint32_t n_experts);
    RetentionStats stats_with_id() const;

    // Override di sola osservazione di RetentionPolicy::protected_for: vedi
    // il commento sulla dichiarazione base. Legge predicted_next_, non lo
    // muta, non viene chiamata da choose_victim() o da after_event().
    std::vector<uint32_t> protected_for(uint32_t layer) const override;

protected:
    Obj choose_victim(uint32_t layer_of_current_access) const override;
    void after_event(uint32_t step, uint32_t layer,
                      const std::vector<uint32_t> & served_sorted) override;

private:
    double   predictor_decay_;
    uint32_t top_k_;
    uint32_t n_experts_;
    uint64_t predictor_decisions_ = 0;
    mutable uint64_t protected_fallbacks_ = 0;

    // Stato del predittore decayed_transition, PER LAYER. Rispecchia
    // DecayedTransitionPredictor._state: (c,e) -> (valore decaduto, ultimo
    // step aggiornato), con decadimento pigro "telescopico" applicato alla
    // lettura, esattamente come il riferimento Python (decay_factor **
    // (i - last)), mai una riscalatura piena della matrice.
    struct TransKey { uint32_t c, e; bool operator==(const TransKey&o)const{return c==o.c&&e==o.e;} };
    struct TransKeyHash { size_t operator()(const TransKey&k)const{return ((size_t)k.c<<32)^(size_t)k.e;} };
    struct PerLayerPredictor {
        std::vector<std::vector<uint32_t>> history; // sorted expert-set per posizione, in ordine di osservazione
        std::unordered_map<TransKey, std::pair<double,int64_t>, TransKeyHash> state; // (valore, ultimo i aggiornato)
    };
    std::unordered_map<uint32_t, PerLayerPredictor> predictors_; // per layer
    std::unordered_map<uint32_t, std::vector<uint32_t>> predicted_next_; // layer -> expert id protetti (ordinati)

    std::vector<uint32_t> predict_for_layer(uint32_t layer) const;
};

}  // namespace argine_retention

#endif  // ARGINE_RETENTION_POLICY_H
