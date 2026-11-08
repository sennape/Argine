"""R2-07 sections 4/5/6/7: the primary sequential unit, the reuse-event
definition, token reuse gap, and distinct-intervening reuse distance.

The primary sequential unit (section 4) is the exact same one R2-05's
request_unit.py and R2-06's transition_unit.py already validated:
`Trace.token_selections(layer, phase)`, walked in its own yielded
order. One POSITION is one yielded (token_index, selection) entry for
one layer; positions are numbered 0, 1, 2, ... in that yielded order
(never by raw token_index, which may skip a token this layer did not
route through -- mirroring R2-06's own "consecutive in the iterator's
own order" stance exactly). A token's selection is treated as an
unordered SET of concurrently selected experts (section 4): no
artificial within-token-tuple ordering is ever constructed for reuse-
distance purposes (section 7) -- each top-k tuple updates every one of
its member experts' last-seen position independently, at the SAME
position, with no ordering implied among them.

REUSE EVENT (section 5): for a layer-local expert e, if e is selected at
position t_prev and next selected (strictly later) at position t_now,
that second occurrence is one reuse event, with
  token_reuse_gap = t_now - t_prev  (always >= 1).
An expert's FIRST-EVER occurrence in a layer has no previous occurrence
and is classified as a COLD / FIRST OBSERVATION (section 26), tracked
separately, NEVER assigned an infinite or fabricated gap, and never
mixed into the reuse-gap distributions.

DISTINCT-INTERVENING REUSE DISTANCE (section 6): for the same reuse
event (e reused at t_now after t_prev), this is the count of DISTINCT
layer-local experts observed in ANY selection at a position strictly
between t_prev and t_now (exclusive of both endpoints), excluding e
itself. This is a conceptually different quantity from token_reuse_gap
(a temporal/positional count, not a count of a time interval) and the
two are never conflated or given the same name (section 6's own
instruction): this module always calls them, respectively, "token reuse
gap" and "distinct-intervening expert reuse distance".

MULTIPLE EXPERTS PER TOKEN / duplicate handling (section 27): if the
same expert id appears more than once within a single position's
selection tuple (not expected under ordinary top-k routing with no
duplicate ids within one tuple, but not assumed impossible -- mirroring
R2-05's and R2-06's own "never assumed impossible" stance for this exact
situation), this module follows the validated trace contract's own
semantics exactly: R2-01's trace_contract/Trace model places no
uniqueness constraint on a single token's selected-expert tuple, so a
duplicate id within one tuple is faithfully recorded AS GIVEN here too
-- it updates that expert's last-seen position exactly once (to the
CURRENT position, same as a single occurrence would), since "last seen
at position t" is a per-expert, per-position fact, not a per-occurrence
one. No reuse event is synthesized BETWEEN two occurrences of the same
expert within the SAME position (gap=0 is not a reuse event under this
definition; section 5's gap is strictly between DIFFERENT positions).
This exact behavior is documented and tested, never silently assumed.
"""

from __future__ import annotations

from typing import Any, Dict, Iterator, List, Sequence, Tuple

from ...research.trace import Trace

TEMPORAL_UNIT_DEFINITION = (
    "One POSITION = one (token_index, selection) entry yielded, in order, by "
    "Trace.token_selections(layer, phase) for one layer; positions are "
    "numbered 0, 1, 2, ... in that yielded order (never by raw token_index). "
    "A selection is an unordered SET of concurrently selected experts: no "
    "artificial within-token ordering is ever constructed."
)

REUSE_EVENT_DEFINITION = (
    "For layer-local expert e selected at position t_prev and next selected "
    "at position t_now (t_now > t_prev, t_prev being e's immediately "
    "preceding occurrence in this layer): one reuse event, with "
    "token_reuse_gap = t_now - t_prev (always >= 1). e's first-ever "
    "occurrence in a layer has no t_prev and is a COLD / FIRST OBSERVATION, "
    "tracked separately, never assigned an infinite or fabricated gap."
)

DISTINCT_INTERVENING_DEFINITION = (
    "For the same reuse event (e at t_prev, then t_now): the count of "
    "DISTINCT layer-local experts observed in any selection at a position "
    "strictly between t_prev and t_now, excluding e itself. Conceptually "
    "different from token_reuse_gap (a position-interval count, not a "
    "distinct-expert count) and never conflated with it under the same name."
)


def iter_layer_positions(trace: Trace, layer: int
                         ) -> Iterator[Tuple[int, Tuple[int, ...]]]:
    """Yields (position, selection) for one layer, position numbered 0, 1,
    2, ... in `trace.token_selections(layer)`'s own yielded order (the
    raw token_index is discarded; it is not the position used anywhere
    in R2-07)."""
    for position, (_token_index, selection) in enumerate(trace.token_selections(layer)):
        yield position, selection


def compute_layer_reuse_events(trace: Trace, layer: int) -> Dict[str, Any]:
    """Single pass over one layer's positions building: the list of reuse
    events (expert_id, t_prev, t_now, token_reuse_gap,
    distinct_intervening_reuse_distance), the cold/first-observation
    count, the raw per-position selection sequence (kept for the
    working-set and null-baseline modules, which need the same
    sequence again; returning it here avoids re-deriving it from the
    trace a second time within one synthesis pass), and the duplicate-
    within-position diagnostic count (section 27/28).

    Section 27 (duplicate handling): a duplicate expert id within ONE
    position's own selection tuple is faithfully preserved in the RAW
    occurrence count (matching R2-05's own "never deduplicated"
    convention for its request unit) but can only update that expert's
    last-seen-position state ONCE per position (state is a per-
    (expert, position) fact, not a per-tuple-slot one) -- so it never
    synthesizes a gap=0 "reuse event" between two occurrences of the
    same expert at the SAME position. Each such extra occurrence is
    counted separately as `duplicate_within_position_count`, making the
    section-28 reconciliation identity exact even in this edge case:
        raw_total_occurrences == cold_observation_count
                               + reuse_event_count
                               + duplicate_within_position_count
    This is not expected to be nonzero under ordinary top-k routing (no
    duplicate ids within one tuple), but is not assumed impossible,
    exactly mirroring R2-05's and R2-06's own stance on this same
    question -- and the trace contract does not forbid it (R2-01's own
    EXPERT_ORDER_SEMANTICS explicitly preserves duplicate information
    rather than enforcing uniqueness), so this is documented, non-
    deduplicating, non-failing behavior, not a contract violation."""
    positions: List[Tuple[int, ...]] = [selection for _p, selection in
                                        iter_layer_positions(trace, layer)]
    result = compute_reuse_events_from_positions(positions)
    result["layer_id"] = layer
    return result


def compute_reuse_events_from_positions(positions: Sequence[Tuple[int, ...]]
                                        ) -> Dict[str, Any]:
    """The same single-pass reuse-event computation as
    `compute_layer_reuse_events`, but over an already-materialized plain
    `positions` sequence rather than a trace+layer. This is the shared
    core both the OBSERVED pipeline (via `compute_layer_reuse_events`)
    and the NULL-BASELINE pipeline (`null_baseline.py`, over a shuffled
    `positions` sequence) call, so both pipelines apply IDENTICAL reuse-
    event semantics -- only the input sequence's order differs."""
    last_seen: Dict[int, int] = {}
    reuse_events: List[Dict[str, Any]] = []
    cold_count = 0
    duplicate_within_position_count = 0
    raw_total_occurrences = 0

    for position, selection in enumerate(positions):
        raw_total_occurrences += len(selection)
        # Deduplicate within THIS position only for the purpose of avoiding
        # a synthesized intra-position "reuse" (section 27: a duplicate
        # expert id within one tuple updates its last-seen position
        # exactly once, never creating a gap=0 event between the two
        # occurrences of the same expert at the SAME position).
        seen_this_position = set()
        for expert_id in selection:
            if expert_id in seen_this_position:
                duplicate_within_position_count += 1
                continue
            seen_this_position.add(expert_id)
            t_prev = last_seen.get(expert_id)
            if t_prev is None:
                cold_count += 1
            else:
                gap = position - t_prev
                distinct_intervening = _count_distinct_intervening(
                    positions, t_prev, position, expert_id)
                reuse_events.append({
                    "expert_id": expert_id, "t_prev": t_prev, "t_now": position,
                    "token_reuse_gap": gap,
                    "distinct_intervening_reuse_distance": distinct_intervening,
                })
            last_seen[expert_id] = position

    return {
        "n_positions": len(positions), "reuse_events": reuse_events,
        "cold_observation_count": cold_count, "positions": list(positions),
        "duplicate_within_position_count": duplicate_within_position_count,
        "raw_total_occurrences": raw_total_occurrences,
    }


def _count_distinct_intervening(positions: List[Tuple[int, ...]], t_prev: int, t_now: int,
                                exclude_expert_id: int) -> int:
    """Distinct layer-local experts observed in positions strictly
    between t_prev and t_now (exclusive both ends), excluding
    exclude_expert_id. O(gap) in the size of the intervening window;
    acceptable given R2-06's own finding of strong immediate locality
    (gaps are typically small), and correctness, not asymptotic
    optimality, is this function's only requirement."""
    seen = set()
    for pos in range(t_prev + 1, t_now):
        for expert_id in positions[pos]:
            if expert_id != exclude_expert_id:
                seen.add(expert_id)
    return len(seen)


def independent_reuse_accounting(trace: Trace, layer: int) -> Dict[str, int]:
    """A SECOND, independently-coded computation of this layer's total
    expert-assignment-occurrence count and cold/reuse/duplicate split,
    deliberately NOT sharing code with `compute_layer_reuse_events`: it
    walks `Step.routes` directly, maintaining its own last-seen dict, and
    counts totals only (not individual events) -- mirroring the
    reconciliation-only role request_unit.independent_count_per_layer and
    transition_unit.independent_count_transitions_per_layer play for
    R2-05/R2-06. Used only for reconciliation (section 28)."""
    last_seen: Dict[int, int] = {}
    position = 0
    raw_total_occurrences = 0
    cold = 0
    reuse = 0
    duplicate_within_position = 0
    for st in trace.steps:
        sel = st.routes.get(layer)
        if not sel:
            continue
        for tok in sel:
            seen_this_position = set()
            for expert_id in tok:
                raw_total_occurrences += 1
                if expert_id in seen_this_position:
                    duplicate_within_position += 1
                    continue
                seen_this_position.add(expert_id)
                if expert_id in last_seen:
                    reuse += 1
                else:
                    cold += 1
                last_seen[expert_id] = position
            position += 1
    return {"raw_total_occurrences": raw_total_occurrences,
           "cold_observation_count": cold, "reuse_event_count": reuse,
           "duplicate_within_position_count": duplicate_within_position}


def reconcile_layer_accounting(primary: Dict[str, Any], independent: Dict[str, int]
                               ) -> List[str]:
    """Section 28 FAIL condition: the identity
        raw_total_occurrences == cold_observation_count + reuse_event_count
                                + duplicate_within_position_count
    must hold for the PRIMARY computation, AND the primary computation's
    own totals must match the INDEPENDENTLY-coded second computation
    exactly. Returns a list of human-readable problems (empty = fully
    reconciled)."""
    problems: List[str] = []
    layer = primary["layer_id"]
    primary_reuse_count = len(primary["reuse_events"])
    identity_total = (primary["cold_observation_count"] + primary_reuse_count +
                      primary["duplicate_within_position_count"])
    if identity_total != primary["raw_total_occurrences"]:
        problems.append(f"layer {layer}: raw_total_occurrences "
                        f"{primary['raw_total_occurrences']} != cold "
                        f"({primary['cold_observation_count']}) + reuse "
                        f"({primary_reuse_count}) + duplicate_within_position "
                        f"({primary['duplicate_within_position_count']}) = {identity_total}")
    if primary["raw_total_occurrences"] != independent["raw_total_occurrences"]:
        problems.append(f"layer {layer}: primary raw_total_occurrences "
                        f"{primary['raw_total_occurrences']} != independently "
                        f"recomputed {independent['raw_total_occurrences']}")
    if primary["cold_observation_count"] != independent["cold_observation_count"]:
        problems.append(f"layer {layer}: primary cold_observation_count "
                        f"{primary['cold_observation_count']} != independently "
                        f"recomputed {independent['cold_observation_count']}")
    if primary_reuse_count != independent["reuse_event_count"]:
        problems.append(f"layer {layer}: primary reuse_event_count "
                        f"{primary_reuse_count} != independently recomputed "
                        f"{independent['reuse_event_count']}")
    if primary["duplicate_within_position_count"] != independent[
            "duplicate_within_position_count"]:
        problems.append(f"layer {layer}: primary duplicate_within_position_count "
                        f"{primary['duplicate_within_position_count']} != "
                        f"independently recomputed "
                        f"{independent['duplicate_within_position_count']}")
    return problems
