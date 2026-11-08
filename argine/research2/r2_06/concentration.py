"""R2-06 section 11/12: per-source-expert transition concentration, and
self-transition metrics.

Top-k target shares predeclared as (1, 4, 8) -- matching exactly the
subset of R2-05's own CANDIDATE_TOP_K that section 10 of the R2-06 task
names explicitly ("top-1/top-4/top-8 target share"); R2-06 does not
reuse R2-05's full (1,4,8,16,32) tuple since a transition row's outgoing
fan-out is a materially different quantity (bounded by however many
DISTINCT targets a source expert was ever observed to transition to,
usually far fewer than the full expert population) from R2-05's
per-layer expert population.
"""

from __future__ import annotations

import math
from typing import Any, Dict, List, Tuple

from . import info_metrics

TOP_K_TARGET_SHARES = (1, 4, 8)


def source_concentration(table: Dict[str, Any], source_expert_id: int) -> Dict[str, Any]:
    """Section 11's per-source-expert metrics: most-likely next expert,
    top-1/4/8 target share, outgoing conditional entropy, outgoing
    effective target count. Fail-closed (returns all-None fields) for a
    source with no outgoing mass."""
    dist = table["conditional"].get(source_expert_id, {})
    row_sum = table["row_sums"].get(source_expert_id, 0)
    if not dist or row_sum == 0:
        return {
            "layer_id": table["layer_id"], "source_expert_id": source_expert_id,
            "outgoing_total": row_sum, "most_likely_next_expert": None,
            "top_1_target_share": None, "top_k_target_share": {},
            "outgoing_conditional_entropy": None, "outgoing_effective_target_count": None,
            "distinct_targets_observed": 0,
        }

    ordered = sorted(dist.items(), key=lambda kv: (-(kv[1] or 0.0), kv[0]))
    most_likely = ordered[0][0]
    probs_desc = [p for _, p in ordered if p is not None]
    top_k_share = {k: sum(probs_desc[:k]) for k in TOP_K_TARGET_SHARES}
    h_out = info_metrics.outgoing_entropy(dist)
    eff_targets = math.exp(h_out)

    return {
        "layer_id": table["layer_id"], "source_expert_id": source_expert_id,
        "outgoing_total": row_sum, "most_likely_next_expert": most_likely,
        "top_1_target_share": top_k_share.get(1), "top_k_target_share": top_k_share,
        "outgoing_conditional_entropy": h_out, "outgoing_effective_target_count": eff_targets,
        "distinct_targets_observed": len(dist),
    }


def layer_source_concentration_rows(table: Dict[str, Any]) -> List[Dict[str, Any]]:
    """One row per source expert in this layer, deterministic ascending
    order by source_expert_id."""
    return [source_concentration(table, i) for i in table["sources"]]


def self_transition_metrics(table: Dict[str, Any]) -> Dict[str, Any]:
    """Section 12: P(i->i) share, lift over marginal, and aggregate
    self-transition mass -- computed WITHOUT assuming dominance; a layer
    where self-transitions are rare is an equally valid, reportable
    result."""
    rows = []
    total_self_mass = 0
    for i in table["sources"]:
        p_self_given_i = table["conditional"].get(i, {}).get(i)
        lift_self = table["lift"].get(i, {}).get(i)
        self_count = table["counts"].get((i, i), 0)
        total_self_mass += self_count
        rows.append({
            "layer_id": table["layer_id"], "expert_id": i,
            "self_transition_probability": p_self_given_i,
            "self_transition_lift": lift_self,
            "self_transition_count": self_count,
        })
    total_mass = table["total_transition_mass"]
    aggregate_self_share = (total_self_mass / total_mass) if total_mass else None
    lifts = [r["self_transition_lift"] for r in rows if r["self_transition_lift"] is not None]
    return {
        "layer_id": table["layer_id"],
        "rows": rows,
        "aggregate_self_transition_mass": total_self_mass,
        "aggregate_self_transition_share": aggregate_self_share,
        "median_self_transition_lift": (sorted(lifts)[len(lifts) // 2] if lifts and
                                        len(lifts) % 2 == 1 else
                                        ((sorted(lifts)[len(lifts)//2 - 1] +
                                          sorted(lifts)[len(lifts)//2]) / 2.0
                                         if lifts else None)),
    }


def layer_aggregate_concentration(source_rows: List[Dict[str, Any]]) -> Dict[str, Any]:
    """Section 11's per-layer aggregate over its own per-source rows:
    median/min/max top-1 target share and outgoing effective target
    count, restricted to sources with defined values (non-fail-closed
    rows)."""
    top1 = [r["top_1_target_share"] for r in source_rows if r["top_1_target_share"] is not None]
    eff = [r["outgoing_effective_target_count"] for r in source_rows
          if r["outgoing_effective_target_count"] is not None]

    def _median(values):
        if not values:
            return None
        s = sorted(values)
        n = len(s)
        return s[n // 2] if n % 2 == 1 else (s[n // 2 - 1] + s[n // 2]) / 2.0

    return {
        "median_top_1_target_share": _median(top1),
        "min_top_1_target_share": (min(top1) if top1 else None),
        "max_top_1_target_share": (max(top1) if top1 else None),
        "median_outgoing_effective_target_count": _median(eff),
    }
