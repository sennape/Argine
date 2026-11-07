"""R2-02 sections 11/12/19/20: predeclared metrics, Oracle-gap computation,
and the sharding-decomposition treatment. Predeclared BEFORE any real
candidate analysis runs (see docs/research2/METHODOLOGY.md) -- this module
defines the metrics, it does not tune them to a result.
"""

from __future__ import annotations

from typing import Any, Dict, Optional

from ...research.replay import ReplayResult
from .replay_multi import DeterministicReplayResult
from . import policy_set


def policy_row(label: str, det: DeterministicReplayResult, rr: ReplayResult) -> Dict[str, Any]:
    """Section 11's primary per-policy metrics."""
    accesses = det.expert_request_count
    miss_rate = (det.miss_count / accesses) if accesses else 0.0
    return {
        "policy": label,
        "total_expert_requests": det.expert_request_count,
        "hits": det.hit_count,
        "misses": det.miss_count,
        "miss_rate": miss_rate,
        "miss_bytes": det.bytes_loaded,
        "evictions": det.eviction_count,
        # LIMITATION (predeclared, not discovered after the fact):
        # argine.research.replay.ReplayResult has no eviction-BYTES field,
        # only an eviction COUNT. Reported as unavailable rather than
        # invented or approximated.
        "eviction_bytes": None,
        "peak_resident_bytes": det.maximum_resident_bytes,
        # A nonzero value here is structurally impossible to observe: the
        # replay itself raises InvariantViolation (-> FAIL, never a
        # silently-recorded violation) the instant residency would exceed
        # budget. Recorded explicitly as 0 so the metric is visible on its
        # own rather than only implied by the absence of a FAIL outcome.
        "budget_violations": 0,
        "deterministic_replay_digest": det.deterministic_replay_digest,
    }


def oracle_gap(baseline_row: Dict[str, Any], oracle_row: Dict[str, Any]) -> Dict[str, Any]:
    """Section 11's primary Oracle-gap metrics for one causal baseline."""
    b_misses, o_misses = baseline_row["misses"], oracle_row["misses"]
    b_bytes, o_bytes = baseline_row["miss_bytes"], oracle_row["miss_bytes"]
    miss_reduction = b_misses - o_misses
    byte_reduction = b_bytes - o_bytes
    return {
        "baseline": baseline_row["policy"],
        "oracle": oracle_row["policy"],
        "miss_reduction": miss_reduction,
        "relative_miss_reduction": (miss_reduction / b_misses) if b_misses else None,
        "miss_byte_reduction": byte_reduction,
        "relative_miss_byte_reduction": (byte_reduction / b_bytes) if b_bytes else None,
        # Section 14: "Oracle produces logically inconsistent results" is a
        # FAIL condition -- this flag is how the orchestrator detects it.
        # A tiny or zero reduction (oracle_dominates stays True at
        # miss_reduction == 0) is a VALID scientific result, never FAIL.
        "oracle_dominates": (o_misses <= b_misses) and (o_bytes <= b_bytes),
    }


def sharding_decomposition(rows_by_label: Dict[str, Dict[str, Any]]) -> Dict[str, Any]:
    """Sections 19/20: decompose the total observed upper-bound gap into a
    structural (sharding) component and a policy-information component.

    No shard-constrained Oracle exists in Research Part 1 (see
    policy_set.py's module docstring), so this decomposition uses
    Global-LRU as the shared pivot between the two gaps rather than a
    Sharded-Oracle-vs-Global-Oracle comparison -- reported as an explicit
    limitation, never hidden, per section 19's own escape clause."""
    sharded = rows_by_label[policy_set.LABEL_RUNTIME_SHARDED_LRU]
    global_lru = rows_by_label[policy_set.LABEL_GLOBAL_LRU]
    oracle = rows_by_label[policy_set.LABEL_ORACLE_GLOBAL_BELADY]
    structural = oracle_gap(sharded, global_lru)
    policy_info = oracle_gap(global_lru, oracle)
    total = oracle_gap(sharded, oracle)
    return {
        "limitation": (
            "no shard-constrained Oracle exists in Research Part 1 "
            "(argine.research.oracle defines a global Belady variant and a "
            "per-LAYER one, never a per-shard one); a true (A) Sharded-LRU-vs-"
            "Sharded-Oracle / (B) Sharded-Oracle-vs-Global-Oracle / (C) total "
            "decomposition (section 19) therefore cannot be built from already-"
            "validated Part 1 components. This decomposition instead uses "
            "Global-LRU as the shared pivot between the structural and "
            "policy-information gaps, which needs no new, unvalidated oracle."),
        "structural_sharding_gap": {
            **structural,
            "interpretation": "Runtime-Sharded-LRU vs Global-LRU: both causal, both "
                              "zero future knowledge -- isolates the cost of the "
                              "runtime's sharded architecture alone, decomposed away "
                              "from any prediction/retention advantage."},
        "policy_information_gap": {
            **policy_info,
            "interpretation": "Global-LRU vs Oracle-Global-Belady: both global cache "
                              "scope -- isolates the cost of imperfect (causal) "
                              "prediction alone, decomposed away from sharding."},
        "total_observed_upper_bound_gap": {
            **total,
            "interpretation": "Runtime-Sharded-LRU vs Oracle-Global-Belady: the full "
                              "observed gap against the real runtime's own "
                              "architecture; approximately structural + "
                              "policy-information above (miss counts need not add "
                              "exactly linearly across policies)."},
    }
