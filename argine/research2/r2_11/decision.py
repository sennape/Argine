"""R2-11 frozen decision-rule classifier (decision_rules.json.primary_decision_rule).

Phase-0 only: pure classification logic over already-computed summary
statistics. Does not compute those statistics itself and is NOT invoked
against real R2-11 data in this phase -- unit-tested only on synthetic
inputs.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Optional


@dataclass(frozen=True)
class R2_11_DecisionInputs:
    mean_delta_4gib: float
    bootstrap_ci_upper_4gib: float
    n_workloads_delta_le_0_at_4gib: int
    n_workloads_total: int
    dominant_workload_fraction: Optional[float]  # max single-workload share of total positive reduction among improving workloads, or None if no improving workload
    mean_delta_1_5gib: float
    mean_delta_8gib: float
    budget_violations: int
    causal_leakage_failures: int
    determinism_checks_passed: bool
    scientific_validity_failure: bool  # e.g. rejected/incomplete trace that could not be resolved


def classify(inputs: R2_11_DecisionInputs) -> str:
    """Return one of R2_11_PASS / R2_11_INCONCLUSIVE / R2_11_FAIL / R2_11_BLOCKED_INVALID,
    applying decision_rules.json's primary_decision_rule literally."""

    if inputs.scientific_validity_failure:
        return "R2_11_BLOCKED_INVALID"

    if inputs.mean_delta_4gib >= 0:
        return "R2_11_FAIL"

    pass_conditions = [
        inputs.mean_delta_4gib < 0,
        inputs.bootstrap_ci_upper_4gib < 0,
        inputs.n_workloads_delta_le_0_at_4gib >= 9 and inputs.n_workloads_total == 12,
        inputs.dominant_workload_fraction is None or inputs.dominant_workload_fraction <= 0.5,
        inputs.mean_delta_1_5gib <= 0,
        inputs.mean_delta_8gib <= 0,
        inputs.budget_violations == 0,
        inputs.causal_leakage_failures == 0,
        inputs.determinism_checks_passed,
    ]

    if all(pass_conditions):
        return "R2_11_PASS"

    return "R2_11_INCONCLUSIVE"


def r2_12_gate(classification: str) -> str:
    if classification == "R2_11_PASS":
        return "R2-12 MAY BE PROPOSED FOR PROSPECTIVE RUNTIME VALIDATION."
    if classification == "R2_11_INCONCLUSIVE":
        return "R2-12 is NOT automatically justified. Close R2-11 as inconclusive unless a separately designed prospective replication is later authorized."
    if classification == "R2_11_FAIL":
        return "R2-12 is NOT proposed from this evidence."
    return "R2-11 is BLOCKED/INVALID; no R2-12 gate decision can be made until resolved."
