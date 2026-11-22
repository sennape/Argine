import json, sys, unittest
from pathlib import Path
HERE = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(HERE))
import build_final_evidence as B


class T(unittest.TestCase):
    def test_01_verify_clean(self): self.assertEqual(B.verify(), [])
    def test_02_counts(self):
        ig = json.loads((HERE / "CAMPAIGN_INTEGRITY.json").read_text())
        self.assertEqual((ig["physical_launches"], ig["counted"], ig["warmups"], ig["launches_at_4GiB"]), (146, 144, 2, 0))
        self.assertEqual(ig["by_budget_kind"], {"1.5_counted": 108, "1.5_warmup": 1, "8_counted": 36, "8_warmup": 1})
    def test_03_classification_is_frozen_rule(self):
        o = json.loads((HERE / "OFFICIAL_ANALYSIS.json").read_text())["budgets"]
        ci = o["1.5"]["PRIMARY_CLASSIFICATION"]["delta_net_ci"]
        self.assertEqual(o["1.5"]["PRIMARY_CLASSIFICATION"]["net_classification"], "NET_LOSS" if ci["lo"] > 0 else "NET_WIN" if ci["hi"] < 0 else "INCONCLUSIVE")
        self.assertTrue(str(o["8"]["classification"]).startswith("NONE"))
    def test_04_no_host_paths(self):
        for n in B.FILES: self.assertNotIn("alpel", (HERE / n).read_text(encoding="utf-8"))
    def test_05_tamper_detected(self):
        p = HERE / "CLAIM_BOUNDARIES.md"; orig = p.read_text(encoding="utf-8")
        try:
            p.write_text(orig + "x", encoding="utf-8"); self.assertTrue(B.verify())
        finally: p.write_text(orig, encoding="utf-8")
        self.assertEqual(B.verify(), [])

if __name__ == "__main__": unittest.main()
