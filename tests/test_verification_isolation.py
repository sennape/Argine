"""Regression test: frozen verifiers must never dirty the caller's checkout.

Defect: verify_paper.py rewrites paper/research3/SOURCE_MAP.md. On Windows, Python text mode writes CRLF into a file
pinned to LF, so a verifier run left the clone modified and later checks (Phase 0 "uncommitted Research 3 changes",
`git status clean`) failed. shared/tooling/run_frozen_verifier.py now runs every frozen verifier in a disposable
clone. These tests reproduce the Windows behaviour on any OS (a usercustomize that makes Path.write_text emit CRLF)
inside a disposable clone of HEAD, and assert: negative control dirties, wrapped run passes and leaves it clean.

Never touches the repository under test (everything happens in temp clones of the committed HEAD)."""
from __future__ import annotations

import os
import shutil
import stat
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
CRLF_SIM = (
    "import pathlib\n"
    "_w = pathlib.Path.write_text\n"
    "def write_text(self, data, encoding=None, errors=None, newline=None):\n"
    "    return _w(self, data.replace('\\n', '\\r\\n'), encoding=encoding, errors=errors, newline='')\n"
    "pathlib.Path.write_text = write_text\n"
)
SM = "paper/research3/SOURCE_MAP.md"


def _rm(path):
    shutil.rmtree(path, onerror=lambda f, p, e: (os.chmod(p, stat.S_IWRITE), f(p)))


def _git(cwd, *a):
    return subprocess.run(["git", *a], cwd=cwd, capture_output=True, text=True)


class VerifierIsolation(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        if _git(ROOT, "rev-parse", "HEAD").returncode != 0:
            raise unittest.SkipTest("needs a Git checkout")
        cls.tmp = tempfile.mkdtemp(prefix="ariso")
        cls.sim = os.path.join(cls.tmp, "sim"); os.makedirs(cls.sim)
        Path(cls.sim, "usercustomize.py").write_text(CRLF_SIM, encoding="utf-8")
        cls.caller = os.path.join(cls.tmp, "c")
        subprocess.run(["git", "-c", "core.longpaths=true", "-c", "core.autocrlf=false", "clone", "-q", "--no-hardlinks", str(ROOT), cls.caller], check=True)
        cls.env = dict(os.environ, PYTHONPATH=cls.sim + os.pathsep + os.environ.get("PYTHONPATH", ""))

    @classmethod
    def tearDownClass(cls):
        _rm(cls.tmp)

    def setUp(self):
        _git(self.caller, "reset", "-q", "--hard", "HEAD"); _git(self.caller, "clean", "-fdq")
        self.assertEqual(_git(self.caller, "status", "--porcelain").stdout, "", "caller must start clean")

    def test_negative_control_direct_run_dirties_under_crlf_write(self):
        r = subprocess.run([sys.executable, "paper/research3/scripts/verify_paper.py"], cwd=self.caller, env=dict(self.env, ARGINE_GITCOMPAT_ROOT=self.caller,
                           PYTHONPATH=os.path.join(self.caller, "shared", "tooling", "gitcompat") + os.pathsep + self.env["PYTHONPATH"]), capture_output=True, text=True)
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        st = _git(self.caller, "status", "--porcelain").stdout
        self.assertIn(SM, st, "the simulation must reproduce the original defect (otherwise this test proves nothing)")
        diff = _git(self.caller, "diff", "--ignore-space-at-eol", "--", SM).stdout
        self.assertEqual(diff, "", "the change is line-ending only")

    def test_r3_paper_verifier_passes_and_leaves_caller_clean(self):
        r = subprocess.run([sys.executable, "shared/tooling/run_frozen_verifier.py", "paper/research3/scripts/verify_paper.py"], cwd=self.caller, env=self.env, capture_output=True, text=True)
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertIn("VERIFY OK", r.stdout)
        self.assertEqual(_git(self.caller, "status", "--porcelain").stdout, "")

    def test_exit_code_is_propagated(self):
        r = subprocess.run([sys.executable, "shared/tooling/run_frozen_verifier.py", "tools/check_links.py", "--no-such-flag-for-failure"], cwd=self.caller, env=self.env, capture_output=True, text=True)
        direct = subprocess.run([sys.executable, "tools/check_links.py", "--no-such-flag-for-failure"], cwd=self.caller, capture_output=True, text=True)
        self.assertEqual(r.returncode, direct.returncode)

    def test_verify_all_passes_and_leaves_caller_clean(self):
        r = subprocess.run([sys.executable, "shared/tooling/verify_all.py", "--skip-unittest"], cwd=self.caller, env=self.env, capture_output=True, text=True)
        self.assertEqual(r.returncode, 0, r.stdout[-3000:] + r.stderr[-2000:])
        for name in ["R3 closure", "R3 paper", "R4 paper", "R4 Phase 0", "R4 Phase 1", "R4 Phase 2", "R5 Phase 0", "R5 Phase 1a", "R5 G1 seal", "R1 registry", "R2 paper freeze", "README index", "links", "consolidation"]:
            self.assertIn("PASS " + name, r.stdout)
        self.assertEqual(_git(self.caller, "status", "--porcelain").stdout, "")

    # --- caller-state preflight: isolation must never hide a dirty caller -------------------------------------
    def _assert_refused(self, cmd):
        r = subprocess.run([sys.executable, *cmd], cwd=self.caller, env=self.env, capture_output=True, text=True)
        self.assertNotEqual(r.returncode, 0, r.stdout[-1500:])
        self.assertIn("caller working tree is dirty", r.stderr)
        self.assertNotIn("PASS ", r.stdout, "no verification may run against a dirty caller")
        return r

    def test_dirty_caller_is_not_hidden(self):
        target = Path(self.caller, "CONTRIBUTING.md"); before = target.read_bytes() + b"\nlocal edit\n"
        target.write_bytes(before)  # disposable fixture; the caller itself is a throw-away clone
        for cmd in (["shared/tooling/verify_all.py", "--skip-unittest"], ["shared/tooling/run_frozen_verifier.py", "paper/research3/scripts/verify_paper.py"]):
            self._assert_refused(cmd)
        self.assertEqual(target.read_bytes(), before, "the caller modification must stay untouched")
        self.assertIn("CONTRIBUTING.md", _git(self.caller, "status", "--porcelain").stdout)

    def test_staged_change_is_not_hidden(self):
        target = Path(self.caller, "CONTRIBUTING.md"); target.write_bytes(target.read_bytes() + b"\nstaged edit\n")
        _git(self.caller, "add", "CONTRIBUTING.md")
        self.assertTrue(_git(self.caller, "status", "--porcelain").stdout.startswith("M "))
        self._assert_refused(["shared/tooling/verify_all.py", "--skip-unittest"])
        self.assertTrue(_git(self.caller, "status", "--porcelain").stdout.startswith("M "), "staged change must remain staged")

    def test_deleted_and_untracked_files_are_not_hidden(self):
        os.remove(Path(self.caller, "CONTRIBUTING.md"))
        self._assert_refused(["shared/tooling/verify_all.py", "--skip-unittest"])
        _git(self.caller, "checkout", "--", "CONTRIBUTING.md")
        Path(self.caller, "stray_untracked.txt").write_text("x", encoding="utf-8")
        self._assert_refused(["shared/tooling/run_frozen_verifier.py", "experiments/R4-00-causal-eviction-distillation/phase0.py", "verify"])
        self.assertTrue(Path(self.caller, "stray_untracked.txt").exists())

    def test_clean_caller_after_r3_verifier(self):
        self.test_r3_paper_verifier_passes_and_leaves_caller_clean()

    def test_full_verify_clean(self):
        self.test_verify_all_passes_and_leaves_caller_clean()

    # --- Windows temp-clone cleanup robustness ----------------------------------------------------------------
    def _load_wrapper(self):
        import importlib.util
        spec = importlib.util.spec_from_file_location("rfv_caller", os.path.join(self.caller, "shared", "tooling", "run_frozen_verifier.py"))
        m = importlib.util.module_from_spec(spec); spec.loader.exec_module(m)
        self.assertEqual(Path(m.ROOT).resolve(), Path(self.caller).resolve())
        return m

    def _tree(self):
        d = tempfile.mkdtemp(prefix="arc", dir=self.tmp)
        os.makedirs(os.path.join(d, "a", "b")); Path(d, "a", "b", "f.txt").write_text("x", encoding="utf-8")
        ro = Path(d, "a", "ro.txt"); ro.write_text("y", encoding="utf-8"); os.chmod(ro, stat.S_IREAD)
        return d

    def test_cleanup_retries_after_simulated_winerror_145(self):
        m = self._load_wrapper(); d = self._tree(); real = shutil.rmtree; calls = []
        def flaky(path, **kw):
            calls.append(1)
            if len(calls) == 1:
                err = OSError(39, "directory not empty"); err.winerror = 145; raise err
            return real(path, **kw)
        from unittest import mock
        with mock.patch.object(m.shutil, "rmtree", flaky), mock.patch.object(m.time, "sleep"):
            self.assertTrue(m._rmtree(d))
        self.assertEqual(len(calls), 2); self.assertFalse(os.path.exists(d))

    def test_cleanup_removes_readonly_files(self):
        m = self._load_wrapper(); d = self._tree()
        self.assertTrue(m._rmtree(d)); self.assertFalse(os.path.exists(d))

    def test_cleanup_retry_is_bounded(self):
        from unittest import mock
        m = self._load_wrapper(); d = self._tree(); calls = []; sleeps = []
        def always(path, **kw):
            calls.append(1); raise OSError(39, "directory not empty")
        with mock.patch.object(m.shutil, "rmtree", always), mock.patch.object(m.time, "sleep", sleeps.append):
            self.assertFalse(m._rmtree(d))
        self.assertEqual(len(calls), m.CLEANUP_ATTEMPTS)
        self.assertEqual(len(sleeps), m.CLEANUP_ATTEMPTS - 1)
        self.assertLessEqual(sum(sleeps), 5.0, "total backoff stays in the low seconds")
        self.assertTrue(os.path.exists(d)); _rm(d)

    def test_cleanup_failure_is_not_confused_with_verifier_result(self):
        from unittest import mock
        import io, contextlib
        m = self._load_wrapper(); err = io.StringIO()
        with mock.patch.object(m, "_rmtree", return_value=False), contextlib.redirect_stderr(err):
            rc = m.run("tools/check_links.py")
        self.assertEqual(rc, 0, "a passing verifier keeps its exit code when cleanup fails")
        self.assertEqual(len(m.CLEANUP_FAILURES), 1)
        self.assertIn("HARNESS CLEANUP ERROR", err.getvalue()); self.assertNotIn("verifier failed", err.getvalue().lower())
        with mock.patch.object(m, "_rmtree", return_value=True):
            rc = m.run("experiments/R4-00-causal-eviction-distillation/phase0.py", "bogus-subcommand")
        self.assertNotEqual(rc, 0, "a failing verifier keeps its non-zero code")
        for t in m.CLEANUP_FAILURES: _rm(t)
        self.assertEqual(_git(self.caller, "status", "--porcelain").stdout, "", "caller untouched")

    def test_no_temp_clone_left_after_verify_all(self):
        scratch = os.path.join(self.tmp, "tmpdir"); os.makedirs(scratch)
        env = dict(self.env, TMPDIR=scratch, TEMP=scratch, TMP=scratch)
        r = subprocess.run([sys.executable, "shared/tooling/verify_all.py", "--skip-unittest"], cwd=self.caller, env=env, capture_output=True, text=True)
        self.assertEqual(r.returncode, 0, r.stdout[-2000:] + r.stderr[-1500:])
        self.assertEqual(os.listdir(scratch), [], "no disposable verifier clone may remain")
        self.assertEqual(_git(self.caller, "status", "--porcelain").stdout, "")

    # --- resource lifetime (the parent must own and release everything before cleanup) -------------------------
    def test_parent_cwd_never_enters_clone_and_resources_released_before_cleanup(self):
        from unittest import mock
        m = self._load_wrapper(); start = os.getcwd(); seen = {}
        def spy(path):
            seen["cwd"] = os.getcwd(); seen["path"] = path; seen.update(m.LAST_RUN)
            return real(path)
        real = m._rmtree
        with mock.patch.object(m, "_rmtree", spy):
            rc = m.run("tools/check_links.py")
        self.assertEqual(rc, 0)
        self.assertEqual(seen["cwd"], start, "A: parent cwd unchanged")
        self.assertFalse(os.path.abspath(seen["cwd"]).startswith(os.path.abspath(seen["path"])), "A: parent cwd is outside the clone")
        self.assertIs(seen["terminated"], True, "B: verifier terminated before cleanup")
        self.assertIsNotNone(seen["returncode"])
        self.assertIs(seen["handles_closed"], True, "C: wrapper-owned handles closed before cleanup")
        self.assertIs(seen["finished_before_cleanup"], True, "D: cleanup follows the end of the verifier context")
        if os.name != "nt":
            with self.assertRaises(ProcessLookupError):
                os.kill(seen["pid"], 0)  # the verifier pid is gone (reaped), not merely finished
        self.assertEqual(os.getcwd(), start)

    def test_popen_is_used_as_context_manager_with_closed_streams(self):
        from unittest import mock
        m = self._load_wrapper(); events = []; real_popen = subprocess.Popen
        class Spy(real_popen):
            def __exit__(self, *a):
                r = super().__exit__(*a); events.append(("exit", self.returncode, self.stdin, self.stdout, self.stderr)); return r
        with mock.patch.object(m.subprocess, "Popen", Spy), mock.patch.object(m, "_rmtree", side_effect=lambda p: events.append(("cleanup",)) or True):
            m.run("tools/check_links.py")
        kinds = [e[0] for e in events]
        self.assertEqual(kinds[-2:], ["exit", "cleanup"], "the verifier's Popen context exited before cleanup began")
        self.assertEqual(kinds.count("cleanup"), 1)
        for e in events[:-1]:
            self.assertIsNotNone(e[1])
            for stream in e[2:]:
                self.assertTrue(stream is None or stream.closed, "pipes are closed when the Popen context exits")

    def test_exit_codes_dirty_setup_and_verifier(self):
        import io, contextlib
        from unittest import mock
        m = self._load_wrapper()
        with mock.patch.object(m, "make_clone", side_effect=subprocess.CalledProcessError(128, "git")), contextlib.redirect_stderr(io.StringIO()) as e:
            self.assertEqual(m.run("tools/check_links.py"), 4)   # H
        self.assertIn("HARNESS SETUP ERROR", e.getvalue())
        self.assertNotEqual(m.run("experiments/R4-00-causal-eviction-distillation/phase0.py", "bogus-subcommand"), 0)  # E
        self.assertEqual(m.run("tools/check_links.py"), 0)  # F
        Path(self.caller, "CONTRIBUTING.md").write_bytes(Path(self.caller, "CONTRIBUTING.md").read_bytes() + b"x")
        with contextlib.redirect_stderr(io.StringIO()):
            self.assertEqual(m.run("tools/check_links.py"), 2)  # G
        self.assertEqual(m.LAST_RUN, {}, "no verifier was started for a dirty caller")

    def test_extended_length_path_for_windows(self):
        m = self._load_wrapper()
        self.assertEqual(m._to_extended("C:\\Users\\x\\Temp\\arv1\\r"), "\\\\?\\C:\\Users\\x\\Temp\\arv1\\r")
        self.assertEqual(m._to_extended("\\\\?\\C:\\a"), "\\\\?\\C:\\a")
        self.assertEqual(m._to_extended("\\\\srv\\sh\\a"), "\\\\?\\UNC\\srv\\sh\\a")
        self.assertEqual(m._long("/tmp/x"), "/tmp/x" if os.name != "nt" else m._to_extended("/tmp/x"))

    def test_very_long_paths_are_removed(self):
        m = self._load_wrapper(); d = tempfile.mkdtemp(prefix="arl", dir=self.tmp)
        deep = os.path.join(d, "r", "experiments", "R2-10-working-set-predictor", "dev_tournament_checkpoint", "cells"); os.makedirs(deep)
        for i in range(5):
            Path(deep, ("instruction-v1_seed-42__recency_frequency_transition_blend__" * 3)[:170] + str(i) + ".json").write_text("{}", encoding="utf-8")
        self.assertTrue(m._rmtree(d)); self.assertFalse(os.path.exists(d))

    def test_debug_report_is_opt_in(self):
        import io, contextlib
        from unittest import mock
        m = self._load_wrapper()
        with contextlib.redirect_stderr(io.StringIO()) as quiet:
            m.run("tools/check_links.py")
        self.assertNotIn("[cleanup-debug]", quiet.getvalue())
        with mock.patch.dict(os.environ, {"ARGINE_VERIFY_DEBUG_CLEANUP": "1"}), contextlib.redirect_stderr(io.StringIO()) as loud:
            m.run("tools/check_links.py")
        for key in ("parent pid=", "verifier pid=", "terminated=True", "entries_remaining=", "wrapper-owned open resources"):
            self.assertIn(key, loud.getvalue())


if __name__ == "__main__":
    unittest.main()
