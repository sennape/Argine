#!/usr/bin/env python3
"""Run a frozen verifier UNMODIFIED, in a disposable clone of the current commit, with the Git-object
equivalence layer active.

    python shared/tooling/run_frozen_verifier.py experiments/R3-final-closure/closure.py verify

Why a disposable clone: some frozen verifiers write files into their own tree (verify_paper.py rewrites
paper/research3/SOURCE_MAP.md; on Windows Python's text mode writes CRLF into a file this repository pins to LF).
Running them in a throw-away `git clone` of HEAD means the caller's checkout can never be modified; the clone is
deleted afterwards and the verifier's real exit code and output are propagated. Nothing is masked (no
assume-unchanged, skip-worktree or ignore rules): the caller's `git status` is simply never touched.

The layer (shared/tooling/gitcompat/sitecustomize.py) answers the verifier's historical-Git queries from
manifests/git-object-equivalence.json, and only when the clone's Git objects equal the recorded originals.
Child interpreters (a verifier that runs another verifier) inherit it through PYTHONPATH.

Because the verifier sees only committed HEAD, the caller is first required to be COMPLETELY clean (`git status
--porcelain` empty: no modified, staged, deleted or untracked files). A dirty caller makes the run fail before any
verification, so a dirty-tree check inside a frozen verifier can never be bypassed by the isolation. Nothing is
committed, staged, restored or filtered automatically. Untracked-file policy: any untracked, non-ignored file fails
(stricter than the historical verifiers, and simpler and safer than distinguishing evidence paths).
Set ARGINE_KEEP_VERIFY_TREE=1 to keep the clone for diagnosis (its path is printed)."""
from __future__ import annotations

import os
import shutil
import stat
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]


def _rmtree(path: str) -> None:
    def onerror(func, p, _exc):  # read-only Git objects on Windows
        os.chmod(p, stat.S_IWRITE); func(p)
    shutil.rmtree(path, onerror=onerror)


def make_clone(dest: str) -> None:
    """Clone the caller's current commit into dest (long paths on, autocrlf off; .gitattributes decides bytes)."""
    subprocess.run(["git", "-c", "core.longpaths=true", "-c", "core.autocrlf=false", "clone", "-q", "--no-hardlinks", str(ROOT), dest], check=True)


class DirtyCaller(RuntimeError):
    pass


def preflight(root: Path = ROOT) -> None:
    """Fail unless the caller checkout is completely clean. Raises DirtyCaller with the offending entries."""
    r = subprocess.run(["git", "-C", str(root), "status", "--porcelain"], capture_output=True, text=True)
    if r.returncode != 0:
        raise DirtyCaller(f"cannot read the caller's Git status (rc={r.returncode}): {r.stderr.strip()}")
    lines = [l for l in r.stdout.splitlines() if l.strip()]
    if lines:
        shown = "\n".join("  " + l for l in lines[:20]) + (f"\n  ... and {len(lines) - 20} more" if len(lines) > 20 else "")
        raise DirtyCaller("caller working tree is dirty; frozen verifiers run on a clone of committed HEAD and would not see these changes. "
                          "Commit, stash or discard them yourself, then re-run. `git status --porcelain` shows:\n" + shown)


def run(script: str, *args: str) -> int:
    try:
        preflight()
    except DirtyCaller as e:
        print("VERIFICATION REFUSED:", e, file=sys.stderr)
        return 2
    tmp = tempfile.mkdtemp(prefix="arv")  # short path: Windows path-length limits
    clone = os.path.join(tmp, "r")
    try:
        make_clone(clone)
        env = dict(os.environ); env["ARGINE_GITCOMPAT_ROOT"] = clone
        env["PYTHONPATH"] = os.path.join(clone, "shared", "tooling", "gitcompat") + os.pathsep + env.get("PYTHONPATH", "")
        return subprocess.run([sys.executable, os.path.join(clone, script), *args], cwd=clone, env=env).returncode
    finally:
        if os.environ.get("ARGINE_KEEP_VERIFY_TREE"):
            print("kept verification clone:", clone)
        else:
            _rmtree(tmp)


if __name__ == "__main__":
    if len(sys.argv) < 2: raise SystemExit(__doc__)
    raise SystemExit(run(sys.argv[1], *sys.argv[2:]))
