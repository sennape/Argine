"""Offline tests of the R3-03B microbenchmark evidence tooling (no model, no timing acquisition)."""
import json, math, subprocess, sys, unittest
from pathlib import Path

HERE = Path(__file__).resolve().parents[1]
ROOT = HERE.parents[1]
sys.path.insert(0, str(HERE))
import validate_evidence as ve  # noqa: E402


class EvidenceTests(unittest.TestCase):
    def test_01_t_quantiles_match_reference_values(self):
        for df, ref in ((2, 4.302653), (8, 2.306004), (19, 2.093024), (89, 1.986979)):
            self.assertAlmostEqual(ve.t975(df), ref, places=5)

    def test_02_quick_manifest_verification_passes(self):
        r = subprocess.run([sys.executable, str(HERE / "validate_evidence.py"), "--quick"], capture_output=True, text=True)
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)

    def test_03_result_is_consistent_with_records(self):
        res = json.loads((HERE / "MICROBENCH_RESULT.json").read_text())
        rep = json.loads((HERE / "host_records/MICROBENCH_REPORT.json").read_text())
        self.assertEqual(res["classification"], "MICROBENCH_COST_ATTRIBUTION_READY")
        self.assertTrue(math.isclose(res["coefficients"]["c_predictor"]["ns_per_call"], rep["c_predictor_ns_per_call"]["mean"], rel_tol=1e-12))
        self.assertTrue(math.isclose(res["coefficients"]["c_predictor"]["ms_per_token_at_48_calls"] * 1e6, rep["c_predictor_token_ns"]["mean"], rel_tol=1e-12))
        self.assertEqual(res["independent_validation"]["checks"], 52)

    def test_04_protocol_untouched(self):
        import hashlib
        d = ROOT / "experiments/R3-03B-direct-cost-attribution"
        self.assertEqual(hashlib.sha256((d / "PROTOCOL.json").read_bytes()).hexdigest(), "295e8e3f3e376248998baf3ea5fa845c8f3b793706ed3e1c7aae0d8b463b1ae5")


if __name__ == "__main__":
    unittest.main()
