"""Offline tests of the R3-03B pre-acquisition block-count calculation (no model, no timing)."""
import json, math, sys, unittest
from pathlib import Path

HERE = Path(__file__).resolve().parents[1]
ROOT = HERE.parents[1]
sys.path.insert(0, str(HERE)); sys.path.insert(0, str(ROOT / "experiments/R3-03B-microbench-evidence"))
import block_count as bc  # noqa: E402
import validate_evidence as ve  # noqa: E402


class BlockCountTests(unittest.TestCase):
    def test_01_block_count_rule(self):
        p = bc.P_TOKEN_MS
        n8, _ = bc.n_req(p / 126.15759 * 100, 3.8, 1.7)
        n4, _ = bc.n_req(p / 273.25817 * 100, 6.9, 9.1)
        self.assertEqual(n8, 9)
        self.assertEqual(n4, 215)
        self.assertGreater(n4, bc.CAPS["4GiB"])
        self.assertLessEqual(n8, bc.CAPS["8GiB"])
        sig = math.sqrt((3.8 ** 2 + 1.7 ** 2) / 2); pp = p / 126.15759 * 100
        self.assertLess(8, (ve.t975(7) * sig / (0.5 * pp)) ** 2)      # N-1 violates the rule (minimality)

    def test_02_addendum_matches_code(self):
        a = json.loads((HERE / "BLOCK_COUNT_ADDENDUM.json").read_text())
        self.assertEqual(a["endpoints"]["8GiB"]["fixed_blocks"], 9)
        self.assertEqual(a["endpoints"]["8GiB"]["physical_launches"], 36)
        self.assertEqual(a["endpoints"]["4GiB"]["classification"], "UNDERPOWERED_FOR_EXPLAINED_VERDICT_DO_NOT_RUN_CAPPED_EXPERIMENT")
        self.assertFalse(a["machine_time_estimate"]["old_8_10_5_hour_estimate_applies"])

    def test_03_recomputed_variance_gives_same_n(self):
        a = json.loads((HERE / "BLOCK_COUNT_ADDENDUM.json").read_text())["endpoints"]
        self.assertEqual(a["8GiB"]["N_req_with_recomputed_cv"], 9)
        self.assertEqual(a["8GiB"]["N_req_with_step_adjusted_for_48_calls"], 9)


if __name__ == "__main__":
    unittest.main()
