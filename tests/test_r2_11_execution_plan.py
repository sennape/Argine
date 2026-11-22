"""R2-11 execution plan / acquisition plan / instrumentation dependency sanity checks."""
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

REPO_ROOT = Path(__file__).resolve().parents[1]
OUT = REPO_ROOT / "experiments" / "R2-11-predictive-retention"


def test_expected_evaluation_matrix_is_72_cells():
    d = json.loads((OUT / "execution_plan.json").read_text(encoding="utf-8"))
    m = d["expected_future_evaluation_matrix"]
    assert m["workloads"] == 12
    assert m["budgets_bytes"] == [1610612736, 4294967296, 8589934592]
    assert len(m["policies"]) == 2
    assert m["primary_scientific_policy_cells"] == 72


def test_state_machine_matches_frozen_sequence():
    d = json.loads((OUT / "execution_plan.json").read_text(encoding="utf-8"))
    assert d["state_machine"] == [
        "R2_11_PROTOCOL_FROZEN",
        "R2_11_ACQUISITION_PLANNED",
        "R2_11_CAPTURED_SEALED",
        "R2_11_EVAL_OPENED",
        "R2_11_EVALUATED_CLOSED",
    ]
    assert d["state_machine_rules"]["no_retention_result_may_be_computed_before_R2_11_EVAL_OPENED"] is True


def test_memory_correctness_requirements_present():
    d = json.loads((OUT / "execution_plan.json").read_text(encoding="utf-8"))
    reqs = " ".join(d["memory_correctness_requirements"])
    assert "budget violations = 0" in reqs
    assert "deterministic eviction" in reqs
    assert "fails closed" in reqs


def test_acquisition_plan_model_runtime_identity():
    d = json.loads((OUT / "acquisition_plan.json").read_text(encoding="utf-8"))
    assert d["model_identity"]["name"] == "Qwen3-30B-A3B-Q4_K_M"
    assert d["model_identity"]["sha256"] == "a015794bfb1d69cb03dbb86b185fb2b9b339f757df5f8f9dd9ebdab8f6ed5d32"
    assert d["runtime_identity"]["name"] == "llama-research-v2"
    conds = d["deterministic_capture_conditions"]
    assert conds == {
        "seed": 42, "context": 4096, "threads": 4, "threads_batch": 12,
        "temperature": 0.0, "top_k": 1, "n_predict": 512,
        "expert_store_capture_budget": "4G",
    }


def test_acquisition_plan_no_capture_performed():
    d = json.loads((OUT / "acquisition_plan.json").read_text(encoding="utf-8"))
    assert "No R2-11 trace capture executed." in d["explicitly_not_performed_in_this_phase"]
    assert d["state"] == "R2_11_ACQUISITION_PLANNED"


def test_instrumentation_dependency_references_correct_repair_commit():
    d = json.loads((OUT / "instrumentation_dependency.json").read_text(encoding="utf-8"))
    assert d["repair_commit"] == "dbe8306"
    assert "trace_completeness" in json.dumps(d)
