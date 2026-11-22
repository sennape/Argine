"""R2-04 sections 9/10/11/12/14: regime classification, peak-opportunity
detection, and headroom persistence -- all mechanically derived from the
fixed, already-replayed R2-03 budget points. No regime, peak budget, or
curve shape is assumed ahead of time; every value here is computed from
whatever `decompose_all_budgets()` / `budget_results` actually contain.
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional

from . import decomposition as decomp

CURVE_MONOTONIC_INCREASING = "monotonic_increasing"
CURVE_MONOTONIC_DECREASING = "monotonic_decreasing"
CURVE_SINGLE_INTERIOR_MAXIMUM = "single_interior_maximum"
CURVE_MULTIPLE_LOCAL_MAXIMA = "multiple_local_maxima"
CURVE_PLATEAU = "plateau"
CURVE_COLLAPSE_AT_EFFECTIVE_RESIDENCY = "collapse_at_effective_residency"
CURVE_OTHER_NON_MONOTONIC = "other_non_monotonic"

_EPS = 1e-9


def capacity_constrained_budgets(budget_results: Dict[int, Dict[str, Dict[str, Any]]],
                                 global_lru_label: str) -> List[int]:
    """Section 14: tested budgets where Global-LRU evictions > 0 (the
    predeclared definition of 'under actual memory pressure')."""
    out = []
    for budget in sorted(budget_results):
        row = budget_results[budget].get(global_lru_label)
        if row is not None and row["evictions"] > 0:
            out.append(budget)
    return out


def effective_residency_budget(budget_results: Dict[int, Dict[str, Dict[str, Any]]],
                               global_lru_label: str, oracle_label: str
                               ) -> Optional[int]:
    """Section 9: the FIRST tested budget (ascending) at which Global-LRU
    and Oracle have equal misses, equal miss bytes, AND both zero
    evictions. Reported only as the first TESTED budget exhibiting this
    convergence -- never claimed to be the mathematically minimal
    capacity (section 9's own caveat)."""
    for budget in sorted(budget_results):
        g = budget_results[budget].get(global_lru_label)
        o = budget_results[budget].get(oracle_label)
        if g is None or o is None:
            continue
        if (g["misses"] == o["misses"] and g["miss_bytes"] == o["miss_bytes"]
               and g["evictions"] == 0 and o["evictions"] == 0):
            return budget
    return None


def peak_opportunity(decomposition_by_budget: Dict[int, Dict[str, Any]]) -> Dict[str, Any]:
    """Section 10: four maxima over the POLICY-INFORMATION HEADROOM gap,
    determined independently -- never assumed to coincide at the same
    budget."""
    info_by_budget = {
        b: d[decomp.KEY_INFORMATION] for b, d in decomposition_by_budget.items()
    }
    if not info_by_budget:
        return {
            "max_relative_miss_byte_headroom": None,
            "max_absolute_miss_byte_headroom": None,
            "max_relative_miss_headroom": None,
            "max_absolute_miss_headroom": None,
        }

    def _argmax(key: str) -> Dict[str, Any]:
        valued = [(b, info[key]) for b, info in info_by_budget.items() if info[key] is not None]
        if not valued:
            return {"budget_bytes": None, "value": None}
        budget, value = max(valued, key=lambda bv: bv[1])
        return {"budget_bytes": budget, "value": value}

    return {
        "max_relative_miss_byte_headroom": _argmax("relative_miss_byte_reduction"),
        "max_absolute_miss_byte_headroom": _argmax("miss_byte_reduction"),
        "max_relative_miss_headroom": _argmax("relative_miss_reduction"),
        "max_absolute_miss_headroom": _argmax("miss_reduction"),
    }


def headroom_persistence(decomposition_by_budget: Dict[int, Dict[str, Any]],
                         capacity_constrained: List[int],
                         effective_residency: Optional[int]) -> Dict[str, Any]:
    """Section 14: does the Oracle strictly improve over Global-LRU (the
    POLICY-INFORMATION HEADROOM gap) at EVERY capacity-constrained tested
    point? The effective-residency point is explicitly excluded from this
    question (section 14) -- under true convergence, retention policy no
    longer has anything left to matter for, which is a different question
    than whether it matters under pressure."""
    checked = [b for b in capacity_constrained if b != effective_residency]
    violations = []
    for budget in checked:
        info = decomposition_by_budget.get(budget, {}).get(decomp.KEY_INFORMATION)
        if info is None:
            violations.append({"budget_bytes": budget, "reason": "no decomposition available"})
            continue
        if not (info["miss_byte_reduction"] > 0 and info["miss_reduction"] > 0):
            violations.append({"budget_bytes": budget,
                              "miss_byte_reduction": info["miss_byte_reduction"],
                              "miss_reduction": info["miss_reduction"]})
    return {
        "capacity_constrained_budgets_checked": checked,
        "effective_residency_budget_excluded": effective_residency,
        "oracle_strictly_improves_at_every_capacity_constrained_point": not violations,
        "violations": violations,
    }


def classify_curve_shape(primary_curve: List[Dict[str, Any]],
                         effective_residency: Optional[int]) -> Dict[str, Any]:
    """Section 12: deterministic classification of the observed relative
    information-headroom-fraction curve. Derived purely from the fixed
    tested points -- never assumed ahead of time, even though the current
    real evidence IS expected (per the task's own framing) to land in the
    'rise followed by collapse at the effective-residency point' category."""
    points = sorted(primary_curve, key=lambda p: p["budget_bytes"])
    budgets = [p["budget_bytes"] for p in points]
    values = [p["relative_miss_byte_reduction"] for p in points]

    if len(values) < 2:
        return {"classification": CURVE_OTHER_NON_MONOTONIC,
               "reason": "fewer than 2 tested points with a defined value"}

    if max(values) - min(values) <= _EPS:
        return {"classification": CURVE_PLATEAU,
               "reason": "all tested points' relative information-headroom fraction "
                         "are equal within numerical tolerance"}

    diffs = [values[i + 1] - values[i] for i in range(len(values) - 1)]

    if all(d > _EPS for d in diffs):
        return {"classification": CURVE_MONOTONIC_INCREASING,
               "reason": "every successive tested point strictly increases"}
    if all(d < -_EPS for d in diffs):
        return {"classification": CURVE_MONOTONIC_DECREASING,
               "reason": "every successive tested point strictly decreases"}

    # Collapse-at-effective-residency: the effective-residency budget is
    # the LAST tested budget, its value is the curve's global minimum, and
    # every point strictly before it was non-decreasing up to its
    # immediate predecessor (i.e. the collapse happens exactly at, and
    # only at, the convergence point -- not an arbitrary interior dip).
    if (effective_residency is not None and effective_residency == budgets[-1]
           and values[-1] <= min(values) + _EPS
           and values[-1] < values[-2] - _EPS
           and all(d >= -_EPS for d in diffs[:-1])):
        return {"classification": CURVE_COLLAPSE_AT_EFFECTIVE_RESIDENCY,
               "reason": f"the curve rises (or holds) up to the budget immediately "
                         f"before the observed effective-residency budget "
                         f"({effective_residency} bytes), then collapses to its "
                         f"global minimum exactly at that budget"}

    # Local-maxima count (interior points strictly greater than both
    # neighbours).
    local_maxima = [i for i in range(1, len(values) - 1)
                    if values[i] > values[i - 1] + _EPS and values[i] > values[i + 1] + _EPS]
    if len(local_maxima) == 1:
        return {"classification": CURVE_SINGLE_INTERIOR_MAXIMUM,
               "reason": f"exactly one interior point ({budgets[local_maxima[0]]} "
                         f"bytes) exceeds both its neighbours"}
    if len(local_maxima) > 1:
        return {"classification": CURVE_MULTIPLE_LOCAL_MAXIMA,
               "reason": f"{len(local_maxima)} interior points each exceed both "
                         f"their neighbours: budgets {[budgets[i] for i in local_maxima]}"}

    return {"classification": CURVE_OTHER_NON_MONOTONIC,
           "reason": "the curve is neither monotonic, a plateau, a single- or "
                     "multiple-interior-maximum shape, nor an effective-residency "
                     "collapse"}
