"""Deterministic offline tests of tools/r3_03b_e2e.py. No model is run; the subprocess/HTTP boundary is mocked."""
import contextlib, io, json, math, sys, tempfile, unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT / "tools"))
import r3_03b_e2e as E  # noqa: E402
import r3_03b_conformance as C  # noqa: E402

PLAN = json.loads((ROOT / "experiments/R3-02-calibration-mechanism/ACQUISITION_PLAN.json").read_text())
CFGS = {c["config_id"]: c for c in PLAN["configurations"]}
GOOD_TIMING = {"schema": "r3-03b-v1", "calls": 24672, "ns_total": 3_000_000_000, "ns_prediction_update": 2_000_000_000, "ns_victim_selection": 100_000_000,
               "victim_calls": 7966, "ns_event_lock_wait": 5000, "ns_event_locked_total": 3_500_000_000, "hist_log2": [0] * 39 + [24672]}


def good_rec(**kw):
    r = {"output_token_digest": C.FROZEN_TOKEN_DIGEST, "n_tokens": 512, "shutdown_mode": "graceful", "policy_timing_records": [dict(GOOD_TIMING)],
         "server_timings": {"predicted_n": 512, "predicted_ms": 64000.0}, "duplicate_event_refusals": 0}
    r.update(kw)
    return r


class Schedule(unittest.TestCase):
    def test_01_eighty_launches_twenty_abba_blocks_alternating(self):
        s = E.abba_schedule()
        self.assertEqual((len(s), E.N_BLOCKS, E.TOTAL_LAUNCHES), (80, 20, 80))
        self.assertEqual([x["seq"] for x in s], list(range(1, 81)))
        for b in range(1, 21):
            arms = "".join(x["arm"] for x in s if x["block"] == b)
            self.assertEqual(arms, "ABBA" if b % 2 else "BAAB")
        self.assertEqual(sum(x["arm"] == "A" for x in s), 40)
        self.assertEqual({x["policy_id"] for x in s if x["arm"] == "A"}, {"b_selected"})
        self.assertEqual({x["policy_id"] for x in s if x["arm"] == "B"}, {"predicted_protect_sham"})
        self.assertEqual({x["config_id"] for x in s}, {"A-8GiB", "B-8GiB"})

    def test_02_no_warmups_and_frozen_cooldowns(self):
        s = E.abba_schedule()
        self.assertTrue(all(x["position"] in (1, 2, 3, 4) for x in s))
        self.assertEqual([x["cooldown_before_s"] for x in s[:5]], [60, 15, 15, 15, 60])
        self.assertEqual(sum(x["cooldown_before_s"] for x in s), 20 * 60 + 20 * 45)

    def test_03_frozen_constants_agree_with_protocol(self):
        p = json.loads((ROOT / "experiments/R3-03B-direct-cost-attribution/PROTOCOL.json").read_text())
        self.assertEqual((E.EQUIV_LO, E.EQUIV_HI), (p["equivalence_interval"]["lower"], p["equivalence_interval"]["upper"]))
        self.assertEqual(E.N_BLOCKS, p["end_to_end_design"]["primary"]["max_blocks"])
        self.assertEqual(E.C.FROZEN_TOKEN_DIGEST, "ac28efbe13c80f363f508a6f19d4b1d3af189eb26d6e9c81309376033075cdfa")

    def test_04_block_count_is_not_a_cli_option(self):
        with contextlib.redirect_stderr(io.StringIO()), self.assertRaises(SystemExit):
            E.main(["--out-dir", "x", "--blocks", "9"])

    def test_05_duration_estimate_arithmetic(self):
        d = E.duration_estimate(100)
        self.assertEqual((d["model_s"], d["cooldown_s"], d["per_block_s"]), (8000, 2100, 100 * 4 + 45 + 60))
        self.assertAlmostEqual(d["total_h"], 10100 / 3600)


class EnvAndValidation(unittest.TestCase):
    def test_06_env_timing_on_no_trace_otherwise_equal_to_conformance_on(self):
        for cid in ("A-8GiB", "B-8GiB"):
            e = E.build_env_e2e(PLAN, CFGS[cid], {"PATH": "p", "ARGINE_RETENTION_TRACE": "leak"})
            ref = C.build_env(PLAN, CFGS[cid], Path("t"), True, {"PATH": "p"})
            self.assertEqual(e["ARGINE_POLICY_TIMING"], "1")
            self.assertNotIn("ARGINE_RETENTION_TRACE", e)
            ref.pop("ARGINE_RETENTION_TRACE")
            self.assertEqual(e, ref)
            self.assertEqual(e["ARGINE_EXPERT_BUDGET"], str(8 * 1024 ** 3))

    def test_07_valid_launch_passes_and_each_defect_is_caught(self):
        self.assertEqual(E.validate_launch(good_rec(), C.FROZEN_TOKEN_DIGEST), [])
        bad = [dict(output_token_digest="0" * 64), dict(n_tokens=511), dict(shutdown_mode="terminate_fallback"), dict(policy_timing_records=[]),
               dict(policy_timing_records=[dict(GOOD_TIMING), dict(GOOD_TIMING)]), dict(policy_timing_records=[dict(GOOD_TIMING, calls=1)]),
               dict(server_timings={}), dict(server_timings={"predicted_n": 512, "predicted_ms": 0}), dict(duplicate_event_refusals=1)]
        for kw in bad:
            self.assertTrue(E.validate_launch(good_rec(**kw), C.FROZEN_TOKEN_DIGEST), kw)


class Shutdown(unittest.TestCase):
    def test_08_driver_uses_validated_graceful_stop_and_never_terminates_directly(self):
        src = (ROOT / "tools/r3_03b_e2e.py").read_text()
        self.assertIn("C.graceful_stop(proc, GRACEFUL_STOP_TIMEOUT_S)", src)
        self.assertNotIn(".terminate(", src)
        self.assertNotIn(".kill(", src)
        self.assertNotIn("shell=True", src)

    def test_09_run_launch_wires_env_command_and_records_shutdown_and_timing(self):
        slot = E.abba_schedule()[0]
        written = {}

        class FakeProc:
            def __init__(self, cmd, env=None, stdout=None, stderr=None):
                written.update(cmd=cmd, env=env)
                stderr.write(("argine: policy_timing " + json.dumps(GOOD_TIMING) + "\n").encode()); stderr.flush()
            def poll(self): return None

        class Resp:
            def __init__(self, body, status=200): self._b, self.status = body, status
            def read(self): return self._b
            def __enter__(self): return self
            def __exit__(self, *a): return False

        payload = {"tokens": list(range(512)), "timings": {"predicted_n": 512, "predicted_ms": 64000.0}}
        def urlopen(req, timeout=None):
            url = req if isinstance(req, str) else req.full_url
            return Resp(b"ok") if url.endswith("/health") else Resp(json.dumps(payload).encode())

        import r3_02_acquire as acq
        with tempfile.TemporaryDirectory() as d, mock.patch.object(E.subprocess, "Popen", FakeProc), \
                mock.patch.object(E.urllib.request, "urlopen", urlopen), mock.patch.object(C, "graceful_stop", return_value="graceful") as gs, \
                mock.patch.object(acq, "workload_prompt_for_request", return_value="p"):
            (Path(d) / "launches").mkdir()
            rec = E.run_launch(PLAN, CFGS["B-8GiB"], Path("llama-server.exe"), Path(d), slot, 1, "sha")
        gs.assert_called_once()
        self.assertEqual(rec["shutdown_mode"], "graceful")
        self.assertEqual(len(rec["policy_timing_records"]), 1)
        self.assertNotIn("ARGINE_RETENTION_TRACE", written["env"])
        self.assertEqual(written["env"]["ARGINE_POLICY_TIMING"], "1")
        self.assertNotIn("shell", written)
        self.assertEqual(rec["n_tokens"], 512)
        self.assertEqual(rec["server_timings"]["predicted_ms"], 64000.0)


class Refusals(unittest.TestCase):
    def _freeze(self, d, **over):
        fz = {"runtime_sha256": E.RUNTIME_SHA, "driver_sha256_lf": "1" * 64, "duration_estimate": {"per_launch_s": 100}}
        fz.update(over)
        fz["freeze_sha256"] = E.freeze_sha(fz)
        (Path(d) / "F.json").write_text(json.dumps(fz))
        return fz

    def _amend(self, d, fz, **over):
        am = {"parent_freeze_sha256": fz["freeze_sha256"], "supersedes_driver_sha256_lf": fz["driver_sha256_lf"],
              "driver_sha256_lf": E.sha256_lf(Path(E.__file__))}
        am.update(over)
        am["freeze_sha256"] = E.freeze_sha(am)
        (Path(d) / "A.json").write_text(json.dumps(am))
        return am

    def _run(self, argv):
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            try:
                rc = E.main(argv)
            except SystemExit as e:
                rc = str(e)
        return rc, buf.getvalue()

    def test_10_refusals(self):
        with tempfile.TemporaryDirectory() as d, mock.patch.object(E, "FREEZE_PATH", Path(d) / "F.json"), \
                mock.patch.object(E, "AMENDMENT_PATH", Path(d) / "A.json"):
            fz = self._freeze(d); am = self._amend(d, fz)
            f, a = fz["freeze_sha256"], am["freeze_sha256"]
            base = ["--runtime", "x", "--runtime-sha256", E.RUNTIME_SHA, "--freeze-sha256", f, "--amendment-sha256", a, "--out-dir", str(Path(d) / "out")]
            rc, out = self._run(base + ["--dry-run"])
            self.assertEqual(rc, 0); self.assertIn('"launches": 80', out); self.assertIn('"accepted_records_found": 0', out)
            rc, out = self._run(base)                                                   # no attestation
            self.assertEqual(rc, 1); self.assertIn("attest", out)
            rc, _ = self._run(["--runtime", "x", "--runtime-sha256", "0" * 64, "--freeze-sha256", f, "--amendment-sha256", a, "--out-dir", "o", "--dry-run"])
            self.assertEqual(rc, 1)                                                     # not the conformance-frozen runtime
            rc, _ = self._run(["--runtime", "x", "--runtime-sha256", E.RUNTIME_SHA, "--freeze-sha256", "0" * 64, "--amendment-sha256", a, "--out-dir", "o", "--dry-run"])
            self.assertIn("REFUSED", rc)                                                # wrong freeze hash
            rc, _ = self._run(["--runtime", "x", "--runtime-sha256", E.RUNTIME_SHA, "--freeze-sha256", f, "--amendment-sha256", "0" * 64, "--out-dir", "o", "--dry-run"])
            self.assertIn("REFUSED", rc)                                                # wrong amendment hash
            rc, _ = self._run(["--runtime", "x", "--runtime-sha256", E.RUNTIME_SHA, "--freeze-sha256", f, "--out-dir", "o", "--dry-run"])
            self.assertEqual(rc, 1)                                                     # amendment hash is mandatory
            am2 = self._amend(d, fz, driver_sha256_lf="0" * 64)
            rc, _ = self._run(["--runtime", "x", "--runtime-sha256", E.RUNTIME_SHA, "--freeze-sha256", f, "--amendment-sha256", am2["freeze_sha256"], "--out-dir", "o", "--dry-run"])
            self.assertIn("REFUSED", rc)                                                # driver differs from amended frozen driver
            am3 = self._amend(d, fz, parent_freeze_sha256="2" * 64)
            rc, _ = self._run(["--runtime", "x", "--runtime-sha256", E.RUNTIME_SHA, "--freeze-sha256", f, "--amendment-sha256", am3["freeze_sha256"], "--out-dir", "o", "--dry-run"])
            self.assertIn("REFUSED", rc)                                                # amendment belongs to another freeze
            fz4 = self._freeze(d, runtime_sha256="1" * 64); am4 = self._amend(d, fz4)
            rc, _ = self._run(["--runtime", "x", "--runtime-sha256", E.RUNTIME_SHA, "--freeze-sha256", fz4["freeze_sha256"], "--amendment-sha256", am4["freeze_sha256"], "--out-dir", "o", "--dry-run"])
            self.assertEqual(rc, 1)                                                     # freeze names another runtime


class Analysis(unittest.TestCase):
    def test_11_fieller_endpoints_satisfy_the_defining_quadratic(self):
        o = [1.1, 0.9, 1.3, 1.0, 1.2, 0.8, 1.15, 1.05, 0.95, 1.25]
        p = [1.0, 1.1, 1.2, 0.9, 1.0, 1.05, 0.95, 1.1, 1.0, 1.15]
        r = E.fieller(o, p)
        n = len(o); t = r["t"]
        import statistics as st
        mo, mp = st.fmean(o), st.fmean(p)
        voo, vpp = st.variance(o) / n, st.variance(p) / n
        vop = sum((x - mo) * (y - mp) for x, y in zip(o, p)) / (n - 1) / n
        for rho in (r["lo"], r["hi"]):
            lhs = (mo - rho * mp) ** 2
            rhs = t * t * (voo - 2 * rho * vop + rho * rho * vpp)
            self.assertAlmostEqual(lhs, rhs, places=9)
        self.assertLess(r["lo"], r["rho"]); self.assertGreater(r["hi"], r["rho"])

    def test_12_classification_branches(self):
        rng = [0.0, 0.01, -0.01, 0.02, -0.02, 0.0, 0.01, -0.01, 0.02]
        p = [1 + x for x in rng]
        self.assertEqual(E.fieller([1.0 * q + z * 0.1 for q, z in zip(p, rng)], p)["classification"], "EXPLAINED")
        self.assertEqual(E.fieller([0.1 * q + z * 0.1 for q, z in zip(p, rng)], p)["classification"], "REJECTED_UNEXPLAINED")
        self.assertEqual(E.fieller([5.0 * q + z * 0.1 for q, z in zip(p, rng)], p)["classification"], "REJECTED_UNEXPLAINED")
        wide = [1.0, 2.5, -0.5, 2.0, 0.0, 1.8, 0.2, 2.2, 1.3]
        self.assertEqual(E.fieller(wide, p)["classification"], "INCONCLUSIVE")
        noisy_p = [1.0, -1.0, 0.5, -0.5, 0.2, -0.2, 0.1, -0.1, 0.0]
        r = E.fieller([1.0] * 9, noisy_p)
        self.assertEqual((r["classification"], r["bounded"]), ("UNTESTABLE", False))

    def test_13_block_contrasts_use_the_abba_pair_means(self):
        recs = []
        for s in E.abba_schedule(2):
            wall = 100.0 if s["arm"] == "A" else 104.0 + s["block"]
            pol = 1e9 if s["arm"] == "A" else 3e9
            recs.append({**s, "admitted": True, "n_tokens": 512, "server_timings": {"predicted_ms": wall * 512, "predicted_n": 512},
                         "policy_timing_records": [{"ns_total": pol * 512 / 1.0}]})
        bc = E.block_contrasts(recs)
        self.assertAlmostEqual(bc[0]["O_ms_per_token"], 5.0); self.assertAlmostEqual(bc[1]["O_ms_per_token"], 6.0)
        self.assertAlmostEqual(bc[0]["P_ms_per_token"], 2000.0)
        with self.assertRaises(ValueError):
            E.block_contrasts(recs[:3])

    def test_15_lf_hash_is_line_ending_insensitive(self):
        with tempfile.TemporaryDirectory() as d:
            a, b = Path(d) / "a", Path(d) / "b"
            a.write_bytes(b"x\ny\n"); b.write_bytes(b"x\r\ny\r\n")
            self.assertEqual(E.sha256_lf(a), E.sha256_lf(b))

    def test_14_t_quantile_df19(self):
        self.assertAlmostEqual(E._t975(19), 2.093024, places=5)


if __name__ == "__main__":
    unittest.main()
