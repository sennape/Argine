"""R2-BRIDGE-01 Phase-0 contract validation: the contract must validate
cleanly and its digest must recompute identically, and upstream R2-09B/
R2-10 digests referenced by it must not have drifted."""
from __future__ import annotations

from argine.research2.r2_bridge01 import contract as c


def test_contract_validates_with_no_errors():
    errors = c.validate_contract()
    assert errors == [], errors


def test_contract_digest_recomputes_identically():
    d1 = c.compute_contract_digest()
    d2 = c.compute_contract_digest()
    assert d1 == d2


def test_upstream_digests_pinned_and_unmodified():
    import sys
    sys.path.insert(0, str(c.repo_root()))
    from argine.research2.r2_09b.contract import compute_contract_digest as r2_09b_digest
    from argine.research2.r2_10.contract import compute_contract_digest as r2_10_digest
    assert r2_09b_digest(c.r2_09_dir()) == c.R2_09B_CONTRACT_DIGEST
    assert r2_10_digest(c.r2_10_dir()) == c.R2_10_PHASE0_CONTRACT_DIGEST


def test_r2_10_closure_untouched():
    cl = c.load_json(c.r2_10_dir() / "PHASE3L-FINAL-CLOSURE-RECORD.json")
    assert cl["r2_11_proposed"] is False
    assert cl["r2_11_started"] is False
    assert cl["final_status"] == "R2-10 CLOSED"


def test_eval_manifest_still_locked():
    em = c.load_json(c.r2_09_dir() / "eval_manifest.json")
    assert em["eval_visibility_state"] == "EVAL_LOCKED"
