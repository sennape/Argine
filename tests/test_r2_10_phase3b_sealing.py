"""R2-10 Phase 3B: structural checks on the EVAL_CAPTURED_SEALED provenance
record. No model execution, no EVAL performance computation, no EVAL_OPENED
transition anywhere in this file.
"""
from __future__ import annotations

import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SEALED_PATH = ROOT / "experiments/R2-10-working-set-predictor/PHASE3B-EVAL-CAPTURE-SEALED.json"
MANIFEST_PATH = ROOT / "experiments/R2-09-prediction-benchmark/eval_manifest.json"

SEALED = json.loads(SEALED_PATH.read_text(encoding="utf-8"))
FROZEN_MODEL_SHA = "a015794bfb1d69cb03dbb86b185fb2b9b339f757df5f8f9dd9ebdab8f6ed5d32"
FROZEN_RUNTIME_SHA = "a0063720ec4bb5d1ba3e4f7c0387dd1bc6a239b0ce038ba3d755351dab3f5cf3"


def test_overlay_state_is_sealed_not_opened():
    assert SEALED["overlay_state"] == "EVAL_CAPTURED_SEALED"
    assert "EVAL_OPENED" not in json.dumps(SEALED["acquisition_state_history"])


def test_eval_manifest_untouched():
    raw = MANIFEST_PATH.read_bytes()
    import hashlib
    actual = hashlib.sha256(raw).hexdigest()
    assert actual == SEALED["eval_manifest_json_sha256_before_this_record"]
    assert actual == SEALED["eval_manifest_json_sha256_after_this_record"]
    assert SEALED["eval_manifest_json_modified"] is False
    assert json.loads(raw)["eval_visibility_state"] == "EVAL_LOCKED"


def test_four_distinct_cells_all_clean():
    cells = SEALED["cells"]
    assert len(cells) == 4
    ids = {c["workload_id"] for c in cells}
    assert ids == {"coding-eval-v1", "reasoning-eval-v1", "summarization-eval-v1",
                   "instruction-eval-v1"}
    sha_values = [c["trace_sha256_recomputed"] for c in cells]
    assert len(set(sha_values)) == 4, "duplicate trace identity"
    digests = [c["normalized_trace_digest"] for c in cells]
    assert len(set(digests)) == 4, "duplicate normalized trace digest"
    for c in cells:
        assert c["reconcile_result"] == []
        assert c["normalize_succeeded"] is True
        assert c["excluded_row_count"] == 0
        assert c["split_is_eval_not_dev"] is True
        assert c["seed_recorded_in_manifest"] == 42
        assert c["model_fingerprint_matches_frozen"] is True
        assert c["model_fingerprint_sha256"] == FROZEN_MODEL_SHA
        assert c["runtime_fingerprint_matches_frozen"] is True
        assert c["runtime_fingerprint_sha256"] == FROZEN_RUNTIME_SHA
        assert c["expert_store_budget_matches_frozen_4096_mib"] is True
        assert c["n_experts"] == 128
        assert c["top_k"] == 8
        assert c["n_layers"] == 48
        assert c["all_expert_ids_in_valid_range"] is True
        assert all(c["capture_params_match_frozen_acquisition_plan"].values())


def test_publication_quality_warning_classified_a_not_c():
    inv = SEALED["publication_quality_warning_investigation"]
    assert inv["classification"] == "A"
    assert inv["verified_identical_across_all_four_captures"] is True
    assert inv["missing_list_contents_for_all_four"] == ["argine2 commit"]


def test_failed_prelaunch_attempt_empty_and_excluded():
    fp = SEALED["failed_prelaunch_attempt"]
    assert fp["classification"] == "PRELAUNCH_RUNTIME_PATH_FAILURE"
    assert fp["contains_valid_scientific_trace"] is False
    assert fp["file_count"] == 0
    assert fp["excluded_from_sealing"] is True


def test_no_eval_performance_data_anywhere_in_record():
    # "EVAL_OPENED" legitimately appears in prose stating it has NOT
    # occurred (checked precisely by test_overlay_state_is_sealed_not_opened,
    # which asserts it is absent from acquisition_state_history specifically).
    # This test only guards against actual EVAL performance numbers leaking in.
    blob = json.dumps(SEALED)
    for forbidden in ("recall_at_k", "miss_bytes", "tokens_per_second"):
        assert forbidden not in blob, f"sealing record must not contain {forbidden!r}"
