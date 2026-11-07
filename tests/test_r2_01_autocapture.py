"""Tests for R2-01 round 3: automating the capture prerequisite into
`research2 run R2-01 --model PATH`, so the human no longer chains
`capture ... && run ...` through the shell.

Synthetic fixtures and a mocked capture() implementation only: nothing
here starts the real 30B model, and nothing here shells out to real git
(git_is_clean() is patched directly, the same boundary the runner itself
checks against) or a real llama-server child process.
"""

from __future__ import annotations

import os
import shutil
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT))
sys.path.insert(0, str(REPO_ROOT / "tests"))

from argine.research2 import runner, store  # noqa: E402
from argine.research2.r2_01 import capture as r2_01_capture  # noqa: E402
from argine.research2.r2_01 import orchestrator, trace_source  # noqa: E402
from test_r2_01 import make_trace, write_trace  # noqa: E402

_CAPTURE_RUN_COUNTER = {"n": 0}


def _mock_capture_factory(tmp_dir: Path, *, succeed: bool):
    """Stands in for the EXISTING R2-01 capture implementation
    (argine.research2.r2_01.capture.capture), at the function boundary
    rather than the subprocess boundary, so these orchestration tests
    don't re-exercise capture()'s own internals (covered separately by
    tests/test_r2_01a_capture.py) and don't pay for a real git subprocess
    call per invocation."""

    def _capture(*, model_path, allow_dirty=False, dry_run=False, **_kw):
        if dry_run:
            return {"experiment_id": "R2-01", "outcome": r2_01_capture.OUTCOME_DRY_RUN}
        if not Path(model_path).is_file():
            return {"experiment_id": "R2-01", "outcome": "MODEL_NOT_FOUND",
                    "message": f"model not found: {model_path}"}
        _CAPTURE_RUN_COUNTER["n"] += 1
        run_id = f"fake-capture-run-{_CAPTURE_RUN_COUNTER['n']}"
        if not succeed:
            capture_dir = tmp_dir / f"capture-{run_id}"
            capture_dir.mkdir(parents=True, exist_ok=True)
            return {"experiment_id": "R2-01", "run_id": run_id, "outcome": "CAPTURE_FAILED",
                    "capture_dir": str(capture_dir),
                    "message": "simulated capture failure: runtime did not start"}
        trace_path = tmp_dir / f"captured-{run_id}.jsonl.gz"
        write_trace(make_trace(), trace_path)
        reg = trace_source.register_source(path=trace_path, source_experiment_id="R2-01",
                                           source_run_id=run_id, source_type="fresh_capture")
        store.set_trace_source_registration("R2-01", reg.to_json())
        return {"experiment_id": "R2-01", "run_id": run_id, "outcome": "CAPTURED",
                "candidate_status": "NON-CANDIDATE", "trace_path": str(trace_path)}

    return _capture


def _mock_capture_claims_success_without_registering(*, model_path, allow_dirty=False,
                                                      dry_run=False, **_kw):
    """A capture() that CLAIMS CAPTURED but never actually persisted a
    registration -- the scenario item 9 (fail-closed) exercises."""
    return {"experiment_id": "R2-01", "run_id": "ghost-run", "outcome": "CAPTURED",
            "candidate_status": "NON-CANDIDATE",
            "trace_path": "/nonexistent/ghost.jsonl.gz"}


class _EvidenceRootCase(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="r2-01-autocapture-"))
        self.addCleanup(lambda: shutil.rmtree(self.tmp, ignore_errors=True))
        self._old_root = os.environ.get("ARGINE3_RESEARCH_ROOT")
        os.environ["ARGINE3_RESEARCH_ROOT"] = str(self.tmp / "evidence")
        self.addCleanup(self._restore_root)
        self.model = self.tmp / "fake-model.gguf"
        self.model.write_bytes(b"not a real model")
        # Patch at the same boundary run_experiment/capture_experiment
        # already check against -- no real git subprocess calls.
        self._clean_patch = mock.patch("argine.research2.runner.git_is_clean",
                                       return_value=True)
        self._clean_patch.start()
        self.addCleanup(self._clean_patch.stop)

    def _restore_root(self):
        if self._old_root is None:
            os.environ.pop("ARGINE3_RESEARCH_ROOT", None)
        else:
            os.environ["ARGINE3_RESEARCH_ROOT"] = self._old_root

    def _patch_capture(self, *, succeed: bool):
        p = mock.patch.object(r2_01_capture, "capture",
                              side_effect=_mock_capture_factory(self.tmp, succeed=succeed))
        p.start()
        self.addCleanup(p.stop)
        return r2_01_capture.capture


class RegisteredSourceWinsTests(_EvidenceRootCase):
    """Items 1, 13, 14: an already-registered valid trace is used directly;
    --model is optional/ignored; a second run does not recapture."""

    def _register_fixture_source(self) -> Path:
        src = self.tmp / "existing-trace.jsonl.gz"
        write_trace(make_trace(), src)
        reg = trace_source.register_source(path=src, source_experiment_id="R2-01",
                                           source_run_id="pre-existing",
                                           source_type="fresh_capture")
        store.set_trace_source_registration("R2-01", reg.to_json())
        return src

    def test_registered_source_used_without_model_no_capture(self):
        src = self._register_fixture_source()
        capture_mock = self._patch_capture(succeed=False)
        result = runner.run_experiment("R2-01", allow_dirty=True)  # no model_path at all
        capture_mock.assert_not_called()
        self.assertIn(result["outcome"], ("PASS", "INCONCLUSIVE", "FAIL"))
        self.assertEqual(result["trace_source"], str(src))
        self.assertIsNone(result["capture_provenance"])

    def test_registered_source_used_even_with_model_given(self):
        self._register_fixture_source()
        capture_mock = self._patch_capture(succeed=False)
        result = runner.run_experiment("R2-01", allow_dirty=True, model_path=str(self.model))
        capture_mock.assert_not_called()  # model is ignored/not required: no capture attempted
        self.assertIn(result["outcome"], ("PASS", "INCONCLUSIVE", "FAIL"))

    def test_second_run_does_not_recapture(self):
        capture_mock = self._patch_capture(succeed=True)
        first = runner.run_experiment("R2-01", allow_dirty=True, model_path=str(self.model))
        calls_after_first = capture_mock.call_count
        second = runner.run_experiment("R2-01", allow_dirty=True, model_path=str(self.model))
        calls_after_second = capture_mock.call_count
        self.assertIn(first["outcome"], ("PASS", "INCONCLUSIVE", "FAIL"))
        self.assertIn(second["outcome"], ("PASS", "INCONCLUSIVE", "FAIL"))
        self.assertEqual(calls_after_first, calls_after_second,
                         "second run must not invoke another capture")
        self.assertEqual(first["trace_source"], second["trace_source"])


class ExplicitSourcePrecedenceTests(_EvidenceRootCase):
    """Items 2, 17: --trace-source always wins, never triggers auto-capture
    even when --model is also given."""

    def test_explicit_trace_source_wins_no_capture_even_with_model(self):
        src = self.tmp / "explicit.jsonl.gz"
        write_trace(make_trace(), src)
        capture_mock = self._patch_capture(succeed=False)
        result = runner.run_experiment("R2-01", allow_dirty=True,
                                       trace_source_arg=str(src), model_path=str(self.model))
        capture_mock.assert_not_called()
        self.assertEqual(result["trace_source"], str(src))
        self.assertIsNone(result["capture_provenance"])


class AutoCaptureSuccessTests(_EvidenceRootCase):
    """Items 3, 4, 10: no trace + model -> capture automatically invoked;
    on success R2-01 continues automatically using exactly that source."""

    def test_no_trace_with_model_triggers_capture_and_continues(self):
        capture_mock = self._patch_capture(succeed=True)
        result = orchestrator.run(allow_dirty=True, model_path=str(self.model))
        capture_mock.assert_called_once()
        self.assertIn(result["outcome"], ("PASS", "INCONCLUSIVE", "FAIL"))
        self.assertIsNotNone(result["capture_provenance"])

    def test_capture_success_source_identity_matches_what_run_used(self):
        self._patch_capture(succeed=True)
        result = orchestrator.run(allow_dirty=True, model_path=str(self.model))
        reg = store.get_trace_source_registration("R2-01")
        self.assertIsNotNone(reg)
        self.assertEqual(result["trace_source"], reg["path"])
        self.assertEqual(result["capture_provenance"]["trace_source_path"], reg["path"])
        self.assertEqual(result["capture_provenance"]["trace_sha256"], reg["sha256"])

    def test_capture_provenance_binds_capture_run_id(self):
        self._patch_capture(succeed=True)
        result = orchestrator.run(allow_dirty=True, model_path=str(self.model))
        cp = result["capture_provenance"]
        self.assertIn("capture_run_id", cp)
        self.assertIsNotNone(cp["capture_run_id"])
        self.assertNotEqual(cp["capture_run_id"], result["run_id"],
                            "capture and R2-01 scientific run get distinct run ids")


class AutoCaptureFailureAtomicityTests(_EvidenceRootCase):
    """Items 5, 6, 7, 8: capture failure is atomic -- no replay, non-zero
    exit, scientific state unchanged, R2-02 stays locked."""

    def test_capture_failure_never_invokes_replay(self):
        self._patch_capture(succeed=False)
        result = orchestrator.run(allow_dirty=True, model_path=str(self.model))
        self.assertEqual(result["outcome"], orchestrator.OUTCOME_CAPTURE_PREREQUISITE_FAILED)
        self.assertNotIn("reasons", result)  # replay/classification never ran
        self.assertIn("R2-01 NOT RUN", result["message"])

    def test_capture_failure_creates_no_r2_01_run_directory(self):
        evidence = Path(os.environ["ARGINE3_RESEARCH_ROOT"])
        self._patch_capture(succeed=False)
        orchestrator.run(allow_dirty=True, model_path=str(self.model))
        self.assertEqual(list((evidence / "R2-01").glob("*/summary.json")), [])

    def test_capture_failure_top_level_cli_exit_nonzero(self):
        self._patch_capture(succeed=False)
        import argine.cli as cli
        rc = cli.main(["research2", "run", "R2-01", "--model", str(self.model),
                      "--allow-dirty"])
        self.assertNotEqual(rc, 0)

    def test_capture_failure_leaves_experiment_state_untouched(self):
        self._patch_capture(succeed=False)
        orchestrator.run(allow_dirty=True, model_path=str(self.model))
        states = store.load_state()
        self.assertNotIn("R2-01", states)  # never transitioned, never touched

    def test_capture_failure_r2_02_remains_not_implemented(self):
        self._patch_capture(succeed=False)
        orchestrator.run(allow_dirty=True, model_path=str(self.model))
        result = runner.run_experiment("R2-02", allow_dirty=True)
        self.assertEqual(result["outcome"], "NOT_IMPLEMENTED")

    def test_capture_failure_does_not_register_a_source(self):
        self._patch_capture(succeed=False)
        orchestrator.run(allow_dirty=True, model_path=str(self.model))
        self.assertIsNone(store.get_trace_source_registration("R2-01"))


class FailClosedOnMissingRegistrationTests(_EvidenceRootCase):
    """Item 9: a capture call that CLAIMS CAPTURED but never actually
    persisted a registration must never be blindly trusted -- run()
    re-resolves from the real store state, not from the claim."""

    def test_claimed_capture_without_real_registration_fails_closed(self):
        p = mock.patch.object(r2_01_capture, "capture",
                              side_effect=_mock_capture_claims_success_without_registering)
        p.start()
        self.addCleanup(p.stop)
        result = orchestrator.run(allow_dirty=True, model_path=str(self.model))
        self.assertNotIn(result["outcome"], ("PASS", "INCONCLUSIVE", "FAIL"))
        self.assertIsNone(store.get_trace_source_registration("R2-01"))


class NoModelBlockerTests(_EvidenceRootCase):
    """Items 11, 12: no trace + no model -> clear blocker; an invalid
    model path -> clear blocker, no replay (capture's own MODEL_NOT_FOUND,
    atomically, same as a CAPTURE_FAILED)."""

    def test_no_trace_no_model_gives_clear_remediation(self):
        result = orchestrator.run(allow_dirty=True)  # model_path=None
        self.assertEqual(result["outcome"], trace_source.TRACE_SOURCE_MISSING)
        self.assertIn("--model PATH", result["message"])

    def test_invalid_model_path_blocks_atomically_no_replay(self):
        self._patch_capture(succeed=True)  # real capture() would itself say MODEL_NOT_FOUND
        result = orchestrator.run(allow_dirty=True, model_path=str(self.tmp / "nope.gguf"))
        self.assertEqual(result["outcome"], orchestrator.OUTCOME_CAPTURE_PREREQUISITE_FAILED)
        self.assertEqual(result["capture_result"]["outcome"], "MODEL_NOT_FOUND")
        self.assertNotIn("reasons", result)


class EstablishedSemanticsUnchangedTests(_EvidenceRootCase):
    """Items 15, 16, 18: dirty-tree gate, --allow-dirty -> NON-CANDIDATE,
    and the standalone `capture` command all keep working exactly as
    before, independent of the new auto-capture path in `run`."""

    def test_dirty_tree_without_allow_dirty_still_refused(self):
        self._clean_patch.stop()  # override setUp's clean=True for this one test
        p = mock.patch("argine.research2.runner.git_is_clean", return_value=False)
        p.start()
        self.addCleanup(p.stop)
        result = runner.run_experiment("R2-01", allow_dirty=False, model_path=str(self.model))
        self.assertEqual(result["outcome"], "REFUSED_DIRTY_TREE")

    def test_allow_dirty_auto_capture_run_is_non_candidate(self):
        self._patch_capture(succeed=True)
        result = orchestrator.run(allow_dirty=True, model_path=str(self.model))
        self.assertEqual(result["candidate_status"], "NON-CANDIDATE")

    def test_standalone_capture_command_still_works_independently(self):
        result = runner.capture_experiment("R2-01", allow_dirty=True,
                                           model_path=str(self.model), dry_run=True)
        self.assertEqual(result["outcome"], r2_01_capture.OUTCOME_DRY_RUN)


class CliModelArgumentTests(_EvidenceRootCase):
    """Section 3: `research2 run R2-01 --model PATH` is wired through with
    the same --model semantics as `capture`, never a second contract."""

    def test_run_cli_accepts_model_flag(self):
        import argine.cli as cli
        rc = cli.main(["research2", "run", "R2-01", "--model", str(self.tmp / "nope.gguf"),
                      "--allow-dirty"])
        self.assertNotEqual(rc, 0)  # MODEL_NOT_FOUND -> atomic blocker -> non-zero


class SafeDemonstrationTests(_EvidenceRootCase):
    """Section 13's three safe demonstrations, as committed regression
    tests: A (registered source, one `run`, no capture), B (no source,
    one `run --model`, mocked capture succeeds, continues automatically),
    C (capture failure, one `run --model`, non-zero, no replay)."""

    def test_demo_a_registered_source_one_run_no_capture(self):
        src = self.tmp / "demo-a.jsonl.gz"
        write_trace(make_trace(), src)
        reg = trace_source.register_source(path=src, source_experiment_id="R2-01",
                                           source_run_id="demo-a", source_type="fresh_capture")
        store.set_trace_source_registration("R2-01", reg.to_json())
        capture_mock = self._patch_capture(succeed=False)
        result = orchestrator.run(allow_dirty=True)
        capture_mock.assert_not_called()
        self.assertIn(result["outcome"], ("PASS", "INCONCLUSIVE", "FAIL"))

    def test_demo_b_no_source_model_given_capture_succeeds_continues(self):
        self._patch_capture(succeed=True)
        result = orchestrator.run(allow_dirty=True, model_path=str(self.model))
        self.assertIn(result["outcome"], ("PASS", "INCONCLUSIVE", "FAIL"))

    def test_demo_c_capture_failure_nonzero_no_replay(self):
        self._patch_capture(succeed=False)
        import argine.cli as cli
        rc = cli.main(["research2", "run", "R2-01", "--model", str(self.model),
                      "--allow-dirty"])
        self.assertNotEqual(rc, 0)


if __name__ == "__main__":
    unittest.main()
