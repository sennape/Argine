"""R2-09A preregistered causal baseline policies under frozen R2-09B v1.1 semantics.

Implements: Global-LRU (verified reuse), LFU (verified reuse), 2Q, LRU-K, Aged/Decayed Frequency.
All policies: byte-capacity-aware, deterministic, causal (no future info), cold start empty,
global causal access ordinal for recency/history/tie-breaking metadata, stable (layer,expert_id)
final tie-break where specified. Compatible with argine.research.replay / Policy interface.
"""
from __future__ import annotations

import math
from collections import OrderedDict, deque
from dataclasses import dataclass, field
from typing import Dict, Iterable, List, Optional, Sequence, Set, Tuple

from ...research.policies import GlobalLRU as _GlobalLRUBase
from ...research.policies import LFU as _LFUBase
from ...research.policies import Policy, Context


Obj = Tuple[int, int]  # (layer, expert)

# --- helpers ---

def stable_obj_key(obj: Obj) -> Tuple[int, int]:
    return (int(obj[0]), int(obj[1]))


def median_expert_bytes(trace_meta_expert_bytes: Dict[int, int]) -> int:
    vals = sorted(int(v) for v in trace_meta_expert_bytes.values() if int(v) > 0)
    if not vals:
        return 1
    m = len(vals) // 2
    if len(vals) % 2:
        return vals[m]
    # deterministic integer median (lower of middle pair would also be deterministic; use floor average)
    return (vals[m - 1] + vals[m]) // 2


def s_ref_from_trace(trace) -> int:
    eb = getattr(getattr(trace, "meta", None), "expert_bytes", None) or {}
    vals = [int(v) for v in eb.values() if int(v) > 0]
    if not vals:
        return 1
    sv = sorted(vals)
    m = len(sv) // 2
    if len(sv) % 2:
        return sv[m]
    # mean fallback? contract states median; if even count floor average deterministic
    return (sv[m - 1] + sv[m]) // 2


def assert_trace_topk(trace) -> int:
    k = getattr(getattr(trace, "meta", None), "top_k", None)
    if k is None:
        # derive from first decode event if needed
        for st in getattr(trace, "steps", []):
            for l in getattr(trace, "layers", []):
                ex = st.experts(l)
                if ex:
                    return len(ex)
        return 0
    return int(k)


# --- Global LRU (exact) ---

class GlobalLRU(_GlobalLRUBase):
    """R2-09A Global-LRU: reuse Part 1 GlobalLRU semantics (global byte LRU, deterministic).
    Contract: eviction by least recent global causal access ordinal; ties by stable (layer,expert_id).
    Part 1 GlobalLRU uses OrderedDict move_to_end on access and pops first (LRU); access order gives
    global recency ordinal semantics; on simultaneous required objects replay processes in layer/expert
    deterministic order and Policy.access is per object as served, preserving deterministic state.
    """
    VERSION = "r2-09a-v1-global-lru"

    def __init__(self):
        super().__init__()
        self._causal = True

    @property
    def causal(self) -> bool:
        return True

    def describe(self):
        d = super().describe()
        d.update({"family_id": "global_lru", "version": self.VERSION, "causal": True,
                  "eviction": "least_recent_global_access_ordinal", "tie_break": "(layer,expert_id)_asc_final"})
        return d


# --- LFU (exact, deterministic ties frozen) ---

class LFU(_LFUBase):
    """R2-09A LFU: cumulative causal access count per object while tracked; on first admission count=1.
    Eviction: lowest frequency; tie-break 1 least recent global access ordinal (LRU among ties);
    tie-break 2 stable (layer,expert_id). Cold start empty.
    Part 1 LFU implements (count, last_touch) min and touches last_touch on access; global touch from
    monotonic access order in replay provides causal ordinal semantics. in_cache_only=False, halflife=0.
    """
    VERSION = "r2-09a-v1-lfu"

    def __init__(self):
        super().__init__(halflife=0, in_cache_only=False)
        self._causal = True

    @property
    def causal(self) -> bool:
        return True

    def describe(self):
        d = super().describe()
        d.update({"family_id": "lfu", "version": self.VERSION, "causal": True,
                  "frequency": "cumulative_causal_access_count",
                  "eviction": "lowest_frequency_then_lru_global_ordinal_then_stable_layer_expert",
                  "tie_break": "lru_global_ordinal_then_(layer,expert_id)_asc"})
        return d


# --- 2Q ---

class TwoQ(Policy):
    VERSION = "r2-09a-v1-twoq"

    def __init__(self, a1in_fraction: float, a1out_slots_factor: int):
        self.a1in_fraction = float(a1in_fraction)
        self.a1out_slots_factor = int(a1out_slots_factor)
        self._causal = True

    @property
    def causal(self) -> bool:
        return True

    @property
    def name(self) -> str:
        return f"2Q(a1in={self.a1in_fraction},a1out={self.a1out_slots_factor})"

    def start(self, ctx: Context) -> None:
        self.ctx = ctx
        self.M = int(ctx.budget)
        self.M_a1in = int(math.floor(self.M * self.a1in_fraction))
        self.M_am = self.M - self.M_a1in
        # A1in: FIFO resident (OrderedDict insertion order = FIFO; oldest = first)
        self.a1in: "OrderedDict[Obj,int]" = OrderedDict()
        self.a1in_bytes = 0
        # Am: LRU resident (OrderedDict LRU: least recent at front)
        self.am: "OrderedDict[Obj,int]" = OrderedDict()
        self.am_bytes = 0
        # A1out ghost: keys only, FIFO oldest at front
        self.a1out: "OrderedDict[Obj,None]" = OrderedDict()
        self.s_ref = self._compute_s_ref()
        self.n_ref = max(1, self.M // self.s_ref) if self.s_ref > 0 else 1
        self.G = max(1, int(math.floor(self.n_ref * self.a1out_slots_factor)))
        self.resident_bytes = 0
        self.evictions = 0
        self.overflow_events = 0
        self.overflow_misses = 0
        self._ordinal = 0

    def _compute_s_ref(self) -> int:
        # S_ref = median trace expert bytes across layers (deterministic).
        vals = sorted(int(self.ctx.bytes_of(l)) for l in self.ctx.layers if int(self.ctx.bytes_of(l)) > 0)
        if not vals:
            vals = sorted({int(v) for v in (self.ctx.size or {}).values() if int(v) > 0})
        if not vals:
            return 1
        m = len(vals) // 2
        return vals[m] if len(vals) % 2 else (vals[m - 1] + vals[m]) // 2

    def _remove_resident(self, obj: Obj) -> None:
        if obj in self.a1in:
            sz = self.a1in.pop(obj)
            self.a1in_bytes -= sz
        elif obj in self.am:
            sz = self.am.pop(obj)
            self.am_bytes -= sz
        else:
            return
        self.resident_bytes -= sz

    def _evict_a1in_one(self) -> Optional[Obj]:
        if not self.a1in:
            return None
        obj, sz = next(iter(self.a1in.items()))  # FIFO oldest
        self.a1in.pop(obj)
        self.a1in_bytes -= sz
        self.resident_bytes -= sz
        self.evictions += 1
        # insert key into A1out ghost
        if obj in self.a1out:
            self.a1out.pop(obj)
        self.a1out[obj] = None
        while len(self.a1out) > self.G:
            self.a1out.popitem(last=False)
        return obj

    def _evict_am_one(self) -> Optional[Obj]:
        if not self.am:
            return None
        # LRU: least recent at front. tie-break shouldn't occur by ordinal; final stable (layer,expert)
        obj, sz = next(iter(self.am.items()))
        self.am.pop(obj)
        self.am_bytes -= sz
        self.resident_bytes -= sz
        self.evictions += 1
        return obj

    def _ensure_fit_am(self, need: int) -> None:
        while self.am_bytes + need > self.M_am and self.am:
            self._evict_am_one()

    def _ensure_fit_a1in(self, need: int) -> None:
        while self.a1in_bytes + need > self.M_a1in and self.a1in:
            self._evict_a1in_one()

    def access(self, step: int, layer: int, experts: Sequence[int]) -> List[int]:
        # process in deterministic stable (layer,expert_id) order as required for simultaneous objects
        missed: List[int] = []
        for e in sorted(int(x) for x in experts):
            self._ordinal += 1
            obj = (int(layer), e)
            sz = int(self.ctx.bytes_of(layer))
            if obj in self.am:
                # hit in Am -> LRU update
                self.am.move_to_end(obj, last=True)
                continue
            if obj in self.a1in:
                # hit in A1in -> no promotion, no FIFO reorder
                continue
            # miss
            missed.append(e)
            if obj in self.a1out:
                # ghost hit: remove ghost, admit to Am
                self.a1out.pop(obj, None)
                self._ensure_fit_am(sz)
                if self.am_bytes + sz <= self.M_am and self.resident_bytes + sz <= self.M and sz <= self.M_am:
                    self.am[obj] = sz
                    self.am_bytes += sz
                    self.resident_bytes += sz
                else:
                    # cannot fit even after eviction (object larger than partition or capacity issues)
                    self.overflow_events += 1
                    self.overflow_misses += 1
            else:
                # miss without ghost: admit to A1in
                self._ensure_fit_a1in(sz)
                if self.a1in_bytes + sz <= self.M_a1in and self.resident_bytes + sz <= self.M and sz <= self.M_a1in:
                    self.a1in[obj] = sz
                    self.a1in_bytes += sz
                    self.resident_bytes += sz
                else:
                    self.overflow_events += 1
                    self.overflow_misses += 1
        return missed

    def end_step(self, step: int) -> None:
        return None

    def describe(self):
        return {
            "family_id": "twoq", "version": self.VERSION, "causal": True,
            "a1in_fraction": self.a1in_fraction, "a1out_slots_factor": self.a1out_slots_factor,
            "main_policy": "LRU", "M_a1in": getattr(self, 'M_a1in', None), "M_am": getattr(self, 'M_am', None),
            "S_ref": getattr(self, 's_ref', None), "N_ref": getattr(self, 'n_ref', None), "G": getattr(self, 'G', None),
        }


# --- LRU-K ---

class LRUK(Policy):
    VERSION = "r2-09a-v1-lru-k"

    def __init__(self, K: int, correlated_reference_period_objects: int):
        self.K = int(K)
        self.crp = int(correlated_reference_period_objects)
        self._causal = True

    @property
    def causal(self) -> bool:
        return True

    @property
    def name(self) -> str:
        return f"LRU-K(K={self.K},crp={self.crp})"

    def start(self, ctx: Context) -> None:
        self.ctx = ctx
        self.M = int(ctx.budget)
        self.resident: Dict[Obj, int] = {}
        self.resident_bytes = 0
        # history of last K reference ordinals (deque, right = most recent)
        self.hist: Dict[Obj, deque] = {}
        self.last_recorded: Dict[Obj, int] = {}
        self.last_ref: Dict[Obj, int] = {}
        self.evictions = 0
        self.overflow_events = 0
        self.overflow_misses = 0
        self._t = 0

    def _record_ref(self, obj: Obj, t: int) -> None:
        lr = self.last_recorded.get(obj)
        if lr is not None and self.crp > 0 and (t - lr) <= self.crp:
            # correlated reference suppression window: ignore update for LRU-K history
            self.last_ref[obj] = t
            return
        dq = self.hist.get(obj)
        if dq is None:
            dq = deque(maxlen=self.K)
            self.hist[obj] = dq
        dq.append(t)
        self.last_recorded[obj] = t
        self.last_ref[obj] = t

    def _backward_k_distance(self, obj: Obj, now: int) -> Tuple[int, int, Tuple[int, int]]:
        dq = self.hist.get(obj)
        # insufficient history: treat missing Kth reference as -infinity/oldest sentinel (eviction-eligible)
        if dq is None or len(dq) < self.K:
            kth = -10**18
        else:
            kth = dq[0]  # oldest among last K
        last = self.last_ref.get(obj, -10**18)
        return (kth, last, stable_obj_key(obj))

    def _choose_victim(self) -> Optional[Obj]:
        if not self.resident:
            return None
        # victim = smallest backward K-distance (oldest Kth). tie-break oldest last reference, then stable key
        return min(self.resident.keys(), key=lambda o: self._backward_k_distance(o, self._t))

    def _evict_one(self) -> bool:
        v = self._choose_victim()
        if v is None:
            return False
        sz = self.resident.pop(v)
        self.resident_bytes -= sz
        self.evictions += 1
        # keep history? histories may be retained for tracked objects; safe to keep (causal metadata) but not necessary.
        return True

    def access(self, step: int, layer: int, experts: Sequence[int]) -> List[int]:
        missed: List[int] = []
        for e in sorted(int(x) for x in experts):
            self._t += 1
            obj = (int(layer), e)
            sz = int(self.ctx.bytes_of(layer))
            self._record_ref(obj, self._t)
            if obj in self.resident:
                continue
            missed.append(e)
            # evict until fit
            while self.resident_bytes + sz > self.M and self.resident:
                self._evict_one()
            if self.resident_bytes + sz <= self.M and sz <= self.M:
                self.resident[obj] = sz
                self.resident_bytes += sz
            else:
                self.overflow_events += 1
                self.overflow_misses += 1
        return missed

    def end_step(self, step: int) -> None:
        return None

    def describe(self):
        return {"family_id": "lru_k", "version": self.VERSION, "causal": True,
                "K": self.K, "correlated_reference_period_objects": self.crp,
                "history_for_new_object": "current_ordinal_padded/sentinel_for_insufficient_history"}


# --- Aged / Decayed Frequency ---

class AgedFrequency(Policy):
    VERSION = "r2-09a-v1-aged-frequency"

    def __init__(self, decay_factor: float, aging_interval_steps: int):
        self.decay_factor = float(decay_factor)
        self.aging_interval_steps = int(aging_interval_steps)
        self._causal = True

    @property
    def causal(self) -> bool:
        return True

    @property
    def name(self) -> str:
        return f"AgedFreq(decay={self.decay_factor},interval={self.aging_interval_steps})"

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

    def _apply_aging_if_due(self) -> None:
        # every aging_interval_steps global causal access ordinals, apply decay to all tracked resident objects
        if self.aging_interval_steps > 0 and self._t % self.aging_interval_steps == 0:
            for obj in list(self.resident.keys()):
                self.scores[obj] = self.scores.get(obj, 0.0) * self.decay_factor

    def _choose_victim(self) -> Optional[Obj]:
        if not self.resident:
            return None
        # lowest score; tie-break least recent global access ordinal, then stable (layer,expert_id)
        def key(o: Obj):
            return (self.scores.get(o, 0.0), self.last_ref.get(o, -1), stable_obj_key(o))
        return min(self.resident.keys(), key=key)

    def _evict_one(self) -> bool:
        v = self._choose_victim()
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
        for e in sorted(int(x) for x in experts):
            self._t += 1
            self._apply_aging_if_due()
            obj = (int(layer), e)
            sz = int(self.ctx.bytes_of(layer))
            # admission access counts: after scheduled aging for current step, increment/set S(obj):=S(obj)+1.0
            prev = self.scores.get(obj, 0.0) if obj in self.resident else 0.0
            if obj not in self.resident:
                missed.append(e)
                while self.resident_bytes + sz > self.M and self.resident:
                    self._evict_one()
                if self.resident_bytes + sz <= self.M and sz <= self.M:
                    self.resident[obj] = sz
                    self.resident_bytes += sz
                    self.scores[obj] = prev + 1.0
                    self.last_ref[obj] = self._t
                else:
                    self.overflow_events += 1
                    self.overflow_misses += 1
                continue
            # hit
            self.scores[obj] = self.scores.get(obj, 0.0) + 1.0
            self.last_ref[obj] = self._t
        return missed

    def end_step(self, step: int) -> None:
        return None

    def describe(self):
        return {"family_id": "aged_frequency", "version": self.VERSION, "causal": True,
                "decay_factor": self.decay_factor, "aging_interval_steps": self.aging_interval_steps,
                "decay_form": "exponential_step_decay"}


# --- config space ---

def build_tournament_configs() -> List[dict]:
    cfgs = []
    # Global-LRU
    cfgs.append({"family_id": "global_lru", "config_id": "global_lru/default", "params": {}, "factory": lambda: GlobalLRU()})
    # LFU
    cfgs.append({"family_id": "lfu", "config_id": "lfu/default", "params": {"halflife": 0, "in_cache_only": False, "tie_break": "lru_global_ordinal_then_stable_layer_expert"}, "factory": lambda: LFU()})
    # 2Q 3x3
    for a1 in [0.10, 0.25, 0.50]:
        for a1out in [1, 2, 4]:
            cfgs.append({"family_id": "twoq", "config_id": f"twoq/a1in={a1:.2f},a1out={a1out},main=lru", "params": {"a1in_fraction": a1, "a1out_slots_factor": a1out, "main_policy": "LRU"}, "factory": (lambda a1=a1,a1out=a1out: TwoQ(a1,a1out))})
    # LRU-K 2x3
    for K in [2,3]:
        for crp in [0,1,2]:
            cfgs.append({"family_id": "lru_k", "config_id": f"lru_k/K={K},crp={crp}", "params": {"K": K, "correlated_reference_period_objects": crp}, "factory": (lambda K=K,crp=crp: LRUK(K,crp))})
    # aged frequency 3x3
    for d in [0.90, 0.99, 0.999]:
        for iv in [1,16,64]:
            cfgs.append({"family_id": "aged_frequency", "config_id": f"aged_frequency/decay={d},interval={iv}", "params": {"decay_factor": d, "aging_interval_steps": iv, "decay_form": "exponential_step_decay"}, "factory": (lambda d=d,iv=iv: AgedFrequency(d,iv))})
    return cfgs

PRIMARY_BUDGETS = [1610612736, 4294967296, 8589934592]
PRIMARY_BUDGET_LABELS = {1610612736: "1.5 GiB", 4294967296: "4 GiB", 8589934592: "8 GiB"}
