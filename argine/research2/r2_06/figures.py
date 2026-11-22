"""R2-06 section 17: machine-readable figure data for the later II-B
paper. Plain data series only -- no styling, no new metric semantics,
no paper freeze (deferred past R2-07/R2-08, mirroring R2-05's own
figures.py docstring).
"""

from __future__ import annotations

from typing import Any, Dict, List

from . import support


def figure_a_nmi_by_layer(layer_info: Dict[int, Dict[str, Any]]) -> List[Dict[str, Any]]:
    """Figure candidate A: normalized mutual information by layer."""
    out = []
    for layer in sorted(layer_info):
        m = layer_info[layer]
        out.append({"layer_id": layer,
                    "normalized_mutual_information": m["normalized_mutual_information"],
                    "mutual_information": m["mutual_information"],
                    "conditional_entropy": m["conditional_entropy"]})
    return out


def figure_b_observed_vs_marginal_expected(tables: Dict[int, Dict[str, Any]],
                                           independence_tables: Dict[int, Dict[str, Any]]
                                           ) -> Dict[int, List[Dict[str, Any]]]:
    """Figure candidate B: observed conditional P(j|i) vs the marginal-
    expected P(j) under independence, for every cell actually observed
    -- the direct visual of 'does observing the source change the
    target distribution'."""
    out: Dict[int, List[Dict[str, Any]]] = {}
    for layer, table in tables.items():
        rows = []
        indep = independence_tables.get(layer, {})
        for i in table["sources"]:
            for j in sorted(table["conditional"].get(i, {})):
                observed = table["conditional"][i][j]
                expected = indep.get("target_marginal", {}).get(j)
                rows.append({"source_expert_id": i, "target_expert_id": j,
                            "observed_conditional_probability": observed,
                            "marginal_expected_probability": expected})
        out[layer] = rows
    return out


def figure_c_strongest_supported_lifts(tables: Dict[int, Dict[str, Any]]
                                       ) -> List[Dict[str, Any]]:
    """Figure candidate C: the single strongest MIN_PAIR_SUPPORT-qualified
    lift per layer (section 14's support-filtered headline view)."""
    out = []
    for layer in sorted(tables):
        candidate = support.strongest_supported_lift(tables[layer])
        if candidate is not None:
            i, j, lift_value = candidate
            out.append({"layer_id": layer, "source_expert_id": i, "target_expert_id": j,
                        "lift": lift_value,
                        "transition_count": tables[layer]["counts"].get((i, j))})
    return out


def figure_d_self_transition_lift_by_layer(layer_self_transitions: Dict[int, Dict[str, Any]]
                                           ) -> List[Dict[str, Any]]:
    """Figure candidate D: median self-transition lift by layer."""
    out = []
    for layer in sorted(layer_self_transitions):
        st = layer_self_transitions[layer]
        out.append({"layer_id": layer,
                    "median_self_transition_lift": st["median_self_transition_lift"],
                    "aggregate_self_transition_share": st["aggregate_self_transition_share"]})
    return out
