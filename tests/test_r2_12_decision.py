"""Unit tests for the R2-12 frozen decision-rule classifier, on synthetic inputs only.

No real R2-12 runtime data exists at this phase; all inputs here are
synthetic/mock, exactly as required by decision_rules.json's
frozen_before_any_result declaration.
"""
import sys
import json
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from argine.research2.r2_12.decision import R2_12_DecisionInputs, classify, research_3_gate

REPO_ROOT = Path(__file__).resolve().parents[1]
DECISION_RULES_PATH = REPO_ROOT / "experiments" / "R2-12-runtime-validation" / "decision_rules.json"


def _base_pass_inputs(**overrides):
    base = dict(
        mean_delta_tps_4gib=5.0,
        bootstrap_ci_lower_4gib=1.0,
        n_workloads_delta_tps_ge_0_at_4gib=4,
        n_workloads_total=4,
        dominant_improving_workload_fraction=0.3,
        output_equivalence_failures=0,
        hardware_state_valid=True,
        thermal_throttling_detected=False,
        instrumentation_overhead_bounded=True,
        budget_violations=0,
        scientific_validity_failure=False,
        overhead_exceeds_measured_benefit=False,
        ttft_regression_exceeds_tolerance=False,
        deterministic_config_preserved=True,
    )
    base.update(overrides)
    return R2_12_DecisionInputs(**base)


def test_all_conditions_met_is_pass():
    assert classify(_base_pass_inputs()) == "R2_12_PASS"


def test_ci_crosses_zero_is_inconclusive():
    inp = _base_pass_inputs(bootstrap_ci_lower_4gib=-0.5)
    assert classify(inp) == "R2_12_INCONCLUSIVE"


def test_not_enough_workloads_improving_is_inconclusive():
    inp = _base_pass_inputs(n_workloads_delta_tps_ge_0_at_4gib=2)
    assert classify(inp) == "R2_12_INCONCLUSIVE"


def test_dominant_workload_over_half_is_inconclusive():
    inp = _base_pass_inputs(dominant_improving_workload_fraction=0.9)
    assert classify(inp) == "R2_12_INCONCLUSIVE"


def test_ttft_regression_beyond_tolerance_is_inconclusive():
    inp = _base_pass_inputs(ttft_regression_exceeds_tolerance=True)
    assert classify(inp) == "R2_12_INCONCLUSIVE"


def test_nondeterministic_config_is_inconclusive_not_pass():
    inp = _base_pass_inputs(deterministic_config_preserved=False)
    assert classify(inp) == "R2_12_INCONCLUSIVE"


def test_zero_primary_mean_delta_is_fail():
    inp = _base_pass_inputs(mean_delta_tps_4gib=0.0, bootstrap_ci_lower_4gib=0.0)
    assert classify(inp) == "R2_12_FAIL"


def test_negative_primary_mean_delta_is_fail():
    inp = _base_pass_inputs(mean_delta_tps_4gib=-3.0, bootstrap_ci_lower_4gib=-8.0)
    assert classify(inp) == "R2_12_FAIL"


def test_overhead_eats_benefit_is_fail_even_if_delta_positive():
    inp = _base_pass_inputs(overhead_exceeds_measured_benefit=True)
    assert classify(inp) == "R2_12_FAIL"


def test_output_equivalence_failure_is_blocked_invalid():
    inp = _base_pass_inputs(output_equivalence_failures=1)
    assert classify(inp) == "R2_12_BLOCKED_INVALID"


def test_hardware_state_invalid_is_blocked_invalid():
    inp = _base_pass_inputs(hardware_state_valid=False)
    assert classify(inp) == "R2_12_BLOCKED_INVALID"


def test_thermal_throttling_is_blocked_invalid():
    inp = _base_pass_inputs(thermal_throttling_detected=True)
    assert classify(inp) == "R2_12_BLOCKED_INVALID"


def test_unbounded_instrumentation_overhead_is_blocked_invalid():
    inp = _base_pass_inputs(instrumentation_overhead_bounded=False)
    assert classify(inp) == "R2_12_BLOCKED_INVALID"


def test_budget_violation_is_blocked_invalid_not_fail():
    # Per decision_rules.json, a budget violation (memory-correctness failure)
    # is a scientific-validity failure -> BLOCKED_INVALID, not FAIL, even
    # though a negative primary delta alone would route to FAIL.
    inp = _base_pass_inputs(budget_violations=1, mean_delta_tps_4gib=-10.0)
    assert classify(inp) == "R2_12_BLOCKED_INVALID"


def test_generic_scientific_validity_failure_overrides_everything():
    inp = _base_pass_inputs(scientific_validity_failure=True)
    assert classify(inp) == "R2_12_BLOCKED_INVALID"


def test_blocked_invalid_takes_precedence_over_fail():
    inp = _base_pass_inputs(output_equivalence_failures=1, mean_delta_tps_4gib=-5.0)
    assert classify(inp) == "R2_12_BLOCKED_INVALID"


def test_decision_rules_json_labels_match_classifier_outputs():
    rules = json.loads(DECISION_RULES_PATH.read_text(encoding="utf-8"))
    labels = set(rules["primary_decision_rule"].keys())
    assert labels == {"R2_12_PASS", "R2_12_INCONCLUSIVE", "R2_12_FAIL", "R2_12_BLOCKED_INVALID"}


def test_pass_requires_all_list_length_matches_frozen_rules():
    rules = json.loads(DECISION_RULES_PATH.read_text(encoding="utf-8"))
    requires_all = rules["primary_decision_rule"]["R2_12_PASS"]["requires_all"]
    assert len(requires_all) == 8


def test_research_3_gate_defines_no_gate():
    rules = json.loads(DECISION_RULES_PATH.read_text(encoding="utf-8"))
    note = rules["research_3_gate"]["note"].lower()
    assert "out of scope" in note
    text = research_3_gate("R2_12_PASS")
    assert "out of scope" in text


def test_sign_convention_inverted_relative_to_r2_11():
    # R2-11: negative byte-delta favored the predictive policy.
    # R2-12: positive tokens/sec delta favors the predictive policy.
    # A positive delta with everything else clean must NOT fail.
    assert classify(_base_pass_inputs(mean_delta_tps_4gib=0.5, bootstrap_ci_lower_4gib=0.01)) == "R2_12_PASS"
