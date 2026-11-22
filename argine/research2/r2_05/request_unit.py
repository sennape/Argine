"""R2-05 section 6: the canonical request unit.

Three candidate granularities exist in Research Part 2's trace model
(argine.research.trace.Trace / Step):

  - "cache access" / "routing event": the per-step-per-layer UNION of
    experts across all tokens of that step (Step.experts(layer), via
    Trace.events()). This is the unit R2-01..R2-04 use for RESIDENCY --
    it is what the runtime must hold resident at once, and duplicate
    selections within a step collapse to one membership test.
  - "unique expert per step": a coarser collapse of the above (one
    expert counted at most once per step, which is already what the
    union gives).
  - "token-expert assignment": one occurrence of one expert id within
    one token's selected tuple (Step.routes[layer][token_index]), i.e.
    each individual top-k routing decision the router actually made.

R2-05's question -- "how concentrated is expert ROUTING" -- is a
question about the router's raw selection behaviour, not about cache
residency. The cache-access unit is unsuitable here: it would silently
undercount an expert that is re-selected by several tokens within the
same step (exactly the duplicate information R2-01's own trace contract
preserves and explicitly declines to collapse before replay time -- see
trace_contract.EXPERT_ORDER_SEMANTICS). R2-05 therefore uses the
TOKEN-EXPERT ASSIGNMENT as its canonical request unit, via
`Trace.token_selections(layer, phase)` -- the exact method the trace
module itself documents as being "used by the routing statistics, which
are about what the router chose per token, not about residency events."

One counted REQUEST = one occurrence of one expert id within one token's
selected-expert tuple, for one (layer_id, expert_id) pair. If a token's
tuple ever contained the same expert id more than once (not expected
under ordinary top-k routing, but not assumed impossible), each
occurrence is counted separately -- duplicate semantics are preserved
exactly as recorded, never deduplicated.

Step order, layer identity, and phase semantics are preserved because
`token_selections` itself walks `trace.steps` in recorded order and
yields per-layer, per-token tuples unmodified; nothing here reorders,
merges, or flattens across layers.
"""

from __future__ import annotations

from typing import Dict, Iterator, Tuple

from ...research.trace import Trace

REQUEST_UNIT_DEFINITION = (
    "One counted expert request = one occurrence of one expert id within one "
    "token's selected-expert tuple (Trace.token_selections(layer, phase)), for "
    "one (layer_id, expert_id) pair. This is the token-expert ASSIGNMENT unit "
    "-- the router's raw per-token top-k selection -- not the cache-access/"
    "residency-event unit R2-01..R2-04 use (the per-step-per-layer union "
    "across tokens). Duplicate expert ids within one token's tuple, if any, "
    "are each counted separately: never deduplicated."
)


def iter_layer_requests(trace: Trace, layer: int) -> Iterator[int]:
    """Yields one expert id per counted request, in trace order, for one
    layer. A pure, order-preserving re-expression of
    `Trace.token_selections` at the single-expert-occurrence granularity."""
    for _token_index, selected in trace.token_selections(layer):
        for expert_id in selected:
            yield expert_id


def count_requests_per_layer(trace: Trace) -> Dict[int, Dict[int, int]]:
    """{layer_id: {expert_id: request_count}}, built by a direct, single
    pass over the trace (layer-local: counts for one layer never leak into
    another layer's dict, even if the same raw expert id appears in both)."""
    counts: Dict[int, Dict[int, int]] = {}
    for layer in trace.layers:
        layer_counts: Dict[int, int] = {}
        for expert_id in iter_layer_requests(trace, layer):
            layer_counts[expert_id] = layer_counts.get(expert_id, 0) + 1
        counts[layer] = layer_counts
    return counts


def total_requests(counts_per_layer: Dict[int, Dict[int, int]]) -> int:
    return sum(sum(layer_counts.values()) for layer_counts in counts_per_layer.values())


def expert_id_range_valid(counts_per_layer: Dict[int, Dict[int, int]], n_experts: int) -> bool:
    """Defensive re-check (trace_contract.normalize already enforces this at
    load time, but R2-05 re-validates its own counted ids independently
    rather than silently trusting an upstream guarantee -- section 19 FAIL
    condition 'expert ID outside valid layer range')."""
    if not n_experts:
        return True
    for layer_counts in counts_per_layer.values():
        for expert_id in layer_counts:
            if not (0 <= expert_id < n_experts):
                return False
    return True


def independent_count_per_layer(trace: Trace) -> Dict[int, int]:
    """A SECOND, independently-coded computation of each layer's total
    request count, deliberately NOT going through
    `iter_layer_requests`/`token_selections`: it walks `Step.routes`
    directly. Used only for reconciliation (section 19: 'request counts
    do not reconcile' is a FAIL condition) -- a genuine second code path
    over the same trace, not a repeat of the same function."""
    totals: Dict[int, int] = {layer: 0 for layer in trace.layers}
    for st in trace.steps:
        for layer, selections in st.routes.items():
            if layer not in totals:
                continue
            for token_tuple in selections:
                totals[layer] += len(token_tuple)
    return totals
