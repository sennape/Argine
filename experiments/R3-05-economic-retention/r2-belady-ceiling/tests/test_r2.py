import json, shutil, sys, tempfile, unittest
from pathlib import Path
HERE = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(HERE))
import r2 as R


def lines(reqs, size=1, layer=0):
    return [{"i": i, "pass": 0, "layer": layer, "bytes": size, "req": r} for i, r in enumerate(reqs)]


class T(unittest.TestCase):
    def test_01_inputs_verify(self): self.assertEqual(R.verify_inputs()["canonical_stream_sha256"], R.STREAM_SHA)
    def test_02_wrong_stream_rejected(self):
        with tempfile.TemporaryDirectory() as d:
            p = Path(d) / "s.jsonl"; data = (R.R1D / "CANONICAL_STREAM.jsonl").read_bytes(); p.write_bytes(data[:-5] + b"9]}\n")
            with self.assertRaises(R.R2Error): R.load_events(p)
    def test_03_frozen_identity_mismatch_rejected(self):
        old = R.R1_FREEZE_SHA
        try:
            R.R1_FREEZE_SHA = "0" * 64
            with self.assertRaises(R.R2Error): R.verify_inputs()
        finally: R.R1_FREEZE_SHA = old
    def test_04_belady_hand_example(self):
        # cache 2 slots (size 1), single layer. sequence a b c a b c: Belady MIN misses = 4 (a,b,c,then one of a/b)
        ev = lines([[1], [2], [3], [1], [2], [3]])
        r = R.replay_policy(ev, R.new_belady(ev), 2); self.assertEqual(r["all_pass"]["misses"], 4); self.assertEqual(r["all_pass"]["hits"], 2)
    def test_05_never_used_again_evicted_first(self):
        ev = lines([[1], [2], [3], [1], [1]])           # 2 never reused: at event 2 (miss on 3) victim must be 2, not 1
        b = R.new_belady(ev); r = R.replay_policy(ev, b, 2); self.assertEqual(r["all_pass"]["misses"], 3)
    def test_06_budget_enforced_and_overflow(self):
        ev = lines([[1, 2, 3]], size=1); r = R.replay_policy(ev, R.new_belady(ev), 2)         # an event bigger than the budget cannot be atomic
        self.assertLessEqual(r["resident_peak_bytes"], 2); self.assertEqual(r["overflow_events"], 1); self.assertEqual(r["budget_violations"], 0)
    def test_07_deterministic_replay(self):
        ev = lines([[1, 2], [2, 3], [4, 1], [3, 4], [1, 2]] * 5); a = R.replay_policy(ev, R.new_belady(ev), 3); b = R.replay_policy(ev, R.new_belady(ev), 3); self.assertEqual(a, b)
    def test_08_no_future_leakage_outside_eviction(self):
        ev = lines([[1, 2], [2, 3], [4, 1], [3, 4]] * 4); snap = json.dumps(ev); pol = R.new_belady(ev); r = R.replay_policy(ev, pol, 3)
        self.assertEqual(json.dumps(ev), snap); self.assertEqual(pol.j, len(ev)); self.assertEqual(r["events"], len(ev))
        self.assertEqual(r["all_pass"]["hits"] + r["all_pass"]["misses"], sum(len(set(e["req"])) for e in ev))      # no event skipped or altered
        with self.assertRaises(RuntimeError): R.replay_policy(list(reversed(ev)), R.new_belady(ev), 3)             # reordered events are refused
    def test_09_integer_accounting_exact(self):
        res = json.loads((HERE / "RESULTS.json").read_text())
        for b, r in res["budgets"].items():
            m = r["mechanism"]
            for k in ("misses", "miss_bytes"):
                self.assertEqual(m[f"A_decode_{k}"] - m[f"BELADY_decode_{k}"], m[f"{'misses' if k == 'misses' else 'miss_bytes'}_avoided_decode"])
            self.assertEqual(m["A_miss_bytes_per_token"] * 512, m["A_decode_miss_bytes"])
    def test_10_results_per_budget(self):
        res = json.loads((HERE / "RESULTS.json").read_text())["budgets"]
        self.assertEqual(res["1.5"]["mechanism"]["A_decode_misses"], 106491)        # frozen R1/Gate 2 A counter
        for b in ("1.5", "4", "8"):
            m = res[b]["mechanism"]; self.assertLess(m["BELADY_decode_misses"], m["A_decode_misses"]); self.assertEqual(res[b]["budget_violations_total"], 0)
            self.assertEqual(res[b]["overflow_events"], {"A": 0, "BELADY": 0})
    def test_11_kappa_conversion(self):
        res = {"budgets": {"1.5": {"mechanism": {"miss_bytes_avoided_per_token": 1e7}}, "4": {"mechanism": {"miss_bytes_avoided_per_token": 1e7}}, "8": {"mechanism": {"miss_bytes_avoided_per_token": 2e7}}}}
        e = R.economic(res, 0.5, 1.0)
        self.assertEqual(e["budgets"]["1.5"]["CLAIRVOYANT_VALUE_LOW_MS_PER_TOKEN"], 5.0); self.assertEqual(e["budgets"]["8"]["CLAIRVOYANT_VALUE_LOW_MS_PER_TOKEN"], 10.0)
        self.assertEqual(e["budgets"]["4"]["CLAIRVOYANT_VALUE_LOW_MS_PER_TOKEN"], "NOT_ESTIMABLE")
    def test_12_k1_boundaries(self):
        self.assertEqual(R.k1_decide(9.999999999), "R3_05_K1_KILL"); self.assertEqual(R.k1_decide(10.0), "R3_05_K1_HEADROOM_PASS"); self.assertEqual(R.k1_decide(10.000000001), "R3_05_K1_HEADROOM_PASS")
    def test_13_k1_agrees_with_frozen_phase0_rule(self):
        import importlib.util
        spec = importlib.util.spec_from_file_location("_p0t", R.P0 / "phase0.py"); z = importlib.util.module_from_spec(spec); spec.loader.exec_module(z)
        for v in (9.0, 10.0, 11.0):
            out = z.classify_phase1({"fidelity_pass": True, "kappa_low_ms_per_MB": 1.0, "events_per_token": 48, "belady_bytes_avoided_per_token": v * 1e6, "pp_bytes_avoided_per_token": 1.0,
                                     "ces_bytes_avoided_per_token": 0.0, "pp_curve_retained_at_f010": 0.5, "candidates": {}})
            self.assertEqual("K1_CEILING" in " ".join(out["kills"]), R.k1_decide(v) == "R3_05_K1_KILL")
    def test_14_committed_k1_and_verify(self):
        k = json.loads((HERE / "K1_DECISION.json").read_text()); self.assertEqual(k["threshold_ms_per_token"], 10.0); self.assertEqual(R.verify(replay_too=False), [])
    def test_15_a_replay_matches_r1_at_1_5(self):
        ev = R.load_events(); r = R.run_one("A", "1.5", ev); self.assertEqual((r["decode"]["misses"], r["decode"]["miss_bytes"]), (106491, 306277576704))
    def test_16_tamper_detected(self):
        p = HERE / "CLAIM_BOUNDARIES.md"; o = p.read_text(encoding="utf-8")
        try:
            p.write_text(o + "x", encoding="utf-8"); self.assertTrue(R.verify())
        finally: p.write_text(o, encoding="utf-8")

if __name__ == "__main__": unittest.main()
