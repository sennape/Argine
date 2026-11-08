"""R2-05 sections 15-20: the paper-relevant claim set, split into
SUPPORTED NOW vs NOT YET SUPPORTED, plus the forbidden-claim guard
(sections 16/17/18/20) mirroring R2-04's own naming safeguard.

R2-05 is descriptive characterization. It must never claim:
  - that hot experts are temporally stable (section 16 -- that is R2-08's
    job, and requires an explicitly predeclared temporal analysis R2-05
    does not perform);
  - a Zipfian/power-law/heavy-tailed distribution shape without
    quantitative evidence for that specific claim (section 20 -- R2-05
    computes descriptive concentration metrics only, never a distributional
    fit test, so this phrase is always forbidden here);
  - that frequency concentration explains the R2-04 Oracle gap by itself
    (section 17), or that frequency alone is sufficient for retention
    (section 18 -- R2-02's own evidence, LFU losing to Global-LRU at
    4 GiB, is the reason this is explicitly NOT a valid inference from
    R2-05 alone).
"""

from __future__ import annotations

from typing import Any, Dict, List

FORBIDDEN_PHRASES = (
    "zipfian", "power-law", "power law", "heavy-tailed", "heavy tailed",
    "temporally stable", "remain hot over time", "frequency solves retention",
    "explains the oracle gap", "frequency alone is sufficient",
)


class ForbiddenClaimError(ValueError):
    """Raised when a generated claim/text violates R2-05's own naming
    safeguard (sections 16/17/18/20)."""


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
    """Scans only the claims this experiment actually ASSERTS
    (`supported_now`) for a forbidden phrase -- mirroring R2-04's own
    guard. `not_yet_supported` entries and the LFU-bridge note
    deliberately NAME the forbidden phrases in order to disclaim them
    (e.g. 'these experts remain hot over time' is listed as a claim R2-05
    must NOT make); scanning those fields would make the disclaimer
    itself trip the guard it exists to support."""
    hits: List[str] = []
    for item in payload.get("supported_now", []):
        _scan_for_forbidden_phrases(item, "payload.supported_now[]", hits)
    if hits:
        raise ForbiddenClaimError("; ".join(hits))


def build_claims(global_summary: Dict[str, Any], heterogeneity: Dict[str, Any],
                 lfu_bridge_note: str) -> Dict[str, Any]:
    supported_now = [
        {
            "claim": f"On the tested trace, the median per-layer normalized routing "
                     f"entropy is {global_summary['layer_median']['normalized_entropy']}.",
            "basis": "global_summary.layer_median.normalized_entropy",
        },
        {
            "claim": f"The most concentrated layer (lowest normalized entropy) is layer "
                     f"{global_summary['most_concentrated_layer']}; the least concentrated "
                     f"is layer {global_summary['least_concentrated_layer']}.",
            "basis": "global_summary.most_concentrated_layer / least_concentrated_layer",
        },
        {
            "claim": f"Layer-to-layer concentration heterogeneity is classified as "
                     f"{heterogeneity['classification']} (descriptive spread only; no "
                     f"causal explanation, no temporal/workload-drift claim).",
            "basis": "layer_heterogeneity.classification",
        },
        {
            "claim": "These experts carry the largest aggregate request share on THIS "
                     "trace (a full-trace frequency statement, not a temporal-stability "
                     "claim).",
            "basis": "frequency.layer_frequency_rows top-ranked experts",
        },
    ]
    not_yet_supported = [
        {"claim": "These hot experts remain hot over time.",
         "reason": "requires an explicitly predeclared temporal analysis; assigned to R2-08."},
        {"claim": "Frequency concentration explains the R2-04 Oracle gap.",
         "reason": "frequency alone may be insufficient; " + lfu_bridge_note},
        {"claim": "The observed distribution is Zipfian / power-law / heavy-tailed.",
         "reason": "R2-05 computes descriptive concentration metrics only, never a "
                  "distributional fit test; no such claim is made without it."},
        {"claim": "Routing can be predicted from this frequency structure.",
         "reason": "predictability is II-C's question (R2-09..R2-12), not R2-05's."},
    ]
    claims = {"supported_now": supported_now, "not_yet_supported": not_yet_supported,
             "lfu_bridge": lfu_bridge_note}
    check_no_forbidden_claims(claims)
    return claims


LFU_BRIDGE_NOTE = (
    "R2-02's own evidence showed LFU performing WORSE than Global-LRU at the 4 GiB "
    "budget (see paper/research2 Table 2). This is direct evidence that simple global "
    "request-frequency information is not, by itself, sufficient to drive a good "
    "retention policy. Therefore, even if R2-05 finds strong frequency concentration, "
    "the correct interpretation is NOT 'frequency solves retention' but rather: "
    "aggregate frequency structure may exist in the routing stream, while frequency "
    "alone does not capture the TIMING information the Oracle exploits. This motivates "
    "R2-06 (transition structure) and R2-07 (reuse distance), not a frequency-only "
    "retention policy."
)
