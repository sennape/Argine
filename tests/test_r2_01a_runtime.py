"""Tests for the R2-01A round-2 hotfix: deterministic llama.cpp runtime
resolution (argine.research2.runtime_resolver) and its wiring into
capture().

Isolated from the real host machine's Argine2/HoPi trees: every test that
exercises resolve()'s tiers monkeypatches runtime_resolver._repo_root() to
point at a throwaway sibling-root structure it builds itself, and clears
ARGINE_LLAMA_DIR, so results never depend on what happens to exist on the
machine actually running the suite.

Synthetic/fake binaries only: nothing here starts the real 30B model or a
real llama-server.
"""

from __future__ import annotations

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

from argine.research2 import runner, store  # noqa: E402
from argine.research2 import runtime_resolver  # noqa: E402
from argine.research2.r2_01 import capture as r2_01_capture  # noqa: E402

CAPABLE_BYTES = b"stock preamble ... ARGINE_EXPERT_STORE ... trailer"
INCAPABLE_BYTES = b"a perfectly ordinary, unpatched llama-server.exe"


class _IsolatedRootsCase(unittest.TestCase):
    """Builds a throwaway sibling-root tree (tmp/Argine3, tmp/Argine2, ...)
    and points runtime_resolver at it instead of the real host filesystem,
    so narrow-discovery/documented-canonical tests are fully deterministic."""

    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="r2-01a-runtime-"))
        self.addCleanup(lambda: shutil.rmtree(self.tmp, ignore_errors=True))
        self.fake_repo = self.tmp / "Argine3"
        self.fake_repo.mkdir()
        self._orig_repo_root = runtime_resolver._repo_root
        runtime_resolver._repo_root = lambda: self.fake_repo
        self.addCleanup(self._restore_repo_root)

        self._old_env = os.environ.get("ARGINE_LLAMA_DIR")
        os.environ.pop("ARGINE_LLAMA_DIR", None)
        self.addCleanup(self._restore_env)

        self._old_research_root = os.environ.get("ARGINE3_RESEARCH_ROOT")
        os.environ["ARGINE3_RESEARCH_ROOT"] = str(self.tmp / "evidence")
        self.addCleanup(self._restore_research_root)

    def _restore_repo_root(self):
        runtime_resolver._repo_root = self._orig_repo_root

    def _restore_env(self):
        if self._old_env is None:
            os.environ.pop("ARGINE_LLAMA_DIR", None)
        else:
            os.environ["ARGINE_LLAMA_DIR"] = self._old_env

    def _restore_research_root(self):
        if self._old_research_root is None:
            os.environ.pop("ARGINE3_RESEARCH_ROOT", None)
        else:
            os.environ["ARGINE3_RESEARCH_ROOT"] = self._old_research_root

    def _write_binary(self, rel_path: str, content: bytes) -> Path:
        p = self.tmp / rel_path
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_bytes(content)
        return p


class EnvironmentVariableTests(_IsolatedRootsCase):
    """Items 1-2: stale ARGINE_LLAMA_DIR detected; a stale env var does not
    qualify as a runtime (resolution continues past it)."""

    def test_stale_env_var_detected_and_does_not_qualify(self):
        os.environ["ARGINE_LLAMA_DIR"] = str(self.tmp / "does-not-exist")
        res = runtime_resolver.resolve()
        self.assertIsNotNone(res.stale_env_note)
        self.assertIn("stale/invalid", res.stale_env_note)
        self.assertNotEqual(res.tier, "argine_llama_dir")

    def test_env_var_pointing_at_incapable_binary_does_not_qualify(self):
        d = self.tmp / "legacy"
        (d / "llama-server.exe").parent.mkdir(parents=True, exist_ok=True)
        (d / "llama-server.exe").write_bytes(INCAPABLE_BYTES)
        os.environ["ARGINE_LLAMA_DIR"] = str(d)
        res = runtime_resolver.resolve()
        self.assertNotEqual(res.tier, "argine_llama_dir")
        self.assertIsNotNone(res.stale_env_note)
        self.assertEqual(res.status, runtime_resolver.RUNTIME_NOT_FOUND)

    def test_absent_env_var_is_not_reported_as_stale(self):
        res = runtime_resolver.resolve()
        self.assertIsNone(res.stale_env_note)


class PrecedenceTests(_IsolatedRootsCase):
    """Items 3-7: explicit override wins; registered runtime resolves;
    exactly one narrow-discovery candidate resolves; zero -> NOT_FOUND;
    multiple -> AMBIGUOUS."""

    def test_explicit_cli_override_wins_over_everything_else(self):
        os.environ["ARGINE_LLAMA_DIR"] = str(self.tmp / "legacy")  # stale, irrelevant
        explicit = self._write_binary("explicit/llama-server.exe", CAPABLE_BYTES)
        res = runtime_resolver.resolve(explicit_runtime=str(explicit))
        self.assertEqual(res.status, runtime_resolver.RUNTIME_RESOLVED)
        self.assertEqual(res.tier, "cli_explicit")
        self.assertEqual(res.resolved_path, str(explicit))

    def test_explicit_cli_override_incapable_is_not_found_not_silently_dropped(self):
        explicit = self._write_binary("explicit2/llama-server.exe", INCAPABLE_BYTES)
        res = runtime_resolver.resolve(explicit_runtime=str(explicit))
        self.assertEqual(res.status, runtime_resolver.RUNTIME_NOT_FOUND)
        self.assertEqual(res.tier, "cli_explicit")

    def test_registered_runtime_resolves_correctly(self):
        reg_bin = self._write_binary("registered/llama-server.exe", CAPABLE_BYTES)
        store.set_runtime_registration({"path": str(reg_bin)})
        res = runtime_resolver.resolve()
        self.assertEqual(res.status, runtime_resolver.RUNTIME_RESOLVED)
        self.assertEqual(res.tier, "registered")
        self.assertEqual(res.resolved_path, str(reg_bin))

    def test_stale_registration_falls_through_rather_than_failing_closed(self):
        store.set_runtime_registration({"path": str(self.tmp / "moved-away.exe")})
        # nothing else present either -> falls all the way to NOT_FOUND, but
        # must not raise or silently claim the dead registration resolved
        res = runtime_resolver.resolve()
        self.assertNotEqual(res.tier, "registered")

    def test_exactly_one_narrow_discovery_candidate_resolves(self):
        argine2 = self.tmp / "Argine2"
        self._write_binary("Argine2/buildA/llama-server.exe", CAPABLE_BYTES)
        res = runtime_resolver.resolve()
        self.assertEqual(res.status, runtime_resolver.RUNTIME_RESOLVED)
        self.assertEqual(res.tier, "narrow_discovery")

    def test_zero_valid_candidates_is_runtime_not_found(self):
        res = runtime_resolver.resolve()
        self.assertEqual(res.status, runtime_resolver.RUNTIME_NOT_FOUND)
        self.assertEqual(res.candidates, [])

    def test_multiple_eligible_candidates_is_runtime_ambiguous(self):
        self._write_binary("Argine2/buildA/llama-server.exe", CAPABLE_BYTES)
        self._write_binary("Argine2/buildB/llama-server.exe", CAPABLE_BYTES)
        res = runtime_resolver.resolve()
        self.assertEqual(res.status, runtime_resolver.RUNTIME_AMBIGUOUS)
        self.assertEqual(sum(1 for c in res.candidates if c.capability_ok), 2)
        # message/candidate counts must never contradict each other
        self.assertIn("2 capability-eligible", res.message)

    def test_never_picks_between_ambiguous_candidates_alphabetically(self):
        # buildZ sorts after buildA; if resolve() ever "picked the first
        # match" instead of reporting ambiguity, this would silently
        # resolve to buildA every time -- assert it does NOT resolve at all.
        self._write_binary("Argine2/buildZ/llama-server.exe", CAPABLE_BYTES)
        self._write_binary("Argine2/buildA/llama-server.exe", CAPABLE_BYTES)
        res = runtime_resolver.resolve()
        self.assertEqual(res.status, runtime_resolver.RUNTIME_AMBIGUOUS)
        self.assertIsNone(res.resolved_path)


class CapabilityEligibilityTests(_IsolatedRootsCase):
    """Item 8: a stock/incompatible llama-server is rejected by capability
    detection, never silently qualifying."""

    def test_stock_binary_without_expert_store_marker_is_ineligible(self):
        self._write_binary("Argine2/stock/llama-server.exe", INCAPABLE_BYTES)
        res = runtime_resolver.resolve()
        self.assertEqual(res.status, runtime_resolver.RUNTIME_NOT_FOUND)
        self.assertEqual(len(res.candidates), 1)
        self.assertFalse(res.candidates[0].capability_ok)

    def test_mixed_stock_and_capable_resolves_only_the_capable_one(self):
        self._write_binary("Argine2/stock/llama-server.exe", INCAPABLE_BYTES)
        capable = self._write_binary("Argine2/patched/llama-server.exe", CAPABLE_BYTES)
        res = runtime_resolver.resolve()
        self.assertEqual(res.status, runtime_resolver.RUNTIME_RESOLVED)
        self.assertEqual(res.resolved_path, str(capable))


class RegistrationStorageTests(_IsolatedRootsCase):
    """Items 9-11: registration stored outside git, does not dirty the
    repository, and binds size/hash."""

    def test_registration_stored_outside_git(self):
        rt_bin = self._write_binary("Argine2/buildA/llama-server.exe", CAPABLE_BYTES)
        reg = runtime_resolver.register_runtime(rt_bin, resolution_source="narrow_discovery")
        store.set_runtime_registration(reg)
        evidence_root = Path(os.environ["ARGINE3_RESEARCH_ROOT"])
        self.assertTrue((evidence_root / store.STATE_FILENAME).exists())
        # the repository tree itself (self.fake_repo, standing in for the
        # real Argine3 checkout) must contain nothing written by this
        self.assertEqual(list(self.fake_repo.rglob("*")), [])

    def test_registration_binds_size_and_hash(self):
        rt_bin = self._write_binary("Argine2/buildA/llama-server.exe", CAPABLE_BYTES)
        reg = runtime_resolver.register_runtime(rt_bin, resolution_source="narrow_discovery")
        self.assertEqual(reg["size_bytes"], len(CAPABLE_BYTES))
        import hashlib
        self.assertEqual(reg["sha256"], hashlib.sha256(CAPABLE_BYTES).hexdigest())

    def test_registering_incapable_binary_refused(self):
        rt_bin = self._write_binary("Argine2/stock/llama-server.exe", INCAPABLE_BYTES)
        with self.assertRaises(ValueError):
            runtime_resolver.register_runtime(rt_bin, resolution_source="narrow_discovery")


class DoctorCheapnessTests(_IsolatedRootsCase):
    """Item 12: status/doctor remain cheap -- no GB-scale hashing, resolve()
    itself never computes a SHA-256 (only register_runtime() does, and only
    when explicitly called)."""

    def test_resolve_does_not_hash_candidates(self):
        self._write_binary("Argine2/buildA/llama-server.exe", CAPABLE_BYTES)
        with mock.patch("hashlib.sha256") as m:
            m.side_effect = AssertionError("resolve() must not hash candidates")
            runtime_resolver.resolve()  # must not raise

    def test_research2_doctor_runs_and_includes_runtime_checks(self):
        from argine.research2 import runner as _runner
        report = _runner.doctor_report()
        self.assertIn("runtime_resolution", report["checks"])
        self.assertIn("argine_llama_dir", report["checks"])
        self.assertNotIn("model_sha256", str(report))


class CaptureRuntimeWiringTests(_IsolatedRootsCase):
    """Items 13-15, 18: capture() uses the resolved runtime (passed into
    the child subprocess's own environment), binds it into provenance, and
    RUNTIME_NOT_FOUND/RUNTIME_AMBIGUOUS create no evidence and map to a
    non-zero top-level exit code."""

    def test_capture_runtime_not_found_creates_no_capture_dir(self):
        model = self._write_binary("fake.gguf", b"x")
        evidence = self.tmp / "evidence2"
        result = r2_01_capture.capture(model_path=model, dry_run=False, out_root=evidence)
        self.assertEqual(result["outcome"], r2_01_capture.OUTCOME_RUNTIME_NOT_FOUND)
        self.assertFalse(evidence.exists())

    def test_capture_runtime_ambiguous_creates_no_capture_dir(self):
        self._write_binary("Argine2/buildA/llama-server.exe", CAPABLE_BYTES)
        self._write_binary("Argine2/buildB/llama-server.exe", CAPABLE_BYTES)
        model = self._write_binary("fake.gguf", b"x")
        evidence = self.tmp / "evidence3"
        result = r2_01_capture.capture(model_path=model, dry_run=False, out_root=evidence)
        self.assertEqual(result["outcome"], r2_01_capture.OUTCOME_RUNTIME_AMBIGUOUS)
        self.assertFalse(evidence.exists())

    def test_dry_run_reports_resolved_runtime_without_executing(self):
        self._write_binary("Argine2/buildA/llama-server.exe", CAPABLE_BYTES)
        model = self._write_binary("fake.gguf", b"x")
        result = r2_01_capture.capture(model_path=model, dry_run=True, out_root=self.tmp / "ev4")
        self.assertEqual(result["outcome"], r2_01_capture.OUTCOME_DRY_RUN)
        self.assertEqual(result["runtime_resolution"]["status"],
                         runtime_resolver.RUNTIME_RESOLVED)
        self.assertFalse((self.tmp / "ev4").exists())

    def test_real_capture_pins_resolved_runtime_into_child_env(self):
        runtime_bin = self._write_binary("Argine2/buildA/llama-server.exe", CAPABLE_BYTES)
        model = self._write_binary("fake.gguf", b"x")
        seen_env = {}

        def _fake_run(cmd, cwd=None, capture_output=True, text=True, env=None):
            seen_env.update(env or {})
            return mock.Mock(returncode=1, stdout="", stderr="boom")

        with mock.patch("subprocess.run", side_effect=_fake_run):
            result = r2_01_capture.capture(model_path=model, dry_run=False,
                                           out_root=self.tmp / "ev5")
        self.assertEqual(result["outcome"], r2_01_capture.OUTCOME_CAPTURE_FAILED)
        self.assertEqual(seen_env.get("ARGINE_LLAMA_DIR"), str(runtime_bin.parent))
        # provenance binds the selected runtime (item 14)
        manifest = next((self.tmp / "ev5").rglob("manifest.json"))
        import json
        data = json.loads(manifest.read_text())
        self.assertEqual(data["runtime_resolution"]["resolved_path"], str(runtime_bin))

    def test_failed_capture_with_env_override_never_mutates_global_argine_llama_dir(self):
        self._write_binary("Argine2/buildA/llama-server.exe", CAPABLE_BYTES)
        model = self._write_binary("fake.gguf", b"x")
        before = os.environ.get("ARGINE_LLAMA_DIR")
        with mock.patch("subprocess.run") as m:
            m.return_value = mock.Mock(returncode=1, stdout="", stderr="boom")
            r2_01_capture.capture(model_path=model, dry_run=False, out_root=self.tmp / "ev6")
        self.assertEqual(os.environ.get("ARGINE_LLAMA_DIR"), before)

    def test_cli_runtime_not_found_is_nonzero_at_top_level(self):
        import argine.cli as cli
        model = self._write_binary("fake.gguf", b"x")
        rc = cli.main(["research2", "capture", "R2-01", "--model", str(model),
                      "--allow-dirty"])
        self.assertNotEqual(rc, 0)

    def test_cli_runtime_ambiguous_is_nonzero_at_top_level(self):
        import argine.cli as cli
        self._write_binary("Argine2/buildA/llama-server.exe", CAPABLE_BYTES)
        self._write_binary("Argine2/buildB/llama-server.exe", CAPABLE_BYTES)
        model = self._write_binary("fake.gguf", b"x")
        rc = cli.main(["research2", "capture", "R2-01", "--model", str(model),
                      "--allow-dirty"])
        self.assertNotEqual(rc, 0)

    def test_successful_mocked_capture_with_resolved_runtime_registers_once(self):
        """Section 8: a real capture success MAY auto-register the runtime
        it used, but never when it already came from an existing
        registration (no redundant re-hash)."""
        runtime_bin = self._write_binary("Argine2/buildA/llama-server.exe", CAPABLE_BYTES)
        model = self._write_binary("fake.gguf", b"x")
        evidence = self.tmp / "ev7"

        def _fake_run(cmd, cwd=None, capture_output=True, text=True, env=None):
            # emulate a successful capture: write the exact trace file
            # capture() expects at the exact path it will check
            out_flag_idx = cmd.index("--out")
            out_dir = Path(cmd[out_flag_idx + 1])
            budget_idx = cmd.index("--expert-budget")
            budget = cmd[budget_idx + 1]
            run_dir = out_dir / f"run-00-{budget}"
            run_dir.mkdir(parents=True, exist_ok=True)
            (run_dir / "trace.jsonl.gz").write_bytes(b"\x1f\x8b fake gzip")
            return mock.Mock(returncode=0, stdout="ok", stderr="")

        with mock.patch("subprocess.run", side_effect=_fake_run):
            result = r2_01_capture.capture(model_path=model, dry_run=False, out_root=evidence)
        self.assertEqual(result["outcome"], r2_01_capture.OUTCOME_CAPTURED)
        reg = store.get_runtime_registration()
        self.assertIsNotNone(reg)
        self.assertEqual(reg["path"], str(runtime_bin))


class WindowsBatchStructureTests(unittest.TestCase):
    """Item 20: the .bat wrapper still structurally captures/propagates the
    python process's own ERRORLEVEL. This cannot execute native cmd.exe
    from this environment (see the final report's EXIT-CODE ROOT CAUSE /
    SAFE WINDOWS VALIDATION sections) -- this only checks the script's own
    text, as a regression guard against someone re-introducing the
    `endlocal`-before-`exit /b` ordering hazard."""

    def test_argine_bat_exits_with_pythons_own_errorlevel(self):
        text = (REPO_ROOT / "argine.bat").read_text(encoding="utf-8")
        lines = [l.strip() for l in text.splitlines() if l.strip() and not
                 l.strip().upper().startswith("REM")]
        self.assertIn("exit /b %ERRORLEVEL%", lines)
        python_idx = next(i for i, l in enumerate(lines) if l.startswith("python -m argine"))
        exit_idx = lines.index("exit /b %ERRORLEVEL%")
        # nothing may run between the python call and exit /b that could
        # itself change ERRORLEVEL before it's read
        self.assertEqual(exit_idx, python_idx + 1)


class ShellChainRuntimeRegressionTests(_IsolatedRootsCase):
    """Real-subprocess regression for the native `&&` chain, using a safe
    (no-model) RUNTIME_NOT_FOUND failure -- a POSIX stand-in for the
    Windows argine.bat && chain, per the task's own allowance (section 10:
    'test the underlying Python process exit code rigorously')."""

    def test_runtime_not_found_blocks_shell_chain(self):
        env = dict(os.environ)
        env["ARGINE3_RESEARCH_ROOT"] = str(self.tmp / "evidence_chain")
        env.pop("ARGINE_LLAMA_DIR", None)
        model = self._write_binary("fake.gguf", b"x")
        cmd = (f"cd {REPO_ROOT} && {sys.executable} -m argine research2 capture R2-01 "
              f"--model {model} --allow-dirty && echo SHOULD_NOT_RUN")
        # Even though this test's own process has runtime_resolver._repo_root
        # monkeypatched, a SEPARATE subprocess re-imports the real module
        # fresh and uses the REAL _repo_root() -- so this exercises whatever
        # the host machine actually has. It is still a valid, meaningful
        # check: it proves the shell-chain-blocking mechanism itself works
        # for ANY non-zero research2 capture outcome, using RUNTIME_NOT_FOUND
        # as a safe (no-model) example whenever the host has no resolvable
        # runtime; if the host DOES have one, this instead falls through to
        # a real CAPTURE_FAILED (model file is a 1-byte fake), which is
        # equally non-zero -- either way SHOULD_NOT_RUN must not appear.
        res = subprocess.run(cmd, shell=True, capture_output=True, text=True,
                             env=env, timeout=60)
        self.assertNotIn("SHOULD_NOT_RUN", res.stdout)
        self.assertNotEqual(res.returncode, 0)


if __name__ == "__main__":
    unittest.main()
