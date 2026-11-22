import hashlib, json, unittest
from pathlib import Path
HERE = Path(__file__).resolve().parents[1]; ROOT = HERE.parents[1]
AD = json.loads((HERE / "N20_ADDENDUM.json").read_text())
BC = json.loads((ROOT / "experiments/R3-03B-end-to-end-block-count/BLOCK_COUNT_ADDENDUM.json").read_text())
PR = json.loads((ROOT / "experiments/R3-03B-direct-cost-attribution/PROTOCOL.json").read_text())


class T(unittest.TestCase):
    def test_01_n20_is_the_protocol_cap_and_gives_80_launches(self):
        d = AD["decision"]
        self.assertEqual((d["fixed_N_blocks"], d["counted_physical_launches"], d["total_physical_launches"]), (20, 80, 80))
        self.assertEqual(d["fixed_N_blocks"], PR["end_to_end_design"]["primary"]["max_blocks"])
        self.assertGreaterEqual(d["fixed_N_blocks"], BC["endpoints"]["8GiB"]["N_req"])

    def test_02_power_numbers_come_from_the_frozen_block_count_addendum(self):
        e = BC["endpoints"]["8GiB"]
        self.assertEqual(AD["why_N20"]["p_explained_if_rho_1_at_N9"], e["approx_power_explained_at_true_rho"]["1.0"])
        self.assertEqual(AD["why_N20"]["p_explained_if_rho_1_at_N20"], e["approx_power_explained_at_cap_blocks"]["1.0"])
        self.assertAlmostEqual(AD["why_N20"]["p_explained_if_rho_1_at_N9"], 0.53, delta=0.01)
        self.assertAlmostEqual(AD["why_N20"]["p_explained_if_rho_1_at_N20"], 0.93, delta=0.01)

    def test_03_4gib_is_not_run(self):
        s = AD["secondary_4GiB"]
        self.assertEqual((s["classification"], s["run"], s["N_req"], s["cap"]), ("UNDERPOWERED_FOR_EXPLAINED_VERDICT", False, 215, 10))

    def test_04_prior_frozen_files_unchanged(self):
        for f, h in AD["prior_files_sha256_at_creation"].items():
            self.assertEqual(hashlib.sha256((ROOT / f).read_bytes()).hexdigest(), h)

    def test_05_parent_conformance_freeze_matches(self):
        fz = json.loads((ROOT / "experiments/R3-03B-instrumented-conformance-evidence/FREEZE.json").read_text())
        self.assertEqual(AD["parents"]["conformance_freeze_sha256"], fz["freeze_sha256"])
        self.assertTrue(AD["no_timing_claim"] and AD["status"].startswith("PRE_ACQUISITION"))
        self.assertIn("equivalence interval [0.5, 2.0]", AD["unchanged_frozen_elements"])


if __name__ == "__main__":
    unittest.main()
