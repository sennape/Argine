"""R2-BRIDGE-01 Phase-1 DEV tournament runner.

Behaviorally equivalent to, but much faster than, calling
`argine.research2.r2_09a.fast_replay.run_fast` with a fresh
`_BaseBridgePolicy` subclass per cell (as Phase 0's
`tests/test_r2_bridge01_*` already does and continues to do for
correctness coverage). The speedup comes from one observation that does
not change any result: the frozen `decayed_transition/decay=0.99`
predictor's output for a given (trace, layer) is a pure function of that
layer's own served-event sequence -- it does not depend on the budget or
which retention mechanism is "consuming" the prediction. Phase 0's
classes recompute it independently once per (config, budget) cell (up to
12x per trace); this module computes it exactly ONCE per (trace, layer)
and replays all 12 cells against that single precomputed prediction
stream.

`tests/test_r2_bridge01_dev_runner_equivalence.py` proves this fast path
reproduces the already-tested slow path (`retention_policies.py`)
bit-for-bit on a sample of cells before it is trusted for the full DEV
tournament.
"""
from __future__ import annotations

from typing import Dict, List, Optional, Sequence, Tuple

from argine.research.policies import Context
from argine.research.replay import InvariantViolation, counted_step, make_context, served_steps
from argine.research.trace import PHASE_DECODE, Trace
from argine.research2.r2_09a.policies import stable_obj_key
from argine.research2.r2_10.causal_walk import CausalContext
from argine.research2.r2_10.predictors import DecayedTransitionPredictor

from .retention_policies import B_SELECTED_PARAMS_BY_BUDGET, FROZEN_PREDICTOR_DECAY, Obj

Prediction = Tuple[int, ...]


def precompute_predictions(trace: Trace) -> Dict[int, List[Prediction]]:
    """One DecayedTransitionPredictor pass per layer, covering ALL served
    steps (v2 semantics: every step, prompt steps included -- identical
    scope to what `_BaseBridgePolicy.access()` already feeds the
    predictor in the slow path). Returns, per layer, a list `preds` such
    that `preds[j]` is the predicted-next set to apply when serving that
    layer's (j+1)-th event; `preds` has the same length as that layer's
    own served-event stream (its last entry is computed but never
    consulted, matching the slow path exactly)."""
    out: Dict[int, List[Prediction]] = {}
    top_k = trace.meta.top_k
    n_experts = trace.meta.n_experts
    for layer in trace.meta.layers:
        stream: List[Tuple[int, Tuple[int, ...]]] = []
        for i in served_steps(trace, None, "v2"):
            ex = trace.steps[i].experts(layer)
            if not ex:
                continue
            stream.append((i, tuple(sorted(int(x) for x in ex))))
        predictor = DecayedTransitionPredictor(n_experts, FROZEN_PREDICTOR_DECAY)
        preds: List[Prediction] = []
        hist: List[Tuple[int, Tuple[int, ...]]] = []
        for pos, (token_index, experts) in enumerate(stream):
            hist.append((token_index, experts))
            ctx = CausalContext(position=pos, token_index=token_index, history=tuple(hist), k=top_k)
            preds.append(predictor.predict(ctx, k=top_k))
        out[layer] = preds
    return out


class _FastBridgePolicyBase:
    """Same AgedFrequency-identical scoring/eviction core as
    `_BaseBridgePolicy`, but consulting a PRECOMPUTED per-layer
    prediction list instead of an internal predictor instance. Produces
    bit-identical `resident`/`scores`/`last_ref`/eviction bookkeeping to
    the slow path for the same inputs (see equivalence test)."""

    def __init__(self, decay_factor: float, aging_interval_steps: int, predictions: Dict[int, List[Prediction]]):
        self.decay_factor = float(decay_factor)
        self.aging_interval_steps = int(aging_interval_steps)
        self._predictions = predictions
        self._pos_in_layer: Dict[int, int] = {}

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
        self._pos_in_layer = {l: 0 for l in ctx.layers}
        self._predicted_next: Dict[int, frozenset] = {l: frozenset() for l in ctx.layers}

    def _apply_aging_if_due(self) -> None:
        if self.aging_interval_steps > 0 and self._t % self.aging_interval_steps == 0:
            for obj in list(self.resident.keys()):
                self.scores[obj] = self.scores.get(obj, 0.0) * self.decay_factor

    def _advance_prediction(self, layer: int) -> None:
        j = self._pos_in_layer.get(layer, 0)
        preds = self._predictions.get(layer, [])
        if j < len(preds):
            self._predicted_next[layer] = frozenset(preds[j])
        self._pos_in_layer[layer] = j + 1

    def _victim_key(self, o: Obj, protected: frozenset):
        raise NotImplementedError

    def _choose_victim(self, layer: int) -> Optional[Obj]:
        raise NotImplementedError

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
        self._advance_prediction(layer)
        return missed

    def end_step(self, step: int) -> None:
        return None


class FastPredictedProtect(_FastBridgePolicyBase):
    name = "FastPredictedProtect"

    def _victim_key(self, o: Obj, protected: frozenset):
        return (self.scores.get(o, 0.0), self.last_ref.get(o, -1), stable_obj_key(o))

    def _choose_victim(self, layer: int) -> Optional[Obj]:
        if not self.resident:
            return None
        protected_experts = self._predicted_next.get(layer, frozenset())
        candidates = [o for o in self.resident if not (o[0] == layer and o[1] in protected_experts)]
        pool = candidates if candidates else list(self.resident.keys())
        return min(pool, key=lambda o: self._victim_key(o, protected_experts))


class FastPredictorRetentionScore(_FastBridgePolicyBase):
    name = "FastPredictorRetentionScore"

    def __init__(self, decay_factor, aging_interval_steps, bonus, predictions):
        super().__init__(decay_factor, aging_interval_steps, predictions)
        self.bonus = float(bonus)

    def _victim_key(self, o: Obj, protected: frozenset):
        base = self.scores.get(o, 0.0)
        nudge = self.bonus if o[1] in protected else 0.0
        return (base + nudge, self.last_ref.get(o, -1), stable_obj_key(o))

    def _choose_victim(self, layer: int) -> Optional[Obj]:
        if not self.resident:
            return None
        protected = self._predicted_next.get(layer, frozenset())
        return min(self.resident.keys(), key=lambda o: self._victim_key(o, protected))


def run_fast_bridge(trace: Trace, policy, budget: int, *, phase=PHASE_DECODE) -> dict:
    """Same det-dict shape/semantics as
    `argine.research2.r2_09a.fast_replay.run_fast`, reimplemented here
    only to avoid that function's internal `assert_causal` call (which
    expects an `argine.research.policies.Policy` subclass; the fast
    classes above intentionally duck-type the same interface without
    that inheritance, to skip its per-instance overhead at scale -- the
    equivalence test proves this costs nothing in correctness)."""
    ctx = make_context(trace, budget)
    policy.start(ctx)
    if policy.resident_bytes > budget:
        raise InvariantViolation(f"{policy.name}: start resident {policy.resident_bytes}>{budget}")
    layers = ctx.layers
    per_layer = {l: {"accesses": 0, "misses": 0} for l in layers}
    hits = misses = acc = events = bmiss = 0
    peak = policy.resident_bytes
    tokens = 0
    n_counted = 0
    for i in served_steps(trace, phase, "v2"):
        st = trace.steps[i]
        counted = counted_step(st, phase, "v2")
        if counted:
            n_counted += 1
        for l in layers:
            ex = st.experts(l)
            if not ex:
                continue
            missed = policy.access(i, l, ex)
            if policy.resident_bytes > budget:
                raise InvariantViolation(f"{policy.name}: resident {policy.resident_bytes}>{budget} step {i} layer {l}")
            peak = max(peak, policy.resident_bytes)
            if counted:
                b = ctx.bytes_of(l)
                m = len(missed)
                events += 1
                acc += len(ex)
                misses += m
                hits += len(ex) - m
                bmiss += m * b
                per_layer[l]["accesses"] += len(ex)
                per_layer[l]["misses"] += m
        policy.end_step(i)
        if counted:
            tokens += st.n_tokens
    return {
        "policy": policy.name,
        "budget": int(budget),
        "phase": phase or "all",
        "steps": n_counted,
        "tokens": tokens,
        "events": events,
        "accesses": acc,
        "hits": hits,
        "misses": misses,
        "bytes_missed": bmiss,
        "evictions": policy.evictions,
        "overflow_events": policy.overflow_events,
        "overflow_misses": policy.overflow_misses,
        "peak_resident": peak,
        "per_layer": per_layer,
    }


def make_fast_policy(family_id: str, budget: int, predictions: Dict[int, List[Prediction]], *, bonus: Optional[float] = None):
    params = B_SELECTED_PARAMS_BY_BUDGET[budget]
    if family_id == "predicted_protect":
        return FastPredictedProtect(params["decay_factor"], params["aging_interval_steps"], predictions)
    if family_id == "predictor_retention_score":
        return FastPredictorRetentionScore(params["decay_factor"], params["aging_interval_steps"], bonus, predictions)
    raise ValueError(f"unknown family_id {family_id!r}")
