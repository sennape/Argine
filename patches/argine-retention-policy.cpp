#include "argine-retention-policy.h"

#include <algorithm>
#include <cmath>
#include <cstdlib>
#include <set>

namespace argine_retention {

// ---------------------------------------------------------------------------
// parsing / validazione (fail-closed)
// ---------------------------------------------------------------------------

namespace {

bool getenv_str(const char * name, std::string & out) {
    const char * v = std::getenv(name);
    if (!v || !*v) { return false; }
    out = v;
    return true;
}

bool parse_double(const std::string & s, double & out) {
    try {
        size_t pos = 0;
        out = std::stod(s, &pos);
        return pos == s.size();
    } catch (...) { return false; }
}

bool parse_u32(const std::string & s, uint32_t & out) {
    try {
        size_t pos = 0;
        long v = std::stol(s, &pos);
        if (pos != s.size() || v < 0) { return false; }
        out = (uint32_t) v;
        return true;
    } catch (...) { return false; }
}

bool decay_in_grid(double d) {
    for (double g : DECAYED_TRANSITION_GRID) {
        if (d == g) { return true; }
    }
    return false;
}

bool matches_frozen_b_selected(double decay, uint32_t interval) {
    const AgedFrequencyParams * all[3] = {
        &FROZEN_B_SELECTED_1_5_GIB, &FROZEN_B_SELECTED_4_GIB, &FROZEN_B_SELECTED_8_GIB
    };
    for (auto * p : all) {
        if (p->decay_factor == decay && p->aging_interval_steps == interval) { return true; }
    }
    return false;
}

}  // namespace

RetentionConfig make_retention_config(
    const std::string & policy_id,
    double decay_factor,
    uint32_t aging_interval_steps,
    double predictor_decay,
    uint32_t top_k,
    uint32_t n_experts) {
    RetentionConfig cfg;
    if (policy_id.empty()) {
        cfg.kind = PolicyKind::NONE;
        return cfg;
    }
    if (!matches_frozen_b_selected(decay_factor, aging_interval_steps)) {
        cfg.kind = PolicyKind::INVALID;
        cfg.rejection_reason =
            "aged_frequency (decay=" + std::to_string(decay_factor) +
            ",interval=" + std::to_string(aging_interval_steps) + ") is not one of the three "
            "frozen B_selected(M) pairs (0.999,1) / (0.99,64) / (0.999,16); refusing to retune "
            "or improvise a new baseline configuration.";
        return cfg;
    }
    cfg.base.decay_factor = decay_factor;
    cfg.base.aging_interval_steps = aging_interval_steps;

    if (policy_id == "b_selected") {
        cfg.kind = PolicyKind::B_SELECTED;
        return cfg;
    }
    if (policy_id == "predicted_protect") {
        if (!decay_in_grid(predictor_decay)) {
            cfg.kind = PolicyKind::INVALID;
            cfg.rejection_reason =
                "predictor decay=" + std::to_string(predictor_decay) +
                " is not in the frozen decayed_transition grid {0.9,0.99,0.999}.";
            return cfg;
        }
        if (predictor_decay != 0.99) {
            // R2-12 froze the predictor at decay=0.99 specifically (contract.json
            // frozen_predictor). A different in-grid value would still be a valid
            // R2-10 predictor but NOT the R2-12 frozen one -- reject rather than
            // silently accept a scientifically-different configuration.
            cfg.kind = PolicyKind::INVALID;
            cfg.rejection_reason =
                "predictor decay=" + std::to_string(predictor_decay) +
                " is in the general decayed_transition grid but is not R2-12's frozen "
                "value (0.99); refusing to substitute a different predictor configuration.";
            return cfg;
        }
        if (top_k == 0 || n_experts == 0 || top_k > n_experts) {
            cfg.kind = PolicyKind::INVALID;
            cfg.rejection_reason =
                "predicted_protect requires explicit, valid top_k and n_experts "
                "(got top_k=" + std::to_string(top_k) + ", n_experts=" + std::to_string(n_experts) +
                "); refusing to infer or default these.";
            return cfg;
        }
        cfg.predictor_decay = predictor_decay;
        cfg.top_k = top_k;
        cfg.n_experts = n_experts;
        cfg.kind = PolicyKind::PREDICTED_PROTECT;
        return cfg;
    }
    cfg.kind = PolicyKind::INVALID;
    cfg.rejection_reason = "unrecognized ARGINE_RETENTION_POLICY identifier '" + policy_id +
        "'; known identifiers are 'b_selected' and 'predicted_protect'. Fail-closed: no "
        "guessed policy is substituted.";
    return cfg;
}

RetentionConfig parse_retention_config_from_env() {
    std::string policy_id;
    if (!getenv_str("ARGINE_RETENTION_POLICY", policy_id)) {
        RetentionConfig cfg;
        cfg.kind = PolicyKind::NONE;
        return cfg;  // not set at all -- the one case that is NOT an error
    }

    std::string decay_s, interval_s, pred_decay_s, topk_s, nexp_s;
    double decay = 0.0, pred_decay = 0.0;
    uint32_t interval = 0, top_k = 0, n_experts = 0;

    if (!getenv_str("ARGINE_RETENTION_DECAY", decay_s) || !parse_double(decay_s, decay)) {
        RetentionConfig cfg; cfg.kind = PolicyKind::INVALID;
        cfg.rejection_reason = "ARGINE_RETENTION_POLICY is set but ARGINE_RETENTION_DECAY is "
            "missing or not a valid number.";
        return cfg;
    }
    if (!getenv_str("ARGINE_RETENTION_INTERVAL", interval_s) || !parse_u32(interval_s, interval)) {
        RetentionConfig cfg; cfg.kind = PolicyKind::INVALID;
        cfg.rejection_reason = "ARGINE_RETENTION_POLICY is set but ARGINE_RETENTION_INTERVAL is "
            "missing or not a valid non-negative integer.";
        return cfg;
    }
    if (policy_id == "predicted_protect") {
        if (!getenv_str("ARGINE_RETENTION_PREDICTOR_DECAY", pred_decay_s) ||
            !parse_double(pred_decay_s, pred_decay)) {
            RetentionConfig cfg; cfg.kind = PolicyKind::INVALID;
            cfg.rejection_reason = "ARGINE_RETENTION_POLICY=predicted_protect requires "
                "ARGINE_RETENTION_PREDICTOR_DECAY, missing or invalid.";
            return cfg;
        }
        if (!getenv_str("ARGINE_RETENTION_TOPK", topk_s) || !parse_u32(topk_s, top_k)) {
            RetentionConfig cfg; cfg.kind = PolicyKind::INVALID;
            cfg.rejection_reason = "ARGINE_RETENTION_POLICY=predicted_protect requires "
                "ARGINE_RETENTION_TOPK, missing or invalid.";
            return cfg;
        }
        if (!getenv_str("ARGINE_RETENTION_NEXPERTS", nexp_s) || !parse_u32(nexp_s, n_experts)) {
            RetentionConfig cfg; cfg.kind = PolicyKind::INVALID;
            cfg.rejection_reason = "ARGINE_RETENTION_POLICY=predicted_protect requires "
                "ARGINE_RETENTION_NEXPERTS, missing or invalid.";
            return cfg;
        }
    }
    return make_retention_config(policy_id, decay, interval, pred_decay, top_k, n_experts);
}

// ---------------------------------------------------------------------------
// RetentionPolicy -- nucleo condiviso (identico ad AgedFrequency)
// ---------------------------------------------------------------------------

RetentionPolicy::RetentionPolicy(double decay_factor, uint32_t aging_interval_steps)
    : decay_factor_(decay_factor), aging_interval_steps_(aging_interval_steps) {}

void RetentionPolicy::start(uint64_t budget_bytes) {
    M_ = budget_bytes;
    resident_bytes_ = 0;
    t_ = 0;
    resident_.clear();
    hits_ = misses_ = evictions_ = 0;
    overflow_events_ = overflow_misses_ = 0;
}

RetentionPolicy::ObjState * RetentionPolicy::find_resident(const Obj & o) {
    for (auto & kv : resident_) { if (kv.first == o) { return &kv.second; } }
    return nullptr;
}
const RetentionPolicy::ObjState * RetentionPolicy::find_resident(const Obj & o) const {
    for (auto & kv : resident_) { if (kv.first == o) { return &kv.second; } }
    return nullptr;
}
void RetentionPolicy::erase_resident(const Obj & o) {
    for (auto it = resident_.begin(); it != resident_.end(); ++it) {
        if (it->first == o) { resident_.erase(it); return; }
    }
}

void RetentionPolicy::apply_aging_if_due() {
    // Python: "every aging_interval_steps global causal access ordinals,
    // apply decay to all tracked resident objects" -- checked as
    // self._t % self.aging_interval_steps == 0, BEFORE processing the
    // current access (self._t is incremented first, then this check runs).
    if (aging_interval_steps_ > 0 && (t_ % aging_interval_steps_) == 0) {
        for (auto & kv : resident_) {
            kv.second.score *= decay_factor_;
        }
    }
}

Obj RetentionPolicy::choose_victim(uint32_t /*layer_of_current_access*/) const {
    // AgedFrequency: lowest score; tie-break least-recent last_ref; final
    // tie-break stable (layer, expert_id) ascending. Exactly
    // policies.py:_choose_victim's `key = (score, last_ref, stable_obj_key)`.
    const Obj * best = nullptr;
    const ObjState * best_state = nullptr;
    for (auto & kv : resident_) {
        if (best == nullptr) { best = &kv.first; best_state = &kv.second; continue; }
        auto better = [&](const ObjState & a, const Obj & ao, const ObjState & b, const Obj & bo) {
            if (a.score != b.score) { return a.score < b.score; }
            if (a.last_ref != b.last_ref) { return a.last_ref < b.last_ref; }
            if (ao.layer != bo.layer) { return ao.layer < bo.layer; }
            return ao.expert < bo.expert;
        };
        if (better(kv.second, kv.first, *best_state, *best)) {
            best = &kv.first; best_state = &kv.second;
        }
    }
    return *best;  // caller guarantees resident_ is non-empty
}

bool RetentionPolicy::evict_one(uint32_t layer_of_current_access) {
    if (resident_.empty()) { return false; }
    Obj v = choose_victim(layer_of_current_access);
    const ObjState * st = find_resident(v);
    resident_bytes_ -= st->bytes;
    erase_resident(v);
    evictions_++;
    return true;
}

EventOutcome RetentionPolicy::access(uint32_t step, uint32_t layer,
                                      const std::vector<uint32_t> & experts_in,
                                      uint64_t bytes_of_layer) {
    EventOutcome out;
    // Python: Policy.access() dedups+sorts (`sorted(set(experts))`) before
    // calling _access(); AgedFrequency/_BaseBridgePolicy then process in
    // that exact order (`for e in sorted(int(x) for x in experts)`).
    std::set<uint32_t> uniq(experts_in.begin(), experts_in.end());
    std::vector<uint32_t> served(uniq.begin(), uniq.end());
    std::vector<Obj> evicted_this_event;

    for (uint32_t e : served) {
        t_ += 1;
        apply_aging_if_due();
        Obj obj{layer, e};
        ObjState * existing = find_resident(obj);
        if (existing == nullptr) {
            out.missed.push_back(e);
            misses_++;
            double prev = 0.0;  // Python: prev = scores.get(obj,0.0) if obj in resident else 0.0
            // "nessun membro dell'evento corrente viene sfrattato per farne
            // spazio ad un altro membro dello STESSO evento" -- i membri
            // gia' ammessi in QUESTO stesso evento sono protetti perche'
            // sono gia' in resident_ (appena inseriti) e quindi non sono
            // mai scelti due volte come stesso obj; la vittima scelta da
            // evict_one() puo' comunque essere un membro appena ammesso in
            // questo stesso evento se lo score lo rende il minimo -- questo
            // E' il comportamento Python esatto (AgedFrequency non protegge
            // i membri dell'evento corrente oltre al fatto che sono
            // appena stati ammessi con score=1.0, last_ref=t_: la
            // protezione "stesso evento" e' una proprieta' EMERGENTE dei
            // punteggi, non una regola separata, in questa famiglia).
            while (resident_bytes_ + bytes_of_layer > M_ && !resident_.empty()) {
                Obj victim = choose_victim(layer);
                const ObjState * vs = find_resident(victim);
                resident_bytes_ -= vs->bytes;
                erase_resident(victim);
                evictions_++;
                evicted_this_event.push_back(victim);
            }
            if (resident_bytes_ + bytes_of_layer <= M_ && bytes_of_layer <= M_) {
                ObjState st{bytes_of_layer, prev + 1.0, (int64_t) t_};
                resident_.push_back({obj, st});
                resident_bytes_ += bytes_of_layer;
            } else {
                out.overflow_events++;
                out.overflow_misses++;
                overflow_events_++;
                overflow_misses_++;
            }
            continue;
        }
        // hit
        hits_++;
        existing->score += 1.0;
        existing->last_ref = (int64_t) t_;
    }

    out.evicted = evicted_this_event;
    out.overflow_events = overflow_events_ > 0 ? out.overflow_events : out.overflow_events;
    after_event(step, layer, served);
    return out;
}

RetentionStats RetentionPolicy::stats() const {
    RetentionStats s;
    s.decay_factor = decay_factor_;
    s.aging_interval_steps = aging_interval_steps_;
    s.hits = hits_;
    s.misses = misses_;
    s.evictions = evictions_;
    s.resident_bytes = resident_bytes_;
    return s;
}

RetentionPolicy::Inspection RetentionPolicy::inspect(uint32_t /*layer_for_protected_query*/) const {
    Inspection insp;
    for (auto & kv : resident_) {
        insp.resident.push_back(kv.first);
        insp.resident_scores.push_back(kv.second.score);
        insp.resident_last_ref.push_back(kv.second.last_ref);
    }
    return insp;
}

void RetentionPolicy::after_event(uint32_t, uint32_t, const std::vector<uint32_t> &) {
    // AgedFrequency: no-op. (Python: `def end_step(self, step): return None`
    // and _BaseBridgePolicy's extra hook is specific to the predictor
    // subclass; base AgedFrequency has nothing to do here.)
}

RetentionStats BSelectedPolicy::stats_with_id() const {
    RetentionStats s = stats();
    s.policy_id = "b_selected";
    return s;
}

// ---------------------------------------------------------------------------
// PredictedProtectPolicy
// ---------------------------------------------------------------------------

PredictedProtectPolicy::PredictedProtectPolicy(const AgedFrequencyParams & base,
                                                double predictor_decay,
                                                uint32_t top_k, uint32_t n_experts)
    : RetentionPolicy(base.decay_factor, base.aging_interval_steps),
      predictor_decay_(predictor_decay), top_k_(top_k), n_experts_(n_experts) {}

RetentionStats PredictedProtectPolicy::stats_with_id() const {
    RetentionStats s = stats();
    s.policy_id = "predicted_protect";
    s.predictor_decay = predictor_decay_;
    s.predictor_decisions = predictor_decisions_;
    s.protected_fallbacks = protected_fallbacks_;
    return s;
}

Obj PredictedProtectPolicy::choose_victim(uint32_t layer_of_current_access) const {
    // Python _choose_victim: candidates = resident objects that are NOT
    // (same layer AND expert in protected); pool = candidates if non-empty
    // else ALL resident (total fallback, counted here as
    // protected_fallbacks_); min by the SAME (score,last_ref,stable_key)
    // key AgedFrequency uses.
    auto it = predicted_next_.find(layer_of_current_access);
    const std::vector<uint32_t> * protected_experts =
        (it == predicted_next_.end()) ? nullptr : &it->second;

    auto is_protected = [&](const Obj & o) {
        if (protected_experts == nullptr || o.layer != layer_of_current_access) { return false; }
        for (uint32_t e : *protected_experts) { if (e == o.expert) { return true; } }
        return false;
    };

    bool any_candidate = false;
    for (auto & kv : resident_) { if (!is_protected(kv.first)) { any_candidate = true; break; } }
    if (!any_candidate) { protected_fallbacks_++; }

    const Obj * best = nullptr;
    const ObjState * best_state = nullptr;
    for (auto & kv : resident_) {
        if (any_candidate && is_protected(kv.first)) { continue; }
        if (best == nullptr) { best = &kv.first; best_state = &kv.second; continue; }
        auto better = [&](const ObjState & a, const Obj & ao, const ObjState & b, const Obj & bo) {
            if (a.score != b.score) { return a.score < b.score; }
            if (a.last_ref != b.last_ref) { return a.last_ref < b.last_ref; }
            if (ao.layer != bo.layer) { return ao.layer < bo.layer; }
            return ao.expert < bo.expert;
        };
        if (better(kv.second, kv.first, *best_state, *best)) { best = &kv.first; best_state = &kv.second; }
    }
    return *best;
}

std::vector<uint32_t> PredictedProtectPolicy::predict_for_layer(uint32_t layer) const {
    // DecayedTransitionPredictor.predict(): score[e] = sum over c in
    // current_set of decayed_val(c,e) read at position i (lazy telescoping
    // decay applied at read time, exactly as the Python reference does in
    // _decayed_transition_counts / predict). apply_output_contract:
    // sort by (-score, expert_id) ascending, take first k.
    auto pit = predictors_.find(layer);
    if (pit == predictors_.end() || pit->second.history.empty()) {
        // Cold start: no history yet for this layer. Python's CausalContext
        // always has at least one entry (the current observation) by the
        // time predict() is called, because _refresh_prediction_after_observing
        // appends BEFORE calling predict(); mirrored in after_event below.
        return {};
    }
    const auto & hist = pit->second.history;
    const int64_t i = (int64_t) hist.size() - 1;
    const auto & current_set = hist[(size_t) i];

    std::vector<double> scores(n_experts_, 0.0);
    for (uint32_t c : current_set) {
        for (uint32_t e = 0; e < n_experts_; e++) {
            auto key_it = pit->second.state.find(TransKey{c, e});
            if (key_it == pit->second.state.end()) { continue; }
            double val = key_it->second.first;
            int64_t last = key_it->second.second;
            scores[e] += val * std::pow(predictor_decay_, (double) (i - last));
        }
    }
    uint32_t k = top_k_;
    if (k == 0 || k > n_experts_) { return {}; }  // should never happen; config validated at construction
    std::vector<uint32_t> ranked(n_experts_);
    for (uint32_t e = 0; e < n_experts_; e++) { ranked[e] = e; }
    std::stable_sort(ranked.begin(), ranked.end(), [&](uint32_t a, uint32_t b) {
        if (scores[a] != scores[b]) { return scores[a] > scores[b]; }
        return a < b;
    });
    return std::vector<uint32_t>(ranked.begin(), ranked.begin() + k);
}

void PredictedProtectPolicy::after_event(uint32_t step, uint32_t layer,
                                          const std::vector<uint32_t> & served_sorted) {
    (void) step;
    auto & pred = predictors_[layer];
    // Append this observation to the layer's own strictly-increasing history
    // BEFORE updating transition state / predicting -- matches
    // _refresh_prediction_after_observing's history.append(...) followed by
    // building CausalContext(position=len(hist)-1, ...).
    pred.history.push_back(served_sorted);
    const int64_t i = (int64_t) pred.history.size() - 1;
    if (i >= 1) {
        const auto & src = pred.history[(size_t) (i - 1)];
        const auto & tgt = pred.history[(size_t) i];
        for (uint32_t c : src) {
            for (uint32_t e : tgt) {
                TransKey key{c, e};
                auto it = pred.state.find(key);
                double val = 0.0; int64_t last = 0;
                if (it != pred.state.end()) { val = it->second.first; last = it->second.second; }
                val = val * std::pow(predictor_decay_, (double) (i - last)) + 1.0;
                pred.state[key] = {val, i};
            }
        }
    }
    predicted_next_[layer] = predict_for_layer(layer);
    predictor_decisions_++;
}

}  // namespace argine_retention
