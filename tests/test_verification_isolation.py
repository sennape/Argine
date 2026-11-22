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
        _git(self.caller, "reset", "-q", "--hard", "HEAD")
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
        for name in ["R3 closure", "R3 paper", "R4 Phase 0", "R4 Phase 1", "R4 Phase 2", "R1 registry", "R2 paper freeze", "README index", "links", "consolidation"]:
            self.assertIn("PASS " + name, r.stdout)
        self.assertEqual(_git(self.caller, "status", "--porcelain").stdout, "")


if __name__ == "__main__":
    unittest.main()
