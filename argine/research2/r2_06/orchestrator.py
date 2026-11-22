"""R2-06 orchestration: the full workflow behind `argine research2 run
R2-06` / `argine research2 analyse R2-06`.

Fully offline DESCRIPTIVE CHARACTERIZATION of SAME-LAYER token-to-token
transition structure in the validated R2-01 trace: R2-01 PASS + R2-05
PASS (prerequisite.py) -> load the exact validated trace (never a new
capture, never model/runtime invocation) -> count layer-local
(source_expert_id, target_expert_id) transition observations at the
token-to-token pairwise-expansion granularity (transition_unit.py) ->
build the primary transition matrix, conditionals, marginals and lift
(matrix.py) -> information-theoretic metrics (info_metrics.py) ->
frequency-controlled null baselines, analytic and shuffled
(null_baseline.py) -> the R2-05 marginal-reconciliation bridge
(bridge.py) -> per-source transition concentration and self-transition
metrics (concentration.py) -> the predeclared support-threshold rule
(support.py) -> the global cross-layer summary (summary.py) -> paper-
ready claims, split SUPPORTED NOW vs NOT YET SUPPORTED, with a
forbidden-claim guard (claims.py) -> figure data (figures.py) ->
scientific outcome classification -> evidence written under the
Research2 evidence root -> registry/state update -> human-readable
summary.

No model weights are loaded, no runtime binary is invoked, no new trace
capture or replay happens anywhere in this module. Determinism is
verified by running the synthesis step TWICE against the identically
loaded trace (and the identically loaded R2-05 bridge evidence, and the
fixed null-baseline seed) and comparing a canonical digest (volatile run
ids/timestamps excluded) -- a mismatch is an immediate FAIL, mirroring
R2-05's own double-synthesis determinism check.
"""

from __future__ import annotations

import csv
import hashlib
import io
import json
from pathlib import Path
from typing import Any, Dict, List, Optional

from .. import provenance, store
from ...research.trace import Trace, TraceError
from . import bridge as bridge_mod
from . import claims as claims_mod
from . import concentration
from . import figures as figures_mod
from . import info_metrics
from . import matrix
from . import null_baseline
from . import prerequisite
from . import summary as summary_mod
from . import support
from . import transition_unit

OUTCOME_PASS = "PASS"
OUTCOME_INCONCLUSIVE = "INCONCLUSIVE"
OUTCOME_FAIL = "FAIL"
OUTCOME_PREREQUISITE_NOT_MET = "R2_01_OR_R2_05_PREREQUISITE_NOT_MET"


def _write_json(path: Path, data: Dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, indent=2, sort_keys=True, default=str), encoding="utf-8")


def _write_text(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


def _json_safe_for_digest(obj: Any) -> Any:
    """Recursively replaces any dict with non-str/int/float/bool/None keys
    (specifically: the (source_expert_id, target_expert_id) TUPLE keys used
    by `counts_per_layer` and each transition table's own "counts" field)
    with a deterministically-ordered list of [key_as_list, value] pairs, so
    the whole synthesis payload -- including the raw transition counts --
    can be hashed via json.dumps without a TypeError. Used ONLY for the
    determinism digest; no JSON evidence FILE ever receives a raw
    tuple-keyed dict (see run()'s own, separately-shaped, file writes)."""
    if isinstance(obj, dict):
        if obj and all(isinstance(k, tuple) for k in obj):
            return [[list(k), _json_safe_for_digest(v)] for k, v in sorted(obj.items())]
        return {k: _json_safe_for_digest(v) for k, v in obj.items()}
    if isinstance(obj, (list, tuple)):
        return [_json_safe_for_digest(v) for v in obj]
    return obj


def _canonical_digest(payload: Dict[str, Any]) -> str:
    safe = _json_safe_for_digest(payload)
    blob = json.dumps(safe, sort_keys=True, default=str).encode("utf-8")
    return hashlib.sha256(blob).hexdigest()


class SynthesisError(ValueError):
    """Raised when the synthesis step itself discovers an internally
    impossible or malformed result (section 19-style FAIL conditions)."""


def _synthesize(trace: Trace, r2_05_frequency_rows: Optional[List[Dict[str, Any]]]
                ) -> Dict[str, Any]:
    """The deterministic synthesis step: a pure function of `trace` and
    `r2_05_frequency_rows` (the R2-05 bridge evidence, loaded ONCE by
    run() and passed in, exactly as R2-05's own orchestrator treats the
    loaded trace as pure input). Called twice by run() with identical
    inputs as a determinism check."""
    n_experts = trace.meta.n_experts or None

    counts_per_layer = transition_unit.count_transitions_per_layer(trace)
    if not transition_unit.expert_id_range_valid(counts_per_layer, trace.meta.n_experts):
        raise SynthesisError("counted transition expert id outside the valid "
                             "[0, n_experts) range for at least one layer")

    independent_totals = transition_unit.independent_count_transitions_per_layer(trace)
    independent_total_sums = {layer: sum(c.values()) for layer, c in independent_totals.items()}
    reconciliation_problems = transition_unit.reconcile_total_transitions(
        counts_per_layer, independent_total_sums)
    if reconciliation_problems:
        raise SynthesisError("; ".join(reconciliation_problems))

    tables = matrix.all_transition_tables(counts_per_layer)

    probability_problems = matrix.probability_mass_valid(tables)
    if probability_problems:
        raise SynthesisError("; ".join(probability_problems))

    layer_info = info_metrics.all_layer_information_metrics(tables)

    layer_source_rows = {layer: concentration.layer_source_concentration_rows(tables[layer])
                         for layer in sorted(tables)}
    layer_aggregate_concentration = {
        layer: concentration.layer_aggregate_concentration(layer_source_rows[layer])
        for layer in sorted(tables)
    }
    layer_self_transitions = {layer: concentration.self_transition_metrics(tables[layer])
                              for layer in sorted(tables)}

    selections_per_layer = {layer: transition_unit.layer_selection_sequence(trace, layer)
                            for layer in trace.layers}

    independence_tables = {layer: null_baseline.analytic_independence_table(tables[layer])
                           for layer in sorted(tables)}
    independence_info = {layer: null_baseline.analytic_independence_information_metrics(
        independence_tables[layer]) for layer in sorted(independence_tables)}

    shuffled_null = {layer: null_baseline.layer_null_baseline(layer, selections_per_layer[layer])
                     for layer in sorted(selections_per_layer)}

    bridge_result = None
    if r2_05_frequency_rows is not None:
        target_marginals = {layer: tables[layer]["target_marginal"] for layer in tables}
        bridge_result = bridge_mod.reconcile_all_layers(target_marginals, r2_05_frequency_rows)

    global_summary = summary_mod.global_summary(layer_info, layer_source_rows,
                                                layer_self_transitions, tables)

    claim_set = claims_mod.build_claims(global_summary, layer_self_transitions)
    claims_mod.check_no_forbidden_claims(claim_set)

    fig_a = figures_mod.figure_a_nmi_by_layer(layer_info)
    fig_b = figures_mod.figure_b_observed_vs_marginal_expected(tables, independence_tables)
    fig_c = figures_mod.figure_c_strongest_supported_lifts(tables)
    fig_d = figures_mod.figure_d_self_transition_lift_by_layer(layer_self_transitions)

    total_mass = transition_unit.total_transition_mass(counts_per_layer)
    headline = {
        "total_transition_observations": total_mass,
        "layer_count": len(counts_per_layer),
        "nominal_experts_per_layer": n_experts,
        "median_nmi": global_summary["median_nmi"],
        "min_nmi": global_summary["min_nmi"],
        "max_nmi": global_summary["max_nmi"],
        "fraction_layers_with_nmi_positive": global_summary["fraction_layers_with_nmi_positive"],
        "median_conditional_entropy": global_summary["median_conditional_entropy"],
        "median_self_transition_lift": global_summary["median_self_transition_lift"],
        "median_top_1_outgoing_probability": global_summary["median_top_1_outgoing_probability"],
        "median_outgoing_effective_target_fraction": global_summary[
            "median_outgoing_effective_target_fraction"],
        "max_supported_transition_lift": global_summary["max_supported_transition_lift"],
        "max_supported_transition_lift_layer": global_summary[
            "max_supported_transition_lift_layer"],
        "strongest_layer_by_nmi": global_summary["strongest_layer_by_nmi"],
        "weakest_layer_by_nmi": global_summary["weakest_layer_by_nmi"],
        "bridge_all_layers_compatible": (bridge_result["all_layers_compatible"]
                                         if bridge_result is not None else None),
    }

    return {
        "n_experts": n_experts,
        "counts_per_layer": counts_per_layer,
        "tables": tables,
        "layer_info": layer_info,
        "layer_source_rows": layer_source_rows,
        "layer_aggregate_concentration": layer_aggregate_concentration,
        "layer_self_transitions": layer_self_transitions,
        "independence_tables": independence_tables,
        "independence_info": independence_info,
        "shuffled_null": shuffled_null,
        "bridge_result": bridge_result,
        "global_summary": global_summary,
        "claims": claim_set,
        "figure_a_nmi_by_layer": fig_a,
        "figure_b_observed_vs_marginal_expected": fig_b,
        "figure_c_strongest_supported_lifts": fig_c,
        "figure_d_self_transition_lift_by_layer": fig_d,
        "headline": headline,
    }


def run(*, allow_dirty: bool = False, **_ignored: Any) -> Dict[str, Any]:
    rec = provenance.build_record("R2-06", outcome="PENDING", allow_dirty=allow_dirty)
    run_id = rec["identity"]["run_id"]
    rec["identity"]["start_utc"] = provenance.utc_now_iso()

    binding = prerequisite.require_r2_01_pass_and_r2_05_pass()
    if not binding.ok:
        return {
            "experiment_id": "R2-06", "run_id": run_id,
            "outcome": OUTCOME_PREREQUISITE_NOT_MET,
            "message": binding.message,
            "prerequisite_status": binding.status,
        }

    try:
        trace = Trace.load(binding.trace_path)
    except (TraceError, OSError, UnicodeDecodeError, json.JSONDecodeError) as e:
        return {
            "experiment_id": "R2-06", "run_id": run_id, "outcome": OUTCOME_FAIL,
            "message": f"could not load the bound R2-01 trace: {e}",
            "trace_path": binding.trace_path,
        }

    if not trace.meta.n_experts:
        inconclusive_reasons = [
            "the bound trace carries no authoritative nominal expert count "
            "(trace.meta.n_experts); R2-06 refuses to silently proceed without "
            "one (mirrors R2-05's own INCONCLUSIVE condition)."
        ]
        rec["identity"]["end_utc"] = provenance.utc_now_iso()
        rec["identity"]["outcome"] = OUTCOME_INCONCLUSIVE
        run_dir = store.run_dir("R2-06", run_id)
        _write_json(run_dir / "manifest.json", rec)
        summary = {
            "experiment_id": "R2-06", "run_id": run_id, "outcome": OUTCOME_INCONCLUSIVE,
            "reasons": inconclusive_reasons, "binding": binding.to_json(),
        }
        _write_json(run_dir / "summary.json", summary)
        store.append_registry_entry({"identity": rec["identity"], "run_dir": str(run_dir)})
        store.set_experiment_state("R2-06", "INCONCLUSIVE")
        return {**summary, "run_dir": str(run_dir)}

    r2_05_evidence = bridge_mod.load_r2_05_canonical_evidence()
    r2_05_frequency_rows = r2_05_evidence["frequency_rows"] if r2_05_evidence else None

    synthesis_errors: List[str] = []
    try:
        synth_a = _synthesize(trace, r2_05_frequency_rows)
        synth_b = _synthesize(trace, r2_05_frequency_rows)
    except SynthesisError as e:
        synthesis_errors.append(str(e))
        synth_a = synth_b = None
    except claims_mod.ForbiddenClaimError as e:
        synthesis_errors.append(f"generated claim violated the naming safeguard: {e}")
        synth_a = synth_b = None

    nondeterminism: List[str] = []
    digest_a = digest_b = None
    if synth_a is not None and synth_b is not None:
        digest_a = _canonical_digest(synth_a)
        digest_b = _canonical_digest(synth_b)
        if digest_a != digest_b:
            nondeterminism.append(
                f"two independent syntheses over the identically loaded trace (and "
                f"identical R2-05 bridge evidence, identical null-baseline seed/"
                f"config) produced different canonical digests ({digest_a} != "
                f"{digest_b})")

    outcome, reasons = classify_outcome(synthesis_errors, nondeterminism, synth_a)

    rec["identity"]["end_utc"] = provenance.utc_now_iso()
    rec["identity"]["outcome"] = outcome
    rec["experiment"]["parameters"] = {
        "r2_01_run_id": binding.r2_01_run_id, "r2_04_run_id": binding.r2_04_run_id,
        "r2_05_run_id": binding.r2_05_run_id, "candidate_status": binding.candidate_status,
        "transition_unit": transition_unit.TRANSITION_UNIT_DEFINITION,
        "min_source_support": support.MIN_SOURCE_SUPPORT,
        "min_pair_support": support.MIN_PAIR_SUPPORT,
        "null_baseline_seed": null_baseline.NULL_BASELINE_SEED,
    }
    rec["artifacts"]["model"] = None

    run_dir = store.run_dir("R2-06", run_id)
    _write_json(run_dir / "manifest.json", rec)
    _write_json(run_dir / "provenance.json", {
        "binding": binding.to_json(),
        "canonical_digest": digest_a,
        "synthesis_errors": synthesis_errors,
        "nondeterminism": nondeterminism,
        "transition_unit_definition": transition_unit.TRANSITION_UNIT_DEFINITION,
        "support_threshold_rationale": support.SUPPORT_THRESHOLD_RATIONALE,
        "r2_05_bridge_run_id": r2_05_evidence["r2_05_run_id"] if r2_05_evidence else None,
    })

    synth = synth_a or {}
    if synth.get("tables"):
        all_rows: List[Dict[str, Any]] = []
        for layer in sorted(synth["tables"]):
            all_rows.extend(matrix.transition_rows(layer, synth["tables"][layer]))
        _write_text(run_dir / "transition_metrics.csv", _transition_rows_to_csv(all_rows))

    _write_text(run_dir / "layer_transition_summary.csv",
               _layer_summary_to_csv(synth.get("layer_info", {}),
                                     synth.get("layer_self_transitions", {}),
                                     synth.get("layer_aggregate_concentration", {})))

    _write_json(run_dir / "transition_structure.json", {
        "layer_info": {str(k): v for k, v in synth.get("layer_info", {}).items()},
        "layer_source_rows": {str(k): v for k, v in synth.get("layer_source_rows", {}).items()},
        "layer_aggregate_concentration": {str(k): v for k, v in synth.get(
            "layer_aggregate_concentration", {}).items()},
        "layer_self_transitions": {str(k): v for k, v in synth.get(
            "layer_self_transitions", {}).items()},
        "bridge_result": synth.get("bridge_result"),
        "global_summary": synth.get("global_summary", {}),
    })

    _write_json(run_dir / "null_baseline.json", {
        "analytic_independence_info": {str(k): v for k, v in synth.get(
            "independence_info", {}).items()},
        "shuffled_null": {str(k): v for k, v in synth.get("shuffled_null", {}).items()},
        "seed": null_baseline.NULL_BASELINE_SEED,
    })

    if synth.get("claims") is not None:
        _write_json(run_dir / "claims.json", synth["claims"])

    _write_json(run_dir / "figure_data.json", {
        "figure_a_nmi_by_layer": synth.get("figure_a_nmi_by_layer", []),
        "figure_b_observed_vs_marginal_expected": {str(k): v for k, v in synth.get(
            "figure_b_observed_vs_marginal_expected", {}).items()},
        "figure_c_strongest_supported_lifts": synth.get("figure_c_strongest_supported_lifts", []),
        "figure_d_self_transition_lift_by_layer": synth.get(
            "figure_d_self_transition_lift_by_layer", []),
    })

    summary = {
        "experiment_id": "R2-06", "run_id": run_id, "outcome": outcome,
        "scope": "offline DESCRIPTIVE CHARACTERIZATION of SAME-LAYER token-to-token "
                "expert-routing transition structure on the validated R2-01 trace -- "
                "no new replay, no model/runtime invocation, no new trace capture, no "
                "predictor, no accuracy claim, no reuse-distance or temporal-drift "
                "measurement, no cross-layer primary claim",
        "transition_unit": transition_unit.TRANSITION_UNIT_DEFINITION,
        "reasons": reasons,
        "candidate_status": rec["git"]["candidate_status"],
        "binding": binding.to_json(),
        "canonical_digest": digest_a,
        "headline": synth.get("headline", {}),
        "synthesis_errors": synthesis_errors,
        "nondeterminism": nondeterminism,
        "roadmap_state": {
            "ii_a": "CLOSED/PASS/PAPER-FROZEN",
            "r2_05": "CLOSED/PASS", "r2_06": outcome,
            "r2_07": "LOCKED", "r2_08": "LOCKED",
            "ii_c": "FUTURE",
        },
    }
    _write_json(run_dir / "summary.json", summary)
    _write_text(run_dir / "summary.md", _summary_markdown(summary))

    store.append_registry_entry({"identity": rec["identity"], "run_dir": str(run_dir)})
    new_state = "COMPLETED" if outcome == OUTCOME_PASS else (
        "INCONCLUSIVE" if outcome == OUTCOME_INCONCLUSIVE else "FAILED")
    for step_state in ("IMPLEMENTED", "READY", "RUNNING"):
        store.set_experiment_state("R2-06", step_state)
    store.set_experiment_state("R2-06", new_state)

    return {**summary, "run_dir": str(run_dir)}


def classify_outcome(synthesis_errors: List[str], nondeterminism: List[str],
                     synth: Optional[Dict[str, Any]]) -> "tuple[str, List[str]]":
    """Section 20-style outcome rules, applied mechanically. PASS does
    NOT require NMI > 0 anywhere -- a trace with no detectable same-
    layer transition structure beyond marginal frequency is valid PASS
    science too, exactly as R2-05's uniform-routing result was."""
    if synthesis_errors:
        return OUTCOME_FAIL, list(synthesis_errors)
    if nondeterminism:
        return OUTCOME_FAIL, list(nondeterminism)
    if synth is None:
        return OUTCOME_FAIL, ["synthesis produced no result for an unspecified reason"]

    return OUTCOME_PASS, [
        "R2-01 PASS and R2-05 PASS verified; trace identity re-verified byte-"
        "identical on disk; expert identity treated layer-locally throughout; "
        "transition counts reconciled exactly against an independently-coded "
        "second computation; conditional probability mass per source expert "
        "sums to 1.0 within tolerance; the minimum transition support threshold "
        "was predeclared before this run and applied only to headline/strong-"
        "transition summaries, never to raw per-cell evidence; two independent "
        "syntheses over the identical trace, identical R2-05 bridge evidence, "
        "and identical null-baseline seed/config produced an identical "
        "canonical digest; no forbidden claim (predictor accuracy, reuse "
        "distance, temporal drift, cross-layer primary evidence, assumed "
        "self-transition dominance) was generated."
    ]


def _transition_rows_to_csv(rows: List[Dict[str, Any]]) -> str:
    buf = io.StringIO()
    fields = ["layer_id", "source_expert_id", "target_expert_id", "transition_count",
              "conditional_probability", "source_marginal_probability",
              "target_marginal_probability", "lift"]
    writer = csv.DictWriter(buf, fieldnames=fields)
    writer.writeheader()
    for row in rows:
        writer.writerow({k: row[k] for k in fields})
    return buf.getvalue()


def _layer_summary_to_csv(layer_info: Dict[int, Dict[str, Any]],
                          layer_self_transitions: Dict[int, Dict[str, Any]],
                          layer_aggregate_concentration: Dict[int, Dict[str, Any]]) -> str:
    buf = io.StringIO()
    fields = ["layer_id", "marginal_entropy", "conditional_entropy", "mutual_information",
              "normalized_mutual_information", "aggregate_self_transition_share",
              "median_self_transition_lift", "median_top_1_target_share",
              "median_outgoing_effective_target_count"]
    writer = csv.DictWriter(buf, fieldnames=fields)
    writer.writeheader()
    for layer in sorted(layer_info):
        info = layer_info[layer]
        st = layer_self_transitions.get(layer, {})
        agg = layer_aggregate_concentration.get(layer, {})
        writer.writerow({
            "layer_id": layer, "marginal_entropy": info.get("marginal_entropy"),
            "conditional_entropy": info.get("conditional_entropy"),
            "mutual_information": info.get("mutual_information"),
            "normalized_mutual_information": info.get("normalized_mutual_information"),
            "aggregate_self_transition_share": st.get("aggregate_self_transition_share"),
            "median_self_transition_lift": st.get("median_self_transition_lift"),
            "median_top_1_target_share": agg.get("median_top_1_target_share"),
            "median_outgoing_effective_target_count": agg.get(
                "median_outgoing_effective_target_count"),
        })
    return buf.getvalue()


def _summary_markdown(summary: Dict[str, Any]) -> str:
    lines = [
        f"# R2-06 run {summary['run_id']}", "",
        f"**Outcome:** {summary['outcome']}", "",
        f"**Scope:** {summary['scope']}", "",
        f"**Transition unit:** {summary['transition_unit']}", "",
        "## Reasons", "",
    ]
    lines += [f"- {r}" for r in summary["reasons"]] or ["- (none)"]
    lines += ["", "## Headline", ""]
    for k, v in summary.get("headline", {}).items():
        lines.append(f"- **{k}**: {v}")
    return "\n".join(lines)


def analyse(*, run_id: Optional[str] = None, **_ignored: Any) -> Dict[str, Any]:
    registry = [e for e in store.load_registry()
               if e.get("identity", {}).get("experiment_id") == "R2-06"]
    if not registry:
        return {"experiment_id": "R2-06", "outcome": "NOT_IMPLEMENTED",
                "message": "no R2-06 run exists yet to analyse"}

    if run_id:
        matches = [e for e in registry if e["identity"]["run_id"] == run_id]
        if not matches:
            raise KeyError(f"no R2-06 run with run_id={run_id!r}")
        entry = matches[0]
    else:
        completed = [e for e in registry if e["identity"]["outcome"] in
                    (OUTCOME_PASS, OUTCOME_INCONCLUSIVE, OUTCOME_FAIL)]
        if not completed:
            return {"experiment_id": "R2-06", "outcome": "NOT_IMPLEMENTED",
                    "message": "no completed R2-06 run exists yet to analyse"}
        latest_ends = sorted({e["identity"]["end_utc"] for e in completed}, reverse=True)
        newest = [e for e in completed if e["identity"]["end_utc"] == latest_ends[0]]
        if len(newest) > 1:
            raise ValueError(
                f"{len(newest)} R2-06 runs share the latest end_utc "
                f"({latest_ends[0]}): 'latest' is ambiguous. Pass an explicit run_id: "
                f"{[e['identity']['run_id'] for e in newest]}")
        entry = newest[0]

    run_dir = Path(entry["run_dir"])
    summary = json.loads((run_dir / "summary.json").read_text(encoding="utf-8"))
    return {**summary, "run_dir": str(run_dir), "analysed_from_existing_evidence": True}
