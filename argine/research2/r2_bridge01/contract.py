"""R2-BRIDGE-01 Phase-0 contract loading, validation, and deterministic digest.

Phase-0 only: freezes the retention-feasibility contract (predictor freeze,
retention-mechanism grid, baseline binding, budgets, metrics, decision
gate, new-corpus acquisition spec, DEV/holdout separation, leakage rules)
BEFORE any BRIDGE holdout trace is captured and BEFORE the full retention
experiment runs. Does not execute the experiment, does not capture BRIDGE
holdout traces, does not inspect BRIDGE results, does not start R2-11 or
R2-12, and does not modify any R2-09/R2-10 frozen artifact.
"""
from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any, Dict, List, Optional

CONTRACT_VERSION = "r2-bridge01-phase0-contract-v1.0"
SCHEMA_VERSION = 1

# Upstream frozen references this contract binds to (must not drift).
R2_09B_CONTRACT_DIGEST = "b6274b354e39854effcca1510f0f817a85a162ab80cb6d29a31041a9978a7b28"
R2_10_PHASE0_CONTRACT_DIGEST = "4d2288b7b4a812fd85d96aa7c2227d639b52f5131bff305d687e03970f53d725"
R2_10_CLOSURE_COMMIT = "e13d89f97ea50452d8e0605c2bbed1f4e4ce589f"

COMPONENT_FILES = (
    "contract.json",
    "retention_policy_space.json",
    "decision_rules.json",
    "corpus_acquisition_contract.json",
    "dev_holdout_separation.json",
    "leakage_contract.json",
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
    return repo_root() / "experiments" / "R2-BRIDGE-01-retention-feasibility"


def r2_09_dir() -> Path:
    return repo_root() / "experiments" / "R2-09-prediction-benchmark"


def r2_10_dir() -> Path:
    return repo_root() / "experiments" / "R2-10-working-set-predictor"


def load_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def load_contract(contract_dir: Optional[Path] = None) -> Dict[str, Any]:
    d = contract_dir or default_contract_dir()
    contract = load_json(d / "contract.json")
    for key, fname in (
        ("retention_policy_space", "retention_policy_space.json"),
        ("decision_rules", "decision_rules.json"),
        ("corpus_acquisition_contract", "corpus_acquisition_contract.json"),
        ("dev_holdout_separation", "dev_holdout_separation.json"),
        ("leakage_contract", "leakage_contract.json"),
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
    _require(up.get("r2_09b_contract_digest") == R2_09B_CONTRACT_DIGEST, "R2-09B contract digest drifted", errors)
    _require(up.get("r2_10_phase0_contract_digest") == R2_10_PHASE0_CONTRACT_DIGEST, "R2-10 phase0 contract digest drifted", errors)
    _require(up.get("r2_10_closure_commit") == R2_10_CLOSURE_COMMIT, "R2-10 closure commit drifted", errors)

    try:
        import sys
        sys.path.insert(0, str(repo_root()))
        from argine.research2.r2_09b.contract import compute_contract_digest as r2_09b_digest
        from argine.research2.r2_10.contract import compute_contract_digest as r2_10_digest
        _require(r2_09b_digest(r2_09_dir()) == R2_09B_CONTRACT_DIGEST, "R2-09B contract digest no longer recomputes to the pinned value", errors)
        _require(r2_10_digest(r2_10_dir()) == R2_10_PHASE0_CONTRACT_DIGEST, "R2-10 contract digest no longer recomputes to the pinned value", errors)
    except Exception as e:  # noqa: BLE001
        errors.append(f"could not independently recompute upstream digests: {e}")

    # R2-10 historical decision must remain untouched
    r2_10_closure = r2_10_dir() / "PHASE3L-FINAL-CLOSURE-RECORD.json"
    _require(r2_10_closure.exists(), "R2-10 PHASE3L-FINAL-CLOSURE-RECORD.json missing", errors)
    if r2_10_closure.exists():
        cl = load_json(r2_10_closure)
        _require(cl.get("r2_11_proposed") is False, "R2-10 closure must still show r2_11_proposed=False", errors)
        _require(cl.get("final_status") == "R2-10 CLOSED", "R2-10 closure status drifted", errors)

    # EVAL firewall: R2-BRIDGE-01 must never open the R2-10 EVAL set
    eval_manifest_path = r2_09_dir() / "eval_manifest.json"
    if eval_manifest_path.exists():
        em = load_json(eval_manifest_path)
        _require(em.get("eval_visibility_state") == "EVAL_LOCKED", "R2-10 EVAL must remain EVAL_LOCKED", errors)

    rps = c.get("_retention_policy_space", {})
    fams = rps.get("families", [])
    fam_ids = [f["family_id"] for f in fams]
    _require(len(fam_ids) == len(set(fam_ids)), "duplicate retention-mechanism family_id", errors)
    _require(len(fam_ids) <= 4, "retention mechanism family count must stay small (Phase-0 intent: tiny grid)", errors)

    dr = c.get("_decision_rules", {})
    labels = {g["label"] for g in dr.get("decision_gate", {}).get("outcomes", [])}
    _require(labels == {"BRIDGE_NEGATIVE", "BRIDGE_POSITIVE_BUT_WEAK", "BRIDGE_POSITIVE"}, "decision gate outcomes must be exactly the three frozen labels", errors)
    _require(dr.get("never_promotes_r2_10_category") is True, "must explicitly state this cannot promote R2-10 Category B to C", errors)
    _require(dr.get("never_reopens_r2_11_not_proposed") is True, "must explicitly state the historical R2-11 NOT PROPOSED decision is untouched", errors)

    chr_ = c.get("_corpus_acquisition_contract", {})
    _require(chr_.get("eval_set_reuse_forbidden") is True, "corpus contract must forbid reusing R2-10 EVAL as BRIDGE holdout", errors)

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
