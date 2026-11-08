"""R2-05 section 9/13: the global II-B/R2-05 summary, built WITHOUT
collapsing layer identity (every input is still a per-layer dict; this
module only aggregates read-only statistics over them).

Two aggregate styles are reported, and kept explicitly distinct per
section 9's own instruction:

  - LAYER-MEDIAN (and min/max): treats every layer as one equally-weighted
    observation, regardless of how many requests it saw.
  - REQUEST-WEIGHTED aggregate: weights each layer's contribution by its
    own total_requests, so a layer that saw ten million requests counts
    proportionally more than one that saw ten.

Primary concentration axis for "most/least concentrated layer" (section
9) is NORMALIZED ENTROPY: it is already bounded to [0, 1] and comparable
across layers regardless of each layer's own N, unlike the Gini/HHI raw
values whose own baselines also depend on N. Lower normalized entropy =
more concentrated; this choice is stated explicitly here rather than left
implicit.
"""

from __future__ import annotations

import statistics
from typing import Any, Dict, List, Optional


def _median(values: List[float]) -> Optional[float]:
    return statistics.median(values) if values else None


def _request_weighted_mean(pairs: List["tuple[float, int]"]) -> Optional[float]:
    """pairs: [(value, weight), ...]. Layers with weight 0 (zero-request
    layers) contribute nothing and are naturally excluded."""
    total_weight = sum(w for _, w in pairs)
    if total_weight == 0:
        return None
    return sum(v * w for v, w in pairs) / total_weight


def global_summary(layer_metrics: Dict[int, Dict[str, Any]]) -> Dict[str, Any]:
    layers_with_data = {l: m for l, m in layer_metrics.items() if m["normalized_entropy"] is not None}

    norm_entropies = [m["normalized_entropy"] for m in layers_with_data.values()]
    eff_counts = [m["effective_expert_count"] for m in layers_with_data.values()]
    ginis = [m["gini"] for m in layers_with_data.values()]
    hhis = [m["hhi"] for m in layers_with_data.values()]
    top8 = [m["top_k_share"].get(8) for m in layers_with_data.values() if 8 in m["top_k_share"]]
    top16 = [m["top_k_share"].get(16) for m in layers_with_data.values() if 16 in m["top_k_share"]]

    most_concentrated = (min(layers_with_data, key=lambda l: layers_with_data[l]["normalized_entropy"])
                         if layers_with_data else None)
    least_concentrated = (max(layers_with_data, key=lambda l: layers_with_data[l]["normalized_entropy"])
                          if layers_with_data else None)

    weighted_entropy = _request_weighted_mean(
        [(m["normalized_entropy"], m["total_requests"]) for m in layers_with_data.values()])
    weighted_gini = _request_weighted_mean(
        [(m["gini"], m["total_requests"]) for m in layers_with_data.values()])
    weighted_hhi = _request_weighted_mean(
        [(m["hhi"], m["total_requests"]) for m in layers_with_data.values()])
    weighted_eff_fraction = _request_weighted_mean(
        [(m["effective_expert_fraction"], m["total_requests"]) for m in layers_with_data.values()])
    weighted_top8 = _request_weighted_mean(
        [(m["top_k_share"][8], m["total_requests"]) for m in layers_with_data.values()
         if 8 in m["top_k_share"]])
    weighted_top16 = _request_weighted_mean(
        [(m["top_k_share"][16], m["total_requests"]) for m in layers_with_data.values()
         if 16 in m["top_k_share"]])

    experts_for = {}
    for pct in (0.50, 0.80, 0.90, 0.95):
        vals = [m["experts_for_pct"].get(pct) for m in layers_with_data.values()
               if m["experts_for_pct"].get(pct) is not None]
        experts_for[pct] = {
            "median": _median(vals), "min": (min(vals) if vals else None),
            "max": (max(vals) if vals else None),
        }

    return {
        "layer_count_with_data": len(layers_with_data),
        "layer_count_total": len(layer_metrics),
        "layer_median": {
            "normalized_entropy": _median(norm_entropies),
            "normalized_entropy_min": (min(norm_entropies) if norm_entropies else None),
            "normalized_entropy_max": (max(norm_entropies) if norm_entropies else None),
            "effective_expert_count": _median(eff_counts),
            "effective_expert_count_min": (min(eff_counts) if eff_counts else None),
            "effective_expert_count_max": (max(eff_counts) if eff_counts else None),
            "gini": _median(ginis),
            "hhi": _median(hhis),
            "top_8_share": _median(top8),
            "top_16_share": _median(top16),
        },
        "request_weighted_aggregate": {
            "normalized_entropy": weighted_entropy,
            "gini": weighted_gini,
            "hhi": weighted_hhi,
            "effective_expert_fraction": weighted_eff_fraction,
            "top_8_share": weighted_top8,
            "top_16_share": weighted_top16,
        },
        "most_concentrated_layer": most_concentrated,
        "least_concentrated_layer": least_concentrated,
        "experts_for_pct": experts_for,
        "concentration_axis": "normalized_entropy (lower = more concentrated)",
    }


def layer_heterogeneity(layer_metrics: Dict[int, Dict[str, Any]]) -> Dict[str, Any]:
    """Section 13: descriptive spread only -- no causal explanation, no
    temporal/workload-drift claim (that is R2-08's)."""
    layers_with_data = {l: m for l, m in layer_metrics.items() if m["normalized_entropy"] is not None}
    if not layers_with_data:
        return {"spread": None, "classification": "NO_DATA",
               "note": "no layer had any counted requests"}
    values = [m["normalized_entropy"] for m in layers_with_data.values()]
    spread = max(values) - min(values)
    classification = ("BROADLY_HOMOGENEOUS" if spread < 0.10 else
                      "HETEROGENEOUS" if spread < 0.40 else "STRONGLY_HETEROGENEOUS")
    return {
        "normalized_entropy_spread": spread,
        "classification": classification,
        "note": "descriptive spread of normalized entropy across layers only; no causal "
               "explanation and no temporal/workload-drift claim is made here (see R2-08).",
    }
