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
        self.reset()

    @property
    def config_id(self) -> str:
        return "markov_transition/default"

    def reset(self) -> None:
        """Clear incremental cache. Does not change predict() output for
        any given history -- purely an internal performance optimization.
        Safe to call any time; _transition_counts falls back to a full
        from-scratch recompute whenever the cache is absent/stale."""
        self._cache: Dict[Tuple[int, int], float] = {}
        self._cache_i: int = -1
        self._cache_last_entry = None

    def _transition_counts(self, history: Tuple) -> Dict[Tuple[int, int], float]:
        # Sparse, incrementally-maintained accumulation. When called in
        # the same strictly-increasing-position order the runner always
        # uses (one new entry appended per call, within one layer walk),
        # each call does O(top_k^2) work to add just the one new
        # transition, instead of O(i * top_k^2) to recompute the whole
        # history from scratch. Any call that is NOT a valid continuation
        # of the cached state (non-monotonic history, as in standalone
        # unit tests, or the first call of a fresh walk) safely falls back
        # to the exact same full from-scratch recompute as before --
        # correctness never depends on call order, only speed does. See
        # PHASE2-PERFORMANCE-NOTE.md for the differential verification
        # against the original dense implementation.
        n = len(history)
        i = n - 1
        if i <= 0:
            self._cache = {}
            self._cache_i = i
            self._cache_last_entry = history[i] if n else None
            return self._cache
        if self._cache_i == i - 1 and self._cache_last_entry == history[i - 1]:
            src = history[i - 1][1]
            tgt = history[i][1]
            for c in src:
                for e in tgt:
                    key = (c, e)
                    self._cache[key] = self._cache.get(key, 0.0) + 1.0
            self._cache_i = i
            self._cache_last_entry = history[i]
            return self._cache
        counts: Dict[Tuple[int, int], float] = {}
        for j in range(1, n):
            src = history[j - 1][1]
            tgt = history[j][1]
            for c in src:
                for e in tgt:
                    key = (c, e)
                    counts[key] = counts.get(key, 0.0) + 1.0
        self._cache = counts
        self._cache_i = i
        self._cache_last_entry = history[i]
        return self._cache

    def predict(self, ctx: CausalContext, k: Optional[int] = None) -> Tuple[int, ...]:
        k = ctx.k if k is None else k
        counts = self._transition_counts(ctx.history)
        _tok, current_set = ctx.current
        scores: List[float] = [0.0] * self.n_experts
        for c in current_set:
            for e in range(self.n_experts):
                scores[e] += counts.get((c, e), 0.0)
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
        self.reset()

    @property
    def config_id(self) -> str:
        return f"decayed_transition/decay={self.decay_factor}"

    def reset(self) -> None:
        """Clear incremental cache. Does not change predict() output for
        any given history -- purely an internal performance optimization."""
        self._state: Dict[Tuple[int, int], Tuple[float, int]] = {}
        self._cache_i: int = -1
        self._cache_last_entry = None

    def _decayed_state(self, history: Tuple) -> Tuple[Dict[Tuple[int, int], Tuple[float, int]], int]:
        # Incrementally-maintained lazy-decay state: decaying every step by
        # a constant factor telescopes (decay_factor**a * decay_factor**b
        # == decay_factor**(a+b)), so each touched (c, e) cell only needs
        # decay_factor**(elapsed steps since it was last touched) applied
        # once, at the moment it is next touched or read -- never a full
        # matrix rescale. When called in the same strictly-increasing-
        # position order the runner always uses, each call does O(top_k^2)
        # work for the one new transition, instead of O(i * n_experts^2)
        # to rescale and rebuild the whole history from scratch. A call
        # that is NOT a valid continuation (non-monotonic history, as in
        # standalone unit tests, or the first call of a fresh walk) safely
        # falls back to a full from-scratch recompute -- correctness never
        # depends on call order, only speed does. See
        # PHASE2-PERFORMANCE-NOTE.md for the differential verification
        # against the original dense implementation.
        n = len(history)
        i = n - 1
        if i <= 0:
            self._state = {}
            self._cache_i = i
            self._cache_last_entry = history[i] if n else None
            return self._state, i
        if self._cache_i == i - 1 and self._cache_last_entry == history[i - 1]:
            src = history[i - 1][1]
            tgt = history[i][1]
            for c in src:
                for e in tgt:
                    key = (c, e)
                    val, last = self._state.get(key, (0.0, 0))
                    val = val * (self.decay_factor ** (i - last)) + 1.0
                    self._state[key] = (val, i)
            self._cache_i = i
            self._cache_last_entry = history[i]
            return self._state, i
        state: Dict[Tuple[int, int], Tuple[float, int]] = {}
        for j in range(1, n):
            src = history[j - 1][1]
            tgt = history[j][1]
            for c in src:
                for e in tgt:
                    key = (c, e)
                    val, last = state.get(key, (0.0, 0))
                    val = val * (self.decay_factor ** (j - last)) + 1.0
                    state[key] = (val, j)
        self._state = state
        self._cache_i = i
        self._cache_last_entry = history[i]
        return self._state, i

    def _decayed_transition_counts(self, history: Tuple) -> Dict[Tuple[int, int], float]:
        state, i = self._decayed_state(history)
        return {key: val * (self.decay_factor ** (i - last)) for key, (val, last) in state.items()}

    def predict(self, ctx: CausalContext, k: Optional[int] = None) -> Tuple[int, ...]:
        k = ctx.k if k is None else k
        state, i = self._decayed_state(ctx.history)
        _tok, current_set = ctx.current
        scores: List[float] = [0.0] * self.n_experts
        for c in current_set:
            for e in range(self.n_experts):
                val, last = state.get((c, e), (0.0, i))
                scores[e] += val * (self.decay_factor ** (i - last))
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
            for e in range(self.n_experts):
                transition_scores[e] += counts.get((c, e), 0.0)

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
