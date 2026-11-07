"""R2-03's predeclared curves (section "PRIMARY METRICS"): built purely by
reshaping the per-budget, per-policy rows already computed by
r2_02.comparison.policy_row / oracle_gap -- no new metric semantics.
"""

from __future__ import annotations

from typing import Any, Dict, List

from ..r2_02 import comparison as r2_02_comparison
from ..r2_02 import policy_set


def oracle_gaps_by_budget(budget_results: Dict[int, Dict[str, Dict[str, Any]]]
                          ) -> Dict[int, Dict[str, Dict[str, Any]]]:
    """{budget_bytes: {baseline_label: oracle_gap dict}} for every budget
    where both the baseline and the Oracle produced a row."""
    out: Dict[int, Dict[str, Dict[str, Any]]] = {}
    for budget, rows in budget_results.items():
        if policy_set.ORACLE_LABEL not in rows:
            continue
        oracle_row = rows[policy_set.ORACLE_LABEL]
        out[budget] = {
            baseline: r2_02_comparison.oracle_gap(rows[baseline], oracle_row)
            for baseline in policy_set.CAUSAL_BASELINE_LABELS if baseline in rows
        }
    return out


def primary_curve_relative_miss_byte_reduction(
        gaps_by_budget: Dict[int, Dict[str, Dict[str, Any]]],
        *, baseline: str = policy_set.LABEL_GLOBAL_LRU) -> List[Dict[str, Any]]:
    """The R2-03 primary curve: relative Oracle miss-byte reduction (vs
    Global-LRU) as a function of budget, in increasing-budget order.
    Reported as the ACTUAL observed shape -- never assumed monotonic."""
    out = []
    for budget in sorted(gaps_by_budget):
        gap = gaps_by_budget[budget].get(baseline)
        if gap is None:
            continue
        out.append({"budget_bytes": budget,
                   "relative_miss_byte_reduction": gap["relative_miss_byte_reduction"],
                   "relative_miss_reduction": gap["relative_miss_reduction"]})
    return out


def miss_rate_curve(budget_results: Dict[int, Dict[str, Dict[str, Any]]], label: str
                    ) -> List[Dict[str, Any]]:
    """Secondary curve: one policy's miss rate as a function of budget."""
    out = []
    for budget in sorted(budget_results):
        row = budget_results[budget].get(label)
        if row is None:
            continue
        out.append({"budget_bytes": budget, "miss_rate": row["miss_rate"]})
    return out
