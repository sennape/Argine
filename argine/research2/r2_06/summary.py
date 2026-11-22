"""R2-06 section 16: the global cross-layer summary.

Primary concentration/structure axis for "strongest/weakest layer" is
NORMALIZED MUTUAL INFORMATION (NMI): bounded in [0, 1] (where defined)
and comparable across layers regardless of each layer's own marginal
entropy, mirroring R2-05's own choice of normalized entropy as its
single deterministic cross-layer comparison axis (summary.py there).
Higher NMI = more same-layer transition structure beyond marginal
frequency; this choice is stated explicitly, as R2-05's was.
"""

from __future__ import annotations

import statistics
from typing import Any, Dict, List, Optional

from . import support


def _median(values: List[float]) -> Optional[float]:
    return statistics.median(values) if values else None


def global_summary(layer_info: Dict[int, Dict[str, Any]],
                   layer_source_rows: Dict[int, List[Dict[str, Any]]],
                   layer_self_transitions: Dict[int, Dict[str, Any]],
                   tables: Dict[int, Dict[str, Any]]) -> Dict[str, Any]:
    layers_with_nmi = {l: m for l, m in layer_info.items()
                       if m["normalized_mutual_information"] is not None}
    nmis = [m["normalized_mutual_information"] for m in layers_with_nmi.values()]
    cond_entropies = [m["conditional_entropy"] for m in layer_info.values()
                      if m["conditional_entropy"] is not None]

    self_lifts = [layer_self_transitions[l]["median_self_transition_lift"]
                 for l in layer_self_transitions
                 if layer_self_transitions[l]["median_self_transition_lift"] is not None]

    top1_shares = []
    eff_fractions = []
    for layer, rows in layer_source_rows.items():
        top1 = [r["top_1_target_share"] for r in rows if r["top_1_target_share"] is not None]
        if top1:
            top1_shares.append(statistics.median(top1))
        eff = [r["outgoing_effective_target_count"] for r in rows
              if r["outgoing_effective_target_count"] is not None and
              r["distinct_targets_observed"] > 0]
        distinct = [r["distinct_targets_observed"] for r in rows
                   if r["distinct_targets_observed"] > 0]
        fractions = [e / d for e, d in zip(eff, distinct) if d > 0]
        if fractions:
            eff_fractions.append(statistics.median(fractions))

    fraction_layers_nmi_positive = (
        sum(1 for v in nmis if v > 0.0) / len(nmis) if nmis else None)

    strongest_layer = (max(layers_with_nmi, key=lambda l: (layers_with_nmi[l][
        "normalized_mutual_information"], -l)) if layers_with_nmi else None)
    weakest_layer = (min(layers_with_nmi, key=lambda l: (layers_with_nmi[l][
        "normalized_mutual_information"], l)) if layers_with_nmi else None)

    max_supported_lift = None
    max_supported_lift_layer = None
    for layer, table in tables.items():
        candidate = support.strongest_supported_lift(table)
        if candidate is not None:
            _, _, lift_value = candidate
            if max_supported_lift is None or lift_value > max_supported_lift:
                max_supported_lift = lift_value
                max_supported_lift_layer = layer

    return {
        "layer_count_with_nmi": len(layers_with_nmi),
        "layer_count_total": len(layer_info),
        "median_nmi": _median(nmis),
        "min_nmi": (min(nmis) if nmis else None),
        "max_nmi": (max(nmis) if nmis else None),
        "median_conditional_entropy": _median(cond_entropies),
        "median_self_transition_lift": _median(self_lifts),
        "fraction_layers_with_nmi_positive": fraction_layers_nmi_positive,
        "median_top_1_outgoing_probability": _median(top1_shares),
        "median_outgoing_effective_target_fraction": _median(eff_fractions),
        "max_supported_transition_lift": max_supported_lift,
        "max_supported_transition_lift_layer": max_supported_lift_layer,
        "strongest_layer_by_nmi": strongest_layer,
        "weakest_layer_by_nmi": weakest_layer,
        "structure_axis": "normalized_mutual_information (higher = more same-layer "
                          "transition structure beyond marginal frequency)",
        "support_threshold_rationale": support.SUPPORT_THRESHOLD_RATIONALE,
    }
