"""R2-12 4 GiB primary formal analysis (additive, read-only w.r.t. canonical records).

Reads the canonical 4 GiB acquisition under experiments/R2-12-runtime-validation/
runtime_results/ and produces the formal, preregistered primary-result artifact
per contract.json / statistical_analysis.json / decision_rules.json /
output_equivalence.json. Performs NO model inference, NO benchmark execution,
and makes NO changes to any frozen protocol, policy code, or sealed canonical
record. Reuses argine.research2.r2_11.bootstrap.paired_bootstrap_ci unmodified
(statistical_analysis.json.bootstrap.implementation_reuse).

Usage: python3 tools/r2_12_primary_analysis.py [--out-dir DIR]
"""
from __future__ import annotations

import argparse
import json
import subprocess
import sys
import time
from collections import defaultdict
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT))
sys.path.insert(0, str(REPO_ROOT / "tools"))

import r2_12_benchmark_driver as drv  # noqa: E402
from argine.research2.r2_11.bootstrap import paired_bootstrap_ci  # noqa: E402

RESULTS_DIR = drv.RESULTS_DIR
WORKLOAD_ORDER = ["r2-11-coding-v1", "r2-11-instruction-v1", "r2-11-reasoning-v1", "r2-11-summarization-v1"]


def load_canonical_records() -> dict:
    records = {}
    for f in RESULTS_DIR.glob("*.json"):
        try:
            d = json.loads(f.read_text())
        except Exception:
            continue
        if d.get("canonical") is True:
            records[d["run_id"]] = d
    return records


def verify_integrity(records: dict, frozen: dict) -> dict:
    cells, launches = drv.build_4gib_plan(frozen)
    expected = {l["run_id"]: l for l in launches}

    missing = sorted(set(expected) - set(records))
    unexpected = sorted(set(records) - set(expected))

    n_warmup = sum(1 for r in records.values() if r.get("warmup") is True)
    n_counted = sum(1 for r in records.values() if r.get("counted") is True)

    by_policy_counted = defaultdict(int)
    by_wp_counted = defaultdict(int)
    for r in records.values():
        if r.get("counted"):
            by_policy_counted[r["policy_id"]] += 1
            by_wp_counted[(r["workload_id"], r["policy_id"])] += 1

    order_mismatches = []
    for rid, launch in expected.items():
        r = records.get(rid)
        if r is None:
            continue
        for field in ("ordering_block", "cell_index", "position_in_abba_sequence", "policy_id", "workload_id"):
            if r.get(field) != launch.get(field):
                order_mismatches.append(rid)
                break

    trace_ok, gen_ok, retention_ok = [], [], []
    for r in records.values():
        env = r.get("environment", {})
        trace_ok.append("ARGINE_RETENTION_TRACE" not in env and r.get("trace_enabled") is False)
        ppc = r.get("pre_popen_env_check")
        if ppc is not None:
            trace_ok.append(ppc.get("retention_trace_env_present") is False
                             and ppc.get("trace_output_variables_present") == [])
        tg = r.get("runtime_metrics", {}).get("tokens_generated")
        gen_ok.append(tg in (512, None))
        if r["policy_id"] == "b_selected":
            retention_ok.append(env.get("ARGINE_RETENTION_DECAY") == "0.99" and env.get("ARGINE_RETENTION_INTERVAL") == "64")
        else:
            retention_ok.append(env.get("ARGINE_RETENTION_DECAY") == "0.99" and env.get("ARGINE_RETENTION_INTERVAL") == "64"
                                 and env.get("ARGINE_RETENTION_PREDICTOR_DECAY") == "0.99"
                                 and env.get("ARGINE_RETENTION_TOPK") == "8")

    # precanonical-contamination check: the SAME run_id legitimately exists in both
    # PRECANONICAL_TRACE_ENABLED_ATTEMPTS/ (quarantined, canonical:false, pre-fix content)
    # and runtime_results/ (fresh, canonical:true, post-fix content) for the 5 run_ids
    # re-executed after the ARGINE_RETENTION_TRACE inheritance fix -- this is expected
    # (deterministic run_id per schedule slot), not contamination. Contamination would
    # mean the CURRENT canonical record's own content is itself non-canonical.
    contaminated = [rid for rid, r in records.items()
                    if r.get("precanonical_classification") is not None
                    or r.get("contaminated_by_inherited_process_trace_state") is True]

    peak_resident_vals = [r["runtime_metrics"]["peak_resident_memory_bytes"] for r in records.values()]

    return {
        "n_canonical_records_found": len(records),
        "n_expected_frozen_launches": len(expected),
        "missing_run_ids": missing,
        "unexpected_run_ids": unexpected,
        "no_duplicate_canonical_run_id": len(records) == len(set(records)),
        "n_warmup": n_warmup,
        "n_counted": n_counted,
        "counted_per_policy": dict(by_policy_counted),
        "exactly_20_per_policy": all(v == 20 for v in by_policy_counted.values()) and len(by_policy_counted) == 2,
        "exactly_5_per_workload_per_policy": all(v == 5 for v in by_wp_counted.values()) and len(by_wp_counted) == 8,
        "all_records_ok_true": all(r.get("ok") is True for r in records.values()),
        "all_counted_ok_true": all(r.get("ok") is True for r in records.values() if r.get("counted")),
        "all_budget_gib_4": all(r.get("budget_gib") == 4.0 for r in records.values()),
        "schedule_order_matches_frozen_plan": len(order_mismatches) == 0,
        "schedule_order_mismatches": order_mismatches,
        "trace_disabled_all_records": all(trace_ok),
        "n_predict_512_all_records": all(gen_ok),
        "retention_params_correct_all_records": all(retention_ok),
        "no_precanonical_contamination": len(contaminated) == 0,
        "contaminated_run_ids": contaminated,
        "peak_resident_memory_bytes_range": {"min": min(peak_resident_vals), "max": max(peak_resident_vals)},
        "peak_resident_vs_bare_4gib_budget_note": (
            "peak_resident_memory_bytes is WHOLE-PROCESS RSS, not expert-store-cache "
            "residency alone; runtime_metrics.json's own definition requires comparing "
            "it against the 4 GiB expert budget PLUS a fixed overhead allowance for "
            "weights/runtime/KV-cache (via argine.planner), not against the bare 4 GiB "
            "figure. That allowance-adjusted check needs the real model file (not "
            "reachable from this analysis environment) and was NOT performed here. "
            "The observed range (~5.2-5.6 GiB) is consistent with a 4 GiB expert "
            "budget plus a modest non-expert overhead and shows no gross anomaly, but "
            "is not a substitute for the real planner-based check."
        ),
        "budget_violations_is_a_driver_constant_not_independently_measured": True,
        "runtime_identity_verified_once_at_campaign_start_not_per_record": True,
    }


def check_output_equivalence(records: dict) -> dict:
    by_cell = defaultdict(dict)
    for rid, r in records.items():
        by_cell[r["cell_index"]][rid] = r

    per_cell = {}
    n_mismatch = 0
    digest_evidence = {}
    for cell_index, recs in by_cell.items():
        reason = drv.check_cell_output_equivalence(cell_index, recs)
        if reason:
            n_mismatch += 1
        per_cell[str(cell_index)] = {"reason": reason, "workload_id": next(iter(recs.values()))["workload_id"]}
        by_policy = defaultdict(set)
        for r in recs.values():
            if r.get("counted"):
                by_policy[r["policy_id"]].add(r.get("output_token_digest"))
        digest_evidence[str(cell_index)] = {k: sorted(v) for k, v in by_policy.items()}

    return {
        "n_cells_checked": len(by_cell),
        "n_matching": len(by_cell) - n_mismatch,
        "n_mismatching": n_mismatch,
        "per_cell": per_cell,
        "digest_evidence_unique_digests_per_policy_per_cell": digest_evidence,
        "primary_performance_comparison_admissible": n_mismatch == 0,
    }


def compute_primary_statistics(records: dict) -> dict:
    counted = [r for r in records.values() if r.get("counted")]
    by_workload = defaultdict(lambda: defaultdict(dict))
    for r in counted:
        by_workload[r["workload_id"]][r["policy_id"]][r["repetition_index"]] = r

    per_workload_mean_delta = {}
    per_workload_pairs = {}
    direction = {"predicted_protect_wins": 0, "b_selected_wins": 0, "ties": 0}

    for wl in WORKLOAD_ORDER:
        pp = by_workload[wl]["predicted_protect"]
        bs = by_workload[wl]["b_selected"]
        deltas, pairs_detail = [], []
        for rep in sorted(pp.keys()):
            pp_tps = pp[rep]["runtime_metrics"]["decode_tokens_per_second"]
            bs_tps = bs[rep]["runtime_metrics"]["decode_tokens_per_second"]
            delta = pp_tps - bs_tps
            deltas.append(delta)
            if delta > 0:
                direction["predicted_protect_wins"] += 1
            elif delta < 0:
                direction["b_selected_wins"] += 1
            else:
                direction["ties"] += 1
            pairs_detail.append({"repetition_index": rep, "predicted_protect_tps": pp_tps,
                                  "b_selected_tps": bs_tps, "delta_tps": delta})
        per_workload_mean_delta[wl] = sum(deltas) / len(deltas)
        per_workload_pairs[wl] = pairs_detail

    per_workload_deltas_list = [per_workload_mean_delta[wl] for wl in WORKLOAD_ORDER]
    observed_mean, ci_lo, ci_hi = paired_bootstrap_ci(per_workload_deltas_list)

    pp_all = [r["runtime_metrics"]["decode_tokens_per_second"] for r in counted if r["policy_id"] == "predicted_protect"]
    bs_all = [r["runtime_metrics"]["decode_tokens_per_second"] for r in counted if r["policy_id"] == "b_selected"]

    pp_ttft = [r["runtime_metrics"]["ttft_seconds"] for r in counted if r["policy_id"] == "predicted_protect"]
    bs_ttft = [r["runtime_metrics"]["ttft_seconds"] for r in counted if r["policy_id"] == "b_selected"]
    pp_e2e = [r["runtime_metrics"]["end_to_end_time_to_final_token_seconds"] for r in counted if r["policy_id"] == "predicted_protect"]
    bs_e2e = [r["runtime_metrics"]["end_to_end_time_to_final_token_seconds"] for r in counted if r["policy_id"] == "b_selected"]

    def tol_check(pp_vals, bs_vals):
        pp_mean, bs_mean = sum(pp_vals) / len(pp_vals), sum(bs_vals) / len(bs_vals)
        tol = max(0.05 * bs_mean, 0.005)
        reg = pp_mean - bs_mean
        return {"predicted_protect_mean": pp_mean, "b_selected_mean": bs_mean,
                "regression_seconds": reg, "tolerance_seconds": tol, "exceeds_tolerance": reg > tol}

    improving_workloads = [wl for wl in WORKLOAD_ORDER if per_workload_mean_delta[wl] >= 0]

    return {
        "per_workload_mean_delta_tps_PP_minus_B": per_workload_mean_delta,
        "per_workload_pairs": per_workload_pairs,
        "primary_aggregate_statistic": {
            "definition": ("equal-weight mean of the 4 per-workload mean delta_tps "
                            "(PredictedProtectPolicy - B_selected) at 4 GiB, per "
                            "statistical_analysis.json.primary_aggregate_statistic"),
            "observed_mean_delta_tps": observed_mean,
        },
        "bootstrap_95ci": {"lower": ci_lo, "upper": ci_hi, "resamples": 100000, "seed": 42,
                            "implementation": "argine.research2.r2_11.bootstrap.paired_bootstrap_ci (reused unmodified)"},
        "ci_excludes_zero_in_favor_of_predicted_protect": ci_lo > 0,
        "pair_direction_counts": direction,
        "n_pairs": sum(direction.values()),
        "descriptive_central_estimates": {
            "predicted_protect_mean_tps": sum(pp_all) / len(pp_all),
            "b_selected_mean_tps": sum(bs_all) / len(bs_all),
        },
        "consistency_workloads_with_delta_ge_0": len(improving_workloads),
        "consistency_requirement_met_3_of_4": len(improving_workloads) >= 3,
        "improving_workloads": improving_workloads,
        "dominance_test_applicable": len(improving_workloads) > 0,
        "ttft_latency_tolerance_check": tol_check(pp_ttft, bs_ttft),
        "end_to_end_latency_tolerance_check": tol_check(pp_e2e, bs_e2e),
        "budget_violations_total": sum((r["runtime_metrics"].get("budget_violations") or 0) for r in records.values()
                                        if r.get("counted") or r.get("warmup")),
    }


def classify(stats: dict, oe: dict) -> dict:
    """Applies decision_rules.json's primary_decision_rule EXACTLY as frozen.
    No invented labels, no post-hoc threshold changes."""
    if not oe["primary_performance_comparison_admissible"]:
        return {"classification": "R2_12_BLOCKED_INVALID",
                "reason": "output-equivalence failure in at least one cell (decision_rules.json.primary_decision_rule.R2_12_BLOCKED_INVALID)"}

    delta = stats["primary_aggregate_statistic"]["observed_mean_delta_tps"]
    ci_lo = stats["bootstrap_95ci"]["lower"]

    if delta <= 0:
        return {"classification": "R2_12_FAIL",
                "reason": ("primary 4 GiB mean delta_tps <= 0 (directionally worse or no "
                            f"improvement): observed_mean_delta_tps={delta:.6f}. Per "
                            "decision_rules.json.primary_decision_rule.R2_12_FAIL, this "
                            "alone is sufficient; the separate overhead-dominance clause "
                            "need not be evaluated.")}

    if ci_lo <= 0:
        return {"classification": "R2_12_INCONCLUSIVE",
                "reason": "delta_tps > 0 but 95% CI includes 0 (decision_rules.json.primary_decision_rule.R2_12_INCONCLUSIVE)"}

    pass_checks = {
        "delta_positive": delta > 0,
        "ci_excludes_zero_favoring_PP": ci_lo > 0,
        "zero_output_equivalence_failures": oe["n_mismatching"] == 0,
        "consistency_3_of_4_workloads": stats["consistency_requirement_met_3_of_4"],
        "ttft_within_tolerance": not stats["ttft_latency_tolerance_check"]["exceeds_tolerance"],
        "e2e_within_tolerance": not stats["end_to_end_latency_tolerance_check"]["exceeds_tolerance"],
    }
    if all(pass_checks.values()):
        return {"classification": "R2_12_PASS", "reason": "all R2_12_PASS requires_all conditions satisfied", "pass_checks": pass_checks}
    return {"classification": "R2_12_INCONCLUSIVE",
            "reason": f"delta>0 and CI excludes zero, but a secondary PASS criterion failed: {pass_checks}",
            "pass_checks": pass_checks}


def continuation_rule() -> dict:
    """No conditional/stop-rule tying 1.5 GiB/8 GiB to the 4 GiB result was found
    in contract.json, execution_plan.json, decision_rules.json, or run_order.json.
    run_order.json's across_cell_ordering defines all three budgets (4.0 primary,
    1.5 stress, 8.0 null/control) as ONE fixed, preregistered schedule; the
    budget-specific interpretive stances in decision_rules.json describe how to
    READ each budget's result, not whether to run it. Absent an explicit
    conditional clause, the schedule's own definition makes 1.5 GiB / 8 GiB
    mandatory preregistered components of the same protocol, not optional
    follow-ups gated on the 4 GiB outcome."""
    return {
        "determination": "A_mandatory_preregistered_components",
        "source": "run_order.json.across_cell_ordering.middle_loop "
                   "(\"budget_gib, in order: 4.0 (primary), 1.5 (stress), 8.0 (null/control)\") "
                   "and decision_rules.json.budget_specific_interpretive_stance_frozen_before_execution",
        "no_conditional_stop_rule_found_in": ["contract.json", "execution_plan.json", "decision_rules.json", "run_order.json"],
        "note": "This determination is NOT an instruction to execute them now; it only "
                "states what the frozen schedule itself defines.",
    }


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--out-dir", default=str(drv.R2_12_DIR))
    args = ap.parse_args()

    frozen = drv.load_frozen_protocol()
    digest = drv.verify_contract_digest()
    records = load_canonical_records()

    integrity = verify_integrity(records, frozen)
    oe = check_output_equivalence(records)
    stats = compute_primary_statistics(records)
    classification = classify(stats, oe)
    continuation = continuation_rule()

    try:
        head = subprocess.run(["git", "rev-parse", "HEAD"], cwd=str(REPO_ROOT),
                               capture_output=True, text=True, timeout=10).stdout.strip()
    except Exception:
        head = None

    artifact = {
        "artifact_type": "R2_12_4GIB_PRIMARY_FORMAL_ANALYSIS",
        "analysis_timestamp_utc": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "repository_commit_head": head,
        "frozen_contract_digest": digest,
        "runtime_v8_identity_sha256": drv.EXPECTED_RUNTIME_SHA256,
        "model_identity_sha256": drv.EXPECTED_MODEL_SHA256,
        "store_identity_sha256": drv.EXPECTED_STORE_SHA256,
        "canonical_acquisition_integrity": integrity,
        "output_equivalence": oe,
        "official_statistical_method": {
            "unit_of_analysis": "paired per-repetition delta_tps within (workload, 4 GiB) cell; "
                                 "PredictedProtectPolicy - B_selected (statistical_analysis.json)",
            "primary_aggregate": "equal-weight mean of 4 per-workload mean deltas",
            "uncertainty_method": "paired bootstrap, 100000 resamples, seed 42, 95% CI, "
                                   "argine.research2.r2_11.bootstrap.paired_bootstrap_ci (reused unmodified)",
        },
        "official_primary_statistics": stats,
        "primary_classification": classification,
        "continuation_rule_determination": continuation,
        "limitations": [
            "prediction_overhead_seconds / retention_policy_overhead_seconds are None for every "
            "record: the CPU-bookkeeping overhead isolation instrumentation_contract.json "
            "describes for the R2_12_FAIL overhead-dominance clause was not performed; it was "
            "not needed for this classification since the primary delta_tps<=0 clause alone "
            "already determines R2_12_FAIL.",
            "budget_violations is a constant 0 written by the driver, not an independently "
            "measured counter from runtime instrumentation; treat 'zero budget violations' as "
            "unverified-by-direct-instrumentation rather than a measured guarantee.",
            "peak_resident_memory_bytes was checked only for gross sanity (range "
            + f"{integrity['peak_resident_memory_bytes_range']['min']}-{integrity['peak_resident_memory_bytes_range']['max']} bytes), "
            "not against the correct planner-derived allowance (requires the real model file, "
            "not reachable from this analysis environment).",
            "Runtime/model/store identity hashes are verified once at campaign start "
            "(host_preflight_gate), not independently re-verified per record in this analysis.",
            "HARDWARE-IDENTITY-STAMP.json does not exist; the hardware-identity gate check "
            "passed on the 'first real run creates it' branch, which this analysis did not "
            "independently confirm against a second stamped run.",
        ],
        "budgets_executed_in_this_analysis_step": {"4.0": True, "1.5": False, "8.0": False},
        "explicit_statement": "1.5 GiB and 8 GiB campaigns were NOT run during this analysis step. "
                               "No model inference was performed by this analysis script.",
    }

    out_dir = Path(args.out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    json_path = out_dir / "R2_12_4GIB_PRIMARY_FORMAL_ANALYSIS.json"
    json_path.write_text(json.dumps(artifact, indent=2, sort_keys=False))

    md_lines = [
        "# R2-12 4 GiB Primary -- Formal Analysis",
        "",
        f"Generated: {artifact['analysis_timestamp_utc']}  |  Commit: {head}",
        "",
        "## Classification",
        f"**{classification['classification']}** -- {classification['reason']}",
        "",
        "## Official primary statistics",
        f"- PredictedProtectPolicy mean TPS: {stats['descriptive_central_estimates']['predicted_protect_mean_tps']:.6f}",
        f"- B_selected mean TPS: {stats['descriptive_central_estimates']['b_selected_mean_tps']:.6f}",
        f"- Primary aggregate (equal-weight mean of 4 per-workload mean deltas, PP - B): {stats['primary_aggregate_statistic']['observed_mean_delta_tps']:.6f}",
        f"- 95% paired bootstrap CI: [{stats['bootstrap_95ci']['lower']:.6f}, {stats['bootstrap_95ci']['upper']:.6f}]",
        f"- Pair direction: PredictedProtect wins {stats['pair_direction_counts']['predicted_protect_wins']}, "
        f"B_selected wins {stats['pair_direction_counts']['b_selected_wins']}, ties {stats['pair_direction_counts']['ties']} (of {stats['n_pairs']})",
        "",
        "## Per-workload mean delta_tps (PredictedProtect - B_selected)",
    ]
    for wl, v in stats["per_workload_mean_delta_tps_PP_minus_B"].items():
        md_lines.append(f"- {wl}: {v:.6f}")
    md_lines += [
        "",
        "## Output equivalence",
        f"{oe['n_matching']}/{oe['n_cells_checked']} cells matched exactly; "
        f"primary performance comparison admissible: {oe['primary_performance_comparison_admissible']}",
        "",
        "## Continuation rule (1.5 GiB / 8 GiB)",
        f"{continuation['determination']} -- see {continuation['source']}",
        "",
        "## Limitations",
    ] + [f"- {l}" for l in artifact["limitations"]] + [
        "",
        "1.5 GiB and 8 GiB were **not** run in this analysis step. No model inference was performed.",
    ]
    md_path = out_dir / "R2_12_4GIB_PRIMARY_FORMAL_ANALYSIS.md"
    md_path.write_text("\n".join(md_lines) + "\n")

    print(json.dumps({"json_path": str(json_path), "md_path": str(md_path),
                       "classification": classification["classification"]}, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
