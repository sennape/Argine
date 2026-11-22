"""R2-02's multi-policy deterministic replay, generalizing (never
duplicating or modifying) R2-01's own `replay_contract.py` digest pattern.

`r2_01.replay_contract.run_contract_replay()` is hardcoded to run exactly
ONE policy (GlobalLRU) for R2-01's own contract-validation purposes and is
R2-01's canonical-evidence code path -- it must stay untouched (R2-01
immutability). This module reuses its `_digest`/`_deterministic_subset`/
`DeterministicReplayResult` building blocks directly (import, not copy)
and adds the one thing R2-01's version does not need: an arbitrary policy
parameter plus the causal/non-causal assertion appropriate to it.
"""

from __future__ import annotations

from typing import Optional

from ...research.policies import Policy
from ...research.replay import InvariantViolation, ReplayResult, replay
from ...research.trace import PHASE_DECODE, Trace
from ..r2_01 import policy_interface
from ..r2_01.replay_contract import (
    REPLAY_SCHEMA_VERSION,
    TRACE_SCHEMA_VERSION,
    DeterministicReplayResult,
    ReplayContractError,
    _deterministic_subset,
    _digest,
)


def run_policy_replay(trace: Trace, policy: Policy, *, budget: int, causal: bool,
                      phase: Optional[str] = PHASE_DECODE, trace_digest: str = ""
                      ) -> "tuple[DeterministicReplayResult, ReplayResult]":
    """Replay `policy` against `trace` at `budget` bytes and build the same
    deterministic-result contract R2-01 validated, generalized to any
    policy. `causal` states which assertion (policy_interface.assert_causal
    / assert_non_causal) this policy must satisfy -- the caller's own
    declared metadata (policy_set.PolicySpec.causal), never inferred.

    Raises ReplayContractError (wrapping InvariantViolation) exactly like
    R2-01's version: a capacity/invariant violation is a FAIL-grade
    finding, never swallowed. Raises policy_interface.PolicyInterfaceError
    if the policy's own `needs_future` flag disagrees with `causal`."""
    if causal:
        policy_interface.assert_causal(policy)
    else:
        policy_interface.assert_non_causal(policy)

    try:
        rr = replay(trace, policy, int(budget), phase=phase)
    except InvariantViolation as e:
        raise ReplayContractError(str(e)) from e

    det = _deterministic_subset(rr)
    final_resident_digest = _digest({"peak_resident": rr.peak_resident, "per_layer": rr.per_layer})
    full_digest = _digest(det)
    result = DeterministicReplayResult(
        trace_schema_version=TRACE_SCHEMA_VERSION,
        replay_schema_version=REPLAY_SCHEMA_VERSION,
        trace_digest=trace_digest,
        policy_identifier=rr.policy,
        effective_budget=int(budget),
        routing_step_count=rr.steps,
        expert_request_count=rr.accesses,
        hit_count=rr.hits,
        miss_count=rr.misses,
        eviction_count=rr.evictions,
        bytes_loaded=rr.bytes_missed,
        maximum_resident_bytes=rr.peak_resident,
        final_resident_set_digest=final_resident_digest,
        deterministic_replay_digest=full_digest,
    )
    return result, rr
