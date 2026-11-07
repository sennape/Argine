"""Tests for the Research Part 2 infrastructure (R2-M0.1).

Execution: python -m unittest tests.test_research2 -v   (from repo root)

Must NOT: start the real runtime, load/hash the real 30B model, require
internet, or write into the user's real LOCALAPPDATA research evidence
directory. Every test uses a temporary evidence root via
ARGINE3_RESEARCH_ROOT and/or a temporary clone of this repository.
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

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT))

from argine.research2 import provenance, roadmap, runner, state, store  # noqa: E402


class TempEvidenceRoot:
    """Context manager: point ARGINE3_RESEARCH_ROOT at a fresh temp dir and
    restore the previous value on exit."""

    def __enter__(self):
        self._old = os.environ.get("ARGINE3_RESEARCH_ROOT")
        self._dir = tempfile.mkdtemp(prefix="argine3-research-test-")
        os.environ["ARGINE3_RESEARCH_ROOT"] = self._dir
        return Path(self._dir)

    def __exit__(self, *exc):
        if self._old is None:
            os.environ.pop("ARGINE3_RESEARCH_ROOT", None)
        else:
            os.environ["ARGINE3_RESEARCH_ROOT"] = self._old
        shutil.rmtree(self._dir, ignore_errors=True)


class RoadmapTests(unittest.TestCase):
    def test_parses_and_validates_clean(self):
        rm = roadmap.load()
        self.assertEqual(rm.validate(), [])

    def test_all_r2_ids_exist_exactly_once(self):
        rm = roadmap.load()
        ids = sorted(rm.experiments.keys())
        expected = sorted(f"R2-{i:02d}" for i in range(1, 13))
        self.assertEqual(ids, expected)
        # exactly once across phase 'experiments' lists too
        from collections import Counter
        counts = Counter()
        for phase in rm.phases:
            for eid in phase.get("experiments", []):
                counts[eid] += 1
        for eid in expected:
            self.assertEqual(counts[eid], 1, f"{eid} should be listed exactly once")

    def test_phase_ordering_II_A_then_B_then_C(self):
        rm = roadmap.load()
        self.assertEqual(rm.phase_order, ["II-A", "II-B", "II-C"])
        self.assertEqual(rm.validate(), [])

    def test_dependency_ordering_is_acyclic_and_chains_phases(self):
        rm = roadmap.load()
        self.assertEqual(rm._check_dependency_acyclic_and_consistent(), [])
        self.assertEqual(rm._check_phase_ordering(), [])

    def test_rejects_duplicate_experiment_id(self):
        data = json.loads(roadmap.default_path().read_text())
        data["phases"][0]["experiments"].append("R2-01")  # duplicate within II-A
        with tempfile.NamedTemporaryFile("w", suffix=".json", delete=False) as f:
            json.dump(data, f)
            path = f.name
        try:
            rm = roadmap.load(path)
            problems = rm.validate()
            self.assertTrue(any("R2-01" in p and "both phase" in p for p in problems))
        finally:
            os.unlink(path)

    def test_rejects_missing_experiment_id(self):
        data = json.loads(roadmap.default_path().read_text())
        del data["experiments"]["R2-12"]
        data["phases"][2]["experiments"].remove("R2-12")
        with tempfile.NamedTemporaryFile("w", suffix=".json", delete=False) as f:
            json.dump(data, f)
            path = f.name
        try:
            rm = roadmap.load(path)
            problems = rm.validate()
            self.assertTrue(any("R2-01..R2-12" in p for p in problems))
        finally:
            os.unlink(path)

    def test_rejects_cyclic_dependency(self):
        data = json.loads(roadmap.default_path().read_text())
        data["experiments"]["R2-01"]["depends_on"] = ["R2-02"]  # R2-02 depends_on R2-01 already
        with tempfile.NamedTemporaryFile("w", suffix=".json", delete=False) as f:
            json.dump(data, f)
            path = f.name
        try:
            rm = roadmap.load(path)
            problems = rm._check_dependency_acyclic_and_consistent()
            self.assertTrue(any("cycle" in p for p in problems))
        finally:
            os.unlink(path)

    def test_malformed_json_raises(self):
        with tempfile.NamedTemporaryFile("w", suffix=".json", delete=False) as f:
            f.write("{not json")
            path = f.name
        try:
            with self.assertRaises(roadmap.RoadmapError):
                roadmap.load(path)
        finally:
            os.unlink(path)


class StateMachineTests(unittest.TestCase):
    def test_protocol_loads_and_validates(self):
        proto = state.load()
        self.assertEqual(proto.validate(), [])

    def test_valid_transitions_accepted(self):
        proto = state.load()
        for a, b in [("PLANNED", "IMPLEMENTED"), ("IMPLEMENTED", "READY"),
                    ("READY", "RUNNING"), ("RUNNING", "COMPLETED"),
                    ("RUNNING", "INCONCLUSIVE"), ("RUNNING", "FAILED"),
                    ("COMPLETED", "READY"), ("FAILED", "READY"),
                    ("INCONCLUSIVE", "READY")]:
            proto.validate_transition(a, b)  # must not raise

    def test_invalid_transitions_rejected(self):
        proto = state.load()
        for a, b in [("RUNNING", "PLANNED"), ("COMPLETED", "RUNNING"),
                    ("PLANNED", "RUNNING"), ("PLANNED", "COMPLETED"),
                    ("BLOCKED", "RUNNING")]:
            with self.assertRaises(state.TransitionError):
                proto.validate_transition(a, b)

    def test_unknown_state_rejected(self):
        proto = state.load()
        with self.assertRaises(state.TransitionError):
            proto.validate_transition("PLANNED", "NOT_A_STATE")
        with self.assertRaises(state.TransitionError):
            proto.validate_transition("NOT_A_STATE", "PLANNED")

    def test_noop_transition_rejected(self):
        proto = state.load()
        with self.assertRaises(state.TransitionError):
            proto.validate_transition("PLANNED", "PLANNED")


class EvidenceRootTests(unittest.TestCase):
    def test_env_override_used(self):
        with TempEvidenceRoot() as d:
            self.assertEqual(store.evidence_root(), d)

    def test_default_is_under_localappdata_on_windows(self):
        old_name, old_local = os.name, os.environ.get("LOCALAPPDATA")
        old_override = os.environ.pop("ARGINE3_RESEARCH_ROOT", None)
        try:
            os.environ["LOCALAPPDATA"] = str(Path(tempfile.gettempdir()) / "fake-local-appdata")
            # os.name is read-only at runtime in CPython; simulate via the
            # same branch by checking the non-Windows fallback instead when
            # not running on Windows, and the Windows branch when we are.
            if os.name == "nt":
                root = store.evidence_root()
                self.assertEqual(root, Path(os.environ["LOCALAPPDATA"]) / "Argine3" / "research")
            else:
                root = store.evidence_root()
                self.assertTrue(str(root).endswith(os.path.join("argine3", "research")))
        finally:
            if old_override is not None:
                os.environ["ARGINE3_RESEARCH_ROOT"] = old_override
            if old_local is not None:
                os.environ["LOCALAPPDATA"] = old_local
            else:
                os.environ.pop("LOCALAPPDATA", None)

    def test_writeable_check_cleans_up_marker(self):
        with TempEvidenceRoot() as d:
            store.ensure_root_writeable()
            self.assertTrue(d.exists())
            self.assertFalse((d / ".write-check").exists())

    def test_state_and_registry_round_trip_in_temp_root(self):
        with TempEvidenceRoot() as d:
            store.set_experiment_state("R2-01", "IMPLEMENTED")
            self.assertEqual(store.load_state()["R2-01"]["state"], "IMPLEMENTED")
            store.append_registry_entry({"identity": {"experiment_id": "R2-01"}})
            self.assertEqual(len(store.load_registry()), 1)
            # confirm it actually landed under the temp root, not the real one
            self.assertTrue((d / store.STATE_FILENAME).exists())
            self.assertTrue((d / store.REGISTRY_FILENAME).exists())


class ProvenanceTests(unittest.TestCase):
    def test_record_has_all_required_field_groups(self):
        rec = provenance.build_record("R2-01", "NOT_IMPLEMENTED")
        self.assertEqual(provenance.validate_record_shape(rec), [])
        for group in ("identity", "git", "software", "hardware", "artifacts",
                      "experiment", "execution"):
            self.assertIn(group, rec)

    def test_identity_git_software_hardware_fields_present(self):
        rec = provenance.build_record("R2-01", "NOT_IMPLEMENTED")
        self.assertIn("run_id", rec["identity"])
        self.assertIn("commit", rec["git"])
        self.assertIn("python_version", rec["software"])
        self.assertIn("cpu", rec["hardware"])
        self.assertIn("model", rec["artifacts"])
        self.assertIn("seed", rec["experiment"])
        self.assertIn("exit_code", rec["execution"])

    def test_candidate_status_matrix(self):
        self.assertEqual(provenance.candidate_status(False, False), "CANDIDATE")
        self.assertEqual(provenance.candidate_status(False, True), "CANDIDATE")
        self.assertEqual(provenance.candidate_status(True, False), "REFUSED")
        self.assertEqual(provenance.candidate_status(True, True), "NON-CANDIDATE")

    def test_serializes_to_json(self):
        rec = provenance.build_record("R2-01", "NOT_IMPLEMENTED")
        json.dumps(rec)  # must not raise


class _TempGitRepoCase(unittest.TestCase):
    """Base class: ONE disposable COPY of the current working tree (shared
    across this class's tests; copying the whole tree per-test is too slow)
    including any not-yet-committed changes, turned into its own clean
    commit, so dirty-tree tests never touch the real repository the task is
    bootstrapping and work the same whether or not R2-M0.1 has been
    committed yet in REPO_ROOT. Each test restores a clean working tree
    (via `git checkout -- .` + `git clean -fdq`) before and after it runs,
    so tests cannot leak dirt into one another."""

    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.mkdtemp(prefix="argine3-copy-")
        shutil.rmtree(cls.tmp)
        shutil.copytree(REPO_ROOT, cls.tmp)
        subprocess.run(["git", "-C", cls.tmp, "add", "-A"], check=True)
        subprocess.run(["git", "-C", cls.tmp, "-c", "user.email=test@example.invalid",
                       "-c", "user.name=test", "commit", "--quiet", "--allow-empty",
                       "-m", "test fixture: synthetic clean commit for isolated testing"],
                      check=True)

    @classmethod
    def tearDownClass(cls):
        shutil.rmtree(cls.tmp, ignore_errors=True)

    def setUp(self):
        subprocess.run(["git", "-C", self.tmp, "checkout", "--quiet", "--", "."], check=True)
        subprocess.run(["git", "-C", self.tmp, "clean", "-fdq"], check=True)

    def _run_in_shared(self, *args):
        env = dict(os.environ)
        env["ARGINE3_RESEARCH_ROOT"] = tempfile.mkdtemp(prefix="argine3-evidence-")
        self.addCleanup(lambda: shutil.rmtree(env["ARGINE3_RESEARCH_ROOT"], ignore_errors=True))
        return subprocess.run([sys.executable, "-m", "argine", "research2", *args],
                              cwd=self.tmp, env=env, capture_output=True, text=True, timeout=60)


class StatusDoctorAuditSubprocessTests(_TempGitRepoCase):
    def test_status_is_read_only_json_and_exits_ok(self):
        r = self._run_in_shared("status")
        self.assertEqual(r.returncode, 0, r.stderr)
        payload = json.loads(r.stdout)
        self.assertEqual(payload["working_tree"], "clean")
        self.assertEqual(len(payload["experiments"]), 12)

    def test_doctor_does_not_hash_or_start_runtime(self):
        r = self._run_in_shared("doctor")
        payload = json.loads(r.stdout)
        self.assertIn("checks", payload)
        self.assertNotIn("model_sha256", json.dumps(payload))

    def test_audit_passes_on_valid_bootstrap(self):
        r = self._run_in_shared("audit")
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertTrue(json.loads(r.stdout)["ok"])

    def test_unknown_experiment_id_rejected(self):
        r = self._run_in_shared("run", "R2-99")
        self.assertEqual(r.returncode, runner.EXIT_UNKNOWN_ID)

    def test_unimplemented_experiment_returns_not_implemented_no_evidence(self):
        env_root = tempfile.mkdtemp(prefix="argine3-evidence-")
        try:
            env = dict(os.environ)
            env["ARGINE3_RESEARCH_ROOT"] = env_root
            r = subprocess.run([sys.executable, "-m", "argine", "research2", "run", "R2-01"],
                               cwd=self.tmp, env=env, capture_output=True, text=True, timeout=60)
            payload = json.loads(r.stdout)
            self.assertEqual(payload["outcome"], "NOT_IMPLEMENTED")
            # no run directory / registry was created for a NOT_IMPLEMENTED result
            self.assertEqual(list(Path(env_root).rglob("*")), [])
        finally:
            shutil.rmtree(env_root, ignore_errors=True)

    def test_candidate_run_refuses_dirty_tree(self):
        (Path(self.tmp) / "README.md").write_text(
            Path(self.tmp, "README.md").read_text(encoding="utf-8") + "\ndirty\n", encoding="utf-8")
        r = self._run_in_shared("run", "R2-01")
        payload = json.loads(r.stdout)
        self.assertEqual(payload["outcome"], "REFUSED_DIRTY_TREE")
        self.assertEqual(r.returncode, runner.EXIT_DIRTY_REFUSED)

    def test_allow_dirty_marks_non_candidate(self):
        (Path(self.tmp) / "README.md").write_text(
            Path(self.tmp, "README.md").read_text(encoding="utf-8") + "\ndirty\n", encoding="utf-8")
        r = self._run_in_shared("run", "R2-01", "--allow-dirty")
        payload = json.loads(r.stdout)
        self.assertEqual(payload["candidate_status"], "NON-CANDIDATE")

    def test_analyse_unimplemented_returns_not_implemented(self):
        r = self._run_in_shared("analyse", "R2-01")
        payload = json.loads(r.stdout)
        self.assertEqual(payload["outcome"], "NOT_IMPLEMENTED")

    def test_next_identifies_r2_01_as_next(self):
        r = self._run_in_shared("next")
        payload = json.loads(r.stdout)
        self.assertEqual(payload["experiment_id"], "R2-01")
        self.assertFalse(payload["implemented"])

    def test_mutable_local_state_does_not_dirty_repository(self):
        r = self._run_in_shared("run", "R2-01", "--allow-dirty")
        self.assertEqual(r.returncode, runner.EXIT_NOT_IMPLEMENTED)
        status = subprocess.run(["git", "-C", self.tmp, "status", "--porcelain"],
                                capture_output=True, text=True, check=True)
        # the run touched no tracked file in the *original* repo state
        self.assertEqual(status.stdout.strip(), "")


class ExistingArgineTestsStillPass(unittest.TestCase):
    """R2-M0.1 must not break Research Part 1 behaviour it inherited."""

    def test_existing_research_command_family_still_registered(self):
        from argine.cli import build_parser
        ap = build_parser()
        # argparse does not expose subparser names directly; smoke-test via parse
        args = ap.parse_args(["research2", "status"])
        self.assertEqual(args.research2_command, "status")
        args = ap.parse_args(["research", "characterize", "x.jsonl", "--out", "/tmp/x"])
        self.assertEqual(args.research_command, "characterize")


if __name__ == "__main__":
    unittest.main()
