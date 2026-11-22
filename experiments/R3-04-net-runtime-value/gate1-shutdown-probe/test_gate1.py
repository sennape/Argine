import copy, json, sys, unittest
from pathlib import Path
HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import validate_gate1 as G


class Gate1(unittest.TestCase):
    def test_verify_and_freeze(self):
        s, c = G.verify(); fz = json.loads((HERE / "FREEZE.json").read_text())
        self.assertEqual(c, "SHUTDOWN_PROBE_SUPPORTS_HELPER"); self.assertEqual(fz["freeze_sha256"], G.freeze_sha(fz)); self.assertEqual(fz["classification"], c)
        self.assertFalse(fz["model_used"]); self.assertEqual(fz["protocol_freeze_sha256"], G.PROTOCOL_FREEZE_SHA256)
        self.assertEqual(s["legacy"]["over_5s"], 10); self.assertEqual(s["own_console_helper"]["over_5s"], 0)

    def test_classifier_other_outcomes(self):
        s, _ = G.verify()
        bad = copy.deepcopy(s); bad["own_console_helper"]["failed"] = 1; self.assertEqual(G.classify(bad), "SHUTDOWN_PROBE_REJECTS_HELPER")
        mid = copy.deepcopy(s); mid["own_console_helper"]["over_5s"] = 1; self.assertEqual(G.classify(mid), "SHUTDOWN_PROBE_INCONCLUSIVE")

    def test_tamper_detected(self):
        fz = json.loads((HERE / "FREEZE.json").read_text()); fz["classification"] = "X"; self.assertNotEqual(fz["freeze_sha256"], G.freeze_sha(fz))


if __name__ == "__main__":
    unittest.main()
