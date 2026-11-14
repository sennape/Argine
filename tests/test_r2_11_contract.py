"""Phase-0 contract validation tests for R2-11."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from argine.research2.r2_11 import contract as c


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


def test_upstream_r2_bridge01_digest_pinned():
    import json
    bridge_contract_dir = c.r2_bridge01_dir()
    from argine.research2.r2_bridge01.contract import compute_contract_digest as bridge_digest
    assert bridge_digest(bridge_contract_dir) == c.R2_BRIDGE01_CONTRACT_DIGEST


def test_r2_10_historical_decision_untouched():
    closure = c.load_json(c.r2_10_dir() / "PHASE3L-FINAL-CLOSURE-RECORD.json")
    assert closure["r2_11_proposed"] is False
    assert closure["final_status"] == "R2-10 CLOSED"


def test_r2_bridge01_closure_is_bridge_positive():
    closure = c.load_json(c.r2_bridge01_dir() / "BRIDGE_EVALUATED_CLOSED.json")
    assert closure["classification"] == "BRIDGE_POSITIVE"
    assert closure["state"] == "BRIDGE_EVALUATED_CLOSED"
