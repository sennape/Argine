"""R2-11's B_selected(M) mapping must match R2-09A-FREEZE.json exactly, never reselected."""
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

REPO_ROOT = Path(__file__).resolve().parents[1]
BASELINE_MAPPING_PATH = REPO_ROOT / "experiments" / "R2-11-predictive-retention" / "baseline_mapping.json"
R2_09A_FREEZE_PATH = REPO_ROOT / "experiments" / "R2-09-prediction-benchmark" / "r2_09a" / "R2-09A-FREEZE.json"


def test_baseline_mapping_matches_r2_09a_freeze_verbatim():
    bm = json.loads(BASELINE_MAPPING_PATH.read_text(encoding="utf-8"))
    freeze = json.loads(R2_09A_FREEZE_PATH.read_text(encoding="utf-8"))
    selected = freeze["selected_baselines"]
    for budget_str, entry in bm["mapping_by_budget_bytes"].items():
        src = selected[budget_str]
        assert entry["family_id"] == src["family_id"]
        assert entry["params"] == src["params"]
        assert entry["config_id"] == src["config_id"]


def test_never_reselect_flag_set():
    bm = json.loads(BASELINE_MAPPING_PATH.read_text(encoding="utf-8"))
    assert bm["never_reselect"] is True


def test_primary_budget_is_4gib():
    bm = json.loads(BASELINE_MAPPING_PATH.read_text(encoding="utf-8"))
    assert bm["primary_decision_budget_bytes"] == 4294967296
    assert set(bm["secondary_robustness_budgets_bytes"]) == {1610612736, 8589934592}
