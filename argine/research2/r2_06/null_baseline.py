"""R2-06 section 13 (part): the frequency-controlled null baseline.

Two distinct, complementary baselines are computed, both deterministic
and documented BEFORE any real R2-06 execution:

  (1) ANALYTIC INDEPENDENCE BASELINE: the expected transition matrix IF
      source and target were statistically independent given each
      layer's OWN observed marginals -- P_expected(i, j) = P(i) * P(j),
      hence P_expected(j|i) = P(j) for every i. This is exact, needs no
      random seed, and is reproducible by construction; it is the
      PRIMARY frequency-controlled baseline (section 11's comparison
      target for "observed-vs-marginal-expected").
  (2) DETERMINISTIC SHUFFLED NULL: a fixed-seed permutation of the
      per-layer SEQUENCE of observed token-to-token selections (the
      exact multiset of selections `trace.token_selections(layer)`
      yields is preserved; only their ORDER is destroyed), re-run
      through the same transition-counting pipeline. This is a genuine
      resampling-style null, included as a second, confirmatory check
      per section 13's "optionally implement" language; it is NOT used
      as the primary baseline because (1) is already exact and requires
      no seed choice to defend.

NULL_BASELINE_SEED is fixed now, before any real execution, and must
never be chosen or adjusted after seeing results.
"""

from __future__ import annotations

import random
from typing import Any, Dict, List, Tuple

from . import info_metrics
from . import matrix

NULL_BASELINE_SEED = 1234


def analytic_independence_table(table: Dict[str, Any]) -> Dict[str, Any]:
    """Builds the EXACT expected transition structure under source/target
    independence, using this layer's own observed source and target
    marginals: P_expected(j|i) = P(j) for every i, every j. No count
    data beyond the marginals is used -- this is the closed-form null,
    not a resampled one."""
    source_marginal = table["source_marginal"]
    target_marginal = table["target_marginal"]
    conditional = {i: dict(target_marginal) for i in source_marginal}
    return {
        "layer_id": table["layer_id"],
        "source_marginal": source_marginal,
        "target_marginal": target_marginal,
        "conditional": conditional,
        "sources": sorted(source_marginal),
    }


def analytic_independence_information_metrics(independence_table: Dict[str, Any]) -> Dict[str, Any]:
    """Under exact independence, H(Y|X) == H(Y) by construction, so
    I(X;Y) == 0 and NMI == 0 whenever H(Y) > 0 (None when H(Y) == 0, for
    the same fail-closed reason info_metrics.layer_information_metrics
    uses). Computed via the shared info_metrics helpers rather than
    hard-coded to 0, so a bug in either path would surface as a
    mismatch, not be silently assumed away."""
    if not independence_table["sources"]:
        return {"layer_id": independence_table["layer_id"], "marginal_entropy": None,
               "conditional_entropy": None, "mutual_information": None,
               "normalized_mutual_information": None}
    h_y = info_metrics.marginal_entropy(independence_table["target_marginal"])
    h_y_given_x = info_metrics.conditional_entropy(independence_table["source_marginal"],
                                                    independence_table["conditional"])
    mi = h_y - h_y_given_x
    nmi = (mi / h_y) if h_y > 0.0 else None
    return {"layer_id": independence_table["layer_id"], "marginal_entropy": h_y,
           "conditional_entropy": h_y_given_x, "mutual_information": mi,
           "normalized_mutual_information": nmi}


def shuffled_selection_sequence(selections: List[Tuple[int, ...]], *,
                                seed: int = NULL_BASELINE_SEED) -> List[Tuple[int, ...]]:
    """A fixed-seed, deterministic permutation of `selections` (the exact
    per-layer sequence `trace.token_selections(layer)` yields, in
    order). Preserves the multiset exactly (same selections, same
    multiplicities, hence the same per-layer single-expert marginal);
    destroys the adjacency/order structure the primary transition
    analysis depends on."""
    rng = random.Random(seed)
    shuffled = list(selections)
    rng.shuffle(shuffled)
    return shuffled


def shuffled_transition_counts(selections: List[Tuple[int, ...]], *,
                               seed: int = NULL_BASELINE_SEED) -> Dict[Tuple[int, int], int]:
    """Re-runs the SAME pairwise set-to-set transition counting
    (transition_unit's documented semantics, re-expressed directly over
    an in-memory sequence rather than re-walking the trace) over the
    shuffled sequence."""
    shuffled = shuffled_selection_sequence(selections, seed=seed)
    counts: Dict[Tuple[int, int], int] = {}
    for a, b in zip(shuffled[:-1], shuffled[1:]):
        for i in a:
            for j in b:
                key = (i, j)
                counts[key] = counts.get(key, 0) + 1
    return counts


def layer_null_baseline(layer: int, selections: List[Tuple[int, ...]], *,
                        seed: int = NULL_BASELINE_SEED) -> Dict[str, Any]:
    """Full null-baseline bundle for one layer: the shuffled transition
    table and its information metrics, alongside the seed/config used
    (so the determinism check can verify the SAME seed/config reproduces
    an identical result)."""
    counts = shuffled_transition_counts(selections, seed=seed)
    table = matrix.layer_transition_table(layer, counts)
    info = info_metrics.layer_information_metrics(table)
    return {
        "layer_id": layer, "seed": seed, "n_selections": len(selections),
        "normalized_mutual_information": info["normalized_mutual_information"],
        "mutual_information": info["mutual_information"],
        "conditional_entropy": info["conditional_entropy"],
        "marginal_entropy": info["marginal_entropy"],
    }
