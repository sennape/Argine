"""R2-10 Phase-1: the four remaining causal predictor families frozen in
experiments/R2-10-working-set-predictor/predictor_space.json.

recency_window, markov_transition, decayed_transition, and
recency_frequency_transition_blend. Each is strictly causal (built only
from CausalContext.history, i.e. causal_boundary.json's
same_layer_history_upto_i) and deterministic. Hyperparameter grids are
exactly the finite grids frozen in predictor_space.json -- no
configuration is invented or silently removed here.

k (output cardinality) always comes from ctx.k (trace.meta.top_k, resolved
before any routing event -- see CARDINALITY-AVAILABILITY-AUDIT-001), never
from the target.
"""
from __future__ import annotations

from typing import Dict, List, Optional, Tuple

from .causal_walk import CausalContext
from .output_contract import apply_output_contract

# Hyperparameter grids -- must match predictor_space.json exactly.
RECENCY_WINDOW_GRID: Tuple[int, ...] = (4, 16, 64, 256)
DECAYED_TRANSITION_GRID: Tuple[float, ...] = (0.9, 0.99, 0.999)
BLEND_WEIGHT_GRID: Tuple[Tuple[float, float, float], ...] = (
    (1.0, 0.0, 0.0),
    (0.0, 1.0, 0.0),
    (0.0, 0.0, 1.0),
    (0.34, 0.33, 0.33),
    (0.5, 0.25, 0.25),
    (0.25, 0.25, 0.5),
)


class RecencyWindowPredictor:
    """score(e) = count of e within the last `window_W` same-layer
    observations ending at (and including) the current position."""

    family_id = "recency_window"

    def __init__(self, n_experts: int, window_W: int):
        if window_W not in RECENCY_WINDOW_GRID:
            raise ValueError(f"window_W={window_W} not in frozen grid {RECENCY_WINDOW_GRID}")
        self.n_experts = n_experts
        self.window_W = window_W

    @property
    def config_id(self) -> str:
        return f"recency_window/window_W={self.window_W}"

    def predict(self, ctx: CausalContext, k: Optional[int] = None) -> Tuple[int, ...]:
        k = ctx.k if k is None else k
        scores: List[float] = [0.0] * self.n_experts
        window = ctx.history[-self.window_W:]
        for _tok, experts in window:
            for e in experts:
                scores[e] += 1.0
        return apply_output_contract(scores, k, self.n_experts)


class MarkovTransitionPredictor:
    """Order-1 same-layer transition predictor: causal_state is empirical
    transition counts P(next_expert | current_expert), accumulated over
    consecutive same-layer pairs strictly BEFORE the current position
    (pairwise set-to-set expansion, same convention R2-06 already defined
    for the token-to-token same-layer transition unit). score(e) = sum over
    c in the CURRENT observed set of transition_count[c][e]."""

    family_id = "markov_transition"

    def __init__(self, n_experts: int):
        self.n_experts = n_experts

    @property
    def config_id(self) -> str:
        return "markov_transition/default"

    def _transition_counts(self, history: Tuple) -> List[List[float]]:
        counts = [[0.0] * self.n_experts for _ in range(self.n_experts)]
        for j in range(1, len(history)):
            src = history[j - 1][1]
            tgt = history[j][1]
            for c in src:
                for e in tgt:
                    counts[c][e] += 1.0
        return counts

    def predict(self, ctx: CausalContext, k: Optional[int] = None) -> Tuple[int, ...]:
        k = ctx.k if k is None else k
        counts = self._transition_counts(ctx.history)
        _tok, current_set = ctx.current
        scores: List[float] = [0.0] * self.n_experts
        for c in current_set:
            row = counts[c]
            for e in range(self.n_experts):
                scores[e] += row[e]
        return apply_output_contract(scores, k, self.n_experts)


class DecayedTransitionPredictor:
    """Same causal_state shape as MarkovTransitionPredictor, but with
    exponential decay applied to transition counts at each new observation
    (mirrors R2-09A's aged_frequency caching family, applied to transition
    counts instead of residency counts)."""

    family_id = "decayed_transition"

    def __init__(self, n_experts: int, decay_factor: float):
        if decay_factor not in DECAYED_TRANSITION_GRID:
            raise ValueError(f"decay_factor={decay_factor} not in frozen grid {DECAYED_TRANSITION_GRID}")
        self.n_experts = n_experts
        self.decay_factor = decay_factor

    @property
    def config_id(self) -> str:
        return f"decayed_transition/decay={self.decay_factor}"

    def _decayed_transition_counts(self, history: Tuple) -> List[List[float]]:
        counts = [[0.0] * self.n_experts for _ in range(self.n_experts)]
        for j in range(1, len(history)):
            for row in counts:
                for e in range(self.n_experts):
                    row[e] *= self.decay_factor
            src = history[j - 1][1]
            tgt = history[j][1]
            for c in src:
                for e in tgt:
                    counts[c][e] += 1.0
        return counts

    def predict(self, ctx: CausalContext, k: Optional[int] = None) -> Tuple[int, ...]:
        k = ctx.k if k is None else k
        counts = self._decayed_transition_counts(ctx.history)
        _tok, current_set = ctx.current
        scores: List[float] = [0.0] * self.n_experts
        for c in current_set:
            row = counts[c]
            for e in range(self.n_experts):
                scores[e] += row[e]
        return apply_output_contract(scores, k, self.n_experts)


class RecencyFrequencyTransitionBlendPredictor:
    """Linear combination of recency_window (fixed window_W=16, matching
    predictor_space.json's documented default for the degenerate grid
    points), global_frequency, and markov_transition scores, each
    independently causal and each normalized to [0,1] by its own max
    before blending so the weights are meaningful."""

    family_id = "recency_frequency_transition_blend"
    DEFAULT_WINDOW_W = 16

    def __init__(self, n_experts: int, weights: Tuple[float, float, float]):
        if weights not in BLEND_WEIGHT_GRID:
            raise ValueError(f"weights={weights} not in frozen grid {BLEND_WEIGHT_GRID}")
        self.n_experts = n_experts
        self.weights = weights
        self._recency = RecencyWindowPredictor(n_experts, self.DEFAULT_WINDOW_W)
        self._markov = MarkovTransitionPredictor(n_experts)

    @property
    def config_id(self) -> str:
        w_r, w_f, w_t = self.weights
        return f"recency_frequency_transition_blend/w_r={w_r},w_f={w_f},w_t={w_t}"

    @staticmethod
    def _normalized(scores: List[float]) -> List[float]:
        m = max(scores) if scores else 0.0
        if m <= 0.0:
            return [0.0] * len(scores)
        return [s / m for s in scores]

    def _raw_scores(self, ctx: CausalContext) -> Tuple[List[float], List[float], List[float]]:
        recency_scores = [0.0] * self.n_experts
        window = ctx.history[-self._recency.window_W:]
        for _tok, experts in window:
            for e in experts:
                recency_scores[e] += 1.0

        freq_scores = [0.0] * self.n_experts
        for _tok, experts in ctx.history:
            for e in experts:
                freq_scores[e] += 1.0

        counts = self._markov._transition_counts(ctx.history)
        _tok, current_set = ctx.current
        transition_scores = [0.0] * self.n_experts
        for c in current_set:
            row = counts[c]
            for e in range(self.n_experts):
                transition_scores[e] += row[e]

        return recency_scores, freq_scores, transition_scores

    def predict(self, ctx: CausalContext, k: Optional[int] = None) -> Tuple[int, ...]:
        k = ctx.k if k is None else k
        recency_scores, freq_scores, transition_scores = self._raw_scores(ctx)
        r_n = self._normalized(recency_scores)
        f_n = self._normalized(freq_scores)
        t_n = self._normalized(transition_scores)
        w_r, w_f, w_t = self.weights
        scores = [w_r * r_n[e] + w_f * f_n[e] + w_t * t_n[e] for e in range(self.n_experts)]
        return apply_output_contract(scores, k, self.n_experts)


def all_configs(n_experts: int) -> Dict[str, List]:
    """Every (family_id -> [instantiated predictor, ...]) pair for the
    frozen finite grids, exactly as predictor_space.json declares them.
    Used by the Phase-1 runner (Step 5) to enumerate the config space
    without hand-listing instances twice."""
    out: Dict[str, List] = {
        "recency_window": [RecencyWindowPredictor(n_experts, w) for w in RECENCY_WINDOW_GRID],
        "markov_transition": [MarkovTransitionPredictor(n_experts)],
        "decayed_transition": [DecayedTransitionPredictor(n_experts, d) for d in DECAYED_TRANSITION_GRID],
        "recency_frequency_transition_blend": [
            RecencyFrequencyTransitionBlendPredictor(n_experts, w) for w in BLEND_WEIGHT_GRID
        ],
    }
    return out
