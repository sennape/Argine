#!/usr/bin/env python3
"""Run every verifier of the consolidated repository and report each result.

    python shared/tooling/verify_all.py [--skip-unittest]

Frozen verifiers run unmodified, each in a disposable clone of HEAD (shared/tooling/run_frozen_verifier.py), so they cannot dirty this checkout; the consolidation check (last) then confirms the checkout is still clean. Exit code 0 only if all pass.
The R1/R2 unit-test suite contains environment-dependent tests (built runtime, raw data); it is run last and
reported separately, and is excluded by --skip-unittest."""
from __future__ import annotations

import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "shared" / "tooling"))
import run_frozen_verifier as rfv  # noqa: E402

FROZEN = [
    ("R3 closure", "experiments/R3-final-closure/closure.py", ["verify"]),
    ("R3 paper", "paper/research3/scripts/verify_paper.py", []),
    ("R4 paper", "paper/research4/scripts/verify_paper.py", []),
    ("R4 Phase 0", "experiments/R4-00-causal-eviction-distillation/phase0.py", ["verify"]),
    ("R4 Phase 1", "experiments/R4-01-cfdc-training-validation/phase1.py", ["verify"]),
    ("R4 Phase 2", "experiments/R4-02-cfdc-prospective-eval/phase2.py", ["verify"]),
    ("R5 Phase 0", "experiments/R5-00-exposed-transfer-stall/phase0.py", ["verify"]),
    ("R5 Phase 1a", "experiments/R5-01-ets-implementation/phase1a.py", ["verify"]),
    ("R2 paper freeze", "paper/research2/scripts/verify_paper_freeze.py", []),
]
PLAIN = [
    ("R1 registry", ["tools/index_experiments.py", "--check"]),
    ("README index", ["strumenti/controlla_readme.py"]),
    ("links", ["tools/check_links.py"]),
    ("consolidation", ["shared/tooling/verify_consolidation.py"]),
]


def main() -> int:
    try:
        rfv.preflight()
    except rfv.DirtyCaller as e:
        print("VERIFICATION REFUSED:", e, file=sys.stderr)
        return 2
    results = []
    for name, script, args in FROZEN:
        results.append((name, rfv.run(script, *args)))
    for name, cmd in PLAIN:
        results.append((name, subprocess.run([sys.executable, *cmd], cwd=str(ROOT)).returncode))
    if "--skip-unittest" not in sys.argv:
        results.append(("unittest (R1/R2, env-dependent)", subprocess.run([sys.executable, "-m", "unittest", "discover", "-s", "tests"], cwd=str(ROOT)).returncode))
    order = ["R3 closure", "R3 paper", "R4 paper", "R4 Phase 0", "R4 Phase 1", "R4 Phase 2", "R5 Phase 0", "R5 Phase 1a", "R1 registry", "R2 paper freeze", "README index", "links", "consolidation"]
    results.sort(key=lambda r: order.index(r[0]) if r[0] in order else len(order))
    print()
    for name, rc in results:
        print(("PASS " if rc == 0 else "FAIL ") + name)
    if rfv.CLEANUP_FAILURES:
        print("\nHARNESS CLEANUP ERROR (not a verifier failure): temporary clones left behind, remove them manually:")
        for t in rfv.CLEANUP_FAILURES: print("  " + t)
    if not all(rc == 0 for _, rc in results): return 1
    return 3 if rfv.CLEANUP_FAILURES else 0  # 3: every verifier passed but the harness could not clean up


if __name__ == "__main__":
    raise SystemExit(main())
