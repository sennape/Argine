"""R2-10 deterministic output contract.

apply_output_contract is the ONLY path from a predictor's raw per-expert
scores to the returned prediction set, applied uniformly to every family
including trivial/null ones -- no predictor receives special-case
privileges (experiments/R2-10-working-set-predictor/output_contract.json).
"""
from __future__ import annotations

import math
from typing import Sequence, Tuple


def apply_output_contract(scores: Sequence[float], k: int, n_experts: int) -> Tuple[int, ...]:
    """Return exactly `k` unique expert IDs in [0, n_experts), deterministically.

    `scores` must have length exactly n_experts (scores[e] is the score for
    expert id e; unseen/cold-start experts should already be 0.0, per the
    frozen cold-start rule -- this function does not itself special-case
    cold start, it simply sorts whatever scores it is given).

    Selection: sort by (-score, expert_id) ascending (higher score first;
    ties broken by lower expert_id), take the first k. Raises ValueError on
    any contract violation rather than silently repairing it.
    """
    if len(scores) != n_experts:
        raise ValueError(f"scores length {len(scores)} != n_experts {n_experts}")
    if k <= 0:
        raise ValueError(f"k must be positive, got {k}")
    if k > n_experts:
        raise ValueError(f"k={k} exceeds n_experts={n_experts}")
    for e, s in enumerate(scores):
        if isinstance(s, float) and math.isnan(s):
            raise ValueError(f"score for expert {e} is NaN")

    ranked = sorted(range(n_experts), key=lambda e: (-scores[e], e))
    selected = tuple(ranked[:k])

    if len(selected) != k or len(set(selected)) != k:
        raise ValueError("internal error: did not produce exactly k unique expert IDs")
    for e in selected:
        if not (0 <= e < n_experts):
            raise ValueError(f"invalid expert id {e} outside universe [0,{n_experts})")
    return selected


def recall_at_k(predicted: Sequence[int], target: Sequence[int]) -> float:
    """future_requested_expert_recall_at_k: |predicted ∩ target| / |target|.

    `predicted` must already satisfy the cardinality rule (len == k_tl ==
    len(target)) by construction via apply_output_contract; this function
    does not enforce that itself, it only computes the frozen primary
    metric (metrics_contract.json.prediction_metrics primary_metric_id).
    """
    if not target:
        raise ValueError("target expert set must be non-empty (k_tl > 0)")
    pred_set = set(predicted)
    tgt_set = set(target)
    return len(pred_set & tgt_set) / len(tgt_set)
