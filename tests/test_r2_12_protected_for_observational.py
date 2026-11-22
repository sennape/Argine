"""R2-12 -- regression: RetentionPolicy::protected_for() is observational only.

This phase added a minimal, read-only accessor to the frozen retention-
policy module (argine-retention-policy.h/.cpp, commit c2f9a68 base; see
RETENTION-POLICY-TRACE-INSTRUMENTATION.md for the full rationale) so the
real-runtime trace can report the actual protected set PredictedProtectPolicy
uses, instead of a structurally-empty placeholder. This test proves that
accessor cannot influence any decision: it compiles and runs
patches/argine-protected-for-observational-test.cpp, a standalone C++ probe
that runs the real PredictedProtectPolicy class over several identical
event sequences twice from fresh construction -- once never calling
protected_for(), once calling it repeatedly (before AND after every
access(), for layers never even touched, discarding every return value) --
and asserts the resulting evicted/missed sequences are byte-for-byte
identical. No real model, no .psxs store, no R2-12 counted run: a
standalone g++ build and run of one C++ translation unit, exactly the
pattern tests/test_r2_12_retention_policy_conformance.py already uses for
other standalone probes.
"""
from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
PATCHES = ROOT / "patches"
PROBE_SRC = PATCHES / "argine-protected-for-observational-test.cpp"
POLICY_CPP = PATCHES / "argine-retention-policy.cpp"
POLICY_H = PATCHES / "argine-retention-policy.h"


def _have_gxx() -> bool:
    return shutil.which("g++") is not None


@pytest.mark.skipif(not _have_gxx(), reason="g++ not available in this environment")
def test_protected_for_is_observational_only(tmp_path):
    assert PROBE_SRC.exists(), f"missing probe source: {PROBE_SRC}"
    assert POLICY_CPP.exists() and POLICY_H.exists()

    binary = tmp_path / "protected_for_observational_test"
    compile_cmd = [
        "g++", "-std=c++17", "-I", str(PATCHES),
        str(POLICY_CPP), str(PROBE_SRC),
        "-o", str(binary),
    ]
    result = subprocess.run(compile_cmd, capture_output=True, text=True, timeout=60)
    assert result.returncode == 0, (
        f"probe failed to compile:\nstdout={result.stdout}\nstderr={result.stderr}"
    )

    run = subprocess.run([str(binary)], capture_output=True, text=True, timeout=30)
    assert run.returncode == 0, (
        f"protected_for() observational-only probe FAILED (decision sequence "
        f"changed by accessor calls):\nstdout={run.stdout}\nstderr={run.stderr}"
    )
    assert "RISULTATO OK: 0 fallimenti" in run.stdout
    assert "[FAIL]" not in run.stdout
    # Every scenario in the probe must have actually run and reported ok,
    # not just an empty/vacuous pass.
    assert run.stdout.count("[ok] scenario") >= 3
