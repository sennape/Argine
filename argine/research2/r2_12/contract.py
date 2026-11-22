"""R2-12 Phase-0 contract loading, validation, and deterministic digest.

Modeled directly on argine.research2.r2_11.contract (same canonical-JSON
digest pattern). Phase-0 only: validates that R2-12's contract components
are internally consistent and that upstream frozen state (R2-10 historical
decision, R2-BRIDGE-01 BRIDGE_POSITIVE, R2-11 R2_11_PASS closure) has not
drifted or been reinterpreted. Does not execute any runtime measurement.
"""
from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any, Dict, List, Optional, Union

CONTRACT_VERSION = "r2-12-phase0-contract-v1.0"
SCHEMA_VERSION = 1

# Upstream frozen references this contract binds to (must not drift).
R2_11_PHASE0_CONTRACT_DIGEST = "5dd3a3987aa74230693135d4ec20c5b8fd0a0de58a30b3b1646ccb2ce7b94f2a"
R2_11_EVAL_OPENED_COMMIT = "01448a3"
R2_11_CLOSURE_COMMIT = "0ccd36d"
R2_11_CLASSIFICATION = "R2_11_PASS"
R2_11_R2_12_GATE_TEXT = "R2-12 MAY BE PROPOSED FOR PROSPECTIVE RUNTIME VALIDATION."

# Last component file (README.md) is plain text, not JSON.
COMPONENT_FILES = (
    "contract.json",
    "runtime_metrics.json",
    "decision_rules.json",
    "hardware_contract.json",
    "machine_state_contract.json",
    "run_order.json",
    "runtime_corpus.json",
    "repetition_plan.json",
    "statistical_analysis.json",
    "instrumentation_contract.json",
    "output_equivalence.json",
    "execution_plan.json",
    "state_machine.json",
    "provenance.json",
    "audit.json",
    "README.md",
)

_TEXT_COMPONENT_FILES = {"README.md"}


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
    return repo_root() / "experiments" / "R2-12-runtime-validation"


def r2_11_dir() -> Path:
    return repo_root() / "experiments" / "R2-11-predictive-retention"


def r2_bridge01_dir() -> Path:
    return repo_root() / "experiments" / "R2-BRIDGE-01-retention-feasibility"


def r2_10_dir() -> Path:
    return repo_root() / "experiments" / "R2-10-working-set-predictor"


def load_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def load_component(contract_dir: Path, name: str) -> Union[Dict[str, Any], str]:
    path = contract_dir / name
    if name in _TEXT_COMPONENT_FILES:
        return path.read_text(encoding="utf-8")
    return load_json(path)


def load_contract(contract_dir: Optional[Path] = None) -> Dict[str, Any]:
    d = contract_dir or default_contract_dir()
    contract = load_json(d / "contract.json")
    for key, fname in (
        ("runtime_metrics", "runtime_metrics.json"),
        ("decision_rules", "decision_rules.json"),
        ("hardware_contract", "hardware_contract.json"),
        ("machine_state_contract", "machine_state_contract.json"),
        ("run_order", "run_order.json"),
        ("runtime_corpus", "runtime_corpus.json"),
        ("repetition_plan", "repetition_plan.json"),
        ("statistical_analysis", "statistical_analysis.json"),
        ("instrumentation_contract", "instrumentation_contract.json"),
        ("output_equivalence", "output_equivalence.json"),
        ("execution_plan", "execution_plan.json"),
        ("state_machine", "state_machine.json"),
        ("provenance", "provenance.json"),
        ("audit", "audit.json"),
    ):
        contract[f"_{key}"] = load_json(d / fname)
    return contract


def compute_contract_digest(contract_dir: Optional[Path] = None) -> str:
    d = contract_dir or default_contract_dir()
    parts: List[Dict[str, Any]] = []
    for name in COMPONENT_FILES:
        obj = load_component(d, name)
        if name == "contract.json":
            obj = dict(obj)
            obj.pop("contract_digest", None)
        if isinstance(obj, str):
            part_sha = sha256_text(obj)
        else:
            part_sha = sha256_text(_canon(obj))
        parts.append({"name": name, "sha256": part_sha, "content": obj})
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
    _require(up.get("r2_11_phase0_contract_digest") == R2_11_PHASE0_CONTRACT_DIGEST, "R2-11 Phase-0 contract digest drifted", errors)
    _require(up.get("r2_11_eval_opened_commit") == R2_11_EVAL_OPENED_COMMIT, "R2-11 EVAL_OPENED commit drifted", errors)
    _require(up.get("r2_11_closure_commit") == R2_11_CLOSURE_COMMIT, "R2-11 closure commit drifted", errors)
    _require(str(up.get("r2_11_classification", "")).startswith(R2_11_CLASSIFICATION), "R2-11 classification drifted / was reinterpreted", errors)
    _require(up.get("r2_11_r2_12_gate_text") == R2_11_R2_12_GATE_TEXT, "R2-11 -> R2-12 gate text drifted", errors)

    # Independently recompute the R2-11 Phase-0 digest from its live source,
    # never trusting the copied string alone.
    try:
        import sys
        sys.path.insert(0, str(repo_root()))
        from argine.research2.r2_11.contract import compute_contract_digest as r2_11_digest
        _require(
            r2_11_digest(r2_11_dir()) == R2_11_PHASE0_CONTRACT_DIGEST,
            "R2-11 contract digest no longer recomputes to the pinned Phase-0 value",
            errors,
        )
    except Exception as e:  # noqa: BLE001
        errors.append(f"could not independently recompute upstream R2-11 Phase-0 digest: {e}")

    # R2-11 final closure must still show R2_11_PASS and must not be reinterpreted.
    r2_11_closure = r2_11_dir() / "R2_11_EVALUATED_CLOSED.json"
    _require(r2_11_closure.exists(), "R2-11 R2_11_EVALUATED_CLOSED.json missing", errors)
    if r2_11_closure.exists():
        cl = load_json(r2_11_closure)
        _require(cl.get("classification") == "R2_11_PASS", "R2-11 closure classification drifted", errors)
        _require(cl.get("state") == "R2_11_EVALUATED_CLOSED", "R2-11 closure state drifted", errors)

    # R2-10 historical decision must remain untouched.
    r2_10_closure = r2_10_dir() / "PHASE3L-FINAL-CLOSURE-RECORD.json"
    _require(r2_10_closure.exists(), "R2-10 PHASE3L-FINAL-CLOSURE-RECORD.json missing", errors)
    if r2_10_closure.exists():
        cl10 = load_json(r2_10_closure)
        _require(cl10.get("r2_11_proposed") is False, "R2-10's original r2_11_proposed=False statement must remain unchanged", errors)
        _require(cl10.get("final_status") == "R2-10 CLOSED", "R2-10 closure status drifted", errors)

    # R2-BRIDGE-01 closure must still show BRIDGE_POSITIVE.
    bridge_closure = r2_bridge01_dir() / "BRIDGE_EVALUATED_CLOSED.json"
    _require(bridge_closure.exists(), "R2-BRIDGE-01 BRIDGE_EVALUATED_CLOSED.json missing", errors)
    if bridge_closure.exists():
        bc = load_json(bridge_closure)
        _require(bc.get("classification") == "BRIDGE_POSITIVE", "R2-BRIDGE-01 classification drifted", errors)

    dr = c.get("_decision_rules", {})
    pdr = dr.get("primary_decision_rule", {})
    labels = set(pdr.keys())
    _require(
        labels == {"R2_12_PASS", "R2_12_INCONCLUSIVE", "R2_12_FAIL", "R2_12_BLOCKED_INVALID"},
        "decision rule outcomes must be exactly the four frozen R2-12 labels",
        errors,
    )
    _require(dr.get("frozen_before_any_result") is True, "decision_rules must declare frozen_before_any_result=True", errors)
    _require(
        "research_3_gate" in dr and "not defined" in dr["research_3_gate"].get("note", "").lower() or "out of scope" in dr.get("research_3_gate", {}).get("note", "").lower(),
        "research_3_gate must explicitly disclaim defining a Research-3 gate",
        errors,
    )

    rc = c.get("_runtime_corpus", {})
    _require(rc.get("n_workloads") == len(rc.get("workloads", [])), "runtime_corpus n_workloads must match workloads list length", errors)
    _require(
        "explicit_non_cherry_picking_disclosure" in rc,
        "runtime_corpus must disclose the full candidate-outcome set used to justify its selection rule",
        errors,
    )

    rp = c.get("_repetition_plan", {})
    _require(rp.get("repetitions_per_cell", 0) >= 5, "repetition_plan must freeze at least 5 repetitions per cell", errors)
    _require("repetition_count_is_frozen" in rp, "repetition_plan must explicitly state the count is frozen before results exist", errors)

    sm = c.get("_state_machine", {})
    _require(sm.get("this_phase_ends_at") == "R2_12_PROTOCOL_FROZEN", "state_machine must end this phase at R2_12_PROTOCOL_FROZEN", errors)

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
