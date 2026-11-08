"""R2-08 same-workload replicate and cross-workload variability (sections 20-21)."""

from __future__ import annotations

from typing import Any, Dict, List, Optional, Sequence, Tuple

from . import drift, workloads


def replicate_variability_for_workload(layer_distributions: Dict[int, Dict[int, List[float]]],
                                       layer_counts: Dict[int, Dict[int, Dict[int, int]]],
                                       nominal: int = drift.NOMINAL_EXPERTS) -> Dict[str, Any]:
    """layer_distributions[layer][seed] = full-support prob vector.
    layer_counts[layer][seed] = counts by expert id.
    """
    per_layer = {}
    for layer in sorted(layer_distributions):
        if 42 not in layer_distributions[layer] or 43 not in layer_distributions[layer]:
            continue
        p42, p43 = layer_distributions[layer][42], layer_distributions[layer][43]
        c42, c43 = layer_counts[layer][42], layer_counts[layer][43]
        e42 = drift.normalized_entropy(p42, nominal=nominal)
        e43 = drift.normalized_entropy(p43, nominal=nominal)
        per_layer[layer] = {
            "frequency_jsd": drift.jsd(p42, p43),
            "top8_jaccard_distance": drift.jaccard_distance(drift.top_k_experts(c42, 8, nominal),
                                                            drift.top_k_experts(c43, 8, nominal)),
            "top16_jaccard_distance": drift.jaccard_distance(drift.top_k_experts(c42, 16, nominal),
                                                             drift.top_k_experts(c43, 16, nominal)),
            "normalized_entropy_difference": (abs(e42 - e43) if e42 is not None and e43 is not None else None),
        }
    vals = [v["frequency_jsd"] for v in per_layer.values()]
    return {
        "per_layer": per_layer,
        "median_replicate_jsd": _median(vals),
        "layer_count": len(per_layer),
    }


def cross_workload_variability(layer_distributions: Dict[Tuple[str, int], Dict[int, List[float]]],
                               layer_counts: Dict[Tuple[str, int], Dict[int, Dict[int, int]]],
                               nominal: int = drift.NOMINAL_EXPERTS) -> Dict[str, Any]:
    """Preserve replicate identity; do not average replicates before raw pairwise evidence."""
    pairs = []
    wids = workloads.CANONICAL_WORKLOAD_IDS
    for i, wa in enumerate(wids):
        for wb in wids[i + 1:]:
            for sa in workloads.SEEDS:
                for sb in workloads.SEEDS:
                    ka, kb = (wa, sa), (wb, sb)
                    if ka not in layer_distributions or kb not in layer_distributions:
                        continue
                    per_layer = {}
                    for layer in sorted(set(layer_distributions[ka]) & set(layer_distributions[kb])):
                        pa, pb = layer_distributions[ka][layer], layer_distributions[kb][layer]
                        ca, cb = layer_counts[ka][layer], layer_counts[kb][layer]
                        per_layer[layer] = {
                            "frequency_jsd": drift.jsd(pa, pb),
                            "top8_jaccard_distance": drift.jaccard_distance(
                                drift.top_k_experts(ca, 8, nominal), drift.top_k_experts(cb, 8, nominal)),
                            "top16_jaccard_distance": drift.jaccard_distance(
                                drift.top_k_experts(ca, 16, nominal), drift.top_k_experts(cb, 16, nominal)),
                        }
                    layer_jsds = [v["frequency_jsd"] for v in per_layer.values()]
                    pairs.append({
                        "workload_a": wa, "seed_a": sa, "workload_b": wb, "seed_b": sb,
                        "per_layer": per_layer,
                        "median_layer_frequency_jsd": _median(layer_jsds),
                    })
    all_jsds = [p["median_layer_frequency_jsd"] for p in pairs if p["median_layer_frequency_jsd"] is not None]
    strongest = None
    if pairs:
        strongest = max((p for p in pairs if p["median_layer_frequency_jsd"] is not None),
                        key=lambda p: p["median_layer_frequency_jsd"], default=None)
    return {
        "pairs": pairs,
        "median_cross_workload_jsd": _median(all_jsds),
        "strongest_workload_pair": (None if strongest is None else {
            "workload_a": strongest["workload_a"], "seed_a": strongest["seed_a"],
            "workload_b": strongest["workload_b"], "seed_b": strongest["seed_b"],
            "median_layer_frequency_jsd": strongest["median_layer_frequency_jsd"],
        }),
    }


def _median(values: Sequence[float]) -> Optional[float]:
    if not values:
        return None
    s = sorted(values)
    n = len(s)
    m = n // 2
    if n % 2 == 1:
        return float(s[m])
    return (s[m - 1] + s[m]) / 2.0
