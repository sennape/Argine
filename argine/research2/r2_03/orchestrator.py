"""R2-03 orchestration: the full workflow behind `argine research2 run
R2-03` / `argine research2 analyse R2-03`.

R2-01 PASS + R2-02 PASS + trace-binding consistency (prerequisite.py) ->
normalize the SAME trace via R2-01's own contract -> replay every
r2_02-declared policy at every predeclared budget (budget_sweep.py),
TWICE each (determinism check) -> verify the 4 GiB point exactly
reproduces R2-02's own registered evidence (reproduction.py) -> Oracle-
gap curves (curves.py) -> scientific outcome classification -> evidence
written under the Research2 evidence root (including a flat CSV) ->
registry/state update -> human-readable summary.

Executes entirely offline against the existing R2-01 trace. No model
weights are loaded and no runtime binary is invoked anywhere in this
module. No latency/throughput/wall-clock/energy/hardware-performance
claim, and no final "recoverable margin" claim (R2-04's job), is computed
or reported anywhere in this module.
"""

from __future__ import annotations

import csv
import io
from pathlib import Path
from typing import Any, Dict, List, Optional

from ...research.trace import PHASE_DECODE
from .. import provenance, store
from ..r2_01 import trace_contract
from ..r2_02 import comparison as r2_02_comparison
from ..r2_02 import policy_set
from ..r2_02.replay_multi import ReplayContractError, run_policy_replay
from . import budget_sweep, curves, prerequisite, reproduction

OUTCOME_PASS = "PASS"
OUTCOME_INCONCLUSIVE = "INCONCLUSIVE"
OUTCOME_FAIL = "FAIL"
OUTCOME_PREREQUISITE_NOT_MET = "R2_01_OR_R2_02_PREREQUISITE_NOT_MET"


def _write_json(path: Path, data: Dict[str, Any]) -> None:
    import json
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, indent=2, sort_keys=True, default=str), encoding="utf-8")


def _write_text(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


def run(*, allow_dirty: bool = False, **_ignored: Any) -> Dict[str, Any]:
    rec = provenance.build_record("R2-03", outcome="PENDING", allow_dirty=allow_dirty)
    run_id = rec["identity"]["run_id"]
    rec["identity"]["start_utc"] = provenance.utc_now_iso()

    binding = prerequisite.require_r2_01_and_r2_02_pass()
    if not binding.ok:
        return {
            "experiment_id": "R2-03", "run_id": run_id,
            "outcome": OUTCOME_PREREQUISITE_NOT_MET,
            "message": binding.message,
            "prerequisite_status": binding.status,
        }

    try:
        trace, norm_report = trace_contract.normalize(binding.trace_path)
    except trace_contract.ContractError as e:
        return {
            "experiment_id": "R2-03", "run_id": run_id, "outcome": OUTCOME_FAIL,
            "message": f"normalization of the R2-01/R2-02-validated trace failed closed: {e}",
            "trace_source": binding.trace_path,
        }

    reconcile_problems = trace_contract.reconcile(norm_report)

    replay_errors: List[str] = []
    interface_errors: List[str] = []
    nondeterminism: List[str] = []
    budget_results: Dict[int, Dict[str, Dict[str, Any]]] = {}

    for budget in budget_sweep.FIXED_BUDGETS_BYTES:
        rows_for_budget: Dict[str, Dict[str, Any]] = {}
        for spec in policy_set.DECLARED_POLICIES:
            tag = f"{budget_sweep.budget_label(budget)}/{spec.label}"
            try:
                det_a, rr_a = run_policy_replay(
                    trace, spec.build(trace), budget=budget, causal=spec.causal,
                    phase=PHASE_DECODE, trace_digest=norm_report.normalized_trace_digest)
                det_b, _rr_b = run_policy_replay(
                    trace, spec.build(trace), budget=budget, causal=spec.causal,
                    phase=PHASE_DECODE, trace_digest=norm_report.normalized_trace_digest)
            except ReplayContractError as e:
                replay_errors.append(f"{tag}: {e}")
                continue
            except Exception as e:  # noqa: BLE001 -- policy_interface.PolicyInterfaceError etc.
                interface_errors.append(f"{tag}: {e}")
                continue
            if det_a.deterministic_replay_digest != det_b.deterministic_replay_digest:
                nondeterminism.append(
                    f"{tag}: two independent replays produced different deterministic "
                    f"digests ({det_a.deterministic_replay_digest} != "
                    f"{det_b.deterministic_replay_digest})")
                continue
            row = r2_02_comparison.policy_row(spec.label, det_a, rr_a)
            row["policy_metadata"] = spec.metadata(capacity_bytes=budget)
            row["budget_bytes"] = budget
            row["budget_label"] = budget_sweep.budget_label(budget)
            rows_for_budget[spec.label] = row
        budget_results[budget] = rows_for_budget

    repro_check: Optional[reproduction.ReproductionCheck] = None
    if binding.r2_02_run_id is not None:
        r2_02_registry = [e for e in store.load_registry()
                         if e.get("identity", {}).get("run_id") == binding.r2_02_run_id]
        if r2_02_registry:
            r2_02_run_dir = Path(r2_02_registry[0]["run_dir"])
            try:
                r2_02_rows = reproduction.load_r2_02_rows(r2_02_run_dir)
                repro_check = reproduction.check(
                    r2_02_rows, budget_results.get(budget_sweep.REPRODUCTION_BUDGET_BYTES, {}))
            except (OSError, KeyError) as e:
                repro_check = reproduction.ReproductionCheck(
                    False, f"could not read R2-02's own evidence to reproduce against: {e}", {})
        else:
            repro_check = reproduction.ReproductionCheck(
                False, f"R2-02 run {binding.r2_02_run_id} is missing its own registry "
                      f"entry/run_dir -- cannot verify reproduction.", {})

    gaps_by_budget = curves.oracle_gaps_by_budget(budget_results)
    primary_curve = curves.primary_curve_relative_miss_byte_reduction(gaps_by_budget)
    global_lru_miss_rate_curve = curves.miss_rate_curve(budget_results, policy_set.LABEL_GLOBAL_LRU)
    oracle_miss_rate_curve = curves.miss_rate_curve(budget_results, policy_set.ORACLE_LABEL)

    outcome, reasons = classify_outcome(
        norm_report, reconcile_problems, replay_errors, interface_errors, nondeterminism,
        budget_results, repro_check)

    rec["identity"]["end_utc"] = provenance.utc_now_iso()
    rec["identity"]["outcome"] = outcome
    rec["experiment"]["workload"] = norm_report.source.source_workload
    rec["experiment"]["parameters"] = {
        "fixed_budgets_bytes": list(budget_sweep.FIXED_BUDGETS_BYTES),
        "reproduction_budget_bytes": budget_sweep.REPRODUCTION_BUDGET_BYTES,
        "phase": PHASE_DECODE,
        "r2_01_run_id": binding.r2_01_run_id, "r2_02_run_id": binding.r2_02_run_id,
    }
    rec["artifacts"]["model"] = None

    run_dir = store.run_dir("R2-03", run_id)
    _write_json(run_dir / "manifest.json", rec)
    _write_json(run_dir / "provenance.json", {
        "binding": binding.to_json(), "trace": norm_report.to_json(),
        "fixed_budgets_bytes": list(budget_sweep.FIXED_BUDGETS_BYTES),
        "phase": PHASE_DECODE,
    })
    _write_json(run_dir / "budget_results.json", {
        str(budget): list(rows.values()) for budget, rows in budget_results.items()})
    _write_json(run_dir / "comparison.json", {
        "reproduction_check": repro_check.to_json() if repro_check else None,
        "oracle_gaps_by_budget": {str(b): g for b, g in gaps_by_budget.items()},
        "primary_curve_relative_miss_byte_reduction_vs_budget": primary_curve,
        "global_lru_miss_rate_vs_budget": global_lru_miss_rate_curve,
        "oracle_miss_rate_vs_budget": oracle_miss_rate_curve,
    })
    _write_text(run_dir / "budget_results.csv", _to_csv(budget_results))

    summary = {
        "experiment_id": "R2-03", "run_id": run_id, "outcome": outcome,
        "scope": "offline budget-sensitivity sweep of the R2-02 Oracle-vs-causal-"
                "retention comparison on the validated R2-01 trace -- no latency/"
                "throughput/wall-clock/energy/hardware-performance claim, no final "
                "recoverable-margin claim (R2-04)",
        "reasons": reasons,
        "candidate_status": rec["git"]["candidate_status"],
        "binding": binding.to_json(),
        "fixed_budgets_bytes": list(budget_sweep.FIXED_BUDGETS_BYTES),
        "reproduction_check": repro_check.to_json() if repro_check else None,
        "replay_errors": replay_errors, "interface_errors": interface_errors,
        "nondeterminism": nondeterminism,
    }
    _write_json(run_dir / "summary.json", summary)
    _write_text(run_dir / "summary.md", _summary_markdown(summary, primary_curve))

    store.append_registry_entry({
        "identity": rec["identity"], "run_dir": str(run_dir),
        "trace_source": binding.trace_path,
    })
    new_state = "COMPLETED" if outcome == OUTCOME_PASS else (
        "INCONCLUSIVE" if outcome == OUTCOME_INCONCLUSIVE else "FAILED")
    for step_state in ("IMPLEMENTED", "READY", "RUNNING"):
        store.set_experiment_state("R2-03", step_state)
    store.set_experiment_state("R2-03", new_state)

    return {**summary, "run_dir": str(run_dir)}


def classify_outcome(norm_report: trace_contract.NormalizationReport,
                     reconcile_problems: List[str], replay_errors: List[str],
                     interface_errors: List[str], nondeterminism: List[str],
                     budget_results: Dict[int, Dict[str, Dict[str, Any]]],
                     repro_check: Optional[reproduction.ReproductionCheck]
                     ) -> "tuple[str, List[str]]":
    """Section "OUTCOME" criteria, applied mechanically. The 4 GiB
    reproduction check is evaluated FIRST and, on mismatch, is FAIL and
    blocks interpretation of the whole sweep -- never silently tolerated,
    regardless of how clean the rest of the sweep looks."""
    reasons: List[str] = []

    if repro_check is not None and not repro_check.ok:
        return OUTCOME_FAIL, [repro_check.message, f"mismatches: {repro_check.mismatches}"]
    if repro_check is None:
        return OUTCOME_FAIL, ["no R2-02 reproduction check could be performed at all "
                             "(missing binding/evidence) -- the sweep cannot be "
                             "interpreted without it"]

    if reconcile_problems:
        return OUTCOME_FAIL, list(reconcile_problems)
    if replay_errors:
        return OUTCOME_FAIL, [f"deterministic replay broke: {e}" for e in replay_errors]
    if interface_errors:
        return OUTCOME_FAIL, [f"causal/non-causal policy interface assertion failed "
                              f"(future information leaked, or a non-causal policy was "
                              f"not actually built non-causally): {e}"
                              for e in interface_errors]
    if nondeterminism:
        return OUTCOME_FAIL, list(nondeterminism)

    expected_labels = {p.label for p in policy_set.DECLARED_POLICIES}
    for budget, rows in budget_results.items():
        missing = expected_labels - set(rows)
        if missing:
            return OUTCOME_INCONCLUSIVE, [
                f"budget {budget}: no result for {sorted(missing)} even though no "
                f"replay/interface error was recorded (malformed policy output)"]
        for label, row in rows.items():
            if row["hits"] + row["misses"] != row["total_expert_requests"]:
                return OUTCOME_FAIL, [f"budget {budget}/{label}: malformed policy "
                                      f"output -- hits + misses != total requests"]
            if row["budget_violations"]:
                return OUTCOME_FAIL, [f"budget {budget}/{label}: capacity invariant "
                                      f"violated"]
        if policy_set.ORACLE_LABEL in rows:
            oracle_row = rows[policy_set.ORACLE_LABEL]
            for baseline_label in policy_set.CAUSAL_BASELINE_LABELS:
                if baseline_label not in rows:
                    continue
                gap = r2_02_comparison.oracle_gap(rows[baseline_label], oracle_row)
                if not gap["oracle_dominates"]:
                    return OUTCOME_FAIL, [
                        f"budget {budget}: Oracle produced logically inconsistent "
                        f"results vs {baseline_label} (miss_reduction="
                        f"{gap['miss_reduction']}, miss_byte_reduction="
                        f"{gap['miss_byte_reduction']})"]

    if norm_report.layers_missing_expert_bytes:
        reasons.append(
            f"{len(norm_report.layers_missing_expert_bytes)} layer(s) have no byte-"
            f"level capacity metadata in the source trace; byte-based metrics cannot "
            f"be fully trusted for those layers at any budget")
    if norm_report.initial_residency == "unknown_cold_assumed":
        reasons.append(
            "source trace carries no 'warmup' segment; all budgets/policies share this "
            "same cold-start assumption identically, so cross-budget comparability is "
            "preserved, but absolute metrics should not be read as a cold-vs-warm claim")

    if reasons:
        reasons.append(repro_check.message)
        return OUTCOME_INCONCLUSIVE, reasons

    reasons.append(
        "the fixed, predeclared budget sweep was evaluated end-to-end: every budget/"
        "policy pair replayed deterministically (two independent replays each), zero "
        "capacity violations, the 4 GiB point exactly reproduces R2-02's registered "
        "evidence, and the Oracle is no worse than every causal baseline it "
        "theoretically dominates at every budget")
    return OUTCOME_PASS, reasons


def _to_csv(budget_results: Dict[int, Dict[str, Dict[str, Any]]]) -> str:
    buf = io.StringIO()
    fields = ["budget_bytes", "budget_label", "policy", "causal", "total_expert_requests",
              "hits", "misses", "miss_rate", "miss_bytes", "evictions", "eviction_bytes",
              "peak_resident_bytes", "budget_violations", "deterministic_replay_digest"]
    writer = csv.DictWriter(buf, fieldnames=fields)
    writer.writeheader()
    for budget in sorted(budget_results):
        for row in budget_results[budget].values():
            writer.writerow({
                "budget_bytes": row["budget_bytes"], "budget_label": row["budget_label"],
                "policy": row["policy"], "causal": row["policy_metadata"]["causal"],
                "total_expert_requests": row["total_expert_requests"], "hits": row["hits"],
                "misses": row["misses"], "miss_rate": row["miss_rate"],
                "miss_bytes": row["miss_bytes"], "evictions": row["evictions"],
                "eviction_bytes": row["eviction_bytes"],
                "peak_resident_bytes": row["peak_resident_bytes"],
                "budget_violations": row["budget_violations"],
                "deterministic_replay_digest": row["deterministic_replay_digest"],
            })
    return buf.getvalue()


def _summary_markdown(summary: Dict[str, Any], primary_curve: List[Dict[str, Any]]) -> str:
    lines = [
        f"# R2-03 run {summary['run_id']}", "",
        f"**Outcome:** {summary['outcome']}", "",
        f"**Scope:** {summary['scope']}", "",
        "## Reasons", "",
    ]
    lines += [f"- {r}" for r in summary["reasons"]] or ["- (none)"]
    lines += ["", "## Primary curve: relative Oracle miss-byte reduction vs budget", ""]
    for point in primary_curve:
        lines.append(f"- {point['budget_bytes']} bytes: "
                     f"{point['relative_miss_byte_reduction']}")
    return "\n".join(lines)


def analyse(*, run_id: Optional[str] = None, **_ignored: Any) -> Dict[str, Any]:
    registry = [e for e in store.load_registry()
               if e.get("identity", {}).get("experiment_id") == "R2-03"]
    if not registry:
        return {"experiment_id": "R2-03", "outcome": "NOT_IMPLEMENTED",
                "message": "no R2-03 run exists yet to analyse"}

    if run_id:
        matches = [e for e in registry if e["identity"]["run_id"] == run_id]
        if not matches:
            raise KeyError(f"no R2-03 run with run_id={run_id!r}")
        entry = matches[0]
    else:
        completed = [e for e in registry if e["identity"]["outcome"] in
                    (OUTCOME_PASS, OUTCOME_INCONCLUSIVE, OUTCOME_FAIL)]
        if not completed:
            return {"experiment_id": "R2-03", "outcome": "NOT_IMPLEMENTED",
                    "message": "no completed R2-03 run exists yet to analyse"}
        latest_ends = sorted({e["identity"]["end_utc"] for e in completed}, reverse=True)
        newest = [e for e in completed if e["identity"]["end_utc"] == latest_ends[0]]
        if len(newest) > 1:
            raise ValueError(
                f"{len(newest)} R2-03 runs share the latest end_utc "
                f"({latest_ends[0]}): 'latest' is ambiguous. Pass an explicit run_id: "
                f"{[e['identity']['run_id'] for e in newest]}")
        entry = newest[0]

    run_dir = Path(entry["run_dir"])
    import json
    summary = json.loads((run_dir / "summary.json").read_text(encoding="utf-8"))
    return {**summary, "run_dir": str(run_dir), "analysed_from_existing_evidence": True}
