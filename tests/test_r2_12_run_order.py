"""Tests for the frozen R2-12 run_order.json: ABBA pairing and opening-policy balance.

This protects against the exact bias bug caught and fixed during Phase 0
authoring (policy A opening every single cell), by asserting the balance
programmatically rather than trusting a one-off visual check.
"""
import json
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
RUN_ORDER_PATH = REPO_ROOT / "experiments" / "R2-12-runtime-validation" / "run_order.json"


def _load():
    return json.loads(RUN_ORDER_PATH.read_text(encoding="utf-8"))


def test_twelve_cells_present():
    ro = _load()
    assert len(ro["cell_sequence_table"]) == 12


def test_opening_policy_is_balanced_six_six():
    ro = _load()
    openings = [cell["opening_policy"] for cell in ro["cell_sequence_table"]]
    assert openings.count("A") == 6
    assert openings.count("B") == 6
    check = ro["opening_policy_balance_check"]
    assert check["n_cells_opening_A"] == 6
    assert check["n_cells_opening_B"] == 6
    assert check["balanced"] is True


def test_each_cell_sequence_is_abba_or_its_mirror():
    ro = _load()
    abba = ["A", "B", "B", "A", "A", "B", "B", "A", "A", "B"]
    mirror = ["B", "A", "A", "B", "B", "A", "A", "B", "B", "A"]
    for cell in ro["cell_sequence_table"]:
        assert cell["sequence"] in (abba, mirror)
        assert cell["sequence"][0] == cell["opening_policy"]


def test_each_sequence_has_five_a_and_five_b():
    ro = _load()
    for cell in ro["cell_sequence_table"]:
        seq = cell["sequence"]
        assert seq.count("A") == 5
        assert seq.count("B") == 5


def test_frozen_before_execution():
    ro = _load()
    assert ro["frozen_before_execution"] is True
    assert ro["deterministic_no_rng_required"] is True


def test_policy_labels_match_frozen_assignment():
    ro = _load()
    assert ro["policy_labels"]["A"] == "B_selected(M)"
    assert ro["policy_labels"]["B"] == "PredictedProtectPolicy"
