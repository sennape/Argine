import copy, json, sys, unittest
from pathlib import Path
HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import audit_gate2 as G


class Gate2(unittest.TestCase):
    def test_verify_and_freeze(self):
        fz = G.verify(); self.assertEqual(fz["classification"], "R3_04_MECHANISM_PASS"); self.assertEqual(fz["shutdown_helper_status"], "R3_04_SHUTDOWN_HELPER_VALIDATED_ON_REAL_MODEL")
        self.assertEqual(fz["protocol_freeze_sha256"], G.PROTOCOL_FREEZE_SHA256); self.assertFalse(fz["acquisition_opened"])

    def test_audit_all_pass_and_counts(self):
        a = json.loads((HERE / "AUDIT.json").read_text()); self.assertTrue(a["all_ok"]); self.assertEqual(a["n_checks"], 30)

    def test_each_tamper_is_caught(self):
        gates = json.loads((HERE / "MECHANISM_GATES.json").read_text()); gi = json.loads((HERE / "GATE_INPUTS.json").read_text())
        recs = {i: json.loads((HERE / "records_sanitised" / f"M{i:02d}.json").read_text()) for i in range(1, 11)}
        def ev(mut):
            r = copy.deepcopy(recs); mut(r); return G.M.evaluate_gates(r, gi["sham_vs_a"], gi["off_vs_on"])
        self.assertEqual(ev(lambda r: None)["status"], "MECHANISM_PASS")
        self.assertEqual(ev(lambda r: r[3]["shutdown"].update(method_used="legacy_after_helper_failure", helper_returncode=2))["status"], "MECHANISM_FAIL")
        self.assertEqual(ev(lambda r: r[9].update(output_token_digest="0" * 64))["status"], "MECHANISM_FAIL")
        self.assertEqual(ev(lambda r: r[2]["replay"].update(semantic_events=1))["status"], "MECHANISM_FAIL")
        self.assertEqual(ev(lambda r: r[1]["shutdown"].update(latency_s=300.0))["shutdown_validation"]["status"], "HELPER_NOT_VALIDATED")

    def test_mechanism_metrics_pattern_and_no_classification(self):
        m = json.loads((HERE / "MECHANISM_METRICS.json").read_text()); red = m["P_vs_A_decode_miss_byte_reduction_pct"]
        self.assertTrue(m["R2_pattern_reproduced"]); self.assertGreater(red["1.5"], red["4"]); self.assertGreater(red["4"], 0); self.assertEqual(red["8"], 0.0)
        self.assertNotIn("classification", json.dumps(m).lower()); self.assertIn("no NET_WIN/NET_LOSS", m["mechanism_only"])

    def test_no_personal_paths_committed(self):
        for p in HERE.rglob("*.json"):
            self.assertNotIn("alpel", p.read_text(), p.name)


if __name__ == "__main__":
    unittest.main()
