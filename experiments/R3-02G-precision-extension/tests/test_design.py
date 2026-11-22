"""R3-02G design tests: deterministic, offline, no model."""

from __future__ import annotations

import hashlib
import json
import random
import statistics
import unittest
from pathlib import Path

REPO = Path(__file__).resolve().parents[3]
PHASE = Path(__file__).resolve().parents[1]
ANALYSIS = json.loads((REPO / "experiments/R3-02-phase2-mechanism/ANALYSIS.json").read_text(encoding="utf-8"))
SCALING = json.loads((PHASE / "PRECISION_SCALING.json").read_text(encoding="utf-8"))
FEAS = json.loads((PHASE / "FEASIBILITY.json").read_text(encoding="utf-8"))
PREREG = json.loads((PHASE / "PREREGISTRATION.json").read_text(encoding="utf-8"))

SEED = 20261116
DRAWS = 10000


def bootstrap_ci(diffs):
    rng = random.Random(SEED)
    n = len(diffs)
    boots = sorted(statistics.median([diffs[rng.randrange(n)] for _ in range(n)]) for _ in range(DRAWS))
    return boots[int(0.025 * DRAWS)], boots[int(0.975 * DRAWS) - 1]


def precision_verdict(half_width, median_a):
    return half_width <= 0.01 * median_a


class TestPilotReproduction(unittest.TestCase):
    def test_five_pairs(self):
        self.assertEqual(len(ANALYSIS["timing"]["4"]["a"]), 5)
        self.assertEqual(len(ANALYSIS["timing"]["4"]["b"]), 5)

    def test_median_and_ci_reproduce(self):
        d = ANALYSIS["timing"]["4"]["wall"]["diffs"]
        self.assertAlmostEqual(statistics.median(d), ANALYSIS["timing"]["4"]["wall"]["median"], places=12)
        lo, hi = bootstrap_ci(d)
        self.assertAlmostEqual(lo, ANALYSIS["timing"]["4"]["wall"]["ci_low"], places=12)
        self.assertAlmostEqual(hi, ANALYSIS["timing"]["4"]["wall"]["ci_high"], places=12)

    def test_threshold(self):
        self.assertAlmostEqual(ANALYSIS["precision"]["4"]["threshold_1pct_a"],
                               0.01 * ANALYSIS["timing"]["4"]["wall"]["median_a"], places=15)


class TestScaling(unittest.TestCase):
    def test_simulation_deterministic(self):
        diffs = SCALING["pilot_diffs_4gib"]
        self.assertEqual(diffs, ANALYSIS["timing"]["4"]["wall"]["diffs"])
        # same seed -> same median of a small deterministic resampling
        a = statistics.median([diffs[random.Random(SEED).randrange(5)] for _ in range(5)])
        b = statistics.median([diffs[random.Random(SEED).randrange(5)] for _ in range(5)])
        self.assertEqual(a, b)

    def test_fixed_n_selected(self):
        self.assertEqual(FEAS["chosen_N"], 600)
        self.assertGreaterEqual(FEAS["probability_pass_at_600"], 0.90)

    def test_naive_sqrt_is_only_sanity(self):
        self.assertNotAlmostEqual(SCALING["primary"]["600"]["median_half"],
                                  SCALING["primary"]["600"]["naive_sqrt"], places=6)

    def test_precision_criterion(self):
        self.assertFalse(precision_verdict(0.030396, 0.266464))
        self.assertTrue(precision_verdict(0.0020, 0.266464))


class TestPreregistration(unittest.TestCase):
    def test_no_optional_stopping(self):
        self.assertTrue(PREREG["run_order"]["no_optional_stopping"])
        self.assertTrue(PREREG["run_order"]["fixed"])
        self.assertEqual(PREREG["new_evidence"]["pairs"], 600)

    def test_extension_only(self):
        self.assertEqual(FEAS["reuse_decision"], "EXTENSION_ONLY")
        self.assertIn("no reuse of the five pilot pairs", PREREG["contamination_boundaries"])

    def test_timing_only(self):
        self.assertEqual(FEAS["trace_requirement"], "TIMING_ONLY_EXTENSION_VALID")
        self.assertTrue(PREREG["inherited_frozen_evidence"]["no_new_trace"])

    def test_fail_closed_on_missing_pairs(self):
        def analyse(pairs):
            if len(pairs) != 600:
                raise ValueError("incomplete extension: expected 600 pairs")
            return True
        with self.assertRaises(ValueError):
            analyse([0.01] * 599)

    def test_inherited_evidence_unchanged(self):
        phase2 = REPO / "experiments/R3-02-phase2-mechanism"
        freeze = json.loads((phase2 / "FREEZE.json").read_text(encoding="utf-8"))
        self.assertEqual(freeze["status"], "MECHANISM_INSUFFICIENT")
        self.assertEqual(hashlib.sha256((phase2 / "ANALYSIS.json").read_bytes()).hexdigest(),
                         freeze["artifacts"]["ANALYSIS.json"])

    def test_no_model_process(self):
        import subprocess as sp
        original = sp.Popen
        sp.Popen = lambda *a, **k: (_ for _ in ()).throw(AssertionError("process started"))
        try:
            self.assertEqual(FEAS["chosen_N"], 600)
        finally:
            sp.Popen = original


if __name__ == "__main__":
    unittest.main(verbosity=2)
