"""R3-03B e2e shutdown incident: resume safety, interrupt semantics, acceptance point. No model; subprocess/HTTP mocked."""
import copy, json, sys, tempfile, unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT / "tools"))
import r3_03b_e2e as E  # noqa: E402
import r3_03b_conformance as C  # noqa: E402

PLAN = json.loads((ROOT / "experiments/R3-02-calibration-mechanism/ACQUISITION_PLAN.json").read_text())
CFGS = {c["config_id"]: c for c in PLAN["configurations"]}
SCHED = E.abba_schedule()
TIMING = {"schema": "r3-03b-v1", "calls": 24672, "ns_total": 3_000_000_000, "ns_prediction_update": 2_000_000_000, "ns_victim_selection": 100_000_000,
          "victim_calls": 7966, "ns_event_lock_wait": 5000, "ns_event_locked_total": 3_500_000_000, "hist_log2": [0] * 39 + [24672]}


def accepted_rec(seq, wall=100.0, pol=1e9):
    s = SCHED[seq - 1]
    return {**s, "attempt": 1, "runtime_sha256": E.RUNTIME_SHA, "output_token_digest": C.FROZEN_TOKEN_DIGEST, "n_tokens": 512, "shutdown_mode": "graceful",
            "policy_timing_records": [dict(TIMING, ns_total=int(pol), ns_prediction_update=int(pol) // 2, ns_victim_selection=int(pol) // 10, ns_event_locked_total=int(pol) * 2)], "server_timings": {"predicted_n": 512, "predicted_ms": wall * 512},
            "duplicate_event_refusals": 0, "error": None, "failures": [], "admitted": True, "wall_seconds": 60.0}


def campaign(d, n_accepted=4, with_try1_logs_for=None):
    out = Path(d) / "camp"
    (out / "records").mkdir(parents=True); (out / "launches").mkdir()
    for seq in range(1, n_accepted + 1):
        (out / "records" / f"{seq:03d}.json").write_text(json.dumps(accepted_rec(seq)))
        s = SCHED[seq - 1]
        (out / "launches" / f"{seq:03d}-b{s['block']:02d}p{s['position']}-{s['arm']}-try1.stderr.log").write_text("ok policy_timing\n")
    if with_try1_logs_for:
        s = SCHED[with_try1_logs_for - 1]
        tag = f"{with_try1_logs_for:03d}-b{s['block']:02d}p{s['position']}-{s['arm']}-try1"
        (out / "launches" / f"{tag}.stderr.log").write_text("srv release: stop processing: n_tokens = 698\n")
        (out / "launches" / f"{tag}.stdout.log").write_text("")
    return out


class ResumeState(unittest.TestCase):
    def test_01_four_accepted_records_survive_and_next_slot_is_005(self):
        with tempfile.TemporaryDirectory() as d:
            out = campaign(d, 4, 5)
            acc = E.load_accepted(out, SCHED, E.RUNTIME_SHA)
            self.assertEqual(sorted(acc), [1, 2, 3, 4])
            nxt = next(s for s in SCHED if s["seq"] not in acc)
            self.assertEqual((nxt["seq"], nxt["block"], nxt["position"], nxt["arm"], nxt["policy_id"]), (5, 2, 1, "B", "predicted_protect_sham"))
            self.assertEqual(E.TOTAL_LAUNCHES - len(acc), 76)
            self.assertEqual("A B B A".split(), [SCHED[i]["arm"] for i in range(4)])

    def test_02_orphan_try1_is_marked_noncounted_and_never_overwritten(self):
        with tempfile.TemporaryDirectory() as d:
            out = campaign(d, 4, 5)
            before = {p.name: p.read_bytes() for p in (out / "launches").glob("005*")}
            made = E.classify_orphans(out, SCHED)
            self.assertEqual(made, ["005.try1.ORPHAN_NONCOUNTED.json"])
            m = json.loads((out / "records" / made[0]).read_text())
            self.assertEqual((m["status"], m["inference_completed_in_log"], m["cleaning_up_logged"], m["policy_timing_present"]),
                             ("INCOMPLETE_NONCOUNTED_LAUNCH", True, False, False))
            self.assertEqual(E.classify_orphans(out, SCHED), [])                       # idempotent
            self.assertEqual(E.prior_attempts(out, 5), 1)                              # retry will be try2, same slot
            self.assertEqual(E.prior_attempts(out, 6), 0)
            self.assertEqual(before, {p.name: p.read_bytes() for p in (out / "launches").glob("005*")})
            self.assertFalse((out / "records" / "005.json").exists())                  # marker is not an accepted record

    def test_03_incomplete_attempt_is_never_an_estimator_input(self):
        with tempfile.TemporaryDirectory() as d:
            out = campaign(d, 4, 5)
            E.classify_orphans(out, SCHED)
            (out / "records" / "005.try1.FAILED.json").write_text(json.dumps(dict(accepted_rec(5), admitted=True)))   # even a mislabelled file
            self.assertEqual(sorted(E.load_accepted(out, SCHED, None)), [1, 2, 3, 4])
            bc = E.block_contrasts(list(E.load_accepted(out, SCHED, None).values()))      # block 1 only: complete ABBA
            self.assertEqual([b["block"] for b in bc], [1])

    def test_04_load_accepted_fails_closed(self):
        for mutate, msg in ((lambda r: r.update(arm="A"), "arm"), (lambda r: r.update(admitted=False), "admitted"),
                            (lambda r: r.update(shutdown_mode="terminate_fallback"), "admission"), (lambda r: r.update(output_token_digest="0" * 64), "admission"),
                            (lambda r: r.update(runtime_sha256="9" * 64), "runtime")):
            with tempfile.TemporaryDirectory() as d:
                out = campaign(d, 4)
                p = out / "records" / "003.json"; r = json.loads(p.read_text()); mutate(r); p.write_text(json.dumps(r))
                with self.assertRaises(ValueError, msg=msg):
                    E.load_accepted(out, SCHED, E.RUNTIME_SHA)
        with tempfile.TemporaryDirectory() as d:                                         # gap in the prefix
            out = campaign(d, 4); (out / "records" / "002.json").unlink()
            with self.assertRaises(ValueError):
                E.load_accepted(out, SCHED, None)

    def test_05_attempt_numbers_and_budget_across_resume(self):
        with tempfile.TemporaryDirectory() as d:
            out = campaign(d, 4, 5)
            self.assertEqual(E.prior_attempts(out, 5) + 1, 2)
            (out / "records" / "005.try2.FAILED.json").write_text("{}")
            self.assertEqual(E.prior_attempts(out, 5), 2)
            self.assertGreaterEqual(E.prior_attempts(out, 5), E.MAX_ATTEMPTS_PER_SLOT)  # frozen rule: no third attempt

    def test_06_schedule_is_unchanged_and_target_is_80(self):
        import hashlib
        fz = json.loads((ROOT / "experiments/R3-03B-end-to-end-opening/E2E_FREEZE.json").read_text())
        self.assertEqual(hashlib.sha256(json.dumps(SCHED, sort_keys=True, separators=(",", ":")).encode()).hexdigest(), fz["design"]["schedule_sha256"])
        self.assertEqual((E.N_BLOCKS, E.TOTAL_LAUNCHES, len(SCHED)), (20, 80, 80))
        self.assertEqual(len({s["seq"] for s in SCHED}), 80)


class LaunchSemantics(unittest.TestCase):
    def _run(self, graceful_side_effect=None, graceful_ret="graceful", urlopen_exc=None, stderr_line=True):
        slot = SCHED[4]
        stops = {}

        class FakeProc:
            pid = 1
            def __init__(self, cmd, env=None, stdout=None, stderr=None):
                if stderr_line:
                    stderr.write(("argine: policy_timing " + json.dumps(TIMING) + "\n").encode()); stderr.flush()
                self.alive = True
            def poll(self): return None if self.alive else 0
            def terminate(self): stops["terminate"] = stops.get("terminate", 0) + 1; self.alive = False
            def kill(self): stops["kill"] = stops.get("kill", 0) + 1; self.alive = False
            def wait(self, timeout=None): return 0

        class Resp:
            def __init__(self, b, status=200): self._b, self.status = b, status
            def read(self): return self._b
            def __enter__(self): return self
            def __exit__(self, *a): return False

        payload = {"tokens": list(range(512)), "timings": {"predicted_n": 512, "predicted_ms": 64000.0}}
        def urlopen(req, timeout=None):
            url = req if isinstance(req, str) else req.full_url
            if url.endswith("/health"):
                return Resp(b"ok")
            if urlopen_exc:
                raise urlopen_exc
            return Resp(json.dumps(payload).encode())

        import r3_02_acquire as acq
        gs = mock.patch.object(C, "graceful_stop", side_effect=graceful_side_effect, return_value=graceful_ret)
        with tempfile.TemporaryDirectory() as d, mock.patch.object(E.subprocess, "Popen", FakeProc), mock.patch.object(E.urllib.request, "urlopen", urlopen), \
                gs as g, mock.patch.object(acq, "workload_prompt_for_request", return_value="p"):
            (Path(d) / "launches").mkdir()
            rec = E.run_launch(PLAN, CFGS[slot["config_id"]], Path("x.exe"), Path(d), slot, 2, "sha")
        return rec, g, stops

    def test_07_normal_launch_uses_the_amended_graceful_timeout(self):
        rec, g, stops = self._run()
        g.assert_called_once()
        self.assertEqual(g.call_args[0][1], E.GRACEFUL_STOP_TIMEOUT_S)
        self.assertGreater(E.GRACEFUL_STOP_TIMEOUT_S, 180)
        self.assertEqual((rec["interrupted"], rec["shutdown_timeout"], rec["attempt"]), (False, False, 2))
        import r3_02_acquire as acq
        self.assertEqual(E.validate_launch(rec, acq.T.output_token_digest(list(range(512)))), [])
        self.assertEqual(stops, {})

    def test_08_shutdown_timeout_is_distinguished_and_cannot_be_accepted(self):
        rec, g, stops = self._run(graceful_ret="terminate_fallback", stderr_line=False)
        self.assertTrue(rec["shutdown_timeout"]); self.assertFalse(rec["interrupted"])
        f = E.validate_launch(rec, C.FROZEN_TOKEN_DIGEST)
        self.assertTrue(any("shutdown" in x for x in f)); self.assertTrue(any("policy_timing" in x for x in f))
        rec2, _, _ = self._run(graceful_ret="terminate_fallback", stderr_line=True)      # even with a timing line present
        self.assertTrue(E.validate_launch(rec2, C.FROZEN_TOKEN_DIGEST))

    def test_09_keyboard_interrupt_during_shutdown_wait_is_explicit_and_cleans_up(self):
        rec, g, stops = self._run(graceful_side_effect=KeyboardInterrupt())
        self.assertTrue(rec["interrupted"]); self.assertFalse(rec["shutdown_timeout"])
        self.assertEqual(rec["shutdown_mode"], "operator_interrupt_terminated")
        self.assertIn("graceful shutdown wait", rec["error"])
        self.assertEqual(stops.get("terminate"), 1)                                     # no orphan llama-server
        self.assertIn("operator interrupt", E.validate_launch(rec, C.FROZEN_TOKEN_DIGEST))

    def test_10_keyboard_interrupt_during_inference_skips_the_graceful_wait(self):
        rec, g, stops = self._run(urlopen_exc=KeyboardInterrupt())
        g.assert_not_called()
        self.assertTrue(rec["interrupted"]); self.assertFalse(rec["request_completed"])
        self.assertEqual(rec["shutdown_mode"], "operator_interrupt_terminated")

    def test_11_main_writes_interrupted_state_and_never_counts_it(self):
        with tempfile.TemporaryDirectory() as d:
            out = campaign(d, 4)
            interrupted = {**accepted_rec(5), "interrupted": True, "shutdown_mode": "operator_interrupt_terminated", "error": "KeyboardInterrupt", "attempt": 1}
            self._drive(out, [interrupted])
            self.assertTrue((out / "records" / "005.try1.INTERRUPTED.json").exists())
            self.assertTrue((out / "CAMPAIGN_INTERRUPTED.json").exists())
            self.assertFalse((out / "records" / "005.json").exists())
            self.assertEqual(sorted(E.load_accepted(out, SCHED, None)), [1, 2, 3, 4])

    def test_12_resume_runs_try2_for_the_same_slot_and_accepts_only_validated(self):
        with tempfile.TemporaryDirectory() as d:
            out = campaign(d, 4, 5)
            good = accepted_rec(5); good["attempt"] = 2
            calls = self._drive(out, [good])
            self.assertEqual([(c["seq"], c["attempt"]) for c in calls[:1]], [(5, 2)])       # same slot, try2
            self.assertTrue(all(c["seq"] >= 5 for c in calls))                              # nothing re-run for 1-4
            self.assertEqual((calls[1]["seq"], calls[1]["attempt"]), (6, 1))                # next slot is 006 (the scripted stop), never a duplicate of 005
            self.assertTrue((out / "records" / "005.json").exists())
            self.assertTrue((out / "records" / "005.try1.ORPHAN_NONCOUNTED.json").exists())  # incident evidence kept
            self.assertEqual(sorted(E.load_accepted(out, SCHED, None)), [1, 2, 3, 4, 5])
            self.assertTrue((out / "CAMPAIGN_RESUMED_01.json").exists())

    def test_13_second_failure_of_the_slot_aborts_without_a_third_attempt(self):
        with tempfile.TemporaryDirectory() as d:
            out = campaign(d, 4, 5)
            bad = dict(accepted_rec(5), shutdown_mode="terminate_fallback", shutdown_timeout=True, policy_timing_records=[], attempt=2)
            calls = self._drive(out, [bad])
            self.assertEqual(len(calls), 1)
            self.assertTrue((out / "CAMPAIGN_ABORTED.json").exists())
            self.assertTrue((out / "records" / "005.try2.FAILED.json").exists())
            self.assertFalse((out / "records" / "005.json").exists())

    def _drive(self, out, recs):
        """Run main(--resume) with run_launch mocked to return the given records in order; returns the recorded call slots."""
        calls, it = [], iter(recs)
        def fake_run_launch(plan, cfg, runtime, out_dir, slot, attempt, runtime_sha):
            calls.append({"seq": slot["seq"], "attempt": attempt})
            try:
                r = next(it)
            except StopIteration:
                raise KeyboardInterrupt           # stop the campaign after the scripted launches
            r = dict(r); r["seq"] = slot["seq"]; r["attempt"] = attempt
            return r
        fz = {"runtime_sha256": E.RUNTIME_SHA, "driver_sha256_lf": "1" * 64, "duration_estimate": {"per_launch_s": 100}}
        fz["freeze_sha256"] = E.freeze_sha(fz)
        am = {"parent_freeze_sha256": fz["freeze_sha256"], "supersedes_driver_sha256_lf": "1" * 64, "driver_sha256_lf": E.sha256_lf(Path(E.__file__))}
        am["freeze_sha256"] = E.freeze_sha(am)
        (out.parent / "F.json").write_text(json.dumps(fz)); (out.parent / "A.json").write_text(json.dumps(am))
        (out / "CAMPAIGN_OPENED.json").write_text(json.dumps({"freeze_sha256": fz["freeze_sha256"], "runtime_sha256": E.RUNTIME_SHA}))
        exe = out.parent / "x.exe"; exe.write_bytes(b"x")
        argv = ["--runtime", str(exe), "--runtime-sha256", E.RUNTIME_SHA, "--freeze-sha256", fz["freeze_sha256"], "--amendment-sha256", am["freeze_sha256"],
                "--out-dir", str(out), "--attest-clean-environment", "--resume"]
        import r3_02_acquire as acq
        with mock.patch.object(E, "FREEZE_PATH", out.parent / "F.json"), mock.patch.object(E, "AMENDMENT_PATH", out.parent / "A.json"), \
                mock.patch.object(E, "run_launch", fake_run_launch), mock.patch.object(E, "sha256_file", lambda p: E.RUNTIME_SHA if Path(p).name == "x.exe" else "0"), \
                mock.patch.object(E.time, "sleep", lambda s: None), mock.patch.object(E.shutil, "disk_usage", lambda p: type("U", (), {"free": 10 ** 12})()), mock.patch.object(acq, "default_free_ram_check", lambda *a, **k: None):
            import io, contextlib
            buf = io.StringIO()
            with contextlib.redirect_stdout(buf):
                try:
                    E.main(argv)
                except SystemExit as e:
                    buf.write(str(e))
            self.last_output = buf.getvalue()
        return calls


if __name__ == "__main__":
    unittest.main()
