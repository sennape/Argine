"""Regression tests for tools/r2_12_secondary_budget_analysis.py, covering
both secondary budgets it supports (1.5 GiB stress, 8.0 GiB null/control).

Checks, per budget, against the REAL on-disk canonical records (read-only):
  - exactly 48 canonical records, 8 warmup / 40 counted;
  - output-equivalence is admissible (0 mismatching cells);
  - no R2_12_PASS/FAIL/INCONCLUSIVE label is ever assigned (per
    decision_rules.json, no secondary-budget classification scheme exists);
  - the analysis is deterministic across independent reruns (bootstrap
    seed 42, resamples 100000);
  - where a committed artifact already exists for that budget
    (R2_12_<LABEL>_SECONDARY_FORMAL_ANALYSIS.json), the freshly recomputed
    statistics/integrity match it exactly, and the committed file itself is
    left byte-identical (hashed before and after).

Zero model inference. Nothing is written to any committed path by this file;
all re-runs use a temporary --out-dir.

Entry point: run_secondary_budget_analysis_tests() -> {"ok": bool, ...}.
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
import r2_12_secondary_budget_analysis as sba  # noqa: E402


def _sha256(p: Path) -> str:
    return hashlib.sha256(p.read_bytes()).hexdigest()


def _check_budget(budget_gib: float, results: dict) -> bool:
    ok = True
    label = drv.BUDGET_FILENAME_LABELS[budget_gib]
    committed_json = drv.R2_12_DIR / f"R2_12_{label}_SECONDARY_FORMAL_ANALYSIS.json"
    committed_md = drv.R2_12_DIR / f"R2_12_{label}_SECONDARY_FORMAL_ANALYSIS.md"
    committed_exists = committed_json.exists() and committed_md.exists()

    before_hashes = None
    if committed_exists:
        before_hashes = (_sha256(committed_json), _sha256(committed_md))

    recs = sba.load_canonical_records_for_budget(budget_gib)
    n_ok = (len(recs) == 48
            and sum(1 for r in recs.values() if r.get("warmup")) == 8
            and sum(1 for r in recs.values() if r.get("counted")) == 40
            and all(r.get("budget_gib") == budget_gib for r in recs.values()))
    results[f"{budget_gib}gib_48_8_40"] = {"ok": n_ok, "n": len(recs)}
    ok = ok and n_ok

    with tempfile.TemporaryDirectory() as tmp1, tempfile.TemporaryDirectory() as tmp2:
        integrity = sba.verify_integrity_for_budget(recs, drv.load_frozen_protocol(), budget_gib)
        oe = sba.primary.check_output_equivalence(recs)
        stats = sba.primary.compute_primary_statistics(recs)
        interp = sba.secondary_interpretation(budget_gib)

        oe_ok = oe["n_mismatching"] == 0 and oe["primary_performance_comparison_admissible"] is True
        results[f"{budget_gib}gib_output_equivalence_admissible"] = {"ok": oe_ok}
        ok = ok and oe_ok

        no_label_ok = interp["no_R2_12_classification_label_assigned"] is True
        results[f"{budget_gib}gib_no_classification_label"] = {"ok": no_label_ok}
        ok = ok and no_label_ok

        # Determinism: run the real CLI entry point twice into independent
        # temp dirs and compare (minus the timestamp).
        import subprocess
        for tmp, tag in ((tmp1, "a"), (tmp2, "b")):
            subprocess.run([sys.executable, str(REPO_ROOT / "tools" / "r2_12_secondary_budget_analysis.py"),
                             "--budget-gib", str(budget_gib), "--out-dir", tmp],
                            check=True, capture_output=True, text=True, cwd=str(REPO_ROOT))
        out_a = json.loads((Path(tmp1) / f"R2_12_{label}_SECONDARY_FORMAL_ANALYSIS.json").read_text())
        out_b = json.loads((Path(tmp2) / f"R2_12_{label}_SECONDARY_FORMAL_ANALYSIS.json").read_text())
        out_a.pop("analysis_timestamp_utc", None)
        out_b.pop("analysis_timestamp_utc", None)
        deterministic_ok = out_a == out_b
        results[f"{budget_gib}gib_deterministic_rerun"] = {"ok": deterministic_ok}
        ok = ok and deterministic_ok

        if committed_exists:
            committed = json.loads(committed_json.read_text())
            stats_match = out_a["official_secondary_statistics"] == committed["official_secondary_statistics"]
            integrity_match = out_a["canonical_acquisition_integrity"] == committed["canonical_acquisition_integrity"]
            results[f"{budget_gib}gib_matches_committed_artifact"] = {
                "ok": stats_match and integrity_match,
            }
            ok = ok and stats_match and integrity_match

            after_hashes = (_sha256(committed_json), _sha256(committed_md))
            untouched_ok = before_hashes == after_hashes
            results[f"{budget_gib}gib_committed_artifact_untouched"] = {"ok": untouched_ok}
            ok = ok and untouched_ok
        else:
            results[f"{budget_gib}gib_matches_committed_artifact"] = {
                "ok": True, "note": "no committed artifact yet for this budget -- skipped",
            }

    return ok


def run_secondary_budget_analysis_tests() -> dict:
    results: dict = {}
    ok = True
    for budget_gib in sba.SECONDARY_BUDGETS:
        ok = _check_budget(budget_gib, results) and ok
    return {"ok": ok, "sub_results": results}


if __name__ == "__main__":
    out = run_secondary_budget_analysis_tests()
    print(json.dumps(out, indent=2, default=str))
    sys.exit(0 if out["ok"] else 1)
