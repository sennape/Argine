"""R2-BRIDGE-01 Phase 0.1 addendum tests. Asserts the DEV-selection-rule
addendum is self-consistent, that it did not disturb the original
Phase-0 digest, and that no DEV retention result existed before this
addendum was frozen.
"""
from __future__ import annotations

import hashlib
import json
from pathlib import Path

from argine.research2.r2_bridge01 import contract as c

REPO_ROOT = c.repo_root()
BRIDGE_DIR = c.default_contract_dir()
ADDENDUM_PATH = BRIDGE_DIR / "DEV-SELECTION-RULE-v1.json"
FREEZE_PATH = BRIDGE_DIR / "PHASE0A-DEV-SELECTION-RULE-FREEZE.json"

ORIGINAL_PHASE0_DIGEST = "e4ef4268e6bc207f20d5f4a56c165b75b977536600ff9d6f7e59604f77a889fc"


def _canon_sha256(obj) -> str:
    canon = json.dumps(obj, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    return hashlib.sha256(canon.encode("utf-8")).hexdigest()


def _addendum():
    return json.loads(ADDENDUM_PATH.read_text())


def _freeze():
    return json.loads(FREEZE_PATH.read_text())


def test_original_phase0_digest_unchanged():
    assert c.compute_contract_digest() == ORIGINAL_PHASE0_DIGEST


def test_addendum_digest_matches_freeze_record():
    addendum = _addendum()
    freeze = _freeze()
    assert _canon_sha256(addendum) == freeze["addendum_digest_sha256"]


def test_freeze_record_pins_original_digest():
    freeze = _freeze()
    assert freeze["original_phase0_contract_digest"] == ORIGINAL_PHASE0_DIGEST
    assert freeze["original_phase0_contract_digest_reverified_unchanged"] is True


def test_selection_budget_is_exactly_4gib():
    addendum = _addendum()
    assert addendum["selection_budget"]["value_bytes"] == 4294967296
    assert addendum["selection_budget"]["value_gib"] == 4.0


def test_exactly_three_eligible_candidates():
    addendum = _addendum()
    candidates = addendum["eligible_candidates_at_4gib"]
    assert len(candidates) == 3
    assert addendum["eligible_candidate_count"] == 3
    config_ids = {cand["config_id"] for cand in candidates}
    assert config_ids == {
        "predicted_protect/budget=4294967296",
        "predictor_retention_score/budget=4294967296,bonus=1.0",
        "predictor_retention_score/budget=4294967296,bonus=4.0",
    }


def test_bonus_zero_excluded_from_selection():
    addendum = _addendum()
    control = addendum["mandatory_control"]
    assert control["config_id"] == "predictor_retention_score/budget=4294967296,bonus=0.0"
    assert control["eligible_for_selection"] is False
    eligible_ids = {cand["config_id"] for cand in addendum["eligible_candidates_at_4gib"]}
    assert control["config_id"] not in eligible_ids


def test_equal_weight_dev_aggregation_stated():
    addendum = _addendum()
    agg = addendum["dev_corpus"]["aggregation"].lower()
    assert "equal weight" in agg
    assert addendum["dev_corpus"]["instances"] == 8


def test_deterministic_tie_break_defined():
    addendum = _addendum()
    rule = addendum["tie_break"]["rule"]
    assert len(rule) == 2
    assert "misses_per_decode_step" in rule[0]
    assert "config_id" in rule[1]


def test_secondary_budgets_cannot_affect_selection():
    addendum = _addendum()
    sb = addendum["secondary_budget_reporting"]
    assert sb["budgets_bytes"] == [1610612736, 8589934592]
    assert "MUST NOT change" in sb["explicit_prohibition"]
    sel_rule = addendum["selection_budget"]["secondary_budget_non_influence"]
    assert "zero weight" in sel_rule


def test_no_dev_result_existed_before_amendment_freeze():
    freeze = _freeze()
    assert freeze["no_dev_result_existed_before_this_freeze"] is True
    # Structural check: no file in the bridge directory contains a computed
    # DEV retention metric at the time this test suite was authored.
    forbidden_keys = ("mean_dev_miss_bytes_per_decode_step_at_4gib_computed",
                       "dev_tournament_results", "selected_config_frozen")
    for path in BRIDGE_DIR.glob("*.json"):
        if path.name in ("DEV-SELECTION-RULE-v1.json", "PHASE0A-DEV-SELECTION-RULE-FREEZE.json"):
            continue
        content = path.read_text()
        for key in forbidden_keys:
            assert key not in content, f"{path.name} unexpectedly contains {key}"


def test_dev_progression_gate_outcomes_are_exactly_two_and_distinct_from_holdout_labels():
    addendum = _addendum()
    labels = {o["label"] for o in addendum["dev_progression_gate"]["outcomes"]}
    assert labels == {"DEV_RETENTION_SIGNAL_POSITIVE", "DEV_NO_RETENTION_IMPROVEMENT"}
    # must not collide with the holdout-stage labels
    holdout_labels = {"BRIDGE_NEGATIVE", "BRIDGE_POSITIVE_BUT_WEAK", "BRIDGE_POSITIVE"}
    assert labels.isdisjoint(holdout_labels)
