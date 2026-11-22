"""Regression tests for R2-10 Phase 3 (EVAL) closure artifacts."""
import json
from pathlib import Path

BASE = Path(__file__).parent.parent / "experiments" / "R2-10-working-set-predictor"


def load(name):
    return json.loads((BASE / name).read_text())


def test_eval_results_four_workloads_three_horizons_three_families():
    d = load("PHASE3D-EVAL-RESULTS.json")
    assert set(d["workload_instances"]) == {
        "coding-eval-v1", "reasoning-eval-v1", "summarization-eval-v1", "instruction-eval-v1",
    }
    assert d["horizons"] == [1, 4, 16]
    assert len(d["families_evaluated"]) == 3
    for fam, cells in d["aggregated_mean_recall_at_k"].items():
        assert set(cells.keys()) == {"h1", "h4", "h16"}


def test_eval_results_determinism_confirmed():
    d = load("PHASE3D-EVAL-RESULTS.json")
    assert d["determinism_check"]["result"] == "ALL_BIT_IDENTICAL"


def test_selected_predictor_beats_both_baselines_at_h1():
    d = load("PHASE3D-EVAL-RESULTS.json")
    agg = d["aggregated_mean_recall_at_k"]
    sel = agg["decayed_transition/decay=0.99"]["h1"]
    assert sel > agg["global_frequency/default"]["h1"]
    assert sel > agg["last_observation/default"]["h1"]


def test_per_workload_analysis_flags_the_h16_global_frequency_exception():
    d = load("PHASE3E-PER-WORKLOAD-ANALYSIS.json")
    assert d["h16"]["positive_in_all_four_workloads_vs_global_frequency"] is False
    assert "instruction-eval-v1" in d["h16"]["workloads_where_selected_does_not_beat_global_frequency"]
    # last_observation comparison remains clean at every horizon
    for key in ("h1_primary", "h4", "h16"):
        assert d[key]["positive_in_all_four_workloads_vs_last_observation"] is True


def test_dev_vs_eval_comparison_answers_abcd():
    d = load("PHASE3G-DEV-VS-EVAL-COMPARISON.json")
    assert d["question_A_generalized_to_eval"] is True
    assert d["question_B_beats_last_observation_on_eval"] is True
    assert d["question_C_beats_global_frequency_on_eval"] is True


def test_leakage_determinism_audit_reports_clean():
    d = load("PHASE3H-LEAKAGE-DETERMINISM-AUDIT.json")
    assert d["frozen_test_suite_rerun"]["result"] == "9 passed, 0 failed"
    assert d["frozen_test_suite_rerun"]["modified_for_this_substep"] is False


def test_frozen_digests_still_match_preregistration():
    d = load("PHASE3I-FROZEN-DIGEST-VERIFICATION.json")
    assert d["eval_manifest_json_unchanged_since_eval_opened"] is True
    assert d["r2_09b_contract_digest_computed"] == d["r2_09b_contract_digest_expected"]
    assert d["r2_10_phase0_contract_digest_computed"] == d["r2_10_phase0_contract_digest_expected"]


def test_final_interpretation_does_not_promote_category_b_to_c():
    d = load("PHASE3J-FINAL-INTERPRETATION.json")
    assert d["scientific_decision"]["E_is_evidence_sufficient_under_frozen_rules_to_propose_R2_11"]["answer"] is False
    assert d["r2_11_recommendation"].startswith("NOT PROPOSED")
    assert "Category B" in d["dev_category_b_status"]


def test_final_interpretation_includes_the_negative_finding():
    d = load("PHASE3J-FINAL-INTERPRETATION.json")
    joined = " ".join(d["negative_findings"])
    assert "instruction-eval-v1" in joined
    assert "global_frequency" in joined


def test_closure_record_does_not_start_r2_11():
    d = load("PHASE3L-FINAL-CLOSURE-RECORD.json")
    assert d["r2_11_proposed"] is False
    assert d["r2_11_started"] is False
    assert d["final_status"] == "R2-10 CLOSED"
    assert d["overlay_state"] == "EVAL_EVALUATED_CLOSED"


def test_scratch_file_not_present():
    assert not (BASE / "_tmp_raw_eval_results.json").exists()
