"""Phase-0 contract validation tests for R2-12."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from argine.research2.r2_12 import contract as c


def test_contract_validates_clean():
    errors = c.validate_contract()
    assert errors == [], errors


def test_contract_digest_is_deterministic():
    d1 = c.compute_contract_digest()
    d2 = c.compute_contract_digest()
    assert d1 == d2
    assert len(d1) == 64


def test_contract_digest_matches_frozen_record():
    info = c.assert_valid()
    assert info["valid"] is True


def test_upstream_r2_11_phase0_digest_pinned():
    from argine.research2.r2_11.contract import compute_contract_digest as r2_11_digest
    assert r2_11_digest(c.r2_11_dir()) == c.R2_11_PHASE0_CONTRACT_DIGEST


def test_r2_10_historical_decision_untouched():
    closure = c.load_json(c.r2_10_dir() / "PHASE3L-FINAL-CLOSURE-RECORD.json")
    assert closure["r2_11_proposed"] is False
    assert closure["final_status"] == "R2-10 CLOSED"


def test_r2_bridge01_closure_is_bridge_positive_unchanged():
    closure = c.load_json(c.r2_bridge01_dir() / "BRIDGE_EVALUATED_CLOSED.json")
    assert closure["classification"] == "BRIDGE_POSITIVE"


def test_r2_11_closure_is_pass_unchanged():
    closure = c.load_json(c.r2_11_dir() / "R2_11_EVALUATED_CLOSED.json")
    assert closure["classification"] == "R2_11_PASS"
    assert closure["state"] == "R2_11_EVALUATED_CLOSED"


def test_no_r2_12_runtime_result_exists_yet():
    contract = c.load_contract()
    corpus = contract["_runtime_corpus"]
    assert "results" not in corpus or corpus.get("results") is None
    execplan = contract["_execution_plan"]
    assert "results" not in execplan or execplan.get("results") is None


def test_component_files_all_present_on_disk():
    d = c.default_contract_dir()
    for name in c.COMPONENT_FILES:
        assert (d / name).exists(), f"missing component file: {name}"


def test_readme_component_is_text_not_json():
    d = c.default_contract_dir()
    obj = c.load_component(d, "README.md")
    assert isinstance(obj, str)
