"""Offline deterministic tests of the R3-04 tooling (drivers, shutdown, mechanism counters). No model, no timing, no network, no sleeping."""
import copy, hashlib, io, json, random, subprocess, sys, tempfile, unittest
from pathlib import Path
from unittest import mock

HERE = Path(__file__).resolve().parents[1]
REPO = HERE.parents[1]
sys.path.insert(0, str(REPO / "tools"))
import r3_04_acquire as A  # noqa: E402
import r3_04_mechanism as M  # noqa: E402
import r3_04_shutdown as S  # noqa: E402
import r3_03b_e2e as E  # noqa: E402
V = A.V
RT = V.RUNTIME_SHA256


def timing(calls=V.EVENTS, total=10_000_000_000, vict=7966):
    h = [0] * 40; h[18] = calls
    return {"schema": "r3-03b-v1", "calls": calls, "ns_total": total, "ns_prediction_update": total // 2, "ns_victim_selection": total // 100, "victim_calls": vict,
            "ns_event_lock_wait": 1000, "ns_event_locked_total": total * 3, "hist_log2": h}


def fake_launch(slot, attempt, wall=100.0, pol=1.0, **over):
    cost = {"A": 0.0, "S": 18.0, "P": 17.0}[slot["arm"]]
    rec = {"artifact": "R3-04-LAUNCH-RECORD", **slot, "attempt": attempt, "runtime_sha256": RT, "output_token_digest": V.TOKEN_DIGEST, "n_tokens": 512, "tokens": [1] * 512,
           "server_timings": {"predicted_ms": (wall + cost) * 512, "predicted_n": 512}, "policy_timing_records": [timing(total=int((pol + (19 if slot["arm"] != "A" else 0)) * 512 * 1e6))],
           "duplicate_event_refusals": 0, "shutdown_mode": "graceful", "error": None, "interrupted": False, "request_completed": True, "shutdown_timeout": False}
    rec.update(over)
    return rec


class FakeProc:
    def __init__(self, exits_after=0.0, pid=4242):
        self.pid, self.sent, self.terminated, self.killed, self._rc = pid, [], False, False, None
        self.exits_after = exits_after
    def poll(self): return self._rc
    def send_signal(self, s): self.sent.append(s)
    def wait(self, timeout=None):
        if self.exits_after is None or self.exits_after > (timeout or 0): raise subprocess.TimeoutExpired("x", timeout)
        self._rc = 0; return 0
    def terminate(self): self.terminated = True; self.exits_after = 0.0
    def kill(self): self.killed = True


class Campaign(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(); self.out = Path(self.tmp.name) / "run"; self.addCleanup(self.tmp.cleanup)
        self.sched = V.timing_schedule()

    def put(self, n):
        (self.out / "records").mkdir(parents=True, exist_ok=True); (self.out / "launches").mkdir(exist_ok=True)
        for s in self.sched[:n]: E.write_atomic(self.out / "records" / f"{s['seq']:03d}.json", {**fake_launch(s, 1), "admitted": True, "failures": []})

    def test_01_accepted_prefix_and_runtime_enforced(self):
        self.put(30); acc = E.load_accepted(self.out, self.sched, RT); self.assertEqual(sorted(acc), list(range(1, 31)))
        with self.assertRaises(ValueError): E.load_accepted(self.out, self.sched, "0" * 64)
        (self.out / "records" / "010.json").unlink()
        with self.assertRaises(ValueError): E.load_accepted(self.out, self.sched, RT)             # gap in the accepted prefix

    def test_02_wrong_runtime_record_fails_validation(self):
        s = self.sched[10]; r = fake_launch(s, 1, runtime_sha256="3f094f5b5f4963f488b63722f365068f09e2aa3b12e2096969da99f471bbd644")
        self.assertIn("runtime sha256 != frozen runtime", A.validate(r, s))
        self.assertEqual(A.validate(fake_launch(s, 1), s), [])

    def test_03_malformed_and_missing_records_fail(self):
        s = self.sched[10]
        for k, v in (("output_token_digest", "0" * 64), ("n_tokens", 511), ("shutdown_mode", "terminate_fallback"), ("policy_timing_records", []), ("server_timings", {}), ("duplicate_event_refusals", 2)):
            self.assertTrue(A.validate(fake_launch(s, 1, **{k: v}), s), k)
        r = fake_launch(s, 1); r["arm"] = "A" if s["arm"] != "A" else "S"
        self.assertTrue(A.validate(r, s))
        self.put(5); p = self.out / "records" / "003.json"; d = json.loads(p.read_text()); d["arm"] = "S" if d["arm"] != "S" else "A"; p.write_text(json.dumps(d))
        with self.assertRaises(ValueError): E.load_accepted(self.out, self.sched, RT)

    def test_04_orphan_and_failed_markers_never_accepted(self):
        self.put(6); lg = self.out / "launches" / "007-1.5GiB-c02p1-A-try1.stderr.log"
        lg.write_text("stop processing: n_tokens = 698\n"); made = E.classify_orphans(self.out, self.sched)
        self.assertEqual(made, ["007.try1.ORPHAN_NONCOUNTED.json"]); self.assertEqual(sorted(E.load_accepted(self.out, self.sched, RT)), list(range(1, 7)))
        self.assertEqual(E.prior_attempts(self.out, 7), 1)

    def test_05_no_analysis_before_complete_schedule(self):
        self.put(180)
        with self.assertRaises(ValueError) as c: A.analyze(self.out)
        self.assertIn("no interim analysis", str(c.exception))

    def test_06_full_campaign_analysis_and_classification(self):
        (self.out / "records").mkdir(parents=True); (self.out / "launches").mkdir()
        rnd = random.Random(7)
        for s in self.sched:
            r = fake_launch(s, 1, wall=100.0 + rnd.gauss(0, 0.5)); E.write_atomic(self.out / "records" / f"{s['seq']:03d}.json", {**r, "admitted": True, "failures": []})
        res = A.analyze(self.out); p = res["budgets"]["1.5"]
        self.assertEqual(p["PRIMARY_CLASSIFICATION"]["net_classification"], "NET_LOSS")
        self.assertAlmostEqual(p["wall_ms_per_token"]["DELTA_COST"]["mean"], 18.0, delta=0.5); self.assertAlmostEqual(p["wall_ms_per_token"]["DELTA_RETENTION"]["mean"], -1.0, delta=0.5)
        self.assertAlmostEqual(p["wall_ms_per_token"]["DELTA_NET"]["mean"], 17.0, delta=0.5); self.assertGreater(p["tokens_per_second_reversed_sign"]["DELTA_NET"]["mean"] * -1, 0)
        self.assertTrue(res["budgets"]["4"]["classification"].startswith("NONE")); self.assertEqual(p["n_blocks"], 18)
        self.assertEqual(res["budgets"]["8"]["n_blocks"], 6)

    def test_07_main_refuses_without_gates(self):
        base = ["--runtime", "x", "--runtime-sha256", RT, "--protocol-freeze-sha256", json.loads((HERE / "FREEZE.json").read_text())["freeze_sha256"], "--opening-freeze-sha256", "0" * 64, "--out-dir", str(self.out)]
        self.assertEqual(A.main(["--out-dir", str(self.out)]), 1)                                   # missing identities
        with self.assertRaises(SystemExit) as c: A.main(base)                                      # opening freeze absent / wrong
        self.assertIn("REFUSED", str(c.exception))
        with self.assertRaises(SystemExit): A.main(base[:5] + ["f" * 64] + base[6:])               # wrong protocol sha
        with mock.patch.object(A, "OPENING_FREEZE", Path(self.tmp.name) / "of.json"):
            of = {"artifact": "x", "protocol_freeze_sha256": base[5], "driver_sha256_lf": V.sha_lf(Path(A.__file__)), "runtime_sha256": RT}; of["freeze_sha256"] = V.freeze_sha(of)
            (Path(self.tmp.name) / "of.json").write_text(json.dumps(of))
            self.assertEqual(A.main(base[:7] + [of["freeze_sha256"]] + base[8:]), 1)                # no clean-environment attestation
            of2 = dict(of, driver_sha256_lf="0" * 64); of2["freeze_sha256"] = V.freeze_sha(of2); (Path(self.tmp.name) / "of.json").write_text(json.dumps(of2))
            with self.assertRaises(SystemExit): A.main(base[:7] + [of2["freeze_sha256"]] + base[8:])  # driver hash differs from the frozen one
        self.assertEqual(A.main(["--runtime", "x", "--runtime-sha256", "0" * 64, "--protocol-freeze-sha256", base[5], "--opening-freeze-sha256", "0" * 64, "--out-dir", str(self.out), "--dry-run"]), 1)

    def test_08_dry_run_and_resume_follow_frozen_schedule(self):
        self.put(7)
        with mock.patch("builtins.print") as pr:
            self.assertEqual(A.main(["--runtime", "x", "--runtime-sha256", RT, "--protocol-freeze-sha256", json.loads((HERE / "FREEZE.json").read_text())["freeze_sha256"], "--opening-freeze-sha256", "0" * 64,
                                     "--out-dir", str(self.out), "--dry-run"]), 0)
        d = json.loads(pr.call_args[0][0]); self.assertEqual((d["accepted_records_found"], d["next_slot"]["seq"], d["schedule_sha256"]), (7, 8, V.schedule_sha(self.sched)))
        self.assertEqual((self.sched[7]["block"], self.sched[7]["position"], self.sched[7]["arm"]), (2, 1, V.block_order(2)[0]))

    def test_09_no_optional_stopping_option(self):
        import argparse
        with mock.patch("sys.stderr", io.StringIO()), self.assertRaises(SystemExit): A.main(["--out-dir", "x", "--n-blocks", "3"])
        src = Path(A.__file__).read_text(); self.assertNotIn("n_blocks\", type", src); self.assertNotIn("--max-blocks", src)

    def test_10_no_shell_true_anywhere(self):
        for n in ("r3_04_acquire.py", "r3_04_mechanism.py", "r3_04_shutdown.py", "r3_04_shutdown_probe.py"):
            self.assertNotIn("shell=True", (REPO / "tools" / n).read_text())


class Shutdown(unittest.TestCase):
    def test_graceful_request_then_wait(self):
        p = FakeProc(0.0); r = S.stop_server(p, 5, "legacy", now=iter([0.0, 2.5]).__next__)
        self.assertEqual((r["mode"], r["latency_s"]), ("graceful", 2.5)); self.assertEqual(len(p.sent), 1); self.assertFalse(p.terminated)

    def test_already_exited_sends_nothing(self):
        p = FakeProc(); p._rc = 0; r = S.stop_server(p, 5, "legacy"); self.assertEqual(r["mode"], "already_exited"); self.assertEqual(p.sent, [])

    def test_timeout_falls_back_to_terminate_and_is_reported(self):
        p = FakeProc(exits_after=None); r = S.stop_server(p, 1, "legacy", now=iter([0.0, 9.0]).__next__)
        self.assertEqual(r["mode"], "terminate_fallback"); self.assertTrue(p.terminated)
        rec = fake_launch(V.timing_schedule()[10], 1, shutdown_mode="terminate_fallback", shutdown_timeout=True)
        self.assertTrue(any("graceful" in f or "terminate" in f for f in A.validate(rec, V.timing_schedule()[10])))

    def test_helper_method_and_failure_fallback(self):
        import os
        calls = []
        def runner(cmd, timeout): calls.append(cmd); return mock.Mock(returncode=0)
        with mock.patch.object(S.os, "name", "nt"), mock.patch.object(S.signal, "CTRL_C_EVENT", 0, create=True), mock.patch.object(S.signal, "signal", lambda *a: None):
            p = FakeProc(0.0); r = S.stop_server(p, 5, "own_console_helper", runner=runner, now=iter([0.0, 1.0]).__next__)
            self.assertEqual((r["mode"], r["method_used"], r["helper_returncode"]), ("graceful", "own_console_helper", 0)); self.assertEqual(p.sent, []); self.assertEqual(calls[0][-1], "4242")
            p2 = FakeProc(0.0); r2 = S.stop_server(p2, 5, "own_console_helper", runner=lambda c, timeout: mock.Mock(returncode=3), now=iter([0.0, 1.0]).__next__)
            self.assertEqual(r2["method_used"], "legacy_after_helper_failure"); self.assertEqual(len(p2.sent), 1)
        self.assertEqual(S.popen_creationflags("legacy"), 0)
        with self.assertRaises(ValueError): S.popen_creationflags("bogus")

    def test_graceful_timeout_unchanged_and_helper_source_compiles(self):
        self.assertEqual(S.GRACEFUL_TIMEOUT_S, 600); compile(S.HELPER_SOURCE, "helper", "exec")

    def test_stop_is_only_called_after_response_in_driver(self):
        src = Path(A.__file__).read_text(); i, j = src.index("payload = json.loads(resp.read()"), src.index("S.stop_server(proc")
        self.assertLess(i, j)


def write_trace(path, policy="b_selected", n_passes=514, miss_per_event=3, protected=0):
    with path.open("w") as f:
        step = 0
        for p in range(n_passes):
            for l in range(48):
                f.write(json.dumps({"step": step, "layer": l, "semantic_event": {"schema": "r3-03a-v1", "pass": p, "layer": l, "node_kind": 0, "first_node": 1},
                                    "hit_experts": [1, 2], "miss_experts": list(range(miss_per_event)), "evicted": [[l, 9]] if p % 2 else [], "bytes_of_layer": 1000,
                                    "protected_before": list(range(protected)), "policy_resident_bytes_after": 10 * (step % 7)}) + "\n")
                f.write(json.dumps({"step": step, "complete": 1, "cache_resident_bytes_post_service": 5}) + "\n"); step += 1


class Mechanism(unittest.TestCase):
    def setUp(self): self.tmp = tempfile.TemporaryDirectory(); self.t = Path(self.tmp.name); self.addCleanup(self.tmp.cleanup)

    def test_01_counts_exact_and_phases(self):
        tr = self.t / "a.jsonl"; write_trace(tr, protected=2); c = M.mechanism_counts(tr)
        self.assertEqual(c["all_passes"]["events"], V.EVENTS); self.assertEqual(c["decode_passes"]["events"], 512 * 48)
        self.assertEqual(c["decode_passes"]["miss_bytes"], 512 * 48 * 3 * 1000); self.assertAlmostEqual(c["decode_miss_bytes_per_token"], 48 * 3 * 1000)
        self.assertEqual(c["all_passes"]["hits"], V.EVENTS * 2); self.assertEqual(c["decode_passes"]["evictions"], 256 * 48); self.assertEqual(c["decode_passes"]["protected_entries"], 512 * 48 * 2)
        self.assertEqual(c["resident_peak_bytes"], 60)

    def test_02_protected_effect_distinguishes_arms(self):
        a, p = self.t / "a.jsonl", self.t / "p.jsonl"; write_trace(a, miss_per_event=3, protected=0); write_trace(p, "predicted_protect", miss_per_event=2, protected=1)
        ca, cp = M.mechanism_counts(a), M.mechanism_counts(p)
        self.assertLess(cp["decode_miss_bytes_per_token"], ca["decode_miss_bytes_per_token"]); self.assertEqual(ca["decode_passes"]["protected_events"], 0); self.assertGreater(cp["decode_passes"]["protected_events"], 0)

    def _recs(self):
        sch = V.mechanism_schedule(); recs = {}
        for m in sch:
            recs[m["mseq"]] = {"output_token_digest": V.TOKEN_DIGEST, "policy_id": m["policy_id"], "budget": m["budget"], "runtime_sha256": RT,
                               "replay": {"ok": True, "semantic_events": V.EVENTS, "passes": 514, "budget_violations": 0, "total_evictions": 100}, "duplicate_event_refusals": 0,
                               "shutdown_mode": "graceful", "policy_timing_records": [timing()] if m["policy_timing"] == "ON" else []}
        return recs, {b: {"ok": True} for b in V.BUDGET_ORDER}, {"ok": True}

    def test_03_gates_pass_and_each_failure_blocks(self):
        r, s, o = self._recs(); self.assertEqual(M.evaluate_gates(r, s, o)["status"], "MECHANISM_PASS")
        def bad(mut):
            r2, s2, o2 = copy.deepcopy(self._recs()); mut(r2, s2, o2); return M.evaluate_gates(r2, s2, o2)["status"]
        self.assertEqual(bad(lambda r, s, o: r[3].update(output_token_digest="0" * 64)), "MECHANISM_FAIL")
        self.assertEqual(bad(lambda r, s, o: r[2]["replay"].update(ok=False)), "MECHANISM_FAIL")
        self.assertEqual(bad(lambda r, s, o: r[2]["replay"].update(semantic_events=100)), "MECHANISM_FAIL")
        self.assertEqual(bad(lambda r, s, o: s.update({"4": {"ok": False}})), "MECHANISM_FAIL")
        self.assertEqual(bad(lambda r, s, o: o.update(ok=False)), "MECHANISM_FAIL")
        self.assertEqual(bad(lambda r, s, o: r[4].update(policy_timing_records=[timing()])), "MECHANISM_FAIL")      # OFF launch must carry no timing
        self.assertEqual(bad(lambda r, s, o: r.pop(5)), "MECHANISM_FAIL")
        self.assertEqual(bad(lambda r, s, o: r[1].update(runtime_sha256="0" * 64)), "MECHANISM_FAIL")
        self.assertEqual(bad(lambda r, s, o: r[7]["replay"].update(budget_violations=1)), "MECHANISM_FAIL")

    def test_04_gate_ignores_size_of_retention_improvement(self):
        r, s, o = self._recs(); r[3]["counts"] = {"decode_miss_bytes_per_token": 0}; r[1]["counts"] = {"decode_miss_bytes_per_token": 10 ** 9}
        self.assertEqual(M.evaluate_gates(r, s, o)["status"], "MECHANISM_PASS")

    def test_05_driver_dry_run_and_refusals(self):
        fz = json.loads((HERE / "FREEZE.json").read_text())["freeze_sha256"]
        with mock.patch("builtins.print") as pr:
            self.assertEqual(M.main(["--runtime", "x", "--runtime-sha256", RT, "--protocol-freeze-sha256", fz, "--out-dir", str(self.t / "o"), "--dry-run"]), 0)
        self.assertEqual(json.loads(pr.call_args[0][0])["launches"], 10)
        self.assertEqual(M.main(["--runtime", "x", "--runtime-sha256", "0" * 64, "--protocol-freeze-sha256", fz, "--out-dir", str(self.t / "o")]), 1)
        self.assertEqual(M.main(["--out-dir", str(self.t / "o")]), 1)

    def test_06_driver_flow_with_injected_launcher_deletes_traces_after_comparison(self):
        fz = json.loads((HERE / "FREEZE.json").read_text())["freeze_sha256"]; out = self.t / "mech"; runtime = self.t / "rt.exe"; runtime.write_bytes(b"x")
        launched = []
        def launcher(plan, cfg, rt, trace, outdir, tag, timing_on, method):
            write_trace(trace, cfg["policy_id"]); launched.append(tag)
            return {"tag": tag, "output_token_digest": V.TOKEN_DIGEST, "n_tokens": 512, "policy_timing_records": [timing()] if timing_on else [], "duplicate_event_refusals": 0,
                    "shutdown_mode": "graceful", "shutdown": {"mode": "graceful"}, "trace_bytes": trace.stat().st_size, "trace_sha256": "ab" * 32, "stderr_path": "x"}
        def analyser(trace, sham): return {"ok": True, "semantic_events": V.EVENTS, "passes": 514, "budget_violations": 0, "total_evictions": 100}
        with mock.patch.object(M.time, "sleep"), mock.patch.object(M.C, "sha256_file", return_value=RT), mock.patch("r3_02_acquire.default_free_ram_check"), \
             mock.patch.object(M.shutil, "disk_usage", return_value=mock.Mock(free=10 ** 12)), \
             mock.patch.object(M.C, "compare_sham_vs_b", return_value={"ok": True}), mock.patch.object(M.C, "compare_canonical", return_value={"ok": True}), mock.patch("builtins.print"):
            rc = M.main(["--runtime", str(runtime), "--runtime-sha256", RT, "--protocol-freeze-sha256", fz, "--out-dir", str(out), "--attest-clean-environment"], launcher=launcher, analyser=analyser)
        self.assertEqual(rc, 0); self.assertEqual(len(launched), 10)
        self.assertEqual(json.loads((out / "MECHANISM_GATES.json").read_text())["status"], "MECHANISM_PASS")
        self.assertEqual(sorted(p.name for p in (out / "traces").glob("*.jsonl")), [])                  # every trace reduced and deleted
        self.assertTrue(all(json.loads((out / "records" / f"M{i:02d}.json").read_text())["counts"] for i in range(1, 11)))


if __name__ == "__main__":
    unittest.main()
