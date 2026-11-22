"""Deterministic replay-result contract (section 9), wrapping Research Part
1's already-validated `argine.research.replay.replay()` rather than
reimplementing event-atomic replay (section 8/13). R2-01 runs exactly ONE
causal, counterfactual policy (GlobalLRU) for contract validation -- this is
NOT a policy comparison (R2-02's job) and carries no performance claim.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from typing import Any, Dict, Optional

from ...research.policies import GlobalLRU, Policy
from ...research.replay import InvariantViolation, ReplayResult, replay
from ...research.trace import PHASE_DECODE, Trace
from . import policy_interface

TRACE_SCHEMA_VERSION = 1
REPLAY_SCHEMA_VERSION = 1

# Deterministic fields only: wall_s/controller_s are measured wall-clock
# timing (volatile execution metadata, section 9), never part of the
# scientific digest.
_DETERMINISTIC_FIELDS = (
    "policy", "budget", "phase", "steps", "tokens", "events", "accesses",
    "hits", "misses", "bytes_missed", "evictions", "overflow_events",
    "overflow_misses", "peak_resident", "per_layer", "per_step_misses",
    "per_step_bytes", "per_step_accesses",
)


class ReplayContractError(ValueError):
    pass


@dataclass
class DeterministicReplayResult:
    trace_schema_version: int
    replay_schema_version: int
    trace_digest: str
    policy_identifier: str
    effective_budget: int
    routing_step_count: int
    expert_request_count: int
    hit_count: int
    miss_count: int
    eviction_count: int
    bytes_loaded: int
    maximum_resident_bytes: int
    final_resident_set_digest: str
    deterministic_replay_digest: str

    def to_json(self) -> Dict[str, Any]:
        return dict(self.__dict__)


def _deterministic_subset(rr: ReplayResult) -> Dict[str, Any]:
    d = {}
    for f in _DETERMINISTIC_FIELDS:
        d[f] = getattr(rr, f)
    return d


def _digest(obj: Any) -> str:
    return hashlib.sha256(json.dumps(obj, sort_keys=True, default=str).encode()).hexdigest()


def default_budget(trace: Trace) -> int:
    """A deterministic, trace-derived default budget: half the bytes needed
    to make every layer's observed experts simultaneously resident at peak,
    or a 1-unit-per-expert count budget when no layer has byte metadata.
    Exists only so R2-01's contract-validation replay has SOME budget when
    the caller does not supply one; R2-02 owns real budget selection."""
    if not trace.meta.expert_bytes:
        return max(1, trace.meta.n_experts // 2 or 1)
    total = 0
    for l in trace.layers:
        b = trace.meta.expert_bytes.get(l, 1)
        seen = set()
        for st in trace.steps:
            seen.update(st.experts(l))
        total += b * len(seen)
    return max(1, total // 2)


def run_contract_replay(trace: Trace, *, budget: Optional[int] = None,
                        phase: Optional[str] = PHASE_DECODE) -> DeterministicReplayResult:
    """Run the single contract-validation replay (GlobalLRU, causal) and
    build the deterministic result contract. Raises ReplayContractError
    (wrapping InvariantViolation) if a capacity or policy invariant is
    violated -- that is a FAIL-grade finding for R2-01, never swallowed."""
    policy: Policy = GlobalLRU()
    policy_interface.assert_causal(policy)
    eff_budget = int(budget) if budget is not None else default_budget(trace)
    try:
        rr = replay(trace, policy, eff_budget, phase=phase)
    except InvariantViolation as e:
        raise ReplayContractError(str(e)) from e

    det = _deterministic_subset(rr)
    final_resident_digest = _digest({"peak_resident": rr.peak_resident, "per_layer": rr.per_layer})
    full_digest = _digest(det)
    return DeterministicReplayResult(
        trace_schema_version=TRACE_SCHEMA_VERSION,
        replay_schema_version=REPLAY_SCHEMA_VERSION,
        trace_digest="",  # filled by caller, who has the NormalizationReport
        policy_identifier=rr.policy,
        effective_budget=eff_budget,
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
