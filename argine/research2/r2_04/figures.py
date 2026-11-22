"""R2-04 section 21: machine-readable paper-figure data. No graphical
styling is produced here (a later paper-freeze step does that) -- these
are plain data series, reshaped from already-computed R2-03/R2-04
evidence, no new metric semantics.
"""

from __future__ import annotations

from typing import Any, Dict, List

from . import decomposition as decomp


def figure_a_miss_rate_by_budget(budget_results: Dict[int, Dict[str, Dict[str, Any]]],
                                 global_lru_label: str, oracle_label: str
                                 ) -> List[Dict[str, Any]]:
    """Figure A: Global-LRU vs Oracle miss rate by budget."""
    out = []
    for budget in sorted(budget_results):
        g = budget_results[budget].get(global_lru_label)
        o = budget_results[budget].get(oracle_label)
        if g is None or o is None:
            continue
        out.append({"budget_bytes": budget,
                   "global_lru_miss_rate": g["miss_rate"],
                   "oracle_miss_rate": o["miss_rate"]})
    return out


def figure_b_information_headroom_fraction_by_budget(
        decomposition_by_budget: Dict[int, Dict[str, Any]]) -> List[Dict[str, Any]]:
    """Figure B: relative information-headroom fraction (POLICY-
    INFORMATION HEADROOM, miss-byte view) by budget."""
    out = []
    for budget in sorted(decomposition_by_budget):
        info = decomposition_by_budget[budget][decomp.KEY_INFORMATION]
        out.append({"budget_bytes": budget,
                   "relative_miss_byte_reduction": info["relative_miss_byte_reduction"],
                   "relative_miss_reduction": info["relative_miss_reduction"]})
    return out


def figure_c_structural_vs_informational_absolute_gap_by_budget(
        decomposition_by_budget: Dict[int, Dict[str, Any]]) -> List[Dict[str, Any]]:
    """Figure C: structural vs informational absolute miss-byte gap by
    budget (the two components whose sum is the total observed
    upper-bound gap -- see decomposition.py's self-check)."""
    out = []
    for budget in sorted(decomposition_by_budget):
        d = decomposition_by_budget[budget]
        out.append({
            "budget_bytes": budget,
            "structural_miss_byte_gap": d[decomp.KEY_STRUCTURAL]["miss_byte_reduction"],
            "informational_miss_byte_gap": d[decomp.KEY_INFORMATION]["miss_byte_reduction"],
            "total_miss_byte_gap": d[decomp.KEY_TOTAL]["miss_byte_reduction"],
        })
    return out
