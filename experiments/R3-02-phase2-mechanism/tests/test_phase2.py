"""R3-02 Phase 2 deterministic analysis tests (no model, read-only evidence)."""

from __future__ import annotations

import json
import random
import statistics
import unittest
from pathlib import Path

REPO = Path(__file__).resolve().parents[3]
PHASE = Path(__file__).resolve().parents[1]
RUN = Path(r"C:\Users\alpel\Desktop\Argine4-r3runs\R3-02-p4")

ANALYSIS = json.loads((PHASE / "ANALYSIS.json").read_text(encoding="utf-8"))
CLASSIFICATION = json.loads((PHASE / "CLASSIFICATION.json").read_text(encoding="utf-8"))

SEED = 20261116
DRAWS = 10000


def bootstrap_median_ci(diffs):
    rng = random.Random(SEED)
    n = len(diffs)
    boots = sorted(statistics.median([diffs[rng.randrange(n)] for _ in range(n)])
                   for _ in range(DRAWS))
    return boots[int(0.025 * DRAWS)], boots[int(0.975 * DRAWS) - 1]


def classify(conditions):
    return "MECHANISM_OBSERVABLE" if all(c["pass"] for c in conditions) else "MECHANISM_INSUFFICIENT"


class TestCompleteness(unittest.TestCase):
    def test_trace_completeness(self):
        self.assertEqual([k for k in ANALYSIS["trajectories"] if k in ("1.5","4","8")], ["1.5","4","8"])
        self.assertEqual(len(ANALYSIS["mechanisms"]), 7)
        self.assertEqual(len(list((RUN / "admissions").glob("trace_count-*.json"))), 7)

    def test_cell_completeness(self):
        self.assertEqual(ANALYSIS["audit"]["cells"], 35)
        self.assertEqual(ANALYSIS["audit"]["cell_admissions"], 35)
        cells = list((RUN / "cells").glob("cell-*.json"))
        self.assertEqual(len(cells), 35)

    def test_no_duplicate_counted_cell(self):
        keys = [p.stem for p in (RUN / "cells").glob("cell-*.json")]
        self.assertEqual(len(keys), len(set(keys)))


class TestPairingAndStatistics(unittest.TestCase):
    def test_pairing_five_reps_per_budget(self):
        for budget in ("1.5", "4", "8"):
            self.assertEqual(len(ANALYSIS["timing"][budget]["a"]), 5)
            self.assertEqual(len(ANALYSIS["timing"][budget]["b"]), 5)

    def test_median_paired_difference(self):
        for budget, d in ANALYSIS["timing"].items():
            self.assertAlmostEqual(statistics.median(d["wall"]["diffs"]), d["wall"]["median"], places=12)

    def test_relative_difference(self):
        for budget, d in ANALYSIS["timing"].items():
            rels = [(b - a) / a for a, b in zip(d["a"], d["b"])]
            self.assertAlmostEqual(statistics.median(rels), d["wall"]["median_relative"], places=12)

    def test_bootstrap_reproducible(self):
        for budget, d in ANALYSIS["timing"].items():
            lo, hi = bootstrap_median_ci(d["wall"]["diffs"])
            self.assertAlmostEqual(lo, d["wall"]["ci_low"], places=12)
            self.assertAlmostEqual(hi, d["wall"]["ci_high"], places=12)

    def test_precision_criterion(self):
        p = ANALYSIS["precision"]["4"]
        self.assertAlmostEqual(p["threshold_1pct_a"], 0.01 * p["median_a"], places=15)
        self.assertEqual(p["bounded"], p["ci_half_width"] <= p["threshold_1pct_a"])
        self.assertFalse(p["bounded"])

    def test_trajectories_equal(self):
        for budget, t in ANALYSIS["trajectory_equality"].items():
            self.assertTrue(t["equal"], budget)


class TestClassificationRule(unittest.TestCase):
    def test_truth_table(self):
        conds = [{"id": i, "pass": True} for i in range(1, 8)]
        self.assertEqual(classify(conds), "MECHANISM_OBSERVABLE")
        conds[5]["pass"] = False
        self.assertEqual(classify(conds), "MECHANISM_INSUFFICIENT")
        conds[0]["pass"] = False
        self.assertEqual(classify(conds), "MECHANISM_INSUFFICIENT")

    def test_recorded_classification(self):
        self.assertEqual(CLASSIFICATION["final_classification"],
                         classify(CLASSIFICATION["conditions"]))
        self.assertEqual(CLASSIFICATION["failed_conditions"], [6])

    def test_fail_closed_on_missing_evidence(self):
        broken = [dict(c) for c in CLASSIFICATION["conditions"]]
        broken[0]["pass"] = False  # missing trace
        self.assertEqual(classify(broken), "MECHANISM_INSUFFICIENT")
        broken[1]["pass"] = False  # inadmissible cell
        self.assertEqual(classify(broken), "MECHANISM_INSUFFICIENT")


if __name__ == "__main__":
    unittest.main(verbosity=2)
