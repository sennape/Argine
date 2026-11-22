"""Unit tests for tools/r2_12_primary_analysis.py.

Runs against the REAL canonical 4 GiB acquisition already on disk (read-only;
no model inference, no mutation of canonical records). Verifies integrity,
output-equivalence, and the statistical/classification pipeline produce the
expected shape and self-consistent results, and that the frozen bootstrap
implementation is reused unmodified.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT))
sys.path.insert(0, str(REPO_ROOT / "tools"))

import r2_12_benchmark_driver as drv  # noqa: E402
import r2_12_primary_analysis as analysis  # noqa: E402


def run_analysis_unit_tests() -> dict:
    results = {}
    ok = True

    frozen = drv.load_frozen_protocol()
    records = analysis.load_canonical_records()
    results["canonical_records_loaded"] = len(records)
    ok = ok and len(records) > 0

    integrity = analysis.verify_integrity(records, frozen)
    results["integrity_n_canonical"] = integrity["n_canonical_records_found"]
    results["integrity_no_missing_or_unexpected"] = (
        integrity["missing_run_ids"] == [] and integrity["unexpected_run_ids"] == []
    )
    ok = ok and results["integrity_no_missing_or_unexpected"]

    oe = analysis.check_output_equivalence(records)
    results["output_equivalence_schema_ok"] = {"n_cells_checked", "n_matching", "n_mismatching",
                                                "primary_performance_comparison_admissible"} <= set(oe.keys())
    ok = ok and results["output_equivalence_schema_ok"]

    stats = analysis.compute_primary_statistics(records)
    results["stats_has_bootstrap_ci"] = "bootstrap_95ci" in stats and {"lower", "upper"} <= set(stats["bootstrap_95ci"].keys())
    results["stats_ci_lower_le_upper"] = stats["bootstrap_95ci"]["lower"] <= stats["bootstrap_95ci"]["upper"]
    results["stats_uses_frozen_bootstrap_params"] = (
        stats["bootstrap_95ci"]["resamples"] == 100000 and stats["bootstrap_95ci"]["seed"] == 42
    )
    ok = ok and results["stats_has_bootstrap_ci"] and results["stats_ci_lower_le_upper"] and results["stats_uses_frozen_bootstrap_params"]

    # Determinism: re-running the bootstrap with the same seed/inputs must give
    # the exact same CI (the frozen implementation must be deterministic).
    stats2 = analysis.compute_primary_statistics(records)
    results["bootstrap_deterministic_across_reruns"] = (
        stats["bootstrap_95ci"]["lower"] == stats2["bootstrap_95ci"]["lower"]
        and stats["bootstrap_95ci"]["upper"] == stats2["bootstrap_95ci"]["upper"]
    )
    ok = ok and results["bootstrap_deterministic_across_reruns"]

    classification = analysis.classify(stats, oe)
    results["classification_is_one_of_frozen_labels"] = classification["classification"] in (
        "R2_12_PASS", "R2_12_FAIL", "R2_12_INCONCLUSIVE", "R2_12_BLOCKED_INVALID")
    ok = ok and results["classification_is_one_of_frozen_labels"]

    # If output-equivalence has zero mismatches and delta<=0, classification MUST be R2_12_FAIL
    # (never silently overridden) -- this is the actual preregistered rule, checked structurally.
    if oe["primary_performance_comparison_admissible"] and stats["primary_aggregate_statistic"]["observed_mean_delta_tps"] <= 0:
        results["fail_classification_consistent_with_rule"] = classification["classification"] == "R2_12_FAIL"
        ok = ok and results["fail_classification_consistent_with_rule"]

    continuation = analysis.continuation_rule()
    results["continuation_rule_has_source"] = bool(continuation.get("source"))
    ok = ok and results["continuation_rule_has_source"]

    return {"ok": ok, "sub_results": results}


if __name__ == "__main__":
    r = run_analysis_unit_tests()
    print(json.dumps(r, indent=2))
    sys.exit(0 if r["ok"] else 1)
