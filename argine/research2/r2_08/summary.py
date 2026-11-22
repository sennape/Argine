"""R2-08 three-scale comparison and predeclared regime classification."""

from __future__ import annotations

from typing import Any, Dict, List, Optional, Sequence, Tuple

# Predeclared BEFORE real execution (section 26). Conservative.
REGIME_DOMINANCE_RATIO = 1.5
STABLE_LOW_DRIFT_JSD = 0.05  # predeclared: all three median JSDs below this => STABLE_ACROSS_WORKLOADS

REGIME_STABLE = "STABLE_ACROSS_WORKLOADS"
REGIME_TEMPORAL = "TEMPORAL_DRIFT_DOMINANT"
REGIME_WORKLOAD = "WORKLOAD_DEPENDENCE_DOMINANT"
REGIME_REPLICATE = "RUN_VARIABILITY_DOMINANT"
REGIME_MIXED = "MIXED"


def safe_ratio(num: Optional[float], den: Optional[float]) -> Optional[float]:
    if num is None or den is None or den == 0:
        return None
    return num / den


def classify_regime(median_temporal_jsd: Optional[float],
                    median_replicate_jsd: Optional[float],
                    median_cross_workload_jsd: Optional[float]) -> Dict[str, Any]:
    vals = {
        "median_temporal_jsd": median_temporal_jsd,
        "median_replicate_jsd": median_replicate_jsd,
        "median_cross_workload_jsd": median_cross_workload_jsd,
    }
    if any(v is None for v in vals.values()):
        return {"regime": REGIME_MIXED, "reason": "missing median JSD values", **vals,
                "thresholds": thresholds_json()}
    t, r, c = median_temporal_jsd, median_replicate_jsd, median_cross_workload_jsd
    ratios = {
        "cross_workload_to_replicate_ratio": safe_ratio(c, r),
        "cross_workload_to_temporal_ratio": safe_ratio(c, t),
        "temporal_to_replicate_ratio": safe_ratio(t, r),
    }
    dom = REGIME_DOMINANCE_RATIO
    low = STABLE_LOW_DRIFT_JSD
    if t < low and r < low and c < low:
        regime = REGIME_STABLE
        reason = f"all median JSDs < {low}"
    elif c >= dom * r and c >= dom * t:
        regime = REGIME_WORKLOAD
        reason = f"median_cross_workload_jsd >= {dom} * replicate and temporal"
    elif t >= dom * c and t >= dom * r:
        regime = REGIME_TEMPORAL
        reason = f"median_temporal_jsd >= {dom} * cross_workload and replicate"
    elif r >= dom * c and r >= dom * t:
        regime = REGIME_REPLICATE
        reason = f"median_replicate_jsd >= {dom} * cross_workload and temporal"
    else:
        regime = REGIME_MIXED
        reason = "no scale dominates by predeclared ratio and not all below stable threshold"
    return {"regime": regime, "reason": reason, **vals, **ratios,
            "thresholds": thresholds_json()}


def thresholds_json() -> Dict[str, Any]:
    return {
        "dominance_ratio": REGIME_DOMINANCE_RATIO,
        "stable_low_drift_jsd": STABLE_LOW_DRIFT_JSD,
        "predeclared_before_real_execution": True,
        "labels": [REGIME_STABLE, REGIME_TEMPORAL, REGIME_WORKLOAD, REGIME_REPLICATE, REGIME_MIXED],
    }


def _median(values: Sequence[float]) -> Optional[float]:
    if not values:
        return None
    s = sorted(values)
    n = len(s)
    m = n // 2
    return float(s[m]) if n % 2 == 1 else (s[m - 1] + s[m]) / 2.0


def layer_heterogeneity(layer_temporal_medians: Dict[int, float],
                        layer_cross_workload_medians: Dict[int, float]) -> Dict[str, Any]:
    def _pick(d: Dict[int, float], smallest: bool) -> Optional[int]:
        if not d:
            return None
        items = sorted(d.items(), key=lambda kv: (kv[1], kv[0]))
        return items[0][0] if smallest else items[-1][0]
    return {
        "most_temporally_stable_layer": _pick(layer_temporal_medians, True),
        "least_temporally_stable_layer": _pick(layer_temporal_medians, False),
        "smallest_cross_workload_jsd_layer": _pick(layer_cross_workload_medians, True),
        "largest_cross_workload_jsd_layer": _pick(layer_cross_workload_medians, False),
        "primary_criterion": "median relevant JSD, tie-breaking by smallest layer id",
    }
