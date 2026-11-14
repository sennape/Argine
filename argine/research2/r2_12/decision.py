"""R2-12 frozen decision-rule classifier (decision_rules.json.primary_decision_rule).

Phase-0 only: pure classification logic over already-computed summary
statistics from a (not-yet-run) real-runtime evaluation campaign. Does not
compute those statistics itself and is NOT invoked against any real R2-12
runtime data in this phase -- unit-tested only on synthetic/mock inputs,
exactly as R2-11's bootstrap.py/decision.py were in R2-11 Phase 0.

Unlike R2-11's byte-level retention metric (where a NEGATIVE delta favored
PredictedProtectPolicy), R2-12's primary metric is decode tokens/sec, where
a POSITIVE delta favors PredictedProtectPolicy. The sign convention is
therefore inverted relative to argine.research2.r2_11.decision.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Optional


@dataclass(frozen=True)
class R2_12_DecisionInputs:
    # Primary (4 GiB) throughput statistics.
    mean_delta_tps_4gib: float  # PredictedProtectPolicy_tps - B_selected_tps; positive = faster
    bootstrap_ci_lower_4gib: float  # 95% paired-bootstrap CI lower bound on mean_delta_tps_4gib
    n_workloads_delta_tps_ge_0_at_4gib: int
    n_workloads_total: int
    dominant_improving_workload_fraction: Optional[float]  # max single-workload share of total positive tps gain among improving workloads, or None if no improving workload

    # Scientific-validity / BLOCKED_INVALID gates (checked before FAIL/PASS logic).
    output_equivalence_failures: int
    hardware_state_valid: bool
    thermal_throttling_detected: bool
    instrumentation_overhead_bounded: bool
    budget_violations: int
    scientific_validity_failure: bool  # catch-all for any other validity failure not already enumerated above

    # FAIL-path signal.
    overhead_exceeds_measured_benefit: bool  # confirmed prediction/retention-policy CPU overhead nets out the throughput benefit

    # Remaining PASS-path requirements.
    ttft_regression_exceeds_tolerance: bool  # per the frozen ttft_latency_tolerance in decision_rules.json
    deterministic_config_preserved: bool


def classify(inputs: R2_12_DecisionInputs) -> str:
    """Return one of R2_12_PASS / R2_12_INCONCLUSIVE / R2_12_FAIL / R2_12_BLOCKED_INVALID,
    applying decision_rules.json's primary_decision_rule literally."""

    if (
        inputs.output_equivalence_failures > 0
        or not inputs.hardware_state_valid
        or inputs.thermal_throttling_detected
        or not inputs.instrumentation_overhead_bounded
        or inputs.budget_violations > 0
        or inputs.scientific_validity_failure
    ):
        return "R2_12_BLOCKED_INVALID"

    if inputs.mean_delta_tps_4gib <= 0 or inputs.overhead_exceeds_measured_benefit:
        return "R2_12_FAIL"

    pass_conditions = [
        inputs.mean_delta_tps_4gib > 0,
        inputs.bootstrap_ci_lower_4gib > 0,
        inputs.n_workloads_delta_tps_ge_0_at_4gib >= 3 and inputs.n_workloads_total == 4,
        inputs.dominant_improving_workload_fraction is None or inputs.dominant_improving_workload_fraction <= 0.5,
        not inputs.ttft_regression_exceeds_tolerance,
        inputs.deterministic_config_preserved,
    ]

    if all(pass_conditions):
        return "R2_12_PASS"

    return "R2_12_INCONCLUSIVE"


def research_3_gate(classification: str) -> str:
    """R2-12 defines no Research-3 gate (decision_rules.json.research_3_gate).
    This function exists only to make that explicit and testable; it is
    descriptive, not a scope authorization of any kind."""
    return (
        "R2-12's own classification is the terminal scientific output of this "
        "protocol. Starting Research 3 is out of scope and not addressed by "
        "this decision rule, regardless of classification="
        f"{classification}."
    )
