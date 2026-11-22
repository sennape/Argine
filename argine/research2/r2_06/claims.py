"""R2-06 claims and the forbidden-claim guard, mirroring R2-05's
claims.py exactly in structure (scan ONLY `supported_now`; never the
`not_yet_supported` disclaimers, which deliberately NAME forbidden
phrases in order to disclaim them).

R2-06 is descriptive transition-structure characterization. It must
never claim:
  - any predictor accuracy/precision/recall/F1 -- R2-06 trains no
    predictor; mutual information/NMI measure association, not a
    predictor's held-out performance (that is II-C's question);
  - reuse-distance or temporal/windowed-drift findings -- those are
    R2-07's and R2-08's jobs respectively, not measured here at all;
  - a cross-layer transition claim as primary evidence -- R2-06's
    primary analysis is SAME-LAYER only;
  - dominance of self-transitions assumed rather than measured.
"""

from __future__ import annotations

from typing import Any, Dict, List

FORBIDDEN_PHRASES = (
    "prediction accuracy", "predict with", "accuracy of", "precision of",
    "recall of", "f1 score", "f1-score", "reuse distance", "temporal drift",
    "windowed drift", "cross-layer transition explains", "self-transitions dominate",
    "we can predict", "predicts the next expert",
)


class ForbiddenClaimError(ValueError):
    """Raised when a generated claim/text violates R2-06's own naming
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
    """Scans only `payload['supported_now']` -- mirrors R2-05's own
    guard exactly, for the same reason: `not_yet_supported` entries
    deliberately name the forbidden phrases in order to disclaim them,
    and scanning those fields would make the disclaimer itself trip the
    guard it exists to support."""
    hits: List[str] = []
    for item in payload.get("supported_now", []):
        _scan_for_forbidden_phrases(item, "payload.supported_now[]", hits)
    if hits:
        raise ForbiddenClaimError("; ".join(hits))


R205_TRANSITION_BRIDGE_NOTE = (
    "R2-05 found descriptive frequency concentration in layer-local expert "
    "routing but explicitly declined to claim that frequency alone explains "
    "retention behaviour (R2-02's own evidence: LFU lost to Global-LRU at 4 "
    "GiB). R2-06 asks a DIFFERENT, complementary question: whether the ORDER "
    "of routing decisions carries information beyond that static frequency "
    "structure. A positive R2-06 finding (NMI > 0 for some layers) does not "
    "retroactively validate a frequency-only policy, and does not by itself "
    "establish that this transition structure is exploitable by any real "
    "policy -- that is a predictive-modelling question assigned to II-C, not "
    "measured here."
)


def build_claims(global_summary: Dict[str, Any],
                 layer_self_transitions: Dict[str, Any]) -> Dict[str, Any]:
    supported_now = [
        {
            "claim": f"On the tested trace, the median per-layer same-layer normalized "
                     f"mutual information between consecutive token-to-token expert "
                     f"selections is {global_summary['median_nmi']}.",
            "basis": "global_summary.median_nmi",
        },
        {
            "claim": f"The strongest same-layer transition structure (highest NMI) is "
                     f"observed at layer {global_summary['strongest_layer_by_nmi']}; the "
                     f"weakest is at layer {global_summary['weakest_layer_by_nmi']}.",
            "basis": "global_summary.strongest_layer_by_nmi / weakest_layer_by_nmi",
        },
        {
            "claim": f"The fraction of layers with strictly positive NMI (i.e. the "
                     f"source expert carries SOME measurable information about the "
                     f"next same-layer target beyond marginal frequency) is "
                     f"{global_summary['fraction_layers_with_nmi_positive']}.",
            "basis": "global_summary.fraction_layers_with_nmi_positive",
        },
        {
            "claim": f"The median per-layer self-transition lift (P(i->i) relative to "
                     f"expert i's own marginal probability) is "
                     f"{global_summary['median_self_transition_lift']}, measured "
                     f"directly rather than assumed.",
            "basis": "global_summary.median_self_transition_lift",
        },
        {
            "claim": "Previous same-layer expert selection state carries measurable "
                     "information about subsequent same-layer routing state beyond "
                     "marginal frequency, on this trace, for layers with NMI > 0.",
            "basis": "layer_information_metrics.normalized_mutual_information",
        },
    ]
    not_yet_supported = [
        {"claim": "This transition structure lets us predict the next expert with X% "
                 "accuracy.",
         "reason": "R2-06 trains no predictor and reports no accuracy/precision/recall/"
                  "F1; predictability is II-C's question, not this experiment's."},
        {"claim": "This transition structure reduces reuse distance.",
         "reason": "reuse-distance measurement is R2-07's job; not computed here."},
        {"claim": "This transition structure is stable over time / across workload "
                 "segments.",
         "reason": "temporal/windowed drift analysis is R2-08's job; not computed here."},
        {"claim": "Cross-layer transitions explain the observed same-layer structure.",
         "reason": "R2-06's primary analysis is SAME-LAYER only; layer-to-layer order "
                  "is architectural, not a measured routing transition, and no cross-"
                  "layer claim is made from this experiment's primary evidence."},
        {"claim": "Self-transitions dominate the routing process.",
         "reason": "self-transition mass/lift is measured per layer, not assumed; "
                  "dominance is reported only where the measured share/lift actually "
                  "shows it."},
    ]
    claims = {"supported_now": supported_now, "not_yet_supported": not_yet_supported,
             "r2_05_transition_bridge": R205_TRANSITION_BRIDGE_NOTE}
    check_no_forbidden_claims(claims)
    return claims
