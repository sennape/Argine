"""Offline deterministic tests of the Gate 2 shutdown-helper path (fail-closed, recorded fallback, flush ordering). No model, no network, no sleeping."""
import copy, json, subprocess, sys, tempfile, unittest
from pathlib import Path
from unittest import mock

HERE = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(HERE / "tests")); sys.path.insert(0, str(HERE.parents[1] / "tools"))
import test_r3_04_tooling as T  # noqa: E402
import r3_04_mechanism as M  # noqa: E402
import r3_04_shutdown as S  # noqa: E402
V = T.V


def sd(method="own_console_helper", used="own_console_helper", rc=0, lat=0.2, mode="graceful"):
    return {"mode": mode, "method_used": used, "latency_s": lat, "helper_returncode": rc}


class GateHelper(unittest.TestCase):
    def recs(self, requested="own_console_helper", **kw):
        r, s, o = T.Mechanism._recs(self)
        for x in r.values():
            x["shutdown_method_requested"] = requested; x["shutdown"] = sd(**kw)
        return r, s, o

    def test_01_helper_success_validated(self):
        r, s, o = self.recs(); g = M.evaluate_gates(r, s, o)
        self.assertEqual(g["status"], "MECHANISM_PASS"); self.assertEqual(g["shutdown_validation"]["status"], "HELPER_VALIDATED_ON_REAL_MODEL")

    def test_02_silent_fallback_to_legacy_fails_closed(self):
        r, s, o = self.recs(used="legacy_after_helper_failure", rc=2); g = M.evaluate_gates(r, s, o)
        self.assertEqual(g["status"], "MECHANISM_FAIL"); self.assertEqual(g["shutdown_validation"]["status"], "HELPER_NOT_VALIDATED")

    def test_03_slow_shutdown_not_validated_but_mechanism_unaffected(self):
        r, s, o = self.recs(lat=300.0); g = M.evaluate_gates(r, s, o)
        self.assertEqual(g["status"], "MECHANISM_PASS"); self.assertEqual(g["shutdown_validation"]["status"], "HELPER_NOT_VALIDATED")

    def test_04_terminate_fallback_fails(self):
        r, s, o = self.recs(mode="terminate_fallback")
        for x in r.values(): x["shutdown_mode"] = "terminate_fallback"
        self.assertEqual(M.evaluate_gates(r, s, o)["status"], "MECHANISM_FAIL")

    def test_05_legacy_request_has_no_helper_requirement(self):
        r, s, o = self.recs(requested="legacy", used="legacy", rc=None); self.assertEqual(M.evaluate_gates(r, s, o)["status"], "MECHANISM_PASS")
        self.assertEqual(M.evaluate_gates(r, s, o)["shutdown_validation"]["status"], "HELPER_NOT_VALIDATED")

    def test_06_driver_aborts_on_first_helper_fallback_and_keeps_no_accepted_record(self):
        fz = json.loads((HERE / "FREEZE.json").read_text())["freeze_sha256"]
        t = Path(tempfile.mkdtemp()); out = t / "m"; rt = t / "rt.exe"; rt.write_bytes(b"x"); calls = []
        def launcher(plan, cfg, runtime, trace, outdir, tag, timing_on, method):
            calls.append(method); T.write_trace(trace, cfg["policy_id"])
            return {"tag": tag, "output_token_digest": V.TOKEN_DIGEST, "n_tokens": 512, "policy_timing_records": [], "duplicate_event_refusals": 0, "shutdown_mode": "graceful",
                    "shutdown": sd(used="legacy_after_helper_failure", rc=3), "trace_bytes": 1, "trace_sha256": "ab" * 32, "stderr_path": "x"}
        with mock.patch.object(M.time, "sleep"), mock.patch.object(M.C, "sha256_file", return_value=T.RT), mock.patch("r3_02_acquire.default_free_ram_check"), \
             mock.patch.object(M.shutil, "disk_usage", return_value=mock.Mock(free=10 ** 12)), mock.patch("builtins.print"):
            rc = M.main(["--runtime", str(rt), "--runtime-sha256", T.RT, "--protocol-freeze-sha256", fz, "--out-dir", str(out), "--attest-clean-environment",
                         "--shutdown-method", "own_console_helper"], launcher=launcher, analyser=lambda *a: self.fail("analysis must not run after a failed shutdown path"))
        self.assertEqual(rc, 1); self.assertEqual(calls, ["own_console_helper"])
        self.assertEqual(list((out / "records").glob("M[0-9][0-9].json")), []); self.assertTrue((out / "records" / "M01.FAILED.json").exists())

    def test_07_stop_server_helper_path_no_kill_before_graceful_and_records(self):
        p = T.FakeProc(exits_after=0.0)
        with mock.patch.object(S.os, "name", "nt"), mock.patch.object(S.signal, "signal"):
            r = S.stop_server(p, 600, "own_console_helper", runner=lambda *a, **k: mock.Mock(returncode=0), now=iter([0.0, 0.1]).__next__)
        self.assertEqual((r["mode"], r["method_used"], r["helper_returncode"]), ("graceful", "own_console_helper", 0))
        self.assertEqual(p.sent, []); self.assertFalse(p.terminated or p.killed)

    def test_08_stop_only_after_response_and_flush_by_graceful_exit(self):
        src = (HERE.parents[1] / "tools" / "r3_04_mechanism.py").read_text()
        body = src[src.index("def run_traced_launch"):src.index("def replay_analysis")]
        self.assertLess(body.index("resp.read()"), body.index("S.stop_server")); self.assertLess(body.index("S.stop_server"), body.index("C.timing_records(text)"))
        self.assertLess(body.index("S.stop_server"), body.index("C.sha256_file(trace)")); self.assertNotIn("terminate()", body); self.assertNotIn("kill()", body)


if __name__ == "__main__":
    unittest.main()
