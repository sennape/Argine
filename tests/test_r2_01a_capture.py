"""Tests for R2-01A (canonical trace recovery / fresh-capture readiness).

Synthetic fixtures and fake/zero-byte "model" files only: nothing here
starts the real 30B model or needs real historical evidence.
"""

from __future__ import annotations

import gzip
import json
import os
import subprocess
import shutil
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT))

from argine.research.trace import Step, Trace, TraceMeta  # noqa: E402
from argine.research2 import runner, store  # noqa: E402
from argine.research2.r2_01 import capture as r2_01_capture  # noqa: E402
from argine.research2.r2_01 import trace_source  # noqa: E402


def _write_sim_trace(path: Path) -> None:
    meta = TraceMeta(model="sim", n_experts=4, top_k=2, layers=[0, 1],
                     expert_bytes={0: 1000, 1: 2000}, workload="coding", split="dev",
                     source="test_r2_01a_capture")
    steps = [Step(graph=0, n_tokens=1, phase="decode", segment="warmup",
                 routes={0: [(0, 1)], 1: [(0, 1)]})]
    for i in range(5):
        steps.append(Step(graph=i + 1, n_tokens=1, phase="decode", segment="synthetic",
                          routes={0: [(i % 4, (i + 1) % 4)], 1: [(i % 4, (i + 1) % 4)]}))
    trace = Trace(meta=meta, steps=steps)
    with gzip.open(path, "wt", encoding="utf-8") as fh:
        fh.write(json.dumps({"format": "argine-router-trace", "version": 1,
                             "meta": trace.meta.to_json()}) + "\n")
        for st in trace.steps:
            fh.write(json.dumps(st.to_json()) + "\n")


class _TmpCase(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="r2-01a-"))
        self.addCleanup(lambda: shutil.rmtree(self.tmp, ignore_errors=True))


class ProtocolTests(unittest.TestCase):
    def test_protocol_is_complete(self):
        self.assertEqual(r2_01_capture.validate_protocol(), [])

    def test_incomplete_protocol_fails_closed(self):
        with mock.patch.dict(r2_01_capture.PROTOCOL, {"seed": None}):
            missing = r2_01_capture.validate_protocol()
            self.assertIn("seed", missing)
            with self.assertRaises(r2_01_capture.CaptureProtocolIncomplete):
                r2_01_capture.plan_capture_command("/x/model.gguf", out_dir=Path("/tmp/x"))

    def test_plan_command_matches_documented_protocol(self):
        cmd = r2_01_capture.plan_capture_command("/x/model.gguf", out_dir=Path("/tmp/out"))
        self.assertIn("--workload", cmd)
        self.assertEqual(cmd[cmd.index("--workload") + 1], "coding")
        self.assertIn("--split", cmd)
        self.assertEqual(cmd[cmd.index("--split") + 1], "dev")
        self.assertIn("--seed", cmd)
        self.assertEqual(cmd[cmd.index("--seed") + 1], "42")
        self.assertIn("--expert-budget", cmd)
        self.assertEqual(cmd[cmd.index("--expert-budget") + 1], "4G")
        # threads-batch has no dedicated capture flag; passed through the
        # existing --llama-arg passthrough using the "=" form (a bare
        # "--llama-arg --threads-batch" is rejected by argparse -- see
        # LlamaArgRealParserTests below, which proves this against the
        # real parser, not just this string check).
        self.assertIn("--llama-arg=--threads-batch", cmd)
        self.assertNotIn("--threads-batch", cmd)  # never as a separate bare token
        idx = cmd.index("--llama-arg=--threads-batch")
        self.assertEqual(cmd[idx + 1], "--llama-arg")
        self.assertEqual(cmd[idx + 2], "12")


class LlamaArgRealParserTests(unittest.TestCase):
    """Proves the exact R2-01 capture argv parses through the REAL
    `argine research trace` argparse parser (not just a string check), and
    that the originally-observed real failure is reproduced and fixed."""

    @classmethod
    def setUpClass(cls):
        import argine.cli as argine_cli
        cls.parser = argine_cli.build_parser()

    def test_real_protocol_command_parses_successfully(self):
        cmd = r2_01_capture.plan_capture_command(
            "/tmp/Qwen_Qwen3-30B-A3B-Q4_K_M.gguf", out_dir=Path("/tmp/out"))
        # strip the leading [sys.executable, "-m", "argine"]: build_parser()
        # parses from "research" onward, exactly like argine.bat does.
        argv = cmd[3:]
        ns = self.parser.parse_args(argv)  # must not raise SystemExit
        self.assertEqual(ns.workload, "coding")
        self.assertEqual(ns.workload_version, "v1")
        self.assertEqual(ns.split, "dev")
        self.assertEqual(ns.seed, 42)
        self.assertEqual(ns.threads, 4)
        self.assertEqual(ns.ctx, 4096)

    def test_downstream_dash_option_survives_llama_arg_exactly(self):
        cmd = r2_01_capture.plan_capture_command("/x/model.gguf", out_dir=Path("/tmp/out"))
        ns = self.parser.parse_args(cmd[3:])
        self.assertEqual(ns.llama_arg, ["--threads-batch", "12"])

    def test_generation_threads_four_represented_correctly(self):
        cmd = r2_01_capture.plan_capture_command("/x/model.gguf", out_dir=Path("/tmp/out"))
        ns = self.parser.parse_args(cmd[3:])
        self.assertEqual(ns.threads, 4)

    def test_batch_threads_twelve_survives_as_llama_arg_value(self):
        cmd = r2_01_capture.plan_capture_command("/x/model.gguf", out_dir=Path("/tmp/out"))
        ns = self.parser.parse_args(cmd[3:])
        self.assertEqual(ns.llama_arg[ns.llama_arg.index("--threads-batch") + 1], "12")

    def test_original_broken_form_is_reproduced_and_rejected(self):
        """Regression anchor: the exact malformed argv the user actually hit
        in production (two bare '--llama-arg' occurrences straddling a
        dash-prefixed value) must still fail the real parser the same way,
        proving this test suite would have caught the original defect."""
        broken = ["trace", "/x/model.gguf", "--expert-store", "--expert-budget", "4G",
                 "--workload", "coding", "--workload-version", "v1", "--split", "dev",
                 "--seed", "42", "--threads", "4",
                 "--llama-arg", "--threads-batch", "--llama-arg", "12",
                 "--ctx", "4096", "--out", "/tmp/out"]
        with self.assertRaises(SystemExit):
            with mock.patch("sys.stderr"):
                self.parser.parse_args(broken)

    def test_expert_store_auto_resolution_flag_present(self):
        cmd = r2_01_capture.plan_capture_command("/x/model.gguf", out_dir=Path("/tmp/out"))
        ns = self.parser.parse_args(cmd[3:])
        self.assertEqual(ns.expert_store, "auto")  # bare --expert-store -> const="auto"

    def test_output_path_survives(self):
        out = Path("/tmp/capture-out-dir")
        cmd = r2_01_capture.plan_capture_command("/x/model.gguf", out_dir=out)
        ns = self.parser.parse_args(cmd[3:])
        self.assertEqual(ns.out, str(out))

    def test_no_real_runtime_or_model_started(self):
        """Parsing alone must never touch a model or runtime."""
        cmd = r2_01_capture.plan_capture_command("/x/model.gguf", out_dir=Path("/tmp/out"))
        with mock.patch("subprocess.run") as m, mock.patch("subprocess.Popen") as m2:
            self.parser.parse_args(cmd[3:])
            m.assert_not_called()
            m2.assert_not_called()


class RegistrationTests(_TmpCase):
    def _fake_file(self, name="fake.jsonl.gz", content=b"hello") -> Path:
        p = self.tmp / name
        p.write_bytes(content)
        return p

    def test_wrong_run_identity_rejected_for_historical_canonical(self):
        p = self._fake_file()
        with self.assertRaises(trace_source.SourceRegistrationError):
            trace_source.register_source(path=p, source_experiment_id="SMOKE-30B",
                                         source_run_id="run-00-4G",
                                         source_type="historical_canonical")

    def test_hash_size_mismatch_rejected_for_historical_canonical(self):
        p = self._fake_file()
        exp = trace_source.DOCUMENTED_CANONICAL_EXP019
        with self.assertRaises(trace_source.SourceRegistrationError):
            trace_source.register_source(
                path=p, source_experiment_id=exp["source_experiment_id"],
                source_run_id=exp["source_run_id"], source_type="historical_canonical")

    def test_exact_documented_identity_and_hash_accepted(self):
        exp = trace_source.DOCUMENTED_CANONICAL_EXP019
        p = self.tmp / "exact.jsonl.gz"
        # construct a file whose size/sha256 match the documented values is
        # impractical here (that's the real historical file's content, not
        # reconstructable); instead verify the acceptance branch logic
        # directly by monkeypatching the documented constants to match a
        # file we control, proving the "exact match -> accepted" path works.
        p.write_bytes(b"x" * 10)
        import hashlib
        sha = hashlib.sha256(b"x" * 10).hexdigest()
        with mock.patch.dict(trace_source.DOCUMENTED_CANONICAL_EXP019,
                             {"expected_sha256": sha, "expected_size_bytes": 10}):
            reg = trace_source.register_source(
                path=p, source_experiment_id=exp["source_experiment_id"],
                source_run_id=exp["source_run_id"], source_type="historical_canonical")
        self.assertEqual(reg.verification_status, "verified")
        self.assertEqual(reg.source_type, "historical_canonical")

    def test_synthetic_fixture_never_promoted_via_registration(self):
        """The real EXP-019 synthetic fixture (e2e-fixture/trace.jsonl.gz) is
        tiny and will never match the documented canonical size/hash, so it
        can never be registered as historical_canonical, however it is
        named or pointed at."""
        p = self._fake_file("trace.jsonl.gz", content=b"synthetic fixture content")
        exp = trace_source.DOCUMENTED_CANONICAL_EXP019
        with self.assertRaises(trace_source.SourceRegistrationError):
            trace_source.register_source(
                path=p, source_experiment_id=exp["source_experiment_id"],
                source_run_id=exp["source_run_id"], source_type="historical_canonical")

    def test_smoke_trace_never_promoted_via_registration(self):
        p = self._fake_file("smoke-trace.jsonl.gz", content=b"smoke run content, not real")
        with self.assertRaises(trace_source.SourceRegistrationError):
            trace_source.register_source(path=p, source_experiment_id="SMOKE-30B-r2",
                                         source_run_id="run-00-4G",
                                         source_type="historical_canonical")

    def test_fresh_capture_is_self_verified(self):
        p = self._fake_file()
        reg = trace_source.register_source(path=p, source_experiment_id="R2-01",
                                           source_run_id="run-xyz",
                                           source_type="fresh_capture")
        self.assertEqual(reg.verification_status, "verified")
        self.assertEqual(reg.source_type, "fresh_capture")
        self.assertEqual(reg.size_bytes, p.stat().st_size)

    def test_missing_file_rejected(self):
        with self.assertRaises(trace_source.SourceRegistrationError):
            trace_source.register_source(path=self.tmp / "nope.jsonl.gz",
                                         source_experiment_id="R2-01",
                                         source_run_id="x", source_type="fresh_capture")

    def test_unknown_source_type_rejected(self):
        p = self._fake_file()
        with self.assertRaises(trace_source.SourceRegistrationError):
            trace_source.register_source(path=p, source_experiment_id="R2-01",
                                         source_run_id="x", source_type="bogus")


class StorePersistenceTests(_TmpCase):
    def setUp(self):
        super().setUp()
        self._old_root = os.environ.get("ARGINE3_RESEARCH_ROOT")
        os.environ["ARGINE3_RESEARCH_ROOT"] = str(self.tmp)
        self.addCleanup(self._restore)

    def _restore(self):
        if self._old_root is None:
            os.environ.pop("ARGINE3_RESEARCH_ROOT", None)
        else:
            os.environ["ARGINE3_RESEARCH_ROOT"] = self._old_root

    def test_registration_persists_outside_git_and_round_trips(self):
        reg = trace_source.SourceRegistration(
            path="/tmp/x.jsonl.gz", source_experiment_id="R2-01", source_run_id="r1",
            size_bytes=5, sha256="abc", source_type="fresh_capture",
            verification_status="verified", registered_at_utc="2026-01-01T00:00:00Z")
        store.set_trace_source_registration("R2-01", reg.to_json())
        got = store.get_trace_source_registration("R2-01")
        self.assertEqual(got["path"], "/tmp/x.jsonl.gz")
        self.assertEqual(got["source_type"], "fresh_capture")
        # persisted as a plain file under the (non-Git) evidence root
        self.assertTrue((self.tmp / "state.json").exists())

    def test_registration_does_not_touch_the_git_repository(self):
        before = set((REPO_ROOT / "research2").glob("*"))
        reg = trace_source.SourceRegistration(
            path="/tmp/x.jsonl.gz", source_experiment_id="R2-01", source_run_id="r1",
            size_bytes=5, sha256="abc", source_type="fresh_capture",
            verification_status="verified", registered_at_utc="2026-01-01T00:00:00Z")
        store.set_trace_source_registration("R2-01", reg.to_json())
        after = set((REPO_ROOT / "research2").glob("*"))
        self.assertEqual(before, after)

    def test_registration_merges_rather_than_clobbers_state(self):
        store.set_experiment_state("R2-01", "READY")
        reg = trace_source.SourceRegistration(
            path="/tmp/x.jsonl.gz", source_experiment_id="R2-01", source_run_id="r1",
            size_bytes=5, sha256="abc", source_type="fresh_capture",
            verification_status="verified", registered_at_utc="2026-01-01T00:00:00Z")
        store.set_trace_source_registration("R2-01", reg.to_json())
        states = store.load_state()
        self.assertEqual(states["R2-01"]["state"], "READY")
        self.assertIn("trace_source_registration", states["R2-01"])


class RunResolvesRegisteredSourceTests(_TmpCase):
    def setUp(self):
        super().setUp()
        self._old_root = os.environ.get("ARGINE3_RESEARCH_ROOT")
        os.environ["ARGINE3_RESEARCH_ROOT"] = str(self.tmp)
        self.addCleanup(self._restore)

    def _restore(self):
        if self._old_root is None:
            os.environ.pop("ARGINE3_RESEARCH_ROOT", None)
        else:
            os.environ["ARGINE3_RESEARCH_ROOT"] = self._old_root

    def test_run_r2_01_resolves_registered_source_automatically(self):
        from argine.research2.r2_01 import orchestrator
        trace_path = self.tmp / "captured-trace.jsonl.gz"
        _write_sim_trace(trace_path)
        reg = trace_source.register_source(path=trace_path, source_experiment_id="R2-01",
                                           source_run_id="simulated", source_type="fresh_capture")
        store.set_trace_source_registration("R2-01", reg.to_json())
        # no trace_source_arg passed: must resolve via the registration alone
        result = orchestrator.run()
        self.assertIn(result["outcome"], ("PASS", "INCONCLUSIVE", "FAIL"))
        self.assertEqual(result["trace_source"], str(trace_path))

    def test_status_reports_trace_source_readiness_cheaply(self):
        import time
        t0 = time.monotonic()
        report = runner.status_report()
        elapsed = time.monotonic() - t0
        # Generous bound: this environment's git subprocess calls (status_report's
        # own pre-existing git_is_clean()/rev-parse calls, unrelated to this
        # extension) have been observed to take several seconds over the
        # remote device bridge; the point of this test is to catch an O(GB)
        # hashing regression, not to benchmark git latency here.
        self.assertLess(elapsed, 30.0, "status must stay cheap (no multi-GB hashing)")
        r2_01 = report["experiments"]["R2-01"]
        self.assertIn("trace_source_status", r2_01)
        self.assertIn("trace_source_runnable", r2_01)
        self.assertIn(r2_01["trace_source_status"],
                     (trace_source.TRACE_SOURCE_MISSING, trace_source.TRACE_SOURCE_AMBIGUOUS,
                      trace_source.TRACE_SOURCE_RESOLVED))

    def test_status_reflects_registered_runnable_source(self):
        trace_path = self.tmp / "captured-trace2.jsonl.gz"
        _write_sim_trace(trace_path)
        reg = trace_source.register_source(path=trace_path, source_experiment_id="R2-01",
                                           source_run_id="simulated2", source_type="fresh_capture")
        store.set_trace_source_registration("R2-01", reg.to_json())
        report = runner.status_report()
        r2_01 = report["experiments"]["R2-01"]
        self.assertTrue(r2_01["trace_source_runnable"])
        self.assertEqual(r2_01["trace_source_status"], trace_source.TRACE_SOURCE_RESOLVED)
        self.assertEqual(r2_01["trace_source_type"], "fresh_capture")
        self.assertIsNone(r2_01["trace_source_blocker"])


class CaptureFunctionTests(_TmpCase):
    def test_protocol_incomplete_blocks_before_model_check(self):
        with mock.patch.dict(r2_01_capture.PROTOCOL, {"workload": None}):
            result = r2_01_capture.capture(model_path="/does/not/matter", dry_run=True)
        self.assertEqual(result["outcome"], r2_01_capture.OUTCOME_PROTOCOL_INCOMPLETE)
        self.assertIn("workload", result["missing_fields"])

    def test_model_not_found(self):
        result = r2_01_capture.capture(model_path=str(self.tmp / "nope.gguf"), dry_run=True)
        self.assertEqual(result["outcome"], r2_01_capture.OUTCOME_MODEL_NOT_FOUND)

    def test_dry_run_plans_without_executing(self):
        model = self.tmp / "fake.gguf"
        model.write_bytes(b"not a real model")
        result = r2_01_capture.capture(model_path=model, dry_run=True,
                                       out_root=self.tmp / "evidence")
        self.assertEqual(result["outcome"], r2_01_capture.OUTCOME_DRY_RUN)
        self.assertIn("command", result)
        self.assertEqual(result["command"][4], "trace")
        # nothing executed: no evidence directory was created
        self.assertFalse((self.tmp / "evidence").exists())

    def test_capture_failed_when_subprocess_produces_nothing(self):
        model = self.tmp / "fake2.gguf"
        model.write_bytes(b"not a real model")
        with mock.patch("subprocess.run") as m:
            m.return_value = mock.Mock(returncode=1, stdout="", stderr="boom")
            result = r2_01_capture.capture(model_path=model, dry_run=False,
                                           out_root=self.tmp / "evidence2")
        self.assertEqual(result["outcome"], r2_01_capture.OUTCOME_CAPTURE_FAILED)
        self.assertEqual(result["exit_code"], 1)
        self.assertIsNone(store.get_trace_source_registration("R2-01"))


class CaptureExperimentGateTests(_TmpCase):
    def test_capture_experiment_refuses_unknown_id(self):
        with self.assertRaises(KeyError):
            runner.capture_experiment("R2-99")

    def test_capture_experiment_not_implemented_for_r2_02(self):
        # R2-02 registers no capture implementation
        result = runner.capture_experiment("R2-02", allow_dirty=True)
        self.assertEqual(result["outcome"], "NOT_IMPLEMENTED")

    def test_capture_experiment_dispatches_to_r2_01(self):
        model = self.tmp / "fake3.gguf"
        model.write_bytes(b"not a real model")
        result = runner.capture_experiment("R2-01", allow_dirty=True,
                                           model_path=str(model), dry_run=True)
        self.assertEqual(result["outcome"], r2_01_capture.OUTCOME_DRY_RUN)

    def test_capture_experiment_requires_model(self):
        result = runner.capture_experiment("R2-01", allow_dirty=True, dry_run=True)
        self.assertEqual(result["outcome"], "USAGE_ERROR")


class CliExitCodeTests(_TmpCase):
    """Defect B: capture ... && run R2-01 must not run `run` when capture
    logically failed. cli.main()'s int return value IS the process's exit
    status (argine/__main__.py does sys.exit(main())), so testing main()'s
    return value here is equivalent to testing the real process exit code;
    a POSIX subprocess chain is also tested below as an end-to-end proof,
    and argine.bat (Windows) was separately fixed to stop losing this exit
    code across setlocal/endlocal (see argine.bat's own comment)."""

    def setUp(self):
        super().setUp()
        self._old_root = os.environ.get("ARGINE3_RESEARCH_ROOT")
        os.environ["ARGINE3_RESEARCH_ROOT"] = str(self.tmp)
        self.addCleanup(self._restore)

    def _restore(self):
        if self._old_root is None:
            os.environ.pop("ARGINE3_RESEARCH_ROOT", None)
        else:
            os.environ["ARGINE3_RESEARCH_ROOT"] = self._old_root

    def test_capture_failed_is_nonzero_at_top_level(self):
        import argine.cli as cli
        model = self.tmp / "fake.gguf"
        model.write_bytes(b"x")
        with mock.patch("subprocess.run") as m:
            m.return_value = mock.Mock(returncode=2, stdout="", stderr="boom")
            rc = cli.main(["research2", "capture", "R2-01", "--model", str(model),
                          "--allow-dirty"])
        self.assertNotEqual(rc, 0)

    def test_successful_mocked_capture_is_zero_at_top_level(self):
        import argine.cli as cli
        model = self.tmp / "fake2.gguf"
        model.write_bytes(b"x")
        rc = cli.main(["research2", "capture", "R2-01", "--model", str(model),
                      "--allow-dirty", "--dry-run"])
        self.assertEqual(rc, 0)

    def test_model_not_found_is_nonzero(self):
        import argine.cli as cli
        rc = cli.main(["research2", "capture", "R2-01", "--model",
                      str(self.tmp / "nope.gguf"), "--allow-dirty"])
        self.assertNotEqual(rc, 0)

    def test_dirty_tree_refusal_is_nonzero(self):
        import argine.cli as cli
        from argine.research2 import runner as _runner
        model = self.tmp / "fake3.gguf"
        model.write_bytes(b"x")
        with mock.patch.object(_runner, "git_is_clean", return_value=False):
            rc = cli.main(["research2", "capture", "R2-01", "--model", str(model)])
        self.assertNotEqual(rc, 0)

    def test_protocol_incomplete_is_nonzero(self):
        import argine.cli as cli
        model = self.tmp / "fake4.gguf"
        model.write_bytes(b"x")
        with mock.patch.dict(r2_01_capture.PROTOCOL, {"workload": None}):
            rc = cli.main(["research2", "capture", "R2-01", "--model", str(model),
                          "--allow-dirty"])
        self.assertNotEqual(rc, 0)

    def test_unknown_capture_experiment_is_nonzero(self):
        import argine.cli as cli
        rc = cli.main(["research2", "capture", "R2-99", "--model", "/tmp/x",
                      "--allow-dirty"])
        self.assertNotEqual(rc, 0)

    def test_not_implemented_capture_is_nonzero(self):
        import argine.cli as cli
        rc = cli.main(["research2", "capture", "R2-02", "--model", "/tmp/x",
                      "--allow-dirty"])
        self.assertNotEqual(rc, 0)

    def test_failed_capture_registers_no_source(self):
        import argine.cli as cli
        model = self.tmp / "fake5.gguf"
        model.write_bytes(b"x")
        with mock.patch("subprocess.run") as m:
            m.return_value = mock.Mock(returncode=2, stdout="", stderr="boom")
            cli.main(["research2", "capture", "R2-01", "--model", str(model),
                     "--allow-dirty"])
        self.assertIsNone(store.get_trace_source_registration("R2-01"))

    def test_failed_capture_cannot_become_candidate_trace_evidence(self):
        """A CAPTURE_FAILED run's own stdout.log/stderr.log/manifest.json
        (legitimate failure diagnostics) must never be resolvable by
        trace_source.resolve() as a usable trace -- they are not even a
        trace file, let alone a plausible one."""
        import argine.cli as cli
        model = self.tmp / "fake6.gguf"
        model.write_bytes(b"x")
        with mock.patch("subprocess.run") as m:
            m.return_value = mock.Mock(returncode=2, stdout="", stderr="boom")
            result_holder = {}
            orig_capture = r2_01_capture.capture
            def _spy(*a, **kw):
                r = orig_capture(*a, **kw)
                result_holder["result"] = r
                return r
            with mock.patch.object(r2_01_capture, "capture", side_effect=_spy):
                from argine.research2 import runner as _runner
                _runner.capture_experiment("R2-01", allow_dirty=True, model_path=str(model),
                                           dry_run=False)
        capture_dir = Path(result_holder["result"]["capture_dir"])
        self.assertTrue((capture_dir / "stderr.log").exists())
        resolution = trace_source.resolve()
        self.assertNotEqual(resolution.resolved_path, str(capture_dir / "stderr.log"))
        self.assertNotIn(str(capture_dir), str(resolution.resolved_path or ""))


class ShellChainRegressionTests(unittest.TestCase):
    """End-to-end proof, via the real `python -m argine` subprocess (the
    exact child process argine.bat invokes), that shell `&&` chaining
    behaves correctly: a logical capture failure must produce a non-zero
    OS process exit code, so a chained `&& next-command` never runs."""

    def _run(self, args, root):
        env = dict(os.environ)
        env["ARGINE3_RESEARCH_ROOT"] = str(root)
        return subprocess.run([sys.executable, "-m", "argine", *args], cwd=str(REPO_ROOT),
                              env=env, capture_output=True, text=True, timeout=60)

    def test_real_subprocess_nonzero_blocks_shell_chain(self):
        root = Path(tempfile.mkdtemp(prefix="r2-01a-shell-"))
        try:
            r = self._run(["research2", "capture", "R2-01", "--model",
                          str(root / "nope.gguf"), "--allow-dirty"], root)
            self.assertNotEqual(r.returncode, 0)
            # the real shell-level proof: `&&` after a nonzero-exit process
            # never runs the right-hand side, in any POSIX shell.
            chain = subprocess.run(
                f'{sys.executable} -m argine research2 capture R2-01 '
                f'--model {root / "nope.gguf"} --allow-dirty && echo SENTINEL_RAN',
                shell=True, cwd=str(REPO_ROOT),
                env={**os.environ, "ARGINE3_RESEARCH_ROOT": str(root)},
                capture_output=True, text=True, timeout=60)
            self.assertNotIn("SENTINEL_RAN", chain.stdout)
            self.assertNotEqual(chain.returncode, 0)
        finally:
            shutil.rmtree(root, ignore_errors=True)

    def test_real_subprocess_zero_on_success_allows_shell_chain(self):
        root = Path(tempfile.mkdtemp(prefix="r2-01a-shell-ok-"))
        model = root / "fake.gguf"
        try:
            root.mkdir(parents=True, exist_ok=True)
            model.write_bytes(b"x")
            chain = subprocess.run(
                f'{sys.executable} -m argine research2 capture R2-01 '
                f'--model {model} --allow-dirty --dry-run && echo SENTINEL_RAN',
                shell=True, cwd=str(REPO_ROOT),
                env={**os.environ, "ARGINE3_RESEARCH_ROOT": str(root)},
                capture_output=True, text=True, timeout=60)
            self.assertIn("SENTINEL_RAN", chain.stdout)
            self.assertEqual(chain.returncode, 0)
        finally:
            shutil.rmtree(root, ignore_errors=True)


if __name__ == "__main__":
    unittest.main()
