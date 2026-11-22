"""R2-10 Phase-0 contract loading, validation, and deterministic digest.

Phase-0 only: freezes the experimental contract (causal boundary, output
contract, DEV/EVAL binding, predictor candidate grid, selection rule,
acceptance categories) BEFORE any DEV predictor tournament result exists.
Does not run predictors against the full DEV corpus, does not implement
retention (R2-11), does not run R2-12, and does not open EVAL data.
"""
from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any, Dict, List, Optional

CONTRACT_VERSION = "r2-10-phase0-contract-v1.0"
SCHEMA_VERSION = 1

# Upstream frozen references this contract binds to (must not drift).
R2_09B_CONTRACT_DIGEST = "b6274b354e39854effcca1510f0f817a85a162ab80cb6d29a31041a9978a7b28"
R2_09A_TOURNAMENT_DIGEST = "5b600d1451983dabafdfbd6ea6bfdc4c4c3bdcf9327c9ec4d57c25025759f847"
R2_09A_DEV_CORPUS_DIGEST = "f758b77492eebc38bc81ba22d6b06ab8f19138d1099b635821369a4d022d9398"

COMPONENT_FILES = (
    "contract.json",
    "causal_boundary.json",
    "output_contract.json",
    "dev_eval_protocol.json",
    "predictor_space.json",
    "selection_rule.json",
    "decision_rules.json",
)


class ContractValidationError(ValueError):
    pass


def _canon(obj: Any) -> str:
    return json.dumps(obj, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


def sha256_text(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def sha256_file(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def repo_root() -> Path:
    return Path(__file__).resolve().parents[3]


def default_contract_dir() -> Path:
    return repo_root() / "experiments" / "R2-10-working-set-predictor"


def r2_09b_dir() -> Path:
    return repo_root() / "experiments" / "R2-09-prediction-benchmark"


def r2_09a_dir() -> Path:
    return r2_09b_dir() / "r2_09a"


def load_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def load_contract(contract_dir: Optional[Path] = None) -> Dict[str, Any]:
    d = contract_dir or default_contract_dir()
    contract = load_json(d / "contract.json")
    for key, fname in (
        ("causal_boundary", "causal_boundary.json"),
        ("output_contract", "output_contract.json"),
        ("dev_eval_protocol", "dev_eval_protocol.json"),
        ("predictor_space", "predictor_space.json"),
        ("selection_rule", "selection_rule.json"),
        ("decision_rules", "decision_rules.json"),
    ):
        contract[f"_{key}"] = load_json(d / fname)
    return contract


def compute_contract_digest(contract_dir: Optional[Path] = None) -> str:
    """Deterministic digest over the frozen R2-10 Phase-0 contract components."""
    d = contract_dir or default_contract_dir()
    parts: List[Dict[str, Any]] = []
    for name in COMPONENT_FILES:
        obj = load_json(d / name)
        if name == "contract.json":
            obj = dict(obj)
            obj.pop("contract_digest", None)
        parts.append({"name": name, "sha256": sha256_text(_canon(obj)), "content": obj})
    material = {
        "contract_version": CONTRACT_VERSION,
        "schema_version": SCHEMA_VERSION,
        "parts": sorted(parts, key=lambda x: x["name"]),
    }
    return sha256_text(_canon(material))


def _require(cond: bool, msg: str, errors: List[str]) -> None:
    if not cond:
        errors.append(msg)


def validate_contract(contract_dir: Optional[Path] = None) -> List[str]:
    """Return list of validation errors (empty means valid)."""
    d = contract_dir or default_contract_dir()
    errors: List[str] = []
    try:
        c = load_contract(d)
    except Exception as e:  # noqa: BLE001
        return [f"failed to load contract: {e}"]

    cb = c["_causal_boundary"]
    oc = c["_output_contract"]
    dep = c["_dev_eval_protocol"]
    ps = c["_predictor_space"]
    sr = c["_selection_rule"]
    dr = c["_decision_rules"]

    _require(c.get("contract_version") == CONTRACT_VERSION, "contract_version mismatch", errors)
    _require(c.get("schema_version") == SCHEMA_VERSION, "schema_version mismatch", errors)

    # upstream binding must not have drifted
    up = c.get("upstream", {})
    _require(up.get("r2_09b_contract_digest") == R2_09B_CONTRACT_DIGEST, "R2-09B contract digest drifted", errors)
    _require(up.get("r2_09a_freeze_digest") == R2_09A_TOURNAMENT_DIGEST, "R2-09A tournament digest drifted", errors)
    try:
        import sys
        sys.path.insert(0, str(repo_root()))
        from argine.research2.r2_09b.contract import (  # noqa: WPS433
            CONTRACT_DIGEST_V10 as _UNUSED,  # noqa: F401
        )
        from argine.research2.r2_09b.contract import compute_contract_digest as r2_09b_digest
        _require(r2_09b_digest(r2_09b_dir()) == R2_09B_CONTRACT_DIGEST, "R2-09B contract digest no longer recomputes to the pinned value", errors)
    except Exception as e:  # noqa: BLE001
        errors.append(f"could not independently recompute R2-09B digest: {e}")

    r2_09a_freeze = r2_09a_dir() / "R2-09A-FREEZE.json"
    _require(r2_09a_freeze.exists(), "R2-09A-FREEZE.json missing", errors)
    if r2_09a_freeze.exists():
        f = load_json(r2_09a_freeze)
        _require(f.get("tournament_digest") == R2_09A_TOURNAMENT_DIGEST, "R2-09A-FREEZE.json tournament_digest mismatch", errors)
        _require(f.get("dev_corpus_digest") == R2_09A_DEV_CORPUS_DIGEST, "R2-09A-FREEZE.json dev_corpus_digest mismatch", errors)

    # EVAL firewall untouched
    eval_manifest_path = r2_09b_dir() / "eval_manifest.json"
    if eval_manifest_path.exists():
        em = load_json(eval_manifest_path)
        _require(em.get("eval_visibility_state") == "EVAL_LOCKED", "EVAL must remain EVAL_LOCKED", errors)
        _require(not em.get("eval_results"), "eval_results must remain empty", errors)
        _require(em.get("opened_utc") is None, "EVAL must remain unopened", errors)

    # DEV corpus binding: 8 cells, matches dev_eval_protocol.json
    dev_manifest_path = r2_09a_dir() / "dev_corpus_manifest.json"
    _require(dev_manifest_path.exists(), "R2-09A dev_corpus_manifest.json missing", errors)
    if dev_manifest_path.exists():
        dm = load_json(dev_manifest_path)
        _require(dm.get("dev_corpus_digest") == R2_09A_DEV_CORPUS_DIGEST, "dev_corpus_manifest.json digest drifted from pinned value", errors)
        cells = sorted(i["cell_id"] for i in dm.get("selected_instances", []))
        expected_cells = sorted(dep.get("dev_corpus", {}).get("cells", []))
        _require(cells == expected_cells, f"DEV cell set mismatch: {cells} != {expected_cells}", errors)

    # causal boundary table completeness
    _require(len(cb.get("allowed", [])) >= 3, "causal_boundary.allowed too small", errors)
    _require(len(cb.get("forbidden", [])) >= 3, "causal_boundary.forbidden too small", errors)
    allowed_ids = {a["id"] for a in cb.get("allowed", [])}
    forbidden_ids = {f["id"] for f in cb.get("forbidden", [])}
    _require(not (allowed_ids & forbidden_ids), "allowed/forbidden id overlap", errors)

    # output contract determinism/cardinality declarations present
    _require("exactly k_{t,l}" in oc.get("cardinality_rule", "") or "exactly k_{t,l}" in str(oc.get("cardinality_rule")), "cardinality_rule must require exactly k_tl", errors)
    _require(oc.get("tie_breaking", {}).get("rule"), "tie_breaking rule missing", errors)
    _require(oc.get("insufficient_history_cold_start", {}).get("rule"), "cold-start rule missing", errors)

    # predictor space: finite grids, causal-only, deterministic, no privileged family
    fams = ps.get("families", [])
    fam_ids = [f["family_id"] for f in fams]
    _require(len(fam_ids) == len(set(fam_ids)), "duplicate predictor family_id", errors)
    for f in fams:
        _require(f.get("causal_state"), f"family {f.get('family_id')} missing causal_state", errors)
        _require("hyperparameters" in f, f"family {f.get('family_id')} missing hyperparameters", errors)
        hp = f["hyperparameters"]
        if hp.get("tunable"):
            _require(isinstance(hp.get("search_space"), dict) and hp["search_space"], f"family {f.get('family_id')} tunable but no finite search_space", errors)
    shuffled = ps.get("null_trivial_baselines", {}).get("analytical_reference_only", [])
    _require(any(s.get("family_id") == "shuffled_null" for s in shuffled), "shuffled_null analytical reference missing", errors)
    _require(all(s.get("classification") == "ANALYTICAL_REFERENCE_ONLY" for s in shuffled), "analytical-only baselines must be classified ANALYTICAL_REFERENCE_ONLY", errors)
    pool_blob = _canon(sr.get("pool", ""))
    _require("shuffled_null" not in pool_blob or "never" in pool_blob, "selection pool must exclude shuffled_null", errors)

    # selection rule frozen before results
    _require(sr.get("selected_from") == "DEV_ONLY", "selection must be DEV_ONLY", errors)
    agg = sr.get("aggregation", {})
    _require(agg.get("primary_selection_statistic"), "missing primary_selection_statistic", errors)
    _require("equal weight" in _canon(agg).lower(), "aggregation must declare explicit equal weighting", errors)
    _require(sr.get("tie_breaking"), "selection tie-breaking missing", errors)

    # acceptance categories A/B/C present, no fabricated numeric PASS threshold
    cats = {c_["category"] for c_ in dr.get("interpretation_categories", [])}
    _require(cats == {"A", "B", "C"}, "interpretation categories must be exactly A/B/C", errors)
    _require(dr.get("leakage_audit", {}).get("status") == "FIRST_CLASS_SCIENTIFIC_TEST, not merely code quality", "leakage audit must be first-class", errors)
    _require(dr.get("determinism_audit", {}).get("status") == "FIRST_CLASS_TEST", "determinism audit must be first-class", errors)
    forbidden_claims = _canon(dr.get("forbidden_claims_from_r2_10", [])).lower()
    _require("runtime speedup" in forbidden_claims, "must explicitly forbid runtime-speedup claims", errors)
    _require("retention improvement" in forbidden_claims, "must explicitly forbid retention-improvement claims", errors)

    # digest
    expected = c.get("contract_digest")
    if expected:
        actual = compute_contract_digest(d)
        _require(actual == expected, f"contract digest mismatch: expected {expected}, got {actual}", errors)

    return errors


def assert_valid(contract_dir: Optional[Path] = None) -> Dict[str, Any]:
    errors = validate_contract(contract_dir)
    if errors:
        raise ContractValidationError("; ".join(errors))
    d = contract_dir or default_contract_dir()
    return {"valid": True, "contract_dir": str(d), "digest": compute_contract_digest(d)}
