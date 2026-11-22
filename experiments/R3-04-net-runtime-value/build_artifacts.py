"""Deterministically (re)builds SCHEDULE.json, SAMPLE_SIZE.json, BUDGET_SELECTION.json, PROTOCOL.json from frozen parent evidence + validate_protocol.
FREEZE.json is built by `python build_artifacts.py --freeze` once the prose files exist. Reads parent evidence read-only; runs no model."""
import json, statistics as st, sys
from pathlib import Path
HERE = Path(__file__).resolve().parent; REPO = HERE.parents[1]
sys.path.insert(0, str(HERE))
import validate_protocol as V  # noqa: E402

def jload(p): return json.loads((REPO / p).read_text(encoding="utf-8"))
def put(name, obj): (HERE / name).write_text(json.dumps(obj, indent=1, sort_keys=True) + "\n", encoding="utf-8")

PARENT_FILES = {
    "r2_10_selection_record": "experiments/R2-10-working-set-predictor/PHASE2-SELECTION-RECORD.json",
    "r2_bridge_final_interpretation": "experiments/R2-BRIDGE-01-retention-feasibility/BRIDGE_FINAL_INTERPRETATION.json",
    "r2_11_per_budget_comparison": "experiments/R2-11-predictive-retention/PER-BUDGET-COMPARISON.json",
    "r2_11_final_interpretation": "experiments/R2-11-predictive-retention/FINAL-INTERPRETATION.json",
    "r2_12_final_closure": "experiments/R2-12-runtime-validation/R2_12_FINAL_CLOSURE.json",
    "r2_12_4gib_analysis": "experiments/R2-12-runtime-validation/R2_12_4GIB_PRIMARY_FORMAL_ANALYSIS.json",
    "r2_12_1_5gib_analysis": "experiments/R2-12-runtime-validation/R2_12_1_5GIB_SECONDARY_FORMAL_ANALYSIS.json",
    "r2_12_8gib_analysis": "experiments/R2-12-runtime-validation/R2_12_8GIB_SECONDARY_FORMAL_ANALYSIS.json",
    "r3_03_event_semantics_freeze": "experiments/R3-03-event-semantics-audit/FREEZE.json",
    "r3_03a_correction_freeze": "experiments/R3-03A-runtime-semantic-correction/FREEZE.json",
    "r3_03a_conformance_freeze": "experiments/R3-03A-runtime-semantic-conformance/FREEZE.json",
    "r3_03b_microbench_freeze": "experiments/R3-03B-microbench-evidence/FREEZE.json",
    "r3_03b_conformance_freeze": "experiments/R3-03B-instrumented-conformance-evidence/FREEZE.json",
    "r3_03b_e2e_freeze": "experiments/R3-03B-end-to-end-opening/E2E_FREEZE.json",
    "r3_03b_e2e_amendment_1": "experiments/R3-03B-end-to-end-opening/E2E_FREEZE_AMENDMENT_1.json",
    "r3_03b_final_freeze": "experiments/R3-03B-final-evidence/FREEZE.json",
    "r3_03b_final_result": "experiments/R3-03B-final-evidence/FINAL_RESULT.json",
    "r3_03b_final_validator": "experiments/R3-03B-final-evidence/validate_final.py",
    "r3_02_acquisition_plan": "experiments/R3-02-calibration-mechanism/ACQUISITION_PLAN.json",
    "r3_02a_sham_equivalence": "experiments/R3-02A-sham-control/EQUIVALENCE.json",
}

def parents():
    out = {}
    for k, p in PARENT_FILES.items():
        f = REPO / p
        out[k] = {"path": p, "sha256": V.sha_file(f), "sha256_lf": V.sha_lf(f)}
    out["r3_03b_final_freeze"]["freeze_sha256_field"] = jload(PARENT_FILES["r3_03b_final_freeze"])["freeze_sha256"]
    return out

def planning_inputs():
    r212 = {}
    for b, k in (("1.5", "r2_12_1_5gib_analysis"), ("4", "r2_12_4gib_analysis"), ("8", "r2_12_8gib_analysis")):
        d = jload(PARENT_FILES[k]); o = d.get("official_primary_statistics") or d["official_secondary_statistics"]
        B, P, D = [], [], []
        for w, prs in o["per_workload_pairs"].items():
            for p in prs:
                bb, pp = 1000 / p["b_selected_tps"], 1000 / p["predicted_protect_tps"]
                B.append(bb); P.append(pp); D.append(pp - bb)
        r212[b] = {"n_pairs": len(D), "mean_b_selected_ms_per_token": st.fmean(B), "mean_predicted_protect_ms_per_token": st.fmean(P),
                   "mean_delta_ms_per_token_OLD_RUNTIME_NOT_REUSED_AS_OUTCOME": st.fmean(D), "sd_paired_delta_ms_per_token": st.stdev(D)}
    fin = jload(PARENT_FILES["r3_03b_final_result"])
    blocks = json.loads((REPO / "experiments/R3-03B-final-evidence/BLOCK_RECOMPUTATION.json").read_text())["blocks"]
    r3 = {"block_contrast_sd_O_ms_per_token_8GiB": st.stdev([b["O_ms_per_token"] for b in blocks]),
          "mean_wall_b_selected_8GiB_ms_per_token": fin["performance"]["mean_wall_ms_per_token"]["b_selected"],
          "mean_wall_sham_8GiB_ms_per_token": fin["performance"]["mean_wall_ms_per_token"]["predicted_protect_sham"],
          "corrected_overhead_ms_per_token": fin["performance"]["corrected_overhead_ms_per_token"],
          "mean_policy_ms_per_token": fin["performance"]["mean_policy_ms_per_token"]}
    return r212, r3

def replay_bounds(r212):
    """Upper bound on realisable wall benefit under the (generous) assumption that wall time is proportional to replay miss bytes."""
    cmp_ = jload(PARENT_FILES["r2_11_per_budget_comparison"])
    import glob
    rel = {}
    for b, key in (("1.5", "mean_delta_1_5gib"), ("4", "mean_delta_4gib_primary"), ("8", "mean_delta_8gib")):
        files = [f for f in glob.glob(str(REPO / "experiments/R2-11-predictive-retention/eval_cells/*.json"))]
        vals = {}
        for f in files:
            d = json.loads(Path(f).read_text()); r = d["result"]
            if str(d["budget_gib"]).rstrip("0").rstrip(".") == b or float(d["budget_gib"]) == float(b):
                vals.setdefault(d["policy_id"], []).append(r["bytes_missed"] / r["steps"])
        mb, mp = st.fmean(vals["b_selected"]), st.fmean(vals["predicted_protect"])
        rel[b] = {"replay_B_miss_bytes_per_step_mean": mb, "replay_P_miss_bytes_per_step_mean": mp, "replay_mean_delta_bytes_per_step": mp - mb,
                  "relative_reduction": (mb - mp) / mb, "prior_delta_artifact_value": cmp_[key]}
    wall = {b: r212[b]["mean_b_selected_ms_per_token"] for b in rel}
    for b in rel:
        rel[b]["max_wall_benefit_ms_per_token_if_wall_proportional_to_miss_bytes"] = rel[b]["relative_reduction"] * wall[b]
        rel[b]["as_fraction_of_delta_cost_ref"] = rel[b]["max_wall_benefit_ms_per_token_if_wall_proportional_to_miss_bytes"] / V.DELTA_COST_REF_MS
    return rel

def main(freeze=False):
    r212, r3 = planning_inputs()
    wall = {"1.5": r212["1.5"]["mean_b_selected_ms_per_token"], "4": r212["4"]["mean_b_selected_ms_per_token"], "8": r3["mean_wall_b_selected_8GiB_ms_per_token"]}
    bounds = replay_bounds(r212)
    sched = V.timing_schedule(); msched = V.mechanism_schedule()
    put("SCHEDULE.json", {"artifact": "R3-04-SCHEDULE", "rule": "block = permutation pi of {A,S,P} followed by reverse(pi) (2 launches per arm per block, mean position 3.5 for every arm in every block); block b uses permutation PERMS[(b-1) mod 6]",
                          "permutations": V.PERMS, "arms": V.ARMS, "budget_order": V.BUDGET_ORDER, "n_blocks": {b: V.BUDGETS[b]["n_blocks"] for b in V.BUDGET_ORDER},
                          "warmups_per_budget": V.WARMUPS_PER_BUDGET, "cooldowns_s": {"within_block": V.COOLDOWN_WITHIN_BLOCK_S, "before_first_launch_of_each_block_and_warmup": V.COOLDOWN_BETWEEN_BLOCKS_S},
                          "timing_schedule": sched, "timing_schedule_sha256": V.schedule_sha(sched), "mechanism_schedule": msched, "mechanism_schedule_sha256": V.schedule_sha(msched),
                          "balance": V.balance_report(sched), "counted_timing_launches": sum(1 for x in sched if x["kind"] == "counted"), "warmup_launches": sum(1 for x in sched if x["kind"] == "warmup"),
                          "mechanism_launches": len(msched), "total_model_bearing_launches": len(sched) + len(msched)})
    hw = V.precision_target_ms()
    ss = {"artifact": "R3-04-SAMPLE-SIZE", "design": "6-launch palindromic blocks; unit of analysis = block-level contrast of 2-launch arm means; t interval, df = N_blocks - 1",
          "planning_scale": {"delta_cost_ref_ms_per_token": V.DELTA_COST_REF_MS, "source": "R3-03B corrected sham overhead (planning scale only; never an R3-04 observation)", "precision_fraction": V.PRECISION_FRACTION, "precision_target_half_width_ms": hw,
                             "meaning": "primary CI half-width <= one third of the measured bookkeeping cost, i.e. the design can tell 'recovers >= ~1/3 of its cost' from 'recovers nothing' and can bound near-break-even"},
          "variance_sources_used": {"allowed": "R2-12 paired-delta SD per budget (old runtime, non-contemporaneous, used ONLY as a conservative planning SD for a block contrast; conservative because a block contrast averages 2 launches per arm); R3-03B 8 GiB block-contrast SD (corrected runtime) as a conservativeness check",
                                    "not_allowed": "R2-12 / R3-03B outcome means as R3-04 observations"},
          "r2_12_proxy": r212, "r3_03b": r3,
          "proxy_conservativeness_check_8GiB": {"r2_12_proxy_sd": r212["8"]["sd_paired_delta_ms_per_token"], "r3_03b_block_sd": r3["block_contrast_sd_O_ms_per_token_8GiB"], "ratio": r212["8"]["sd_paired_delta_ms_per_token"] / r3["block_contrast_sd_O_ms_per_token_8GiB"]},
          "replay_implied_retention_benefit_bounds": bounds, "per_budget": {}}
    expected_net = {b: V.DELTA_COST_REF_MS - bounds[b]["max_wall_benefit_ms_per_token_if_wall_proportional_to_miss_bytes"] for b in V.BUDGET_ORDER}
    for b in V.BUDGET_ORDER:
        sd = r212[b]["sd_paired_delta_ms_per_token"]; n = V.BUDGETS[b]["n_blocks"]
        cand = {}
        for nn in (6, 12, 18, 24, 30):
            cand[str(nn)] = {"launches_counted": nn * 6, "half_width_ms": V.t975(nn - 1) * sd / (nn ** 0.5), "power_to_exclude_zero_at_expected_net": V.power_one_sided(expected_net[b], sd, nn),
                             "meets_primary_precision_target": V.t975(nn - 1) * sd / (nn ** 0.5) <= hw}
        ss["per_budget"][b] = {"role": V.BUDGETS[b]["role"], "planning_sd_ms": sd, "expected_net_for_planning_ms": expected_net[b],
                               "expected_net_basis": "DELTA_COST_REF minus the replay-implied maximum wall benefit (generous: wall proportional to miss bytes); planning only", "candidates": cand,
                               "smallest_multiple_of_6_meeting_primary_precision": V.smallest_n_for_halfwidth(sd, hw), "frozen_n_blocks": n, "frozen_counted_launches": n * 6,
                               "frozen_half_width_ms_planning": V.t975(n - 1) * sd / (n ** 0.5), "frozen_power_planning": V.power_one_sided(expected_net[b], sd, n),
                               "selection_rule": ("smallest multiple of 6 with planning half-width <= DELTA_COST_REF/3" if b == V.PRIMARY_BUDGET else
                                                   "one full cycle of the 6 permutations (minimum fully order-balanced N); descriptive role, no classification label, no inferential decision depends on it")}
    ss["hours"] = V.hours_estimate(wall)
    ss["hours_inputs_ms_per_token"] = {"b_selected_wall_planning": wall, "note": "1.5 and 4 GiB use the (slower, old-runtime) R2-12 values: deliberately conservative; 8 GiB uses the R3-03B measured value. S/P add DELTA_COST_REF. Prefill scaled from 10.7 s at 8 GiB; startup 10 s; traced decode x5.05 (R3-00A)"}
    ss["r3_03b_observed_for_calibration"] = {"campaign_span_h": 7.36, "per_launch_incl_cooldown_s": 26495 / 80, "model_prediction_current_8GiB_s_per_launch_incl_cooldown": V.launch_seconds("8", "A", wall, "current") + 15 + 60 / 4}
    ss["disk"] = {"timing_records_bytes_estimate": "<= 20 KB x 183 launches + stderr ~4 KB: < 6 MB", "mechanism_trace_bytes_per_launch_peak": "<= ~12 GB (R3-03A: 6.8 GB/launch observed at 4-8 GiB; R3-03B noted ~10 GB), each trace is analysed to a compact record and its sha256 + size are kept; raw trace is then deleted by the driver (regenerable: greedy decoding + deterministic policy)",
                  "free_space_gate_bytes_timing": 2 * 1024 ** 3, "free_space_gate_bytes_mechanism": 25 * 1024 ** 3, "retained_total_bytes_estimate": "< 50 MB"}
    put("SAMPLE_SIZE.json", ss)
    put("BUDGET_SELECTION.json", {"artifact": "R3-04-BUDGET-SELECTION", "evaluated": {b: {"replay_evidence": bounds[b], "old_runtime_wall_b_selected_ms_per_token": wall[b], "role": V.BUDGETS[b]["role"], "n_blocks": V.BUDGETS[b]["n_blocks"]} for b in V.BUDGET_ORDER},
        "primary": {"budget": "1.5 GiB", "why": "largest evidence-supported retention benefit (R2-11 replay: 0.131% miss-byte reduction vs 0.037% at 4 GiB and exactly 0 at 8 GiB); it is the best case for NET_WIN: if the real policy cannot recover its bookkeeping cost where retention helps most, no other tested budget can win. It also never had a real-model conformance for any policy in the corrected runtime, so the mechanism phase covers it first.",
                    "limit": "A NET_LOSS at 1.5 GiB is not a proof of loss at budgets that were not tested; it bounds the tested implementation at the most favourable tested budget."},
        "confirmatory": {"budget": "4 GiB", "why": "the R2-BRIDGE/R2-11/R2-12 primary budget: links R3-04 to the original negative R2-12 result under the corrected runtime and gives a middle point of the budget gradient. Descriptive N=6 (one full permutation cycle); no classification label.", "droppable_without_changing_primary_answer": True},
        "negative_control": {"budget": "8 GiB", "why": "replay retention benefit is exactly zero (tie): P-S should equal only the extra policy bookkeeping time and miss/eviction counters should tie. It validates that the instrument does not manufacture a retention effect, and its S-A contrast is the budget R3-03B already measured (replication check). Descriptive N=6.", "caution": "the real runtime at 8 GiB still has eviction (victim_calls 7966 in R3-03B) so 'zero retention benefit' is a replay-level expectation that the mechanism phase checks directly"},
        "rejected": ["1.5+4+8 all as primary (multiplicity, ~3x cost, no information gain for the single net question)", "8 GiB only (would test the budget where retention benefit is zero; cannot answer 'does retention pay for itself')", "4 GiB only (small effect, I/O-bound noise, no best-case test)"],
        "prior_evidence_use": {"budget_selection": "R2-11 per-budget replay retention deltas (replay evidence, used as a prior only)", "variance_planning": "R2-12 paired-delta SD (conservative proxy) and R3-03B block SD (check)", "negative_control": "R2-11 8 GiB exact tie", "positive_control": "none claimed: no direct runtime positive control exists for retention benefit",
                               "instrumentation": "R3-03B conformance/final evidence (policy_timing schema r3-03b-v1)", "not_used_as_observations": "all R2-12 and R3-03B outcome values"}})
    proto = {"artifact": "R3-04-PROTOCOL", "schema": "r3-04-protocol-v1", "status": "R3_04_PROTOCOL_FROZEN", "acquisition_opened": False, "model_runs_performed": False,
             "question": "Does the retention benefit produced by the REAL predicted_protect policy recover its own predictor/bookkeeping cost and improve corrected-runtime end-to-end performance relative to b_selected?",
             "arms": {"A": {"policy_id": "b_selected"}, "S": {"policy_id": "predicted_protect_sham", "role": "identical predictive bookkeeping, victims chosen exactly as b_selected"}, "P": {"policy_id": "predicted_protect", "role": "full policy"}},
             "configs": {b: {a: V.config(b, a) for a in "ASP"} for b in V.BUDGET_ORDER},
             "workload": {"id": "r2-11-coding-v1", "seed": 42, "temperature": 0, "top_k": 1, "n_predict": 512, "context": 4096, "single_workload": True},
             "primary_endpoint": {"name": "decode_wall_ms_per_token", "definition": "server timings predicted_ms / predicted_n (n=512), lower is better", "frozen_before_acquisition": True},
             "secondary_endpoints": ["decode_tokens_per_second = 1000 / ms_per_token (per launch, then block means; reversed sign)", "policy_ms_per_token = policy_timing.ns_total / 512 / 1e6 per arm", "decode miss bytes per token, hits, evictions (mechanism phase, trace-derived, exact)", "ttft/prefill ms (descriptive)"],
             "estimands": {"DELTA_COST": "S - A", "DELTA_RETENTION": "P - S", "DELTA_NET": "P - A (= DELTA_COST + DELTA_RETENTION)", "unit": "block-level contrast of 2-launch arm means", "negative_wall_net": "P faster than A",
                           "POL_COST/POL_RET/POL_NET": "same contrasts of policy_ms_per_token", "DELTA_RETENTION_ADJ": "DELTA_RETENTION wall minus POL_RET (removes the measured extra bookkeeping of P over S)", "rho_cost": "mean(DELTA_COST)/mean(POL_COST), 95% Fieller df=N-1 (R3-03B continuation; descriptive at every budget)"},
             "decision_rule": {"scope": "PRIMARY budget 1.5 GiB, DELTA_NET in decode wall ms/token only", "ci": "two-sided 95% t interval over blocks, df = 17", "NET_WIN": "CI upper bound < 0", "NET_LOSS": "CI lower bound > 0", "INCONCLUSIVE": "CI contains 0",
                               "precision_flag": f"adequate iff CI half-width <= DELTA_COST_REF/3 = {V.precision_target_ms():.6f} ms/token", "claim_classes": {"Q4_NET_RUNTIME_WIN": "NET_WIN", "Q3_APPROXIMATE_BREAK_EVEN_BOUNDED": "INCONCLUSIVE and precision adequate", "Q5_NO_RELIABLE_CONCLUSION": "INCONCLUSIVE and precision inadequate",
                               "Q2_PARTIAL_COST_RECOVERY_STILL_SLOWER": "NET_LOSS and 95% CI of DELTA_RETENTION_ADJ entirely < 0", "Q1_NO_COST_RECOVERY_RESOLVED": "NET_LOSS otherwise (the mechanism phase states separately whether retention improved: P improves retention but does not recover cost)"},
                               "no_practical_threshold": "No arbitrary percentage threshold is used. The only scale is the measured bookkeeping cost, used for precision planning, not for classification.", "secondary_budgets": "descriptive; no classification label; they can never override the primary classification",
                               "tokens_per_second": "reported, sign-reversed, never used to classify"},
             "n_blocks": {b: V.BUDGETS[b]["n_blocks"] for b in V.BUDGET_ORDER}, "counted_launches": {b: 6 * V.BUDGETS[b]["n_blocks"] for b in V.BUDGET_ORDER}, "total_counted_launches": 180,
             "warmup": {"rule": "ONE uncounted warmup launch per budget campaign, executed with the first arm of block 1 under the identical env, 60 s cooldown after; recorded, never in any estimator; frozen now",
                        "why": "R3-03B had none and its block 1 was the most influential block (descriptive finding); R2-12 used warmups; a warmup removes the cold-start confound from block 1 at a cost of 3 launches"},
             "schedule": {"timing_schedule_sha256": V.schedule_sha(sched), "mechanism_schedule_sha256": V.schedule_sha(msched), "file": "SCHEDULE.json"},
             "optional_stopping": "none: N per budget is a constant of this protocol, the driver has no N option, no estimator runs before the fixed N is complete",
             "failure_rule": "same as R3-03B: a failed launch is retained as FAILED; the same slot may be rerun once with attempt+1; a second failure aborts the campaign; no imputation/replacement/reordering/outlier removal",
             "admission_rule": "token digest == frozen digest; 512 tokens; shutdown graceful; exactly one policy_timing record with calls == 24672; server timings present; duplicate_event_refusals == 0; no error; runtime sha256 == frozen",
             "runtime": {"sha256": V.RUNTIME_SHA256, "reuse": True, "new_instrumentation_required": False, "why": "policy_timing (r3-03b-v1) already gives per-arm policy time; retention mechanism counters are obtained from dedicated trace launches (exact, deterministic policy) not from new runtime counters, so the conformant R3-03B binary is reused and nothing is rebuilt"},
             "mechanism_phase": {"purpose": "exact aggregate retention metrics + conformance of A/S/P at each R3-04 budget on the reused runtime; NOT timing", "launches": len(msched), "sha256": V.schedule_sha(msched), "trace_in_timing_campaign": False,
                                 "metrics": ["decode miss bytes/token", "decode hits", "decode evictions", "all-pass totals", "protected_before effect", "resident bytes peak"],
                                 "fail_closed_gates": ["any launch digest != frozen digest", "events != 24672 or passes != 514", "frozen R2 replay mismatch (R3-03A analyser) for A, S or P", "S decisions differ from A decisions (decomposition invalid)", "P timing-OFF vs ON canonical trace differs", "budget violation", "duplicate refusal"],
                                 "no_outcome_gating": "the magnitude/sign of the retention improvement never gates the timing campaign"},
             "shutdown": "see SHUTDOWN_PLAN.md: diagnosis undetermined; tooling-only change (own console + attach/generate helper) validated model-free then in the mechanism phase; fallback = R3-03B path; graceful semantics and policy_timing emission unchanged",
             "parents": parents(), "planning_inputs_sha256": {"SAMPLE_SIZE.json": None},
             "future_gate": "HoPi apples-to-apples comparison: see CLAIM_BOUNDARIES.md section 'Future HoPi gate'; not part of R3-04"}
    proto["planning_inputs_sha256"] = {"SAMPLE_SIZE.json": V.sha_file(HERE / "SAMPLE_SIZE.json"), "SCHEDULE.json": V.sha_file(HERE / "SCHEDULE.json"), "BUDGET_SELECTION.json": V.sha_file(HERE / "BUDGET_SELECTION.json")}
    put("PROTOCOL.json", proto)
    if freeze:
        files = ["PROTOCOL.json", "SCHEDULE.json", "SAMPLE_SIZE.json", "BUDGET_SELECTION.json", "ESTIMANDS.md", "INSTRUMENTATION_PLAN.md", "SHUTDOWN_PLAN.md", "CLAIM_BOUNDARIES.md", "README.md", "validate_protocol.py", "build_artifacts.py"]
        fr = {"artifact": "R3-04-PROTOCOL-FREEZE", "status": "R3_04_PROTOCOL_FROZEN", "acquisition_opened": False, "files_sha256_lf": {f: V.sha_lf(HERE / f) for f in files},
              "timing_schedule_sha256": V.schedule_sha(sched), "mechanism_schedule_sha256": V.schedule_sha(msched), "runtime_sha256": V.RUNTIME_SHA256,
              "parent_freeze_sha256": {"r3_03b_final": parents()["r3_03b_final_freeze"]["freeze_sha256_field"]}, "primary_budget": "1.5 GiB", "primary_endpoint": "decode_wall_ms_per_token"}
        fr["freeze_sha256"] = V.freeze_sha(fr)
        put("FREEZE.json", fr); print(fr["freeze_sha256"])

if __name__ == "__main__":
    main("--freeze" in sys.argv)
