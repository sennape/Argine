"""Deterministic offline tests of the R3-04 frozen protocol (no model, no timing, no network)."""
import json, math, random, sys, unittest
from pathlib import Path
HERE = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(HERE))
import validate_protocol as V  # noqa: E402

def rec(budget, block, arm, wall, pol=1.0, kind="counted"):
    return {"budget": budget, "block": block, "arm": arm, "kind": kind, "predicted_ms": wall * 512, "predicted_n": 512, "policy_timing": {"ns_total": int(pol * 512 * 1e6)}}

def synth(n, a, s, p, noise=0.0, seed=1, pol=(1.0, 20.0, 20.5)):
    r = random.Random(seed); out = []
    for b in range(1, n + 1):
        for arm in V.block_order(b):
            base = {"A": a, "S": s, "P": p}[arm]; pl = dict(zip("ASP", pol))[arm]
            out.append(rec("1.5", b, arm, base + r.gauss(0, noise), pl))
    return out

class T(unittest.TestCase):
    def test_01_policy_identities(self):
        self.assertEqual(V.ARMS, {"A": "b_selected", "S": "predicted_protect_sham", "P": "predicted_protect"})
        for b in V.BUDGET_ORDER:
            a, s, p = (V.config(b, x) for x in "ASP")
            self.assertEqual(a["params"], V.BUDGETS[b]["pair"])
            self.assertEqual({k: v for k, v in s["params"].items() if k in V.BUDGETS[b]["pair"]}, V.BUDGETS[b]["pair"])
            self.assertEqual(s["params"], p["params"]); self.assertNotIn("top_k", a["params"])
            self.assertEqual((a["policy_id"], s["policy_id"], p["policy_id"]), ("b_selected", "predicted_protect_sham", "predicted_protect"))

    def test_02_schedule_balance(self):
        s = V.timing_schedule(); rep = V.balance_report(s)
        for b, r in rep.items():
            self.assertTrue(r["every_block_has_each_arm_twice"] and r["within_block_mean_position_equal_3_5"] and r["n_blocks_multiple_of_6"])
            self.assertEqual(len(set(r["per_arm"].values())), 1); self.assertEqual(len(set(r["first_position_counts"].values())), 1)
            self.assertEqual(len(set(r["permutation_counts"].values())), 1)
        for blk in range(1, 13):
            o = V.block_order(blk); self.assertEqual(o, o[:3] + o[:3][::-1]); self.assertEqual(sorted(o[:3]), ["A", "P", "S"])

    def test_03_sample_counts_and_warmups(self):
        s = V.timing_schedule()
        self.assertEqual((len(s), sum(x["kind"] == "counted" for x in s), sum(x["kind"] == "warmup" for x in s)), (183, 180, 3))
        self.assertEqual({b: sum(1 for x in s if x["budget"] == b and x["kind"] == "counted") for b in V.BUDGET_ORDER}, {"1.5": 108, "4": 36, "8": 36})
        self.assertEqual(len(V.mechanism_schedule()), 10)
        self.assertEqual(sum(1 for m in V.mechanism_schedule() if m["policy_timing"] == "OFF"), 1)

    def test_04_schedule_is_frozen(self):
        sc = json.loads((HERE / "SCHEDULE.json").read_text()); self.assertEqual(sc["timing_schedule_sha256"], V.schedule_sha(V.timing_schedule()))
        self.assertEqual(sc["mechanism_schedule_sha256"], V.schedule_sha(V.mechanism_schedule()))
        self.assertEqual(sc["timing_schedule"], V.timing_schedule())

    def test_05_no_optional_stopping_in_protocol(self):
        p = json.loads((HERE / "PROTOCOL.json").read_text()); self.assertIn("none", p["optional_stopping"])
        self.assertEqual(p["total_counted_launches"], 180); self.assertFalse(p["acquisition_opened"]); self.assertFalse(p["model_runs_performed"])

    def test_06_primary_endpoint_and_contrasts_exact(self):
        r = synth(18, 100.0, 118.0, 117.0)
        c = V.contrasts(r, V.wall_ms_per_token)
        for k, v in (("DELTA_COST", 18.0), ("DELTA_RETENTION", -1.0), ("DELTA_NET", 17.0)):
            self.assertTrue(all(math.isclose(x, v) for x in c[k]))
        for i in range(18): self.assertAlmostEqual(c["DELTA_NET"][i], c["DELTA_COST"][i] + c["DELTA_RETENTION"][i])
        self.assertAlmostEqual(V.wall_ms_per_token({"predicted_ms": 51200.0, "predicted_n": 512}), 100.0)
        self.assertAlmostEqual(V.tps({"predicted_ms": 51200.0, "predicted_n": 512}), 10.0)

    def test_07_warmup_and_incomplete_blocks_rejected(self):
        r = synth(6, 100, 118, 117) + [rec("1.5", 0, "A", 1e6, kind="warmup")]
        self.assertEqual(len(V.contrasts(r, V.wall_ms_per_token)["blocks"]), 6)
        self.assertTrue(all(math.isclose(x, 17.0) for x in V.contrasts(r, V.wall_ms_per_token)["DELTA_NET"]))
        with self.assertRaises(ValueError): V.block_means(synth(6, 1, 2, 3)[:-1], V.wall_ms_per_token)

    def test_08_classification_branches(self):
        loss = V.classify_primary(V.contrasts(synth(18, 100, 118, 117, 1.0), V.wall_ms_per_token)["DELTA_NET"])
        self.assertEqual((loss["net_classification"], loss["claim_class"]), ("NET_LOSS", "Q1_NO_COST_RECOVERY_RESOLVED"))
        win = V.classify_primary(V.contrasts(synth(18, 100, 118, 90, 1.0), V.wall_ms_per_token)["DELTA_NET"])
        self.assertEqual((win["net_classification"], win["claim_class"]), ("NET_WIN", "Q4_NET_RUNTIME_WIN"))
        be = V.classify_primary(V.contrasts(synth(18, 100, 118, 100.0, 1.0), V.wall_ms_per_token)["DELTA_NET"])
        self.assertEqual((be["net_classification"], be["claim_class"], be["precision_adequate"]), ("INCONCLUSIVE", "Q3_APPROXIMATE_BREAK_EVEN_BOUNDED", True))
        noisy = V.classify_primary(V.contrasts(synth(18, 100, 118, 100.0, 40.0, seed=3), V.wall_ms_per_token)["DELTA_NET"])
        self.assertEqual((noisy["net_classification"], noisy["claim_class"]), ("INCONCLUSIVE", "Q5_NO_RELIABLE_CONCLUSION"))
        c = V.contrasts(synth(18, 100, 118, 117.9, 0.5), V.wall_ms_per_token)
        q2 = V.classify_primary(c["DELTA_NET"], [x - (-1.0) - 0.0 for x in c["DELTA_RETENTION"]])
        self.assertIn(q2["claim_class"], ("Q1_NO_COST_RECOVERY_RESOLVED", "Q2_PARTIAL_COST_RECOVERY_STILL_SLOWER"))
        q2b = V.classify_primary(V.contrasts(synth(18, 100, 118, 112, 0.5), V.wall_ms_per_token)["DELTA_NET"], [-6.0 + 0.1 * i for i in range(18)])
        self.assertEqual(q2b["claim_class"], "Q2_PARTIAL_COST_RECOVERY_STILL_SLOWER")

    def test_09_point_estimate_alone_never_classifies(self):
        x = [10.0 if i % 2 else -9.0 for i in range(18)]      # positive mean, interval contains 0
        self.assertGreater(sum(x) / 18, 0); self.assertEqual(V.classify_primary(x)["net_classification"], "INCONCLUSIVE")

    def test_10_boundary_zero_interval(self):
        x = [1.0] * 17 + [1.0 + 1e-12]; r = V.classify_primary(x); self.assertEqual(r["net_classification"], "NET_LOSS")
        self.assertEqual(V.classify_primary([0.0, 0.0] * 9)["net_classification"], "INCONCLUSIVE")

    def test_11_sample_size_arithmetic(self):
        ss = json.loads((HERE / "SAMPLE_SIZE.json").read_text())
        self.assertAlmostEqual(V.precision_target_ms(), 17.466373388671876 / 3)
        p = ss["per_budget"]["1.5"]; self.assertEqual(p["smallest_multiple_of_6_meeting_primary_precision"], 18)
        self.assertLessEqual(V.t975(17) * p["planning_sd_ms"] / math.sqrt(18), V.precision_target_ms()); self.assertGreater(V.t975(11) * p["planning_sd_ms"] / math.sqrt(12), V.precision_target_ms())
        self.assertAlmostEqual(V.power_one_sided(1e-9, 1, 12), 0.025, delta=0.002)
        self.assertGreater(V.power_one_sided(16.9, 11.0, 18), 0.99)

    def test_12_hours_model_calibrated_to_r3_03b(self):
        wall = {"1.5": 431.1, "4": 287.2, "8": 100.846}
        self.assertAlmostEqual(V.launch_seconds("8", "A", wall, "current") + 15 + 15, 330.0, delta=1.0)     # 331 s observed in R3-03B
        h = V.hours_estimate(wall); self.assertGreater(h["current"]["model_bearing_hours_total"], h["fixed"]["model_bearing_hours_total"])

    def test_13_freeze_integrity(self):
        f = json.loads((HERE / "FREEZE.json").read_text()); self.assertEqual(f["freeze_sha256"], V.freeze_sha(f))
        for n, h in f["files_sha256_lf"].items(): self.assertEqual(V.sha_lf(HERE / n), h, n)
        self.assertEqual(f["runtime_sha256"], V.RUNTIME_SHA256)
        tmp = dict(f); tmp["primary_budget"] = "4 GiB"; self.assertNotEqual(V.freeze_sha(tmp), f["freeze_sha256"])

    def test_14_parent_artifacts_unchanged(self):
        p = json.loads((HERE / "PROTOCOL.json").read_text())["parents"]
        for k, v in p.items(): self.assertEqual(V.sha_file(V.REPO / v["path"]), v["sha256"], k)
        self.assertEqual(p["r3_03b_final_freeze"]["freeze_sha256_field"], "1846b00954e033bc9a4cb136cd05373a411d7a8de699923235f31dbfd2f53448")

if __name__ == "__main__":
    unittest.main()
