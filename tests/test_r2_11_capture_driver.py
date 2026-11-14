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


def test_resolve_capture_argv_uses_explicit_interpreter_not_literal_python():
    """Regression test for the 9009 ('Python non e` stato trovato...')
    failure: under the old shell=True + raw-string invocation, the literal
    token 'python' was resolved by cmd.exe's PATH search, which on the real
    host hit the Microsoft Store Python app-execution-alias stub instead of
    the already-running Anaconda interpreter. The fix must build an argv
    list whose first element is the resolved interpreter (sys.executable,
    or an explicitly supplied resolved interpreter), never the bare string
    'python', and the resulting argv must contain no stray 'python' token
    anywhere else."""
    ap = drv.load_json(drv.ACQUISITION_PLAN)
    fake_interpreter = r"C:\Users\alpel\anaconda3\python.exe"

    for wl_id, cmd in ap["planned_capture_commands_windows_powershell"].items():
        assert cmd.split()[0] == "python", (
            f"frozen command for {wl_id} unexpectedly does not start with the "
            "literal 'python' token this test is regression-testing against"
        )

        argv = drv.resolve_capture_argv(cmd, python_executable=fake_interpreter)

        assert isinstance(argv, list)
        assert argv[0] == fake_interpreter, (
            f"{wl_id}: expected argv[0] to be the explicitly resolved "
            f"interpreter {fake_interpreter!r}, got {argv[0]!r}"
        )
        assert "python" not in argv[1:], (
            f"{wl_id}: no token after the interpreter should be the bare, "
            f"PATH-dependent string 'python': {argv}"
        )
        # None of the frozen scientific parameters may be altered by the
        # interpreter-resolution rewrite.
        assert argv[1:5] == ["-m", "argine", "research", "trace"]
        assert "--expert-store" in argv
        assert "4G" in argv
        assert "--ctx" in argv and "4096" in argv
        assert "--threads" in argv and "4" in argv
        assert "--llama-arg=--threads-batch" in argv
        assert "--seed" in argv and "42" in argv


def test_resolve_capture_argv_defaults_to_sys_executable(monkeypatch):
    """With no interpreter explicitly supplied, resolve_capture_argv must use
    sys.executable (the already-running interpreter), never shell out to a
    bare 'python' that depends on PATH resolution."""
    ap = drv.load_json(drv.ACQUISITION_PLAN)
    any_cmd = next(iter(ap["planned_capture_commands_windows_powershell"].values()))

    fake_sys_executable = r"C:\fake\env\python.exe"
    monkeypatch.setattr(drv.sys, "executable", fake_sys_executable)

    argv = drv.resolve_capture_argv(any_cmd)
    assert argv[0] == fake_sys_executable


def test_resolve_capture_argv_refuses_unexpected_leading_executable():
    """If a frozen command string were ever to start with something other
    than the literal 'python' token this fix was written for, the driver
    must fail loudly rather than silently trusting shell PATH resolution for
    an unrecognized executable."""
    import pytest

    with pytest.raises(ValueError):
        drv.resolve_capture_argv("notpython -m argine research trace", python_executable="X")


def test_resolve_capture_argv_expands_percent_style_env_vars_in_out_path():
    """The frozen --out path contains a Windows %LOCALAPPDATA%-style
    reference. Under the old shell=True invocation, cmd.exe expanded this
    automatically; with shell=False (argv list, no shell), the driver itself
    must expand it so the resolved output path is unchanged."""
    import os

    os.environ["LOCALAPPDATA"] = r"C:\Users\alpel\AppData\Local"
    ap = drv.load_json(drv.ACQUISITION_PLAN)
    any_wl, any_cmd = next(iter(ap["planned_capture_commands_windows_powershell"].items()))

    argv = drv.resolve_capture_argv(any_cmd, python_executable="X")
    out_idx = argv.index("--out")
    out_path = argv[out_idx + 1]
    assert "%LOCALAPPDATA%" not in out_path
    assert "AppData" in out_path


def test_run_capture_invokes_subprocess_with_shell_false_and_logged_argv(monkeypatch, capsys):
    """run_capture() must invoke subprocess.run with an argv list and
    shell=False (never shell=True with a raw PATH-dependent string), and
    must print the exact argv before executing it."""
    import subprocess as _subprocess

    captured = {}

    class _FakeCompletedProcess:
        returncode = 0
        stdout = ""
        stderr = ""

    def _fake_run(argv, shell=None, cwd=None, capture_output=None, text=None, env=None):
        captured["argv"] = argv
        captured["shell"] = shell
        captured["env"] = env
        return _FakeCompletedProcess()

    monkeypatch.setattr(_subprocess, "run", _fake_run)
    monkeypatch.setattr(drv, "subprocess", _subprocess)
    monkeypatch.setenv("ARGINE_LLAMA_DIR", r"C:\llama-research-v2")

    ap = drv.load_json(drv.ACQUISITION_PLAN)
    any_wl, any_cmd = next(iter(ap["planned_capture_commands_windows_powershell"].items()))

    drv.run_capture(any_wl, any_cmd)

    assert captured["shell"] is False
    assert isinstance(captured["argv"], list)
    assert captured["argv"][0] != "python"
    assert captured["env"]["ARGINE_LLAMA_DIR"] == r"C:\llama-research-v2"

    out = capsys.readouterr().out
    assert "[argv]" in out
    assert "ARGINE_LLAMA_DIR" in out
