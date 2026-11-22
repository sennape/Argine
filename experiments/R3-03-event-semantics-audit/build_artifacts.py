"""Assemble AUDIT.json from per-budget raw analyses (read-only; no model).

Usage:
    python build_artifacts.py <dir-with-audit_B15.json audit_B4.json audit_B8.json>
Raw analyses come from tools/r3_event_semantics_audit.py (see README.md).
"""
from __future__ import annotations

import hashlib
import json
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
REPO = HERE.parents[1]
ANALYSIS = json.loads((REPO / "experiments/R3-02-phase2-mechanism/ANALYSIS.json").read_text(encoding="utf-8"))
ARCHIVE_DIR = REPO.parent / "Argine4-r3runs/R3-02-p4/archive"

BUDGETS = {"1.5GiB": ("B15", "1.5", 0.999, 1), "4GiB": ("B4", "4", 0.99, 64), "8GiB": ("B8", "8", 0.999, 16)}
HEAD = "fd42d77546e44be342436846f7688744cb5ffa40"


def sha(p: Path) -> str:
    return hashlib.sha256(p.read_bytes()).hexdigest()


def main(raw_dir: str) -> None:
    raw = Path(raw_dir)
    ev, ident, miss, cons, sim, trace_id = {}, {}, {}, None, {}, {}
    for name, (tag, _b, _d, _i) in BUDGETS.items():
        d = json.loads((raw / f"audit_{tag}.json").read_text(encoding="utf-8"))
        (HERE / f"AUDIT_RAW_{name}.json").write_text(json.dumps(d, indent=1, sort_keys=True) + "\n", encoding="utf-8")
        ev[name], ident[name], miss[name], sim[name] = d["event_counts"], d["set_identity"], d["miss_position"], d["simulation"]
        cons = d["r2_consecutive_event_identity"]
        a = ARCHIVE_DIR / f"archive-B-{name}.json"
        if a.exists():
            j = json.loads(a.read_text(encoding="utf-8"))
            trace_id[name] = {"archive_sha256": j["archive_sha256"], "logical_sha256": j["logical_sha256"],
                              "logical_bytes": j["logical_bytes"]}

    # exploratory cost estimate from FROZEN R3-02 timing (never from R3-02G Session 1)
    cost = {}
    for name, (_t, b, decay, interval) in BUDGETS.items():
        w = ANALYSIS["timing"][b]["wall"]
        calls_tok = ev[name]["decode_calls_per_decode_pass"]
        per_call = w["median"] / calls_tok
        cost[name] = {
            "median_sham_overhead_s_per_token": w["median"],
            "median_A_step_time_s_per_token": w["median_a"],
            "decode_policy_calls_per_token": calls_tok,
            "approx_cost_per_call_us": per_call * 1e6,
            "approx_bookkeeping_per_token_ms": w["median"] * 1e3,
            "if_one_call_per_layer_per_token_calls": calls_tok / 3,
            "approx_bookkeeping_per_token_ms_at_1_call_if_linear": w["median"] * 1e3 / 3,
            "max_reduction_if_linear_ms_per_token": w["median"] * 1e3 * 2 / 3,
            "max_reduction_fraction_if_linear": 2 / 3,
        }
    # base-policy (b_selected) aging cadence: t_ advances per expert touched per call
    base = {}
    for name, (_t, b, decay, interval) in BUDGETS.items():
        t2, t3 = 48 * 8, 48 * 8 * 3
        base[name] = {
            "aging_interval_touches": interval, "score_decay_factor": decay,
            "touches_per_decode_token_R2": t2, "touches_per_decode_token_R3": t3,
            "aging_ticks_per_decode_token_R2": t2 / interval, "aging_ticks_per_decode_token_R3": t3 / interval,
            "score_decay_per_decode_token_R2": decay ** (t2 / interval),
            "score_decay_per_decode_token_R3": decay ** (t3 / interval),
            "score_increment_per_expert_use_R2": 1, "score_increment_per_expert_use_R3": 3,
        }

    audit = {
        "artifact": "R3-EVENT-SEMANTICS-AUDIT",
        "classification": "R2_R3_EVENT_SEMANTICS_NOT_EQUIVALENT",
        "head_before_task": HEAD,
        "no_model_loaded": True,
        "no_r3_02g_session1_timing_read": True,
        "inputs": {"frozen_r3_02_traces": trace_id, "runtime_sha256": "3f094f5b5f4963f488b63722f365068f09e2aa3b12e2096969da99f471bbd644",
                   "workload": "r2-11-coding-v1 (DEV), seed 42, n_predict 512",
                   "trace_files_used": "B (predicted_protect_sham) traces; only they log protected_before",
                   "tool": "tools/r3_event_semantics_audit.py"},
        "r2_event_definition": {
            "one_event_is": "the sorted UNION of experts routed in ONE forward-pass step for ONE layer (one token when decoding)",
            "entering_predictor": "(step_index, layer, expert_set) from Trace.events()",
            "per_token": "48 events (one per MoE layer) per decode step",
            "per_layer_per_token": 1,
            "gate_up_down": "NOT separate events; the router decides once per layer and the legacy converter keeps only gate rows (up/down repeat the ids)",
            "history_updated": "after the event has been served (_refresh_prediction_after_observing, retention_policies.py:104, called at :167)",
            "transition_counts_updated": "in the same call, from the previous same-layer event to this one (DecayedTransitionPredictor._decayed_state)",
            "prediction_computed": "in the same call, after history/transition update; consulted only at the NEXT same-layer access",
            "previous_event": "the same layer's previous served step",
            "identical_consecutive_sets": {"possible": True, "measured_fraction_decode_adjacent": cons["fraction_identical_sets"],
                                           "measured_mean_jaccard": cons["mean_jaccard"], "decode_adjacent_pairs": cons["decode_adjacent_pairs"]},
            "base_policy_clock": "_t increments once per expert touched per event; aging when _t % interval == 0",
            "sources": ["argine/research/trace.py:188 Trace.events", "argine/research2/r2_bridge01/dev_runner.py:189,216 run_fast_bridge -> policy.access(i, l, ex)",
                        "argine/research2/r2_bridge01/retention_policies.py:141-168", "argine/research2/r2_10/predictors.py:142 DecayedTransitionPredictor",
                        "argine/research/replay.py:95 served_steps (v2: every step incl. prompt)"],
        },
        "r3_runtime_call_definition": {
            "one_call_is": "one psx_cache_ensure_many() call = one MoE GGML_OP_MUL_MAT_ID NODE (gate OR up OR down tensor of a layer), carrying the union of experts of that node for all three kinds",
            "calls_per_layer_per_forward_pass": 3,
            "calls_per_decode_token": ev["4GiB"]["decode_calls_per_decode_pass"],
            "each_call_does": ["policy->access() (argine-store.cpp:1633) with step = policy_step++ (:1607)",
                               "RetentionPolicy::access advances t_ per expert, scores, hits/misses, evictions",
                               "after_event() (argine-retention-policy.cpp:318) -> history.push_back, transition update, predict_for_layer, predictor_decisions_++"],
            "hook_level": "EXPERT-TENSOR ACCESS (one per MUL_MAT_ID node), not expert-selection (one per layer per forward pass)",
            "contrast": "the R2 router-trace logger in the same function is guarded by (arx_graph, layer) and fires once per layer per pass (argine-experts.cpp:953); the policy call is not",
        },
        "event_counts": ev,
        "set_identity": ident,
        "miss_position": miss,
        "r2_consecutive_event_identity": cons,
        "simulation": sim,
        "predictor_state_effect": {
            "history": "3 entries per layer per pass instead of 1 (measured ratio 3.0)",
            "transition_counts": {"per_decode_token_R3": "3 transition updates: S(t-1)->S(t), S(t)->S(t), S(t)->S(t)", "per_decode_token_R2": "1 transition update: S(t-1)->S(t)",
                                  "measured_cell_update_ratio_4GiB": sim["4GiB"]["state_effect"]["transition_update_ratio"],
                                  "self_transition_mass_fraction_decode": 2 / 3},
            "decay": {"effective_decay_per_token_R2": 0.99, "effective_decay_per_token_R3": 0.99 ** 3,
                      "time_constant_tokens_R2": 100.0, "time_constant_tokens_R3": sim["4GiB"]["state_effect"]["effective_time_constant_tokens_R3"]},
            "protected_set": {"consulted_first_call_identical_to_R2_fraction": sim["4GiB"]["protected_set_consulted_at_first_call_of_next_token"]["fraction_identical_R2_vs_R3"],
                              "mean_jaccard_with_R2": sim["4GiB"]["protected_set_consulted_at_first_call_of_next_token"]["mean_jaccard_R2_vs_R3"]},
            "prediction_quality_recall_at_8": sim["4GiB"]["next_token_recall_at_k"],
            "distinction": {"computational_overhead": "bookkeeping runs 3x per layer-token (~2/3 of it re-processing an identical set)",
                            "semantic_predictor_drift": "the predictor is a different predictor: self-transitions dominate its state, its time constant is ~3x shorter, and calls 2-3 consult a set that already contains the event being served"},
        },
        "base_policy_effect": {"note": "t_/aging/score clock of b_selected is ALSO advanced per call, so A (b_selected) in R3 is not R2's B_selected either; A/B remain mutually consistent",
                               "by_budget": base},
        "first_semantic_divergence": {
            "structural": {"policy_step": 1, "forward_pass": 0, "layer": 0, "call_in_triplet": 2,
                           "what": "second policy->access() for the same (pass, layer) event: R2 defines exactly one event there, so the runtime stream gains an extra event with an identical expert set"},
            "first_comparable_protected_set_difference": sim["4GiB"]["first_protected_set_divergence_at_comparable_event"],
        },
        "root_cause": {
            "file": "patches/argine-experts.cpp", "function": "ExpertTraits::compute_forward (l.868) -> psx_cache_ensure_many (l.1189, thread 0)",
            "node_structure": "ggml executes ffn_gate_exps, ffn_up_exps and ffn_down_exps as three separate MUL_MAT_ID nodes per layer; identifica() (l.135) accepts all three kinds with no filter",
            "why_each_node_triggers_policy": "each node builds the FULL three-kind request (dove[] over all kinds, l.1003) and calls psx_cache_ensure_many; patches/argine-store.cpp:2278 routes every such call to servi_evento_con_politica, which calls policy->access() once (l.1633)",
            "design_assumption_that_failed": "RETENTION-POLICY-INTEGRATION-IMPLEMENTATION.md states 'one access() call per MoE node ... matching the Python reference's event boundary exactly': node was equated with (step, layer) event",
            "intent_vs_effect": "the source comment (l.985-991) intends gate/up/down to load together so the next two nodes hit cache; that holds physically but the policy hook was not made once-per-layer",
            "hook_placed_at": "expert-tensor access level (physical accounting), not expert-selection semantic level",
            "r3_build_identical_to_repo_patches": True,
        },
        "exploratory_cost_estimates": {
            "status": "EXPLORATORY derived estimates from frozen R3-02 TIMING medians; not confirmatory; R3-02G Session 1 not used",
            "by_budget": cost,
            "caveats": ["assumes cost is uniform per call; calls 2-3 touch already-present transition keys (no new hash inserts) and are probably cheaper than call 1, so the 2/3 saving is an UPPER bound",
                        "overhead is the sham minus b_selected wall step time; includes lock hold time on the single shard mutex",
                        "total miss I/O also falls if calls 2-3 stop re-missing (see miss_position), which this estimate does not include"],
        },
        "consequences": {
            "R2-10": "UNAFFECTED. Predictor quality was evaluated offline on the one-observation-per-token same-layer stream (Trace.token_selections); the result stands for the predictor it evaluated.",
            "R2-11": "UNAFFECTED as a replay result: PredictedProtect was driven by one access() per (step, layer). It is not evidence about the runtime-realised policy.",
            "R2-12": "Frozen R2_12_FAIL stands as a statement about the runtime realisation. Its event stream per token was not measured (canonical runs had tracing off); the SAME node-dispatch source (argine-experts.cpp unchanged since R2-12 Phase F) makes a 3x event stream the strong default, so R2-12 is NOT a clean test of the R2-11 policy at runtime. INCONCLUSIVE for causal attribution; not retro-invalidated.",
            "R3-02": "Classification MECHANISM_INSUFFICIENT unchanged. Measurements are valid for the REALISED runtime: A/B trajectories identical, sham overhead real (both arms share the 3x base semantics). But the overhead is the cost of a 3x event stream, and traffic at 4/8 GiB includes misses at calls 2-3 that R2 replay semantics cannot produce.",
        },
        "minimal_correction_architecture": {
            "principle": "separate PHYSICAL expert-access accounting (3 nodes per layer; pins, reads, hits) from POLICY events (1 per forward pass per layer)",
            "design": ["derive a (forward_pass_id, layer) event key inside the policy path without relying on ARGINE_ROUTE tracing (detect pass boundary as layer index decreasing, as arx does)",
                       "call policy->access()/after_event only on the first MoE node of each (pass, layer); the up/down nodes still run the physical ensure (pins, reads) but do not advance t_, scores, history, transitions or policy_step",
                       "carry policy event identity (pass, layer, step) into the trace rows so that rows == events and triplet grouping becomes an assertion, not an inference",
                       "keep sham and predicted_protect behind the same single-event hook so A/B/C stay comparable"],
            "not_implemented": True,
        },
        "required_conformance_before_new_timing": [
            "semantic equivalence: policy rows per run == forward_passes x 48 (ratio exactly 1.0)",
            "predictor-state equivalence: logged protected_before equals the frozen R2 DecayedTransitionPredictor on the routed stream (100%)",
            "trajectory equivalence: runtime A (b_selected) eviction/resident trajectory equals frozen Python B_selected replay of the same routing trace",
            "output-token identity: output_token_digest unchanged vs R3-02 (ac28efbe...)",
            "conformance vs frozen R2 reference on real runtime traces, not only synthetic conformance_traces",
            "sham/b_selected trajectory identity re-established under the corrected hook",
        ],
        "recommended_r3_03_direction": "R3_03_RUNTIME_SEMANTIC_CORRECTION_FIRST",
        "caveats": [
            "single workload (r2-11-coding-v1, DEV), single model, single host; routing is identical across budgets so the three budget analyses are replications of the event structure, not independent workloads",
            "decode-token recall figures come from one trace and are NOT a re-evaluation of R2-10",
            "misses at calls 2-3 are consistent with same-event self-eviction churn (R2-12 Phase F) but that mechanism was inferred from miss/eviction co-occurrence, not replayed",
            "R2-12 binary not inspected; its event multiplicity is inferred from unchanged source",
            "why calls 2-3 never miss at 1.5 GiB is not explained here",
        ],
    }
    (HERE / "AUDIT.json").write_text(json.dumps(audit, indent=1, sort_keys=True) + "\n", encoding="utf-8")
    print("AUDIT.json written")


if __name__ == "__main__":
    main(sys.argv[1])
