"""G1 driver: mandatory prerequisites fail closed BEFORE any model/workload launch (host incident of the first G1 attempt).

In the first attempt argine-ets-test.exe died with STATUS_HEAP_CORRUPTION (exit 3221226356 = 0xC0000374, no RESULT line)
and the driver still launched the 30B workload. These tests prove that a failed model-free run (non-zero exit, missing,
malformed, duplicated or wrong RESULT line) or a failed startup smoke stops the run with EXIT_PREREQUISITES and that
neither the workload launcher, the smoke launches nor the model hashing is ever invoked. No runtime is started.
"""
from __future__ import annotations

import argparse, importlib.util, json, sys, tempfile, unittest
from pathlib import Path
from unittest import mock

HERE = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location("host_conformance", HERE / "host_conformance.py")
H = importlib.util.module_from_spec(spec); sys.modules["host_conformance"] = H; spec.loader.exec_module(H)  # type: ignore

GOOD_STDOUT = "R5 ETS model-free conformance (Windows OVERLAPPED backend)\n[1] ...\n\nRESULT: 143 passed, 0 failed, 3 skipped\n"
GOOD_SMOKE = {"version_returncode": 0, "version_output": "", "refusals": [{"name": n, "pass": True} for n in ("a", "b", "c")]}


def mf(rc: int, stdout: str, stderr: str = ""):
    return H.parse_model_free(rc, stdout, stderr)


class ParseModelFree(unittest.TestCase):
    def test_host_incident_heap_corruption_no_output(self):
        m = mf(3221226356, "")
        self.assertEqual(m["returncode_hex"], "0xC0000374")
        f = H.model_free_failures(m)
        self.assertTrue(any("0xC0000374" in x for x in f), f)
        self.assertTrue(any("RESULT" in x for x in f), f)

    def test_good_run_has_no_failure(self):
        self.assertEqual(H.model_free_failures(mf(0, GOOD_STDOUT)), [])

    def test_each_defect_is_a_failure(self):
        cases = {
            "nonzero exit with a valid RESULT line": mf(1, GOOD_STDOUT),
            "missing RESULT line": mf(0, "R5 ETS model-free conformance\n[1] ...\n"),
            "malformed RESULT line": mf(0, "RESULT: 143 passed, 0 failed\n"),
            "RESULT not at line start": mf(0, "x RESULT: 143 passed, 0 failed, 3 skipped\n"),
            "two RESULT lines": mf(0, GOOD_STDOUT + "RESULT: 143 passed, 0 failed, 3 skipped\n"),
            "wrong passed count": mf(0, "RESULT: 142 passed, 0 failed, 3 skipped\n"),
            "failed checks": mf(0, "RESULT: 142 passed, 1 failed, 3 skipped\n"),
            "wrong skip count": mf(0, "RESULT: 143 passed, 0 failed, 4 skipped\n"),
            "unexpected skip message": mf(0, GOOD_STDOUT + "UNEXPECTED SKIP COUNT: 4 (expected 3)\n"),
        }
        for name, m in cases.items():
            self.assertTrue(H.model_free_failures(m), name)


class SmokeFailures(unittest.TestCase):
    def test_good(self):
        self.assertEqual(H.smoke_failures(GOOD_SMOKE), [])

    def test_failures(self):
        bad = [dict(GOOD_SMOKE, version_returncode=1),
               dict(GOOD_SMOKE, refusals=GOOD_SMOKE["refusals"][:2]),
               dict(GOOD_SMOKE, refusals=[{"name": "a", "pass": True}, {"name": "b", "pass": False}, {"name": "c", "pass": True}]),
               {}]
        for b in bad:
            self.assertTrue(H.smoke_failures(b), b)


class NoLaunchOnFailedPrerequisite(unittest.TestCase):
    """cmd_run with the identity step stubbed: the gates decide whether anything touches the model."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(); self.out = Path(self.tmp.name) / "g1"
        self.args = argparse.Namespace(out=str(self.out), model="M.gguf", store="M.psxs", v5_runtime="v5.exe", v5_search_root=None)
        ident = {"r5_runtime_sha256": "r", "ets_test_sha256": "t"}
        self.patches = [
            mock.patch.object(H, "check_identities", return_value=(ident, Path("rt.exe"), Path("ets.exe"), Path("v5.exe"))),
            mock.patch.object(H, "run_launch", side_effect=AssertionError("a workload launch was started")),
            mock.patch.object(H, "refusal", side_effect=AssertionError("a smoke launch was started")),
        ]
        for p in self.patches:
            p.start()

    def tearDown(self):
        for p in self.patches:
            p.stop()
        self.tmp.cleanup()

    def _run(self, model_free, smoke=None):
        launch = H.run_launch
        with mock.patch.object(H, "run_model_free", return_value=model_free), \
             mock.patch.object(H, "run_smoke", return_value=smoke if smoke is not None else GOOD_SMOKE) as sm, \
             mock.patch.object(H, "sha256_file", side_effect=AssertionError("the model was hashed")) as sha:
            rc = H.cmd_run(self.args)
        return rc, sm, sha, launch

    def test_heap_corruption_exit_stops_before_any_launch(self):
        rc, sm, sha, launch = self._run(mf(3221226356, ""))
        self.assertEqual(rc, H.EXIT_PREREQUISITES)
        sm.assert_not_called(); sha.assert_not_called(); launch.assert_not_called()
        rec = json.loads((self.out / "PREREQUISITES.json").read_text(encoding="utf-8"))
        self.assertEqual((rec["pass"], rec["stage"], rec["model_launches"]), (False, "windows_model_free", 0))
        self.assertTrue((self.out / "WINDOWS_MODEL_FREE.json").exists())          # diagnostics preserved
        self.assertFalse((self.out / "DEV_LOG.jsonl").exists())

    def test_missing_or_malformed_result_stops_before_any_launch(self):
        for out in ("", "RESULT: 143 passed\n", GOOD_STDOUT + GOOD_STDOUT, "RESULT: 143 passed, 1 failed, 3 skipped\n"):
            rc, sm, sha, launch = self._run(mf(0, out))
            self.assertEqual(rc, H.EXIT_PREREQUISITES, out)
            sm.assert_not_called(); sha.assert_not_called(); launch.assert_not_called()

    def test_failed_smoke_or_refusal_stops_before_workload_launches(self):
        for smoke in (dict(GOOD_SMOKE, version_returncode=5),
                      dict(GOOD_SMOKE, refusals=[{"name": "a", "pass": False}, {"name": "b", "pass": True}, {"name": "c", "pass": True}])):
            launch = H.run_launch
            with mock.patch.object(H, "run_model_free", return_value=mf(0, GOOD_STDOUT)), \
                 mock.patch.object(H, "run_smoke", return_value=smoke), \
                 mock.patch.object(H, "sha256_file", return_value="h"), \
                 mock.patch.object(H, "dev_prompts", return_value=[]):
                rc = H.cmd_run(self.args)
            self.assertEqual(rc, H.EXIT_PREREQUISITES)
            launch.assert_not_called()
            rec = json.loads((self.out / "PREREQUISITES.json").read_text(encoding="utf-8"))
            self.assertEqual((rec["pass"], rec["stage"]), (False, "startup_smoke"))

    def test_all_prerequisites_pass_then_launches_run(self):
        calls = []
        def fake_launch(slot, rt, model, store, out, prompt):
            calls.append(slot["seq"]); return dict(slot, error=None)
        with mock.patch.object(H, "run_model_free", return_value=mf(0, GOOD_STDOUT)), \
             mock.patch.object(H, "run_smoke", return_value=GOOD_SMOKE), \
             mock.patch.object(H, "sha256_file", return_value="h"), \
             mock.patch.object(H, "run_launch", side_effect=fake_launch):
            H.cmd_run(self.args)
        self.assertEqual(calls, [s["seq"] for s in H.plan()])
        self.assertTrue(json.loads((self.out / "PREREQUISITES.json").read_text(encoding="utf-8"))["pass"])


class IdentityAndOutputDirectory(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(); self.out = Path(self.tmp.name) / "g1"; self.out.mkdir()
        self.args = argparse.Namespace(out=str(self.out), model="M.gguf", store="M.psxs", v5_runtime=str(Path(self.tmp.name) / "v5.exe"), v5_search_root=None)
        (Path(self.tmp.name) / "v5.exe").write_bytes(b"x")

    def tearDown(self):
        self.tmp.cleanup()

    def _ids(self, sha_by_name):
        def fake_sha(p):
            return sha_by_name.get(Path(p).name, "other")
        with mock.patch.object(H, "sha256_file", side_effect=fake_sha), \
             mock.patch.object(H.Path, "is_file", return_value=True), \
             mock.patch.object(H, "v5_identity", return_value={"accepted": True, "kind": "reconstructed", "raw_sha256": "v"}):
            return H.check_identities(self.args, self.out)

    def test_superseded_runtime_is_refused(self):
        self.assertIsNone(self._ids(dict(H.R5_SUPERSEDED)))

    def test_authorized_runtime_is_accepted(self):
        self.assertIsNotNone(self._ids(dict(H.R5_AUTHORIZED)))

    def test_one_binary_changed_is_refused(self):
        self.assertIsNone(self._ids({"llama-server.exe": H.R5_AUTHORIZED["llama-server.exe"], "argine-ets-test.exe": "x" * 64}))

    def test_interrupted_attempt_directory_is_refused(self):
        """The partial launches of the first attempt (other runtime identity) can never be resumed into the campaign."""
        (self.out / "IDENTITY.json").write_text(json.dumps({"r5_runtime_sha256": H.R5_SUPERSEDED["llama-server.exe"],
                                                            "ets_test_sha256": H.R5_SUPERSEDED["argine-ets-test.exe"]}), encoding="utf-8")
        (self.out / "DEV_LOG.jsonl").write_text('{"seq": 1}\n', encoding="utf-8")
        self.assertIsNone(self._ids(dict(H.R5_AUTHORIZED)))

    def test_unidentified_launch_log_is_refused(self):
        (self.out / "DEV_LOG.jsonl").write_text('{"seq": 1}\n', encoding="utf-8")
        self.assertIsNone(self._ids(dict(H.R5_AUTHORIZED)))


class ModelFreeSubcommand(unittest.TestCase):
    """`host_conformance.py model-free`: the minimal host validation runs ONLY argine-ets-test.exe, with the same gate."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(); self.out = Path(self.tmp.name) / "mf"

    def tearDown(self):
        self.tmp.cleanup()

    def _main(self, model_free):
        paths = {"llama-server.exe": Path("rt.exe"), "argine-ets-test.exe": Path("ets.exe")}
        with mock.patch.object(H, "authorized_binaries", return_value=paths), \
             mock.patch.object(H, "run_model_free", return_value=model_free) as rmf, \
             mock.patch.object(H, "run_launch", side_effect=AssertionError("launch")), \
             mock.patch.object(H, "refusal", side_effect=AssertionError("launch")):
            rc = H.main(["model-free", "--out", str(self.out)])
        rmf.assert_called_once_with(Path("ets.exe"), self.out / "ets-test-scratch")
        return rc

    def test_heap_corruption_fails(self):
        self.assertEqual(self._main(mf(3221226356, "")), H.EXIT_PREREQUISITES)
        self.assertEqual(json.loads((self.out / "WINDOWS_MODEL_FREE.json").read_text(encoding="utf-8"))["returncode_hex"], "0xC0000374")

    def test_pass(self):
        self.assertEqual(self._main(mf(0, GOOD_STDOUT)), 0)

    def test_unauthorized_binary_refused_without_running(self):
        with mock.patch.object(H, "authorized_binaries", return_value=None), \
             mock.patch.object(H, "run_model_free", side_effect=AssertionError("ran")):
            self.assertEqual(H.main(["model-free", "--out", str(self.out)]), 2)


if __name__ == "__main__":
    unittest.main()
