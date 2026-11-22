"""R2-06 section 10: information-theoretic metrics over one layer's
transition structure.

These quantities describe how much information the SOURCE expert carries
about the TARGET expert, beyond what the target's own marginal frequency
already tells you. They are NEVER described, here or anywhere downstream
(claims.py enforces this), as "prediction accuracy" -- mutual information
and normalized mutual information measure association/dependence, not a
predictor's performance on a held-out task; R2-06 trains no predictor
and reports no accuracy/precision/recall/F1 (that is explicitly II-C's
question, not this experiment's).

All entropies are computed in natural log (nats), matching R2-05's
`metrics.shannon_entropy` convention, so NMI = I(X;Y) / H(Y) is a pure
ratio independent of the log base used.
"""

from __future__ import annotations

import math
from typing import Any, Dict


def _entropy(probabilities) -> float:
    """H = -sum p_i log(p_i), natural log, 0 log 0 := 0. Identical
    convention to r2_05.metrics.shannon_entropy; duplicated here (not
    imported) so this module has no dependency on r2_05's package,
    keeping R2-06 self-contained apart from the explicit, separate
    bridge.py reconciliation step."""
    return -sum(p * math.log(p) for p in probabilities if p and p > 0.0)


def marginal_entropy(target_marginal: Dict[int, float]) -> float:
    """H(Y): entropy of the target marginal distribution P(j)."""
    return _entropy(target_marginal.values())


def conditional_entropy(source_marginal: Dict[int, float],
                        conditional: Dict[int, Dict[int, float]]) -> float:
    """H(Y|X) = sum_i P(i) * H(Y|X=i), weighted by each source expert's
    own marginal probability P(i). Sources with zero outgoing mass do
    not occur here (source_marginal only contains sources with row_sum
    > 0 by construction in matrix.layer_transition_table)."""
    total = 0.0
    for i, p_i in source_marginal.items():
        dist = conditional.get(i, {})
        h_i = _entropy(p for p in dist.values() if p is not None)
        total += p_i * h_i
    return total


def outgoing_entropy(conditional_row: Dict[int, "float | None"]) -> float:
    """H(Y|X=i) for a single source expert i: entropy of that source's
    OWN conditional target distribution. Used by concentration.py for
    the per-source 'outgoing conditional entropy' metric (section 11)."""
    return _entropy(p for p in conditional_row.values() if p is not None)


def layer_information_metrics(table: Dict[str, Any]) -> Dict[str, Any]:
    """H(Y), H(Y|X), I(X;Y) = H(Y) - H(Y|X), and NMI = I(X;Y)/H(Y) when
    H(Y) > 0, else None (fail-closed: a target marginal with zero entropy
    -- every transition in this layer landing on exactly one expert --
    makes a NORMALIZED mutual information undefined, not zero; I(X;Y)
    itself is still reported, since it is well-defined, just not
    normalizable)."""
    if not table["sources"]:
        return {
            "layer_id": table["layer_id"], "marginal_entropy": None,
            "conditional_entropy": None, "mutual_information": None,
            "normalized_mutual_information": None,
        }
    h_y = marginal_entropy(table["target_marginal"])
    h_y_given_x = conditional_entropy(table["source_marginal"], table["conditional"])
    mi = h_y - h_y_given_x
    if -1e-9 < mi < 0.0:
        # Floating-point noise around a true value of exactly 0 (X, Y
        # independent): clamp rather than report a physically-impossible
        # negative mutual information.
        mi = 0.0
    nmi = (mi / h_y) if h_y > 0.0 else None
    return {
        "layer_id": table["layer_id"],
        "marginal_entropy": h_y,
        "conditional_entropy": h_y_given_x,
        "mutual_information": mi,
        "normalized_mutual_information": nmi,
    }


def all_layer_information_metrics(tables: Dict[int, Dict[str, Any]]) -> Dict[int, Dict[str, Any]]:
    return {layer: layer_information_metrics(table) for layer, table in sorted(tables.items())}
