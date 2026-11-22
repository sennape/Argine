"""R2-06 section 4/6: the canonical transition unit.

R2-06's question -- "does the SEQUENCE of layer-local expert selections
carry conditional structure beyond marginal frequency (R2-05)?" -- is a
question about ORDER, so the unit must be a PAIR of consecutive routing
decisions, not a single one (R2-05's unit).

Candidate transition units considered (section 4):

  (A) token-to-token, SAME layer: the selection at token t and the
      selection at token t+1, both read for the SAME layer, via
      `Trace.token_selections(layer, phase)` -- the same accessor
      R2-05's request_unit.py uses, walked one step ahead of itself.
  (B) step-to-step, SAME layer: `Step.experts(layer)` at step s and at
      step s+1 -- coarser (collapses within-step token duplicates the
      same way the residency-event unit does for R2-05).
  (C) cross-layer, SAME token: selection at layer L and the selection at
      the NEXT layer the trace visits, for the SAME token/step -- always
      trivially determined by the trace's own fixed layer-visitation
      order (Trace.events() visits layers in ascending order every
      step), so a "transition" there would measure architecture, not
      routing structure, and would not be a meaningful SCIENTIFIC
      transition at all.

R2-06 uses (A): token-to-token, SAME layer. `token_selections` is
already the per-token ordering `Trace` itself documents as recording
"what the router chose per token" (not residency events), and it is
exactly the request unit R2-05 counts individually -- using it again,
walked as CONSECUTIVE PAIRS, keeps R2-06's transition unit the natural
one-step refinement of R2-05's request unit rather than inventing a new
granularity. (B) is available as a documented, clearly-labelled secondary
check only (not used for any primary number here); (C) is excluded
entirely per section 4's own instruction not to treat trivial layer order
as a scientific transition -- R2-06's PRIMARY analysis is SAME-LAYER
only.

One counted TRANSITION OBSERVATION = one (source_expert_id,
target_expert_id) pair contributed by one pair of CONSECUTIVE entries
yielded by `trace.token_selections(layer)` for one layer: entry t
(selection at token t) as source, entry t+1 (the very next entry yielded
by the SAME iterator -- i.e. the next token for which that layer has ANY
recorded routing, not necessarily token_index+1) as target. "Consecutive"
means consecutive in the iterator's own yielded order, never consecutive
TOKEN INDEX; a layer need not route every token, and this definition
deliberately does not invent a transition across a gap the trace itself
never routed through that layer.

Multi-expert (top-k) selections are expanded via documented PAIRWISE
SET-TO-SET transition counting (section 6): for every expert i in the
source selection (size k_src) and every expert j in the target selection
(size k_tgt), one transition observation i->j is counted, with EQUAL
WEIGHT 1 per pair -- never collapsed, never deduplicated even if the same
(i, j) pair recurs within one source/target pair (not expected under
ordinary top-k routing with k_src, k_tgt <= n_experts and no duplicate
ids within one tuple, but not assumed impossible; any such duplicate
pair is counted as two separate observations here, mirroring R2-05's own
"never deduplicated" stance for its request unit).

This means ONE token-to-token transition step contributes EXACTLY
k_src * k_tgt pairwise observations to the transition matrix, not one.
This is NOT "counting k^2 independent token events" in the sense of
treating them as k^2 independent samples of the ROUTING PROCESS -- it is
the documented, declared pairwise-expansion weighting scheme for
representing a multi-expert-to-multi-expert transition as a sum of
single-expert-pair observations, exactly analogous to how a weighted
bipartite co-occurrence count is built from a set-to-set adjacency. Any
layer with a fixed top_k the real trace records (trace.meta.top_k) will
have the SAME k_src = k_tgt = top_k for ordinary decode steps, so this
expansion contributes a constant multiplicative factor (top_k^2) to the
layer's total transition mass -- documented here explicitly so a reader
of transition_metrics.csv is never tempted to compare RAW transition
mass counts directly against R2-05's raw request counts without
accounting for this multiplicative difference (the mass RECONCILIATION
r2_06 performs against R2-05, see bridge.py, works at the level of
per-expert MARGINAL SHARE / probability, which this multiplicative
factor cancels out of, not raw counts).
"""

from __future__ import annotations

from typing import Dict, Iterator, List, Tuple

from ...research.trace import Trace

TRANSITION_UNIT_DEFINITION = (
    "One counted transition observation = one (source_expert_id, "
    "target_expert_id) pair contributed by one pair of CONSECUTIVE entries "
    "yielded by Trace.token_selections(layer, phase) for one layer (token-to-"
    "token, SAME layer; 'consecutive' = consecutive in the iterator's own "
    "yielded order, not consecutive token_index). Multi-expert selections are "
    "expanded via pairwise set-to-set counting: every expert i in the source "
    "selection paired with every expert j in the target selection, each pair "
    "counted once with weight 1 (never deduplicated). Cross-layer transitions "
    "are excluded from this unit entirely; layer visitation order is "
    "architectural, not a routing-structure transition."
)

# Candidate (B) from the module docstring, retained ONLY as a documented,
# clearly-labelled secondary/non-primary accessor (section 4's explicit
# allowance for a secondary check). Nothing in the primary analysis
# (matrix.py, info_metrics.py, support.py, concentration.py, summary.py,
# claims.py) calls this.
SECONDARY_STEP_TO_STEP_UNIT_DEFINITION = (
    "SECONDARY, NON-PRIMARY unit: step-to-step, same layer, via "
    "Step.experts(layer) at step s and step s+1 (the per-step residency-"
    "event union, coarser than the primary token-to-token unit -- collapses "
    "within-step token duplicates the same way R2-01..R2-04's residency "
    "unit does). Not used by any primary R2-06 metric."
)


def iter_layer_transitions(trace: Trace, layer: int
                           ) -> Iterator[Tuple[Tuple[int, ...], Tuple[int, ...]]]:
    """Yields (source_selection, target_selection) for every CONSECUTIVE
    pair of entries `trace.token_selections(layer)` yields, in order. A
    pure, order-preserving re-expression of that iterator at the
    transition-unit granularity: this function invents no new ordering
    and skips no entry the iterator itself would not have skipped."""
    prev_selection = None
    for _token_index, selection in trace.token_selections(layer):
        if prev_selection is not None:
            yield prev_selection, selection
        prev_selection = selection


def layer_selection_sequence(trace: Trace, layer: int) -> List[Tuple[int, ...]]:
    """The plain, ordered list of selections `trace.token_selections(layer)`
    yields for one layer (selection tuples only, token_index dropped).
    This is the exact sequence whose ADJACENT pairs
    `iter_layer_transitions` walks, and the exact sequence
    `null_baseline.shuffled_selection_sequence` permutes -- both consume
    this same list so the primary and null-baseline pipelines start from
    an identical, order-preserving re-expression of the trace."""
    return [selection for _token_index, selection in trace.token_selections(layer)]


def count_transitions_per_layer(trace: Trace) -> Dict[int, Dict[Tuple[int, int], int]]:
    """{layer_id: {(source_expert_id, target_expert_id): observation_count}},
    built by a single pass per layer over `iter_layer_transitions`, with the
    documented pairwise set-to-set expansion (section 6). Layer-local:
    counts for one layer never leak into another layer's dict."""
    counts: Dict[int, Dict[Tuple[int, int], int]] = {}
    for layer in trace.layers:
        layer_counts: Dict[Tuple[int, int], int] = {}
        for source_selection, target_selection in iter_layer_transitions(trace, layer):
            for i in source_selection:
                for j in target_selection:
                    key = (i, j)
                    layer_counts[key] = layer_counts.get(key, 0) + 1
        counts[layer] = layer_counts
    return counts


def total_transition_mass(counts_per_layer: Dict[int, Dict[Tuple[int, int], int]]) -> int:
    return sum(sum(layer_counts.values()) for layer_counts in counts_per_layer.values())


def expert_id_range_valid(counts_per_layer: Dict[int, Dict[Tuple[int, int], int]],
                          n_experts: int) -> bool:
    """Defensive re-check (mirrors request_unit.expert_id_range_valid):
    every source and target id counted here must lie in [0, n_experts)."""
    if not n_experts:
        return True
    for layer_counts in counts_per_layer.values():
        for (i, j) in layer_counts:
            if not (0 <= i < n_experts and 0 <= j < n_experts):
                return False
    return True


def independent_count_transitions_per_layer(trace: Trace
                                            ) -> Dict[int, Dict[Tuple[int, int], int]]:
    """A SECOND, independently-coded computation of each layer's transition
    counts, deliberately NOT going through `iter_layer_transitions`: it
    walks `Step.routes` directly, token by token, carrying its own
    previous-selection state across step boundaries. Used only for
    reconciliation (mirrors request_unit.independent_count_per_layer's
    role for R2-05) -- a genuine second code path over the same trace."""
    totals: Dict[int, Dict[Tuple[int, int], int]] = {layer: {} for layer in trace.layers}
    prev_by_layer: Dict[int, Tuple[int, ...]] = {}
    for st in trace.steps:
        for layer in trace.layers:
            sel = st.routes.get(layer)
            if not sel:
                continue
            for tok in sel:
                prev = prev_by_layer.get(layer)
                if prev is not None:
                    layer_counts = totals[layer]
                    for i in prev:
                        for j in tok:
                            key = (i, j)
                            layer_counts[key] = layer_counts.get(key, 0) + 1
                prev_by_layer[layer] = tok
    return totals


def reconcile_total_transitions(counts_per_layer: Dict[int, Dict[Tuple[int, int], int]],
                                expected_total_per_layer: Dict[int, int]) -> "list[str]":
    """Section 19-style FAIL condition (mirrors frequency.reconcile_total_requests):
    transition counts must reconcile exactly with an independently
    recomputed total, per layer."""
    problems = []
    for layer, counts in counts_per_layer.items():
        total = sum(counts.values())
        expected = expected_total_per_layer.get(layer)
        if expected is None:
            problems.append(f"layer {layer}: no independently computed expected "
                            f"transition total to reconcile against")
        elif total != expected:
            problems.append(f"layer {layer}: transition total {total} != independently "
                            f"recomputed total {expected}")
    return problems
