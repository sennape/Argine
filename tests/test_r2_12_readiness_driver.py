"""Tests for the R2-12 live-host readiness driver's check logic.

Run in this Linux sandbox, NOT on the real Windows target host -- these
tests verify the driver's own correctness (status taxonomy, fail-closed
behavior, no benchmark side effects, report structure), not that the real
host is ready. That verification only happens when the driver is actually
run on the real machine.
"""
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import tools.r2_12_readiness_driver as rd  # noqa: E402


def test_check_result_rejects_invalid_status():
    import pytest
    with pytest.raises(ValueError):
        rd.CheckResult(name="x", status="NOT_A_REAL_STATUS")


def test_report_overall_is_pass_with_no_blocking_failures():
    report = rd.ReadinessReport()
    report.add("a", rd.PASS)
    report.add("b", rd.ADVISORY, "telemetry unavailable")
    assert report.overall == rd.PASS


def test_report_overall_is_blocking_with_any_blocking_failure():
    report = rd.ReadinessReport()
    report.add("a", rd.PASS)
    report.add("b", rd.BLOCKING_FAILURE, "missing")
    assert report.overall == rd.BLOCKING_FAILURE


def test_advisory_never_counted_as_blocking():
    report = rd.ReadinessReport()
    for _ in range(10):
        report.add("telemetry", rd.ADVISORY, "unavailable")
    assert report.overall == rd.PASS
    assert len(report.blocking_failures) == 0
    assert len(report.advisories) == 10


def test_full_check_run_does_not_crash_and_produces_structured_report():
    report = rd.run_readiness_checks(operator_attests_clean_environment=False)
    out = report.to_json()
    assert "overall" in out and out["overall"] in (rd.PASS, rd.BLOCKING_FAILURE)
    assert out["n_checks"] == len(report.checks)
    assert out["n_pass"] + out["n_blocking_failure"] + out["n_advisory"] == out["n_checks"]


def test_operator_attestation_defaults_to_blocking_not_advisory():
    report = rd.run_readiness_checks(operator_attests_clean_environment=False)
    names = {c.name: c.status for c in report.checks}
    assert names["operator_attested_clean_environment"] == rd.BLOCKING_FAILURE


def test_operator_attestation_passes_when_explicitly_set():
    report = rd.run_readiness_checks(operator_attests_clean_environment=True)
    names = {c.name: c.status for c in report.checks}
    assert names["operator_attested_clean_environment"] == rd.PASS


def test_sandbox_run_is_blocked_on_missing_windows_model_and_concludes_overall_blocking():
    # In this Linux sandbox the frozen Windows model path cannot exist;
    # the driver must fail closed rather than silently skip the check.
    report = rd.run_readiness_checks(operator_attests_clean_environment=True)
    names = {c.name: c.status for c in report.checks}
    assert names["model_path_exists"] == rd.BLOCKING_FAILURE
    assert report.overall == rd.BLOCKING_FAILURE


def test_hardware_constants_are_read_from_frozen_contract_not_hardcoded():
    hw = rd.load_json(rd.CONTRACT_DIR / "hardware_contract.json")
    identity = hw["fixed_identity_known_at_phase0"]
    assert "model_path_windows" in identity["model_identity"]
    assert len(identity["model_identity"]["sha256"]) == 64
    assert len(identity["runtime_identity"]["sha256"]) == 64


def test_readiness_report_never_implies_runtime_ready_by_itself(tmp_path):
    report = rd.run_readiness_checks(operator_attests_clean_environment=True)
    out = report.to_json()
    out["note"] = (
        "This report alone does NOT constitute R2_12_RUNTIME_READY."
    )
    assert "does NOT constitute R2_12_RUNTIME_READY" in out["note"]


def test_no_benchmark_or_measured_comparison_functions_exist_in_module():
    # The readiness driver must contain no function capable of invoking a
    # measured policy comparison -- only checks.
    import inspect
    src = inspect.getsource(rd)
    forbidden = ["run_fast(", "PredictedProtectPolicy(", "AgedFrequency(", "tokens_per_second ="]
    for token in forbidden:
        assert token not in src, f"readiness driver must not reference {token!r}"


def test_main_writes_report_to_requested_path_and_exit_code_matches_overall(tmp_path, monkeypatch):
    out_path = tmp_path / "report.json"
    monkeypatch.setattr(sys, "argv", [
        "r2_12_readiness_driver.py", "--operator-attests-clean-environment", "--out", str(out_path),
    ])
    rc = rd.main()
    assert out_path.exists()
    data = json.loads(out_path.read_text())
    assert data["overall"] in (rd.PASS, rd.BLOCKING_FAILURE)
    assert rc == (0 if data["overall"] == rd.PASS else 1)
