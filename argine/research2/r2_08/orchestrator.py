"""R2-08 orchestration: routing drift and workload dependence (II-B).

Fully offline analysis once all 8 canonical capture cells are valid. Capture
execution is injectable for tests; this implementation MUST NOT execute real
30B captures. Primary analysis is decode-only on a matched horizon H.
"""

from __future__ import annotations

import csv
import hashlib
import io
import json
import statistics
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Tuple

from .. import provenance, runtime_resolver, store
from ...research.trace import PHASE_DECODE, Step, Trace, TraceMeta
from . import bridges, capture as capture_mod, capture_matrix, claims as claims_mod
from . import drift, figures as figures_mod, matched_horizon, prerequisite, summary as summary_mod
from . import variability, windowing, workloads

OUTCOME_PASS = "PASS"
OUTCOME_INCONCLUSIVE = "INCONCLUSIVE"
OUTCOME_FAIL = "FAIL"
OUTCOME_PREREQUISITE_NOT_MET = "UPSTREAM_PREREQUISITE_NOT_MET"
OUTCOME_BLOCKED = "BLOCKED"

_PLACEHOLDER_RUNTIME_SHA = "runtime-unresolved:canonical-r2-protocol"

JSD_DEFINITION = "Jensen-Shannon divergence, log base 2, full nominal 128-expert support including zeros, value in [0,1]."
JACCARD_DEFINITION = "Jaccard similarity = |A∩B|/|A∪B| on top-k expert sets (k in {8,16}); Jaccard distance = 1-similarity; tie-breaking by expert ID ascending (deterministic)."


def _write_json(path: Path, data: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, indent=2, sort_keys=True, default=str), encoding="utf-8")


def _write_text(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


def _canonical_digest(payload: Dict[str, Any]) -> str:
    blob = json.dumps(payload, sort_keys=True, default=str).encode("utf-8")
    return hashlib.sha256(blob).hexdigest()


def _median(values: List[float]) -> Optional[float]:
    if not values:
        return None
    s = sorted(values)
    n = len(s)
    m = n // 2
    return float(s[m]) if n % 2 == 1 else (s[m - 1] + s[m]) / 2.0


def _decode_selections(trace: Trace, layer: int, h: int) -> List[Tuple[int, ...]]:
    sels = [sel for _, sel in trace.token_selections(layer, phase=PHASE_DECODE)]
    return sels[:h]


def _looks_like_sha256(value: Any) -> bool:
    if not isinstance(value, str) or len(value) != 64:
        return False
    try:
        int(value, 16)
        return True
    except ValueError:
        return False


def _runtime_sha_from_registration(*, resolved_path: Optional[str], root: Optional[Path]) -> Optional[str]:
    reg = store.get_runtime_registration(root)
    if not reg or not reg.get("sha256"):
        return None
    if not _looks_like_sha256(reg.get("sha256")):
        return None
    if resolved_path and reg.get("path"):
        try:
            if Path(reg["path"]).resolve() != Path(resolved_path).resolve():
                return None
        except OSError:
            return None
    return str(reg["sha256"])


def _resolve_runtime_sha(binding: prerequisite.R208Binding, *, root: Optional[Path],
                         runtime_path: Optional[str]) -> Tuple[Optional[str], Optional[str]]:
    """Return (verified_runtime_sha, error_message). Never invents a SHA.

    For the canonical reused R2-01 cell, provenance must establish the actual
    runtime SHA or this fails closed.
    """
    # 1) Reused trace fingerprint, only if it contains an actual SHA.
    try:
        validation = capture_mod.validate_trace_file(Path(binding.reused_trace_path))
        fp_data = ((validation.get("trace_meta") or {}).get("fingerprint") or {})
        for key in ("runtime_sha256",):
            v = fp_data.get(key)
            if _looks_like_sha256(v):
                return str(v), None
        rt = fp_data.get("runtime")
        if isinstance(rt, dict) and _looks_like_sha256(rt.get("sha256")):
            return str(rt["sha256"]), None
    except Exception:
        pass

    # 2) Canonical R2-01 reused capture provenance (scientifically bound).
    canonical_reuse = (binding.reused_trace_sha256 == prerequisite.CANONICAL_REUSED_TRACE_SHA256
                       and binding.reused_capture_source == prerequisite.CANONICAL_REUSED_CAPTURE_SOURCE)
    if canonical_reuse:
        sha, err = capture_mod.runtime_sha256_from_reused_r2_01_capture(root=root)
        if sha:
            return sha, None
        return None, (f"canonical reused R2-01 cell runtime identity cannot be established from "
                      f"provenance: {err}")

    # 3) Current runtime registration (useful for synthetic/offline-safe flows that
    # already have verified runtime state), only when it matches a resolvable runtime
    # context or is the only verified local registration.
    try:
        rr = runtime_resolver.resolve(explicit_runtime=runtime_path)
        if rr.status == runtime_resolver.RUNTIME_RESOLVED:
            sha = _runtime_sha_from_registration(resolved_path=rr.resolved_path, root=root)
            if sha:
                return sha, None
            if rr.tier == "documented_canonical":
                return runtime_resolver.DOCUMENTED_CANONICAL_RUNTIME["expected_sha256"], None
    except Exception:
        pass
    sha = _runtime_sha_from_registration(resolved_path=None, root=root)
    if sha:
        return sha, None
    return None, None


def _resolve_canonical_identity(binding: prerequisite.R208Binding,
                                 model_path: Optional[str],
                                 root: Optional[Path],
                                 runtime_path: Optional[str] = None) -> Tuple[str, str, Optional[str]]:
    """Resolve consistent model/runtime SHA across all 8 cells.

    Returns (model_sha, runtime_sha, runtime_identity_error). runtime_sha is a
    verified SHA when establishable; for the canonical reused R2-01 cell a
    placeholder is never returned without an error.
    """
    model_sha = None
    runtime_sha = None
    runtime_err = None
    # from reused trace fingerprint
    try:
        validation = capture_mod.validate_trace_file(Path(binding.reused_trace_path))
        fp_data = ((validation.get("trace_meta") or {}).get("fingerprint") or {})
        model_sha = fp_data.get("model_sha256") or (fp_data.get("model") or {}).get("sha256")
    except Exception:
        pass
    # from model file if available
    if not model_sha and model_path and Path(model_path).is_file():
        try:
            h = hashlib.sha256()
            with open(model_path, "rb") as fh:
                for chunk in iter(lambda: fh.read(1 << 20), b""):
                    h.update(chunk)
            model_sha = h.hexdigest()
        except OSError:
            pass
    runtime_sha, runtime_err = _resolve_runtime_sha(binding, root=root, runtime_path=runtime_path)
    # consistent placeholders when unresolved (test/offline-safe, but still consistent)
    if not model_sha or model_sha == "unknown-from-trace" or not _looks_like_sha256(str(model_sha)):
        model_sha = f"model-unresolved:{binding.reused_trace_sha256[:16]}"
    if not runtime_sha or not _looks_like_sha256(str(runtime_sha)):
        canonical_reuse = (binding.reused_trace_sha256 == prerequisite.CANONICAL_REUSED_TRACE_SHA256
                           and binding.reused_capture_source == prerequisite.CANONICAL_REUSED_CAPTURE_SOURCE)
        if canonical_reuse and not runtime_err:
            runtime_err = "canonical reused R2-01 runtime SHA could not be verified"
        runtime_sha = _PLACEHOLDER_RUNTIME_SHA
    return str(model_sha), str(runtime_sha), runtime_err


def _ensure_reused_coding_cell(binding: prerequisite.R208Binding,
                               root: Optional[Path],
                               model_sha: str, runtime_sha: str) -> capture_matrix.CaptureCell:
    """Register/reuse coding-v1/seed-42 from canonical R2-01 trace."""
    validation = capture_mod.validate_trace_file(Path(binding.reused_trace_path))
    if not validation.get("ok"):
        raise ValueError(f"reused R2-01 trace invalid: {validation}")
    conf = capture_mod.configuration_for_cell("coding-v1", 42, model_sha256=model_sha,
                                              runtime_sha256=runtime_sha)
    cell = capture_matrix.CaptureCell(
        workload_id="coding-v1", workload_version=workloads.CODING_V1.version,
        seed=42, prompt_sha256=conf["prompt_sha256"], model_sha256=model_sha,
        runtime_sha256=runtime_sha, configuration_fingerprint=conf["fingerprint_hash"],
        configuration=conf["fingerprint"], capture_run_id=binding.r2_01_run_id,
        capture_source=binding.reused_capture_source, trace_path=binding.reused_trace_path,
        trace_sha256=binding.reused_trace_sha256, validation_state="VALID",
        canonical_status=True, reused=True, note="reused from R2-01 canonical trace",
    )
    capture_matrix.upsert_cell(cell, root)
    return cell


def _identity_for_captures(model_path: Optional[str], cells: Dict[Tuple[str, int], capture_matrix.CaptureCell]
                           ) -> Tuple[str, str]:
    """Best-effort model/runtime SHA for new captures. If unavailable, use stable placeholders;
    fingerprint compatibility is still checked across captured cells."""
    model_sha = "unresolved-model-sha"
    runtime_sha = "unresolved-runtime-sha"
    # Prefer identity from an existing valid cell
    for c in cells.values():
        if c.validation_state == "VALID" and c.model_sha256 and c.model_sha256 != "unknown-from-trace":
            model_sha = c.model_sha256
        if c.validation_state == "VALID" and c.runtime_sha256 and c.runtime_sha256 != "unknown-from-trace":
            runtime_sha = c.runtime_sha256
    if model_path and Path(model_path).is_file():
        try:
            import hashlib as _hl
            h = _hl.sha256()
            with open(model_path, "rb") as fh:
                for chunk in iter(lambda: fh.read(1 << 20), b""):
                    h.update(chunk)
            model_sha = h.hexdigest()
        except OSError:
            pass
    return model_sha, runtime_sha


def _cell_diagnostics(cells: Dict[Tuple[str, int], capture_matrix.CaptureCell]) -> Dict[str, Any]:
    return {
        "expected_cells": [{"workload_id": w, "seed": s} for w, s in capture_matrix.expected_canonical_cells()],
        "cells": {f"{w}/seed-{s}": c.to_json() for (w, s), c in sorted(cells.items())},
        "missing_or_invalid": [{"workload_id": w, "seed": s}
                               for w, s in capture_matrix.missing_or_invalid_cells(cells)],
    }


def _load_trace_for_cell(cell: capture_matrix.CaptureCell) -> Trace:
    from ..r2_01 import trace_contract
    trace, _ = trace_contract.normalize(cell.trace_path)
    return trace


def _compute_capture_analyses(cell: capture_matrix.CaptureCell, h: int) -> Dict[str, Any]:
    trace = _load_trace_for_cell(cell)
    nominal = trace.meta.n_experts or drift.NOMINAL_EXPERTS
    layers = sorted(trace.layers)
    # temporal drift per layer
    layer_temporal = {}
    layer_distributions_full = {}
    layer_counts_full = {}
    per_layer_median_adj_jsd = []
    per_layer_median_top8_jacc = []
    for layer in layers:
        sels = _decode_selections(trace, layer, h)
        windows, wdef = windowing.split_into_windows(sels, h)
        td = drift.temporal_drift_for_layer(windows, nominal=nominal)
        # don't persist full distributions per layer in every capture row to keep artifacts reasonable
        layer_temporal[str(layer)] = {
            "adjacent_window_jsd": td["adjacent_window_jsd"],
            "median_adjacent_window_jsd": td["median_adjacent_window_jsd"],
            "max_adjacent_window_jsd": td["max_adjacent_window_jsd"],
            "first_vs_last_jsd": td["first_vs_last_jsd"],
            "normalized_entropy_per_window": td["normalized_entropy_per_window"],
            "entropy_range": td["entropy_range"],
            "top8_adjacent_jaccard_distance": td["top8_adjacent_jaccard_distance"],
            "top16_adjacent_jaccard_distance": td["top16_adjacent_jaccard_distance"],
            "median_top8_adjacent_jaccard_distance": td["median_top8_adjacent_jaccard_distance"],
            "median_top16_adjacent_jaccard_distance": td["median_top16_adjacent_jaccard_distance"],
        }
        if td["median_adjacent_window_jsd"] is not None:
            per_layer_median_adj_jsd.append(td["median_adjacent_window_jsd"])
        if td["median_top8_adjacent_jaccard_distance"] is not None:
            per_layer_median_top8_jacc.append(td["median_top8_adjacent_jaccard_distance"])
        # full-horizon distribution/counts for variability comparisons
        p_full, c_full = drift.distribution_from_selections(sels, nominal=nominal)
        layer_distributions_full[layer] = p_full
        layer_counts_full[layer] = c_full
    # bridges on matched decode trace view
    matched_trace = matched_horizon.build_matched_decode_trace(trace, h)
    bridges_out = bridges.compute_workload_bridge_metrics(matched_trace)
    return {
        "cell_id": cell.cell_id, "workload_id": cell.workload_id, "seed": cell.seed,
        "trace_sha256": cell.trace_sha256,
        "temporal_drift_per_layer": layer_temporal,
        "median_temporal_jsd": _median(per_layer_median_adj_jsd),
        "median_top8_temporal_jaccard_distance": _median(per_layer_median_top8_jacc),
        "bridges": bridges_out,
        "window_definition": windowing.window_definition(h),
        "_layer_distributions_full": layer_distributions_full,
        "_layer_counts_full": layer_counts_full,
    }


def _synthesize(cells: Dict[Tuple[str, int], capture_matrix.CaptureCell],
                horizon: Dict[str, Any]) -> Dict[str, Any]:
    h = int(horizon["h"])
    analyses = {}
    temporal_by_capture = {}
    layer_distributions = {}
    layer_counts = {}
    for key in sorted(cells):
        cell = cells[key]
        a = _compute_capture_analyses(cell, h)
        analyses[f"{cell.workload_id}/seed-{cell.seed}"] = a
        temporal_by_capture[a["cell_id"]] = a
        layer_distributions[(cell.workload_id, cell.seed)] = a["_layer_distributions_full"]
        layer_counts[(cell.workload_id, cell.seed)] = a["_layer_counts_full"]

    # replicate variability per workload
    replicate_per_workload = {}
    all_rep_jsds = []
    for wid in workloads.CANONICAL_WORKLOAD_IDS:
        ld = {42: layer_distributions.get((wid, 42), {}), 43: layer_distributions.get((wid, 43), {})}
        lc = {42: layer_counts.get((wid, 42), {}), 43: layer_counts.get((wid, 43), {})}
        # reshape to layer -> seed
        by_layer_dist = {}
        by_layer_count = {}
        for seed in (42, 43):
            for layer, v in ld.get(seed, {}).items():
                by_layer_dist.setdefault(layer, {})[seed] = v
            for layer, v in lc.get(seed, {}).items():
                by_layer_count.setdefault(layer, {})[seed] = v
        rv = variability.replicate_variability_for_workload(by_layer_dist, by_layer_count)
        replicate_per_workload[wid] = rv
        if rv.get("median_replicate_jsd") is not None:
            all_rep_jsds.append(rv["median_replicate_jsd"])
    replicate = {"per_workload": replicate_per_workload, "median_replicate_jsd": _median(all_rep_jsds)}

    cross = variability.cross_workload_variability(layer_distributions, layer_counts)

    all_temporal = [a["median_temporal_jsd"] for a in analyses.values() if a.get("median_temporal_jsd") is not None]
    median_temporal = _median(all_temporal)
    regime = summary_mod.classify_regime(median_temporal, replicate.get("median_replicate_jsd"),
                                         cross.get("median_cross_workload_jsd"))

    # layer heterogeneity (aggregate across captures)
    layer_temporal_medians = {}
    for layer in set(l for a in analyses.values() for l in (int(k) for k in a["temporal_drift_per_layer"])):
        vals = []
        for a in analyses.values():
            lt = a["temporal_drift_per_layer"].get(str(layer))
            if lt and lt.get("median_adjacent_window_jsd") is not None:
                vals.append(lt["median_adjacent_window_jsd"])
        if vals:
            layer_temporal_medians[layer] = _median(vals)
    # cross-workload per layer medians across pairs
    layer_cross = {}
    for pair in cross.get("pairs", []):
        for layer_s, pl in pair.get("per_layer", {}).items():
            layer_cross.setdefault(int(layer_s), []).append(pl["frequency_jsd"])
    layer_cross_medians = {l: _median(vs) for l, vs in layer_cross.items() if vs}
    heterogeneity = summary_mod.layer_heterogeneity(layer_temporal_medians, layer_cross_medians)

    # workload-level structural summaries (aggregate replicates)
    workload_metrics = {}
    for wid in workloads.CANONICAL_WORKLOAD_IDS:
        reps = [analyses[f"{wid}/seed-{s}"] for s in workloads.SEEDS if f"{wid}/seed-{s}" in analyses]
        def _bmed(path):
            vals = []
            for r in reps:
                cur = r
                for p in path:
                    cur = (cur or {}).get(p)
                if cur is not None and isinstance(cur, (int, float)):
                    vals.append(float(cur))
            return _median(vals)
        workload_metrics[wid] = {
            "replicates": [r["cell_id"] for r in reps],
            "bridges": {
                "r2_05": {
                    "normalized_entropy_median": _bmed(["bridges", "r2_05", "normalized_entropy_median"]),
                    "effective_expert_fraction_median": _bmed(["bridges", "r2_05", "effective_expert_fraction_median"]),
                    "top8_share_median": _bmed(["bridges", "r2_05", "top8_share_median"]),
                },
                "r2_06": {
                    "nmi_median": _bmed(["bridges", "r2_06", "nmi_median"]),
                    "self_transition_lift_median": _bmed(["bridges", "r2_06", "self_transition_lift_median"]),
                },
                "r2_07": {
                    "immediate_reuse_median": _bmed(["bridges", "r2_07", "immediate_reuse_median"]),
                    "reuse_le4_median": _bmed(["bridges", "r2_07", "reuse_le4_median"]),
                    "reuse_le16_median": _bmed(["bridges", "r2_07", "reuse_le16_median"]),
                    "median_token_reuse_gap_median": _bmed(["bridges", "r2_07", "median_token_reuse_gap_median"]),
                    "w32_working_set_median": _bmed(["bridges", "r2_07", "w32_working_set_median"]),
                    "w128_working_set_median": _bmed(["bridges", "r2_07", "w128_working_set_median"]),
                },
            },
        }

    # stability summaries
    workload_temporal = {}
    for wid in workloads.CANONICAL_WORKLOAD_IDS:
        vals = [analyses[f"{wid}/seed-{s}"]["median_temporal_jsd"] for s in workloads.SEEDS
                if f"{wid}/seed-{s}" in analyses and analyses[f"{wid}/seed-{s}"].get("median_temporal_jsd") is not None]
        workload_temporal[wid] = _median(vals)
    stable_workload = min((w for w, v in workload_temporal.items() if v is not None), key=lambda w: (workload_temporal[w], w), default=None)
    least_stable_workload = max((w for w, v in workload_temporal.items() if v is not None), key=lambda w: (workload_temporal[w], w), default=None)

    claim_set = claims_mod.build_claims(regime, {"replicate": replicate, "cross_workload": cross}, workload_metrics)
    claims_mod.check_no_forbidden_claims(claim_set)
    figure_data = figures_mod.build_figure_data(analyses, replicate, cross, workload_metrics)

    # strip internal fields before persisting analyses in some forms? keep in internal; persist sanitized
    analyses_public = {}
    for k, a in analyses.items():
        analyses_public[k] = {kk: vv for kk, vv in a.items() if not kk.startswith("_")}

    result = {
        "horizon": horizon,
        "window_definition": windowing.window_definition(h),
        "jsd_definition": JSD_DEFINITION,
        "jaccard_definition": JACCARD_DEFINITION,
        "temporal_by_capture": analyses_public,
        "replicate_variability": replicate,
        "cross_workload_variability": cross,
        "comparison": regime,
        "heterogeneity": heterogeneity,
        "workload_metrics": workload_metrics,
        "claims": claim_set,
        "figure_data": figure_data,
        "headline": {
            "workload_count": len(workloads.CANONICAL_WORKLOAD_IDS),
            "canonical_capture_count": len(cells),
            "matched_decode_horizon": h,
            "temporal_window_length": windowing.window_definition(h)["window_length"],
            "median_temporal_jsd": median_temporal,
            "median_same_workload_replicate_jsd": replicate.get("median_replicate_jsd"),
            "median_cross_workload_jsd": cross.get("median_cross_workload_jsd"),
            "cross_workload_to_replicate_ratio": regime.get("cross_workload_to_replicate_ratio"),
            "cross_workload_to_temporal_ratio": regime.get("cross_workload_to_temporal_ratio"),
            "temporal_to_replicate_ratio": regime.get("temporal_to_replicate_ratio"),
            "median_top8_temporal_jaccard_distance": _median([a.get("median_top8_temporal_jaccard_distance")
                                                              for a in analyses.values()
                                                              if a.get("median_top8_temporal_jaccard_distance") is not None]),
            "strongest_workload_pair": cross.get("strongest_workload_pair"),
            "most_stable_workload": stable_workload,
            "least_stable_workload": least_stable_workload,
            "most_stable_layer": heterogeneity.get("most_temporally_stable_layer"),
            "least_stable_layer": heterogeneity.get("least_temporally_stable_layer"),
            "regime_classification": regime.get("regime"),
            "all_configuration_fingerprints_compatible": None,  # filled by caller
        },
        "workload_temporal_medians": workload_temporal,
    }
    return result


def _build_canonical_digest(binding: prerequisite.R208Binding, cells: Dict[Tuple[str, int], capture_matrix.CaptureCell],
                            synth: Dict[str, Any], fp_compat: Dict[str, Any]) -> str:
    # Bind upstream run IDs/digests
    up = {eid: bridges.load_upstream_digest(eid) for eid in ("R2-05", "R2-06", "R2-07")}
    payload = {
        "experiment_id": "R2-08",
        "upstream": {
            "R2-05": {"run_id": (up["R2-05"] or {}).get("run_id"), "canonical_digest": (up["R2-05"] or {}).get("canonical_digest")},
            "R2-06": {"run_id": (up["R2-06"] or {}).get("run_id"), "canonical_digest": (up["R2-06"] or {}).get("canonical_digest")},
            "R2-07": {"run_id": (up["R2-07"] or {}).get("run_id"), "canonical_digest": (up["R2-07"] or {}).get("canonical_digest")},
        },
        "trace_shas": sorted([c.trace_sha256 for c in cells.values()]),
        "workload_prompt_shas": {w.workload_id: w.sha256 for w in workloads.CANONICAL_WORKLOADS},
        "configuration_fingerprints_compatible": fp_compat.get("compatible"),
        "matched_horizon": synth["horizon"]["h"],
        "temporal_window_definition": synth["window_definition"],
        "jsd_definition": synth["jsd_definition"],
        "jaccard_definition": synth["jaccard_definition"],
        "regime_thresholds": summary_mod.thresholds_json(),
        "canonical_results": synth["headline"],
        "comparison": synth["comparison"],
    }
    return _canonical_digest(payload)


def run(*, allow_dirty: bool = False, model_path: Optional[str] = None,
        capture_fn: Optional[Callable[..., Dict[str, Any]]] = None,
        evidence_root: Optional[Path] = None, runtime_path: Optional[str] = None,
        **_ignored: Any) -> Dict[str, Any]:
    """R2-08 automated workflow. capture_fn is injectable for tests (must not
    start real 30B). When all 8 cells are valid, performs zero captures."""
    root = evidence_root or store.evidence_root()
    rec = provenance.build_record("R2-08", outcome="PENDING", allow_dirty=allow_dirty)
    run_id = rec["identity"]["run_id"]
    rec["identity"]["start_utc"] = provenance.utc_now_iso()

    binding = prerequisite.require_full_chain_pass(root)
    if not binding.ok:
        return {"experiment_id": "R2-08", "run_id": run_id, "outcome": OUTCOME_PREREQUISITE_NOT_MET,
                "message": binding.message, "binding": binding.to_json()}

    model_sha, runtime_sha, runtime_identity_err = _resolve_canonical_identity(
        binding, model_path, root, runtime_path=runtime_path)
    canonical_reuse = (binding.reused_trace_sha256 == prerequisite.CANONICAL_REUSED_TRACE_SHA256
                       and binding.reused_capture_source == prerequisite.CANONICAL_REUSED_CAPTURE_SOURCE)
    cells = capture_matrix.load_matrix(root)
    if runtime_identity_err and canonical_reuse:
        return {"experiment_id": "R2-08", "run_id": run_id, "outcome": OUTCOME_FAIL,
                "message": runtime_identity_err,
                "capture_matrix": _cell_diagnostics(cells)}
    try:
        _ensure_reused_coding_cell(binding, root, model_sha, runtime_sha)
        cells = capture_matrix.load_matrix(root)
    except Exception as e:  # noqa: BLE001
        return {"experiment_id": "R2-08", "run_id": run_id, "outcome": OUTCOME_FAIL,
                "message": f"failed to register reused coding-v1/seed-42 cell: {e}"}

    newly_performed = 0
    reused_count = sum(1 for c in cells.values() if c.reused and c.canonical_status)

    # Capture only missing/invalid cells (never recapture valid ones)
    missing = capture_matrix.missing_or_invalid_cells(cells)
    if missing:
        if capture_fn is None and model_path is None:
            return {"experiment_id": "R2-08", "run_id": run_id, "outcome": OUTCOME_BLOCKED,
                    "message": "capture cells missing/invalid and no --model path provided; "
                               "offline analysis requires all 8 valid canonical captures.",
                    "capture_matrix": _cell_diagnostics(cells)}
        # runtime_path ownership: kwargs is the single authoritative carrier for
        # the capture call (execute_capture accepts and resolves it). The injectable
        # capture_fn receives the same full kwargs; never duplicate runtime_path.
        do_capture = capture_fn or capture_mod.execute_capture
        capture_dir_root = root / "R2-08" / "captures" / run_id
        for wid, seed in missing:
            if (wid, seed) == workloads.REUSED_CELL:
                continue
            out_dir = capture_dir_root / f"{wid}-seed{seed}"
            # execute (or mock) capture
            kwargs = dict(model_path=model_path or "", workload_id=wid, seed=seed, out_dir=out_dir,
                          allow_dirty=allow_dirty, runtime_path=runtime_path, dry_run=False)
            result = do_capture(**kwargs)
            if result.get("outcome") not in ("CAPTURED",):
                # preserve valid captures, do not register failed as canonical
                validation = result.get("validation") or {}
                cell = capture_matrix.CaptureCell(
                    workload_id=wid, workload_version=workloads.WORKLOADS_BY_ID[wid].version,
                    seed=seed, prompt_sha256=capture_mod.prompt_sha256_for_workload(wid),
                    model_sha256=model_sha, runtime_sha256=runtime_sha,
                    configuration_fingerprint="", configuration={},
                    capture_run_id=result.get("run_id"), trace_path=result.get("trace_path") or validation.get("path"),
                    trace_sha256=validation.get("trace_sha256"), validation_state="INVALID",
                    canonical_status=False, note=f"capture failed: {result.get('outcome')}",
                )
                capture_matrix.upsert_cell(cell, root)
                cells = capture_matrix.load_matrix(root)
                return {"experiment_id": "R2-08", "run_id": run_id, "outcome": OUTCOME_BLOCKED,
                        "message": f"capture failed for {wid}/seed-{seed}: {result.get('outcome')}; "
                                   "valid completed cells preserved; rerun resumes from missing/invalid cells.",
                        "capture_result": result, "capture_matrix": _cell_diagnostics(cells)}
            # atomic registration after validation
            val = result.get("validation") or capture_mod.validate_trace_file(Path(result["trace_path"]))
            if not val.get("ok"):
                cell = capture_matrix.CaptureCell(
                    workload_id=wid, workload_version=workloads.WORKLOADS_BY_ID[wid].version,
                    seed=seed, prompt_sha256=capture_mod.prompt_sha256_for_workload(wid),
                    model_sha256=model_sha, runtime_sha256=runtime_sha,
                    configuration_fingerprint="", configuration={},
                    trace_path=result.get("trace_path"), trace_sha256=val.get("trace_sha256"),
                    validation_state="INVALID", canonical_status=False, note=f"validation failed: {val.get('reason')}",
                )
                capture_matrix.upsert_cell(cell, root)
                cells = capture_matrix.load_matrix(root)
                return {"experiment_id": "R2-08", "run_id": run_id, "outcome": OUTCOME_BLOCKED,
                        "message": f"trace validation failed for {wid}/seed-{seed}: {val.get('reason')}",
                        "capture_matrix": _cell_diagnostics(cells)}
            result_runtime_sha = result.get("runtime_sha256")
            effective_runtime_sha = runtime_sha
            if _looks_like_sha256(result_runtime_sha):
                if _looks_like_sha256(runtime_sha) and str(result_runtime_sha) != str(runtime_sha):
                    return {"experiment_id": "R2-08", "run_id": run_id, "outcome": OUTCOME_FAIL,
                            "message": (f"runtime SHA mismatch for {wid}/seed-{seed}: capture returned "
                                        f"{result_runtime_sha}, expected verified {runtime_sha}"),
                            "capture_matrix": _cell_diagnostics(cells)}
                effective_runtime_sha = str(result_runtime_sha)
                if not _looks_like_sha256(runtime_sha) or runtime_sha == _PLACEHOLDER_RUNTIME_SHA:
                    runtime_sha = effective_runtime_sha
                    # Upgrade reused cell to the same verified runtime identity.
                    try:
                        _ensure_reused_coding_cell(binding, root, model_sha, runtime_sha)
                        cells = capture_matrix.load_matrix(root)
                    except Exception as e:  # noqa: BLE001
                        return {"experiment_id": "R2-08", "run_id": run_id, "outcome": OUTCOME_FAIL,
                                "message": f"failed to bind reused cell runtime identity: {e}"}
            conf = capture_mod.configuration_for_cell(wid, seed, model_sha256=model_sha, runtime_sha256=effective_runtime_sha)
            cell = capture_matrix.CaptureCell(
                workload_id=wid, workload_version=workloads.WORKLOADS_BY_ID[wid].version,
                seed=seed, prompt_sha256=conf["prompt_sha256"], model_sha256=model_sha,
                runtime_sha256=effective_runtime_sha, configuration_fingerprint=conf["fingerprint_hash"],
                configuration=conf["fingerprint"], capture_run_id=result.get("run_id"),
                capture_source=result.get("capture_dir"), trace_path=result["trace_path"],
                trace_sha256=val["trace_sha256"], validation_state="VALID", canonical_status=True,
                reused=False, note="captured",
            )
            capture_matrix.upsert_cell(cell, root)  # persist after every validated capture
            cells = capture_matrix.load_matrix(root)
            newly_performed += 1

    cells = capture_matrix.load_matrix(root)
    if not capture_matrix.all_canonical_valid(cells):
        return {"experiment_id": "R2-08", "run_id": run_id, "outcome": OUTCOME_INCONCLUSIVE,
                "message": "fewer than 8 valid canonical capture cells",
                "capture_matrix": _cell_diagnostics(cells)}

    # Canonical reuse requires a verified (non-placeholder) runtime identity bound
    # consistently across all 8 cells before scientific analysis.
    if canonical_reuse:
        bad = [c.cell_id for c in cells.values()
               if c.canonical_status and c.validation_state == "VALID"
               and not _looks_like_sha256(c.runtime_sha256)]
        if bad:
            return {"experiment_id": "R2-08", "run_id": run_id, "outcome": OUTCOME_FAIL,
                    "message": ("verified runtime SHA required for canonical R2-08 cells; "
                                f"placeholder/non-verified runtime identity remains: {bad}"),
                    "capture_matrix": _cell_diagnostics(cells)}
        runtime_shas = {c.runtime_sha256 for c in cells.values() if c.canonical_status}
        if len(runtime_shas) != 1 or not _looks_like_sha256(next(iter(runtime_shas), "")):
            return {"experiment_id": "R2-08", "run_id": run_id, "outcome": OUTCOME_FAIL,
                    "message": f"all canonical R2-08 cells must share one verified runtime SHA; got {sorted(runtime_shas)}",
                    "capture_matrix": _cell_diagnostics(cells)}

    # configuration fingerprint compatibility
    fps = [c.configuration for c in cells.values() if c.configuration]
    fp_compat = capture_matrix.compare_fingerprint_compatibility(fps) if fps else {"compatible": True, "reason": "no configuration dicts (reused trace metadata limited)"}

    # matched horizon
    infos = []
    for (wid, seed), c in sorted(cells.items()):
        tr = _load_trace_for_cell(c)
        total, pre, dec = matched_horizon.count_positions(tr)
        infos.append(matched_horizon.CaptureHorizonInfo(wid, seed, c.trace_sha256, total, pre, dec,
                                                        matched_horizon.valid_decode_positions(tr)))
    horizon = matched_horizon.compute_matched_horizon(infos)
    if not horizon.get("ok"):
        return {"experiment_id": "R2-08", "run_id": run_id, "outcome": OUTCOME_INCONCLUSIVE,
                "message": horizon.get("reason"), "horizon": horizon,
                "capture_matrix": _cell_diagnostics(cells)}

    # verify decode phase available
    for c in cells.values():
        tr = _load_trace_for_cell(c)
        if matched_horizon.valid_decode_positions(tr) <= 0:
            return {"experiment_id": "R2-08", "run_id": run_id, "outcome": OUTCOME_INCONCLUSIVE,
                    "message": f"decode phase not reliably available for {c.cell_id}"}

    # double synthesis for determinism
    try:
        synth_a = _synthesize(cells, horizon)
        synth_b = _synthesize(cells, horizon)
    except claims_mod.ForbiddenClaimError as e:
        return {"experiment_id": "R2-08", "run_id": run_id, "outcome": OUTCOME_FAIL,
                "message": f"forbidden claim generated: {e}"}
    except Exception as e:  # noqa: BLE001
        return {"experiment_id": "R2-08", "run_id": run_id, "outcome": OUTCOME_FAIL,
                "message": f"synthesis failed: {e}"}

    digest_a = _build_canonical_digest(binding, cells, synth_a, fp_compat)
    digest_b = _build_canonical_digest(binding, cells, synth_b, fp_compat)
    if digest_a != digest_b:
        return {"experiment_id": "R2-08", "run_id": run_id, "outcome": OUTCOME_FAIL,
                "message": f"nondeterministic synthesis: {digest_a} != {digest_b}"}

    if not fp_compat.get("compatible", False):
        outcome = OUTCOME_FAIL
        reasons = [f"configuration fingerprint drift: {fp_compat}"]
    else:
        outcome = OUTCOME_PASS
        reasons = ["all prerequisites verified; 8 canonical cells valid; decode phase available; "
                   f"matched horizon H={horizon['h']}>=256; deterministic double synthesis identical; "
                   "no forbidden claims."]

    synth_a["headline"]["all_configuration_fingerprints_compatible"] = fp_compat.get("compatible")
    synth_a["headline"]["reused_capture_count"] = sum(1 for c in cells.values() if c.reused)
    synth_a["headline"]["newly_performed_capture_count"] = newly_performed

    rec["identity"]["end_utc"] = provenance.utc_now_iso()
    rec["identity"]["outcome"] = outcome
    rec["experiment"]["parameters"] = {
        "binding": binding.to_json(), "matched_horizon": horizon["h"],
        "newly_performed_capture_count": newly_performed,
    }
    run_dir = store.run_dir("R2-08", run_id, root)
    _write_json(run_dir / "manifest.json", rec)
    _write_json(run_dir / "provenance.json", {
        "binding": binding.to_json(), "canonical_digest": digest_a,
        "configuration_fingerprint_compatibility": fp_compat,
        "upstream_digests": {eid: bridges.load_upstream_digest(eid, root) for eid in ("R2-05", "R2-06", "R2-07")},
    })
    _write_json(run_dir / "summary.json", {
        "experiment_id": "R2-08", "run_id": run_id, "outcome": outcome,
        "reasons": reasons, "binding": binding.to_json(), "canonical_digest": digest_a,
        "headline": synth_a["headline"], "comparison": synth_a["comparison"],
        "horizon": horizon, "capture_matrix": _cell_diagnostics(cells),
        "scope": "offline descriptive characterization (temporal drift, same-workload replicate variability, cross-workload variability) on Qwen3-30B-A3B-Q4_K_M under tested protocol; no predictor, no retention-policy change, no performance claim, no cross-model claim.",
    })
    _write_json(run_dir / "capture_matrix.json", _cell_diagnostics(cells))
    _write_json(run_dir / "workload_manifest.json", workloads.workload_manifest_json())
    _write_json(run_dir / "capture_diagnostics.json", {"horizon": horizon, "cells": _cell_diagnostics(cells)})
    _write_json(run_dir / "temporal_drift.json", synth_a["temporal_by_capture"])
    _write_json(run_dir / "replicate_variability.json", synth_a["replicate_variability"])
    _write_json(run_dir / "cross_workload.json", synth_a["cross_workload_variability"])
    _write_json(run_dir / "layer_summary.json", {"heterogeneity": synth_a["heterogeneity"],
                                                 "workload_temporal_medians": synth_a["workload_temporal_medians"]})
    _write_json(run_dir / "workload_metrics.json", synth_a["workload_metrics"])
    _write_json(run_dir / "comparison.json", synth_a["comparison"])
    _write_json(run_dir / "claims.json", synth_a["claims"])
    _write_json(run_dir / "figure_data.json", synth_a["figure_data"])

    # CSV artifacts
    _write_temporal_csv(run_dir / "temporal_drift.csv", synth_a["temporal_by_capture"])
    _write_replicate_csv(run_dir / "replicate_variability.csv", synth_a["replicate_variability"])
    _write_cross_csv(run_dir / "cross_workload.csv", synth_a["cross_workload_variability"])
    _write_layer_csv(run_dir / "layer_summary.csv", synth_a["temporal_by_capture"], synth_a["cross_workload_variability"])

    store.append_registry_entry({"identity": rec["identity"], "run_dir": str(run_dir)}, root)
    if outcome == OUTCOME_PASS:
        for step in ("IMPLEMENTED", "READY", "RUNNING"):
            store.set_experiment_state("R2-08", step, root)
        store.set_experiment_state("R2-08", "COMPLETED", root)
    elif outcome == OUTCOME_INCONCLUSIVE:
        for step in ("IMPLEMENTED", "READY", "RUNNING"):
            store.set_experiment_state("R2-08", step, root)
        store.set_experiment_state("R2-08", "INCONCLUSIVE", root)
    elif outcome == OUTCOME_FAIL:
        for step in ("IMPLEMENTED", "READY", "RUNNING"):
            store.set_experiment_state("R2-08", step, root)
        store.set_experiment_state("R2-08", "FAILED", root)

    return {
        "experiment_id": "R2-08", "run_id": run_id, "outcome": outcome,
        "reasons": reasons, "binding": binding.to_json(), "canonical_digest": digest_a,
        "headline": synth_a["headline"], "comparison": synth_a["comparison"],
        "horizon": horizon, "run_dir": str(run_dir),
        "capture_matrix": _cell_diagnostics(cells),
        "scope": "offline descriptive characterization; no predictor, no retention-policy change, no performance claim, no cross-model claim.",
    }


def _write_temporal_csv(path: Path, temporal_by_capture: Dict[str, Any]) -> None:
    fields = ["workload_id", "seed", "trace_sha256", "median_temporal_jsd",
              "median_top8_temporal_jaccard_distance"]
    with path.open("w", newline="", encoding="utf-8") as fh:
        w = csv.DictWriter(fh, fieldnames=fields)
        w.writeheader()
        for k in sorted(temporal_by_capture):
            a = temporal_by_capture[k]
            w.writerow({f: a.get(f) for f in fields})


def _write_replicate_csv(path: Path, replicate: Dict[str, Any]) -> None:
    fields = ["workload_id", "layer_id", "frequency_jsd", "top8_jaccard_distance",
              "top16_jaccard_distance", "normalized_entropy_difference"]
    with path.open("w", newline="", encoding="utf-8") as fh:
        w = csv.DictWriter(fh, fieldnames=fields)
        w.writeheader()
        for wid, item in sorted((replicate.get("per_workload") or {}).items()):
            for layer, row in sorted((item.get("per_layer") or {}).items(), key=lambda kv: int(kv[0])):
                w.writerow({"workload_id": wid, "layer_id": layer, **row})


def _write_cross_csv(path: Path, cross: Dict[str, Any]) -> None:
    fields = ["workload_a", "seed_a", "workload_b", "seed_b", "median_layer_frequency_jsd"]
    with path.open("w", newline="", encoding="utf-8") as fh:
        w = csv.DictWriter(fh, fieldnames=fields)
        w.writeheader()
        for pair in cross.get("pairs", []):
            w.writerow({f: pair.get(f) for f in fields})


def _write_layer_csv(path: Path, temporal_by_capture: Dict[str, Any], cross: Dict[str, Any]) -> None:
    fields = ["layer_id", "median_temporal_jsd_across_captures", "median_cross_workload_jsd_across_pairs"]
    # aggregate
    t_by_layer = {}
    for a in temporal_by_capture.values():
        for ls, lt in (a.get("temporal_drift_per_layer") or {}).items():
            if lt.get("median_adjacent_window_jsd") is not None:
                t_by_layer.setdefault(int(ls), []).append(lt["median_adjacent_window_jsd"])
    c_by_layer = {}
    for pair in cross.get("pairs", []):
        for ls, pl in (pair.get("per_layer") or {}).items():
            c_by_layer.setdefault(int(ls), []).append(pl["frequency_jsd"])
    layers = sorted(set(t_by_layer) | set(c_by_layer))
    with path.open("w", newline="", encoding="utf-8") as fh:
        w = csv.DictWriter(fh, fieldnames=fields)
        w.writeheader()
        for layer in layers:
            w.writerow({"layer_id": layer,
                        "median_temporal_jsd_across_captures": _median(t_by_layer.get(layer, [])),
                        "median_cross_workload_jsd_across_pairs": _median(c_by_layer.get(layer, []))})


def analyse(*, run_id: Optional[str] = None, evidence_root: Optional[Path] = None, **_ignored: Any) -> Dict[str, Any]:
    root = evidence_root or store.evidence_root()
    registry = [e for e in store.load_registry(root)
                if e.get("identity", {}).get("experiment_id") == "R2-08"]
    if not registry:
        return {"experiment_id": "R2-08", "outcome": "NOT_IMPLEMENTED",
                "message": "no R2-08 run exists yet to analyse"}
    if run_id:
        matches = [e for e in registry if e["identity"]["run_id"] == run_id]
        if not matches:
            raise KeyError(f"no R2-08 run with run_id={run_id!r}")
        entry = matches[0]
    else:
        completed = [e for e in registry if e["identity"]["outcome"] in (OUTCOME_PASS, OUTCOME_INCONCLUSIVE, OUTCOME_FAIL)]
        if not completed:
            return {"experiment_id": "R2-08", "outcome": "NOT_IMPLEMENTED",
                    "message": "no completed R2-08 run exists yet to analyse"}
        latest_ends = sorted({e["identity"]["end_utc"] for e in completed}, reverse=True)
        newest = [e for e in completed if e["identity"]["end_utc"] == latest_ends[0]]
        if len(newest) > 1:
            raise ValueError(f"latest R2-08 run ambiguous: {[e['identity']['run_id'] for e in newest]}")
        entry = newest[0]
    run_dir = Path(entry["run_dir"])
    summary = json.loads((run_dir / "summary.json").read_text(encoding="utf-8"))
    return {**summary, "run_dir": str(run_dir), "analysed_from_existing_evidence": True}
