"""R2-07 claims and the forbidden-claim guard, mirroring R2-05's/R2-06's
claims.py exactly in structure (scan ONLY `supported_now`; never the
`not_yet_supported` disclaimers, which deliberately NAME forbidden
phrases in order to disclaim them).

R2-07 is descriptive temporal-locality characterization, bound to the
single validated canonical trace. It must never claim:
  - any predictor accuracy/precision/recall/F1/AUC (section 25 -- R2-07
    trains no predictor; predictability is II-C's question);
  - a retention-policy performance improvement (section 18 -- "shorter
    reuse gaps are structurally favorable to retention" is allowed;
    "this policy would reduce misses by X%" is forbidden -- actual
    retention improvement belongs to II-C);
  - temporal/workload drift or beginning/middle/end stability findings
    (section 23 -- that is R2-08's job, not measured here at all);
  - a cross-workload or cross-model generalization claim (section 24 --
    R2-07 remains bound to "the validated canonical trace", never "MoE
    models generally..." or "Qwen workloads generally...");
  - a claim that R2-06 already proved long-range locality (section 16
    -- R2-07 is what tests that, for the first time).
"""

from __future__ import annotations

from typing import Any, Dict, List

FORBIDDEN_PHRASES = (
    "prediction accuracy", "predict with", "accuracy of", "precision of",
    "recall of", "f1 score", "f1-score", "auc of", "reduce misses",
    "reduces misses", "retention policy would", "this policy would",
    "temporal drift", "windowed drift", "workload drift", "moe models generally",
    "qwen workloads generally", "models generally exhibit", "workloads generally",
    "r2-06 proves", "r2-06 already proved", "proves long-range locality",
    "we can predict", "predicts the next expert",
)


class ForbiddenClaimError(ValueError):
    """Raised when a generated claim/text violates R2-07's own naming
    safeguard."""


def _scan_for_forbidden_phrases(value: Any, path: str, hits: List[str]) -> None:
    if isinstance(value, str):
        low = value.lower()
        for phrase in FORBIDDEN_PHRASES:
            if phrase in low:
                hits.append(f"{path}: contains forbidden phrase {phrase!r}")
    elif isinstance(value, dict):
        for k, v in value.items():
            _scan_for_forbidden_phrases(v, f"{path}.{k}", hits)
    elif isinstance(value, (list, tuple)):
        for i, v in enumerate(value):
            _scan_for_forbidden_phrases(v, f"{path}[{i}]", hits)


def check_no_forbidden_claims(payload: Dict[str, Any]) -> None:
    """Scans only `payload['supported_now']` -- mirrors R2-05's/R2-06's
    own guard exactly, for the same reason: `not_yet_supported` entries
    deliberately name the forbidden phrases in order to disclaim them."""
    hits: List[str] = []
    for item in payload.get("supported_now", []):
        _scan_for_forbidden_phrases(item, "payload.supported_now[]", hits)
    if hits:
        raise ForbiddenClaimError("; ".join(hits))


R206_BRIDGE_NOTE = (
    "R2-06 identified immediate (one-step) conditional routing structure. "
    "R2-07 measures the temporal horizon and working-set consequences of "
    "that structure -- R2-06's finding does not by itself establish long-"
    "range locality; R2-07 is what tests that."
)

R205_BRIDGE_NOTE = (
    "R2-05 showed frequency concentration. Being frequent is a logically "
    "distinct property from being reused soon; R2-07 computes a secondary, "
    "descriptive Spearman correlation between request frequency and median "
    "token reuse gap to characterize, not causally explain, this "
    "distinction."
)


def build_claims(global_summary: Dict[str, Any], bridge_correlation: Dict[str, Any]
                 ) -> Dict[str, Any]:
    supported_now = [
        {
            "claim": f"On the validated canonical trace, the median per-layer immediate "
                     f"(gap=1) reuse fraction is "
                     f"{global_summary['median_immediate_reuse_fraction']}.",
            "basis": "global_summary.median_immediate_reuse_fraction",
        },
        {
            "claim": f"On the validated canonical trace, the median per-layer token "
                     f"reuse gap is {global_summary['median_token_reuse_gap']}, and the "
                     f"median per-layer distinct-intervening expert reuse distance is "
                     f"{global_summary['median_distinct_intervening_reuse_distance']}.",
            "basis": "global_summary.median_token_reuse_gap / "
                    "median_distinct_intervening_reuse_distance",
        },
        {
            "claim": f"On the validated canonical trace, the median working-set size "
                     f"at window 32 is "
                     f"{global_summary['median_working_set_size_by_window'].get(32)}, "
                     f"out of the nominal expert population per layer.",
            "basis": "global_summary.median_working_set_size_by_window[32]",
        },
        {
            "claim": "On the validated canonical trace, shorter reuse gaps are "
                     "structurally favorable to retention (a descriptive, non-"
                     "performance statement; no retention-policy improvement is "
                     "claimed).",
            "basis": "section 18's allowed interpretation",
        },
        {
            "claim": "On the validated canonical trace, layer-local reuse locality "
                     "(section 16's bridge) extends the one-step conditional "
                     "structure R2-06 identified to a measurable multi-step horizon, "
                     "and the active working set grows as the observation window "
                     "expands at the rate this experiment's growth curve describes.",
            "basis": "global_summary.heterogeneity / working-set growth curve",
        },
    ]
    if bridge_correlation.get("spearman_correlation") is not None:
        supported_now.append({
            "claim": f"On the validated canonical trace, the descriptive Spearman "
                     f"rank correlation between per-expert request frequency (R2-05) "
                     f"and median token reuse gap (R2-07) is "
                     f"{bridge_correlation['spearman_correlation']} (secondary; no "
                     f"causal interpretation).",
            "basis": "bridge.frequency_vs_reuse_gap_correlation.spearman_correlation",
        })
    not_yet_supported = [
        {"claim": "This reuse structure lets us predict the next expert with X% "
                 "accuracy.",
         "reason": "R2-07 trains no predictor and reports no accuracy/precision/"
                  "recall/F1/AUC; predictability is II-C's question."},
        {"claim": "A retention policy using these reuse-distance signals would "
                 "reduce misses by X%.",
         "reason": "actual retention improvement is II-C's job; R2-07 reports only "
                  "the descriptive structural observation that shorter reuse gaps "
                  "are structurally favorable to retention."},
        {"claim": "Reuse locality changes over time / across workload phases.",
         "reason": "temporal/windowed drift analysis is R2-08's job; not computed "
                  "here (section 23)."},
        {"claim": "MoE models generally / Qwen workloads generally exhibit this "
                 "reuse structure.",
         "reason": "R2-07 remains bound to the single validated canonical trace; "
                  "cross-workload/cross-model generalization is explicitly deferred "
                  "to R2-08 (section 24)."},
        {"claim": "R2-06 already proved long-range locality.",
         "reason": "R2-06 established only one-step conditional structure; R2-07 is "
                  "what tests whether that extends beyond one step (section 16)."},
    ]
    claims = {"supported_now": supported_now, "not_yet_supported": not_yet_supported,
             "r2_06_bridge": R206_BRIDGE_NOTE, "r2_05_bridge": R205_BRIDGE_NOTE}
    check_no_forbidden_claims(claims)
    return claims
