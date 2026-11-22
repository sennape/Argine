"""R2-10 Phase-0 contract validation tests (contract/freeze only).

No DEV predictor tournament, no EVAL access, no R2-11/R2-12 work.
"""
from __future__ import annotations

import json
from pathlib import Path

from argine.research2.r2_10.contract import (
    CONTRACT_VERSION,
    R2_09A_DEV_CORPUS_DIGEST,
    R2_09A_TOURNAMENT_DIGEST,
    R2_09B_CONTRACT_DIGEST,
    compute_contract_digest,
    default_contract_dir,
    r2_09a_dir,
    r2_09b_dir,
    validate_contract,
)

ROOT = Path(__file__).resolve().parents[1]
CONTRACT_DIR = default_contract_dir()


def _read(name: str):
    return json.loads((CONTRACT_DIR / name).read_text(encoding="utf-8"))


def test_contract_loads_and_validates():
    errors = validate_contract(CONTRACT_DIR)
    assert errors == [], errors


def test_contract_version():
    c = _read("contract.json")
    assert c["contract_version"] == CONTRACT_VERSION == "r2-10-phase0-contract-v1.0"


def test_contract_digest_deterministic_and_recorded():
    d1 = compute_contract_digest(CONTRACT_DIR)
    d2 = compute_contract_digest(CONTRACT_DIR)
    assert d1 == d2
    c = _read("contract.json")
    assert c["contract_digest"] == d1
    freeze = json.loads((CONTRACT_DIR / "R2-10-PHASE0-FREEZE.json").read_text(encoding="utf-8"))
    assert freeze["phase0_contract_digest"] == d1


def test_upstream_r2_09b_binding_not_drifted():
    c = _read("contract.json")
    assert c["upstream"]["r2_09b_contract_digest"] == R2_09B_CONTRACT_DIGEST
    from argine.research2.r2_09b.contract import compute_contract_digest as r2_09b_digest

    assert r2_09b_digest(r2_09b_dir()) == R2_09B_CONTRACT_DIGEST


def test_upstream_r2_09a_binding_not_drifted():
    f = json.loads((r2_09a_dir() / "R2-09A-FREEZE.json").read_text(encoding="utf-8"))
    assert f["tournament_digest"] == R2_09A_TOURNAMENT_DIGEST
    assert f["dev_corpus_digest"] == R2_09A_DEV_CORPUS_DIGEST


def test_dev_corpus_binding_matches_r2_09a_cells():
    dep = _read("dev_eval_protocol.json")
    dm = json.loads((r2_09a_dir() / "dev_corpus_manifest.json").read_text(encoding="utf-8"))
    assert dm["dev_corpus_digest"] == R2_09A_DEV_CORPUS_DIGEST
    cells = sorted(i["cell_id"] for i in dm["selected_instances"])
    assert cells == sorted(dep["dev_corpus"]["cells"])
    assert len(cells) == 8


def test_eval_firewall_untouched():
    em = json.loads((r2_09b_dir() / "eval_manifest.json").read_text(encoding="utf-8"))
    assert em["eval_visibility_state"] == "EVAL_LOCKED"
    assert em.get("eval_results") in (None, [], {})
    assert em.get("opened_utc") is None


def test_causal_boundary_table_disjoint_and_nonempty():
    cb = _read("causal_boundary.json")
    allowed_ids = {a["id"] for a in cb["allowed"]}
    forbidden_ids = {f["id"] for f in cb["forbidden"]}
    assert allowed_ids and forbidden_ids
    assert not (allowed_ids & forbidden_ids)
    assert "target_event_itself" in forbidden_ids
    assert "same_step_later_layer" in forbidden_ids
    assert "same_layer_future" in forbidden_ids


def test_output_contract_cardinality_and_tie_break_declared():
    oc = _read("output_contract.json")
    assert "exactly k_{t,l}" in oc["cardinality_rule"]
    assert oc["tie_breaking"]["rule"]
    assert oc["insufficient_history_cold_start"]["rule"]


def test_predictor_space_finite_grids_and_no_privileges():
    ps = _read("predictor_space.json")
    fams = {f["family_id"]: f for f in ps["families"]}
    assert {"global_frequency", "last_observation", "recency_window", "markov_transition",
            "decayed_transition", "recency_frequency_transition_blend"}.issubset(fams)
    for fid, f in fams.items():
        assert f["causal_state"]
        hp = f["hyperparameters"]
        if hp.get("tunable"):
            assert hp.get("search_space")
    shuffled = ps["null_trivial_baselines"]["analytical_reference_only"]
    assert any(s["family_id"] == "shuffled_null" for s in shuffled)
    assert all(s["classification"] == "ANALYTICAL_REFERENCE_ONLY" for s in shuffled)
    assert "shuffled_null" not in ps["null_trivial_baselines"]["causal_runtime_candidates"]


def test_selection_rule_dev_only_equal_weight_frozen_before_results():
    sr = _read("selection_rule.json")
    assert sr["selected_from"] == "DEV_ONLY"
    assert "equal weight" in json.dumps(sr["aggregation"]).lower()
    assert sr["tie_breaking"]
    assert "shuffled_null" not in sr["pool"] or "never" in sr["pool"].lower()


def test_decision_rules_categories_and_forbidden_claims():
    dr = _read("decision_rules.json")
    cats = {c["category"] for c in dr["interpretation_categories"]}
    assert cats == {"A", "B", "C"}
    blob = json.dumps(dr["forbidden_claims_from_r2_10"]).lower()
    assert "runtime speedup" in blob
    assert "retention improvement" in blob
    assert dr["leakage_audit"]["status"].startswith("FIRST_CLASS")
    assert dr["determinism_audit"]["status"] == "FIRST_CLASS_TEST"


def test_r2_10_does_not_claim_o_b_is_o_star():
    c = _read("contract.json")
    assert c["case_c_binding"]["o_b_proven_byte_optimal"] is False
