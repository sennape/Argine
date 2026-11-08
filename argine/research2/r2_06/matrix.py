"""R2-06 sections 7/8/9: the primary per-layer transition matrix,
conditional probabilities, marginals, and lift.

Expert identity is layer-local throughout (mirrors R2-05 section 5):
every cell is keyed by (layer_id, source_expert_id, target_expert_id),
and nothing here ever merges counts for the same raw expert_id across
different layers.

The marginal target distribution P(j) used here is derived FROM the
transition structure itself (the column sums of T_layer), not borrowed
from R2-05's request-unit marginal. This is a deliberate, documented
choice (section 9): lift(i->j) = P(j|i) / P(j) and the information-
theoretic identity I(X;Y) = H(Y) - H(Y|X) (info_metrics.py) both require
P(j) to be the marginal of the SAME joint distribution P(i, j) that
P(j|i) comes from -- substituting R2-05's marginal (built from a
different, single-expert request unit, with different edge-token
handling) would silently break that identity. R2-05's marginal is used
only as a SEPARATE, clearly-labelled reconciliation check (bridge.py),
never as an input to these primary quantities.
"""

from __future__ import annotations

from typing import Any, Dict, Tuple


def layer_transition_table(layer: int, counts: Dict[Tuple[int, int], int]) -> Dict[str, Any]:
    """Builds, for one layer: the raw matrix (as a dict keyed by
    (i, j)), row sums (per-source outgoing totals), column sums
    (per-target incoming totals), the joint P(i,j), the source marginal
    P(i), the target marginal P(j) (both derived from THIS layer's
    transition structure), the conditional P(j|i), and lift(i->j).

    Fail-closed on zero denominators (section 9): P(j|i) and lift(i->j)
    are both None, never a divide-by-zero or a fabricated 0.0, whenever
    their denominator (the source's own outgoing total, or the target's
    marginal probability) is zero."""
    total_mass = sum(counts.values())
    row_sums: Dict[int, int] = {}
    col_sums: Dict[int, int] = {}
    for (i, j), c in counts.items():
        row_sums[i] = row_sums.get(i, 0) + c
        col_sums[j] = col_sums.get(j, 0) + c

    if total_mass == 0:
        return {
            "layer_id": layer, "total_transition_mass": 0, "counts": {}, "row_sums": {},
            "col_sums": {}, "source_marginal": {}, "target_marginal": {}, "conditional": {},
            "lift": {}, "sources": [], "targets": [],
        }

    source_marginal = {i: s / total_mass for i, s in row_sums.items()}
    target_marginal = {j: s / total_mass for j, s in col_sums.items()}

    conditional: Dict[int, Dict[int, float]] = {}
    lift: Dict[int, Dict[int, "float | None"]] = {}
    for (i, j), c in counts.items():
        row_total = row_sums[i]
        p_j_given_i = (c / row_total) if row_total > 0 else None
        conditional.setdefault(i, {})[j] = p_j_given_i
        p_j = target_marginal.get(j, 0.0)
        if p_j_given_i is None or p_j == 0.0:
            lift_value = None
        else:
            lift_value = p_j_given_i / p_j
        lift.setdefault(i, {})[j] = lift_value

    return {
        "layer_id": layer,
        "total_transition_mass": total_mass,
        "counts": dict(counts),
        "row_sums": row_sums,
        "col_sums": col_sums,
        "source_marginal": source_marginal,
        "target_marginal": target_marginal,
        "conditional": conditional,
        "lift": lift,
        "sources": sorted(row_sums),
        "targets": sorted(col_sums),
    }


def all_transition_tables(counts_per_layer: Dict[int, Dict[Tuple[int, int], int]]
                          ) -> Dict[int, Dict[str, Any]]:
    return {layer: layer_transition_table(layer, counts_per_layer[layer])
           for layer in sorted(counts_per_layer)}


def transition_rows(layer: int, table: Dict[str, Any]) -> "list[Dict[str, Any]]":
    """Flat, deterministically-ordered rows for transition_metrics.csv:
    one row per (source_expert_id, target_expert_id) actually observed,
    ordered by source ascending, then target ascending (never by count --
    rank is not a meaningful axis for a sparse pairwise matrix the way it
    is for R2-05's single-expert frequency table)."""
    rows = []
    for i in table["sources"]:
        for j in sorted(table["conditional"].get(i, {})):
            rows.append({
                "layer_id": layer, "source_expert_id": i, "target_expert_id": j,
                "transition_count": table["counts"][(i, j)],
                "conditional_probability": table["conditional"][i][j],
                "source_marginal_probability": table["source_marginal"].get(i),
                "target_marginal_probability": table["target_marginal"].get(j),
                "lift": table["lift"][i][j],
            })
    return rows


def probability_mass_valid(tables: Dict[int, Dict[str, Any]], *,
                           tolerance: float = 1e-9) -> "list[str]":
    """Section 19-style FAIL condition (mirrors frequency.probability_mass_valid):
    for every source expert with any outgoing mass, its conditional
    distribution over targets must sum to 1.0 within tolerance."""
    problems = []
    for layer, table in tables.items():
        for i in table["sources"]:
            dist = table["conditional"].get(i, {})
            mass = sum(p for p in dist.values() if p is not None)
            if abs(mass - 1.0) > tolerance:
                problems.append(f"layer {layer}, source expert {i}: conditional "
                                f"probability mass {mass} does not sum to 1.0 within "
                                f"tolerance {tolerance}")
    return problems
