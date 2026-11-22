"""R2-07 orchestration: the full workflow behind `argine research2 run
R2-07` / `argine research2 analyse R2-07`.

Fully offline DESCRIPTIVE CHARACTERIZATION of how far layer-local expert
reuse locality persists through the routing sequence, and how quickly
the active expert working set grows as the observation window expands,
on the validated R2-01 trace: R2-01 PASS + R2-04 PASS/II-A-closed +
R2-05 PASS + R2-06 PASS (prerequisite.py) -> load the exact validated
trace (never a new capture, never model/runtime invocation) -> compute
layer-local reuse events, token reuse gaps, and distinct-intervening
reuse distances (temporal_unit.py) -> their distributions
(reuse_stats.py) -> rolling working-set metrics and the growth curve
(working_set.py) -> the frequency-preserving whole-tuple-shuffle null
baseline (null_baseline.py) -> the R2-05/R2-06 bridges and the
secondary frequency-vs-reuse-gap Spearman correlation (bridge.py) ->
the global cross-layer summary and observed-vs-null comparison
(summary.py) -> paper-ready claims, split SUPPORTED NOW vs NOT YET
SUPPORTED, with a forbidden-claim guard (claims.py) -> figure data
(figures.py) -> scientific outcome classification -> evidence written
under the Research2 evidence root -> registry/state update -> human-
readable summary.

No model weights are loaded, no runtime binary is invoked, no new trace
capture or replay happens anywhere in this module. Determinism is
verified by running the synthesis step TWICE against the identically
loaded trace (identical R2-05/R2-06 bridge evidence, identical null-
baseline seed/config) and comparing a canonical digest (volatile run
ids/timestamps excluded) -- a mismatch is an immediate FAIL, mirroring
R2-05's/R2-06's own double-synthesis determinism check.
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
from . import figures as figures_mod
from . import null_baseline
from . import prerequisite
from . import reuse_stats
from . import summary as summary_mod
from . import temporal_unit
from . import working_set

OUTCOME_PASS = "PASS"
OUTCOME_INCONCLUSIVE = "INCONCLUSIVE"
OUTCOME_FAIL = "FAIL"
OUTCOME_PREREQUISITE_NOT_MET = "UPSTREAM_PREREQUISITE_NOT_MET"

_COMPARISON_WINDOWS = (8, 32, 128, 256)


def _write_json(path: Path, data: Dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, indent=2, sort_keys=True, default=str), encoding="utf-8")


def _write_text(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


def _json_safe_for_digest(obj: Any) -> Any:
    """Recursively replaces any dict with non-str/int/float/bool/None keys
    (specifically: (layer_id) tuple-free here, but kept for structural
    symmetry with R2-06's own digest-safety helper, and because
    `layer_window_metrics` dicts are keyed by plain ints, which json
    already serializes fine -- this still guards defensively against any
    future tuple-keyed structure making it into the digest payload)."""
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
    impossible or malformed result (section 19/28/29/30-style FAIL
    conditions)."""


def _representative_layers(layer_gap_distributions: Dict[int, Dict[str, Any]]) -> List[int]:
    """Deterministic, data-driven (never arbitrary) choice of
    representative layers for the figure candidates (section 22):
    shortest median reuse gap, longest median reuse gap, and the layer
    whose median reuse gap is closest to the cross-layer median (ties
    broken by the smallest layer id) -- mirrors R2-05's own
    `_representative_layers` pattern exactly."""
    with_data = {l: d["median"] for l, d in layer_gap_distributions.items()
                if d["median"] is not None}
    if not with_data:
        return []
    by_gap = sorted(with_data.items(), key=lambda kv: (kv[1], kv[0]))
    values = [v for _, v in by_gap]
    median_value = values[len(values) // 2] if len(values) % 2 == 1 else (
        values[len(values) // 2 - 1] + values[len(values) // 2]) / 2.0
    median_layer = min(with_data, key=lambda l: (abs(with_data[l] - median_value), l))
    shortest = by_gap[0][0]
    longest = by_gap[-1][0]
    return sorted(set([shortest, median_layer, longest]))


def _synthesize(trace: Trace, r2_05_frequency_rows: Optional[List[Dict[str, Any]]],
                r2_06_evidence: Optional[Dict[str, Any]]) -> Dict[str, Any]:
    """The deterministic synthesis step: a pure function of `trace`,
    `r2_05_frequency_rows`, and `r2_06_evidence` -- all loaded ONCE by
    run() and passed in, exactly as R2-05's/R2-06's own orchestrators
    treat their loaded upstream evidence as pure input. Called twice by
    run() with identical inputs as a determinism check."""
    n_experts = trace.meta.n_experts or None

    layer_reuse: Dict[int, Dict[str, Any]] = {}
    layer_gap_dist: Dict[int, Dict[str, Any]] = {}
    layer_distinct_dist: Dict[int, Dict[str, Any]] = {}
    layer_window_metrics: Dict[int, Dict[int, Dict[str, Any]]] = {}
    layer_growth_curve: Dict[int, List[Dict[str, Any]]] = {}
    layer_null: Dict[int, Dict[str, Any]] = {}
    layer_null_gap_dist: Dict[int, Dict[str, Any]] = {}
    layer_null_distinct_dist: Dict[int, Dict[str, Any]] = {}
    layer_comparisons: Dict[int, Dict[str, Any]] = {}
    layer_observed_expert_count: Dict[int, int] = {}

    for layer in sorted(trace.layers):
        reuse = temporal_unit.compute_layer_reuse_events(trace, layer)

        if n_experts:
            for selection in reuse["positions"]:
                for expert_id in selection:
                    if not (0 <= expert_id < n_experts):
                        raise SynthesisError(f"layer {layer}: expert id {expert_id} outside "
                                             f"the valid [0, {n_experts}) range")

        indep = temporal_unit.independent_reuse_accounting(trace, layer)
        problems = temporal_unit.reconcile_layer_accounting(reuse, indep)
        if problems:
            raise SynthesisError("; ".join(problems))

        gaps = [ev["token_reuse_gap"] for ev in reuse["reuse_events"]]
        distances = [ev["distinct_intervening_reuse_distance"] for ev in reuse["reuse_events"]]
        gap_dist = reuse_stats.token_reuse_gap_distribution(gaps)
        distinct_dist = reuse_stats.distinct_intervening_distribution(distances)

        window_metrics = working_set.layer_working_set_metrics(
            reuse["positions"], nominal_expert_count=n_experts)
        growth = working_set.growth_curve(reuse["positions"])
        observed_count = working_set.observed_expert_count(reuse["positions"])

        null_bundle = null_baseline.layer_null_baseline(reuse["positions"],
                                                         nominal_expert_count=n_experts)
        if null_bundle["invariant_problems"]:
            raise SynthesisError(f"layer {layer}: null-baseline invariant violated: "
                                 f"{'; '.join(null_bundle['invariant_problems'])}")

        null_gaps = [ev["token_reuse_gap"] for ev in null_bundle["reuse_events"]]
        null_distances = [ev["distinct_intervening_reuse_distance"]
                          for ev in null_bundle["reuse_events"]]
        null_gap_dist = reuse_stats.token_reuse_gap_distribution(null_gaps)
        null_distinct_dist = reuse_stats.distinct_intervening_distribution(null_distances)

        comparison = {
            "immediate_reuse_fraction": {
                "observed": gap_dist["within_threshold_fraction"].get(1),
                "null": null_gap_dist["within_threshold_fraction"].get(1)},
            "reuse_within_4_fraction": {
                "observed": gap_dist["within_threshold_fraction"].get(4),
                "null": null_gap_dist["within_threshold_fraction"].get(4)},
            "reuse_within_16_fraction": {
                "observed": gap_dist["within_threshold_fraction"].get(16),
                "null": null_gap_dist["within_threshold_fraction"].get(16)},
            "median_token_reuse_gap": {"observed": gap_dist["median"],
                                       "null": null_gap_dist["median"]},
            "p90_token_reuse_gap": {"observed": gap_dist["p90"], "null": null_gap_dist["p90"]},
            "median_distinct_intervening_reuse_distance": {
                "observed": distinct_dist["median"], "null": null_distinct_dist["median"]},
            "working_set_size": {
                w: {"observed": window_metrics.get(w, {}).get("median_working_set_size"),
                   "null": null_bundle["window_metrics"].get(w, {}).get(
                       "median_working_set_size")}
                for w in _COMPARISON_WINDOWS
            },
        }

        layer_reuse[layer] = reuse
        layer_gap_dist[layer] = gap_dist
        layer_distinct_dist[layer] = distinct_dist
        layer_window_metrics[layer] = window_metrics
        layer_growth_curve[layer] = growth
        layer_null[layer] = null_bundle
        layer_null_gap_dist[layer] = null_gap_dist
        layer_null_distinct_dist[layer] = null_distinct_dist
        layer_comparisons[layer] = comparison
        layer_observed_expert_count[layer] = observed_count

    global_summary = summary_mod.global_summary(layer_gap_dist, layer_distinct_dist,
                                                 layer_window_metrics, layer_comparisons)

    layer_reuse_events_only = {l: r["reuse_events"] for l, r in layer_reuse.items()}
    if r2_05_frequency_rows is not None:
        bridge_correlation = bridge_mod.frequency_vs_reuse_gap_correlation(
            r2_05_frequency_rows, layer_reuse_events_only)
    else:
        bridge_correlation = {"n_paired_observations": 0, "spearman_correlation": None,
                              "sample": [], "note": "R2-05 bridge evidence unavailable."}

    claim_set = claims_mod.build_claims(global_summary, bridge_correlation)
    claims_mod.check_no_forbidden_claims(claim_set)

    representative_layers = _representative_layers(layer_gap_dist)
    fig_a = {l: figures_mod.figure_a_reuse_gap_cdf(
        [ev["token_reuse_gap"] for ev in layer_reuse[l]["reuse_events"]])
        for l in representative_layers}
    fig_b = {l: figures_mod.figure_b_distinct_intervening_cdf(
        [ev["distinct_intervening_reuse_distance"] for ev in layer_reuse[l]["reuse_events"]])
        for l in representative_layers}
    fig_c = {l: figures_mod.figure_c_working_set_growth_curve(layer_growth_curve[l])
            for l in representative_layers}
    fig_d = {l: figures_mod.figure_d_observed_vs_shuffled_working_set(
        layer_growth_curve[l], layer_null[l]["growth_curve"]) for l in representative_layers}

    total_reuse_events = sum(len(r["reuse_events"]) for r in layer_reuse.values())
    total_first_observations = sum(r["cold_observation_count"] for r in layer_reuse.values())
    headline = {
        "total_reuse_events": total_reuse_events,
        "total_first_observations": total_first_observations,
        "layer_count": len(layer_reuse),
        "nominal_experts_per_layer": n_experts,
        "median_immediate_reuse_fraction": global_summary["median_immediate_reuse_fraction"],
        "median_reuse_within_4_fraction": global_summary["median_reuse_within_4_fraction"],
        "median_reuse_within_16_fraction": global_summary["median_reuse_within_16_fraction"],
        "median_token_reuse_gap": global_summary["median_token_reuse_gap"],
        "median_p90_token_reuse_gap": global_summary["median_p90_token_reuse_gap"],
        "median_distinct_intervening_reuse_distance": global_summary[
            "median_distinct_intervening_reuse_distance"],
        "median_working_set_size_by_window": global_summary["median_working_set_size_by_window"],
        "observed_vs_null": global_summary["observed_vs_null"],
        "shortest_median_reuse_gap_layer": global_summary["heterogeneity"][
            "shortest_median_reuse_gap_layer"],
        "longest_median_reuse_gap_layer": global_summary["heterogeneity"][
            "longest_median_reuse_gap_layer"],
        "frequency_vs_reuse_gap_spearman_correlation": bridge_correlation.get(
            "spearman_correlation"),
    }

    return {
        "n_experts": n_experts,
        "layer_reuse": layer_reuse,
        "layer_gap_dist": layer_gap_dist,
        "layer_distinct_dist": layer_distinct_dist,
        "layer_window_metrics": layer_window_metrics,
        "layer_growth_curve": layer_growth_curve,
        "layer_null": layer_null,
        "layer_comparisons": layer_comparisons,
        "layer_observed_expert_count": layer_observed_expert_count,
        "global_summary": global_summary,
        "bridge_correlation": bridge_correlation,
        "claims": claim_set,
        "representative_layers": representative_layers,
        "figure_a_reuse_gap_cdf": fig_a,
        "figure_b_distinct_intervening_cdf": fig_b,
        "figure_c_working_set_growth_curve": fig_c,
        "figure_d_observed_vs_shuffled_working_set": fig_d,
        "headline": headline,
    }


def run(*, allow_dirty: bool = False, **_ignored: Any) -> Dict[str, Any]:
    rec = provenance.build_record("R2-07", outcome="PENDING", allow_dirty=allow_dirty)
    run_id = rec["identity"]["run_id"]
    rec["identity"]["start_utc"] = provenance.utc_now_iso()

    binding = prerequisite.require_full_chain_pass()
    if not binding.ok:
        return {
            "experiment_id": "R2-07", "run_id": run_id,
            "outcome": OUTCOME_PREREQUISITE_NOT_MET,
            "message": binding.message,
            "prerequisite_status": binding.status,
        }

    try:
        trace = Trace.load(binding.trace_path)
    except (TraceError, OSError, UnicodeDecodeError, json.JSONDecodeError) as e:
        return {
            "experiment_id": "R2-07", "run_id": run_id, "outcome": OUTCOME_FAIL,
            "message": f"could not load the bound R2-01 trace: {e}",
            "trace_path": binding.trace_path,
        }

    if not trace.meta.n_experts:
        inconclusive_reasons = [
            "the bound trace carries no authoritative nominal expert count "
            "(trace.meta.n_experts); normalized working-set metrics would not be "
            "comparable across layers without one -- R2-07 refuses to silently "
            "proceed without one (mirrors R2-05's/R2-06's own INCONCLUSIVE "
            "condition)."
        ]
        rec["identity"]["end_utc"] = provenance.utc_now_iso()
        rec["identity"]["outcome"] = OUTCOME_INCONCLUSIVE
        run_dir = store.run_dir("R2-07", run_id)
        _write_json(run_dir / "manifest.json", rec)
        summary = {
            "experiment_id": "R2-07", "run_id": run_id, "outcome": OUTCOME_INCONCLUSIVE,
            "reasons": inconclusive_reasons, "binding": binding.to_json(),
        }
        _write_json(run_dir / "summary.json", summary)
        store.append_registry_entry({"identity": rec["identity"], "run_dir": str(run_dir)})
        store.set_experiment_state("R2-07", "INCONCLUSIVE")
        return {**summary, "run_dir": str(run_dir)}

    r2_05_evidence = bridge_mod.load_r2_05_canonical_evidence()
    r2_05_frequency_rows = r2_05_evidence["frequency_rows"] if r2_05_evidence else None
    r2_06_evidence = bridge_mod.load_r2_06_canonical_evidence()

    synthesis_errors: List[str] = []
    try:
        synth_a = _synthesize(trace, r2_05_frequency_rows, r2_06_evidence)
        synth_b = _synthesize(trace, r2_05_frequency_rows, r2_06_evidence)
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
                f"identical R2-05/R2-06 bridge evidence, identical null-baseline "
                f"seed/config) produced different canonical digests ({digest_a} != "
                f"{digest_b})")

    outcome, reasons = classify_outcome(synthesis_errors, nondeterminism, synth_a)

    rec["identity"]["end_utc"] = provenance.utc_now_iso()
    rec["identity"]["outcome"] = outcome
    rec["experiment"]["parameters"] = {
        "r2_01_run_id": binding.r2_01_run_id, "r2_04_run_id": binding.r2_04_run_id,
        "r2_05_run_id": binding.r2_05_run_id, "r2_06_run_id": binding.r2_06_run_id,
        "candidate_status": binding.candidate_status,
        "temporal_unit": temporal_unit.TEMPORAL_UNIT_DEFINITION,
        "reuse_event_definition": temporal_unit.REUSE_EVENT_DEFINITION,
        "distinct_intervening_definition": temporal_unit.DISTINCT_INTERVENING_DEFINITION,
        "quantile_method": reuse_stats.QUANTILE_METHOD_DEFINITION,
        "token_gap_thresholds": list(reuse_stats.TOKEN_GAP_THRESHOLDS),
        "distinct_intervening_thresholds": list(reuse_stats.DISTINCT_INTERVENING_THRESHOLDS),
        "canonical_windows": list(working_set.CANONICAL_WINDOWS),
        "null_baseline_seed": null_baseline.NULL_BASELINE_SEED,
        "null_baseline_replicate_count": null_baseline.NULL_BASELINE_REPLICATE_COUNT,
        "r2_05_canonical_digest": (r2_05_evidence or {}).get("r2_05_canonical_digest"),
        "r2_06_canonical_digest": (r2_06_evidence or {}).get("r2_06_canonical_digest"),
    }
    rec["artifacts"]["model"] = None

    run_dir = store.run_dir("R2-07", run_id)
    _write_json(run_dir / "manifest.json", rec)
    _write_json(run_dir / "provenance.json", {
        "binding": binding.to_json(),
        "canonical_digest": digest_a,
        "synthesis_errors": synthesis_errors,
        "nondeterminism": nondeterminism,
        "temporal_unit_definition": temporal_unit.TEMPORAL_UNIT_DEFINITION,
        "reuse_event_definition": temporal_unit.REUSE_EVENT_DEFINITION,
        "distinct_intervening_definition": temporal_unit.DISTINCT_INTERVENING_DEFINITION,
        "r2_06_bridge_run_id": r2_06_evidence["r2_06_run_id"] if r2_06_evidence else None,
        "r2_05_bridge_run_id": r2_05_evidence["r2_05_run_id"] if r2_05_evidence else None,
    })

    synth = synth_a or {}
    if synth.get("layer_reuse"):
        all_rows: List[Dict[str, Any]] = []
        for layer in sorted(synth["layer_reuse"]):
            for ev in synth["layer_reuse"][layer]["reuse_events"]:
                all_rows.append({"layer_id": layer, **ev})
        _write_text(run_dir / "reuse_events.csv", _reuse_events_to_csv(all_rows))

    _write_text(run_dir / "layer_reuse_summary.csv",
               _layer_summary_to_csv(synth.get("layer_gap_dist", {}),
                                     synth.get("layer_distinct_dist", {}),
                                     synth.get("layer_reuse", {})))

    _write_text(run_dir / "working_set_by_window.csv",
               _working_set_to_csv(synth.get("layer_window_metrics", {})))

    _write_json(run_dir / "reuse_structure.json", {
        "layer_gap_dist": {str(k): v for k, v in synth.get("layer_gap_dist", {}).items()},
        "layer_distinct_dist": {str(k): v for k, v in
                               synth.get("layer_distinct_dist", {}).items()},
        "layer_window_metrics": {str(k): v for k, v in
                                synth.get("layer_window_metrics", {}).items()},
        "layer_growth_curve": {str(k): v for k, v in synth.get("layer_growth_curve", {}).items()},
        "layer_observed_expert_count": {str(k): v for k, v in
                                       synth.get("layer_observed_expert_count", {}).items()},
        "layer_comparisons": {str(k): v for k, v in synth.get("layer_comparisons", {}).items()},
        "global_summary": synth.get("global_summary", {}),
    })

    _write_json(run_dir / "null_baseline.json", {
        "seed": null_baseline.NULL_BASELINE_SEED,
        "n_replicates": null_baseline.NULL_BASELINE_REPLICATE_COUNT,
        "shuffle_unit": null_baseline.SHUFFLE_UNIT_DEFINITION,
        "layer_null": {str(k): {"invariant_problems": v["invariant_problems"],
                                "n_positions": v["n_positions"],
                                "cold_observation_count": v["cold_observation_count"],
                                "reuse_event_count": v["reuse_event_count"],
                                "window_metrics": v["window_metrics"],
                                "growth_curve": v["growth_curve"]}
                       for k, v in synth.get("layer_null", {}).items()},
    })

    _write_json(run_dir / "bridge.json", {
        "r2_06_bridge_note": claims_mod.R206_BRIDGE_NOTE,
        "r2_05_bridge_note": claims_mod.R205_BRIDGE_NOTE,
        "r2_06_headline": (r2_06_evidence or {}).get("r2_06_headline"),
        "frequency_vs_reuse_gap_correlation": synth.get("bridge_correlation", {}),
    })

    if synth.get("claims") is not None:
        _write_json(run_dir / "claims.json", synth["claims"])

    _write_json(run_dir / "figure_data.json", {
        "figure_a_reuse_gap_cdf": {str(k): v for k, v in
                                  synth.get("figure_a_reuse_gap_cdf", {}).items()},
        "figure_b_distinct_intervening_cdf": {str(k): v for k, v in
                                             synth.get("figure_b_distinct_intervening_cdf",
                                                      {}).items()},
        "figure_c_working_set_growth_curve": {str(k): v for k, v in
                                             synth.get("figure_c_working_set_growth_curve",
                                                      {}).items()},
        "figure_d_observed_vs_shuffled_working_set": {str(k): v for k, v in synth.get(
            "figure_d_observed_vs_shuffled_working_set", {}).items()},
        "representative_layers": synth.get("representative_layers", []),
    })

    summary = {
        "experiment_id": "R2-07", "run_id": run_id, "outcome": outcome,
        "scope": "offline DESCRIPTIVE CHARACTERIZATION of layer-local expert reuse "
                "locality and working-set growth on the validated R2-01 trace -- no "
                "new replay, no model/runtime invocation, no new trace capture, no "
                "predictor, no accuracy claim, no retention-policy performance claim, "
                "no temporal-drift or cross-workload claim",
        "temporal_unit": temporal_unit.TEMPORAL_UNIT_DEFINITION,
        "reasons": reasons,
        "candidate_status": rec["git"]["candidate_status"],
        "binding": binding.to_json(),
        "canonical_digest": digest_a,
        "headline": synth.get("headline", {}),
        "synthesis_errors": synthesis_errors,
        "nondeterminism": nondeterminism,
        "roadmap_state": {
            "ii_a": "CLOSED/PASS/PAPER-FROZEN",
            "r2_05": "CLOSED/PASS", "r2_06": "CLOSED/PASS", "r2_07": outcome,
            "r2_08": "LOCKED", "ii_c": "FUTURE",
        },
    }
    _write_json(run_dir / "summary.json", summary)
    _write_text(run_dir / "summary.md", _summary_markdown(summary))

    store.append_registry_entry({"identity": rec["identity"], "run_dir": str(run_dir)})
    new_state = "COMPLETED" if outcome == OUTCOME_PASS else (
        "INCONCLUSIVE" if outcome == OUTCOME_INCONCLUSIVE else "FAILED")
    for step_state in ("IMPLEMENTED", "READY", "RUNNING"):
        store.set_experiment_state("R2-07", step_state)
    store.set_experiment_state("R2-07", new_state)

    return {**summary, "run_dir": str(run_dir)}


def classify_outcome(synthesis_errors: List[str], nondeterminism: List[str],
                     synth: Optional[Dict[str, Any]]) -> "tuple[str, List[str]]":
    """Section 30's outcome rules, applied mechanically. PASS does NOT
    require short reuse distance anywhere -- long or near-null reuse
    behaviour is valid PASS science too, exactly as a uniform R2-05
    result or a zero-NMI R2-06 layer were."""
    if synthesis_errors:
        return OUTCOME_FAIL, list(synthesis_errors)
    if nondeterminism:
        return OUTCOME_FAIL, list(nondeterminism)
    if synth is None:
        return OUTCOME_FAIL, ["synthesis produced no result for an unspecified reason"]

    return OUTCOME_PASS, [
        "R2-01 PASS, R2-04/II-A PASS, R2-05 PASS, and R2-06 PASS verified; trace "
        "identity re-verified byte-identical on disk; layer-local expert identity "
        "preserved throughout; reuse accounting reconciled exactly against an "
        "independently-coded second computation (cold + reuse + duplicate-within-"
        "position == raw total occurrences); working-set windows computed "
        "deterministically with full-windows-only semantics; null-baseline "
        "invariants (token count, per-token cardinality, total occurrences, "
        "per-expert marginal counts) verified preserved; two independent "
        "syntheses over the identical trace, identical R2-05/R2-06 bridge "
        "evidence, and identical null-baseline seed/config produced an identical "
        "canonical digest; no forbidden claim (predictor accuracy, retention-"
        "policy performance, temporal drift, cross-workload generalization, "
        "R2-06-already-proved-long-range-locality) was generated."
    ]


def _reuse_events_to_csv(rows: List[Dict[str, Any]]) -> str:
    buf = io.StringIO()
    fields = ["layer_id", "expert_id", "t_prev", "t_now", "token_reuse_gap",
              "distinct_intervening_reuse_distance"]
    writer = csv.DictWriter(buf, fieldnames=fields)
    writer.writeheader()
    for row in rows:
        writer.writerow({k: row[k] for k in fields})
    return buf.getvalue()


def _layer_summary_to_csv(layer_gap_dist: Dict[int, Dict[str, Any]],
                          layer_distinct_dist: Dict[int, Dict[str, Any]],
                          layer_reuse: Dict[int, Dict[str, Any]]) -> str:
    buf = io.StringIO()
    fields = ["layer_id", "reuse_event_count", "cold_observation_count",
              "median_token_reuse_gap", "p75_token_reuse_gap", "p90_token_reuse_gap",
              "p95_token_reuse_gap", "max_token_reuse_gap", "immediate_reuse_fraction",
              "median_distinct_intervening_reuse_distance"]
    writer = csv.DictWriter(buf, fieldnames=fields)
    writer.writeheader()
    for layer in sorted(layer_gap_dist):
        gap = layer_gap_dist[layer]
        distinct = layer_distinct_dist.get(layer, {})
        reuse = layer_reuse.get(layer, {})
        writer.writerow({
            "layer_id": layer, "reuse_event_count": len(reuse.get("reuse_events", [])),
            "cold_observation_count": reuse.get("cold_observation_count"),
            "median_token_reuse_gap": gap.get("median"), "p75_token_reuse_gap": gap.get("p75"),
            "p90_token_reuse_gap": gap.get("p90"), "p95_token_reuse_gap": gap.get("p95"),
            "max_token_reuse_gap": gap.get("max"),
            "immediate_reuse_fraction": gap.get("within_threshold_fraction", {}).get(1),
            "median_distinct_intervening_reuse_distance": distinct.get("median"),
        })
    return buf.getvalue()


def _working_set_to_csv(layer_window_metrics: Dict[int, Dict[int, Dict[str, Any]]]) -> str:
    buf = io.StringIO()
    fields = ["layer_id", "window", "available", "n_full_windows", "mean_working_set_size",
              "median_working_set_size", "p90_working_set_size", "max_working_set_size",
              "working_set_fraction"]
    writer = csv.DictWriter(buf, fieldnames=fields)
    writer.writeheader()
    for layer in sorted(layer_window_metrics):
        for window in sorted(layer_window_metrics[layer]):
            m = layer_window_metrics[layer][window]
            writer.writerow({"layer_id": layer, **{k: m.get(k) for k in fields if k != "layer_id"}})
    return buf.getvalue()


def _summary_markdown(summary: Dict[str, Any]) -> str:
    lines = [
        f"# R2-07 run {summary['run_id']}", "",
        f"**Outcome:** {summary['outcome']}", "",
        f"**Scope:** {summary['scope']}", "",
        f"**Temporal unit:** {summary['temporal_unit']}", "",
        "## Reasons", "",
    ]
    lines += [f"- {r}" for r in summary["reasons"]] or ["- (none)"]
    lines += ["", "## Headline", ""]
    for k, v in summary.get("headline", {}).items():
        lines.append(f"- **{k}**: {v}")
    return "\n".join(lines)


def analyse(*, run_id: Optional[str] = None, **_ignored: Any) -> Dict[str, Any]:
    registry = [e for e in store.load_registry()
               if e.get("identity", {}).get("experiment_id") == "R2-07"]
    if not registry:
        return {"experiment_id": "R2-07", "outcome": "NOT_IMPLEMENTED",
                "message": "no R2-07 run exists yet to analyse"}

    if run_id:
        matches = [e for e in registry if e["identity"]["run_id"] == run_id]
        if not matches:
            raise KeyError(f"no R2-07 run with run_id={run_id!r}")
        entry = matches[0]
    else:
        completed = [e for e in registry if e["identity"]["outcome"] in
                    (OUTCOME_PASS, OUTCOME_INCONCLUSIVE, OUTCOME_FAIL)]
        if not completed:
            return {"experiment_id": "R2-07", "outcome": "NOT_IMPLEMENTED",
                    "message": "no completed R2-07 run exists yet to analyse"}
        latest_ends = sorted({e["identity"]["end_utc"] for e in completed}, reverse=True)
        newest = [e for e in completed if e["identity"]["end_utc"] == latest_ends[0]]
        if len(newest) > 1:
            raise ValueError(
                f"{len(newest)} R2-07 runs share the latest end_utc "
                f"({latest_ends[0]}): 'latest' is ambiguous. Pass an explicit run_id: "
                f"{[e['identity']['run_id'] for e in newest]}")
        entry = newest[0]

    run_dir = Path(entry["run_dir"])
    summary = json.loads((run_dir / "summary.json").read_text(encoding="utf-8"))
    return {**summary, "run_dir": str(run_dir), "analysed_from_existing_evidence": True}
