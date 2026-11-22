"""R2-11 frozen paired bootstrap procedure (decision_rules.json.bootstrap_rule).

Phase-0 only: implements the frozen bootstrap semantics so they can be
unit-tested on synthetic data before any real R2-11 trace is captured.
This module is NOT invoked against real R2-11 data in this phase.
"""
from __future__ import annotations

import random
from typing import List, Sequence, Tuple

BOOTSTRAP_RESAMPLES = 100_000
BOOTSTRAP_SEED = 42
CONFIDENCE_LEVEL = 0.95


def _percentile(sorted_values: Sequence[float], pct: float) -> float:
    """Linear-interpolation percentile, matching numpy.percentile's default 'linear' method."""
    n = len(sorted_values)
    if n == 1:
        return sorted_values[0]
    rank = pct / 100.0 * (n - 1)
    lo = int(rank)
    hi = min(lo + 1, n - 1)
    frac = rank - lo
    return sorted_values[lo] * (1 - frac) + sorted_values[hi] * frac


def paired_bootstrap_ci(
    deltas: Sequence[float],
    resamples: int = BOOTSTRAP_RESAMPLES,
    seed: int = BOOTSTRAP_SEED,
    confidence: float = CONFIDENCE_LEVEL,
) -> Tuple[float, float, float]:
    """Paired bootstrap over workload instances.

    `deltas` is the sequence of per-instance paired deltas
    (PredictedProtectPolicy_i - B_selected_i) at a single budget. Each
    resample draws len(deltas) instances WITH replacement as paired units
    (never resampling policies independently), computes the equal-weight
    mean of that resample, and the empirical (lo, hi) percentile interval
    of the resulting distribution of means is returned alongside the
    point-estimate mean of the observed (non-resampled) deltas.

    Returns (observed_mean, ci_lower, ci_upper).
    """
    n = len(deltas)
    if n == 0:
        raise ValueError("deltas must be non-empty")
    observed_mean = sum(deltas) / n
    rng = random.Random(seed)
    means: List[float] = []
    for _ in range(resamples):
        resample = [deltas[rng.randrange(n)] for _ in range(n)]
        means.append(sum(resample) / n)
    means.sort()
    alpha = 1.0 - confidence
    lo = _percentile(means, 100 * (alpha / 2))
    hi = _percentile(means, 100 * (1 - alpha / 2))
    return observed_mean, lo, hi
