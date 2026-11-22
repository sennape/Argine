"""R2-11 Phase-0 contract loading, validation, and deterministic digest.

Modeled directly on argine.research2.r2_bridge01.contract (same canonical-
JSON digest pattern). Phase-0 only: validates that R2-11's contract
components are internally consistent and that upstream frozen state
(R2-10 closure, R2-BRIDGE-01 closure/BRIDGE_POSITIVE) has not drifted.
Does not execute any scientific computation.
"""
from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any, Dict, List, Optional

CONTRACT_VERSION = "r2-11-phase0-contract-v1.0"
SCHEMA_VERSION = 1

# Upstream frozen references this contract binds to (must not drift).
R2_BRIDGE01_CONTRACT_DIGEST = "e4ef4268e6bc207f20d5f4a56c165b75b977536600ff9d6f7e59604f77a889fc"
R2_BRIDGE01_CLOSURE_COMMIT = "58b36a1ce5d90827a4f7b639f511833257569cf9"
R2_10_CLOSURE_COMMIT = "e13d89f97ea50452d8e0605c2bbed1f4e4ce589f"

COMPONENT_FILES = (
    "contract.json",
    "decision_rules.json",
    "metrics_contract.json",
    "causal_boundary.json",
    "retention_policy.json",
    "baseline_mapping.json",
    "eval_manifest.json",
    "acquisition_plan.json",
    "freshness_audit.json",
    "instrumentation_dependency.json",
    "execution_plan.json",
    "provenance.json",
    "audit.json",
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
    return repo_root() / "experiments" / "R2-11-predictive-retention"


def r2_bridge01_dir() -> Path:
    return repo_root() / "experiments" / "R2-BRIDGE-01-retention-feasibility"


def r2_10_dir() -> Path:
    return repo_root() / "experiments" / "R2-10-working-set-predictor"


def load_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def load_contract(contract_dir: Optional[Path] = None) -> Dict[str, Any]:
    d = contract_dir or default_contract_dir()
    contract = load_json(d / "contract.json")
    for key, fname in (
        ("decision_rules", "decision_rules.json"),
        ("metrics_contract", "metrics_contract.json"),
        ("causal_boundary", "causal_boundary.json"),
        ("retention_policy", "retention_policy.json"),
        ("baseline_mapping", "baseline_mapping.json"),
        ("eval_manifest", "eval_manifest.json"),
        ("acquisition_plan", "acquisition_plan.json"),
        ("freshness_audit", "freshness_audit.json"),
        ("instrumentation_dependency", "instrumentation_dependency.json"),
        ("execution_plan", "execution_plan.json"),
        ("provenance", "provenance.json"),
        ("audit", "audit.json"),
    ):
        contract[f"_{key}"] = load_json(d / fname)
    return contract


def compute_contract_digest(contract_dir: Optional[Path] = None) -> str:
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
    d = contract_dir or default_contract_dir()
    errors: List[str] = []
    try:
        c = load_contract(d)
    except Exception as e:  # noqa: BLE001
        return [f"failed to load contract: {e}"]

    _require(c.get("contract_version") == CONTRACT_VERSION, "contract_version mismatch", errors)
    _require(c.get("schema_version") == SCHEMA_VERSION, "schema_version mismatch", errors)

    up = c.get("upstream", {})
    _require(up.get("r2_bridge01_contract_digest") == R2_BRIDGE01_CONTRACT_DIGEST, "R2-BRIDGE-01 contract digest drifted", errors)
    _require(up.get("r2_bridge01_closure_commit") == R2_BRIDGE01_CLOSURE_COMMIT, "R2-BRIDGE-01 closure commit drifted", errors)
    _require(up.get("r2_10_closure_commit") == R2_10_CLOSURE_COMMIT, "R2-10 closure commit drifted", errors)

    try:
        import sys
        sys.path.insert(0, str(repo_root()))
        from argine.research2.r2_bridge01.contract import compute_contract_digest as r2_bridge01_digest
        _require(r2_bridge01_digest(r2_bridge01_dir()) == R2_BRIDGE01_CONTRACT_DIGEST, "R2-BRIDGE-01 contract digest no longer recomputes to the pinned value", errors)
    except Exception as e:  # noqa: BLE001
        errors.append(f"could not independently recompute upstream R2-BRIDGE-01 digest: {e}")

    # R2-10 historical decision must remain untouched
    r2_10_closure = r2_10_dir() / "PHASE3L-FINAL-CLOSURE-RECORD.json"
    _require(r2_10_closure.exists(), "R2-10 PHASE3L-FINAL-CLOSURE-RECORD.json missing", errors)
    if r2_10_closure.exists():
        cl = load_json(r2_10_closure)
        _require(cl.get("r2_11_proposed") is False, "R2-10 closure must still show r2_11_proposed=False (historical statement, never rewritten)", errors)
        _require(cl.get("final_status") == "R2-10 CLOSED", "R2-10 closure status drifted", errors)

    # R2-BRIDGE-01 closure must show BRIDGE_POSITIVE (the reason R2-11 exists at all)
    bridge_closure = r2_bridge01_dir() / "BRIDGE_EVALUATED_CLOSED.json"
    _require(bridge_closure.exists(), "R2-BRIDGE-01 BRIDGE_EVALUATED_CLOSED.json missing", errors)
    if bridge_closure.exists():
        bc = load_json(bridge_closure)
        _require(bc.get("classification") == "BRIDGE_POSITIVE", "R2-BRIDGE-01 classification must be BRIDGE_POSITIVE for R2-11 to be prospectively justified", errors)
        _require(bc.get("state") == "BRIDGE_EVALUATED_CLOSED", "R2-BRIDGE-01 state drifted", errors)

    dr = c.get("_decision_rules", {})
    pdr = dr.get("primary_decision_rule", {})
    labels = set(pdr.keys())
    _require(labels == {"R2_11_PASS", "R2_11_INCONCLUSIVE", "R2_11_FAIL", "R2_11_BLOCKED_INVALID"}, "decision rule outcomes must be exactly the four frozen labels", errors)
    _require(dr.get("never_changes_historical_r2_10_evidence") is True, "must explicitly state historical R2-10 evidence is untouched", errors)
    _require(bool(dr.get("never_changes_historical_r2_11_not_proposed_decision")), "must explicitly state the historical R2-11 NOT PROPOSED decision is untouched", errors)
    _require(dr.get("never_claims_runtime_speedup") is True, "must explicitly forbid runtime-speedup claims", errors)

    em = c.get("_eval_manifest", {})
    specs = em.get("eval_workload_specs", [])
    _require(len(specs) == 12, "eval_manifest must contain exactly 12 workload specs", errors)
    cats = [s.get("category") for s in specs]
    from collections import Counter
    cat_counts = Counter(cats)
    _require(len(cat_counts) == 4 and all(v == 3 for v in cat_counts.values()), "must be exactly 3 workloads per category across 4 categories", errors)
    _require(em.get("results") is None, "eval_manifest.results must be null (no R2-11 result may exist yet)", errors)

    fa = c.get("_freshness_audit", {})
    _require(fa.get("pass") is True, "freshness_audit must report pass=True", errors)
    _require(len(fa.get("violations", [])) == 0, "freshness_audit must report zero violations", errors)

    bl = c.get("_baseline_mapping", {})
    _require(bl.get("never_reselect") is True, "baseline_mapping must declare never_reselect", errors)

    rp = c.get("_retention_policy", {})
    _require(rp.get("frozen_predictor", {}).get("no_retuning") is True, "retention_policy predictor must declare no_retuning", errors)
    _require(rp.get("frozen_retention_mechanism", {}).get("no_policy_tournament") is True, "retention_policy mechanism must declare no_policy_tournament", errors)

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
