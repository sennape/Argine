"""Focused tests for R2-08 prerequisite / R2-07 canonical binding integration.

Reproduces the R2-08 integration bug where R2-08 incorrectly read
`r2_07_run_id` from the R2-07 *upstream-chain* binding (R207Binding), which
describes prerequisites required by R2-07 and does not carry the R2-07 PASS
run identity itself. R2-08 must independently verify and bind the canonical
R2-07 PASS result.

Synthetic fixtures only (temp evidence root, injectable upstream binding).
"""

from __future__ import annotations

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

from argine.research2 import store  # noqa: E402
from argine.research2.r2_01 import trace_contract  # noqa: E402
from argine.research2.r2_07 import prerequisite as r2_07_prerequisite  # noqa: E402
from argine.research2.r2_08 import prerequisite  # noqa: E402


CANONICAL_R2_07_RUN_ID = "af28e8c6dc5044a18e4a86137d59156a"
CANONICAL_R2_07_DIGEST = "ad36e615970b196b64ba3a3fdc2559e252cc8627e5d17d0d830d16742f59281a"


class TempEvidenceMixin(unittest.TestCase):
    def setUp(self):
        super().setUp()
        self.tmp = Path(tempfile.mkdtemp(prefix="r2-08-prereq-"))
        self.addCleanup(lambda: shutil.rmtree(self.tmp, ignore_errors=True))
        self._env_patch = mock.patch.dict(os.environ, {"ARGINE3_RESEARCH_ROOT": str(self.tmp)})
        self._env_patch.start()
        self.addCleanup(self._env_patch.stop)

    def make_trace_file(self, content: bytes = b"canonical-r2-01-trace\n") -> tuple[Path, str]:
        path = self.tmp / "reused-r2-01" / "trace.jsonl.gz"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(content)
        return path, trace_contract.sha256_file(path)["sha256"]

    def register_r2_07_pass(self, *, run_id: str = CANONICAL_R2_07_RUN_ID,
                            digest: str = CANONICAL_R2_07_DIGEST,
                            candidate_status: str = "CANDIDATE",
                            r2_01_run_id: str = "r2-01-test",
                            trace_path: str = "/tmp/t.jsonl.gz",
                            trace_sha256: str = "abc",
                            outcome: str = "PASS",
                            end_utc: str = "2026-06-01T00:00:00Z",
                            omit: tuple[str, ...] = (),
                            malformed: dict[str, str] | None = None,
                            digest_summary: str | None = None,
                            digest_prov: str | None = None) -> Path:
        run_dir = store.run_dir("R2-07", run_id)
        run_dir.mkdir(parents=True, exist_ok=True)
        summary = {
            "experiment_id": "R2-07",
            "run_id": run_id,
            "outcome": outcome,
            "canonical_digest": digest if digest_summary is None else digest_summary,
            "candidate_status": candidate_status,
        }
        prov = {
            "canonical_digest": digest if digest_prov is None else digest_prov,
            "binding": {
                "r2_01_run_id": r2_01_run_id,
                "trace_path": trace_path,
                "trace_sha256": trace_sha256,
                "candidate_status": candidate_status,
            },
        }
        manifest = {"git": {"candidate_status": candidate_status}}
        files = {"summary.json": summary, "provenance.json": prov, "manifest.json": manifest}
        if malformed:
            for name, text in malformed.items():
                (run_dir / name).write_text(text, encoding="utf-8")
                files.pop(name, None)
        for name, obj in files.items():
            if name not in omit:
                (run_dir / name).write_text(json.dumps(obj), encoding="utf-8")
        store.append_registry_entry({
            "identity": {"experiment_id": "R2-07", "run_id": run_id,
                         "outcome": outcome, "end_utc": end_utc},
            "run_dir": str(run_dir),
        })
        return run_dir

    def upstream_binding(self, trace_path: Path, trace_sha256: str, r2_01_run_id: str):
        """A real R207Binding (upstream chain for R2-07) -- has no r2_07_run_id."""
        return r2_07_prerequisite.R207Binding(
            status=r2_07_prerequisite.STATUS_OK,
            message="upstream chain ok",
            r2_01_run_id=r2_01_run_id,
            r2_04_run_id="r2-04-pass",
            r2_05_run_id="r2-05-pass",
            r2_06_run_id="r2-06-pass",
            trace_path=str(trace_path),
            trace_sha256=trace_sha256,
            candidate_status="CANDIDATE",
        )


class R208R207PrerequisiteRegressionTests(TempEvidenceMixin):
    def test_valid_chain_binds_canonical_r2_07_without_attribute_error(self):
        """Regression for R2-08 integration bug (AttributeError on upstream.r2_07_run_id).

        Valid R2-01→R2-07 chain: R2-07 upstream binding is OK, canonical R2-07
        PASS evidence exists; R2-08 returns the canonical R2-07 run ID/digest.
        """
        trace_path, trace_sha = self.make_trace_file()
        reused_r2_01 = "r2-01-canonical"
        upstream = self.upstream_binding(trace_path, trace_sha, reused_r2_01)
        self.register_r2_07_pass(r2_01_run_id=reused_r2_01,
                                 trace_path=str(trace_path), trace_sha256=trace_sha)

        with mock.patch.object(r2_07_prerequisite, "require_full_chain_pass", return_value=upstream), \
             mock.patch.object(prerequisite, "REUSED_R2_01_RUN_ID", reused_r2_01), \
             mock.patch.object(prerequisite, "REUSED_TRACE_SHA256", trace_sha):
            binding = prerequisite.require_full_chain_pass()

        self.assertTrue(binding.ok, binding.message)
        self.assertEqual(binding.r2_07_run_id, CANONICAL_R2_07_RUN_ID)
        self.assertEqual(binding.r2_01_run_id, reused_r2_01)
        self.assertEqual(binding.r2_06_run_id, "r2-06-pass")
        self.assertEqual(binding.reused_trace_sha256, trace_sha)
        self.assertIn(CANONICAL_R2_07_DIGEST, binding.message)

    def test_r2_07_missing_fails_closed(self):
        trace_path, trace_sha = self.make_trace_file()
        reused_r2_01 = "r2-01-canonical"
        upstream = self.upstream_binding(trace_path, trace_sha, reused_r2_01)

        with mock.patch.object(r2_07_prerequisite, "require_full_chain_pass", return_value=upstream), \
             mock.patch.object(prerequisite, "REUSED_R2_01_RUN_ID", reused_r2_01), \
             mock.patch.object(prerequisite, "REUSED_TRACE_SHA256", trace_sha):
            binding = prerequisite.require_full_chain_pass()

        self.assertFalse(binding.ok)
        self.assertEqual(binding.status, prerequisite.STATUS_NO_R2_07_RUN)

    def test_r2_07_not_pass_fails_closed(self):
        trace_path, trace_sha = self.make_trace_file()
        reused_r2_01 = "r2-01-canonical"
        upstream = self.upstream_binding(trace_path, trace_sha, reused_r2_01)
        self.register_r2_07_pass(run_id="not-pass-run", digest="x", outcome="INCONCLUSIVE",
                                 r2_01_run_id=reused_r2_01, trace_path=str(trace_path),
                                 trace_sha256=trace_sha)

        with mock.patch.object(r2_07_prerequisite, "require_full_chain_pass", return_value=upstream), \
             mock.patch.object(prerequisite, "REUSED_R2_01_RUN_ID", reused_r2_01), \
             mock.patch.object(prerequisite, "REUSED_TRACE_SHA256", trace_sha):
            binding = prerequisite.require_full_chain_pass()

        self.assertFalse(binding.ok)
        self.assertEqual(binding.status, prerequisite.STATUS_R2_07_NOT_PASS)

    def test_r2_07_digest_mismatch_fails_closed(self):
        trace_path, trace_sha = self.make_trace_file()
        reused_r2_01 = "r2-01-canonical"
        upstream = self.upstream_binding(trace_path, trace_sha, reused_r2_01)
        self.register_r2_07_pass(digest="0" * 64, r2_01_run_id=reused_r2_01,
                                 trace_path=str(trace_path), trace_sha256=trace_sha)

        with mock.patch.object(r2_07_prerequisite, "require_full_chain_pass", return_value=upstream), \
             mock.patch.object(prerequisite, "REUSED_R2_01_RUN_ID", reused_r2_01), \
             mock.patch.object(prerequisite, "REUSED_TRACE_SHA256", trace_sha):
            binding = prerequisite.require_full_chain_pass()

        self.assertFalse(binding.ok)
        self.assertEqual(binding.status, prerequisite.STATUS_R2_07_BINDING_MISMATCH)
        self.assertEqual(binding.r2_07_run_id, CANONICAL_R2_07_RUN_ID)

    def test_r2_07_run_id_mismatch_fails_closed(self):
        trace_path, trace_sha = self.make_trace_file()
        reused_r2_01 = "r2-01-canonical"
        upstream = self.upstream_binding(trace_path, trace_sha, reused_r2_01)
        self.register_r2_07_pass(run_id="wrong-run-id", r2_01_run_id=reused_r2_01,
                                 trace_path=str(trace_path), trace_sha256=trace_sha)

        with mock.patch.object(r2_07_prerequisite, "require_full_chain_pass", return_value=upstream), \
             mock.patch.object(prerequisite, "REUSED_R2_01_RUN_ID", reused_r2_01), \
             mock.patch.object(prerequisite, "REUSED_TRACE_SHA256", trace_sha):
            binding = prerequisite.require_full_chain_pass()

        self.assertFalse(binding.ok)
        self.assertEqual(binding.status, prerequisite.STATUS_R2_07_BINDING_MISMATCH)

    def test_r2_07_trace_identity_differs_fails_closed(self):
        trace_path, trace_sha = self.make_trace_file()
        reused_r2_01 = "r2-01-canonical"
        upstream = self.upstream_binding(trace_path, trace_sha, reused_r2_01)
        self.register_r2_07_pass(r2_01_run_id="different-r2-01",
                                 trace_path=str(trace_path), trace_sha256=trace_sha)

        with mock.patch.object(r2_07_prerequisite, "require_full_chain_pass", return_value=upstream), \
             mock.patch.object(prerequisite, "REUSED_R2_01_RUN_ID", reused_r2_01), \
             mock.patch.object(prerequisite, "REUSED_TRACE_SHA256", trace_sha):
            binding = prerequisite.require_full_chain_pass()

        self.assertFalse(binding.ok)
        self.assertEqual(binding.status, prerequisite.STATUS_R2_07_BINDING_MISMATCH)

    def test_r2_07_malformed_evidence_fails_closed(self):
        trace_path, trace_sha = self.make_trace_file()
        reused_r2_01 = "r2-01-canonical"
        upstream = self.upstream_binding(trace_path, trace_sha, reused_r2_01)
        self.register_r2_07_pass(r2_01_run_id=reused_r2_01, trace_path=str(trace_path),
                                 trace_sha256=trace_sha,
                                 malformed={"provenance.json": "{not-json"})

        with mock.patch.object(r2_07_prerequisite, "require_full_chain_pass", return_value=upstream), \
             mock.patch.object(prerequisite, "REUSED_R2_01_RUN_ID", reused_r2_01), \
             mock.patch.object(prerequisite, "REUSED_TRACE_SHA256", trace_sha):
            binding = prerequisite.require_full_chain_pass()

        self.assertFalse(binding.ok)
        self.assertEqual(binding.status, prerequisite.STATUS_R2_07_EVIDENCE_MISSING)

    def test_r2_07_candidate_status_not_canonical_fails_closed(self):
        trace_path, trace_sha = self.make_trace_file()
        reused_r2_01 = "r2-01-canonical"
        upstream = self.upstream_binding(trace_path, trace_sha, reused_r2_01)
        self.register_r2_07_pass(candidate_status="NON_CANDIDATE", r2_01_run_id=reused_r2_01,
                                 trace_path=str(trace_path), trace_sha256=trace_sha)

        with mock.patch.object(r2_07_prerequisite, "require_full_chain_pass", return_value=upstream), \
             mock.patch.object(prerequisite, "REUSED_R2_01_RUN_ID", reused_r2_01), \
             mock.patch.object(prerequisite, "REUSED_TRACE_SHA256", trace_sha):
            binding = prerequisite.require_full_chain_pass()

        self.assertFalse(binding.ok)
        self.assertEqual(binding.status, prerequisite.STATUS_R2_07_BINDING_MISMATCH)


if __name__ == "__main__":
    unittest.main()
