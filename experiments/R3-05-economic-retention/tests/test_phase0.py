import json, sys, unittest
from pathlib import Path
HERE = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(HERE))
import phase0 as Z


def x(**kw):
    base = {"fidelity_pass": True, "kappa_low_ms_per_MB": 0.3792, "events_per_token": 48, "belady_bytes_avoided_per_token": 240e6, "pp_bytes_avoided_per_token": 559224.0,
            "ces_bytes_avoided_per_token": 0.0, "pp_curve_retained_at_f010": 0.5,
            "candidates": {"NUE_X": {"dev_bytes_avoided_per_token": 60e6, "eval_bytes_avoided_per_token": 60e6, "microbench_incremental_ns_per_event": 2000.0},
                           "NUE_LO": {"dev_bytes_avoided_per_token": 20e6, "eval_bytes_avoided_per_token": 20e6, "microbench_incremental_ns_per_event": 200.0}}}
    base.update(kw); return base


class T(unittest.TestCase):
    def test_01_verify_clean(self): self.assertEqual(Z.verify(), [])
    def test_02_envelope_matches_frozen_r3_04(self):
        e = json.loads((HERE / "ECONOMIC_ENVELOPE.json").read_text())
        self.assertAlmostEqual(e["inputs"]["C0_wall_cost_ms"], 14.588048502604149, places=9)
        self.assertAlmostEqual(e["derived"]["required_cost_reduction_if_benefit_unchanged"], 1 - 0.57 / 14.588048502604149, places=12)
        self.assertEqual(e["inputs"]["events_per_token"], 48)
    def test_03_cost_only_never_reaches_margin(self):
        e = json.loads((HERE / "ECONOMIC_ENVELOPE.json").read_text())
        self.assertNotEqual(e["break_even_matrix"]["cost/100|benefit x1"]["label"], "WIN_WITH_MARGIN")
    def test_04_feasible_path(self):
        r = Z.classify_phase1(x()); self.assertEqual(r["outcome"], "R3_05_FEASIBLE"); self.assertEqual(r["selected"], "NUE_X")
    def test_05_fidelity_invalidates(self): self.assertEqual(Z.classify_phase1(x(fidelity_pass=False))["outcome"], "R3_05_PHASE1_INVALID")
    def test_06_ceiling_kill(self):
        r = Z.classify_phase1(x(belady_bytes_avoided_per_token=10e6)); self.assertEqual(r["outcome"], "R3_05_INFEASIBLE"); self.assertTrue(r["kills"][0].startswith("K1"))
    def test_07_benefit_floor_kill_regardless_of_cost(self):
        c = {"NUE_X": {"dev_bytes_avoided_per_token": 5e6, "eval_bytes_avoided_per_token": 5e6, "microbench_incremental_ns_per_event": 0.0}}
        r = Z.classify_phase1(x(candidates=c)); self.assertEqual(r["outcome"], "R3_05_INFEASIBLE"); self.assertTrue(any(k.startswith("K4") for k in r["kills"]))
    def test_08_marginal_and_ratio(self):
        c = {"NUE_X": {"dev_bytes_avoided_per_token": 40e6, "eval_bytes_avoided_per_token": 40e6, "microbench_incremental_ns_per_event": 30000.0}}
        self.assertEqual(Z.classify_phase1(x(candidates=c))["outcome"], "R3_05_MARGINAL")
    def test_09_dev_eval_sign(self):
        c = {"NUE_X": {"dev_bytes_avoided_per_token": -1e6, "eval_bytes_avoided_per_token": 60e6, "microbench_incremental_ns_per_event": 0.0}}
        self.assertNotEqual(Z.classify_phase1(x(candidates=c))["outcome"], "R3_05_FEASIBLE")
    def test_10_k2_k3_k5(self):
        c = {"NUE_X": {"dev_bytes_avoided_per_token": 60e6, "eval_bytes_avoided_per_token": 60e6, "microbench_incremental_ns_per_event": 2000.0},
             "NUE_LO": {"dev_bytes_avoided_per_token": 58e6, "eval_bytes_avoided_per_token": 58e6, "microbench_incremental_ns_per_event": 100.0}}
        r = Z.classify_phase1(x(ces_bytes_avoided_per_token=500000.0, pp_curve_retained_at_f010=0.1, candidates=c))
        ks = " ".join(r["kills"]); self.assertIn("K2", ks); self.assertIn("K3", ks); self.assertIn("K5", ks); self.assertEqual(r["selected"], "NUE_LO")
    def test_11_tamper_detected(self):
        p = HERE / "CLAIM_BOUNDARIES.md"; o = p.read_text(encoding="utf-8")
        try:
            p.write_text(o + "x", encoding="utf-8"); self.assertTrue(Z.verify())
        finally: p.write_text(o, encoding="utf-8")
    def test_12_protocol_authorizes_no_model(self):
        pr = json.loads((HERE / "PROTOCOL.json").read_text()); self.assertEqual(pr["compute_budget"]["phase1_model_hours"], 0.0)
        self.assertFalse(any(r["model"] for r in pr["evidence_ladder"] if r["rung"] in ("R0", "R1", "R2", "R3", "R4", "R5", "R6")))

if __name__ == "__main__": unittest.main()
