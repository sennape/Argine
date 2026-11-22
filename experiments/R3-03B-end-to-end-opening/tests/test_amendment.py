import hashlib, json, sys, unittest
from pathlib import Path
ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT / "tools"))
import r3_03b_e2e as E  # noqa: E402
D = ROOT / "experiments/R3-03B-end-to-end-opening"
FZ = json.loads((D / "E2E_FREEZE.json").read_text())
AM = json.loads((D / "E2E_FREEZE_AMENDMENT_1.json").read_text())


class T(unittest.TestCase):
    def test_01_hashes_and_lineage(self):
        self.assertEqual(E.freeze_sha(AM), AM["freeze_sha256"])
        self.assertEqual(E.freeze_sha(FZ), FZ["freeze_sha256"])
        self.assertEqual(AM["parent_freeze_sha256"], FZ["freeze_sha256"])
        self.assertEqual(AM["supersedes_driver_sha256_lf"], FZ["driver_sha256_lf"])
        self.assertEqual(AM["driver_sha256_lf"], E.sha256_lf(ROOT / "tools/r3_03b_e2e.py"))
        self.assertNotEqual(AM["driver_sha256_lf"], FZ["driver_sha256_lf"])
        self.assertEqual(AM["incident_sha256"], hashlib.sha256((ROOT / AM["incident"]).read_bytes()).hexdigest())

    def test_02_scientific_content_is_unchanged(self):
        for k, h in AM["scientific_content_unchanged"].items():
            self.assertEqual(hashlib.sha256(json.dumps(FZ[k], sort_keys=True, separators=(",", ":")).encode()).hexdigest(), h, k)
        self.assertEqual(hashlib.sha256(json.dumps(E.abba_schedule(), sort_keys=True, separators=(",", ":")).encode()).hexdigest(), FZ["design"]["schedule_sha256"])
        self.assertEqual((E.N_BLOCKS, E.TOTAL_LAUNCHES, E.MAX_ATTEMPTS_PER_SLOT), (20, 80, 2))

    def test_03_campaign_state_matches_the_incident_record(self):
        inc = json.loads((ROOT / AM["incident"]).read_text())
        st = AM["campaign_state_at_amendment"]
        self.assertEqual((st["accepted_counted_launches"], st["remaining_accepted_launches"], st["target_accepted_launches"]), (4, 76, 80))
        self.assertEqual(inc["counted_launches_acquired"], 4); self.assertEqual(inc["complete_abba_blocks_acquired"], 1)
        self.assertEqual(inc["launch_005"]["status"], "INCOMPLETE_NONCOUNTED_LAUNCH")
        s5 = E.abba_schedule()[4]
        self.assertEqual((st["next_slot"]["seq"], st["next_slot"]["block"], st["next_slot"]["position"], st["next_slot"]["arm"]), (s5["seq"], s5["block"], s5["position"], s5["arm"]))
        self.assertEqual(st["next_slot"]["retry_identity"], "005-b02p1-B-try2")

    def test_04_original_freeze_driver_hash_is_the_superseded_commit(self):
        import subprocess
        r = subprocess.run(["git", "show", "bee595c:tools/r3_03b_e2e.py"], cwd=ROOT, capture_output=True)
        if r.returncode != 0:
            self.skipTest("git history unavailable")
        self.assertEqual(hashlib.sha256(r.stdout.replace(b"\r\n", b"\n")).hexdigest(), FZ["driver_sha256_lf"])


if __name__ == "__main__":
    unittest.main()
