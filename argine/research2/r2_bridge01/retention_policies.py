"""R2-BRIDGE-01 retention-policy family.

Both mechanisms below are thin wrappers around the ALREADY-FROZEN,
per-budget B_selected(M) baseline (`argine.research2.r2_09a.policies.AgedFrequency`,
exact hyperparameters from experiments/R2-09-prediction-benchmark/r2_09a/R2-09A-FREEZE.json)
plus the ALREADY-FROZEN R2-10 causal predictor
(`argine.research2.r2_10.predictors.DecayedTransitionPredictor`, decay=0.99 only --
no retuning, no alternate predictor, no alternate decay).

Neither mechanism reimplements eviction, memory accounting, or prediction:
they reuse `argine.research.policies.Policy`'s event-atomic contract exactly
(admission/eviction at event granularity, members processed in ascending
expert id) and the exact (score, last_ref, (layer,expert_id)) deterministic
tie-break AgedFrequency already uses, so any behavioral difference from
B_selected(M) traces back to exactly one thing: the prediction-derived
nudge each mechanism applies.

Causal boundary (mirrors experiments/R2-10-working-set-predictor/causal_boundary.json
`prediction_instant` verbatim): the predicted set used to protect/score
eviction candidates during the access at same-layer iterator position i+1
is computed strictly from the causal context built after observing
position i and before observing position i+1 of THAT layer's own stream
-- i.e. the same "immediately after (t,l), before (t+h,l)" instant R2-10
froze. No same-layer future, same-step-later-layer, or future-step data
ever reaches a predictor instance here; each layer gets its own
DecayedTransitionPredictor instance and its own strictly-increasing
`history` tuple built one observed event at a time.
"""
from __future__ import annotations

from typing import Dict, List, Optional, Sequence, Tuple

from ..r2_09a.policies import stable_obj_key
from ..r2_10.causal_walk import CausalContext
from ..r2_10.predictors import DecayedTransitionPredictor
from ...research.policies import Context, Policy

Obj = Tuple[int, int]

# Frozen prospectively for R2-BRIDGE-01 Phase 0 -- see contract.json.
FROZEN_PREDICTOR_FAMILY = "decayed_transition"
FROZEN_PREDICTOR_DECAY = 0.99

# B_selected(M) per primary R2-09A budget (bytes) -- copied verbatim from
# experiments/R2-09-prediction-benchmark/r2_09a/R2-09A-FREEZE.json.selected_baselines.
# Never reselected here.
B_SELECTED_PARAMS_BY_BUDGET: Dict[int, Dict[str, float]] = {
    1610612736: {"decay_factor": 0.999, "aging_interval_steps": 1},
    4294967296: {"decay_factor": 0.99, "aging_interval_steps": 64},
    8589934592: {"decay_factor": 0.999, "aging_interval_steps": 16},
}


def _aged_frequency_params_for_budget(budget: int) -> Dict[str, float]:
    if budget not in B_SELECTED_PARAMS_BY_BUDGET:
        raise ValueError(
            f"budget {budget} is not one of R2-BRIDGE-01's frozen primary budgets "
            f"{sorted(B_SELECTED_PARAMS_BY_BUDGET)}; do not improvise a new budget."
        )
    return B_SELECTED_PARAMS_BY_BUDGET[budget]


class _BaseBridgePolicy(Policy):
    """Shared AgedFrequency-identical scoring/eviction core plus a
    per-layer predicted-next-expert-set, refreshed causally once per
    same-layer event. Subclasses differ only in how the predicted set
    nudges victim selection (`_victim_key`)."""

    family = "retention_bridge"

    def __init__(self, decay_factor: float, aging_interval_steps: int):
        self.decay_factor = float(decay_factor)
        self.aging_interval_steps = int(aging_interval_steps)

    def start(self, ctx: Context) -> None:
        self.ctx = ctx
        self.M = int(ctx.budget)
        self.resident: Dict[Obj, int] = {}
        self.scores: Dict[Obj, float] = {}
        self.last_ref: Dict[Obj, int] = {}
        self.resident_bytes = 0
        self.evictions = 0
        self.overflow_events = 0
        self.overflow_misses = 0
        self._t = 0
        # Per-layer causal predictor state -- one fresh instance per layer,
        # never shared across layers or re-used from a prior trace.
        self._predictors: Dict[int, DecayedTransitionPredictor] = {}
        self._history: Dict[int, List[Tuple[int, Tuple[int, ...]]]] = {}
        self._predicted_next: Dict[int, frozenset] = {l: frozenset() for l in ctx.layers}

    def _apply_aging_if_due(self) -> None:
        if self.aging_interval_steps > 0 and self._t % self.aging_interval_steps == 0:
            for obj in list(self.resident.keys()):
                self.scores[obj] = self.scores.get(obj, 0.0) * self.decay_factor

    def _predictor_for(self, layer: int) -> DecayedTransitionPredictor:
        pred = self._predictors.get(layer)
        if pred is None:
            pred = DecayedTransitionPredictor(self.ctx.n_experts, FROZEN_PREDICTOR_DECAY)
            self._predictors[layer] = pred
        return pred

    def _refresh_prediction_after_observing(self, layer: int, token_index: int, experts: Tuple[int, ...]) -> None:
        """Causal update: call AFTER experts at (token_index, layer) have
        been served/admitted, predicting the layer's NEXT same-layer event
        (h=1). The resulting set is only consulted on the *next* access()
        call for this layer -- never the current one -- matching
        causal_boundary.json's prediction_instant exactly."""
        hist = self._history.setdefault(layer, [])
        hist.append((token_index, tuple(sorted(experts))))
        ctx = CausalContext(
            position=len(hist) - 1,
            token_index=token_index,
            history=tuple(hist),
            k=self.ctx.top_k,
        )
        pred = self._predictor_for(layer).predict(ctx, k=self.ctx.top_k)
        self._predicted_next[layer] = frozenset(pred)

    def _victim_key(self, o: Obj, protected: frozenset):
        raise NotImplementedError

    def _choose_victim(self, layer: int) -> Optional[Obj]:
        if not self.resident:
            return None
        protected = self._predicted_next.get(layer, frozenset())
        return min(self.resident.keys(), key=lambda o: self._victim_key(o, protected))

    def _evict_one(self, layer: int) -> bool:
        v = self._choose_victim(layer)
        if v is None:
            return False
        sz = self.resident.pop(v)
        self.scores.pop(v, None)
        self.last_ref.pop(v, None)
        self.resident_bytes -= sz
        self.evictions += 1
        return True

    def access(self, step: int, layer: int, experts: Sequence[int]) -> List[int]:
        missed: List[int] = []
        served_experts = sorted(int(x) for x in experts)
        for e in served_experts:
            self._t += 1
            self._apply_aging_if_due()
            obj = (int(layer), e)
            sz = int(self.ctx.bytes_of(layer))
            prev = self.scores.get(obj, 0.0) if obj in self.resident else 0.0
            if obj not in self.resident:
                missed.append(e)
                while self.resident_bytes + sz > self.M and self.resident:
                    self._evict_one(layer)
                if self.resident_bytes + sz <= self.M and sz <= self.M:
                    self.resident[obj] = sz
                    self.resident_bytes += sz
                    self.scores[obj] = prev + 1.0
                    self.last_ref[obj] = self._t
                else:
                    self.overflow_events += 1
                    self.overflow_misses += 1
                continue
            self.scores[obj] = self.scores.get(obj, 0.0) + 1.0
            self.last_ref[obj] = self._t
        # Causal refresh happens AFTER this event has been fully served,
        # so the resulting prediction can only affect a FUTURE access().
        self._refresh_prediction_after_observing(layer, step, tuple(served_experts))
        return missed

    def end_step(self, step: int) -> None:
        return None


class PredictedProtectPolicy(_BaseBridgePolicy):
    """Mechanism family PP ("predicted-protect", hard shield).

    Eviction victim is chosen as in AgedFrequency, but restricted to
    resident objects of the SAME layer whose expert id is NOT in that
    layer's current predicted-next set. If every resident object of that
    layer is protected (shield would otherwise deadlock), the restriction
    is dropped for that single eviction and the unrestricted AgedFrequency
    choice is used -- a deterministic, total fallback that guarantees
    correctness/no-deadlock and therefore zero budget violations.

    No extra hyperparameter beyond "shield on" -- the smallest possible
    mechanism in this family.
    """

    @property
    def causal(self) -> bool:
        return True

    @property
    def name(self) -> str:
        return f"PredictedProtect(decay={self.decay_factor},interval={self.aging_interval_steps})"

    def _victim_key(self, o: Obj, protected: frozenset):
        return (self.scores.get(o, 0.0), self.last_ref.get(o, -1), stable_obj_key(o))

    def _choose_victim(self, layer: int) -> Optional[Obj]:
        if not self.resident:
            return None
        protected_experts = self._predicted_next.get(layer, frozenset())
        candidates = [o for o in self.resident if not (o[0] == layer and o[1] in protected_experts)]
        pool = candidates if candidates else list(self.resident.keys())
        return min(pool, key=lambda o: self._victim_key(o, protected_experts))

    def describe(self):
        return {
            "family_id": "predicted_protect",
            "mechanism": "hard_shield_with_total_fallback",
            "causal": True,
            "frozen_predictor": f"{FROZEN_PREDICTOR_FAMILY}/decay={FROZEN_PREDICTOR_DECAY}",
            "base_decay_factor": self.decay_factor,
            "base_aging_interval_steps": self.aging_interval_steps,
        }


class PredictorRetentionScorePolicy(_BaseBridgePolicy):
    """Mechanism family PRS ("predictor retention score", soft bonus).

    Victim-selection key is identical to AgedFrequency's own
    (score, last_ref, stable_obj_key) EXCEPT that a resident object whose
    expert id is in its layer's current predicted-next set has `bonus`
    added to its score ONLY at the moment of victim comparison (never
    persisted into self.scores, so the bonus can never leak into aging or
    hit-scoring arithmetic -- it is a pure tie-break-time nudge).

    bonus=0.0 is the mandatory control cell: it must reproduce
    AgedFrequency / B_selected(M) bit-for-bit (see
    tests/test_r2_bridge01_control_equivalence.py), which is itself a
    first-class correctness check on this wrapper.
    """

    FROZEN_BONUS_GRID: Tuple[float, ...] = (0.0, 1.0, 4.0)

    def __init__(self, decay_factor: float, aging_interval_steps: int, bonus: float):
        if bonus not in self.FROZEN_BONUS_GRID:
            raise ValueError(f"bonus={bonus} not in frozen grid {self.FROZEN_BONUS_GRID}")
        super().__init__(decay_factor, aging_interval_steps)
        self.bonus = float(bonus)

    @property
    def causal(self) -> bool:
        return True

    @property
    def name(self) -> str:
        return f"PredictorRetentionScore(decay={self.decay_factor},interval={self.aging_interval_steps},bonus={self.bonus})"

    def _victim_key(self, o: Obj, protected: frozenset):
        base = self.scores.get(o, 0.0)
        nudge = self.bonus if o[1] in protected else 0.0
        return (base + nudge, self.last_ref.get(o, -1), stable_obj_key(o))

    def describe(self):
        return {
            "family_id": "predictor_retention_score",
            "mechanism": "additive_tiebreak_bonus",
            "causal": True,
            "frozen_predictor": f"{FROZEN_PREDICTOR_FAMILY}/decay={FROZEN_PREDICTOR_DECAY}",
            "base_decay_factor": self.decay_factor,
            "base_aging_interval_steps": self.aging_interval_steps,
            "bonus": self.bonus,
        }


def build_bridge_configs() -> List[dict]:
    """The full, intentionally-tiny, frozen R2-BRIDGE-01 Phase-0 grid:
    1 PredictedProtect config + 3 PredictorRetentionScore bonus configs
    (including the bonus=0.0 control), FOR EACH of the three frozen
    B_selected(M) budgets. 4 mechanism configs x 3 budgets = 12 cells per
    DEV/BRIDGE-holdout instance -- small by design (see decision_rules.json)."""
    configs = []
    for budget, params in B_SELECTED_PARAMS_BY_BUDGET.items():
        configs.append({
            "budget_bytes": budget,
            "family_id": "predicted_protect",
            "config_id": f"predicted_protect/budget={budget}",
            "params": dict(params),
        })
        for bonus in PredictorRetentionScorePolicy.FROZEN_BONUS_GRID:
            configs.append({
                "budget_bytes": budget,
                "family_id": "predictor_retention_score",
                "config_id": f"predictor_retention_score/budget={budget},bonus={bonus}",
                "params": {**params, "bonus": bonus},
            })
    return configs


def make_policy(config: dict):
    """Construct the Policy instance for one `build_bridge_configs()` entry."""
    params = config["params"]
    if config["family_id"] == "predicted_protect":
        return PredictedProtectPolicy(params["decay_factor"], params["aging_interval_steps"])
    if config["family_id"] == "predictor_retention_score":
        return PredictorRetentionScorePolicy(params["decay_factor"], params["aging_interval_steps"], params["bonus"])
    raise ValueError(f"unknown family_id {config['family_id']!r}")
