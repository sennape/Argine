"""Smoke tests for the R2-11 Phase 1 capture driver tool (tools/r2_11_capture_driver.py).

These test only the driver's pure/importable logic (hashing, JSON loading,
preflight structure) -- they never invoke a real capture, since that
requires the real Windows host, model, and runtime.
"""
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT))
sys.path.insert(0, str(REPO_ROOT / "tools"))

import r2_11_capture_driver as drv  # noqa: E402


def test_driver_module_imports():
    assert hasattr(drv, "main")
    assert hasattr(drv, "host_preflight")
    assert hasattr(drv, "validate_capture")
    assert hasattr(drv, "run_capture")


def test_sha256_file_matches_hashlib(tmp_path):
    import hashlib
    p = tmp_path / "x.bin"
    p.write_bytes(b"hello r2-11" * 1000)
    expected = hashlib.sha256(p.read_bytes()).hexdigest()
    assert drv.sha256_file(p) == expected


def test_acquisition_plan_and_eval_manifest_load_cleanly():
    ap = drv.load_json(drv.ACQUISITION_PLAN)
    em = drv.load_json(drv.EVAL_MANIFEST)
    assert ap["state"] == "R2_11_ACQUISITION_PLANNED"
    assert len(em["eval_workload_specs"]) == 12


def test_host_preflight_reports_structured_failures_without_crashing():
    ap = drv.load_json(drv.ACQUISITION_PLAN)
    report = drv.host_preflight(ap)
    assert "checks" in report
    assert "ok" in report
    names = {c["name"] for c in report["checks"]}
    assert "model_exists" in names
    assert "ARGINE_LLAMA_DIR_set" in names
    assert "argine_importable" in names
    assert "sufficient_free_ram" in names
    assert "no_concurrent_writer" in names
    assert sum(1 for n in names if n.startswith("output_unused::")) == 12
