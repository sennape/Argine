"""Tests for R2-01A (canonical trace recovery / fresh-capture readiness).

Synthetic fixtures and fake/zero-byte "model" files only: nothing here
starts the real 30B model or needs real historical evidence.
"""

from __future__ import annotations

import gzip
import json
import os
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
        # threads-batch has no dedicated flag; passed through --llama-arg
        self.assertIn("--llama-arg", cmd)
        self.assertIn("--threads-batch", cmd)


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


if __name__ == "__main__":
    unittest.main()
