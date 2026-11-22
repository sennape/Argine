"""R2-04 sections 5/17/20/24: paper-ready claims, explicitly separated
into SUPPORTED NOW (strictly backed by this experiment's own evidence)
and NOT YET SUPPORTED (statements this experiment must never make, listed
so the next paper-freeze step cannot accidentally reach for them). Also
documents (never computes) the II-C future metric, section 24.

Naming safeguard (section 5): every SUPPORTED NOW claim about the
POLICY-INFORMATION HEADROOM uses 'upper bound' / 'headroom' / 'potentially
recoverable information headroom' phrasing, never 'can be recovered' or
a bare performance-improvement percentage.
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional

from . import decomposition as decomp
from ..r2_03 import budget_sweep

FUTURE_METRIC_II_C = {
    "name": "fraction_of_oracle_headroom_recovered",
    "status": "NOT_YET_SUPPORTED -- no causal predictor exists yet (II-C, R2-09..R2-12)",
    "formula": ("fraction_of_oracle_headroom_recovered = "
               "(causal_baseline_miss_bytes - predictor_miss_bytes) / "
               "(causal_baseline_miss_bytes - oracle_miss_bytes)"),
    "formula_variables": {
        "causal_baseline_miss_bytes": "miss bytes of a declared causal baseline "
                                      "(e.g. Global-LRU) at a given budget",
        "predictor_miss_bytes": "miss bytes of a future causal working-set predictor "
                               "(II-C) at that same budget",
        "oracle_miss_bytes": "miss bytes of Oracle-Global-Belady at that same budget "
                            "(the denominator's anchor: the full informational upper "
                            "bound this experiment, R2-04, establishes)",
    },
    "defined_for": "a given budget, to be evaluated once a causal predictor exists",
    "purpose": "the clean scientific bridge from II-A's upper bound (this experiment) "
              "to II-C's demonstrated causal recovery -- documented now so II-C has "
              "an unambiguous, predeclared metric to report against, never computed "
              "here (no predictor exists yet).",
}


def build_claims(decomposition_by_budget: Dict[int, Dict[str, Any]],
                 reproduction_budget_bytes: Optional[int],
                 effective_residency_budget: Optional[int],
                 curve_shape: str) -> Dict[str, Any]:
    supported: List[Dict[str, Any]] = []
    not_yet: List[Dict[str, Any]] = []

    if reproduction_budget_bytes is not None and reproduction_budget_bytes in decomposition_by_budget:
        info = decomposition_by_budget[reproduction_budget_bytes][decomp.KEY_INFORMATION]
        label = budget_sweep.budget_label(reproduction_budget_bytes)
        if info["relative_miss_byte_reduction"] is not None:
            supported.append({
                "claim": (f"At the tested {label} budget, Oracle-Global-Belady "
                         f"establishes an upper-bound information headroom of "
                         f"{info['relative_miss_byte_reduction']:.4f} relative miss-"
                         f"byte reduction over Global-LRU."),
                "basis": f"policy_information_headroom at budget_bytes="
                        f"{reproduction_budget_bytes}",
            })

    for budget, decomposition in sorted(decomposition_by_budget.items()):
        info = decomposition[decomp.KEY_INFORMATION]
        if info["relative_miss_byte_reduction"] is None:
            continue
        label = budget_sweep.budget_label(budget)
        supported.append({
            "claim": (f"At the tested {label} budget, the Oracle-Global-Belady "
                     f"comparison establishes a potentially recoverable information "
                     f"headroom (upper bound) of "
                     f"{info['relative_miss_byte_reduction']:.4f} relative miss-byte "
                     f"reduction, and {info['miss_byte_reduction']} absolute miss-"
                     f"byte reduction, over Global-LRU."),
            "basis": f"policy_information_headroom at budget_bytes={budget}",
        })

    if effective_residency_budget is not None:
        label = budget_sweep.budget_label(effective_residency_budget)
        supported.append({
            "claim": (f"At the {label} budget (the first tested budget exhibiting "
                     f"policy convergence), Global-LRU and Oracle-Global-Belady "
                     f"produced identical miss counts, identical miss bytes, and "
                     f"zero evictions each; this is the observed effective-residency "
                     f"point for the tested sweep, not a claim about the "
                     f"mathematically minimal required capacity."),
            "basis": "effective_residency_budget (section 9)",
        })

    supported.append({
        "claim": (f"The observed relative information-headroom-fraction curve across "
                 f"the tested budget sweep is classified as: {curve_shape}."),
        "basis": "curve_shape classification (section 12)",
    })

    not_yet.extend([
        {"claim": "A causal predictor will recover X% of the Oracle headroom.",
         "reason": "no causal predictor exists yet; this is II-C's (R2-09..R2-12) job, "
                  "not R2-04's."},
        {"claim": "The Oracle gap itself is recoverable by a practical predictor.",
         "reason": "the Oracle comparison establishes an informational upper bound "
                  "only; II-B investigates whether routing contains exploitable "
                  "structure, and II-C determines what fraction of this upper bound "
                  "can be recovered causally (section 17)."},
        {"claim": "X% lower latency / X% faster inference / X% higher tokens/s / X% "
                 "lower SSD traffic measured in production / X% real runtime "
                 "improvement.",
         "reason": "R2-04 is an offline miss-cost analysis; miss bytes are a proxy for "
                  "expert data that would need to be serviced under the replay model, "
                  "never a production performance measurement (section 16)."},
        {"claim": "Causally recovered margin: any numeric percentage.",
         "reason": "CAUSALLY RECOVERED MARGIN is NOT YET MEASURED -- II-C "
                  "(section 4's terminology contract); never a fabricated figure."},
        {"claim": FUTURE_METRIC_II_C["formula"],
         "reason": FUTURE_METRIC_II_C["status"]},
    ])

    return {
        "supported_now": supported,
        "not_yet_supported": not_yet,
        "causal_recovery": "NOT_YET_MEASURED -- II-C",
        "future_metric_ii_c": FUTURE_METRIC_II_C,
    }


FORBIDDEN_SUPPORTED_CLAIM_PHRASES = (
    "can be recovered", "will recover", "will improve runtime", "faster inference",
    "lower latency", "tokens/s", "ssd traffic", "real runtime improvement",
    "causally recovered margin:",
)


class ForbiddenClaimError(ValueError):
    """Raised when a SUPPORTED NOW claim's text contains a phrase this
    experiment is forbidden from asserting (section 17/section 5's
    naming safeguard) -- an automated, defense-in-depth guard on top of
    claims.py's own hand-written, already-safe claim text."""


def check_no_forbidden_claims(claims: Dict[str, Any]) -> None:
    for item in claims.get("supported_now", []):
        lowered = item["claim"].lower()
        for phrase in FORBIDDEN_SUPPORTED_CLAIM_PHRASES:
            if phrase in lowered:
                raise ForbiddenClaimError(
                    f"a SUPPORTED NOW claim contains forbidden phrase {phrase!r}: "
                    f"{item['claim']!r}")
