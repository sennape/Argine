"""R2-05 section 22: machine-readable figure data for later II-B paper
figures. Plain data series only -- no styling, no new metric semantics,
no paper freeze (that is explicitly deferred past R2-08, section 22).
"""

from __future__ import annotations

from typing import Any, Dict, List


def figure_a_rank_frequency_by_layer(frequency_rows: List[Dict[str, Any]],
                                     representative_layers: List[int]
                                     ) -> Dict[int, List[Dict[str, Any]]]:
    """Figure candidate A: rank-frequency curve by selected representative
    layers (section 22). Representative layers are chosen by the caller
    (orchestrator picks min/median/max-concentration layers -- a
    deterministic, data-driven but non-arbitrary selection, documented at
    the call site) and passed in explicitly; this function does not
    choose them itself."""
    out: Dict[int, List[Dict[str, Any]]] = {}
    for layer in representative_layers:
        rows = [r for r in frequency_rows if r["layer_id"] == layer]
        rows = sorted(rows, key=lambda r: r["rank_within_layer"])
        out[layer] = [{"rank": r["rank_within_layer"],
                       "request_fraction_within_layer": r["request_fraction_within_layer"]}
                      for r in rows]
    return out


def figure_b_normalized_entropy_and_effective_fraction_by_layer(
        layer_metrics: Dict[int, Dict[str, Any]]) -> List[Dict[str, Any]]:
    """Figure candidate B: normalized entropy / effective expert fraction
    by layer."""
    out = []
    for layer in sorted(layer_metrics):
        m = layer_metrics[layer]
        out.append({
            "layer_id": layer,
            "normalized_entropy": m["normalized_entropy"],
            "effective_expert_fraction": m["effective_expert_fraction"],
        })
    return out


def figure_c_top_k_share_by_layer(layer_metrics: Dict[int, Dict[str, Any]]
                                  ) -> List[Dict[str, Any]]:
    """Figure candidate C: top-k request share by layer."""
    out = []
    for layer in sorted(layer_metrics):
        m = layer_metrics[layer]
        out.append({"layer_id": layer, "top_k_share": dict(m["top_k_share"])})
    return out
