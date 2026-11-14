"""R2-11 freshness audit: zero violations against R2-10 DEV/EVAL and R2-BRIDGE-01."""
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

REPO_ROOT = Path(__file__).resolve().parents[1]
AUDIT_PATH = REPO_ROOT / "experiments" / "R2-11-predictive-retention" / "freshness_audit.json"


def _load():
    return json.loads(AUDIT_PATH.read_text(encoding="utf-8"))


def test_freshness_audit_passes():
    d = _load()
    assert d["pass"] is True
    assert d["violations"] == []


def test_freshness_audit_compared_all_required_corpora():
    d = _load()
    compared = d["corpora_compared_against"]
    joined = " ".join(compared)
    assert "r2_10_dev" in joined
    assert "r2_10_eval" in joined
    assert "r2_bridge01_holdout" in joined
    assert "r2_11_new" in joined


def test_freshness_audit_counts_match_12_new_workloads():
    d = _load()
    assert d["counts"]["r2_11_new_items"] == 12


def test_no_model_output_inspected_statement_present():
    d = _load()
    assert "No model output was inspected" in d["method"]
