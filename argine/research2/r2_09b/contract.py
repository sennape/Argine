"""R2-09B contract loading, validation, and deterministic digest.

Preregistration-only: validates frozen benchmark semantics. Does not run
experiments, capture traces, inspect EVAL results, or execute models.
"""
from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, Iterable, List, Mapping, Optional, Sequence, Tuple

CONTRACT_VERSION = "r2-09b-contract-v1"
SCHEMA_VERSION = 1

# Frozen DEV identities that EVAL specs must not overlap.
DEV_WORKLOAD_SPECS: Tuple[Dict[str, str], ...] = (
    {
        "workload_id": "coding-v1",
        "source_path": "workloads/v1/coding.json",
        "sha256": "460949e7abf004374bb0b06dcda89eeaba0e916b5f6053784afe037e6f2162d8",
        "split": "dev",
    },
    {
        "workload_id": "reasoning-v1",
        "source_path": "research2/workloads/r2_08/reasoning-v1.json",
        "sha256": "df938ba8b233877743a80fac33a9fe7c6e363461969c399d2300aa0f0290c183",
        "split": "dev",
    },
    {
        "workload_id": "summarization-v1",
        "source_path": "research2/workloads/r2_08/summarization-v1.json",
        "sha256": "1411d3f44661822206a3c5c300cefbab7be1a4ff8dd8d6880d554b62d8943266",
        "split": "dev",
    },
    {
        "workload_id": "instruction-v1",
        "source_path": "research2/workloads/r2_08/instruction-v1.json",
        "sha256": "59636f262206fd6d621f91c3d31a5cc03072284f368c930292dea88dc40fcc40",
        "split": "dev",
    },
)
DEV_TRACE_SHA256 = "e6c16f2bcdb4ef841770ff16ac404c91312297112bcd7e653438cc22b8d7b2f1"
R2_03_CANONICAL_RUN_ID = "d957077073374f1e9fd1e101a002946f"
IIA_IIB_PAPER_BASELINE = "4025a2ecdd30cb5d2604fdfdf5cb4966f594a11d"
PRIMARY_BUDGETS_GIB = (1.5, 4.0, 8.0)
PRIMARY_BUDGET_BYTES = (1610612736, 4294967296, 8589934592)


class ContractValidationError(ValueError):
    pass


def _canon(obj: Any) -> str:
    return json.dumps(obj, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


def sha256_text(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def sha256_file(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def default_contract_dir() -> Path:
    return Path(__file__).resolve().parents[3] / "experiments" / "R2-09-prediction-benchmark"


def load_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def load_contract(contract_dir: Optional[Path] = None) -> Dict[str, Any]:
    d = contract_dir or default_contract_dir()
    contract = load_json(d / "contract.json")
    # attach component payloads for validation convenience
    for key, fname in (
        ("eval_manifest", "eval_manifest.json"),
        ("baseline_space", "baseline_space.json"),
        ("prediction_contract", "prediction_contract.json"),
        ("metrics_contract", "metrics_contract.json"),
        ("decision_rules", "decision_rules.json"),
    ):
        contract[f"_{key}"] = load_json(d / fname)
    return contract


def compute_contract_digest(contract_dir: Optional[Path] = None) -> str:
    """Deterministic digest over frozen contract components.

    Covers: EVAL specs (files + hashes), budgets, baseline families,
    hyperparameter spaces, prediction horizons, primary metrics, stochastic
    profile, decision rules, and threshold status.
    """
    d = contract_dir or default_contract_dir()
    repo_root = Path(__file__).resolve().parents[3]
    parts: List[Dict[str, Any]] = []
    for name in (
        "contract.json",
        "eval_manifest.json",
        "baseline_space.json",
        "prediction_contract.json",
        "metrics_contract.json",
        "decision_rules.json",
    ):
        p = d / name
        obj = load_json(p)
        if name == "contract.json":
            obj = dict(obj)
            obj.pop("contract_digest", None)
        parts.append({"name": name, "sha256": sha256_text(_canon(obj)), "content": obj})
    # include frozen EVAL workload spec files referenced by eval manifest
    eval_manifest = load_json(d / "eval_manifest.json")
    for spec in eval_manifest.get("eval_workload_specs", []):
        rel = spec["source_path"]
        p = repo_root / rel
        parts.append({"name": rel, "sha256": sha256_file(p)})
        src = spec.get("source_input")
        if src and src.get("path"):
            sp = repo_root / src["path"]
            parts.append({"name": src["path"], "sha256": sha256_file(sp)})
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

    em = c["_eval_manifest"]
    bs = c["_baseline_space"]
    pc = c["_prediction_contract"]
    mc = c["_metrics_contract"]
    dr = c["_decision_rules"]

    # versions
    _require(c.get("contract_version") == CONTRACT_VERSION, "contract_version mismatch", errors)
    _require(c.get("schema_version") == SCHEMA_VERSION, "schema_version mismatch", errors)

    # no EVAL results populated
    _require(em.get("eval_visibility_state") == "EVAL_LOCKED", "EVAL must be EVAL_LOCKED at freeze", errors)
    _require(not em.get("eval_results"), "eval_results must be empty/absent", errors)
    _require(not em.get("opened_utc"), "EVAL must not be opened", errors)

    # budgets
    budgets = tuple(c.get("primary_budgets_gib", []))
    _require(budgets == PRIMARY_BUDGETS_GIB, f"primary budgets must be {PRIMARY_BUDGETS_GIB}", errors)
    _require(16.0 not in budgets and 16 not in budgets, "16 GiB must not be primary pressured budget", errors)

    # EVAL specs
    specs = em.get("eval_workload_specs", [])
    _require(len(specs) >= 4, "need >=4 EVAL workload specs (balanced by family)", errors)
    spec_ids = [s["workload_id"] for s in specs]
    _require(len(spec_ids) == len(set(spec_ids)), "duplicate EVAL workload_id", errors)
    spec_hashes = [s["sha256"] for s in specs]
    _require(len(spec_hashes) == len(set(spec_hashes)), "duplicate EVAL workload sha256", errors)
    dev_hashes = {s["sha256"] for s in DEV_WORKLOAD_SPECS}
    dev_paths = {s["source_path"] for s in DEV_WORKLOAD_SPECS}
    dev_ids = {s["workload_id"] for s in DEV_WORKLOAD_SPECS}
    repo_root = Path(__file__).resolve().parents[3]
    for s in specs:
        _require(s["split"] == "eval", f"EVAL spec {s['workload_id']} must have split=eval", errors)
        _require(s["sha256"] not in dev_hashes, f"EVAL spec overlaps DEV hash: {s['workload_id']}", errors)
        _require(s["source_path"] not in dev_paths, f"EVAL spec overlaps DEV path: {s['source_path']}", errors)
        _require(s["workload_id"] not in dev_ids, f"EVAL spec overlaps DEV workload_id: {s['workload_id']}", errors)
        p = repo_root / s["source_path"]
        _require(p.exists(), f"missing EVAL spec file: {s['source_path']}", errors)
        if p.exists():
            actual = sha256_file(p)
            _require(actual == s["sha256"], f"hash mismatch for {s['source_path']}: {actual} != {s['sha256']}", errors)

    # baseline families + hyperparam spaces
    families = bs.get("baseline_families", [])
    fam_ids = [f["family_id"] for f in families]
    _require(len(fam_ids) == len(set(fam_ids)), "duplicate baseline family_id", errors)
    required_fams = {"global_lru", "lfu", "twoq", "lru_k", "aged_frequency"}
    _require(required_fams.issubset(set(fam_ids)), f"missing required baseline families: {required_fams - set(fam_ids)}", errors)
    for f in families:
        _require(f.get("causal") is True, f"baseline {f.get('family_id')} must be causal", errors)
        hp = f.get("hyperparameters", {})
        search = hp.get("search_space") if isinstance(hp, dict) else None
        tunable = bool(hp.get("tunable")) if isinstance(hp, dict) else False
        if tunable:
            _require(isinstance(search, dict) and search, f"tunable baseline {f['family_id']} missing frozen search_space", errors)

    # LFU existing evidence referenced, not rerun
    refs = c.get("existing_dev_evidence", {})
    _require(refs.get("r2_03_run_id") == R2_03_CANONICAL_RUN_ID, "R2-03 run reference missing/wrong", errors)
    _require(refs.get("lfu_classification") == "LFU_SWEEP_CANONICAL_AUXILIARY", "LFU classification missing/wrong", errors)
    _require(refs.get("rerun_lfu_sweep") is False, "LFU sweep must not be rerun", errors)

    # prediction horizons/targets
    _require(pc.get("primary_target"), "missing primary prediction target", errors)
    horizons = pc.get("horizons", [])
    _require(isinstance(horizons, list) and horizons, "missing prediction horizons", errors)
    _require(all(f.get("causal_only") is True for f in pc.get("allowed_feature_families", [])), "all feature families must be causal_only", errors)
    _require(any(f.get("feature_family_id") == "future_information" for f in pc.get("forbidden_features", [])) or pc.get("forbidden_features"), "forbidden features must be declared", errors)

    # metrics
    pm = mc.get("prediction_metrics", {})
    _require(pm.get("primary_metric_id"), "missing primary prediction metric", errors)
    pdir = {m["metric_id"]: m.get("direction") for m in pm.get("metrics", [])}
    _require(pdir.get(pm.get("primary_metric_id")) in {"higher", "lower"}, "primary prediction metric direction missing", errors)
    rm = mc.get("retention_metrics", {})
    _require(rm.get("primary_physical_replay_quantity") == "miss_bytes", "primary physical replay quantity must be miss_bytes", errors)
    for m in rm.get("metrics", []):
        _require(m.get("direction") in {"higher", "lower", "zero", "report"}, f"retention metric {m.get('metric_id')} missing direction", errors)

    # CASE C
    cc = c.get("case_c", {})
    _require(cc.get("o_b_proven_byte_optimal") is False, "O_B must not be marked byte-optimal", errors)
    _require(cc.get("recovery_of_belady_gap", {}).get("signed") is True, "RecoveryOfBeladyGap must be signed", errors)
    _require(cc.get("recovery_of_belady_gap", {}).get("allows_negative") is True, "RecoveryOfBeladyGap must allow negative", errors)
    _require(cc.get("recovery_of_belady_gap", {}).get("allows_greater_than_one") is True, "RecoveryOfBeladyGap must allow >1", errors)

    # thresholds/EXP-025
    th = c.get("thresholds", {})
    _require(th.get("status") == "PENDING_POLICY_STREAM_CALIBRATION", "engineering threshold status must be PENDING_POLICY_STREAM_CALIBRATION", errors)
    _require(th.get("numeric_threshold_frozen") is False, "numeric engineering threshold must not be fabricated", errors)
    ex = c.get("exp025", {})
    _require(ex.get("classification") == "P2_FULL_STREAM_DISTRIBUTION_MODEL", "EXP-025 classification mismatch", errors)
    _require(ex.get("arbitrary_policy_stream_transferability") == "UNSUPPORTED", "EXP-025 transferability must be UNSUPPORTED", errors)
    _require(ex.get("direct_bytes_to_seconds_forbidden") is True, "direct bytes->seconds conversion must be forbidden", errors)

    # stochastic
    st = c.get("stochastic_condition", {})
    _require(st.get("status") in {"UNRESOLVED", "FROZEN"}, "stochastic status invalid", errors)
    if st.get("status") == "UNRESOLVED":
        _require(not st.get("profile"), "unresolved stochastic profile must not define numeric profile", errors)

    # deterministic condition
    det = c.get("deterministic_condition", {})
    _require(det.get("temperature") == 0.0, "deterministic temperature must be 0.0", errors)
    _require(det.get("top_k") == 1, "deterministic top_k must be 1", errors)

    # hypotheses
    hyps = dr.get("primary_hypotheses", [])
    hyp_ids = [h["hypothesis_id"] for h in hyps]
    _require(len(hyp_ids) == len(set(hyp_ids)), "duplicate primary hypothesis IDs", errors)
    _require(hyps, "primary hypotheses missing", errors)
    for h in hyps:
        _require(h.get("budget_gib") in PRIMARY_BUDGETS_GIB, f"hypothesis {h['hypothesis_id']} budget not primary", errors)
        _require(h.get("metric_direction") in {"improve", "higher", "lower", "nonnegative", "lower_is_better_for_P"}, f"hypothesis {h['hypothesis_id']} missing direction", errors)
        _require(h.get("evaluation_state") in {"DEFINED", "EVAL_LOCKED"}, f"hypothesis {h['hypothesis_id']} state invalid", errors)
        _require(not h.get("eval_result"), f"hypothesis {h['hypothesis_id']} must not have eval_result", errors)

    # reporting granularity
    rg = dr.get("reporting_granularity", {})
    _require(rg.get("primary_unit") == "budget_x_workload", "primary reporting granularity must be budget_x_workload", errors)

    # B_selected rule
    bsel = dr.get("b_selected_rule", {})
    _require(bsel.get("selected_from") == "DEV_ONLY", "B_selected must be selected from DEV only", errors)
    _require(bsel.get("eval_hindsight_diagnostic_only") is True, "EVAL hindsight baseline must be diagnostic only", errors)

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
