"""R2-07 section 14/15/29: the frequency-preserving null baseline.

Adapts the deterministic-shuffle methodology R2-06 already validated
(`r2_06.null_baseline.shuffled_selection_sequence`), but WITHOUT
importing r2_06 (R2-07 stays self-contained apart from its explicit,
separate R2-05/R2-06 bridge module, exactly as R2-06 stayed self-
contained from R2-05): the shuffling UNIT here is one layer's own
WHOLE token-selection tuple (section 14's own preferred approach --
"shuffle whole token-selection tuples within each layer"), permuted as
a block. This is the exact same operation R2-06's shuffle already is
(a permutation of a list of tuples) -- R2-07 reuses the identical
algorithm, re-expressed locally.

Shuffling whole tuples, rather than individual expert assignments
within a tuple, PROVABLY preserves, per layer, with no further
verification needed beyond a straightforward multiset-equality check
(section 29):
  - the per-layer POSITION (token) count (same list, same length);
  - each individual token-selection's own CARDINALITY (each tuple is
    moved whole, never split or resized);
  - the TOTAL expert-assignment-occurrence count (sum of tuple lengths
    is a multiset property, invariant under permutation);
  - the PER-EXPERT MARGINAL count (each expert's total occurrence count
    across all tuples is also a multiset property).
These invariants are verified PROGRAMMATICALLY (section 29) by
`verify_shuffle_invariants`, never merely assumed from the shuffle
algorithm's own correctness -- a defensive, fail-closed check exactly
mirroring R2-06's own stance on its null baseline's marginal
preservation.

NULL_BASELINE_SEED is fixed now, before any real R2-07 execution, and
must never be chosen or adjusted after seeing results. Exactly ONE
shuffled replicate is used (section 14 permits predeclaring more than
one; R2-07 predeclares exactly one, documented here, since R2-07 only
reports descriptive observed/null ratios, not a formal significance
test that would need replicate variance -- section 15's own
instruction not to make formal significance claims without multiple
replicates and valid statistical methodology, which R2-07 does not
implement).
"""

from __future__ import annotations

import random
from typing import Any, Dict, List, Sequence, Tuple

from . import temporal_unit, working_set

NULL_BASELINE_SEED = 1234
NULL_BASELINE_REPLICATE_COUNT = 1
SHUFFLE_UNIT_DEFINITION = (
    "Whole token-selection tuples are shuffled as indivisible blocks within "
    "each layer (never individual expert assignments within a tuple), using "
    "a fixed, predeclared seed and exactly one replicate -- this provably "
    "preserves per-layer token count, per-token cardinality, total "
    "assignment occurrences, and per-expert marginal counts, while "
    "destroying temporal order/locality."
)


def shuffled_positions(positions: Sequence[Tuple[int, ...]], *,
                       seed: int = NULL_BASELINE_SEED) -> List[Tuple[int, ...]]:
    """A fixed-seed, deterministic permutation of `positions` (the exact
    per-layer sequence of token-selection tuples). Each tuple is moved
    whole; none is split, resized, or regenerated."""
    rng = random.Random(seed)
    shuffled = list(positions)
    rng.shuffle(shuffled)
    return shuffled


def verify_shuffle_invariants(original: Sequence[Tuple[int, ...]],
                              shuffled: Sequence[Tuple[int, ...]]) -> List[str]:
    """Section 29's programmatic invariant check. Returns a list of
    human-readable problems (empty = all invariants hold). FAIL-closed:
    any violation here is an immediate R2-07 FAIL (never silently
    tolerated), per section 30's outcome rules."""
    problems: List[str] = []
    if len(original) != len(shuffled):
        problems.append(f"token count changed: {len(original)} -> {len(shuffled)}")
        return problems  # further checks would be meaningless

    original_cardinalities = sorted(len(sel) for sel in original)
    shuffled_cardinalities = sorted(len(sel) for sel in shuffled)
    if original_cardinalities != shuffled_cardinalities:
        problems.append("per-token selection cardinality multiset changed under shuffle")

    original_total = sum(len(sel) for sel in original)
    shuffled_total = sum(len(sel) for sel in shuffled)
    if original_total != shuffled_total:
        problems.append(f"total assignment occurrences changed: {original_total} -> "
                        f"{shuffled_total}")

    original_marginal: Dict[int, int] = {}
    for sel in original:
        for e in sel:
            original_marginal[e] = original_marginal.get(e, 0) + 1
    shuffled_marginal: Dict[int, int] = {}
    for sel in shuffled:
        for e in sel:
            shuffled_marginal[e] = shuffled_marginal.get(e, 0) + 1
    if original_marginal != shuffled_marginal:
        problems.append("per-expert marginal counts changed under shuffle")

    return problems


def layer_null_baseline(positions: Sequence[Tuple[int, ...]], *,
                        nominal_expert_count: "int | None",
                        seed: int = NULL_BASELINE_SEED) -> Dict[str, Any]:
    """Full null-baseline bundle for one layer: the shuffled position
    sequence, the invariant-check result, and the SAME reuse-event /
    working-set pipeline the observed data goes through, applied to the
    shuffled sequence."""
    shuffled = shuffled_positions(positions, seed=seed)
    invariant_problems = verify_shuffle_invariants(positions, shuffled)

    reuse = temporal_unit.compute_reuse_events_from_positions(shuffled)
    window_metrics = working_set.layer_working_set_metrics(
        shuffled, nominal_expert_count=nominal_expert_count)
    growth = working_set.growth_curve(shuffled)

    return {
        "seed": seed, "n_replicates": NULL_BASELINE_REPLICATE_COUNT,
        "invariant_problems": invariant_problems,
        "n_positions": reuse["n_positions"],
        "cold_observation_count": reuse["cold_observation_count"],
        "reuse_event_count": len(reuse["reuse_events"]),
        "reuse_events": reuse["reuse_events"],
        "window_metrics": window_metrics,
        "growth_curve": growth,
    }
