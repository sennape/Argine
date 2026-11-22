import json, sys, unittest
from pathlib import Path
HERE = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(HERE))
import r1 as R


class T(unittest.TestCase):
    @classmethod
    def setUpClass(cls): cls.problems = R.verify(replay_too=True)
    def test_01_committed_evidence_verifies(self): self.assertEqual(self.problems, [])
    def test_02_stream_shape(self):
        sm = json.loads((HERE / "STREAM_MANIFEST.json").read_text()); self.assertEqual((sm["events"], sm["passes"], sm["events_per_pass"]), (24672, 514, 48))
        self.assertTrue(all(v["equal"] for v in sm["cross_trace"].values())); self.assertTrue(sm["extraction_byte_deterministic"])
    def test_03_recorded_replay_exact_and_expected_values(self):
        r = json.loads((HERE / "FIDELITY_RESULTS.json").read_text())
        self.assertEqual(r["replay"]["A"]["replay"]["decode"]["misses"] / 512, 207.990234375); self.assertEqual(r["replay"]["PP"]["replay"]["decode"]["misses"] / 512, 207.79296875)
        self.assertTrue(r["replay"]["A"]["all_exact"] and r["replay"]["PP"]["all_exact"])
    def test_04_tamper_detected(self):
        p = HERE / "CLAIM_BOUNDARIES.md"; o = p.read_text(encoding="utf-8")
        try:
            p.write_text(o + "x", encoding="utf-8"); self.assertTrue(R.verify(replay_too=False))
        finally: p.write_text(o, encoding="utf-8")

if __name__ == "__main__": unittest.main()
