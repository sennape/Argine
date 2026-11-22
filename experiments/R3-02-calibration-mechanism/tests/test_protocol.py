"""R3-02 Phase 0 protocol invariant tests (no model, no acquisition)."""

from __future__ import annotations

import json
import sys
import unittest
from pathlib import Path

REPO = Path(__file__).resolve().parents[3]
PHASE = REPO / "experiments" / "R3-02-calibration-mechanism"
sys.path.insert(0, str(REPO / "tools"))

import r3_02_plan as P  # noqa: E402


class TestMatrix(unittest.TestCase):
    def setUp(self):
        self.plan = P.build_plan()
        self.launches = self.plan["launches"]

    def _n(self, kind, phase=None):
        return sum(1 for l in self.launches if l["kind"] == kind and (phase is None or l["phase"] == phase))

    def test_configuration_count(self):
        self.assertEqual(len(self.plan["configurations"]), 7)
        self.assertEqual([c["config_id"] for c in self.plan["configurations"]],
                         ["A-1.5GiB", "A-4GiB", "A-8GiB", "B-1.5GiB", "B-4GiB", "B-8GiB", "C-8GiB"])

    def test_launch_counts(self):
        self.assertEqual(self._n("PREFLIGHT"), 3)
        self.assertEqual(self._n("TRACE_COUNT"), 7)
        self.assertEqual(self._n("TIMING_WARMUP"), 7)
        self.assertEqual(self._n("TIMING_COUNTED"), 35)
        self.assertEqual(len(self.launches), 52)
        self.assertEqual(self.plan["counts"]["scientific_matrix_launches"], 49)
        self.assertEqual(self.plan["counts"]["scientific_cells"], 35)

    def test_cells_are_counted_timing_only(self):
        cells = [l for l in self.launches if l["scientific_cell"]]
        self.assertEqual(len(cells), 35)
        self.assertTrue(all(l["kind"] == "TIMING_COUNTED" and l["counted"] for l in cells))
        self.assertTrue(all(not l["counted"] for l in self.launches if l["kind"] != "TIMING_COUNTED"))

    def test_reuse_cardinality(self):
        reuse = {}
        for l in self.launches:
            if l["kind"] == "TIMING_COUNTED":
                reuse[l["config_id"]] = reuse.get(l["config_id"], 0) + 1
        self.assertEqual(reuse, {c: 5 for c in
                                 ["A-1.5GiB", "A-4GiB", "A-8GiB", "B-1.5GiB", "B-4GiB", "B-8GiB", "C-8GiB"]})

    def test_ab_alternation(self):
        for label in ("1.5", "4"):
            reps = [l for l in self.launches
                    if l["budget_label"] == label and l["kind"] == "TIMING_COUNTED"]
            for rep in range(1, 6):
                order = [l["control"] for l in reps if l["repetition"] == rep]
                self.assertIn(order, (["A", "B"], ["B", "A"]))
            first = [l["control"] for l in reps if l["slot"] == 1]
            self.assertEqual(first, ["A", "B", "A", "B", "A"])

    def test_abc_latin_square(self):
        reps = [l for l in self.launches
                if l["budget_label"] == "8" and l["kind"] == "TIMING_COUNTED"]
        pos = {c: [0, 0, 0] for c in "ABC"}
        for rep in range(1, 6):
            order = [l["control"] for l in reps if l["repetition"] == rep]
            self.assertEqual(sorted(order), ["A", "B", "C"])
            for slot, c in enumerate(order):
                pos[c][slot] += 1
        for c, p in pos.items():
            self.assertLessEqual(max(p) - min(p), 1)

    def test_preflights_are_non_scientific(self):
        pre = [l for l in self.launches if l["phase"] == "PREFLIGHT"]
        self.assertEqual([l["control"] for l in pre], ["A", "B", "C"])
        for l in pre:
            self.assertFalse(l["counted"])
            self.assertFalse(l["scientific_cell"])
            self.assertEqual(l["budget_label"], "8")
            self.assertEqual(l["n_predict"], P.DECODING["n_predict_preflight"])

    def test_policy_parameters_exact(self):
        base = {"1.5": (0.999, 1), "4": (0.99, 64), "8": (0.999, 16)}
        for c in self.plan["configurations"]:
            self.assertEqual(c["params"]["decay_factor"], base[c["budget_label"]][0])
            self.assertEqual(c["params"]["aging_interval_steps"], base[c["budget_label"]][1])
            if c["control"] in ("B", "C"):
                self.assertEqual(c["params"]["predictor_decay"], 0.99)
                self.assertEqual(c["params"]["top_k"], 8)
                self.assertEqual(c["params"]["n_experts"], 128)
            else:
                self.assertNotIn("top_k", c["params"])

    def test_identities_frozen(self):
        self.assertEqual(self.plan["runtime"]["sha256"],
                         "3f094f5b5f4963f488b63722f365068f09e2aa3b12e2096969da99f471bbd644")
        self.assertEqual(self.plan["model"]["sha256"],
                         "a015794bfb1d69cb03dbb86b185fb2b9b339f757df5f8f9dd9ebdab8f6ed5d32")
        self.assertEqual(self.plan["store"]["sha256"],
                         "fef943ae0bc017864a47d41c1fc90d422016045f7e8705b1d138b7defd8c5146")
        self.assertEqual(self.plan["workload"]["sha256"],
                         "c02f9657d12d874cc43523e2b609bb604a68a6a8b267b2ffac6d3420c509b730")
        self.assertEqual(self.plan["contract_digest"], P.CONTRACT_DIGEST)

    def test_no_eval_or_results(self):
        blob = P.canonical_json(self.plan) + P.canonical_json(P.build_preregistration())
        for bad in ("EVAL", "held-out", "observed", "tps", "measured_value", "decode_tokens_per_second"):
            self.assertNotIn(bad, blob)
        self.assertFalse(self.plan["measurements_included"])

    def test_digest_deterministic(self):
        self.assertEqual(P.protocol_digest(), P.protocol_digest())
        self.assertEqual(len(P.protocol_digest()), 64)

    def test_validate_returns_no_failures(self):
        self.assertEqual(P.validate(), [])

    def test_generated_files_match_builder(self):
        pre = json.loads((PHASE / "PREREGISTRATION.json").read_text(encoding="utf-8"))
        plan = json.loads((PHASE / "ACQUISITION_PLAN.json").read_text(encoding="utf-8"))
        self.assertEqual(pre, P.build_preregistration())
        self.assertEqual(plan, P.build_plan())


class TestFreeze(unittest.TestCase):
    def test_freeze_matches_digest(self):
        freeze = json.loads((PHASE / "FREEZE.json").read_text(encoding="utf-8"))
        self.assertEqual(freeze["status"], "PASS_R3_02_PROTOCOL_FROZEN")
        self.assertEqual(freeze["protocol_digest"], P.protocol_digest())
        self.assertEqual(freeze["counts"]["total_host_process_launches"], 52)

    def test_freeze_artifacts_match_disk(self):
        freeze = json.loads((PHASE / "FREEZE.json").read_text(encoding="utf-8"))
        for name, sha in freeze["artifacts"].items():
            self.assertEqual((PHASE / name).read_bytes().__len__() > 0, True)
            import hashlib
            self.assertEqual(hashlib.sha256((PHASE / name).read_bytes()).hexdigest(), sha, name)


if __name__ == "__main__":
    unittest.main(verbosity=2)
