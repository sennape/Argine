"""R2-02 orchestration: the full workflow behind `argine research2 run
R2-02` / `argine research2 analyse R2-02`.

R2-01 PASS prerequisite + trace-SHA binding (prerequisite.py) -> normalize
the SAME trace via R2-01's own contract (trace_contract.normalize, never a
re-normalization with different semantics) -> fixed 4 GiB budget -> replay
every declared policy (policy_set.py) through the generalized deterministic
contract (replay_multi.py) TWICE each (determinism check) -> Oracle-gap /
sharding-decomposition metrics (comparison.py) -> scientific outcome
classification -> evidence written under the Research2 evidence root ->
registry/state update -> human-readable summary.

Executes entirely offline against the existing R2-01 trace. No model
weights are loaded and no runtime binary is invoked anywhere in this
module. No latency/throughput/wall-clock/energy/hardware-performance claim
is computed or reported anywhere in this module.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, Dict, List, Optional

from ...research.trace import PHASE_DECODE
from .. import provenance, store
from ..r2_01 import trace_contract
from . import comparison, policy_set, prerequisite
from .replay_multi import ReplayContractError, run_policy_replay

OUTCOME_PASS = "PASS"
OUTCOME_INCONCLUSIVE = "INCONCLUSIVE"
OUTCOME_FAIL = "FAIL"
OUTCOME_R2_01_PREREQUISITE_NOT_MET = "R2_01_PREREQUISITE_NOT_MET"

# Section 4: "Fixed budget: a 4 GiB expert-memory budget, matching the real
# R2-01 capture." Never swept (that is R2-03's job) -- a single, named,
# predeclared constant, not a parameter this module exposes for sweeping.
FIXED_BUDGET_BYTES = 4 * (1 << 30)


def _write_json(path: Path, data: Dict[str, Any]) -> None:
    import json
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, indent=2, sort_keys=True, default=str), encoding="utf-8")


def _write_text(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


def run(*, allow_dirty: bool = False, **_ignored: Any) -> Dict[str, Any]:
    rec = provenance.build_record("R2-02", outcome="PENDING", allow_dirty=allow_dirty)
    run_id = rec["identity"]["run_id"]
    rec["identity"]["start_utc"] = provenance.utc_now_iso()

    binding = prerequisite.require_r2_01_pass()
    if not binding.ok:
        # Section 5/R2-01-prerequisite tests: atomic failure, no run
        # directory, no provenance file, no false evidence -- mirrors
        # R2-01's own TRACE_SOURCE_MISSING/AMBIGUOUS early-return style.
        return {
            "experiment_id": "R2-02", "run_id": run_id,
            "outcome": OUTCOME_R2_01_PREREQUISITE_NOT_MET,
            "message": binding.message,
            "r2_01_prerequisite_status": binding.status,
        }

    try:
        trace, norm_report = trace_contract.normalize(binding.trace_path)
    except trace_contract.ContractError as e:
        return {
            "experiment_id": "R2-02", "run_id": run_id, "outcome": OUTCOME_FAIL,
            "message": f"normalization of the R2-01-validated trace failed closed: {e}",
            "trace_source": binding.trace_path,
        }

    reconcile_problems = trace_contract.reconcile(norm_report)
    budget = FIXED_BUDGET_BYTES

    replay_errors: List[str] = []
    interface_errors: List[str] = []
    nondeterminism: List[str] = []
    rows_by_label: Dict[str, Dict[str, Any]] = {}
    det_results: Dict[str, Any] = {}

    for spec in policy_set.DECLARED_POLICIES:
        try:
            policy_a = spec.build(trace)
            det_a, rr_a = run_policy_replay(
                trace, policy_a, budget=budget, causal=spec.causal, phase=PHASE_DECODE,
                trace_digest=norm_report.normalized_trace_digest)
            # Section 16: determinism check -- replay the SAME policy spec a
            # second, fully independent time and require a byte-identical
            # canonical digest. A real non-determinism bug (or a policy that
            # secretly depends on external mutable state) fails closed here
            # rather than being discovered only by a human re-running later.
            policy_b = spec.build(trace)
            det_b, _rr_b = run_policy_replay(
                trace, policy_b, budget=budget, causal=spec.causal, phase=PHASE_DECODE,
                trace_digest=norm_report.normalized_trace_digest)
        except ReplayContractError as e:
            replay_errors.append(f"{spec.label}: {e}")
            continue
        except Exception as e:  # noqa: BLE001 -- policy_interface.PolicyInterfaceError etc.
            interface_errors.append(f"{spec.label}: {e}")
            continue

        if det_a.deterministic_replay_digest != det_b.deterministic_replay_digest:
            nondeterminism.append(
                f"{spec.label}: two independent replays of the same trace/budget/"
                f"policy/configuration produced different deterministic digests "
                f"({det_a.deterministic_replay_digest} != "
                f"{det_b.deterministic_replay_digest})")
            continue

        row = comparison.policy_row(spec.label, det_a, rr_a)
        row["policy_metadata"] = spec.metadata(capacity_bytes=budget)
        rows_by_label[spec.label] = row
        det_results[spec.label] = det_a

    outcome, reasons = classify_outcome(
        norm_report, reconcile_problems, replay_errors, interface_errors,
        nondeterminism, rows_by_label)

    comparison_payload: Dict[str, Any] = {}
    if set(policy_set.CAUSAL_BASELINE_LABELS + [policy_set.ORACLE_LABEL]) <= set(rows_by_label):
        oracle_row = rows_by_label[policy_set.ORACLE_LABEL]
        gaps = {
            baseline_label: comparison.oracle_gap(rows_by_label[baseline_label], oracle_row)
            for baseline_label in policy_set.CAUSAL_BASELINE_LABELS
        }
        comparison_payload["oracle_gaps_by_baseline"] = gaps
        comparison_payload["sharding_decomposition"] = comparison.sharding_decomposition(
            rows_by_label)

    rec["identity"]["end_utc"] = provenance.utc_now_iso()
    rec["identity"]["outcome"] = outcome
    rec["experiment"]["workload"] = norm_report.source.source_workload
    rec["experiment"]["parameters"] = {"fixed_budget_bytes": budget,
                                       "phase": PHASE_DECODE,
                                       "r2_01_run_id": binding.r2_01_run_id}
    rec["artifacts"]["model"] = None  # no model weights loaded anywhere in R2-02

    run_dir = store.run_dir("R2-02", run_id)
    _write_json(run_dir / "manifest.json", rec)
    _write_json(run_dir / "provenance.json", {
        "r2_01_binding": binding.to_json(),
        "trace": norm_report.to_json(),
        "fixed_budget_bytes": budget,
        "phase": PHASE_DECODE,
    })
    _write_json(run_dir / "policy_results.json", {"policies": list(rows_by_label.values())})
    _write_json(run_dir / "comparison.json", comparison_payload)

    summary = {
        "experiment_id": "R2-02", "run_id": run_id, "outcome": outcome,
        "scope": "offline retention/replay comparison only (Oracle vs causal retention "
                "baselines) at a fixed 4 GiB budget on the validated R2-01 trace -- no "
                "latency/throughput/wall-clock/energy/hardware-performance claim, no "
                "budget sweep (R2-03), no recoverable-margin claim (R2-04)",
        "reasons": reasons,
        "candidate_status": rec["git"]["candidate_status"],
        "r2_01_binding": binding.to_json(),
        "fixed_budget_bytes": budget,
        "policies_evaluated": list(rows_by_label.keys()),
        "replay_errors": replay_errors,
        "interface_errors": interface_errors,
        "nondeterminism": nondeterminism,
    }
    _write_json(run_dir / "summary.json", summary)
    _write_text(run_dir / "summary.md", _summary_markdown(summary, rows_by_label, comparison_payload))

    store.append_registry_entry({
        "identity": rec["identity"], "run_dir": str(run_dir),
        "trace_source": binding.trace_path,
    })
    new_state = "COMPLETED" if outcome == OUTCOME_PASS else (
        "INCONCLUSIVE" if outcome == OUTCOME_INCONCLUSIVE else "FAILED")
    for step_state in ("IMPLEMENTED", "READY", "RUNNING"):
        store.set_experiment_state("R2-02", step_state)
    store.set_experiment_state("R2-02", new_state)

    return {**summary, "run_dir": str(run_dir)}


def classify_outcome(norm_report: trace_contract.NormalizationReport,
                     reconcile_problems: List[str], replay_errors: List[str],
                     interface_errors: List[str], nondeterminism: List[str],
                     rows_by_label: Dict[str, Dict[str, Any]]
                     ) -> "tuple[str, List[str]]":
    """Section 14's criteria, applied mechanically.

    PASS requires every declared policy to have produced a row (nothing
    missing, nothing malformed), zero replay/interface errors, zero
    non-determinism, and the Oracle to dominate every causal baseline --
    never that the Oracle gap be large (a zero/tiny gap is still PASS)."""
    reasons: List[str] = []

    if reconcile_problems:
        return OUTCOME_FAIL, list(reconcile_problems)
    if replay_errors:
        return OUTCOME_FAIL, [f"deterministic replay broke (capacity/invariant "
                              f"violation): {e}" for e in replay_errors]
    if interface_errors:
        return OUTCOME_FAIL, [f"causal/non-causal policy interface assertion failed "
                              f"(a declared-causal policy leaked future information, "
                              f"or a declared-non-causal policy was not actually built "
                              f"non-causally): {e}" for e in interface_errors]
    if nondeterminism:
        return OUTCOME_FAIL, list(nondeterminism)

    expected_labels = {p.label for p in policy_set.DECLARED_POLICIES}
    missing = expected_labels - set(rows_by_label)
    if missing:
        return OUTCOME_INCONCLUSIVE, [
            f"policy semantics could not be aligned: no result was produced for "
            f"{sorted(missing)} even though no replay/interface error was recorded "
            f"(malformed policy output)"]

    for label, row in rows_by_label.items():
        req = row["total_expert_requests"]
        if row["hits"] + row["misses"] != req:
            return OUTCOME_FAIL, [f"{label}: malformed policy output -- hits "
                                  f"({row['hits']}) + misses ({row['misses']}) != "
                                  f"total expert requests ({req})"]
        if row["budget_violations"]:
            return OUTCOME_FAIL, [f"{label}: capacity invariant violated "
                                  f"({row['budget_violations']} violation(s))"]

    for baseline_label in policy_set.CAUSAL_BASELINE_LABELS:
        gap = comparison.oracle_gap(rows_by_label[baseline_label],
                                    rows_by_label[policy_set.ORACLE_LABEL])
        if not gap["oracle_dominates"]:
            return OUTCOME_FAIL, [
                f"Oracle produced logically inconsistent results: "
                f"{policy_set.ORACLE_LABEL} performed WORSE than "
                f"{baseline_label}, a policy it theoretically dominates under "
                f"the same replay model (miss_reduction="
                f"{gap['miss_reduction']}, miss_byte_reduction="
                f"{gap['miss_byte_reduction']})"]

    if norm_report.layers_missing_expert_bytes:
        reasons.append(
            f"{len(norm_report.layers_missing_expert_bytes)} layer(s) have no byte-"
            f"level capacity metadata in the source trace; R2-02's own byte-based "
            f"miss/eviction-byte metrics cannot be fully trusted for those layers "
            f"(this is the exact structural caveat R2-01B already flagged as "
            f"relevant to R2-02)")
    if norm_report.initial_residency == "unknown_cold_assumed":
        reasons.append(
            "source trace carries no 'warmup' segment, so initial residency could not "
            "be distinguished from a true cold start; all policies share this same "
            "cold-start assumption identically, so comparability is preserved, but the "
            "absolute metrics should not be read as a cold-vs-warm-runtime claim")

    if reasons:
        return OUTCOME_INCONCLUSIVE, reasons

    reasons.append(
        "all declared policies replayed deterministically (verified by two "
        "independent replays per policy) with zero budget violations; the Oracle is "
        "non-causal by construction and every causal baseline is structurally causal "
        "(policy_interface assertions held); the Oracle is no worse than every causal "
        "baseline it theoretically dominates")
    return OUTCOME_PASS, reasons


def _summary_markdown(summary: Dict[str, Any], rows_by_label: Dict[str, Dict[str, Any]],
                      comparison_payload: Dict[str, Any]) -> str:
    lines = [
        f"# R2-02 run {summary['run_id']}", "",
        f"**Outcome:** {summary['outcome']}", "",
        f"**Scope:** {summary['scope']}", "",
        f"**Fixed budget:** {summary['fixed_budget_bytes']} bytes", "",
        "## Reasons", "",
    ]
    lines += [f"- {r}" for r in summary["reasons"]] or ["- (none)"]
    lines += ["", "## Policy results", ""]
    for label, row in rows_by_label.items():
        lines.append(f"- **{label}**: misses={row['misses']}, miss_bytes={row['miss_bytes']}, "
                     f"evictions={row['evictions']}, peak_resident_bytes="
                     f"{row['peak_resident_bytes']}")
    if comparison_payload:
        lines += ["", "## Oracle gaps", ""]
        for baseline, gap in comparison_payload.get("oracle_gaps_by_baseline", {}).items():
            lines.append(f"- {baseline} -> {policy_set.ORACLE_LABEL}: "
                         f"miss_reduction={gap['miss_reduction']}, "
                         f"relative_miss_reduction={gap['relative_miss_reduction']}")
    return "\n".join(lines)


def analyse(*, run_id: Optional[str] = None, **_ignored: Any) -> Dict[str, Any]:
    registry = [e for e in store.load_registry()
               if e.get("identity", {}).get("experiment_id") == "R2-02"]
    if not registry:
        return {"experiment_id": "R2-02", "outcome": "NOT_IMPLEMENTED",
                "message": "no R2-02 run exists yet to analyse"}

    if run_id:
        matches = [e for e in registry if e["identity"]["run_id"] == run_id]
        if not matches:
            raise KeyError(f"no R2-02 run with run_id={run_id!r}")
        entry = matches[0]
    else:
        completed = [e for e in registry if e["identity"]["outcome"] in
                    (OUTCOME_PASS, OUTCOME_INCONCLUSIVE, OUTCOME_FAIL)]
        if not completed:
            return {"experiment_id": "R2-02", "outcome": "NOT_IMPLEMENTED",
                    "message": "no completed R2-02 run exists yet to analyse"}
        latest_ends = sorted({e["identity"]["end_utc"] for e in completed}, reverse=True)
        newest = [e for e in completed if e["identity"]["end_utc"] == latest_ends[0]]
        if len(newest) > 1:
            raise ValueError(
                f"{len(newest)} R2-02 runs share the latest end_utc "
                f"({latest_ends[0]}): 'latest' is ambiguous. Pass an explicit run_id: "
                f"{[e['identity']['run_id'] for e in newest]}")
        entry = newest[0]

    run_dir = Path(entry["run_dir"])
    import json
    summary = json.loads((run_dir / "summary.json").read_text(encoding="utf-8"))
    return {**summary, "run_dir": str(run_dir), "analysed_from_existing_evidence": True}
