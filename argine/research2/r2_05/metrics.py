"""R2-05 section 8/10/11/12: per-layer concentration metrics, the uniform-
routing baseline, concentration ratios, and the Pareto-style concentration
counts.

N (the "relevant expert population") is, throughout this module, the
NOMINAL expert count when authoritative model metadata provides one
(trace.meta.n_experts), per section 8's explicit preference. When no
authoritative nominal count exists, callers fall back to the OBSERVED
count for that layer and must flag that explicitly (see
`N_IS_NOMINAL`/`N_IS_OBSERVED` below) -- normalized metrics computed
against an observed-only N are not comparable across layers with
different observed counts, and R2-05's own outcome classification
(orchestrator.py) treats a missing nominal count as a reason for
INCONCLUSIVE rather than silently proceeding.
"""

from __future__ import annotations

import math
from typing import Any, Dict, List, Optional

N_IS_NOMINAL = "nominal_expert_count"
N_IS_OBSERVED = "observed_expert_count_fallback"

# Predeclared fixed top-k values (section 8G), chosen before any real
# execution and before inspecting results, based on the documented real
# nominal expert count (128 experts/layer for the validated Qwen3-30B-A3B
# trace). Filtering a candidate k down to only those <= N for a given
# layer is a structural necessity (you cannot ask for the top-200 of 6
# experts), not a results-dependent choice.
CANDIDATE_TOP_K = (1, 4, 8, 16, 32)

# Predeclared Pareto-style concentration thresholds (section 12).
CONCENTRATION_PCT_THRESHOLDS = (0.50, 0.80, 0.90, 0.95)


def shannon_entropy(probabilities: List[float]) -> float:
    """H = -sum p_i log(p_i), natural log, 0 log 0 := 0."""
    return -sum(p * math.log(p) for p in probabilities if p > 0.0)


def top_k_values_for_n(n: int) -> List[int]:
    return [k for k in CANDIDATE_TOP_K if k <= n]


def gini_coefficient(sorted_ascending_shares: List[float]) -> float:
    """Standard discrete Gini over a population of N "individuals" (one
    per nominal expert slot, zero-padded for never-observed experts, so
    those slots correctly contribute to inequality rather than being
    silently excluded). shares must already be sorted ascending and sum
    to 1.0 (or 0.0 for a population with no requests at all, handled by
    the caller before this is reached)."""
    n = len(sorted_ascending_shares)
    if n == 0:
        return 0.0
    total = sum(sorted_ascending_shares)
    if total == 0.0:
        return 0.0
    weighted = sum((i + 1) * s for i, s in enumerate(sorted_ascending_shares))
    return (2.0 * weighted) / (n * total) - (n + 1.0) / n


def layer_concentration_metrics(layer: int, counts: Dict[int, int], *,
                                nominal_expert_count: Optional[int]) -> Dict[str, Any]:
    """All of section 8's metrics for one layer, plus the uniform baseline
    (section 10) and concentration ratios (section 11) computed against
    the SAME N used for this layer's normalized metrics."""
    total = sum(counts.values())
    observed_count = len(counts)

    if nominal_expert_count is not None and nominal_expert_count > 0:
        n = nominal_expert_count
        n_basis = N_IS_NOMINAL
    else:
        n = observed_count
        n_basis = N_IS_OBSERVED

    if total == 0 or n == 0:
        # Zero-request layer (section 19): a valid, non-error state. Every
        # normalized metric is reported as None rather than a divide-by-zero
        # or a fabricated number.
        return {
            "layer_id": layer, "total_requests": total, "observed_expert_count": observed_count,
            "nominal_expert_count": nominal_expert_count, "n_used": n, "n_basis": n_basis,
            "shannon_entropy": None, "normalized_entropy": None, "effective_expert_count": None,
            "effective_expert_fraction": None, "hhi": None, "gini": None,
            "max_expert_share": None, "top_k_share": {}, "uniform_baseline": _uniform_baseline(n),
            "concentration_ratios": {}, "experts_for_pct": {k: None for k in
                                                            CONCENTRATION_PCT_THRESHOLDS},
        }

    shares_by_expert = {e: c / total for e, c in counts.items()}
    probs = list(shares_by_expert.values())
    h = shannon_entropy(probs)
    h_norm = (h / math.log(n)) if n > 1 else (1.0 if h == 0.0 else None)
    n_eff = math.exp(h)
    hhi = sum(p * p for p in probs)
    max_share = max(probs)

    # Gini over the full nominal population (zero-padded for unobserved
    # experts) when N is nominal; over the observed-only population
    # otherwise (there is no authoritative slot count to zero-pad to).
    if n_basis == N_IS_NOMINAL:
        padded = sorted(probs + [0.0] * max(0, n - observed_count))
    else:
        padded = sorted(probs)
    gini = gini_coefficient(padded)

    ordered_desc = sorted(probs, reverse=True)
    top_k = top_k_values_for_n(n)
    top_k_share = {k: sum(ordered_desc[:k]) for k in top_k}

    uniform = _uniform_baseline(n)
    concentration_ratios = {
        f"top{k}_ratio_to_uniform": (top_k_share[k] / uniform["top_k_share"][k])
        for k in top_k
    }
    concentration_ratios["effective_expert_fraction"] = n_eff / n

    experts_for_pct = {}
    cumulative = 0.0
    needed = {pct: None for pct in CONCENTRATION_PCT_THRESHOLDS}
    for i, p in enumerate(ordered_desc, start=1):
        cumulative += p
        for pct in CONCENTRATION_PCT_THRESHOLDS:
            if needed[pct] is None and cumulative >= pct - 1e-12:
                needed[pct] = i
    experts_for_pct = needed

    return {
        "layer_id": layer, "total_requests": total, "observed_expert_count": observed_count,
        "nominal_expert_count": nominal_expert_count, "n_used": n, "n_basis": n_basis,
        "shannon_entropy": h, "normalized_entropy": h_norm, "effective_expert_count": n_eff,
        "effective_expert_fraction": n_eff / n, "hhi": hhi, "gini": gini,
        "max_expert_share": max_share, "top_k_share": top_k_share, "uniform_baseline": uniform,
        "concentration_ratios": concentration_ratios, "experts_for_pct": experts_for_pct,
    }


def _uniform_baseline(n: int) -> Dict[str, Any]:
    """Section 10's theoretical uniform-routing reference values for N
    nominal experts."""
    if n <= 0:
        return {"n": n, "normalized_entropy": None, "effective_expert_count": None,
               "hhi": None, "gini": 0.0, "top_k_share": {}}
    top_k = top_k_values_for_n(n)
    return {
        "n": n,
        "normalized_entropy": 1.0,
        "effective_expert_count": float(n),
        "hhi": 1.0 / n,
        "gini": 0.0,
        "top_k_share": {k: k / n for k in top_k},
    }
