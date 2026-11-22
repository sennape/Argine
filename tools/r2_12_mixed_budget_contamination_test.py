"""Regression test for the cross-budget contamination bug reported 2026-11-16:
tools/r2_12_primary_analysis.py's load_canonical_records() used to scan for
canonical:True with no budget_gib filter, so once more than one budget's
canonical records exist as sibling files under the same RESULTS_DIR, the
4 GiB primary analysis silently pulled in other budgets' records too.

Part 1 (synthetic fixture, isolated temp RESULTS_DIR): proves the loader
itself is budget-scoped using a fabricated mixed-budget record set
containing 1.5 / 4.0 / 8.0 GiB records side by side (8.0 GiB canonical
data does not exist for real yet, so it must be fabricated here to be
tested at all).

Part 2 (real on-disk data, read-only): proves the repaired loader, run
against the REAL RESULTS_DIR where both 4.0 and 1.5 GiB canonical records
now genuinely coexist, reproduces the already-committed 4 GiB official
statistics and R2_12_FAIL classification exactly, and that the committed
JSON/Markdown artifact files are untouched (byte-identical before/after)
by this verification.

Zero model inference anywhere in this file. No canonical record, frozen
contract, or committed artifact is written to by this file.

Entry point: run_mixed_budget_contamination_tests() -> {"ok": bool, ...}.
Also runnable standalone: `python3 tools/r2_12_mixed_budget_contamination_test.py`.
"""
from __future__ import annotations

import hashlib
import json
import sys
import tempfile
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT))
sys.path.insert(0, str(REPO_ROOT / "tools"))

import r2_12_benchmark_driver as drv  # noqa: E402
import r2_12_primary_analysis as pa  # noqa: E402


def _fake_record(run_id: str, budget_gib: float, policy_id: str, workload_id: str,
                  repetition_index: int, counted: bool, tps: float) -> dict:
    return {
        "run_id": run_id,
        "canonical": True,
        "budget_gib": budget_gib,
        "policy_id": policy_id,
        "workload_id": workload_id,
        "repetition_index": repetition_index,
        "counted": counted,
        "warmup": not counted,
        "ok": True,
        "cell_index": 0,
        "ordering_block": "warmup" if not counted else "counted",
        "position_in_abba_sequence": repetition_index,
        "environment": {},
        "runtime_metrics": {
            "decode_tokens_per_second": tps,
            "ttft_seconds": 0.01,
            "end_to_end_time_to_final_token_seconds": 1.0,
            "budget_violations": 0,
            "peak_resident_memory_bytes": 1,
            "tokens_generated": 512,
        },
    }


def _build_synthetic_mixed_budget_fixture(tmp_results_dir: Path) -> dict:
    """Writes a fabricated mixed-budget record set (1.5 / 4.0 / 8.0 GiB)
    into tmp_results_dir. Returns counts actually written per budget."""
    workloads = ["r2-11-coding-v1", "r2-11-instruction-v1", "r2-11-reasoning-v1", "r2-11-summarization-v1"]
    counts = {1.5: 0, 4.0: 0, 8.0: 0}
    for budget_gib in (1.5, 4.0, 8.0):
        for wl in workloads:
            for policy_id in ("b_selected", "predicted_protect"):
                for rep in range(5):
                    run_id = f"FAKE-{budget_gib}-{wl}-{policy_id}-{rep}"
                    rec = _fake_record(run_id, budget_gib, policy_id, wl, rep, counted=True, tps=10.0 + rep)
                    (tmp_results_dir / f"{run_id}.json").write_text(json.dumps(rec))
                    counts[budget_gib] += 1
        # 8 warmups per budget too, so it looks like a real campaign shape
        # (40 counted + 8 warmup = 48, matching the real 4 GiB/1.5 GiB shape).
        for i in range(8):
            run_id = f"FAKE-{budget_gib}-warmup-{i}"
            rec = _fake_record(run_id, budget_gib, "b_selected", workloads[0], 100 + i, counted=False, tps=1.0)
            (tmp_results_dir / f"{run_id}.json").write_text(json.dumps(rec))
            counts[budget_gib] += 1
    return counts


def part1_synthetic_fixture_contamination_proof(results: dict) -> bool:
    ok = True
    with tempfile.TemporaryDirectory() as tmp:
        tmp_results_dir = Path(tmp)
        _build_synthetic_mixed_budget_fixture(tmp_results_dir)

        old_results_dir = pa.RESULTS_DIR
        pa.RESULTS_DIR = tmp_results_dir
        try:
            recs_4 = pa.load_canonical_records(budget_gib=4.0)
            recs_15 = pa.load_canonical_records(budget_gib=1.5)
            recs_8 = pa.load_canonical_records(budget_gib=8.0)
        finally:
            pa.RESULTS_DIR = old_results_dir

        # 1. the primary loader returns only 4 GiB records
        check_1 = all(r.get("budget_gib") == 4.0 for r in recs_4.values())
        results["1_loader_returns_only_4gib"] = {"ok": check_1, "n": len(recs_4)}
        ok = ok and check_1

        # 2. exactly 48 primary records are selected (40 counted + 8 warmup, per cell shape)
        check_2 = len(recs_4) == 48
        results["2_exactly_48_primary_records_selected"] = {"ok": check_2, "n": len(recs_4)}
        ok = ok and check_2

        # 3. 1.5 GiB records cannot contaminate the 4 GiB analysis
        check_3 = (len(set(recs_4) & set(recs_15)) == 0
                   and all(r.get("budget_gib") != 1.5 for r in recs_4.values()))
        results["3_1_5gib_cannot_contaminate_4gib"] = {"ok": check_3}
        ok = ok and check_3

        # 4. 8 GiB records cannot contaminate the 4 GiB analysis
        check_4 = (len(set(recs_4) & set(recs_8)) == 0
                   and all(r.get("budget_gib") != 8.0 for r in recs_4.values()))
        results["4_8gib_cannot_contaminate_4gib"] = {"ok": check_4}
        ok = ok and check_4

        # Extra: prove the OLD (unfiltered) behavior really would have been
        # contaminated, so this regression test actually exercises the bug.
        old_behavior_records = {}
        for f in tmp_results_dir.glob("*.json"):
            d = json.loads(f.read_text())
            if d.get("canonical") is True:
                old_behavior_records[d["run_id"]] = d
        would_have_been_contaminated = len(old_behavior_records) == 48 * 3
        results["sanity_old_unfiltered_behavior_was_contaminated"] = {
            "ok": would_have_been_contaminated, "n": len(old_behavior_records),
        }
        ok = ok and would_have_been_contaminated

        # And prove the 1.5/8.0 loaders are each internally self-consistent too.
        check_15 = len(recs_15) == 48 and all(r.get("budget_gib") == 1.5 for r in recs_15.values())
        check_8 = len(recs_8) == 48 and all(r.get("budget_gib") == 8.0 for r in recs_8.values())
        results["sanity_1_5gib_and_8gib_each_self_consistent"] = {"ok": check_15 and check_8}
        ok = ok and check_15 and check_8

    return ok


def part2_real_data_reproduction_and_untouched_artifact_proof(results: dict) -> bool:
    ok = True
    committed_json_path = drv.R2_12_DIR / "R2_12_4GIB_PRIMARY_FORMAL_ANALYSIS.json"
    committed_md_path = drv.R2_12_DIR / "R2_12_4GIB_PRIMARY_FORMAL_ANALYSIS.md"

    def _sha256(p: Path) -> str:
        return hashlib.sha256(p.read_bytes()).hexdigest()

    before_json_hash = _sha256(committed_json_path)
    before_md_hash = _sha256(committed_md_path)

    committed = json.loads(committed_json_path.read_text())

    # Recompute using the REAL on-disk RESULTS_DIR (now containing both 4.0
    # and 1.5 GiB canonical data) and the REPAIRED, budget-scoped loader --
    # written to TEMPORARY variables only, nothing is written to disk here.
    frozen = drv.load_frozen_protocol()
    recs_4 = pa.load_canonical_records(budget_gib=4.0)
    integrity = pa.verify_integrity(recs_4, frozen)
    oe = pa.check_output_equivalence(recs_4)
    stats = pa.compute_primary_statistics(recs_4)
    classification = pa.classify(stats, oe)

    # 5. the existing 4 GiB statistics reproduce the committed artifact exactly
    committed_stats = committed["official_primary_statistics"]
    stats_match = stats == committed_stats
    results["5_recomputed_stats_match_committed_artifact_exactly"] = {"ok": stats_match}
    if not stats_match:
        # Report the structured scientific fields explicitly rather than
        # silently failing, per the task's own fallback instruction.
        results["5_recomputed_stats_match_committed_artifact_exactly"]["diff_fields"] = {
            "recomputed_aggregate_delta": stats["primary_aggregate_statistic"]["observed_mean_delta_tps"],
            "committed_aggregate_delta": committed_stats["primary_aggregate_statistic"]["observed_mean_delta_tps"],
            "recomputed_ci": stats["bootstrap_95ci"],
            "committed_ci": committed_stats["bootstrap_95ci"],
        }
    ok = ok and stats_match

    integrity_match = integrity == committed["canonical_acquisition_integrity"]
    results["5b_recomputed_integrity_matches_committed"] = {"ok": integrity_match}
    ok = ok and integrity_match

    oe_match = oe == committed["output_equivalence"]
    results["5c_recomputed_output_equivalence_matches_committed"] = {"ok": oe_match}
    ok = ok and oe_match

    # 6. the frozen primary classification remains R2_12_FAIL
    classification_ok = (classification["classification"] == "R2_12_FAIL"
                          == committed["primary_classification"]["classification"])
    results["6_classification_remains_R2_12_FAIL"] = {
        "ok": classification_ok, "classification": classification["classification"],
    }
    ok = ok and classification_ok

    # Also pin the exact numeric values the task asked to verify.
    agg = stats["primary_aggregate_statistic"]["observed_mean_delta_tps"]
    ci_lo, ci_hi = stats["bootstrap_95ci"]["lower"], stats["bootstrap_95ci"]["upper"]
    numeric_ok = (round(agg, 6) == -0.171721 and round(ci_lo, 6) == -0.259917 and round(ci_hi, 6) == -0.091642
                  and integrity["n_canonical_records_found"] == 48
                  and integrity["n_warmup"] == 8 and integrity["n_counted"] == 40)
    results["6b_exact_published_numbers_reproduced"] = {
        "ok": numeric_ok, "aggregate_delta": agg, "ci": [ci_lo, ci_hi],
        "n_canonical": integrity["n_canonical_records_found"],
        "n_warmup": integrity["n_warmup"], "n_counted": integrity["n_counted"],
    }
    ok = ok and numeric_ok

    # 7. the committed 4 GiB JSON/Markdown artifact files remain byte-identical
    # before/after this repair -- re-hash the SAME committed files (never
    # rewritten by this test or by the repair) and compare.
    after_json_hash = _sha256(committed_json_path)
    after_md_hash = _sha256(committed_md_path)
    byte_identical = before_json_hash == after_json_hash and before_md_hash == after_md_hash
    results["7_committed_artifact_files_byte_identical_before_after"] = {
        "ok": byte_identical,
        "json_sha256": after_json_hash,
        "md_sha256": after_md_hash,
    }
    ok = ok and byte_identical

    return ok


def run_mixed_budget_contamination_tests() -> dict:
    results: dict = {}
    ok = True
    ok = ok and part1_synthetic_fixture_contamination_proof(results)
    ok = ok and part2_real_data_reproduction_and_untouched_artifact_proof(results)
    return {"ok": ok, "sub_results": results}


if __name__ == "__main__":
    print(json.dumps(run_mixed_budget_contamination_tests(), indent=2, default=str))
    sys.exit(0 if run_mixed_budget_contamination_tests()["ok"] else 1)
