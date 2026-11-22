import hashlib, json, os, unittest
from pathlib import Path
HERE = Path(__file__).resolve().parents[1]
EXP = HERE.parent
C = json.loads((HERE/"CLOSURE.json").read_text())

class ClosureTests(unittest.TestCase):
    def test_status(self):
        self.assertEqual(C["r3_02g_status"], "R3_02G_TERMINATED_INCOMPLETE")
        self.assertEqual(C["reason"], "DESIGN_SUPERSEDED_BEFORE_UNBLINDING")
        self.assertEqual(C["session1_status"], "PROTOCOL_DEVIATION_NONCONFIRMATORY_SEALED")
        self.assertEqual(C["r3_02_classification_unchanged"], "MECHANISM_INSUFFICIENT")
        self.assertTrue("NOT a failed scientific hypothesis" in C["statement"])
    def test_preserved_hashes(self):
        for p, h in C["preserved_artifact_sha256"].items():
            self.assertEqual(hashlib.sha256((EXP/p).read_bytes()).hexdigest(), h, p)
    def test_classification_text(self):
        t = (EXP/"R3-02-phase2-mechanism/CLASSIFICATION.json").read_text()
        self.assertTrue("MECHANISM_INSUFFICIENT" in t)
    def test_seal_manifest_consistent(self):
        m = json.loads((HERE/"SESSION1_SEAL_MANIFEST.json").read_text())
        self.assertEqual(len(m["manifest"]), C["session1_seal"]["files"])
        self.assertEqual(m["summary"]["root_sha256"], C["session1_seal"]["root_sha256"])
    def test_no_timing_results_in_closure_files(self):
        for n in ("CLOSURE.json", "SESSION1_SEAL_MANIFEST.json"):
            t = (HERE/n).read_text()
            for k in ("b_minus_a", "effect_size", "ci_95", "pass_probability", "median_a", "overhead_s"):
                self.assertFalse(k in t, (n, k))
    def test_seal_verification_optional(self):
        d = os.environ.get("R3_02G_RUN_DIR")
        if not d:
            self.skipTest("R3_02G_RUN_DIR not set")
        m = json.loads((HERE/"SESSION1_SEAL_MANIFEST.json").read_text())["manifest"]
        for rel, h in m.items():
            self.assertEqual(hashlib.sha256((Path(d)/rel).read_bytes()).hexdigest(), h, rel)
if __name__ == "__main__":
    unittest.main()
