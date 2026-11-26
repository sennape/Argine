"""The frozen Phase 2 statistics and classification on synthetic launch records (no runtime, no evaluation content)."""
from __future__ import annotations

import copy, json, sys, unittest
from fractions import Fraction
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from fixtures import HERE, load, record_set, DIGEST   # noqa: E402

A = load("analysis", HERE / "analysis.py")
P0 = HERE.parents[0] / "R5-00-exposed-transfer-stall"


def with_r(recs, campaign, rb):
    """Set A to 250 ms/token and C so that block b of `campaign` has relative reduction rb[b], exactly where the decimal
    value is representable (e.g. 0.02 -> C = 245 ms, -0.02 -> 255 ms)."""
    out = copy.deepcopy(recs)
    for r in out:
        if r["campaign"] == campaign:
            v = Fraction(250) * (1 - Fraction(str(rb[r["block"] - 1]))) if r["arm"] == "C" else Fraction(250)
            r["server_timings"]["predicted_ms"] = float(v * 512)
    return out


class StudentT(unittest.TestCase):
    def test_quantiles_match_the_frozen_precision_plan(self):
        dr = json.loads((P0 / "DECISION_RULES.json").read_text(encoding="utf-8"))
        self.assertAlmostEqual(A.t_quantile(0.975, 19), dr["precision_plan"]["t_975_df19"], places=10)
        self.assertAlmostEqual(A.t_quantile(0.975, 11), 2.200985160091639, places=9)
        self.assertAlmostEqual(A.t_cdf(0.0, 7), 0.5, places=12)
        self.assertAlmostEqual(A.t_cdf(-1.3, 9) + A.t_cdf(1.3, 9), 1.0, places=12)

    def test_fixed_critical_values_match_the_computed_quantiles(self):
        for df, t in A.T975.items():
            self.assertAlmostEqual(float(t), A.t_quantile(0.975, df), places=12)

    def test_exact_decisions_do_not_depend_on_float_summation(self):
        xs = [Fraction(1, 50)] * 20                                                # exactly 0.02, zero variance
        self.assertTrue(A.lower_ge(xs, Fraction(1, 50))); self.assertTrue(A.upper_le(xs, Fraction(1, 50)))
        self.assertFalse(A.upper_lt(xs, Fraction(1, 50)))
        ys = [Fraction(1, 50) + Fraction(1, 10**18)] * 20
        self.assertFalse(A.upper_le(ys, Fraction(1, 50)))
        self.assertEqual(A.TH(0.02), Fraction(1, 50)); self.assertEqual(A.TH(0.01), Fraction(1, 100))

    def test_interval(self):
        iv = A.interval95([1.0, 2.0, 3.0, 4.0])
        self.assertAlmostEqual(iv["mean"], 2.5); self.assertEqual(iv["df"], 3)
        self.assertAlmostEqual(iv["half_width"], A.t_quantile(0.975, 3) * (5 / 3) ** 0.5 / 2, places=9)


class Classification(unittest.TestCase):
    def test_positive_runtime_improvement(self):
        recs, mm = record_set(effect_15=0.05, effect_4=0.0)
        r = A.classify(recs, mm)
        self.assertEqual(r["classification"], "R5_POSITIVE_RUNTIME_IMPROVEMENT", r["gates"])
        self.assertTrue(r["improvement"]); self.assertTrue(all(r["gates"].values()))

    def test_primary_boundary_inclusive(self):
        recs, mm = record_set()
        exact = with_r(recs, "timing_1.5GiB", [0.02] * 20)                      # zero variance: lower bound = 0.02 exactly
        r = A.classify(exact, mm)
        self.assertAlmostEqual(r["primary_1.5GiB"]["interval95"]["lower"], 0.02, places=12)
        self.assertTrue(r["gates"]["G3"])
        below = with_r(recs, "timing_1.5GiB", [0.0199] * 20)
        self.assertFalse(A.classify(below, mm)["gates"]["G3"])

    def test_primary_uses_the_lower_bound_not_the_mean(self):
        recs, mm = record_set()
        noisy = with_r(recs, "timing_1.5GiB", [0.08, -0.03] * 10)               # mean 0.025 >= 0.02 but lower bound < 0.02
        r = A.classify(noisy, mm)
        self.assertGreater(r["primary_1.5GiB"]["aggregate_mean_r"], 0.02); self.assertFalse(r["gates"]["G3"])
        self.assertNotEqual(r["classification"], "R5_POSITIVE_RUNTIME_IMPROVEMENT")

    def test_no_harm_boundary(self):
        recs, mm = record_set()
        ok = with_r(recs, "timing_4GiB", [-0.02] * 20)                            # slowdown exactly 2%: upper bound 0.02 passes
        self.assertTrue(A.classify(ok, mm)["gates"]["G5"])
        bad = with_r(recs, "timing_4GiB", [-0.0201] * 20)
        r = A.classify(bad, mm)
        self.assertFalse(r["gates"]["G5"]); self.assertEqual(r["classification"], "R5_PRIMARY_GAIN_NOT_ROBUST")

    def test_consistency_needs_three_prompts(self):
        recs, mm = record_set(prompt_effect={"code-eval-1": 0.12, "nl-eval-1": 0.12, "math-eval-1": -0.002, "json-eval-1": -0.002})
        r = A.classify(recs, mm)
        self.assertEqual(r["consistency_1.5GiB"]["positive_prompts"], 2); self.assertFalse(r["gates"]["G6"]); self.assertTrue(r["gates"]["G3"], r["primary_1.5GiB"]["interval95"])
        self.assertEqual(r["classification"], "R5_PRIMARY_GAIN_NOT_ROBUST")

    def test_mechanism_absent(self):
        recs, mm = record_set(mech={"B": (80.0, 190.0, 4e8), "C0_I": (80.0, 185.0, 4e8), "C_I": (80.0, 185.0, 4e8)})
        r = A.classify(recs, mm)
        self.assertFalse(r["mechanism"]["a"]); self.assertEqual(r["classification"], "R5_RUNTIME_CHANGE_WITHOUT_MECHANISM")

    def test_hidden_floor(self):
        recs, mm = record_set(hidden=0.249)
        r = A.classify(recs, mm)
        self.assertFalse(r["mechanism"]["b"]); self.assertFalse(r["gates"]["G4"])
        recs, mm = record_set(hidden=0.25)
        self.assertTrue(A.classify(recs, mm)["mechanism"]["b"])

    def test_attribution_share(self):
        # wait reduction 10 ms but store reduction 30 ms: less than half of the hook saving comes from waiting less
        recs, mm = record_set(mech={"B": (80.0, 200.0, 4e8), "C0_I": (80.0, 180.0, 4e8), "C_I": (70.0, 170.0, 4e8)})
        r = A.classify(recs, mm)
        self.assertTrue(r["mechanism"]["c_applies"]); self.assertFalse(r["mechanism"]["c"])
        self.assertEqual(r["classification"], "R5_RUNTIME_CHANGE_WITHOUT_MECHANISM")
        # store reduction not positive: (c) holds by the frozen text
        recs, mm = record_set(mech={"B": (80.0, 160.0, 4e8), "C0_I": (80.0, 180.0, 4e8), "C_I": (70.0, 170.0, 4e8)})
        r = A.classify(recs, mm)
        self.assertFalse(r["mechanism"]["c_applies"]); self.assertTrue(r["mechanism"]["c"])

    def test_mechanism_without_gain_and_neither(self):
        recs, mm = record_set(effect_15=0.0)
        self.assertEqual(A.classify(recs, mm)["classification"], "R5_MECHANISM_PRESENT_BUT_NO_NET_RUNTIME_GAIN")
        recs, mm = record_set(effect_15=0.0, mech={"B": (80.0, 190.0, 4e8), "C0_I": (80.0, 185.0, 4e8), "C_I": (80.0, 185.0, 4e8)})
        self.assertEqual(A.classify(recs, mm)["classification"], "R5_NO_REALISED_OVERLAP_NO_GAIN")

    def test_purity_before_runtime_gates(self):
        recs, mm = record_set()
        for r in recs:
            if r["campaign"] == "timing_4GiB" and r["arm"] == "C":
                r["r5_counters"]["evictions"] = int(r["r5_counters"]["evictions"] * 1.011)
        r = A.classify(recs, mm)
        self.assertFalse(r["gates"]["G7"]); self.assertEqual(r["classification"], "R5_CONFOUNDED_NOT_PURE_SCHEDULING")

    def test_correctness_and_integrity_precedence(self):
        recs, mm = record_set()
        c = copy.deepcopy(recs); next(r for r in c if r["arm"] == "C")["token_digest"] = "other"
        self.assertEqual(A.classify(c, mm)["classification"], "R5_IMPLEMENTATION_OR_CAPABILITY_BLOCKED")
        a = copy.deepcopy(recs); next(r for r in a if r["arm"] == "A")["token_digest"] = "other"
        self.assertEqual(A.classify(a, mm)["classification"], "R5_INVALID")
        both = copy.deepcopy(recs)
        next(r for r in both if r["arm"] == "C_I")["r5_counters"]["violations"] = 1
        next(r for r in both if r["arm"] == "B" and r["prompt"] != "code-eval-1")["r5_counters"]["violations"] = 1
        self.assertEqual(A.classify(both, mm)["classification"], "R5_IMPLEMENTATION_OR_CAPABILITY_BLOCKED")   # row 1 before row 3

    def test_no_partial_analysis(self):
        recs, mm = record_set()
        with self.assertRaises(ValueError):
            A.classify(recs[:-1], mm)

    def test_traced_never_enter_the_timing_endpoints(self):
        recs, mm = record_set()
        bad = copy.deepcopy(recs)
        next(r for r in bad if r["campaign"] == "timing_1.5GiB")["traced"] = True
        with self.assertRaises(ValueError):
            A.classify(bad, mm)

    def test_every_label_is_reachable_and_ordered_as_frozen(self):
        dr = json.loads((P0 / "DECISION_RULES.json").read_text(encoding="utf-8"))
        self.assertEqual([row[1] for row in dr["classification_order"]], A.LABELS)
        f = dict(G3=True, G4=True, G5=True, G6=True, G7=True)
        self.assertEqual(A.classify_order(f, True, True), A.LABELS[0])
        self.assertEqual(A.classify_order(f, False, True), A.LABELS[2])
        self.assertEqual(A.classify_order(dict(f, G7=False), False, False), A.LABELS[3])
        self.assertEqual(A.classify_order(f, False, False), A.LABELS[4])
        self.assertEqual(A.classify_order(dict(f, G6=False), False, False), A.LABELS[5])
        self.assertEqual(A.classify_order(dict(f, G4=False), False, False), A.LABELS[6])
        self.assertEqual(A.classify_order(dict(f, G3=False), False, False), A.LABELS[7])
        self.assertEqual(A.classify_order(dict(f, G3=False, G4=False), False, False), A.LABELS[8])
        self.assertEqual(A.terminal("x", True)["classification"], A.LABELS[0])
        self.assertEqual(A.terminal("x", False)["classification"], A.LABELS[2])

    def test_thresholds_are_the_frozen_ones(self):
        th = json.loads((P0 / "DECISION_RULES.json").read_text(encoding="utf-8"))["thresholds"]
        self.assertEqual((A.MEI, A.NO_HARM, A.HIDDEN_FLOOR, A.ATTRIBUTION, A.PURITY),
                         (th["minimum_effect_of_interest"], th["no_harm_4GiB"], th["hidden_deferred_bytes_floor"],
                          th["mechanism_attribution_share"], th["scheduling_purity"]))


if __name__ == "__main__":
    unittest.main()
