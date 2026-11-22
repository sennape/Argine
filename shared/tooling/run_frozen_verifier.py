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
Windows paths: the disposable clone is removed through an extended-length (\\?\) path, because several frozen evidence files have names so long that
<temp dir>\...\file exceeds MAX_PATH. The parent process never changes its working directory (children get cwd=), and every
verifier process is waited for and its handles closed before cleanup starts. Set ARGINE_VERIFY_DEBUG_CLEANUP=1 for a
resource report on stderr before cleanup. A persistent cleanup failure never changes the verifier's exit code; it is reported as HARNESS CLEANUP ERROR.
Set ARGINE_KEEP_VERIFY_TREE=1 to keep the clone for diagnosis (its path is printed)."""
from __future__ import annotations

import ntpath
import os
import shutil
import stat
import subprocess
import sys
import tempfile
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]

CLEANUP_FAILURES: list[str] = []  # temp clones that could not be removed (reported by verify_all)
CLEANUP_ATTEMPTS = 8  # defensive fallback only; the primary fix is deterministic resource ownership
CLEANUP_DELAY = 0.1  # seconds, doubled each retry and capped at CLEANUP_MAX_DELAY: ~2.5 s in total, never unbounded
CLEANUP_MAX_DELAY = 0.5
LAST_RUN: dict = {}  # bookkeeping of the most recent verifier execution (used by tests and the debug report)
LAST_CLEANUP_ERROR: list = []  # [(path, exception)] of the last failed attempt (debug report)


def _debug() -> bool:
    return bool(os.environ.get("ARGINE_VERIFY_DEBUG_CLEANUP"))


def _to_extended(path: str) -> str:
    """Windows extended-length form (\\\\?\\C:\\...), which lifts the 260-character MAX_PATH limit."""
    p = ntpath.abspath(path)
    if p.startswith("\\\\?\\"):
        return p
    return "\\\\?\\UNC\\" + p[2:] if p.startswith("\\\\") else "\\\\?\\" + p


def _long(path: str) -> str:
    return _to_extended(path) if os.name == "nt" else path


def _clear_readonly_and_retry(func, path, exc):
    """rmtree error handler: read-only Git objects on Windows. One retry after clearing the attribute."""
    LAST_CLEANUP_ERROR[:] = [(path, exc if isinstance(exc, BaseException) else exc[1])]
    try:
        os.chmod(path, stat.S_IWRITE)
    except OSError:
        pass
    func(path)


def _remaining(path: str) -> list:
    out = []
    for dp, dn, fn in os.walk(_long(path)):
        out.extend(os.path.join(dp, n) for n in dn + fn)
    return out


def _rmtree(path: str) -> bool:
    """Remove a directory tree. Returns True when it is gone, False after bounded retries.

    The tree is addressed through an extended-length path on Windows: several frozen evidence files have names
    of ~165 characters, so <temp>\\arvXXXX\\r\\experiments\\...\\cells\\<name> exceeds MAX_PATH and could not be
    unlinked through an ordinary path. The retry loop is only a defensive fallback for genuinely transient
    states (a file pending deletion while an antivirus/indexer still holds it); it is bounded."""
    target = _long(path)
    delay = CLEANUP_DELAY
    for attempt in range(CLEANUP_ATTEMPTS):
        try:
            if sys.version_info >= (3, 12):
                shutil.rmtree(target, onexc=_clear_readonly_and_retry)
            else:
                shutil.rmtree(target, onerror=_clear_readonly_and_retry)
        except OSError as e:
            LAST_CLEANUP_ERROR[:] = [(getattr(e, "filename", None), e)]
        if not os.path.exists(target):
            return True
        if attempt + 1 < CLEANUP_ATTEMPTS:
            time.sleep(delay); delay = min(delay * 2, CLEANUP_MAX_DELAY)
    return False


def make_clone(dest: str) -> None:
    """Clone the caller's current commit into dest (long paths on, autocrlf off; .gitattributes decides bytes)."""
    subprocess.run(["git", "-c", "core.longpaths=true", "-c", "core.autocrlf=false", "-c", "core.fsmonitor=false", "clone", "-q", "--no-hardlinks", str(ROOT), dest],
                   check=True, stdin=subprocess.DEVNULL)


class DirtyCaller(RuntimeError):
    pass


def preflight(root: Path = ROOT) -> None:
    """Fail unless the caller checkout is completely clean. Raises DirtyCaller with the offending entries."""
    r = subprocess.run(["git", "-C", str(root), "status", "--porcelain"], capture_output=True, text=True, stdin=subprocess.DEVNULL)
    if r.returncode != 0:
        raise DirtyCaller(f"cannot read the caller's Git status (rc={r.returncode}): {r.stderr.strip()}")
    lines = [l for l in r.stdout.splitlines() if l.strip()]
    if lines:
        shown = "\n".join("  " + l for l in lines[:20]) + (f"\n  ... and {len(lines) - 20} more" if len(lines) > 20 else "")
        raise DirtyCaller("caller working tree is dirty; frozen verifiers run on a clone of committed HEAD and would not see these changes. "
                          "Commit, stash or discard them yourself, then re-run. `git status --porcelain` shows:\n" + shown)


def _execute(clone: str, script: str, args: tuple) -> int:
    """Run the verifier inside the clone. The parent never changes its own cwd: the child gets cwd=clone.

    Ownership: the Popen object is a context manager, so its process handle and any pipes are closed when the
    block exits; wait() guarantees the verifier has terminated. Nothing of this function outlives it, so cleanup
    starts with no live process, handle or cwd reference into the clone."""
    env = dict(os.environ); env["ARGINE_GITCOMPAT_ROOT"] = clone
    env["PYTHONPATH"] = os.path.join(clone, "shared", "tooling", "gitcompat") + os.pathsep + env.get("PYTHONPATH", "")
    # No background Git daemons watching the clone (a daemon would hold directory handles into it).
    env.update(GIT_CONFIG_COUNT="1", GIT_CONFIG_KEY_0="core.fsmonitor", GIT_CONFIG_VALUE_0="false")
    with subprocess.Popen([sys.executable, os.path.join(clone, script), *args], cwd=clone, env=env, stdin=subprocess.DEVNULL, close_fds=True) as proc:
        rc = proc.wait()
        LAST_RUN.update(pid=proc.pid, returncode=rc, terminated=proc.poll() is not None)
    LAST_RUN["handles_closed"] = True
    return rc


def _debug_report(tmp: str, rc) -> None:
    rest = []
    try:
        rest = _remaining(tmp)
    except OSError as e:
        print(f"[cleanup-debug] cannot enumerate: {e}", file=sys.stderr)
    err = LAST_CLEANUP_ERROR[-1] if LAST_CLEANUP_ERROR else None
    print("[cleanup-debug] parent pid=%s cwd=%s\n[cleanup-debug] verifier pid=%s returncode=%s terminated=%s handles_closed=%s\n"
          "[cleanup-debug] temp root=%s exists=%s entries_remaining=%d longest_remaining_path=%d\n[cleanup-debug] last error=%r\n"
          "[cleanup-debug] wrapper-owned open resources: none (Popen closed by its context manager, stdin=DEVNULL, no chdir)"
          % (os.getpid(), os.getcwd(), LAST_RUN.get("pid"), LAST_RUN.get("returncode", rc), LAST_RUN.get("terminated"), LAST_RUN.get("handles_closed"),
             tmp, os.path.exists(_long(tmp)), len(rest), max((len(x) for x in rest), default=0), err), file=sys.stderr)


def run(script: str, *args: str) -> int:
    try:
        preflight()
    except DirtyCaller as e:
        print("VERIFICATION REFUSED:", e, file=sys.stderr)
        return 2
    # Exit codes: the verifier's own code is returned unchanged (A: verifier failure); 2 = dirty caller;
    # 4 = harness setup failure (B). A cleanup failure (C) never changes the verifier's code: it is reported
    # separately as HARNESS CLEANUP ERROR and recorded in CLEANUP_FAILURES.
    LAST_RUN.clear(); LAST_CLEANUP_ERROR.clear()
    tmp = tempfile.mkdtemp(prefix="arv")  # short path: Windows path-length limits
    clone = os.path.join(tmp, "r")
    rc = 4
    try:
        make_clone(clone)
    except (subprocess.CalledProcessError, OSError) as e:
        print(f"HARNESS SETUP ERROR: could not create the disposable clone of HEAD: {e}", file=sys.stderr)
    else:
        rc = _execute(clone, script, args)  # every resource of the execution is released when this returns
    LAST_RUN["finished_before_cleanup"] = True
    if os.environ.get("ARGINE_KEEP_VERIFY_TREE"):
        print("kept verification clone:", clone)
    else:
        if _debug():
            _debug_report(tmp, rc)
        if not _rmtree(tmp):
            CLEANUP_FAILURES.append(tmp)
            print(f"HARNESS CLEANUP ERROR: the verifier has finished (its result is unaffected) but the temporary clone could not be removed after {CLEANUP_ATTEMPTS} attempts: {tmp}", file=sys.stderr)
            if _debug():
                _debug_report(tmp, rc)
    return rc


if __name__ == "__main__":
    if len(sys.argv) < 2: raise SystemExit(__doc__)
    raise SystemExit(run(sys.argv[1], *sys.argv[2:]))
