import hashlib, json, sys, unittest
from pathlib import Path
ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT / "tools"))
import r3_03b_e2e as E  # noqa: E402
FZ = json.loads((ROOT / "experiments/R3-03B-end-to-end-opening/E2E_FREEZE.json").read_text())


class T(unittest.TestCase):
    def test_01_freeze_hash_and_driver_hash(self):
        self.assertEqual(E.freeze_sha(FZ), FZ["freeze_sha256"])
        # the original freeze names the driver of commit bee595c; Amendment 1 supersedes it (see test_amendment.py)
        am = json.loads((ROOT / "experiments/R3-03B-end-to-end-opening/E2E_FREEZE_AMENDMENT_1.json").read_text())
        self.assertEqual(am["supersedes_driver_sha256_lf"], FZ["driver_sha256_lf"])

    def test_02_design_matches_driver_constants(self):
        d = FZ["design"]
        self.assertEqual((d["N_blocks"], d["counted_physical_launches"], d["warmup_launches"], d["total_physical_launches"]), (E.N_BLOCKS, E.TOTAL_LAUNCHES, 0, 80))
        s = E.abba_schedule()
        self.assertEqual(hashlib.sha256(json.dumps(s, sort_keys=True, separators=(",", ":")).encode()).hexdigest(), d["schedule_sha256"])
        self.assertEqual(FZ["runtime_sha256"], E.RUNTIME_SHA)
        self.assertEqual(FZ["estimands"]["interval"], {"lower": E.EQUIV_LO, "upper": E.EQUIV_HI})

    def test_03_parents_and_no_trace_policy(self):
        cf = json.loads((ROOT / "experiments/R3-03B-instrumented-conformance-evidence/FREEZE.json").read_text())
        self.assertEqual(FZ["parents"]["conformance_freeze_sha256"], cf["freeze_sha256"])
        self.assertEqual(FZ["runtime_sha256"], cf["runtime_sha256"])
        self.assertIn("event-level retention traces", FZ["retention"]["not_retained"])
        self.assertTrue(FZ["no_timing_claim"])

    def test_04_duration_estimate_is_consistent(self):
        d = FZ["duration_estimate"]
        self.assertEqual(d["total_s"], d["model_s"] + d["cooldown_s"])
        self.assertEqual(d["model_s"], 80 * d["per_launch_s"])
        self.assertEqual(d["cooldown_s"], 2100)


if __name__ == "__main__":
    unittest.main()
