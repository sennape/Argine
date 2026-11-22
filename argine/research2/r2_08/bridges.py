"""R2-08 bridges to R2-05/R2-06/R2-07 (sections 22-24, 44).

Reuses R2-05/06/07 definitions where scientifically compatible. Bridge metrics
for R2-08 workload summaries are computed on the matched decode-horizon trace
view (primary analysis domain) so cross-workload comparisons are comparable.
"""

from __future__ import annotations

import json
import statistics
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence

from .. import store
from ...research.trace import Trace
from ..r2_05 import metrics as r2_05_metrics
from ..r2_05 import request_unit as r2_05_request_unit
from ..r2_06 import concentration as r2_06_concentration
from ..r2_06 import info_metrics as r2_06_info_metrics
from ..r2_06 import matrix as r2_06_matrix
from ..r2_06 import transition_unit as r2_06_transition_unit
from ..r2_07 import reuse_stats as r2_07_reuse_stats
from ..r2_07 import temporal_unit as r2_07_temporal_unit
from ..r2_07 import working_set as r2_07_working_set

R205_BRIDGE_NOTE = (
    "R2-05 metrics (expert frequency, normalized entropy, effective-expert "
    "fraction, top-k traffic share) are reused via r2_05 definitions on the "
    "matched decode-horizon trace view."
)
R206_BRIDGE_NOTE = (
    "R2-06 metrics (NMI, self-transition lift) are reused via r2_06 definitions "
    "on the matched decode-horizon trace view."
)
R207_BRIDGE_NOTE = (
    "R2-07 metrics (immediate reuse, reuse<=4/16, median token reuse gap, W32/W128 "
    "working-set sizes) are reused via r2_07 definitions on the matched "
    "decode-horizon trace view."
)


def compute_r2_05_summary(trace: Trace) -> Dict[str, Any]:
    n_experts = trace.meta.n_experts or None
    counts_per_layer = r2_05_request_unit.count_requests_per_layer(trace)
    per_layer = {}
    for layer in sorted(counts_per_layer):
        per_layer[layer] = r2_05_metrics.layer_concentration_metrics(
            layer, counts_per_layer[layer], nominal_expert_count=n_experts)
    vals = [m for m in per_layer.values() if m.get("normalized_entropy") is not None]
    def _med(key):
        xs = [m[key] for m in vals if m.get(key) is not None]
        return statistics.median(xs) if xs else None
    top8 = []
    for m in vals:
        tk = m.get("top_k_share") or {}
        if 8 in tk:
            top8.append(tk[8])
    return {
        "per_layer": per_layer,
        "normalized_entropy_median": _med("normalized_entropy"),
        "effective_expert_fraction_median": _med("effective_expert_fraction"),
        "top8_share_median": statistics.median(top8) if top8 else None,
        "note": R205_BRIDGE_NOTE,
    }


def compute_r2_06_summary(trace: Trace) -> Dict[str, Any]:
    counts = r2_06_transition_unit.count_transitions_per_layer(trace)
    tables = r2_06_matrix.all_transition_tables(counts)
    info = r2_06_info_metrics.all_layer_information_metrics(tables)
    self_metrics = {l: r2_06_concentration.self_transition_metrics(tables[l]) for l in tables}
    nmis = [info[l]["normalized_mutual_information"] for l in info
            if info[l].get("normalized_mutual_information") is not None]
    lifts = [self_metrics[l]["median_self_transition_lift"] for l in self_metrics
             if self_metrics[l].get("median_self_transition_lift") is not None]
    return {
        "per_layer_nmi": {str(l): info[l] for l in info},
        "per_layer_self_transition": {str(l): self_metrics[l] for l in self_metrics},
        "nmi_median": statistics.median(nmis) if nmis else None,
        "self_transition_lift_median": statistics.median(lifts) if lifts else None,
        "note": R206_BRIDGE_NOTE,
    }


def compute_r2_07_summary(trace: Trace) -> Dict[str, Any]:
    n_experts = trace.meta.n_experts or None
    per_layer = {}
    for layer in sorted(trace.layers):
        reuse = r2_07_temporal_unit.compute_layer_reuse_events(trace, layer)
        gaps = [ev["token_reuse_gap"] for ev in reuse["reuse_events"]]
        gap_dist = r2_07_reuse_stats.token_reuse_gap_distribution(gaps)
        ws = r2_07_working_set.layer_working_set_metrics(
            reuse["positions"], nominal_expert_count=n_experts, windows=(32, 128))
        w32 = ws.get(32, {})
        w128 = ws.get(128, {})
        per_layer[layer] = {
            "immediate_reuse_fraction": (gap_dist.get("within_threshold_fraction") or {}).get(1),
            "reuse_within_4_fraction": (gap_dist.get("within_threshold_fraction") or {}).get(4),
            "reuse_within_16_fraction": (gap_dist.get("within_threshold_fraction") or {}).get(16),
            "median_token_reuse_gap": gap_dist.get("median"),
            "w32_working_set_size": w32.get("median_working_set_size"),
            "w128_working_set_size": w128.get("median_working_set_size"),
            "w32_available": w32.get("available"),
            "w128_available": w128.get("available"),
        }
    def _med(key):
        xs = [m[key] for m in per_layer.values() if m.get(key) is not None]
        return statistics.median(xs) if xs else None
    return {
        "per_layer": per_layer,
        "immediate_reuse_median": _med("immediate_reuse_fraction"),
        "reuse_le4_median": _med("reuse_within_4_fraction"),
        "reuse_le16_median": _med("reuse_within_16_fraction"),
        "median_token_reuse_gap_median": _med("median_token_reuse_gap"),
        "w32_working_set_median": _med("w32_working_set_size"),
        "w128_working_set_median": _med("w128_working_set_size"),
        "note": R207_BRIDGE_NOTE,
    }


def compute_workload_bridge_metrics(trace: Trace) -> Dict[str, Any]:
    return {
        "r2_05": compute_r2_05_summary(trace),
        "r2_06": compute_r2_06_summary(trace),
        "r2_07": compute_r2_07_summary(trace),
    }


def load_upstream_digest(experiment_id: str, root: Optional[Path] = None) -> Optional[Dict[str, Any]]:
    registry = [e for e in store.load_registry(root)
               if e.get("identity", {}).get("experiment_id") == experiment_id
               and e.get("identity", {}).get("outcome") == "PASS"]
    if not registry:
        return None
    latest_ends = sorted({e["identity"]["end_utc"] for e in registry}, reverse=True)
    newest = [e for e in registry if e["identity"]["end_utc"] == latest_ends[0]]
    entry = sorted(newest, key=lambda e: e["identity"]["run_id"])[0]
    run_dir = Path(entry["run_dir"])
    out = {"run_id": entry["identity"]["run_id"], "run_dir": str(run_dir)}
    summary_path = run_dir / "summary.json"
    if summary_path.is_file():
        try:
            s = json.loads(summary_path.read_text(encoding="utf-8"))
            out["canonical_digest"] = s.get("canonical_digest")
            out["headline"] = s.get("headline")
        except (ValueError, OSError):
            pass
    return out
