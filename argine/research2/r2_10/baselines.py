"""R2-10 Phase-0 trivial/null causal predictor baselines.

Only the two causal_runtime_candidate trivial baselines from
predictor_space.json are implemented here (global_frequency,
last_observation) -- enough to exercise causal_walk.py and
output_contract.py end-to-end for the Phase-0 leakage/determinism/
cardinality tests. shuffled_null is implemented separately as an
ANALYTICAL_REFERENCE_ONLY descriptive statistic, never wired through
apply_output_contract, and never a member of any selection pool.
"""
from __future__ import annotations

import random
from typing import List, Sequence, Tuple

from .causal_walk import CausalContext, Entry
from .output_contract import apply_output_contract


class GlobalFrequencyBaseline:
    """score(e) = cumulative count of e observed in history up to and
    including the current position. Deterministic; no hyperparameters."""

    family_id = "global_frequency"

    def __init__(self, n_experts: int):
        self.n_experts = n_experts

    def predict(self, ctx: CausalContext, k: int) -> Tuple[int, ...]:
        scores: List[float] = [0.0] * self.n_experts
        for _tok, experts in ctx.history:
            for e in experts:
                scores[e] += 1.0
        return apply_output_contract(scores, k, self.n_experts)


class LastObservationBaseline:
    """score(e) = 1.0 if e in the single most recent observed expert_set,
    else 0.0 (which includes true cold start at position 0, where
    `current` IS the only observation and so is also the prediction; the
    rule still only ever reads ctx.history/ctx.current, never the future).
    Deterministic; no hyperparameters."""

    family_id = "last_observation"

    def __init__(self, n_experts: int):
        self.n_experts = n_experts

    def predict(self, ctx: CausalContext, k: int) -> Tuple[int, ...]:
        scores: List[float] = [0.0] * self.n_experts
        _tok, last_set = ctx.current
        for e in last_set:
            scores[e] = 1.0
        return apply_output_contract(scores, k, self.n_experts)


def shuffled_null_descriptive_recall(
    stream: Sequence[Entry], horizon: int, n_experts: int, seed: int = 0
) -> float:
    """ANALYTICAL_REFERENCE_ONLY descriptive statistic (predictor_space.json
    null_trivial_baselines.analytical_reference_only). Shuffles the
    SAME-layer expert-set sequence (breaking true temporal order, mirroring
    R2-06/R2-07's null_baseline.py convention) and reports the mean recall a
    GlobalFrequencyBaseline-style score would achieve against the shuffled
    target stream. This is NEVER passed through apply_output_contract as a
    runtime policy and NEVER entered into selection_rule.json's pool; it
    exists only to show what chance/marginal structure alone would achieve.
    Explicitly seeded (seed=0 default) for reproducibility, per the
    determinism requirement even for this non-causal reference statistic.
    """
    from .output_contract import recall_at_k

    rng = random.Random(seed)
    shuffled = list(stream)
    rng.shuffle(shuffled)
    if len(shuffled) <= horizon:
        return float("nan")
    recalls = []
    scores = [0.0] * n_experts
    for i in range(len(shuffled) - horizon):
        _tok, experts = shuffled[i]
        for e in experts:
            scores[e] += 1.0
        target = shuffled[i + horizon][1]
        if not target:
            continue
        pred = apply_output_contract(list(scores), len(target), n_experts)
        recalls.append(recall_at_k(pred, target))
    return sum(recalls) / len(recalls) if recalls else float("nan")
