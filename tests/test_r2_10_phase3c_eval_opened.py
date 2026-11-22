"""R2-10 Phase 3C: structural checks on the EVAL_OPENED provenance record.

No EVAL performance metric may appear in this record -- it marks the
opening boundary, strictly before any metric computation.
"""
from __future__ import annotations

import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
OPENED_PATH = ROOT / "experiments/R2-10-working-set-predictor/PHASE3C-EVAL-OPENED.json"
MANIFEST_PATH = ROOT / "experiments/R2-09-prediction-benchmark/eval_manifest.json"

OPENED = json.loads(OPENED_PATH.read_text(encoding="utf-8"))


def test_overlay_state_is_opened():
    assert OPENED["overlay_state"] == "EVAL_OPENED"
    assert OPENED["supersedes_overlay_state"] == "EVAL_CAPTURED_SEALED"


def test_eval_manifest_untouched():
    import hashlib
    raw = MANIFEST_PATH.read_bytes()
    actual = hashlib.sha256(raw).hexdigest()
    assert actual == OPENED["eval_manifest_json_sha256_before_this_record"]
    assert actual == OPENED["eval_manifest_json_sha256_after_this_record"]
    assert OPENED["eval_manifest_json_modified"] is False
    assert json.loads(raw)["eval_visibility_state"] == "EVAL_LOCKED"


def test_selected_predictor_frozen_before_opening():
    sp = OPENED["selected_predictor_config_frozen_before_opening"]
    assert sp["config_id"] == "decayed_transition/decay=0.99"
    assert sp["preregistration_committed_before_opening"] is True


def test_no_eval_performance_data_present():
    # "recall_at_k_h1" etc. legitimately appear as part of the frozen
    # metric NAME (e.g. "future_requested_expert_recall_at_k, h=1
    # (recall_at_k_h1)") copied from the pre-registration -- that is a
    # metric definition, not a computed value. This test instead checks
    # that no actual numeric EVAL result field (as later artifacts will
    # name them) is present.
    blob = json.dumps(OPENED)
    for forbidden in ("mean_recall_h1", "mean_recall_h4", "mean_recall_h16",
                      "eval_recall_at_k", "baseline_results"):
        assert forbidden not in blob, f"EVAL_OPENED record must not contain {forbidden!r}"
    assert OPENED["no_eval_result_inspected_before_opening"] is True
    assert "cells" not in OPENED, "EVAL_OPENED record must not carry per-cell EVAL results"


def test_four_sealed_trace_identities_present_and_distinct():
    ids = OPENED["sealed_trace_identities"]
    assert len(ids) == 4
    shas = {c["trace_sha256"] for c in ids}
    assert len(shas) == 4
