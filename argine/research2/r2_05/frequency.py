"""R2-05 section 7: the primary per-(layer, expert) frequency table.

Expert identity is layer-local throughout this module (section 5): every
row is keyed by the pair (layer_id, expert_id), and nothing here ever
merges counts for the same raw expert_id across different layers.
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional


def layer_frequency_rows(layer: int, counts: Dict[int, int], *,
                         nominal_expert_count: Optional[int] = None
                         ) -> List[Dict[str, Any]]:
    """One layer's frequency table: deterministic ordering by rank
    (descending count), with expert_id ascending as the tie-breaker
    (section 7). `counts` is {expert_id: request_count} for THIS layer
    only (layer-local; see request_unit.count_requests_per_layer)."""
    total = sum(counts.values())
    ordered = sorted(counts.items(), key=lambda kv: (-kv[1], kv[0]))
    rows: List[Dict[str, Any]] = []
    cumulative = 0
    for rank, (expert_id, count) in enumerate(ordered, start=1):
        cumulative += count
        rows.append({
            "layer_id": layer,
            "expert_id": expert_id,
            "request_count": count,
            "request_fraction_within_layer": (count / total) if total else 0.0,
            "rank_within_layer": rank,
            "cumulative_request_fraction_within_layer": (cumulative / total) if total else 0.0,
        })
    return rows


def layer_population_summary(layer: int, counts: Dict[int, int], *,
                             nominal_expert_count: Optional[int] = None
                             ) -> Dict[str, Any]:
    """section 7's extra per-layer fields: observed-expert count, nominal
    count where known, never-observed count where it can be determined
    (requires an authoritative nominal count; otherwise explicitly None,
    never guessed)."""
    observed = len(counts)
    never_observed = (nominal_expert_count - observed
                      if nominal_expert_count is not None and nominal_expert_count >= observed
                      else None)
    return {
        "layer_id": layer,
        "observed_expert_count": observed,
        "nominal_expert_count": nominal_expert_count,
        "never_observed_expert_count": never_observed,
    }


def all_frequency_rows(counts_per_layer: Dict[int, Dict[int, int]], *,
                       nominal_expert_count: Optional[int] = None
                       ) -> List[Dict[str, Any]]:
    """Full table across every layer, in the canonical deterministic
    order: layer ascending, then rank, then expert_id as tie-breaker
    (already guaranteed within a layer by layer_frequency_rows' own
    ordering; this just concatenates layers in ascending order)."""
    rows: List[Dict[str, Any]] = []
    for layer in sorted(counts_per_layer):
        rows.extend(layer_frequency_rows(layer, counts_per_layer[layer],
                                         nominal_expert_count=nominal_expert_count))
    return rows


def reconcile_total_requests(counts_per_layer: Dict[int, Dict[int, int]],
                             expected_total_per_layer: Dict[int, int]) -> List[str]:
    """Section 19 FAIL condition: frequency counts must reconcile exactly
    with the trace's own request count, per layer. Returns a list of
    human-readable problems (empty = reconciled)."""
    problems: List[str] = []
    for layer, counts in counts_per_layer.items():
        total = sum(counts.values())
        expected = expected_total_per_layer.get(layer)
        if expected is None:
            problems.append(f"layer {layer}: no independently computed expected total "
                            f"to reconcile against")
        elif total != expected:
            problems.append(f"layer {layer}: frequency total {total} != independently "
                            f"recomputed total {expected}")
    return problems


def probability_mass_valid(counts_per_layer: Dict[int, Dict[int, int]], *,
                           tolerance: float = 1e-9) -> List[str]:
    """Section 19 FAIL condition: probability mass per layer must sum to
    1.0 (within floating-point tolerance) whenever the layer has any
    requests at all. A zero-request layer is handled separately (section
    19: 'zero-request layer handling') and is not a probability error."""
    problems: List[str] = []
    for layer, counts in counts_per_layer.items():
        total = sum(counts.values())
        if total == 0:
            continue
        mass = sum((c / total) for c in counts.values())
        if abs(mass - 1.0) > tolerance:
            problems.append(f"layer {layer}: probability mass {mass} does not sum to 1.0 "
                            f"within tolerance {tolerance}")
    return problems
