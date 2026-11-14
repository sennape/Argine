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


# ---------------------------------------------------------------------------
# Regression tests for the RAM-preflight fix: the driver must reuse Argine's
# real memory planner (the one `argine research trace` itself uses) instead
# of a model-file-size heuristic. Uses the repo's own synthetic MoE fixture
# (tests/make_fixtures.make_moe_30b), never the real 18.6 GB model file.
# ---------------------------------------------------------------------------

def _make_synthetic_moe_model(tmp_path):
    sys.path.insert(0, str(REPO_ROOT / "tests"))
    import make_fixtures
    from argine.gguf import read_gguf

    p = tmp_path / "moe-30B-A3B-Q4_K_M.gguf"
    make_fixtures.make_moe_30b(p)
    return read_gguf(str(p)), p


def test_plan_feasibility_check_passes_at_11gib_free_for_frozen_4gib_expert_store(tmp_path, monkeypatch):
    from argine import hardware as hwmod
    from argine.util import GB

    model, model_path = _make_synthetic_moe_model(tmp_path)

    hw = hwmod.HardwareReport()
    hw.ram_total = 16 * GB
    hw.ram_available = int(11.02 * GB)  # matches the real host's reported free RAM
    hw.cpu_cores_physical = 4
    hw.cpu_cores_logical = 8
    hw.backends = ["cpu"]
    monkeypatch.setattr(hwmod, "detect", lambda extra_disk_paths=None: hw)
    monkeypatch.setattr("argine.gguf.find_gguf", lambda p: model_path)
    monkeypatch.setattr("argine.gguf.read_gguf", lambda p: model)

    ap = drv.load_json(drv.ACQUISITION_PLAN)
    result = drv.plan_feasibility_check(ap, model_path)
    assert result["method"] == "planner"
    assert result["feasible"] is True
    # The full 18.6 GB model-file size must never be treated as required
    # resident RAM: the plan's own memory estimate must be far smaller than
    # that, because expert weights are capped at the frozen 4 GiB budget.
    assert result["estimate_total"] < 8 * GB


def test_plan_feasibility_check_fails_on_clearly_insufficient_ram(tmp_path, monkeypatch):
    from argine import hardware as hwmod
    from argine.util import GB

    model, model_path = _make_synthetic_moe_model(tmp_path)

    hw = hwmod.HardwareReport()
    hw.ram_total = 16 * GB
    hw.ram_available = 2 * GB  # clearly insufficient even for the 4 GiB expert-store budget
    hw.cpu_cores_physical = 4
    hw.cpu_cores_logical = 8
    hw.backends = ["cpu"]
    monkeypatch.setattr(hwmod, "detect", lambda extra_disk_paths=None: hw)
    monkeypatch.setattr("argine.gguf.find_gguf", lambda p: model_path)
    monkeypatch.setattr("argine.gguf.read_gguf", lambda p: model)

    ap = drv.load_json(drv.ACQUISITION_PLAN)
    result = drv.plan_feasibility_check(ap, model_path)
    assert result["method"] == "planner"
    assert result["feasible"] is False
    assert result["failure_reason"]


def test_full_model_file_size_not_treated_as_resident_ram(tmp_path, monkeypatch):
    """The 18.6 GB GGUF file size must never appear as a RAM requirement:
    the real planner's expert-cache line item equals the frozen 4 GiB
    budget, not the file size."""
    from argine import hardware as hwmod
    from argine.util import GB

    model, model_path = _make_synthetic_moe_model(tmp_path)

    hw = hwmod.HardwareReport()
    hw.ram_total = 16 * GB
    hw.ram_available = int(11.02 * GB)
    hw.cpu_cores_physical = 4
    hw.cpu_cores_logical = 8
    hw.backends = ["cpu"]
    monkeypatch.setattr(hwmod, "detect", lambda extra_disk_paths=None: hw)
    monkeypatch.setattr("argine.gguf.find_gguf", lambda p: model_path)
    monkeypatch.setattr("argine.gguf.read_gguf", lambda p: model)

    ap = drv.load_json(drv.ACQUISITION_PLAN)
    result = drv.plan_feasibility_check(ap, model_path)
    assert result["method"] == "planner"
    assert result["estimate_total"] < model_path.stat().st_size


def test_host_preflight_passes_sufficient_free_ram_check_via_real_planner(tmp_path, monkeypatch):
    """End-to-end: host_preflight's sufficient_free_ram check must pass for
    ~11 GiB free RAM once routed through the real planner, not the old
    1.5x-model-size heuristic."""
    from argine import hardware as hwmod
    from argine.util import GB

    model, model_path = _make_synthetic_moe_model(tmp_path)

    hw = hwmod.HardwareReport()
    hw.ram_total = 16 * GB
    hw.ram_available = int(11.02 * GB)
    hw.cpu_cores_physical = 4
    hw.cpu_cores_logical = 8
    hw.backends = ["cpu"]
    monkeypatch.setattr(hwmod, "detect", lambda extra_disk_paths=None: hw)
    monkeypatch.setattr("argine.gguf.find_gguf", lambda p: model_path)
    monkeypatch.setattr("argine.gguf.read_gguf", lambda p: model)
    monkeypatch.setattr(drv, "free_ram_bytes", lambda: hw.ram_available)

    ap = drv.load_json(drv.ACQUISITION_PLAN)
    ap = dict(ap)
    ap["model_identity"] = dict(ap["model_identity"])
    ap["model_identity"]["model_path_windows"] = str(model_path)
    ap["model_identity"]["sha256"] = drv.sha256_file(model_path)

    report = drv.host_preflight(ap)
    ram_check = next(c for c in report["checks"] if c["name"] == "sufficient_free_ram")
    assert ram_check["ok"] is True, ram_check["detail"]
