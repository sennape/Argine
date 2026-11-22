"""R2-12 secondary/stress-budget formal analysis (additive, read-only w.r.t.
canonical records). Covers the 1.5 GiB (stress) and 8 GiB (null/control)
secondary budgets defined by run_order.json's frozen schedule.

This module deliberately REUSES the already-committed 4 GiB primary
analysis machinery rather than re-implementing it:
  - tools.r2_12_primary_analysis.check_output_equivalence(records) is
    imported and called UNMODIFIED -- it is already generic over any set
    of records grouped by cell_index, with no 4-GiB-specific assumption.
  - tools.r2_12_primary_analysis.compute_primary_statistics(records) is
    imported and called UNMODIFIED -- it is already generic: it computes
    per-workload paired deltas, the equal-weight aggregate, the frozen
    bootstrap 95% CI (via the same reused
    argine.research2.r2_11.bootstrap.paired_bootstrap_ci), direction
    counts, and the latency-tolerance checks, from whatever `records`
    dict it is given. Nothing about its body assumes 4 GiB.
  - tools.r2_12_primary_analysis.py itself is NOT modified by this file,
    so the already-committed 4 GiB formal analysis artifact and the
    script that produced it are untouched.

The one piece that genuinely cannot be reused unmodified is the
integrity-verification step, because
tools.r2_12_primary_analysis.verify_integrity() hardcodes
drv.build_4gib_plan() and the literal 4 GiB retention
decay/interval ("0.99"/"64"). verify_integrity_for_budget() below is a
new, budget-parameterized integrity check, but it is built entirely from
the SAME frozen, already-existing driver helpers the 4 GiB analysis (and
the budget-aware benchmark driver itself) use for this exact purpose --
drv.build_budget_plan, drv.budget_gib_to_bytes,
drv.resolve_baseline_params_for_budget, drv.resolve_predictor_decay_factor
-- so there is no parallel re-derivation of what the correct per-budget
parameters are; those values come from one place only (contract.json via
the driver's resolver functions), exactly as for 4 GiB.

CRITICAL: per decision_rules.json, the R2_12_PASS / R2_12_FAIL /
R2_12_INCONCLUSIVE / R2_12_BLOCKED_INVALID classification scheme is
defined ONLY in terms of "primary 4 GiB mean delta_tps" and the primary
4 GiB bootstrap CI. decision_rules.json.budget_specific_interpretive_stance
_frozen_before_execution.1_5gib_stress explicitly states that 1.5 GiB
"must NOT replace the 4 GiB primary budget as the basis for the
R2_12_PASS/FAIL/INCONCLUSIVE classification, even if its own effect size
is larger." No alternative secondary classification scheme is defined
anywhere in decision_rules.json or statistical_analysis.json for 1.5 GiB
or 8 GiB -- statistical_analysis.json.secondary_budget_statistics only
specifies that the identical per-cell/aggregate/bootstrap PROCEDURE is
applied independently at each secondary budget, not that a PASS/FAIL
label is produced from it. Accordingly this script computes and reports
the full frozen statistical result for the secondary budget, but
explicitly does NOT assign it any R2_12_* classification label: doing so
would be inventing an interpretive rule decision_rules.json does not
contain, which is the opposite error from "mechanically reusing the
primary label."

Usage: python3 tools/r2_12_secondary_budget_analysis.py --budget-gib 1.5 [--out-dir DIR]
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
import r2_12_primary_analysis as primary  # noqa: E402  -- reused unmodified

SECONDARY_BUDGETS = (1.5, 8.0)
SECONDARY_LABEL = {1.5: "1.5gib-stress", 8.0: "8gib-null-control"}


def load_canonical_records_for_budget(budget_gib: float) -> dict:
    """Thin wrapper over the shared, budget-scoped loader
    (tools.r2_12_primary_analysis.load_canonical_records), factored out
    there so the primary 4 GiB path and this secondary-budget path use
    the exact same filtered-loading logic -- one source of truth, no
    parallel re-implementation of the budget filter."""
    return primary.load_canonical_records(budget_gib=budget_gib)


def verify_integrity_for_budget(records: dict, frozen: dict, budget_gib: float) -> dict:
    """Budget-parameterized integrity check. Built from the SAME frozen
    per-budget resolver helpers the driver and the 4 GiB analysis use
    (drv.build_budget_plan / drv.budget_gib_to_bytes /
    drv.resolve_baseline_params_for_budget /
    drv.resolve_predictor_decay_factor) -- no re-derivation of expected
    values, no parallel source of truth."""
    cells, launches = drv.build_budget_plan(frozen, budget_gib)
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

    budget_bytes = drv.budget_gib_to_bytes(budget_gib)
    baseline_params = drv.resolve_baseline_params_for_budget(frozen, budget_bytes)
    predictor_decay_factor = drv.resolve_predictor_decay_factor(frozen)
    expected_decay = str(baseline_params["decay_factor"])
    expected_interval = str(baseline_params["aging_interval_steps"])
    expected_predictor_decay = str(predictor_decay_factor)
    expected_budget_bytes_str = str(budget_bytes)

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
        base_ok = (env.get("ARGINE_RETENTION_DECAY") == expected_decay
                   and env.get("ARGINE_RETENTION_INTERVAL") == expected_interval
                   and env.get("ARGINE_EXPERT_BUDGET") == expected_budget_bytes_str)
        if r["policy_id"] == "b_selected":
            retention_ok.append(base_ok)
        else:
            retention_ok.append(base_ok
                                 and env.get("ARGINE_RETENTION_PREDICTOR_DECAY") == expected_predictor_decay
                                 and env.get("ARGINE_RETENTION_TOPK") == "8")

    contaminated = [rid for rid, r in records.items()
                    if r.get("precanonical_classification") is not None
                    or r.get("contaminated_by_inherited_process_trace_state") is True]

    peak_resident_vals = [r["runtime_metrics"]["peak_resident_memory_bytes"] for r in records.values()]

    return {
        "budget_gib": budget_gib,
        "budget_bytes": budget_bytes,
        "session_id": drv.BUDGET_SESSION_IDS.get(budget_gib),
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
        "all_budget_gib_matches_target": all(r.get("budget_gib") == budget_gib for r in records.values()),
        "schedule_order_matches_frozen_plan": len(order_mismatches) == 0,
        "schedule_order_mismatches": order_mismatches,
        "trace_disabled_all_records": all(trace_ok),
        "n_predict_512_all_records": all(gen_ok),
        "retention_params_correct_all_records": all(retention_ok),
        "expected_baseline_decay_factor": baseline_params["decay_factor"],
        "expected_baseline_aging_interval_steps": baseline_params["aging_interval_steps"],
        "expected_predictor_decay_factor": predictor_decay_factor,
        "no_precanonical_contamination": len(contaminated) == 0,
        "contaminated_run_ids": contaminated,
        "peak_resident_memory_bytes_range": {"min": min(peak_resident_vals), "max": max(peak_resident_vals)},
        "budget_violations_is_a_driver_constant_not_independently_measured": True,
        "runtime_identity_verified_once_at_campaign_start_not_per_record": True,
    }


def secondary_interpretation(budget_gib: float) -> dict:
    """Explicit, frozen-contract-sourced statement of what decision_rules.json
    does and does NOT say about this secondary budget's classification."""
    stance_key = "1_5gib_stress" if budget_gib == 1.5 else "8gib_null_control"
    return {
        "no_R2_12_classification_label_assigned": True,
        "reason": (
            "decision_rules.json.primary_decision_rule defines R2_12_PASS / "
            "R2_12_FAIL / R2_12_INCONCLUSIVE / R2_12_BLOCKED_INVALID strictly in "
            "terms of the primary 4 GiB mean delta_tps and the primary 4 GiB "
            "bootstrap CI. No alternative classification scheme for a secondary "
            "budget is defined anywhere in decision_rules.json or "
            "statistical_analysis.json. statistical_analysis.json."
            "secondary_budget_statistics specifies only that the identical "
            "per-cell/aggregate/bootstrap PROCEDURE is applied independently at "
            "each secondary budget (own CI, not pooled with 4 GiB) -- it does not "
            "say a PASS/FAIL/INCONCLUSIVE label follows from that procedure's "
            "output at a secondary budget."
        ),
        "frozen_interpretive_stance": (
            "decision_rules.json.budget_specific_interpretive_stance_frozen_"
            f"before_execution.{stance_key}"
        ),
        "frozen_interpretive_stance_text": drv.load_frozen_protocol()["decision_rules"][
            "budget_specific_interpretive_stance_frozen_before_execution"
        ][stance_key],
        "forbidden_claims_reminder": (
            "This secondary-budget magnitude must not be used to override, "
            "soften, or re-derive the already-committed primary 4 GiB "
            "R2_12_FAIL classification, and no causal attribution (e.g. "
            "'memory pressure caused X') may be made from a descriptive "
            "magnitude comparison alone."
        ),
    }


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--budget-gib", type=float, required=True, choices=SECONDARY_BUDGETS)
    ap.add_argument("--out-dir", default=str(drv.R2_12_DIR))
    args = ap.parse_args()
    budget_gib = args.budget_gib

    frozen = drv.load_frozen_protocol()
    digest = drv.verify_contract_digest()
    records = load_canonical_records_for_budget(budget_gib)

    integrity = verify_integrity_for_budget(records, frozen, budget_gib)
    oe = primary.check_output_equivalence(records)          # reused unmodified
    stats = primary.compute_primary_statistics(records)      # reused unmodified
    interpretation = secondary_interpretation(budget_gib)

    try:
        head = subprocess.run(["git", "rev-parse", "HEAD"], cwd=str(REPO_ROOT),
                               capture_output=True, text=True, timeout=10).stdout.strip()
    except Exception:
        head = None

    label = SECONDARY_LABEL[budget_gib]
    file_label = drv.BUDGET_FILENAME_LABELS[budget_gib]

    artifact = {
        "artifact_type": f"R2_12_{file_label}_SECONDARY_FORMAL_ANALYSIS",
        "budget_gib": budget_gib,
        "budget_role": label,
        "analysis_timestamp_utc": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "repository_commit_head": head,
        "frozen_contract_digest": digest,
        "runtime_v8_identity_sha256": drv.EXPECTED_RUNTIME_SHA256,
        "model_identity_sha256": drv.EXPECTED_MODEL_SHA256,
        "store_identity_sha256": drv.EXPECTED_STORE_SHA256,
        "canonical_acquisition_integrity": integrity,
        "output_equivalence": oe,
        "official_statistical_method": {
            "unit_of_analysis": f"paired per-repetition delta_tps within (workload, {budget_gib} GiB) cell; "
                                 "PredictedProtectPolicy - B_selected (statistical_analysis.json, "
                                 "secondary_budget_statistics: identical procedure to the 4 GiB primary, "
                                 "own bootstrap CI, not pooled with the primary 4 GiB budget)",
            "primary_aggregate": "equal-weight mean of 4 per-workload mean deltas",
            "uncertainty_method": "paired bootstrap, 100000 resamples, seed 42, 95% CI, "
                                   "argine.research2.r2_11.bootstrap.paired_bootstrap_ci "
                                   "(reused unmodified -- same call as the 4 GiB primary analysis)",
            "note": "compute_primary_statistics() and check_output_equivalence() are imported "
                    "from tools/r2_12_primary_analysis.py and called UNMODIFIED on this budget's "
                    "own record set; no parallel statistical implementation exists.",
        },
        "official_secondary_statistics": stats,
        "secondary_classification_determination": interpretation,
        "limitations": [
            "prediction_overhead_seconds / retention_policy_overhead_seconds are None for every "
            "record, same as the 4 GiB primary analysis; not needed here since no PASS/FAIL "
            "overhead-dominance clause applies to a secondary budget.",
            "budget_violations is a constant 0 written by the driver, not an independently "
            "measured counter from runtime instrumentation.",
            "peak_resident_memory_bytes was checked only for gross sanity (range "
            + f"{integrity['peak_resident_memory_bytes_range']['min']}-{integrity['peak_resident_memory_bytes_range']['max']} bytes), "
            "not against the correct planner-derived allowance (requires the real model file, "
            "not reachable from this analysis environment).",
            "Runtime/model/store identity hashes are verified once at campaign start "
            "(host_preflight_gate), not independently re-verified per record in this analysis -- "
            "identical caveat to the 4 GiB primary analysis.",
        ],
        "explicit_statement": f"No model inference was performed by this analysis script. "
                               f"The {budget_gib} GiB campaign itself was already executed and "
                               "committed as canonical evidence prior to this analysis step.",
    }

    out_dir = Path(args.out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    json_path = out_dir / f"R2_12_{file_label}_SECONDARY_FORMAL_ANALYSIS.json"
    json_path.write_text(json.dumps(artifact, indent=2, sort_keys=False))

    md_lines = [
        f"# R2-12 {budget_gib} GiB Secondary ({label}) -- Formal Analysis",
        "",
        f"Generated: {artifact['analysis_timestamp_utc']}  |  Commit: {head}",
        "",
        "## Classification",
        "**No R2_12_PASS/FAIL/INCONCLUSIVE label applies** -- decision_rules.json's "
        "primary_decision_rule is defined strictly in terms of the primary 4 GiB "
        "result; no secondary-budget classification scheme exists in the frozen "
        "contract. See `secondary_classification_determination` for the full reasoning.",
        "",
        "## Official secondary statistics (frozen method, applied independently at this budget)",
        f"- PredictedProtectPolicy mean TPS: {stats['descriptive_central_estimates']['predicted_protect_mean_tps']:.6f}",
        f"- B_selected mean TPS: {stats['descriptive_central_estimates']['b_selected_mean_tps']:.6f}",
        f"- Aggregate (equal-weight mean of 4 per-workload mean deltas, PP - B): {stats['primary_aggregate_statistic']['observed_mean_delta_tps']:.6f}",
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
        f"comparison admissible: {oe['primary_performance_comparison_admissible']}",
        "",
        "## Acquisition integrity summary",
        f"- Canonical records found: {integrity['n_canonical_records_found']} / expected {integrity['n_expected_frozen_launches']}",
        f"- Warmup/counted: {integrity['n_warmup']} / {integrity['n_counted']}",
        f"- Counted per policy: {integrity['counted_per_policy']}",
        f"- No precanonical contamination: {integrity['no_precanonical_contamination']}",
        f"- Schedule order matches frozen plan: {integrity['schedule_order_matches_frozen_plan']}",
        "",
        "## Limitations",
    ] + [f"- {l}" for l in artifact["limitations"]] + [
        "",
        "No model inference was performed by this analysis script.",
    ]
    md_path = out_dir / f"R2_12_{file_label}_SECONDARY_FORMAL_ANALYSIS.md"
    md_path.write_text("\n".join(md_lines) + "\n")

    print(json.dumps({"json_path": str(json_path), "md_path": str(md_path),
                       "no_classification_label": True}, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
