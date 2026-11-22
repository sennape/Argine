"""R2-08 drift metrics: JSD (log2, full 128 support), entropy, hot-set Jaccard.

Expert identity is (layer_id, expert_id). All frequency vectors use the full
nominal 128-expert support, including zeros.
"""

from __future__ import annotations

import math
from typing import Dict, Iterable, List, Optional, Sequence, Tuple

NOMINAL_EXPERTS = 128
TOP_K_HOTSETS = (8, 16)


def normalize_distribution(counts: Dict[int, int], nominal: int = NOMINAL_EXPERTS) -> List[float]:
    total = sum(counts.get(i, 0) for i in range(nominal))
    if total <= 0:
        # no evidence: return uniform? spec requires valid probability distributions;
        # fail closed by returning None-like handled by caller. But easier: zeros -> all zero invalid.
        return [0.0] * nominal
    return [counts.get(i, 0) / total for i in range(nominal)]


def is_valid_probability_vector(p: Sequence[float], tol: float = 1e-9) -> bool:
    if not p:
        return False
    if any(x < -tol for x in p):
        return False
    return abs(sum(p) - 1.0) <= tol


def shannon_entropy(p: Sequence[float]) -> float:
    return -sum(x * math.log(x) for x in p if x > 0.0)


def normalized_entropy(p: Sequence[float], nominal: int = NOMINAL_EXPERTS) -> Optional[float]:
    if nominal <= 1:
        return 1.0 if shannon_entropy(p) == 0.0 else None
    h = shannon_entropy(p)
    return h / math.log(nominal)


def _kl_divergence(p: Sequence[float], m: Sequence[float]) -> float:
    # 0*log(0/m)=0; m>0 where p>0 in practice for JSD
    s = 0.0
    for pi, mi in zip(p, m):
        if pi > 0.0:
            if mi <= 0.0:
                return float("inf")
            s += pi * math.log2(pi / mi)
    return s


def jsd(p: Sequence[float], q: Sequence[float]) -> float:
    """Jensen-Shannon divergence, log base 2, in [0,1]."""
    if len(p) != len(q):
        raise ValueError("JSD requires equal-length distributions")
    m = [(pi + qi) / 2.0 for pi, qi in zip(p, q)]
    val = 0.5 * _kl_divergence(p, m) + 0.5 * _kl_divergence(q, m)
    # numerical clamp
    if val < 0.0 and val > -1e-12:
        val = 0.0
    if val > 1.0 and val < 1.0 + 1e-12:
        val = 1.0
    return val


def counts_from_selections(selections: Iterable[Tuple[int, ...]]) -> Dict[int, int]:
    counts: Dict[int, int] = {}
    for sel in selections:
        for e in sel:
            counts[e] = counts.get(e, 0) + 1
    return counts


def top_k_experts(counts: Dict[int, int], k: int, nominal: int = NOMINAL_EXPERTS) -> List[int]:
    """Top-k by count desc, tie-breaking by expert ID ascending (deterministic)."""
    items = [(counts.get(i, 0), -i, i) for i in range(nominal)]  # -i not needed; sort key below
    items.sort(key=lambda t: (-t[0], t[2]))
    return [i for _, __, i in items[:k]]


def jaccard_distance(a: Sequence[int], b: Sequence[int]) -> float:
    sa, sb = set(a), set(b)
    inter = len(sa & sb)
    union = len(sa | sb)
    if union == 0:
        return 0.0
    return 1.0 - (inter / union)


def distribution_from_selections(selections: Sequence[Tuple[int, ...]], nominal: int = NOMINAL_EXPERTS
                                ) -> Tuple[List[float], Dict[int, int]]:
    counts = counts_from_selections(selections)
    p = normalize_distribution(counts, nominal=nominal)
    return p, counts


def temporal_drift_for_layer(window_selections: Sequence[Sequence[Tuple[int, ...]]],
                             nominal: int = NOMINAL_EXPERTS) -> Dict[str, object]:
    """window_selections: list of 8 windows, each a list of selected-expert tuples."""
    dists = []
    entropies = []
    top8 = []
    top16 = []
    counts_list = []
    for sel_list in window_selections:
        p, c = distribution_from_selections(sel_list, nominal=nominal)
        if not is_valid_probability_vector(p):
            raise ValueError("invalid probability distribution in temporal window")
        dists.append(p)
        counts_list.append(c)
        entropies.append(normalized_entropy(p, nominal=nominal))
        top8.append(top_k_experts(c, 8, nominal=nominal))
        top16.append(top_k_experts(c, 16, nominal=nominal))

    adjacent_jsd = [jsd(dists[i], dists[i + 1]) for i in range(len(dists) - 1)]
    adj_j8 = [jaccard_distance(top8[i], top8[i + 1]) for i in range(len(top8) - 1)]
    adj_j16 = [jaccard_distance(top16[i], top16[i + 1]) for i in range(len(top16) - 1)]
    for v in adjacent_jsd + [jsd(dists[0], dists[-1])]:
        if not (0.0 <= v <= 1.0):
            raise ValueError(f"JSD out of [0,1]: {v}")
    return {
        "adjacent_window_jsd": adjacent_jsd,
        "median_adjacent_window_jsd": _median(adjacent_jsd),
        "max_adjacent_window_jsd": max(adjacent_jsd) if adjacent_jsd else None,
        "first_vs_last_jsd": jsd(dists[0], dists[-1]),
        "normalized_entropy_per_window": entropies,
        "entropy_range": (max(e for e in entropies if e is not None) - min(e for e in entropies if e is not None))
        if all(e is not None for e in entropies) else None,
        "top8_adjacent_jaccard_distance": adj_j8,
        "top16_adjacent_jaccard_distance": adj_j16,
        "median_top8_adjacent_jaccard_distance": _median(adj_j8),
        "median_top16_adjacent_jaccard_distance": _median(adj_j16),
        "window_distributions": dists,  # for internal use; not persisted raw by default
        "window_counts": counts_list,
    }


def _median(values: Sequence[float]) -> Optional[float]:
    if not values:
        return None
    s = sorted(values)
    n = len(s)
    m = n // 2
    if n % 2 == 1:
        return float(s[m])
    return (s[m - 1] + s[m]) / 2.0
