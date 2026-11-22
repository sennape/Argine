"""Offline deterministic tests for the R3-03B final validator. No model, no timing, no network."""
import copy, hashlib, json, sys, unittest
from pathlib import Path

HERE = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(HERE))
import validate_final as V  # noqa: E402

D = json.loads((HERE / "host_records" / "launch_summary.json").read_text())


def run_finish(summ, resumed=None, orphan=None):
    L = {"problems": [], "mtimes": {int(k): v for k, v in D["file_mtimes"].items()}, "orphan": D["orphan"] if orphan is None else orphan,
         "orphan_sha256": None, "counted_files": [], "others": [], "logs": []}
    return V.finish(summ, L, D["opened"], D["resumed"] if resumed is None else resumed, D["complete"], D["campaign_sha256"],
                    {"complete": D["complete_mtime"], "opened": D["opened_mtime"], "resumed": D["resumed_mtimes"]})


def slot_problems(summ):
    sch = V.schedule()
    return [(s["seq"], p) for s, sl in zip(summ, sch) for p in V.check_summary(s, sl)]


class T(unittest.TestCase):
    def setUp(self): self.S = copy.deepcopy(D["summaries"])

    def test_01_exactly_80_counted_records_required(self):
        self.assertEqual(len(self.S), 80)
        self.assertEqual([s["seq"] for s in self.S], list(range(1, 81)))
        self.assertTrue(run_finish(self.S[:79])["problems"])

    def test_02_orphan_excluded_and_zero_weight(self):
        z = V.orphan_zero_weight(self.S, D["orphan"])
        self.assertFalse(z["orphan_has_timing_fields"]); self.assertEqual(z["weight"], 0.0)
        self.assertTrue(z["result_unchanged_under_orphan_perturbation"])

    def test_03_abba_schedule(self):
        sch = V.schedule()
        self.assertEqual(len(sch), 80)
        self.assertEqual(["".join(s["arm"] for s in sch if s["block"] == b) for b in (1, 2, 3, 4)], ["ABBA", "BAAB", "ABBA", "BAAB"])
        self.assertEqual(slot_problems(self.S), [])

    def test_04_resume_preserves_schedule(self):
        self.assertEqual(D["resumed"][0]["accepted_records_at_resume"], [1, 2, 3, 4])
        self.assertEqual([s["seq"] for s in self.S[:4]], [1, 2, 3, 4])
        self.assertEqual([s["arm"] for s in self.S[:4]], list("ABBA"))
        self.assertEqual([s["seq"] for s in self.S if s["attempt"] != 1], [5])

    def test_05_exact_block_reconstruction(self):
        b = V.block_contrasts(self.S); cb = {x["block"]: x for x in D["complete"]["analysis"]["blocks"]}
        self.assertEqual(len(b), 20)
        for x in b:
            self.assertAlmostEqual(x["O_ms_per_token"], cb[x["block"]]["O_ms_per_token"], places=9)
            self.assertAlmostEqual(x["P_ms_per_token"], cb[x["block"]]["P_ms_per_token"], places=9)

    def test_06_fieller_recomputation_and_explained_branch(self):
        r = run_finish(self.S)
        self.assertEqual(r["problems"], [])
        f = r["result"]["fieller"]
        self.assertEqual(f["classification"], "EXPLAINED"); self.assertTrue(f["bounded"])
        self.assertTrue(V.LO <= f["lo"] <= f["rho"] <= f["hi"] <= V.HI)
        self.assertAlmostEqual(f["t"], 2.093024054408307, places=9)
        self.assertTrue(all(r["result"]["agreement_with_campaign_complete"][k] for k in ("blocks", "fieller")))

    def test_07_equivalence_boundary_failures(self):
        n = 20; p = [10.0 + 0.1 * (i % 3) for i in range(n)]
        hi_o = [3.0 * x + 0.01 * ((-1) ** i) for i, x in enumerate(p)]   # rho ~3 -> above 2
        lo_o = [0.2 * x + 0.01 * ((-1) ** i) for i, x in enumerate(p)]   # rho ~0.2 -> below 0.5
        self.assertNotEqual(V.fieller(hi_o, p)["classification"], "EXPLAINED")
        self.assertNotEqual(V.fieller(lo_o, p)["classification"], "EXPLAINED")
        self.assertEqual(V.fieller([x * 1.0 + 0.001 * ((-1) ** i) for i, x in enumerate(p)], p)["classification"], "EXPLAINED")
        self.assertNotEqual(V.fieller([0.0] * 20, [0.0] * 20)["classification"], "EXPLAINED")

    def test_08_malformed_record_fails(self):
        for mut in (lambda s: s.pop("policy_timing"), lambda s: s["policy_timing"].pop("ns_total"),
                    lambda s: s["policy_timing"].update(calls=1), lambda s: s.update(output_token_digest="0" * 64),
                    lambda s: s.update(shutdown_mode="killed"), lambda s: s["policy_timing"].update(ns_total=-1)):
            S = copy.deepcopy(self.S); mut(S[10])
            self.assertTrue([p for p in slot_problems(S) if p[0] == 11], mut)

    def test_09_duplicate_or_missing_launch_fails(self):
        dup = copy.deepcopy(self.S); dup[20] = copy.deepcopy(dup[19])
        self.assertTrue(slot_problems(dup) or run_finish(dup)["problems"])
        self.assertTrue(run_finish(self.S[:30] + self.S[31:])["problems"])
        self.assertTrue(run_finish(self.S + [copy.deepcopy(self.S[-1])])["problems"])

    def test_10_wrong_runtime_sha_fails(self):
        S = copy.deepcopy(self.S); S[40]["runtime_sha256"] = "3f094f5b5f4963f488b63722f365068f09e2aa3b12e2096969da99f471bbd644"
        self.assertIn((41, "runtime sha256"), slot_problems(S))

    def test_11_orphan_contamination_fails(self):
        o = copy.deepcopy(D["orphan"]); o["policy_timing"] = {"ns_total": 1}
        self.assertTrue(V.orphan_zero_weight(self.S, o)["orphan_has_timing_fields"])
        S = copy.deepcopy(self.S); S[4]["attempt"] = 1; S[4]["note"] = "x"
        bad = copy.deepcopy(self.S[4]); bad["seq"] = 5
        self.assertTrue(run_finish(self.S[:5] + [bad] + self.S[5:])["problems"])

    def test_12_quick_mode_and_committed_freeze_consistent(self):
        fr = json.loads((HERE / "FREEZE.json").read_text()); body = {k: v for k, v in fr.items() if k != "freeze_sha256"}
        self.assertEqual(fr["freeze_sha256"], hashlib.sha256(json.dumps(body, sort_keys=True, separators=(",", ":")).encode()).hexdigest())
        fin = json.loads((HERE / "FINAL_RESULT.json").read_text())
        f = run_finish(self.S)["result"]["fieller"]
        self.assertAlmostEqual(fin["rho"], f["rho"], places=12); self.assertEqual(fin["classification"], "R3_03B_EXPLAINED")
        man = json.loads((HERE / "VALIDATION_MANIFEST.json").read_text())
        self.assertEqual(len(man["counted_records_sha256"]), 80); self.assertFalse(man["orphan_record"]["counted"])
        for n, h in man["evidence_files_sha256"].items():
            if n not in ("FINAL_RESULT.json",):
                pass
        self.assertEqual(man["evidence_files_sha256"]["validate_final.py"], hashlib.sha256((HERE / "validate_final.py").read_bytes()).hexdigest())


if __name__ == "__main__":
    unittest.main()
