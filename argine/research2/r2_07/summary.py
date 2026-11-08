"""R2-07 sections 20/21: layer heterogeneity and the global cross-layer
headline summary.

All cross-layer aggregates are LAYER-MEDIAN style (mirroring R2-05's and
R2-06's own "treat every layer as one equally-weighted observation"
convention for their global summaries) -- computed only over layers
that actually have a defined value for the metric in question (a layer
with zero reuse events, for instance, contributes no value to the
median-token-reuse-gap aggregate, rather than a fabricated one).
"""

from __future__ import annotations

import statistics
from typing import Any, Dict, List, Optional

HEADLINE_WORKING_SET_WINDOWS = (8, 32, 128, 256)


def _median(values: List[float]) -> Optional[float]:
    return statistics.median(values) if values else None


def layer_heterogeneity(layer_gap_distributions: Dict[int, Dict[str, Any]],
                        layer_window_metrics: Dict[int, Dict[int, Dict[str, Any]]]
                        ) -> Dict[str, Any]:
    """Section 20: identifies, WITHOUT assigning any architectural cause
    (section 20's own instruction), the layers with the shortest/longest
    median token reuse gap, and the smallest/largest median W=32 and
    W=128 working sets."""
    with_median_gap = {l: d["median"] for l, d in layer_gap_distributions.items()
                       if d["median"] is not None}
    shortest_gap_layer = (min(with_median_gap, key=lambda l: (with_median_gap[l], l))
                          if with_median_gap else None)
    longest_gap_layer = (max(with_median_gap, key=lambda l: (with_median_gap[l], -l))
                         if with_median_gap else None)

    def _extreme_by_window(window: int):
        with_value = {l: m[window]["median_working_set_size"]
                     for l, m in layer_window_metrics.items()
                     if m.get(window, {}).get("available") and
                     m[window]["median_working_set_size"] is not None}
        if not with_value:
            return None, None
        smallest = min(with_value, key=lambda l: (with_value[l], l))
        largest = max(with_value, key=lambda l: (with_value[l], -l))
        return smallest, largest

    smallest_w32, largest_w32 = _extreme_by_window(32)
    smallest_w128, largest_w128 = _extreme_by_window(128)

    return {
        "shortest_median_reuse_gap_layer": shortest_gap_layer,
        "longest_median_reuse_gap_layer": longest_gap_layer,
        "smallest_median_working_set_w32_layer": smallest_w32,
        "largest_median_working_set_w32_layer": largest_w32,
        "smallest_median_working_set_w128_layer": smallest_w128,
        "largest_median_working_set_w128_layer": largest_w128,
        "note": "descriptive variation only; no architectural cause is assigned "
               "(section 20).",
    }


def global_summary(layer_gap_distributions: Dict[int, Dict[str, Any]],
                   layer_distinct_distributions: Dict[int, Dict[str, Any]],
                   layer_window_metrics: Dict[int, Dict[int, Dict[str, Any]]],
                   layer_null_comparison: Dict[int, Dict[str, Any]]) -> Dict[str, Any]:
    """Section 21's global headline, built from per-layer dicts."""
    immediate_fractions = [d["within_threshold_fraction"].get(1)
                           for d in layer_gap_distributions.values()
                           if d["within_threshold_fraction"].get(1) is not None]
    within_4_fractions = [d["within_threshold_fraction"].get(4)
                          for d in layer_gap_distributions.values()
                          if d["within_threshold_fraction"].get(4) is not None]
    within_16_fractions = [d["within_threshold_fraction"].get(16)
                           for d in layer_gap_distributions.values()
                           if d["within_threshold_fraction"].get(16) is not None]
    medians = [d["median"] for d in layer_gap_distributions.values() if d["median"] is not None]
    p90s = [d["p90"] for d in layer_gap_distributions.values() if d["p90"] is not None]
    distinct_medians = [d["median"] for d in layer_distinct_distributions.values()
                        if d["median"] is not None]

    window_medians = {}
    for w in HEADLINE_WORKING_SET_WINDOWS:
        vals = [m[w]["median_working_set_size"] for m in layer_window_metrics.values()
                if m.get(w, {}).get("available") and m[w]["median_working_set_size"] is not None]
        window_medians[w] = _median(vals)

    heterogeneity = layer_heterogeneity(layer_gap_distributions, layer_window_metrics)

    return {
        "layer_count": len(layer_gap_distributions),
        "layer_count_with_reuse_events": len(medians),
        "median_immediate_reuse_fraction": _median(immediate_fractions),
        "median_reuse_within_4_fraction": _median(within_4_fractions),
        "median_reuse_within_16_fraction": _median(within_16_fractions),
        "median_token_reuse_gap": _median(medians),
        "median_p90_token_reuse_gap": _median(p90s),
        "median_distinct_intervening_reuse_distance": _median(distinct_medians),
        "median_working_set_size_by_window": window_medians,
        "observed_vs_null": observed_vs_null_summary(layer_null_comparison),
        "heterogeneity": heterogeneity,
    }


_SCALAR_COMPARISON_KEYS = (
    "immediate_reuse_fraction", "reuse_within_4_fraction", "reuse_within_16_fraction",
    "median_token_reuse_gap", "p90_token_reuse_gap",
    "median_distinct_intervening_reuse_distance",
)
_WORKING_SET_COMPARISON_WINDOWS = (8, 32, 128, 256)


def observed_vs_null_summary(layer_comparisons: Dict[int, Dict[str, Any]]) -> Dict[str, Any]:
    """Section 15/21: a cross-layer, median-based descriptive observed-
    vs-null comparison for each predeclared metric. Ratio/difference are
    None (never fabricated) whenever either side is undefined for that
    metric across all layers. No formal significance claim is made
    anywhere here (section 15's own instruction) -- these are plain
    descriptive medians, ratios, and differences."""
    out: Dict[str, Any] = {}
    for key in _SCALAR_COMPARISON_KEYS:
        observed_vals = [c[key]["observed"] for c in layer_comparisons.values()
                         if c.get(key, {}).get("observed") is not None]
        null_vals = [c[key]["null"] for c in layer_comparisons.values()
                    if c.get(key, {}).get("null") is not None]
        med_obs = _median(observed_vals)
        med_null = _median(null_vals)
        ratio = (med_obs / med_null) if (med_obs is not None and med_null) else None
        diff = (med_obs - med_null) if (med_obs is not None and med_null is not None) else None
        out[key] = {"median_observed": med_obs, "median_null": med_null,
                   "ratio_observed_to_null": ratio, "difference_observed_minus_null": diff}

    working_set_out = {}
    for w in _WORKING_SET_COMPARISON_WINDOWS:
        observed_vals = [c["working_set_size"][w]["observed"] for c in layer_comparisons.values()
                         if c.get("working_set_size", {}).get(w, {}).get("observed") is not None]
        null_vals = [c["working_set_size"][w]["null"] for c in layer_comparisons.values()
                    if c.get("working_set_size", {}).get(w, {}).get("null") is not None]
        med_obs = _median(observed_vals)
        med_null = _median(null_vals)
        ratio = (med_obs / med_null) if (med_obs is not None and med_null) else None
        diff = (med_obs - med_null) if (med_obs is not None and med_null is not None) else None
        working_set_out[w] = {"median_observed": med_obs, "median_null": med_null,
                             "ratio_observed_to_null": ratio,
                             "difference_observed_minus_null": diff}
    out["working_set_size"] = working_set_out
    return out
