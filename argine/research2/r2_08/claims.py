"""R2-08 claims and forbidden-claim guard (section 39)."""

from __future__ import annotations

from typing import Any, Dict, List

FORBIDDEN_PHRASES = (
    "recognizes workload identity", "model recognizes workload",
    "experts specialize semantically", "semantic specialization for these workloads",
    "this improves cache performance", "improves cache performance",
    "faster inference", "lower latency", "higher throughput",
    "proves all moe models", "moe models generally",
    "prediction accuracy", "predictor accuracy", "prefetch", "oracle-headroom recovery",
    "we trained", "train a model", "heuristic predictor",
)


class ForbiddenClaimError(ValueError):
    pass


def _scan(value: Any, path: str, hits: List[str]) -> None:
    if isinstance(value, str):
        low = value.lower()
        for phrase in FORBIDDEN_PHRASES:
            if phrase in low:
                hits.append(f"{path}: contains forbidden phrase {phrase!r}")
    elif isinstance(value, dict):
        for k, v in value.items():
            _scan(v, f"{path}.{k}", hits)
    elif isinstance(value, (list, tuple)):
        for i, v in enumerate(value):
            _scan(v, f"{path}[{i}]", hits)


def check_no_forbidden_claims(payload: Dict[str, Any]) -> None:
    hits: List[str] = []
    for item in payload.get("supported_now", []):
        _scan(item, "payload.supported_now[]", hits)
    # scan top-level summary strings that might leak claims, but not disclaimers
    for k in ("headline_claims",):
        if k in payload:
            _scan(payload[k], f"payload.{k}", hits)
    if hits:
        raise ForbiddenClaimError("; ".join(hits))


def build_claims(regime: Dict[str, Any], comparison: Dict[str, Any],
                 workload_metrics: Dict[str, Any]) -> Dict[str, Any]:
    supported = []
    t = regime.get("median_temporal_jsd")
    r = regime.get("median_replicate_jsd")
    c = regime.get("median_cross_workload_jsd")
    if t is not None:
        supported.append({
            "claim": f"Routing distributions exhibit measurable temporal drift under the tested protocol (median adjacent-window JSD={t}).",
            "basis": "temporal_drift.median_temporal_jsd",
        })
    if c is not None and r is not None:
        rel = "exceed" if c > r else "do not exceed" if c < r else "equal"
        supported.append({
            "claim": f"Cross-workload routing differences {rel} same-workload replicate variability under the tested protocol (median cross-workload JSD={c}, median replicate JSD={r}).",
            "basis": "three-scale comparison",
        })
    # reuse locality across workload families - check bridge metrics if present
    has_reuse = any(((workload_metrics.get(wid) or {}).get("bridges", {}).get("r2_07", {})
                     .get("immediate_reuse_median") is not None) for wid in workload_metrics)
    if has_reuse:
        supported.append({
            "claim": "Short-horizon reuse locality is present across the tested workload families under the tested protocol (R2-07-compatible bridge metrics).",
            "basis": "workload_metrics.*.bridges.r2_07",
        })
    supported.append({
        "claim": f"Regime classification under predeclared thresholds is {regime.get('regime')} ({regime.get('reason')}).",
        "basis": "summary.classify_regime",
    })
    not_yet = [
        {"claim": "The model recognizes workload identity.",
         "reason": "forbidden; R2-08 measures routing structure, not model cognition."},
        {"claim": "Experts specialize semantically for these workloads.",
         "reason": "forbidden unless independently established elsewhere."},
        {"claim": "This explains the Oracle advantage.",
         "reason": "forbidden unless independently established elsewhere."},
        {"claim": "This improves cache performance / faster inference / lower latency / higher throughput.",
         "reason": "R2-08 is not a runtime performance benchmark."},
        {"claim": "This proves all MoE models behave this way.",
         "reason": "scope limited to tested Qwen model/workloads/consumer-machine protocol."},
    ]
    payload = {"supported_now": supported, "not_yet_supported": not_yet,
               "scope": "Qwen3-30B-A3B-Q4_K_M, tested workload families, tested consumer-machine protocol only."}
    check_no_forbidden_claims(payload)
    return payload
