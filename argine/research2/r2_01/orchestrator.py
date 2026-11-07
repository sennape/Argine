"""R2-01 orchestration: the full candidate workflow behind
`argine research2 run R2-01` / `argine research2 analyse R2-01` (section 15/16).

preflight -> (clean-tree is checked by runner.run_experiment before this
module is ever called) -> trace-source resolution -> source hashing ->
provenance capture -> normalization -> trace validation -> replay
validation -> analysis -> scientific outcome classification -> external
evidence writing -> registry/state update -> human-readable summary.

No raw/normalized evidence is ever written to Git: everything lands under
the Research2 evidence root (argine.research2.store), exactly like every
other future experiment.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, Dict, Optional

from .. import provenance, store
from . import replay_contract, trace_contract, trace_source

OUTCOME_PASS = "PASS"
OUTCOME_INCONCLUSIVE = "INCONCLUSIVE"
OUTCOME_FAIL = "FAIL"
OUTCOME_CAPTURE_PREREQUISITE_FAILED = "CAPTURE_PREREQUISITE_FAILED"


def _write_json(path: Path, data: Dict[str, Any]) -> None:
    import json
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, indent=2, sort_keys=True, default=str), encoding="utf-8")


def _write_text(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


def classify_outcome(norm_report: trace_contract.NormalizationReport,
                     reconcile_problems: list, rr: Optional[replay_contract.DeterministicReplayResult]
                     ) -> "tuple[str, list[str]]":
    """Section 14's criteria, applied mechanically -- never inferred from a
    process exit code alone, never silently promoted."""
    reasons = []
    if reconcile_problems:
        reasons.extend(reconcile_problems)
        return OUTCOME_FAIL, reasons
    if rr is None:
        reasons.append("replay did not complete (see replay error)")
        return OUTCOME_FAIL, reasons

    if norm_report.layers_missing_expert_bytes:
        reasons.append(
            f"{len(norm_report.layers_missing_expert_bytes)} layer(s) have no byte-level "
            f"capacity metadata in the source trace: structural fidelity is demonstrated, "
            f"but byte-based retention evaluation (R2-02) cannot be fully trusted for "
            f"those layers without a fresh capture or external model metadata")
    if norm_report.initial_residency == "unknown_cold_assumed":
        reasons.append(
            "source trace carries no 'warmup' segment, so initial residency could not be "
            "distinguished from a true cold start; this trace alone cannot establish "
            "replay suitability for a warm-runtime comparison")

    behavioral_observable = False  # Level D: see run(); no source candidate carries
                                   # recorded resident/hit/miss state independent of replay
    if not behavioral_observable and reasons:
        return OUTCOME_INCONCLUSIVE, reasons + [
            "behavioral replay validation (Level D) is NOT_OBSERVABLE for this source: "
            "it carries no independently recorded resident/hit/miss state to compare "
            "replay against, and structural fidelity alone is not sufficient given the "
            "limitations above"]
    if not behavioral_observable:
        reasons.append("behavioral replay validation (Level D) is NOT_OBSERVABLE for this "
                       "source (no independently recorded resident/hit/miss state); "
                       "structural fidelity (Levels A-C) was otherwise sufficient")
        return OUTCOME_INCONCLUSIVE, reasons
    return OUTCOME_PASS, reasons  # unreachable while behavioral_observable is always False


def run(*, trace_source_arg: Optional[str] = None, allow_dirty: bool = False,
       model_path: Optional[str] = None
       ) -> Dict[str, Any]:
    rec = provenance.build_record("R2-01", outcome="PENDING", allow_dirty=allow_dirty)
    run_id = rec["identity"]["run_id"]
    rec["identity"]["start_utc"] = provenance.utc_now_iso()

    local = store.load_state().get("R2-01", {})
    # R2-01A: a verified registration (historical or fresh-capture) takes
    # priority over the legacy plain "trace_source" string, but a smoke/
    # synthetic trace can never reach this path either way -- registration
    # itself refuses anything that is not the documented canonical EXP-019
    # identity or this session's own fresh-capture output (see
    # trace_source.register_source).
    registration = local.get("trace_source_registration")
    local_state_path = (registration or {}).get("path") or local.get("trace_source")
    resolution = trace_source.resolve(explicit_source=trace_source_arg,
                                      local_state_source=local_state_path)

    capture_provenance: Optional[Dict[str, Any]] = None
    # Section 2/4: automatic fresh-capture prerequisite. Only ever
    # attempted when there is truly NO eligible source (TRACE_SOURCE_
    # MISSING) -- never when the block is TRACE_SOURCE_AMBIGUOUS (real
    # candidates already exist; capturing a new one is never the fix for
    # that), and never when an explicit --trace-source was given (resolve()
    # already short-circuits to RESOLVED in that case, so this branch is
    # unreachable then regardless). The hook itself also refuses to attempt
    # anything without a model path.
    if resolution.status == trace_source.TRACE_SOURCE_MISSING:
        from .. import runner as _runner  # deferred: runner imports this
                                          # module during its own init
        capture_attempt = _runner.run_prerequisite(
            "R2-01", resolution, model_path=model_path, allow_dirty=allow_dirty)
        if capture_attempt is not None:
            if capture_attempt.get("outcome") != "CAPTURED":
                # Atomic failure (section 5): no replay, no R2-01 run
                # directory, no scientific-state change, no R2-02 unlock,
                # no trace registration -- capture() itself already
                # guarantees the latter two. Return directly; the
                # experiment state machine is never touched below this
                # point, so it stays wherever it already was.
                return {
                    "experiment_id": "R2-01", "run_id": run_id,
                    "outcome": OUTCOME_CAPTURE_PREREQUISITE_FAILED,
                    "message": (
                        "R2-01 NOT RUN because the prerequisite capture failed "
                        f"(capture outcome: {capture_attempt.get('outcome')}). "
                        "See capture_result for the full diagnostic (including "
                        "capture_dir, where applicable)."),
                    "capture_result": capture_attempt,
                }
            # Capture succeeded: re-resolve from the ACTUAL store state
            # (never just trusting capture_attempt's own claim) so a
            # capture that reports CAPTURED but somehow failed to persist
            # its registration fails closed here exactly like any other
            # missing/ambiguous source, rather than being blindly trusted.
            # Section 6: this also guarantees the source used is the one
            # JUST registered, never some unrelated historical candidate
            # discover_local_candidates() might also see.
            fresh_local = store.load_state().get("R2-01", {})
            fresh_registration = fresh_local.get("trace_source_registration")
            fresh_local_state_path = (fresh_registration or {}).get("path")
            resolution = trace_source.resolve(explicit_source=trace_source_arg,
                                              local_state_source=fresh_local_state_path)
            if resolution.status == trace_source.TRACE_SOURCE_RESOLVED:
                capture_provenance = {
                    "capture_run_id": capture_attempt.get("run_id"),
                    "capture_candidate_status": capture_attempt.get("candidate_status"),
                    "trace_source_path": resolution.resolved_path,
                    "trace_sha256": (fresh_registration or {}).get("sha256"),
                }

    if resolution.status != trace_source.TRACE_SOURCE_RESOLVED:
        message = resolution.message
        if resolution.status == trace_source.TRACE_SOURCE_MISSING and not model_path:
            # Section 3: a clear, specific remediation when fresh capture
            # is the actual available remedy -- never silently a no-op.
            message = (f"{message} A fresh capture is available for R2-01: re-run "
                       f"with --model PATH to capture automatically, or use "
                       f"--trace-source PATH to name an existing trace.")
        return {
            "experiment_id": "R2-01", "run_id": run_id, "outcome": resolution.status,
            "message": message,
            "candidates": [c.__dict__ for c in resolution.candidates],
        }  # no run directory, no provenance, no evidence: this run never began

    src_path = resolution.resolved_path
    try:
        trace, norm_report = trace_contract.normalize(src_path)
    except trace_contract.ContractError as e:
        return {
            "experiment_id": "R2-01", "run_id": run_id, "outcome": "FAIL",
            "message": f"normalization failed closed: {e}",
            "trace_source": src_path,
        }

    reconcile_problems = trace_contract.reconcile(norm_report)

    rr = None
    replay_error = None
    try:
        rr = replay_contract.run_contract_replay(trace)
        rr.trace_digest = norm_report.normalized_trace_digest
    except replay_contract.ReplayContractError as e:
        replay_error = str(e)
        reconcile_problems = reconcile_problems + [f"replay invariant violated: {e}"]

    outcome, reasons = classify_outcome(norm_report, reconcile_problems, rr)

    rec["identity"]["end_utc"] = provenance.utc_now_iso()
    rec["identity"]["outcome"] = outcome
    rec["experiment"]["workload"] = norm_report.source.source_workload
    rec["artifacts"]["model"] = None  # not materially known for any candidate source
    if capture_provenance is not None:
        rec["artifacts"]["capture_provenance"] = capture_provenance

    run_dir = store.run_dir("R2-01", run_id)
    _write_json(run_dir / "manifest.json", rec)
    _write_json(run_dir / "source-trace.json", norm_report.to_json())
    if rr is not None:
        _write_json(run_dir / "replay-result.json", rr.to_json())
    _write_json(run_dir / "validation.json", {
        "reconcile_problems": reconcile_problems,
        "replay_error": replay_error,
        "reasons": reasons,
    })
    summary = {
        "experiment_id": "R2-01", "run_id": run_id, "outcome": outcome,
        "scope": "trace/replay contract validation only -- no Oracle/LRU/LFU comparison, "
                "no performance or latency claim",
        "trace_source": src_path, "reasons": reasons,
        # surfaced at top level too (not just nested in manifest.json's git
        # block) so CLI/test callers see it the same way runner.py's own
        # REFUSED_DIRTY_TREE / NOT_IMPLEMENTED payloads already do.
        "candidate_status": rec["git"]["candidate_status"],
        # Section 9: explicit, never-duplicated binding between this R2-01
        # scientific run and the capture run that produced its source
        # trace (None when the source was already registered/explicit --
        # i.e. no capture happened as part of THIS run).
        "capture_provenance": capture_provenance,
    }
    _write_json(run_dir / "summary.json", summary)
    _write_text(run_dir / "summary.md", _summary_markdown(summary, norm_report, rr))

    store.append_registry_entry({
        "identity": rec["identity"], "run_dir": str(run_dir), "trace_source": src_path,
    })
    new_state = "COMPLETED" if outcome == "PASS" else ("INCONCLUSIVE" if outcome ==
                                                        OUTCOME_INCONCLUSIVE else "FAILED")
    # the experiment must have passed through RUNNING to reach a terminal
    # state; R2-M0.1's protocol requires PLANNED->IMPLEMENTED->READY->RUNNING
    for step_state in ("IMPLEMENTED", "READY", "RUNNING"):
        store.set_experiment_state("R2-01", step_state)
    store.set_experiment_state("R2-01", new_state)

    return {**summary, "run_dir": str(run_dir)}


def _summary_markdown(summary: Dict[str, Any], norm_report: trace_contract.NormalizationReport,
                      rr: Optional[replay_contract.DeterministicReplayResult]) -> str:
    lines = [
        f"# R2-01 run {summary['run_id']}", "",
        f"**Outcome:** {summary['outcome']}", "",
        f"**Scope:** {summary['scope']}", "",
        f"**Trace source:** `{summary['trace_source']}`", "",
        "## Reasons", "",
    ]
    lines += [f"- {r}" for r in summary["reasons"]] or ["- (none)"]
    lines += ["", "## Normalization", "",
             f"- source rows: {norm_report.source_row_count}",
             f"- normalized steps: {norm_report.normalized_step_count}",
             f"- excluded rows: {norm_report.excluded_row_count} ({norm_report.exclusion_reasons})",
             f"- layers missing expert_bytes: {norm_report.layers_missing_expert_bytes}",
             f"- initial residency: {norm_report.initial_residency}", ""]
    if rr is not None:
        lines += ["## Replay (contract validation only; not a policy comparison)", "",
                 f"- policy: {rr.policy_identifier} (causal)",
                 f"- routing steps: {rr.routing_step_count}",
                 f"- expert requests: {rr.expert_request_count}, hits: {rr.hit_count}, "
                 f"misses: {rr.miss_count}, evictions: {rr.eviction_count}",
                 f"- deterministic replay digest: {rr.deterministic_replay_digest}", ""]
    return "\n".join(lines)


def analyse(*, run_id: Optional[str] = None) -> Dict[str, Any]:
    registry = [e for e in store.load_registry()
               if e.get("identity", {}).get("experiment_id") == "R2-01"]
    if not registry:
        return {"experiment_id": "R2-01", "outcome": "NOT_IMPLEMENTED",
                "message": "no R2-01 run exists yet to analyse"}

    if run_id:
        matches = [e for e in registry if e["identity"]["run_id"] == run_id]
        if not matches:
            raise KeyError(f"no R2-01 run with run_id={run_id!r}")
        entry = matches[0]
    else:
        completed = [e for e in registry if e["identity"]["outcome"] in
                    (OUTCOME_PASS, OUTCOME_INCONCLUSIVE, OUTCOME_FAIL)]
        if not completed:
            return {"experiment_id": "R2-01", "outcome": "NOT_IMPLEMENTED",
                    "message": "no completed R2-01 run exists yet to analyse"}
        latest_ends = sorted({e["identity"]["end_utc"] for e in completed}, reverse=True)
        newest = [e for e in completed if e["identity"]["end_utc"] == latest_ends[0]]
        if len(newest) > 1:
            raise ValueError(
                f"{len(newest)} R2-01 runs share the latest end_utc "
                f"({latest_ends[0]}): 'latest' is ambiguous. Pass an explicit run_id: "
                f"{[e['identity']['run_id'] for e in newest]}")
        entry = newest[0]

    run_dir = Path(entry["run_dir"])
    import json
    summary = json.loads((run_dir / "summary.json").read_text(encoding="utf-8"))
    return {**summary, "run_dir": str(run_dir), "analysed_from_existing_evidence": True}
