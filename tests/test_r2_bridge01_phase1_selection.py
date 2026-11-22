"""R2-BRIDGE-01 Phase-1 DEV selection result tests."""
from __future__ import annotations

import json

from argine.research2.r2_bridge01 import contract as c

BRIDGE_DIR = c.default_contract_dir()
ORIGINAL_PHASE0_DIGEST = "e4ef4268e6bc207f20d5f4a56c165b75b977536600ff9d6f7e59604f77a889fc"
ADDENDUM_DIGEST = "5954a0f6d13fcf427124f8b7f7d7f009d2d76fbe82e4b4e28c6aa2167fc8eb92"


def _load(name):
    return json.loads((BRIDGE_DIR / name).read_text())


def test_original_phase0_digest_still_unchanged():
    assert c.compute_contract_digest() == ORIGINAL_PHASE0_DIGEST


def test_tournament_results_complete_96_cells_zero_violations():
    results = _load("PHASE1-DEV-TOURNAMENT-RESULTS.json")
    assert results["total_cells"] == 96
    assert results["expected_cells"] == 96
    assert results["budget_violations_total"] == 0
    assert results["overflow_misses_total"] == 0


def test_selection_freeze_matches_analysis():
    freeze = _load("PHASE1-SELECTION-FREEZE.json")
    analysis = _load("PHASE1-DEV-SELECTION-ANALYSIS.json")
    assert freeze["classification"] == "DEV_RETENTION_SIGNAL_POSITIVE"
    assert freeze["selected_config_id"] == analysis["best_candidate_config_id"]
    assert freeze["primary_aggregate_dev_value"] == analysis["best_candidate_mean_miss_bytes_per_decode_step"]
    assert freeze["baseline_aggregate_value"] == analysis["baseline_b_selected_4gib_mean_miss_bytes_per_decode_step"]


def test_selection_freeze_pins_both_digests():
    freeze = _load("PHASE1-SELECTION-FREEZE.json")
    assert freeze["original_phase0_contract_digest"] == ORIGINAL_PHASE0_DIGEST
    assert freeze["selection_rule_amendment_digest"] == ADDENDUM_DIGEST


def test_selected_candidate_was_eligible_not_the_excluded_control():
    freeze = _load("PHASE1-SELECTION-FREEZE.json")
    assert freeze["selected_config_id"] != "predictor_retention_score/budget=4294967296,bonus=0.0"
    assert freeze["selected_config_id"] in (
        "predicted_protect/budget=4294967296",
        "predictor_retention_score/budget=4294967296,bonus=1.0",
        "predictor_retention_score/budget=4294967296,bonus=4.0",
    )


def test_per_instance_improvement_consistent_across_all_8():
    analysis = _load("PHASE1-DEV-SELECTION-ANALYSIS.json")
    tournament = _load("PHASE1-DEV-TOURNAMENT-RESULTS.json")
    best_cid = analysis["best_candidate_config_id"]
    fam, _, rest = best_cid.partition("/")
    budget = 4294967296
    for inst in ["coding-v1__seed-42", "coding-v1__seed-43", "instruction-v1__seed-42", "instruction-v1__seed-43",
                 "reasoning-v1__seed-42", "reasoning-v1__seed-43", "summarization-v1__seed-42", "summarization-v1__seed-43"]:
        cell = tournament["cells"][f"{inst}|budget={budget}|{fam}"]
        base_cell = tournament["cells"][f"{inst}|budget={budget}|predictor_retention_score,bonus=0.0"]
        assert cell["bytes_missed"] / cell["steps"] <= base_cell["bytes_missed"] / base_cell["steps"]


def test_secondary_budgets_did_not_change_winner():
    freeze = _load("PHASE1-SELECTION-FREEZE.json")
    robustness = _load("PHASE1-SECONDARY-BUDGET-ROBUSTNESS.json")
    # Selection decision is pinned to the 4 GiB analysis; secondary budgets
    # are present only as descriptive robustness evidence.
    assert freeze["budget_bytes"] == 4294967296
    assert set(robustness["budgets"].keys()) == {"1610612736", "8589934592"}
    assert robustness["role"] == "descriptive_only_not_used_for_selection"


def test_bridge_holdout_not_opened():
    freeze = _load("PHASE1-SELECTION-FREEZE.json")
    assert "NOT CAPTURED, NOT OPENED, NOT INSPECTED" in freeze["bridge_holdout_status"]


def test_r2_10_and_r2_11_history_unchanged():
    r2_10_closure = c.load_json(c.r2_10_dir() / "PHASE3L-FINAL-CLOSURE-RECORD.json")
    assert r2_10_closure["r2_11_proposed"] is False
    assert r2_10_closure["r2_11_started"] is False
    assert r2_10_closure["final_status"] == "R2-10 CLOSED"
