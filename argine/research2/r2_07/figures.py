"""R2-07 section 22: machine-readable figure-ready data for the later
II-B paper. Plain data series only -- no styling, no new metric
semantics, no paper freeze (deferred past R2-08, mirroring R2-05's/
R2-06's own figures.py docstrings).
"""

from __future__ import annotations

from typing import Any, Dict, List, Sequence


def figure_a_reuse_gap_cdf(gaps: Sequence[int]) -> List[Dict[str, Any]]:
    """Figure candidate A: reuse-gap CDF. x = token reuse gap (each
    distinct observed value), y = fraction of reuse events with gap <=
    x. Built directly from the observed sample, never from a fitted
    distribution."""
    if not gaps:
        return []
    sorted_gaps = sorted(gaps)
    n = len(sorted_gaps)
    out = []
    i = 0
    cumulative = 0
    while i < n:
        j = i
        while j + 1 < n and sorted_gaps[j + 1] == sorted_gaps[i]:
            j += 1
        cumulative += (j - i + 1)
        out.append({"token_reuse_gap": sorted_gaps[i], "cumulative_fraction": cumulative / n})
        i = j + 1
    return out


def figure_b_distinct_intervening_cdf(distances: Sequence[int]) -> List[Dict[str, Any]]:
    """Figure candidate B: distinct-intervening-reuse-distance CDF, same
    construction as figure A over the distinct-intervening sample."""
    return [{"distinct_intervening_reuse_distance": row["token_reuse_gap"],
            "cumulative_fraction": row["cumulative_fraction"]}
           for row in figure_a_reuse_gap_cdf(distances)]


def figure_c_working_set_growth_curve(growth_curve_rows: List[Dict[str, Any]]
                                      ) -> List[Dict[str, Any]]:
    """Figure candidate C: window size -> median distinct active experts,
    passed through unchanged from working_set.growth_curve's own output
    (kept as its own figure function per section 22's own structure,
    rather than reusing growth_curve_rows directly at every call site)."""
    return list(growth_curve_rows)


def figure_d_observed_vs_shuffled_working_set(observed_growth_curve: List[Dict[str, Any]],
                                              null_growth_curve: List[Dict[str, Any]]
                                              ) -> List[Dict[str, Any]]:
    """Figure candidate D: observed vs shuffled/null working-set growth
    curve, aligned by window size."""
    null_by_window = {row["window"]: row for row in null_growth_curve}
    out = []
    for row in observed_growth_curve:
        w = row["window"]
        null_row = null_by_window.get(w, {})
        out.append({
            "window": w,
            "observed_median_working_set_size": row.get("median_working_set_size"),
            "null_median_working_set_size": null_row.get("median_working_set_size"),
        })
    return out
