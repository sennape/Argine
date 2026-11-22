"""Regression tests for R2-08 capture runtime resolution continuation and runtime
identity (R2-08 integration fixes).

Reproduces the observed integration failure where runtime_resolver returned
RUNTIME_RESOLVED but r2_08.capture.execute_capture returned it as the final
capture outcome (failing to continue into capture). Also covers runtime SHA
identity binding for canonical reused/new cells.

No real llama-server, no real 30B model, no network.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT))

from argine.research2 import runtime_resolver, store  # noqa: E402
from argine.research2.r2_08 import capture, orchestrator, prerequisite  # noqa: E402

CANONICAL_RUNTIME = r"C:\Users\alpel\Desktop\Argine2\llama-research-v2\llama-server.exe"
CANONICAL_RUNTIME_SHA = runtime_resolver.DOCUMENTED_CANONICAL_RUNTIME["expected_sha256"]


class TempEvidenceMixin(unittest.TestCase):
    def setUp(self):
        super().setUp()
        self.tmp = Path(tempfile.mkdtemp(prefix="r2-08-capture-rt-"))
        self.addCleanup(lambda: shutil.rmtree(self.tmp, ignore_errors=True))
        self._env_patch = mock.patch.dict(os.environ, {"ARGINE3_RESEARCH_ROOT": str(self.tmp)})
        self._env_patch.start()
        self.addCleanup(self._env_patch.stop)
        self.model = self.tmp / "fake.gguf"
        self.model.write_bytes(b"fake-model")


class ExecuteCaptureRuntimeContinuationTests(TempEvidenceMixin):
    """Primary issue: RUNTIME_RESOLVED is a successful resolver state that must
    continue into capture; it must never be the final capture outcome."""

    def _resolved(self, tier="registered", path=CANONICAL_RUNTIME):
        return runtime_resolver.RuntimeResolution(
            status=runtime_resolver.RUNTIME_RESOLVED,
            resolved_path=path,
            tier=tier,
            message=f"using runtime: {path}",
        )

    def test_runtime_resolved_continues_into_capture_not_terminal(self):
        # Exact integration shape: explicit runtime absent, resolver returns
        # RUNTIME_RESOLVED via registered runtime; subprocess mocked.
        store.set_runtime_registration({
            "path": CANONICAL_RUNTIME,
            "sha256": CANONICAL_RUNTIME_SHA,
            "capability_status": "expert-store marker present",
            "resolution_source": "registered",
        })
        seen = {}

        def fake_run(cmd, capture_output=True, text=True, env=None):
            seen["cmd"] = cmd
            seen["env"] = dict(env or {})
            out_dir = Path(cmd[cmd.index("--out") + 1])
            tp = capture.expected_trace_path(out_dir)
            tp.parent.mkdir(parents=True, exist_ok=True)
            tp.write_text("not-a-real-trace", encoding="utf-8")
            return mock.Mock(returncode=0, stdout="ok", stderr="")

        with mock.patch.object(runtime_resolver, "resolve", return_value=self._resolved()), \
             mock.patch("subprocess.run", side_effect=fake_run), \
             mock.patch.object(capture, "validate_trace_file",
                               return_value={"ok": True, "trace_sha256": "abc123", "size_bytes": 1,
                                             "trace_meta": {}}):
            res = capture.execute_capture(model_path=str(self.model), workload_id="coding-v1",
                                          seed=43, out_dir=self.tmp / "out")

        self.assertEqual(res["outcome"], "CAPTURED", res)
        self.assertNotEqual(res["outcome"], "RUNTIME_RESOLVED")
        self.assertIn("cmd", seen, "capture must continue into subprocess execution")
        self.assertEqual(seen["env"].get("ARGINE_LLAMA_DIR"), str(Path(CANONICAL_RUNTIME).parent))
        self.assertEqual(res["runtime_sha256"], CANONICAL_RUNTIME_SHA)
        self.assertEqual(res["runtime_resolution"]["status"], "RUNTIME_RESOLVED")
        self.assertEqual(res["runtime_resolution"]["resolved_path"], CANONICAL_RUNTIME)

    def test_runtime_resolved_dry_run_reports_resolved_not_terminal_failure(self):
        store.set_runtime_registration({
            "path": CANONICAL_RUNTIME, "sha256": CANONICAL_RUNTIME_SHA,
            "capability_status": "ok", "resolution_source": "registered",
        })
        with mock.patch.object(runtime_resolver, "resolve", return_value=self._resolved()):
            res = capture.execute_capture(model_path=str(self.model), workload_id="coding-v1",
                                          seed=43, out_dir=self.tmp / "out", dry_run=True)
        self.assertEqual(res["outcome"], "DRY_RUN")
        self.assertIn("command", res)
        self.assertEqual(res["runtime_resolution"]["status"], "RUNTIME_RESOLVED")

    def test_runtime_not_found_remains_terminal(self):
        rr = runtime_resolver.RuntimeResolution(status=runtime_resolver.RUNTIME_NOT_FOUND,
                                                message="no runtime")
        with mock.patch.object(runtime_resolver, "resolve", return_value=rr), \
             mock.patch("subprocess.run") as m:
            res = capture.execute_capture(model_path=str(self.model), workload_id="coding-v1",
                                          seed=43, out_dir=self.tmp / "out")
        self.assertEqual(res["outcome"], "RUNTIME_NOT_FOUND")
        m.assert_not_called()

    def test_runtime_ambiguous_remains_terminal(self):
        rr = runtime_resolver.RuntimeResolution(status=runtime_resolver.RUNTIME_AMBIGUOUS,
                                                message="ambiguous")
        with mock.patch.object(runtime_resolver, "resolve", return_value=rr), \
             mock.patch("subprocess.run") as m:
            res = capture.execute_capture(model_path=str(self.model), workload_id="coding-v1",
                                          seed=43, out_dir=self.tmp / "out")
        self.assertEqual(res["outcome"], "RUNTIME_AMBIGUOUS")
        m.assert_not_called()

    def test_runtime_without_verified_sha_fails_closed_before_launch(self):
        rr = self._resolved(tier="cli_explicit", path=str(self.tmp / "rt.exe"))
        (self.tmp / "rt.exe").write_bytes(b"x")
        with mock.patch.object(runtime_resolver, "resolve", return_value=rr), \
             mock.patch.object(capture, "_verified_runtime_sha256",
                               return_value=(None, None, "no sha")), \
             mock.patch("subprocess.run") as m:
            res = capture.execute_capture(model_path=str(self.model), workload_id="coding-v1",
                                          seed=43, out_dir=self.tmp / "out")
        self.assertEqual(res["outcome"], capture.OUTCOME_RUNTIME_IDENTITY_UNVERIFIED)
        m.assert_not_called()


class RuntimeShaReuseTests(TempEvidenceMixin):
    def _write_canonical_r2_01_capture_manifest(self):
        cap = self.tmp / "R2-01" / "captures" / prerequisite.CANONICAL_REUSED_CAPTURE_SOURCE
        cap.mkdir(parents=True, exist_ok=True)
        (cap / "manifest.json").write_text(json.dumps({
            "runtime_resolution": {
                "status": "RUNTIME_RESOLVED",
                "resolved_path": CANONICAL_RUNTIME,
                "tier": "documented_canonical",
                "message": "ok",
            }
        }), encoding="utf-8")

    def test_reused_r2_01_runtime_sha_bound_from_canonical_provenance(self):
        self._write_canonical_r2_01_capture_manifest()
        store.set_runtime_registration({
            "path": CANONICAL_RUNTIME, "sha256": CANONICAL_RUNTIME_SHA,
            "capability_status": "ok", "resolution_source": "documented_canonical",
        })
        sha, err = capture.runtime_sha256_from_reused_r2_01_capture()
        self.assertIsNone(err)
        self.assertEqual(sha, CANONICAL_RUNTIME_SHA)

    def test_reused_r2_01_runtime_sha_fails_closed_without_provenance(self):
        # canonical reuse binding but no capture manifest / no verifiable identity
        sha, err = capture.runtime_sha256_from_reused_r2_01_capture()
        self.assertIsNone(sha)
        self.assertTrue(err)

    def test_canonical_reuse_requires_verified_runtime_sha(self):
        binding = prerequisite.R208Binding(
            status=prerequisite.STATUS_OK, message="ok",
            r2_01_run_id=prerequisite.CANONICAL_REUSED_R2_01_RUN_ID,
            r2_04_run_id="r204", r2_05_run_id="r205", r2_06_run_id="r206", r2_07_run_id="r207",
            reused_trace_path=str(self.tmp / "missing-trace.jsonl.gz"),
            reused_trace_sha256=prerequisite.CANONICAL_REUSED_TRACE_SHA256,
            reused_capture_source=prerequisite.CANONICAL_REUSED_CAPTURE_SOURCE, candidate_status="CANDIDATE")
        # no canonical capture manifest / registration -> must fail closed
        with mock.patch.object(prerequisite, "require_full_chain_pass", return_value=binding):
            res = orchestrator.run(allow_dirty=True)
        self.assertEqual(res["outcome"], orchestrator.OUTCOME_FAIL)
        self.assertIn("runtime", res.get("message", "").lower())

    def test_reused_and_new_captures_share_verified_runtime_identity(self):
        # Canonical provenance establishes runtime SHA for reused cell; new captures
        # must bind the same verified runtime identity before scientific analysis.
        import gzip, hashlib
        from argine.research.trace import Step, Trace, TraceMeta

        def write_trace(trace, path):
            path.parent.mkdir(parents=True, exist_ok=True)
            with gzip.open(path, "wt", encoding="utf-8") as fh:
                fh.write(json.dumps({"format": "argine-router-trace", "version": 1,
                                     "meta": trace.meta.to_json()}) + "\n")
                for st in trace.steps:
                    fh.write(json.dumps(st.to_json()) + "\n")
            return hashlib.sha256(path.read_bytes()).hexdigest()

        tr = Trace(meta=TraceMeta(model="m", n_experts=128, top_k=2, layers=[0], workload="coding-v1"),
                   steps=[Step(graph=i, n_tokens=1, phase="decode", segment="coding-v1",
                               routes={0: [(i % 8, (i + 1) % 8)]}) for i in range(300)])
        reused_tp = self.tmp / "reused" / "trace.jsonl.gz"
        reused_sha = write_trace(tr, reused_tp)

        cap = self.tmp / "R2-01" / "captures" / prerequisite.CANONICAL_REUSED_CAPTURE_SOURCE
        cap.mkdir(parents=True, exist_ok=True)
        (cap / "manifest.json").write_text(json.dumps({
            "runtime_resolution": {"status": "RUNTIME_RESOLVED", "resolved_path": CANONICAL_RUNTIME,
                                   "tier": "documented_canonical", "message": "ok"}
        }), encoding="utf-8")
        store.set_runtime_registration({"path": CANONICAL_RUNTIME, "sha256": CANONICAL_RUNTIME_SHA,
                                        "capability_status": "ok", "resolution_source": "documented_canonical"})

        binding = prerequisite.R208Binding(
            status=prerequisite.STATUS_OK, message="ok",
            r2_01_run_id=prerequisite.CANONICAL_REUSED_R2_01_RUN_ID,
            r2_04_run_id="r204", r2_05_run_id="r205", r2_06_run_id="r206", r2_07_run_id="r207",
            reused_trace_path=str(reused_tp), reused_trace_sha256=reused_sha,
            reused_capture_source=prerequisite.CANONICAL_REUSED_CAPTURE_SOURCE, candidate_status="CANDIDATE")

        up = {"run_id": "u", "canonical_digest": "d" * 64, "headline": {}}

        def fake_capture(model_path, workload_id, seed, out_dir, allow_dirty=False, runtime_path=None, dry_run=False, **kw):
            tp = Path(out_dir) / "run-00-4G" / "trace.jsonl.gz"
            sha = write_trace(tr, tp)
            return {"outcome": "CAPTURED", "trace_path": str(tp),
                    "validation": {"ok": True, "trace_sha256": sha, "size_bytes": tp.stat().st_size,
                                   "trace_meta": {}},
                    "out_dir": str(out_dir), "run_id": f"cap-{workload_id}-{seed}",
                    "runtime_sha256": CANONICAL_RUNTIME_SHA}

        with mock.patch.object(prerequisite, "require_full_chain_pass", return_value=binding), \
             mock.patch.object(prerequisite, "REUSED_TRACE_SHA256", reused_sha), \
             mock.patch("argine.research2.r2_08.bridges.load_upstream_digest", return_value=up):
            res = orchestrator.run(allow_dirty=True, model_path=str(self.model), capture_fn=fake_capture)

        self.assertEqual(res["outcome"], orchestrator.OUTCOME_PASS, res.get("message"))
        cells = res["capture_matrix"]["cells"]
        runtime_shas = {v["runtime_sha256"] for v in cells.values()}
        self.assertEqual(runtime_shas, {CANONICAL_RUNTIME_SHA})
        self.assertTrue(all(v["runtime_sha256"] != "runtime-unresolved:canonical-r2-protocol"
                            for v in cells.values()))

    def test_capture_runtime_sha_mismatch_fails_closed(self):
        import gzip, hashlib
        from argine.research.trace import Step, Trace, TraceMeta

        def write_trace(trace, path):
            path.parent.mkdir(parents=True, exist_ok=True)
            with gzip.open(path, "wt", encoding="utf-8") as fh:
                fh.write(json.dumps({"format": "argine-router-trace", "version": 1,
                                     "meta": trace.meta.to_json()}) + "\n")
                for st in trace.steps:
                    fh.write(json.dumps(st.to_json()) + "\n")
            return hashlib.sha256(path.read_bytes()).hexdigest()

        tr = Trace(meta=TraceMeta(model="m", n_experts=128, top_k=2, layers=[0], workload="coding-v1"),
                   steps=[Step(graph=i, n_tokens=1, phase="decode", segment="coding-v1",
                               routes={0: [(i % 8, (i + 1) % 8)]}) for i in range(300)])
        reused_tp = self.tmp / "reused" / "trace.jsonl.gz"
        reused_sha = write_trace(tr, reused_tp)
        cap = self.tmp / "R2-01" / "captures" / prerequisite.CANONICAL_REUSED_CAPTURE_SOURCE
        cap.mkdir(parents=True, exist_ok=True)
        (cap / "manifest.json").write_text(json.dumps({
            "runtime_resolution": {"status": "RUNTIME_RESOLVED", "resolved_path": CANONICAL_RUNTIME,
                                   "tier": "documented_canonical", "message": "ok"}
        }), encoding="utf-8")
        store.set_runtime_registration({"path": CANONICAL_RUNTIME, "sha256": CANONICAL_RUNTIME_SHA,
                                        "capability_status": "ok", "resolution_source": "documented_canonical"})
        binding = prerequisite.R208Binding(
            status=prerequisite.STATUS_OK, message="ok",
            r2_01_run_id=prerequisite.CANONICAL_REUSED_R2_01_RUN_ID,
            r2_04_run_id="r204", r2_05_run_id="r205", r2_06_run_id="r206", r2_07_run_id="r207",
            reused_trace_path=str(reused_tp), reused_trace_sha256=reused_sha,
            reused_capture_source=prerequisite.CANONICAL_REUSED_CAPTURE_SOURCE, candidate_status="CANDIDATE")
        up = {"run_id": "u", "canonical_digest": "d" * 64, "headline": {}}

        def bad_capture(model_path, workload_id, seed, out_dir, **kw):
            tp = Path(out_dir) / "run-00-4G" / "trace.jsonl.gz"
            sha = write_trace(tr, tp)
            return {"outcome": "CAPTURED", "trace_path": str(tp),
                    "validation": {"ok": True, "trace_sha256": sha, "size_bytes": tp.stat().st_size, "trace_meta": {}},
                    "out_dir": str(out_dir), "runtime_sha256": "0" * 64}

        with mock.patch.object(prerequisite, "require_full_chain_pass", return_value=binding), \
             mock.patch.object(prerequisite, "REUSED_TRACE_SHA256", reused_sha), \
             mock.patch("argine.research2.r2_08.bridges.load_upstream_digest", return_value=up):
            res = orchestrator.run(allow_dirty=True, model_path=str(self.model), capture_fn=bad_capture)
        self.assertEqual(res["outcome"], orchestrator.OUTCOME_FAIL)
        self.assertIn("runtime SHA mismatch", res.get("message", ""))


if __name__ == "__main__":
    unittest.main()
