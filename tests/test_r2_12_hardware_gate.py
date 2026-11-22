"""Tests for the frozen R2-12 hardware_contract.json pre-run hardware gate design."""
import json
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
HW_PATH = REPO_ROOT / "experiments" / "R2-12-runtime-validation" / "hardware_contract.json"
MACHINE_PATH = REPO_ROOT / "experiments" / "R2-12-runtime-validation" / "machine_state_contract.json"


def _load_hw():
    return json.loads(HW_PATH.read_text(encoding="utf-8"))


def _load_machine():
    return json.loads(MACHINE_PATH.read_text(encoding="utf-8"))


def test_model_and_runtime_identity_reused_from_r2_11():
    hw = _load_hw()
    identity = hw["fixed_identity_known_at_phase0"]
    assert "Reused verbatim from" in identity["model_identity"]["sha256_source"]
    assert "Reused verbatim from" in identity["runtime_identity"]["sha256_source"]
    assert len(identity["model_identity"]["sha256"]) == 64
    assert len(identity["runtime_identity"]["sha256"]) == 64


def test_runtime_and_model_hash_pins_match_between_sections():
    hw = _load_hw()
    identity = hw["fixed_identity_known_at_phase0"]
    stamp_reqs = hw["fields_to_be_captured_and_stamped_at_first_real_run"]
    assert stamp_reqs["runtime_binary_sha256_must_equal"] == identity["runtime_identity"]["sha256"]
    assert stamp_reqs["model_sha256_must_equal"] == identity["model_identity"]["sha256"]


def test_gate_fails_closed_on_any_mismatch():
    hw = _load_hw()
    rule = hw["no_silent_hardware_change_rule"]
    assert "fails the pre-run gate closed" in rule
    assert "do not silently proceed" in rule


def test_new_protocol_version_triggers_are_the_full_identity_set():
    hw = _load_hw()
    triggers = hw["new_protocol_version_required_if"]
    assert len(triggers) == 7
    joined = " ".join(triggers)
    for term in ("cpu_model", "physical_ram_bytes", "gpu_or_igpu", "storage_device_hosting_expert_store", "os_version", "runtime_binary_sha256", "model_sha256"):
        assert term in joined


def test_hardware_stamp_schema_has_eight_fields():
    hw = _load_hw()
    schema = hw["fields_to_be_captured_and_stamped_at_first_real_run"]["schema"]
    assert len(schema) == 8
    assert "power_mode" in schema


def test_power_mode_checked_against_machine_state_not_hardware_stamp():
    hw = _load_hw()
    rule = hw["no_silent_hardware_change_rule"]
    assert "machine_state_contract.json" in rule


def test_chrome_must_be_fully_closed_for_publication_quality_runs():
    ms = _load_machine()
    assert "fully closed" in ms["chrome_policy"]["requirement"]
    assert "non_publication_quality=true" in ms["chrome_policy"]["exploratory_runs"]


def test_power_saver_explicitly_forbidden_for_publication_runs():
    ms = _load_machine()
    conditions = " ".join(ms["required_conditions_for_publication_quality_runs"])
    assert "Power saver" in conditions


def test_non_machine_checkable_conditions_become_operator_attestation():
    ms = _load_machine()
    assert "operator_attested_clean_environment" in ms["pre_run_self_check"]
