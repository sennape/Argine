"""Unit tests for the R2-11 frozen decision-rule classifier, on synthetic inputs only."""
import sys
import json
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from argine.research2.r2_11.decision import R2_11_DecisionInputs, classify, r2_12_gate

REPO_ROOT = Path(__file__).resolve().parents[1]
DECISION_RULES_PATH = REPO_ROOT / "experiments" / "R2-11-predictive-retention" / "decision_rules.json"


def _base_pass_inputs(**overrides):
    base = dict(
        mean_delta_4gib=-100.0,
        bootstrap_ci_upper_4gib=-10.0,
        n_workloads_delta_le_0_at_4gib=11,
        n_workloads_total=12,
        dominant_workload_fraction=0.3,
        mean_delta_1_5gib=-50.0,
        mean_delta_8gib=-5.0,
        budget_violations=0,
        causal_leakage_failures=0,
        determinism_checks_passed=True,
        scientific_validity_failure=False,
    )
    base.update(overrides)
    return R2_11_DecisionInputs(**base)


def test_all_conditions_met_is_pass():
    assert classify(_base_pass_inputs()) == "R2_11_PASS"


def test_ci_crosses_zero_is_inconclusive():
    inp = _base_pass_inputs(bootstrap_ci_upper_4gib=5.0)
    assert classify(inp) == "R2_11_INCONCLUSIVE"


def test_not_enough_workloads_improving_is_inconclusive():
    inp = _base_pass_inputs(n_workloads_delta_le_0_at_4gib=8)
    assert classify(inp) == "R2_11_INCONCLUSIVE"


def test_dominant_workload_over_half_is_inconclusive():
    inp = _base_pass_inputs(dominant_workload_fraction=0.6)
    assert classify(inp) == "R2_11_INCONCLUSIVE"


def test_secondary_budget_positive_is_inconclusive():
    inp = _base_pass_inputs(mean_delta_8gib=1.0)
    assert classify(inp) == "R2_11_INCONCLUSIVE"


def test_nonneg_primary_mean_delta_is_fail():
    inp = _base_pass_inputs(mean_delta_4gib=0.0, bootstrap_ci_upper_4gib=0.0)
    assert classify(inp) == "R2_11_FAIL"


def test_positive_primary_mean_delta_is_fail():
    inp = _base_pass_inputs(mean_delta_4gib=50.0, bootstrap_ci_upper_4gib=80.0)
    assert classify(inp) == "R2_11_FAIL"


def test_scientific_validity_failure_overrides_everything_to_blocked():
    inp = _base_pass_inputs(scientific_validity_failure=True, budget_violations=1)
    assert classify(inp) == "R2_11_BLOCKED_INVALID"


def test_budget_violation_alone_without_validity_flag_is_inconclusive_not_fail():
    # budget_violations != 0 fails the PASS gate but the validity flag is what
    # routes to BLOCKED_INVALID per decision_rules.json; test the boundary explicitly.
    inp = _base_pass_inputs(budget_violations=1, scientific_validity_failure=False)
    assert classify(inp) == "R2_11_INCONCLUSIVE"


def test_r2_12_gate_text_matches_frozen_rules():
    rules = json.loads(DECISION_RULES_PATH.read_text(encoding="utf-8"))
    gate = rules["r2_12_gate"]
    assert r2_12_gate("R2_11_PASS") == gate["if_R2_11_PASS"]
    assert r2_12_gate("R2_11_INCONCLUSIVE") == gate["if_R2_11_INCONCLUSIVE"]
    assert r2_12_gate("R2_11_FAIL") == gate["if_R2_11_FAIL"]


def test_decision_rules_json_labels_match_classifier_outputs():
    rules = json.loads(DECISION_RULES_PATH.read_text(encoding="utf-8"))
    labels = set(rules["primary_decision_rule"].keys())
    assert labels == {"R2_11_PASS", "R2_11_INCONCLUSIVE", "R2_11_FAIL", "R2_11_BLOCKED_INVALID"}
