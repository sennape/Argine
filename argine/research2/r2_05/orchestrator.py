"""R2-05 orchestration: the full workflow behind `argine research2 run
R2-05` / `argine research2 analyse R2-05`.

Fully offline DESCRIPTIVE CHARACTERIZATION of the validated R2-01 trace's
expert-routing frequency: R2-01 PASS + R2-04 PASS/II-A-closed (prerequisite.py)
-> load the exact validated trace (never a new capture, never model/
runtime invocation) -> count layer-local (layer_id, expert_id) requests
at the token-expert-assignment granularity (request_unit.py) -> build the
primary frequency table (frequency.py) -> compute per-layer concentration
metrics, the uniform baseline, and concentration ratios (metrics.py) ->
build the global summary and layer-heterogeneity classification
(summary.py) -> paper-ready claims, explicitly separated into SUPPORTED
NOW vs NOT YET SUPPORTED, with a forbidden-claim guard (claims.py) ->
figure data (figures.py) -> scientific outcome classification -> evidence
written under the Research2 evidence root -> registry/state update ->
human-readable summary.

No model weights are loaded, no runtime binary is invoked, no new trace
capture or replay happens anywhere in this module. Determinism is
verified by running the synthesis step TWICE against the identically
loaded trace and comparing a canonical digest (volatile run ids/
timestamps excluded) -- a mismatch is an immediate FAIL, mirroring R2-04's
own double-synthesis determinism check.
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
from . import claims as claims_mod
from . import figures as figures_mod
from . import frequency
from . import metrics
from . import prerequisite
from . import request_unit
from . import summary as summary_mod

OUTCOME_PASS = "PASS"
OUTCOME_INCONCLUSIVE = "INCONCLUSIVE"
OUTCOME_FAIL = "FAIL"
OUTCOME_PREREQUISITE_NOT_MET = "R2_01_OR_R2_04_PREREQUISITE_NOT_MET"


def _write_json(path: Path, data: Dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, indent=2, sort_keys=True, default=str), encoding="utf-8")


def _write_text(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


def _canonical_digest(payload: Dict[str, Any]) -> str:
    blob = json.dumps(payload, sort_keys=True, default=str).encode("utf-8")
    return hashlib.sha256(blob).hexdigest()


class SynthesisError(ValueError):
    """Raised when the synthesis step itself discovers an internally
    impossible or malformed result (section 19 FAIL conditions)."""


def _representative_layers(layer_metrics: Dict[int, Dict[str, Any]]) -> List[int]:
    """Deterministic, data-driven (never arbitrary) choice of
    representative layers for figure candidate A (section 22): the most
    concentrated layer (min normalized entropy), the least concentrated
    layer (max normalized entropy), and the layer whose normalized
    entropy is closest to the cross-layer median (ties broken by the
    smallest layer id)."""
    with_data = {l: m for l, m in layer_metrics.items() if m["normalized_entropy"] is not None}
    if not with_data:
        return []
    by_entropy = sorted(with_data.items(), key=lambda kv: (kv[1]["normalized_entropy"], kv[0]))
    values = [v["normalized_entropy"] for _, v in by_entropy]
    median_value = values[len(values) // 2] if len(values) % 2 == 1 else (
        values[len(values) // 2 - 1] + values[len(values) // 2]) / 2.0
    median_layer = min(with_data, key=lambda l: (abs(with_data[l]["normalized_entropy"] -
                                                     median_value), l))
    most_concentrated = by_entropy[0][0]
    least_concentrated = by_entropy[-1][0]
    out = sorted(set([most_concentrated, median_layer, least_concentrated]))
    return out


def _synthesize(trace: Trace) -> Dict[str, Any]:
    """The deterministic synthesis step: a pure function of `trace`.
    Called twice by run() with the identically loaded trace as a
    determinism check."""
    n_experts = trace.meta.n_experts or None

    counts_per_layer = request_unit.count_requests_per_layer(trace)
    if not request_unit.expert_id_range_valid(counts_per_layer, trace.meta.n_experts):
        raise SynthesisError("counted expert id outside the valid [0, n_experts) range "
                             "for at least one layer")

    independent_totals = request_unit.independent_count_per_layer(trace)
    reconciliation_problems = frequency.reconcile_total_requests(
        counts_per_layer, independent_totals)
    if reconciliation_problems:
        raise SynthesisError("; ".join(reconciliation_problems))

    probability_problems = frequency.probability_mass_valid(counts_per_layer)
    if probability_problems:
        raise SynthesisError("; ".join(probability_problems))

    frequency_rows = frequency.all_frequency_rows(counts_per_layer, nominal_expert_count=n_experts)
    population_rows = [frequency.layer_population_summary(l, counts_per_layer[l],
                                                          nominal_expert_count=n_experts)
                      for l in sorted(counts_per_layer)]

    layer_metrics = {
        layer: metrics.layer_concentration_metrics(layer, counts_per_layer[layer],
                                                   nominal_expert_count=n_experts)
        for layer in sorted(counts_per_layer)
    }

    global_summary = summary_mod.global_summary(layer_metrics)
    heterogeneity = summary_mod.layer_heterogeneity(layer_metrics)

    representative_layers = _representative_layers(layer_metrics)
    fig_a = figures_mod.figure_a_rank_frequency_by_layer(frequency_rows, representative_layers)
    fig_b = figures_mod.figure_b_normalized_entropy_and_effective_fraction_by_layer(layer_metrics)
    fig_c = figures_mod.figure_c_top_k_share_by_layer(layer_metrics)

    claim_set = claims_mod.build_claims(global_summary, heterogeneity, claims_mod.LFU_BRIDGE_NOTE)
    claims_mod.check_no_forbidden_claims(claim_set)

    total_requests = request_unit.total_requests(counts_per_layer)
    headline = {
        "total_routing_requests": total_requests,
        "layer_count": len(counts_per_layer),
        "nominal_experts_per_layer": n_experts,
        "top_k_recorded_in_trace_meta": trace.meta.top_k or None,
        "median_normalized_entropy": global_summary["layer_median"]["normalized_entropy"],
        "median_effective_expert_fraction": global_summary["request_weighted_aggregate"][
            "effective_expert_fraction"],
        "median_top_8_share": global_summary["layer_median"]["top_8_share"],
        "min_top_8_share": (min((m["top_k_share"].get(8) for m in layer_metrics.values()
                                 if 8 in m["top_k_share"]), default=None)),
        "max_top_8_share": (max((m["top_k_share"].get(8) for m in layer_metrics.values()
                                 if 8 in m["top_k_share"]), default=None)),
        "most_concentrated_layer": global_summary["most_concentrated_layer"],
        "least_concentrated_layer": global_summary["least_concentrated_layer"],
        "experts_for_80pct_median": global_summary["experts_for_pct"].get(0.80, {}).get("median"),
        "experts_for_80pct_min": global_summary["experts_for_pct"].get(0.80, {}).get("min"),
        "experts_for_80pct_max": global_summary["experts_for_pct"].get(0.80, {}).get("max"),
    }

    return {
        "n_experts": n_experts,
        "counts_per_layer": counts_per_layer,
        "frequency_rows": frequency_rows,
        "population_rows": population_rows,
        "layer_metrics": layer_metrics,
        "global_summary": global_summary,
        "heterogeneity": heterogeneity,
        "representative_layers": representative_layers,
        "figure_a_rank_frequency_by_layer": fig_a,
        "figure_b_normalized_entropy_and_effective_fraction_by_layer": fig_b,
        "figure_c_top_k_share_by_layer": fig_c,
        "claims": claim_set,
        "headline": headline,
    }


def run(*, allow_dirty: bool = False, **_ignored: Any) -> Dict[str, Any]:
    rec = provenance.build_record("R2-05", outcome="PENDING", allow_dirty=allow_dirty)
    run_id = rec["identity"]["run_id"]
    rec["identity"]["start_utc"] = provenance.utc_now_iso()

    binding = prerequisite.require_r2_01_pass_and_r2_04_pass()
    if not binding.ok:
        return {
            "experiment_id": "R2-05", "run_id": run_id,
            "outcome": OUTCOME_PREREQUISITE_NOT_MET,
            "message": binding.message,
            "prerequisite_status": binding.status,
        }

    try:
        trace = Trace.load(binding.trace_path)
    except (TraceError, OSError, UnicodeDecodeError, json.JSONDecodeError) as e:
        return {
            "experiment_id": "R2-05", "run_id": run_id, "outcome": OUTCOME_FAIL,
            "message": f"could not load the bound R2-01 trace: {e}",
            "trace_path": binding.trace_path,
        }

    if not trace.meta.n_experts:
        inconclusive_reasons = [
            "the bound trace carries no authoritative nominal expert count "
            "(trace.meta.n_experts); normalized metrics (normalized entropy, "
            "effective-expert fraction, the uniform baseline) would not be "
            "comparable across layers without one -- R2-05 refuses to silently "
            "fall back to a per-layer observed count for the headline result."
        ]
        rec["identity"]["end_utc"] = provenance.utc_now_iso()
        rec["identity"]["outcome"] = OUTCOME_INCONCLUSIVE
        run_dir = store.run_dir("R2-05", run_id)
        _write_json(run_dir / "manifest.json", rec)
        summary = {
            "experiment_id": "R2-05", "run_id": run_id, "outcome": OUTCOME_INCONCLUSIVE,
            "reasons": inconclusive_reasons, "binding": binding.to_json(),
        }
        _write_json(run_dir / "summary.json", summary)
        store.append_registry_entry({"identity": rec["identity"], "run_dir": str(run_dir)})
        store.set_experiment_state("R2-05", "INCONCLUSIVE")
        return {**summary, "run_dir": str(run_dir)}

    synthesis_errors: List[str] = []
    try:
        synth_a = _synthesize(trace)
        synth_b = _synthesize(trace)
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
                f"two independent syntheses over the identically loaded trace produced "
                f"different canonical digests ({digest_a} != {digest_b})")

    outcome, reasons = classify_outcome(synthesis_errors, nondeterminism, synth_a)

    rec["identity"]["end_utc"] = provenance.utc_now_iso()
    rec["identity"]["outcome"] = outcome
    rec["experiment"]["parameters"] = {
        "r2_01_run_id": binding.r2_01_run_id, "r2_04_run_id": binding.r2_04_run_id,
        "candidate_status": binding.candidate_status,
        "request_unit": request_unit.REQUEST_UNIT_DEFINITION,
    }
    rec["artifacts"]["model"] = None

    run_dir = store.run_dir("R2-05", run_id)
    _write_json(run_dir / "manifest.json", rec)
    _write_json(run_dir / "provenance.json", {
        "binding": binding.to_json(),
        "canonical_digest": digest_a,
        "synthesis_errors": synthesis_errors,
        "nondeterminism": nondeterminism,
        "request_unit_definition": request_unit.REQUEST_UNIT_DEFINITION,
    })

    synth = synth_a or {}
    if synth.get("frequency_rows"):
        _write_text(run_dir / "expert_frequency.csv", _frequency_to_csv(synth["frequency_rows"]))
    if synth.get("layer_metrics"):
        _write_text(run_dir / "layer_concentration.csv",
                   _concentration_to_csv(synth["layer_metrics"]))
    _write_json(run_dir / "concentration.json", {
        "layer_metrics": {str(k): v for k, v in synth.get("layer_metrics", {}).items()},
        "population_rows": synth.get("population_rows", []),
        "global_summary": synth.get("global_summary", {}),
        "heterogeneity": synth.get("heterogeneity", {}),
    })
    if synth.get("claims") is not None:
        _write_json(run_dir / "claims.json", synth["claims"])
    _write_json(run_dir / "figure_data.json", {
        "figure_a_rank_frequency_by_layer": {str(k): v for k, v in
                                            synth.get("figure_a_rank_frequency_by_layer", {}).items()},
        "figure_b_normalized_entropy_and_effective_fraction_by_layer": synth.get(
            "figure_b_normalized_entropy_and_effective_fraction_by_layer", []),
        "figure_c_top_k_share_by_layer": synth.get("figure_c_top_k_share_by_layer", []),
        "representative_layers": synth.get("representative_layers", []),
    })

    summary = {
        "experiment_id": "R2-05", "run_id": run_id, "outcome": outcome,
        "scope": "offline DESCRIPTIVE CHARACTERIZATION of layer-local expert routing "
                "frequency on the validated R2-01 trace -- no new replay, no model/"
                "runtime invocation, no new trace capture, no causal or predictability "
                "claim",
        "request_unit": request_unit.REQUEST_UNIT_DEFINITION,
        "reasons": reasons,
        "candidate_status": rec["git"]["candidate_status"],
        "binding": binding.to_json(),
        "canonical_digest": digest_a,
        "headline": synth.get("headline", {}),
        "synthesis_errors": synthesis_errors,
        "nondeterminism": nondeterminism,
        "roadmap_state": {
            "ii_a": "CLOSED/PASS/PAPER-FROZEN",
            "r2_05": outcome,
            "r2_06": "LOCKED", "r2_07": "LOCKED", "r2_08": "LOCKED",
            "ii_c": "FUTURE",
        },
    }
    _write_json(run_dir / "summary.json", summary)
    _write_text(run_dir / "summary.md", _summary_markdown(summary))

    store.append_registry_entry({"identity": rec["identity"], "run_dir": str(run_dir)})
    new_state = "COMPLETED" if outcome == OUTCOME_PASS else (
        "INCONCLUSIVE" if outcome == OUTCOME_INCONCLUSIVE else "FAILED")
    for step_state in ("IMPLEMENTED", "READY", "RUNNING"):
        store.set_experiment_state("R2-05", step_state)
    store.set_experiment_state("R2-05", new_state)

    return {**summary, "run_dir": str(run_dir)}


def classify_outcome(synthesis_errors: List[str], nondeterminism: List[str],
                     synth: Optional[Dict[str, Any]]) -> "tuple[str, List[str]]":
    """Section 19's outcome rules, applied mechanically. PASS does NOT
    require strong concentration -- a uniform or near-uniform result is
    valid PASS science, and so is a highly concentrated one."""
    if synthesis_errors:
        return OUTCOME_FAIL, list(synthesis_errors)
    if nondeterminism:
        return OUTCOME_FAIL, list(nondeterminism)
    if synth is None:
        return OUTCOME_FAIL, ["synthesis produced no result for an unspecified reason"]

    return OUTCOME_PASS, [
        "R2-01 PASS and R2-04 PASS/II-A-closed verified; trace identity re-verified "
        "byte-identical on disk; expert identity treated layer-locally throughout; "
        "frequency counts reconciled exactly against an independently-coded second "
        "computation; probability mass per layer sums to 1.0 within tolerance; rank "
        "and tie-breaking deterministic; two independent syntheses over the identical "
        "trace produced an identical canonical digest; no forbidden claim (temporal "
        "stability, Zipfian/power-law without evidence, frequency-explains-Oracle-gap) "
        "was generated."
    ]


def _frequency_to_csv(frequency_rows: List[Dict[str, Any]]) -> str:
    buf = io.StringIO()
    fields = ["layer_id", "expert_id", "request_count", "request_fraction_within_layer",
              "rank_within_layer", "cumulative_request_fraction_within_layer"]
    writer = csv.DictWriter(buf, fieldnames=fields)
    writer.writeheader()
    for row in frequency_rows:
        writer.writerow({k: row[k] for k in fields})
    return buf.getvalue()


def _concentration_to_csv(layer_metrics: Dict[int, Dict[str, Any]]) -> str:
    buf = io.StringIO()
    fields = ["layer_id", "total_requests", "observed_expert_count", "nominal_expert_count",
              "n_used", "n_basis", "shannon_entropy", "normalized_entropy",
              "effective_expert_count", "effective_expert_fraction", "hhi", "gini",
              "max_expert_share"]
    writer = csv.DictWriter(buf, fieldnames=fields)
    writer.writeheader()
    for layer in sorted(layer_metrics):
        m = layer_metrics[layer]
        writer.writerow({k: m.get(k) for k in fields})
    return buf.getvalue()


def _summary_markdown(summary: Dict[str, Any]) -> str:
    lines = [
        f"# R2-05 run {summary['run_id']}", "",
        f"**Outcome:** {summary['outcome']}", "",
        f"**Scope:** {summary['scope']}", "",
        f"**Request unit:** {summary['request_unit']}", "",
        "## Reasons", "",
    ]
    lines += [f"- {r}" for r in summary["reasons"]] or ["- (none)"]
    lines += ["", "## Headline", ""]
    for k, v in summary.get("headline", {}).items():
        lines.append(f"- **{k}**: {v}")
    return "\n".join(lines)


def analyse(*, run_id: Optional[str] = None, **_ignored: Any) -> Dict[str, Any]:
    registry = [e for e in store.load_registry()
               if e.get("identity", {}).get("experiment_id") == "R2-05"]
    if not registry:
        return {"experiment_id": "R2-05", "outcome": "NOT_IMPLEMENTED",
                "message": "no R2-05 run exists yet to analyse"}

    if run_id:
        matches = [e for e in registry if e["identity"]["run_id"] == run_id]
        if not matches:
            raise KeyError(f"no R2-05 run with run_id={run_id!r}")
        entry = matches[0]
    else:
        completed = [e for e in registry if e["identity"]["outcome"] in
                    (OUTCOME_PASS, OUTCOME_INCONCLUSIVE, OUTCOME_FAIL)]
        if not completed:
            return {"experiment_id": "R2-05", "outcome": "NOT_IMPLEMENTED",
                    "message": "no completed R2-05 run exists yet to analyse"}
        latest_ends = sorted({e["identity"]["end_utc"] for e in completed}, reverse=True)
        newest = [e for e in completed if e["identity"]["end_utc"] == latest_ends[0]]
        if len(newest) > 1:
            raise ValueError(
                f"{len(newest)} R2-05 runs share the latest end_utc "
                f"({latest_ends[0]}): 'latest' is ambiguous. Pass an explicit run_id: "
                f"{[e['identity']['run_id'] for e in newest]}")
        entry = newest[0]

    run_dir = Path(entry["run_dir"])
    summary = json.loads((run_dir / "summary.json").read_text(encoding="utf-8"))
    return {**summary, "run_dir": str(run_dir), "analysed_from_existing_evidence": True}
