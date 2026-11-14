"""Tests for the frozen R2-12 instrumentation_contract.json."""
import json
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
PATH = REPO_ROOT / "experiments" / "R2-12-runtime-validation" / "instrumentation_contract.json"


def _load():
    return json.loads(PATH.read_text(encoding="utf-8"))


def test_eight_fields_recorded_per_run():
    ic = _load()
    assert len(ic["fields_recorded_per_run"]) == 8


def test_overhead_never_assumed_free():
    ic = _load()
    assert "NEVER assumed free" in ic["overhead_must_be_measured_or_bounded"]


def test_minimal_mode_requires_consistency_proof_before_use():
    ic = _load()
    req = ic["minimal_instrumentation_mode_requirement"]["if_available"]
    assert "must be proven" in req.lower()
    assert "consistency" in req.lower()


def test_minimal_mode_fallback_is_symmetric_not_silently_assumed_away():
    ic = _load()
    fallback = ic["minimal_instrumentation_mode_requirement"]["if_not_available_or_not_provably_consistent"]
    assert "symmetrically for BOTH policies" in fallback
    assert "rather than silently assumed away" in fallback


def test_no_mixing_instrumentation_modes_within_a_comparison():
    ic = _load()
    rule = ic["no_mixing_instrumentation_modes_within_a_comparison"]
    assert "forbidden" in rule.lower()


def test_scientific_behavior_verified_separately_reusing_r2_11_machinery():
    ic = _load()
    sep = ic["separate_scientific_behavior_verification"]
    assert "trace_contract.normalize/reconcile" in sep
    assert "trace_completeness" in sep


def test_fields_include_both_timing_and_expert_io_counters():
    ic = _load()
    fields = set(ic["fields_recorded_per_run"])
    for f in ("ttft_seconds", "decode_duration_seconds", "expert_store_bytes_read", "expert_store_reads"):
        assert f in fields
