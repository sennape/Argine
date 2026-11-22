"""Offline deterministic tests of the R3-04 campaign-efficiency amendment and its driver gating. No model, no network, no sleeping."""
import copy, json, random, shutil, sys, tempfile, unittest
from pathlib import Path
from unittest import mock

HERE = Path(__file__).resolve().parents[1]
REPO = HERE.parents[1]
PROTO = REPO / "experiments" / "R3-04-net-runtime-value"
sys.path.insert(0, str(HERE)); sys.path.insert(0, str(PROTO / "tests")); sys.path.insert(0, str(REPO / "tools"))
import validate_amendment as VA  # noqa: E402
import time_model as TM  # noqa: E402
import test_r3_04_tooling as T  # noqa: E402
A, E, V, RT = T.A, T.E, T.V, T.RT
PFZ = json.loads((PROTO / "FREEZE.json").read_text())["freeze_sha256"]
AFZ = json.loads((HERE / "FREEZE.json").read_text())["freeze_sha256"]
AM = json.loads((HERE / "AMENDMENT.json").read_text())


class Amendment(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(); self.t = Path(self.tmp.name); self.addCleanup(self.tmp.cleanup)
        self.sched = A.amended_schedule(AM["design"]["blocks"]); self.out = self.t / "run"

    def put(self, n, sched=None):
        (self.out / "records").mkdir(parents=True, exist_ok=True); (self.out / "launches").mkdir(exist_ok=True)
        rnd = random.Random(3)
        for s in (sched or self.sched)[:n]:
            E.write_atomic(self.out / "records" / f"{s['seq']:03d}.json", {**T.fake_launch(s, 1, wall=100.0 + rnd.gauss(0, 0.5)), "admitted": True, "failures": []})

    def test_01_validator_passes(self):
        self.assertTrue(VA.validate()["ok"])

    def test_02_recommended_schedule_counts_and_budgets(self):
        s = self.sched
        self.assertEqual((len(s), sum(x["kind"] == "counted" for x in s), sum(x["kind"] == "warmup" for x in s)), (146, 144, 2))
        self.assertEqual({b: len({x["block"] for x in s if x["budget"] == b and x["kind"] == "counted"}) for b in ("1.5", "8")}, {"1.5": 18, "8": 6})
        self.assertFalse([x for x in s if x["budget"] == "4"]); self.assertEqual([x["seq"] for x in s], list(range(1, 147)))
        self.assertEqual(AM["design"]["blocks"], {"1.5": 18, "8": 6}); self.assertEqual(V.schedule_sha(s), AM["amended_schedule_sha256"])
        frozen = [x for x in V.timing_schedule() if x["budget"] != "4"]
        self.assertEqual([(x["budget"], x["kind"], x["block"], x["position"], x["arm"], x["cooldown_before_s"]) for x in s], [(x["budget"], x["kind"], x["block"], x["position"], x["arm"], x["cooldown_before_s"]) for x in frozen])

    def test_03_primary_n_and_classification_logic_unchanged(self):
        self.assertEqual(V.BUDGETS["1.5"]["n_blocks"], 18); self.assertEqual(AM["unchanged"]["primary_n_blocks"], 18)
        win, loss, inc = ([-5.0 + d for d in (0.1, -0.1, 0.2, -0.2, 0.0, 0.1)], [5.0 + d for d in (0.1, -0.1, 0.2, -0.2, 0.0, 0.1)], [-1.0, 1.0, -2.0, 2.0, 0.5, -0.5])
        self.assertEqual([V.classify_primary(x)["net_classification"] for x in (win, loss, inc)], ["NET_WIN", "NET_LOSS", "INCONCLUSIVE"])
        pf = json.loads((PROTO / "FREEZE.json").read_text()); self.assertEqual(V.sha_lf(PROTO / "validate_protocol.py"), pf["files_sha256_lf"]["validate_protocol.py"])     # frozen rule/estimator file untouched
        self.assertEqual(pf["freeze_sha256"], "a88359b52891f93ec49e67978d92c2eb3b73db588611bce46b3103b2978c1202")

    def test_04_driver_refuses_wrong_or_tampered_amendment(self):
        with self.assertRaises(SystemExit): A.load_amendment("0" * 64, PFZ)
        with self.assertRaises(SystemExit): A.load_amendment(AFZ, "f" * 64)
        d = self.t / "amend"; shutil.copytree(HERE, d, ignore=shutil.ignore_patterns("__pycache__", "tests"))
        with mock.patch.object(A, "AMEND_DIR", d):
            self.assertEqual(A.load_amendment(AFZ, PFZ)[1], self.sched)
            am = json.loads((d / "AMENDMENT.json").read_text()); am["design"]["blocks"]["1.5"] = 17; (d / "AMENDMENT.json").write_text(json.dumps(am, indent=1, sort_keys=True) + "\n")
            with self.assertRaises(SystemExit) as c: A.load_amendment(AFZ, PFZ)
            self.assertIn("differs from the frozen hash", str(c.exception))
            fz = json.loads((d / "FREEZE.json").read_text()); fz["files_sha256_lf"]["AMENDMENT.json"] = V.sha_lf(d / "AMENDMENT.json"); fz["freeze_sha256"] = V.freeze_sha(fz)      # coherent re-freeze with N=17
            (d / "FREEZE.json").write_text(json.dumps(fz))
            with self.assertRaises(SystemExit) as c2: A.load_amendment(fz["freeze_sha256"], PFZ)
            self.assertIn("changes a frozen N", str(c2.exception))

    def test_05_shutdown_helper_required(self):
        s = self.sched[10]; ok = dict(shutdown_method_requested="own_console_helper", shutdown={"mode": "graceful", "method_used": "own_console_helper", "latency_s": 1.0, "helper_returncode": 0})
        self.assertEqual(A.validate(T.fake_launch(s, 1, **ok), s, require_helper=True), [])
        for bad in ({"shutdown_method_requested": "legacy"}, {"shutdown": {**ok["shutdown"], "method_used": "legacy_after_helper_failure", "helper_returncode": 2}}, {"shutdown": {**ok["shutdown"], "helper_returncode": 3}}):
            self.assertTrue(any("helper required" in f for f in A.validate(T.fake_launch(s, 1, **{**ok, **bad}), s, require_helper=True)))
        self.assertEqual(A.validate(T.fake_launch(s, 1), s), [])                                     # unamended campaign unchanged
        args = ["--runtime", "x", "--runtime-sha256", RT, "--protocol-freeze-sha256", PFZ, "--amendment-sha256", AFZ, "--opening-freeze-sha256", "0" * 64, "--out-dir", str(self.out), "--dry-run"]
        with mock.patch("builtins.print"):
            self.assertEqual(A.main(args + ["--shutdown-method", "legacy"]), 1); self.assertEqual(A.main(args), 1)       # default legacy refused under the amendment
            self.assertEqual(A.main(args + ["--shutdown-method", "own_console_helper"]), 0)

    def test_06_amendment_sha_enforced_in_driver(self):
        args = ["--runtime", "x", "--runtime-sha256", RT, "--protocol-freeze-sha256", PFZ, "--opening-freeze-sha256", "0" * 64, "--out-dir", str(self.out), "--shutdown-method", "own_console_helper"]
        with self.assertRaises(SystemExit): A.main(args + ["--amendment-sha256", "0" * 64, "--dry-run"])
        with mock.patch("builtins.print") as pr:
            self.assertEqual(A.main(args + ["--amendment-sha256", AFZ, "--dry-run"]), 0)
        d = json.loads(pr.call_args[0][0]); self.assertEqual((d["launches"], d["amended"], d["schedule_sha256"], d["next_slot"]["seq"]), (146, True, AM["amended_schedule_sha256"], 1))
        ofp = self.t / "of.json"
        def of(**kw):
            o = {"artifact": "x", "protocol_freeze_sha256": PFZ, "driver_sha256_lf": V.sha_lf(Path(A.__file__)), "runtime_sha256": RT, **kw}; o["freeze_sha256"] = V.freeze_sha(o); ofp.write_text(json.dumps(o)); return o["freeze_sha256"]
        base = ["--runtime", "x", "--runtime-sha256", RT, "--protocol-freeze-sha256", PFZ, "--out-dir", str(self.out), "--shutdown-method", "own_console_helper", "--attest-clean-environment"]
        with mock.patch.object(A, "OPENING_FREEZE", ofp), mock.patch("builtins.print"):
            h = of(); self.assertEqual(A.main(base + ["--opening-freeze-sha256", h, "--amendment-sha256", AFZ]), 1)      # opening freeze does not name the amendment
            h = of(amendment_freeze_sha256=AFZ); self.assertEqual(A.main(base + ["--opening-freeze-sha256", h]), 1)        # opening freeze names it, flag absent
            h = of(amendment_freeze_sha256="1" * 64); self.assertEqual(A.main(base + ["--opening-freeze-sha256", h, "--amendment-sha256", AFZ]), 1)

    def test_07_no_analysis_before_complete_amended_acquisition(self):
        self.put(145)
        with self.assertRaises(ValueError) as c: A.analyze(self.out, self.sched)
        self.assertIn("no interim analysis", str(c.exception))
        self.put(146); res = A.analyze(self.out, self.sched)
        self.assertEqual(sorted(res["budgets"]), ["1.5", "8"]); self.assertEqual(res["budgets"]["1.5"]["n_blocks"], 18); self.assertEqual(res["budgets"]["8"]["n_blocks"], 6)
        self.assertIn("PRIMARY_CLASSIFICATION", res["budgets"]["1.5"]); self.assertTrue(res["budgets"]["8"]["classification"].startswith("NONE"))

    def test_08_resume_orphan_and_prefix_safety(self):
        self.put(20); self.assertEqual(sorted(E.load_accepted(self.out, self.sched, RT)), list(range(1, 21)))
        (self.out / "launches" / "021-1.5GiB-c04p3-S-try1.stderr.log").write_text("stop processing: n_tokens = 698\n")
        made = E.classify_orphans(self.out, self.sched); self.assertEqual(made, ["021.try1.ORPHAN_NONCOUNTED.json"]); self.assertEqual(sorted(E.load_accepted(self.out, self.sched, RT)), list(range(1, 21)))
        E.write_atomic(self.out / "records" / "147.json", {**T.fake_launch(self.sched[0], 1, seq=147), "admitted": True, "failures": []})
        with self.assertRaises(ValueError): E.load_accepted(self.out, self.sched, RT)                # nothing beyond the amended schedule (e.g. a 4 GiB slot) is ever accepted
        (self.out / "records" / "147.json").unlink(); (self.out / "records" / "010.json").unlink()
        with self.assertRaises(ValueError): E.load_accepted(self.out, self.sched, RT)                # gap in the accepted prefix

    def test_09_resume_requires_same_amendment(self):
        ofp = self.t / "of.json"; o = {"artifact": "x", "protocol_freeze_sha256": PFZ, "driver_sha256_lf": V.sha_lf(Path(A.__file__)), "runtime_sha256": RT, "amendment_freeze_sha256": AFZ}; o["freeze_sha256"] = V.freeze_sha(o); ofp.write_text(json.dumps(o))
        (self.out / "records").mkdir(parents=True); (self.out / "CAMPAIGN_OPENED.json").write_text(json.dumps({"protocol_freeze_sha256": PFZ, "runtime_sha256": RT, "amendment_freeze_sha256": None}))
        args = ["--runtime", "x", "--runtime-sha256", RT, "--protocol-freeze-sha256", PFZ, "--amendment-sha256", AFZ, "--opening-freeze-sha256", o["freeze_sha256"], "--out-dir", str(self.out),
                "--shutdown-method", "own_console_helper", "--attest-clean-environment", "--resume"]
        with mock.patch.object(A, "OPENING_FREEZE", ofp), mock.patch.object(A.E, "sha256_file", return_value=RT), mock.patch("builtins.print") as pr:
            self.assertEqual(A.main(args), 1)
        self.assertIn("--resume requires", pr.call_args[0][0])

    def test_10_time_model_pure_and_bounded(self):
        for n in ("FULL", "PRIMARY_PLUS_8G", "PRIMARY_ONLY"):
            lo, ce, hi = (TM.hours({"FULL": {"1.5": 18, "4": 6, "8": 6}, "PRIMARY_PLUS_8G": {"1.5": 18, "8": 6}, "PRIMARY_ONLY": {"1.5": 18}}[n], s, "helper")["wall_h"] for s in TM.SCENARIOS)
            self.assertLess(lo, ce); self.assertLess(ce, hi)
        self.assertEqual(TM.hours({"1.5": 18, "8": 6}, "central", "helper")["physical_launches"], 146)
        self.assertGreater(TM.hours({"1.5": 18, "8": 6}, "central", "legacy")["wall_h"], TM.hours({"1.5": 18, "8": 6}, "central", "helper")["wall_h"])

    def test_11_no_shell_true_and_opening_freeze_consistent(self):
        self.assertNotIn("shell=True", (REPO / "tools" / "r3_04_acquire.py").read_text()); of = PROTO / "OPENING_FREEZE.json"; self.assertTrue(not of.exists() or (json.loads(of.read_text())["amendment_freeze_sha256"] == AFZ and json.loads(of.read_text())["campaign_authorized"] is False))      # opening (if present) names this amendment and is not an authorization


if __name__ == "__main__":
    unittest.main()
