"""R2-07 sections 8/9: deterministic quantile semantics and the
reuse-gap / distinct-intervening-reuse-distance distributions.

Quantile method (predeclared, documented BEFORE any real execution):
LINEAR INTERPOLATION BETWEEN ORDER STATISTICS over the sorted sample
(the same method NumPy/R call "linear"/"type 7"), 0-indexed: for a
sorted sample of n values and quantile q in [0, 1], the "virtual index"
is h = q * (n - 1); the result is
    sorted[floor(h)] + (h - floor(h)) * (sorted[ceil(h)] - sorted[floor(h)]).
Chosen because it is a single, standard, widely-documented rule with no
free parameters to tune per-result, and because it needs no assumption
about the population beyond the sample itself (no inferred unobserved
tail behaviour -- section 8's explicit instruction). Median = quantile
at q=0.5.

Both distributions (token reuse gap, section 8; distinct-intervening
reuse distance, section 9) use this SAME quantile function; only their
PREDECLARED within-threshold fraction cutoffs differ, per the task's own
section 8/9 lists.
"""

from __future__ import annotations

import math
from typing import Dict, List, Optional, Sequence

# Section 8: predeclared token-reuse-gap within-threshold cutoffs.
TOKEN_GAP_THRESHOLDS = (1, 2, 4, 8, 16, 32, 64, 128, 256)

# Section 9: predeclared distinct-intervening-distance within-threshold
# cutoffs (a DIFFERENT, smaller predeclared list -- the task's own
# section 9 only names 4/8/16/32/64, not the full gap list; this
# distinction is deliberate, not an oversight).
DISTINCT_INTERVENING_THRESHOLDS = (4, 8, 16, 32, 64)

QUANTILE_LEVELS = (0.50, 0.75, 0.90, 0.95)

QUANTILE_METHOD_DEFINITION = (
    "Linear interpolation between order statistics over the sorted sample "
    "(NumPy/R 'linear'/type-7 convention): for sorted values and quantile q, "
    "h = q * (n - 1); result = sorted[floor(h)] + (h - floor(h)) * "
    "(sorted[ceil(h)] - sorted[floor(h)]). No unobserved-tail inference."
)


def quantile(sorted_values: Sequence[float], q: float) -> Optional[float]:
    """The predeclared linear-interpolation quantile. None for an empty
    sample (fail-closed: never a fabricated value)."""
    n = len(sorted_values)
    if n == 0:
        return None
    if n == 1:
        return float(sorted_values[0])
    h = q * (n - 1)
    lo = math.floor(h)
    hi = math.ceil(h)
    if lo == hi:
        return float(sorted_values[lo])
    frac = h - lo
    return sorted_values[lo] + frac * (sorted_values[hi] - sorted_values[lo])


def within_threshold_fractions(values: Sequence[float], thresholds: Sequence[int]
                               ) -> Dict[int, Optional[float]]:
    """fraction of `values` that are <= each predeclared threshold. None
    (not 0.0) when `values` is empty -- an empty reuse-event population
    has no defined fraction, fail-closed rather than fabricated."""
    n = len(values)
    if n == 0:
        return {t: None for t in thresholds}
    return {t: sum(1 for v in values if v <= t) / n for t in thresholds}


def distribution_summary(values: Sequence[float], *, thresholds: Sequence[int]
                         ) -> Dict[str, object]:
    """Full distribution summary for either the token-reuse-gap or the
    distinct-intervening-reuse-distance population: n, median/p75/p90/
    p95, max, and the predeclared within-threshold fractions. All
    quantile/max fields are None for an empty population (never a
    fabricated 0 or 0.0)."""
    sorted_values = sorted(values)
    n = len(sorted_values)
    return {
        "n": n,
        "median": quantile(sorted_values, 0.50),
        "p75": quantile(sorted_values, 0.75),
        "p90": quantile(sorted_values, 0.90),
        "p95": quantile(sorted_values, 0.95),
        "max": (sorted_values[-1] if n else None),
        "within_threshold_fraction": within_threshold_fractions(sorted_values, thresholds),
    }


def token_reuse_gap_distribution(gaps: Sequence[int]) -> Dict[str, object]:
    return distribution_summary(gaps, thresholds=TOKEN_GAP_THRESHOLDS)


def distinct_intervening_distribution(distances: Sequence[int]) -> Dict[str, object]:
    return distribution_summary(distances, thresholds=DISTINCT_INTERVENING_THRESHOLDS)
