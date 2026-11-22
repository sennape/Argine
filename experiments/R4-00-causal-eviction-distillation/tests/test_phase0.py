"""Contract tests for Research 4 Phase 0 (CFDC). They read only the frozen contract and repository records; no trace is opened."""
from __future__ import annotations

import importlib.util, json, re, subprocess, unittest
from fractions import Fraction
from pathlib import Path

HERE = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location("r4_phase0", HERE / "phase0.py"); P = importlib.util.module_from_spec(spec); spec.loader.exec_module(P)  # type: ignore
J = lambda n: json.loads((HERE / n).read_text(encoding="utf-8"))


class FeatureCausality(unittest.TestCase):
    def test_no_future_dependent_feature(self):
        fc = J("FEATURE_CONTRACT.json")
        self.assertEqual(fc["vocabulary_size"], 16)
        bad = re.compile(r"next[_ ]use|nu_j|future|teacher|belady|lookahead", re.I)
        for f in fc["features"]:
            self.assertFalse(f["depends_on_future"], f["name"])
            for k in ("definition", "update_timing", "causal_justification", "initial_value"):
                self.assertNotRegex(f[k], bad, f"{f['name']}.{k}")
            for k in ("definition", "units", "update_timing", "initial_value", "tie_semantics", "causal_justification"):
                self.assertTrue(f[k], f"{f['name']} missing {k}")
            self.assertTrue(f["integer_valued"])

    def test_vocabulary_matches_freeze_and_model_order(self):
        names = [f["name"] for f in J("FEATURE_CONTRACT.json")["features"]]
        self.assertEqual(names, J("FREEZE.json")["feature_vocabulary"])
        self.assertEqual(names, J("MODEL_CONTRACT.json")["feature_order"])
        self.assertEqual(len(set(names)), len(names))


class Splits(unittest.TestCase):
    def setUp(self): self.s = J("DATA_SPLITS.json")["splits"]

    def test_exact_identities(self):
        self.assertEqual([t["instance_id"] for t in self.s["TRAIN"]["traces"]],
                         ["coding-v1__seed-42", "instruction-v1__seed-42", "reasoning-v1__seed-42", "summarization-v1__seed-42"])
        self.assertEqual(len(self.s["VALIDATION"]["traces"]), 12)
        self.assertTrue(all(t["workload_id"].startswith("r2-11-") for t in self.s["VALIDATION"]["traces"]))
        self.assertEqual(sorted(t["workload_id"] for t in self.s["EVAL"]["traces"]),
                         ["coding-eval-v1", "instruction-eval-v1", "reasoning-eval-v1", "summarization-eval-v1"])
        self.assertEqual({t["seed"] for t in self.s["REPLICATE_CHECK"]["traces"]}, {43})
        for t in self.s["TRAIN"]["traces"] + self.s["VALIDATION"]["traces"] + self.s["EVAL"]["traces"]:
            self.assertRegex(t["trace_sha256"], r"^[0-9a-f]{64}$")

    def test_identities_match_sealed_records(self):
        dev = {i["instance_id"]: i["trace_sha256"] for i in json.loads((P.REPO / P.DEV_MANIFEST).read_text())["selected_instances"]}
        for t in self.s["TRAIN"]["traces"] + self.s["REPLICATE_CHECK"]["traces"]: self.assertEqual(dev[t["instance_id"]], t["trace_sha256"])
        ev = {c["workload_id"]: c["trace_sha256_recomputed"] for c in json.loads((P.REPO / P.EVAL_SEALED).read_text())["cells"]}
        for t in self.s["EVAL"]["traces"]: self.assertEqual(ev[t["workload_id"]], t["trace_sha256"])
        va = {c["workload_id"]: c["trace_sha256_independently_recomputed"] for c in json.loads((P.REPO / P.R2_11_SEALED).read_text())["canonical_captures"]}
        for t in self.s["VALIDATION"]["traces"]: self.assertEqual(va[t["workload_id"]], t["trace_sha256"])

    def test_no_overlap(self):
        roles = ["TRAIN", "VALIDATION", "EVAL", "REPLICATE_CHECK"]
        h = {r: {t["trace_sha256"] for t in self.s[r]["traces"]} for r in roles}
        w = {r: {t["workload_id"] for t in self.s[r]["traces"]} for r in roles}
        for i, a in enumerate(roles):
            for b in roles[i + 1:]:
                self.assertFalse(h[a] & h[b], f"trace overlap {a}/{b}")
                if {a, b} != {"TRAIN", "REPLICATE_CHECK"}: self.assertFalse(w[a] & w[b], f"workload overlap {a}/{b}")
        self.assertFalse({t["workload_spec_sha256"] for t in self.s["VALIDATION"]["traces"]} & {t["workload_spec_sha256"] for t in self.s["EVAL"]["traces"]})

    def test_eval_locked(self):
        self.assertEqual(self.s["EVAL"]["state"], "LOCKED")
        fz = J("FREEZE.json")
        self.assertEqual((fz["state"], fz["eval_state"], fz["outcome_metrics_present"]), ("R4_CFDC_PHASE0_FROZEN", "LOCKED", False))
        self.assertEqual(P.outcome_files(), [])


class BaselinesAndModels(unittest.TestCase):
    def test_frozen_baseline_identities(self):
        b = J("BASELINES.json")
        self.assertIn("decay 0.999", b["reference"]["definition"]); self.assertIn("aging interval 1", b["reference"]["definition"])
        self.assertFalse(b["reference"]["retrained_or_modified"])
        ids = [c["id"] for c in b["comparators"]]; self.assertEqual(ids, ["NUE_X", "DECAYED_COUNT"])
        nue = b["comparators"][0]; self.assertIn("make_nue('X', 'LEX')", nue["definition"]); self.assertFalse(nue["retrained_or_modified"])
        self.assertIn("BeladyGlobal", b["oracle"]["definition"])
        self.assertEqual(b["replay"]["budget_bytes"], 1610612736)

    def test_fixed_decay(self):
        d = J("BASELINES.json")["comparators"][1]["definition"]
        self.assertIn("decay 0.99 per served step", d); self.assertIn("no sweep", d)
        self.assertEqual(re.findall(r"0\.9\d*", d), ["0.99", "0.99"])
        f02 = next(f for f in J("FEATURE_CONTRACT.json")["features"] if f["id"] == "F02")
        self.assertIn("0.99", f02["definition"])

    def test_cheap_structural_limits(self):
        c = J("MODEL_CONTRACT.json")["CHEAP"]
        self.assertEqual((c["max_depth"], c["max_distinct_features"], c["min_samples_leaf"], c["min_decisions_per_leaf"]), (3, 4, 1000, 50))
        self.assertTrue(c["deterministic"]); self.assertIn("ensembles", c["forbidden"]); self.assertIn("neural networks", c["forbidden"])
        self.assertIn("integer", c["export"]); self.assertIn("LRU", c["eviction_rule"])
        r = J("MODEL_CONTRACT.json")["RICH"]
        self.assertEqual((r["rounds"], r["learning_rate"], r["max_depth"], r["min_samples_leaf"]), (200, 0.1, 6, 200))
        self.assertIn("NOT a runtime candidate", r["role"])


class DecisionRules(unittest.TestCase):
    def setUp(self): self.d = J("DECISION_RULES.json")

    def test_primary_endpoint(self):
        e = self.d["primary_endpoint"]
        self.assertEqual(e["aggregate"], "capture(P) = sum_w (B_A,w - B_P,w) / sum_w (B_A,w - B_Bel,w) over all traces of the split (pooled bytes, exact rational arithmetic)")
        self.assertIn("decode miss bytes", e["per_trace"])
        self.assertEqual(J("FREEZE.json")["endpoint"], e["aggregate"])

    def test_thresholds(self):
        t = self.d["thresholds"]
        self.assertEqual((Fraction(t["viability"]), Fraction(t["incremental_margin"]), Fraction(t["workload_floor"]), Fraction(t["rich_gate"])),
                         (Fraction(1, 4), Fraction(3, 100), Fraction(0), Fraction(1, 4)))
        self.assertIn("max(1/4, max(capture_EVAL(NUE_X), capture_EVAL(DECAYED_COUNT)) + 3/100)", self.d["rules"]["B_incremental_value"])
        self.assertIn("[0.20, 0.25) is a FAIL", self.d["rules"]["A_operational_viability"])

    def test_classification_is_total_and_ordered(self):
        labels = [c[1] for c in self.d["classification_order"]]
        self.assertEqual(labels[0], "R4_CFDC_INVALID")
        def classify(A, B, C, R):
            if A and B and C: return "R4_CFDC_CHEAP_VIABLE"
            if A and B: return "R4_CFDC_HETEROGENEOUS_NOT_VIABLE"
            if A: return "R4_CFDC_CHEAP_NOT_INCREMENTAL"
            return "R4_CFDC_CAUSAL_INFORMATION_PRESENT_CHEAP_NOT_DEMONSTRATED" if R else "R4_CFDC_CAUSAL_STATE_INSUFFICIENT"
        seen = {classify(a, b, c, r) for a in (0, 1) for b in (0, 1) for c in (0, 1) for r in (0, 1)}
        self.assertEqual(seen, set(labels[1:]))

    def test_anti_rescue(self):
        a = self.d["anti_rescue"]
        self.assertTrue(a["one_experiment"])
        for f in ("second feature round", "new model family", "threshold relaxation", "new training corpus", "new EVAL corpus", "one more candidate", "re-opening EVAL"):
            self.assertIn(f, a["forbidden"])
        self.assertIn("standalone CHEAP policy-cost microbenchmark", self.d["authorized_next_step_if_viable"]["only"])
        self.assertIn(">= 10 ms/token", self.d["authorized_next_step_if_viable"]["later_gate"]); self.assertIn(">= 2.0", self.d["authorized_next_step_if_viable"]["later_gate"])
        self.assertIn("exactly once", self.d["eval_opening"])


class FreezeAndResearch3(unittest.TestCase):
    def test_contract_verifies(self):
        self.assertEqual(P.verify(), [])

    def test_research3_immutable(self):
        self.assertEqual(P.research3_unchanged(), [])
        r = subprocess.run(["git", "-C", str(P.REPO), "cat-file", "-t", P.R3_CLOSURE_COMMIT], capture_output=True, text=True)
        self.assertEqual(r.stdout.strip(), "commit")
        fz = json.loads((P.EXP / "R3-final-closure/FREEZE.json").read_text())
        self.assertEqual(fz["freeze_sha256"], P.R3_CLOSURE_FREEZE)


if __name__ == "__main__":
    unittest.main()
