"""R2-07 sections 10/11/12/13: the rolling working-set definition, the
predeclared canonical windows, per-layer/window metrics, and the
working-set growth curve.

working_set_size(t, W) = the number of distinct layer-local experts
selected at least once in the trailing W positions ending at (and
including) position t, i.e. positions [t - W + 1, t]. FULL WINDOWS
ONLY (section 10): a window is only computed, and only contributes to
any statistic, when the layer has at least W positions available up to
and including t (t >= W - 1) -- a partial/startup window is never
silently included. If a layer has fewer than W positions in total, that
(layer, W) pair is marked UNAVAILABLE rather than fabricating a
full-window result from an incomplete one.

CANONICAL_WINDOWS (section 11) are predeclared BEFORE any real R2-07
execution and never chosen after observing results. 512 may appear ONLY
as a documented, clearly-labelled SECONDARY diagnostic (never a headline
figure) when a layer has enough positions to support a full W=512
window; the canonical HEADLINE stops at 256 (section 11's own
instruction).
"""

from __future__ import annotations

import statistics
from typing import Any, Dict, List, Optional, Sequence, Tuple

from . import reuse_stats

CANONICAL_WINDOWS = (1, 2, 4, 8, 16, 32, 64, 128, 256)
SECONDARY_WINDOW = 512


def sliding_distinct_counts(positions: Sequence[Tuple[int, ...]], window: int) -> List[int]:
    """The full-window working_set_size(t, W) series for one layer, for
    every t with a FULL window available (t >= window - 1), in
    ascending t order. An O(n) sliding-window algorithm: a running
    multiset (expert_id -> count of occurrences currently within the
    window) is maintained as the window slides right by one position at
    a time, so each position's experts are added/removed exactly once
    regardless of how many are in the window at once."""
    n = len(positions)
    if window <= 0 or n < window:
        return []
    counts: Dict[int, int] = {}
    distinct = 0

    def _add(pos_idx: int) -> None:
        nonlocal distinct
        for expert_id in set(positions[pos_idx]):
            if counts.get(expert_id, 0) == 0:
                distinct += 1
            counts[expert_id] = counts.get(expert_id, 0) + 1

    def _remove(pos_idx: int) -> None:
        nonlocal distinct
        for expert_id in set(positions[pos_idx]):
            counts[expert_id] -= 1
            if counts[expert_id] == 0:
                distinct -= 1

    series: List[int] = []
    for t in range(n):
        _add(t)
        if t >= window:
            _remove(t - window)
        if t >= window - 1:
            series.append(distinct)
    return series


def layer_window_metrics(positions: Sequence[Tuple[int, ...]], window: int, *,
                         nominal_expert_count: Optional[int] = None) -> Dict[str, Any]:
    """Section 12's metrics for one (layer, window) pair. UNAVAILABLE
    (section 10) -- not zero, not fabricated -- when the layer has fewer
    than `window` positions."""
    n_positions = len(positions)
    if n_positions < window:
        return {
            "window": window, "available": False, "n_full_windows": 0,
            "mean_working_set_size": None, "median_working_set_size": None,
            "p90_working_set_size": None, "max_working_set_size": None,
            "working_set_fraction": None,
        }
    series = sliding_distinct_counts(positions, window)
    mean_val = statistics.fmean(series) if series else None
    median_val = statistics.median(series) if series else None
    sorted_series = sorted(series)
    p90 = reuse_stats.quantile(sorted_series, 0.90)
    max_val = max(series) if series else None
    fraction = (max_val / nominal_expert_count
               if max_val is not None and nominal_expert_count else None)
    return {
        "window": window, "available": True, "n_full_windows": len(series),
        "mean_working_set_size": mean_val, "median_working_set_size": median_val,
        "p90_working_set_size": p90, "max_working_set_size": max_val,
        "working_set_fraction": fraction,
    }


def layer_working_set_metrics(positions: Sequence[Tuple[int, ...]], *,
                              nominal_expert_count: Optional[int] = None,
                              windows: Sequence[int] = CANONICAL_WINDOWS
                              ) -> Dict[int, Dict[str, Any]]:
    """All canonical-window metrics for one layer, keyed by window size."""
    return {w: layer_window_metrics(positions, w, nominal_expert_count=nominal_expert_count)
           for w in windows}


def growth_curve(positions: Sequence[Tuple[int, ...]], *,
                 windows: Sequence[int] = CANONICAL_WINDOWS) -> List[Dict[str, Any]]:
    """Section 13's primary artifact: window length -> median working-set
    size, for every canonical window the layer has enough positions to
    support. No shape is assumed or forced (section 13's own
    instruction) -- this is a plain, ordered data series."""
    out = []
    for w in windows:
        if len(positions) < w:
            out.append({"window": w, "available": False, "median_working_set_size": None})
            continue
        series = sliding_distinct_counts(positions, w)
        out.append({"window": w, "available": True,
                    "median_working_set_size": statistics.median(series) if series else None})
    return out


def observed_expert_count(positions: Sequence[Tuple[int, ...]]) -> int:
    """The full-trace observed expert population for this layer -- kept
    explicitly distinct from `nominal_expert_count` (the model's
    declared expert count) and from any rolling `working_set_size`
    (section 12's own instruction to keep these three concepts
    separate)."""
    seen = set()
    for selection in positions:
        seen.update(selection)
    return len(seen)
