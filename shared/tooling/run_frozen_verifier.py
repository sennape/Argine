#!/usr/bin/env python3
"""Run a frozen verifier UNMODIFIED with the Git-object equivalence layer active.

    python shared/tooling/run_frozen_verifier.py experiments/R3-final-closure/closure.py verify

The layer (shared/tooling/gitcompat/sitecustomize.py) answers the verifier's historical-Git queries from
manifests/git-object-equivalence.json, and only when the current Git objects equal the recorded originals.
Child interpreters (a verifier that runs another verifier) inherit it through PYTHONPATH."""
from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]


def run(script: str, *args: str) -> int:
    env = dict(os.environ); env["ARGINE_GITCOMPAT_ROOT"] = str(ROOT)
    env["PYTHONPATH"] = str(ROOT / "shared" / "tooling" / "gitcompat") + os.pathsep + env.get("PYTHONPATH", "")
    return subprocess.run([sys.executable, str(ROOT / script), *args], cwd=str(ROOT), env=env).returncode


if __name__ == "__main__":
    if len(sys.argv) < 2: raise SystemExit(__doc__)
    raise SystemExit(run(sys.argv[1], *sys.argv[2:]))
