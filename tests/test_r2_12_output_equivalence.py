"""Tests for the frozen R2-12 output_equivalence.json contract."""
import json
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
PATH = REPO_ROOT / "experiments" / "R2-12-runtime-validation" / "output_equivalence.json"


def _load():
    return json.loads(PATH.read_text(encoding="utf-8"))


def test_frozen_before_execution():
    oe = _load()
    assert oe["frozen_before_execution"] is True


def test_deterministic_generation_conditions_fixed():
    oe = _load()
    fixed = oe["fixed_for_comparison"]
    assert fixed["seed"] == 42
    assert fixed["temperature"] == 0.0
    assert fixed["top_k"] == 1
    assert fixed["context"] == 4096
    assert fixed["n_predict"] == 512


def test_exact_equality_required_by_default():
    oe = _load()
    req = oe["equivalence_check"]["requirement"]
    assert "EXACT equality" in req


def test_divergence_routes_to_blocked_invalid():
    oe = _load()
    on_div = oe["equivalence_check"]["on_divergence"]
    assert "BLOCKED_INVALID" in on_div
    assert "decision_rules.json" in on_div


def test_checked_once_per_workload_budget_pair_not_per_repetition():
    oe = _load()
    where = oe["equivalence_check"]["where_checked"]
    assert "Once per (workload,budget) pair" in where


def test_deterministic_generation_conditions_match_contract_json():
    contract = json.loads((REPO_ROOT / "experiments" / "R2-12-runtime-validation" / "contract.json").read_text(encoding="utf-8"))
    dgc = contract["deterministic_generation_conditions"]
    oe = _load()
    fixed = oe["fixed_for_comparison"]
    assert dgc["seed"] == fixed["seed"]
    assert dgc["temperature"] == fixed["temperature"]
    assert dgc["top_k"] == fixed["top_k"]
    assert dgc["context"] == fixed["context"]
    assert dgc["n_predict"] == fixed["n_predict"]
