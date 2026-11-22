"""Research 4 Phase 1 -- CFDC replay engine (exact, integer byte accounting, own-state replay).

Implements the frozen Phase 0 contracts (experiments/R4-00-causal-eviction-distillation): the 16 causal features, the
current-event shield with total fallback, the teacher (next-use) labels, and the policies A, NUE_X, DECAYED_COUNT,
CHEAP and RICH.  BELADY is the frozen argine.research.oracle.BeladyGlobal driven through an adapter.
No EVAL loader exists in this module.
"""
from __future__ import annotations

import bisect
import hashlib
import json
import math
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Sequence, Tuple

import numpy as np

HERE = Path(__file__).resolve().parent
REPO = HERE.parents[1]

NL, NE = 48, 128
K = NL * NE
BUDGET = 1610612736
SENT = 1_000_000
SAMPLE_EVERY = 64
Y_INF = 21.0
DECAY = 0.99
FEATURE_ORDER = ["events_since_last_ref", "decayed_count_fx", "events_since_admission", "hits_since_admission", "reload_count",
                 "prev_reuse_interval", "mean_reuse_interval", "in_prev_layer_event", "occupancy_permille", "pending_misses",
                 "recency_rank", "frequency_rank", "expert_bytes", "layer_id", "expert_id", "layer_cycle_offset"]
FID = {n: f"F{i + 1:02d}" for i, n in enumerate(FEATURE_ORDER)}


class EngineError(Exception):
    pass


def sha_bytes(b: bytes) -> str: return hashlib.sha256(b).hexdigest()


# ====================================================================== event streams
@dataclass
class EventStream:
    name: str
    step: List[int]
    layer: List[int]
    exp: List[Tuple[int, ...]]
    counted: List[bool]
    sizes: Dict[int, int]
    budget: int
    top_k: int
    n_experts: int
    tokens: int

    @property
    def n(self) -> int: return len(self.step)


def from_trace(trace, budget: int = BUDGET, name: str = "") -> EventStream:
    """Same served/counted semantics as r4.py:dev_replay (every step served; decode, non-warmup steps counted)."""
    from argine.research.replay import make_context, served_steps, counted_step
    from argine.research.trace import PHASE_DECODE
    ctx = make_context(trace, budget)
    step, layer, exp, counted = [], [], [], []
    tokens = 0
    for i in served_steps(trace, PHASE_DECODE, "v2"):
        st = trace.steps[i]
        c = counted_step(st, PHASE_DECODE, "v2")
        for l in ctx.layers:
            ex = st.experts(l)
            if not ex:
                continue
            e = tuple(sorted(set(int(x) for x in ex)))
            if tuple(int(x) for x in ex) != e:
                raise EngineError("trace event is not sorted/unique")
            step.append(i); layer.append(int(l)); exp.append(e); counted.append(bool(c))
        if c:
            tokens += st.n_tokens
    sizes = {int(l): int(ctx.bytes_of(l)) for l in ctx.layers}
    if len(step) >= 1 << 20:
        raise EngineError("trace has >= 2^20 served events")
    if list(ctx.layers) != list(range(NL)) or ctx.n_experts != NE:
        raise EngineError("unexpected layer/expert geometry")
    return EventStream(name, step, layer, exp, counted, sizes, int(budget), int(ctx.top_k), int(ctx.n_experts), tokens)


def from_canonical(rows: Sequence[Dict[str, Any]], first_decode: int, n_decode: int, budget: int = BUDGET, top_k: int = 8, n_experts: int = 128) -> EventStream:
    """The Research 1 canonical stream (24,672 events): decode events are those with i >= first_decode."""
    sizes = {int(r["layer"]): int(r["bytes"]) for r in rows}
    return EventStream("canonical", [int(r["i"]) for r in rows], [int(r["layer"]) for r in rows], [tuple(sorted(set(int(x) for x in r["req"]))) for r in rows],
                       [int(r["i"]) >= first_decode for r in rows], sizes, int(budget), top_k, n_experts, n_decode)


# ====================================================================== simulator
class Dec:
    """One eviction decision; features are computed lazily and cached (candidates only)."""
    __slots__ = ("sim", "c", "j", "i", "l", "rb", "pending", "cache", "fallback")

    def __init__(self, sim, c, j, i, l, rb, pending, fallback):
        self.sim, self.c, self.j, self.i, self.l, self.rb, self.pending, self.fallback, self.cache = sim, c, j, i, l, rb, pending, fallback, {}

    def sraw(self):
        """Exact S_k(i_now) (binary64): requests of the current event excluded."""
        v = self.cache.get("sraw")
        if v is None:
            s, c = self.sim, self.c
            cur = s.jlast[c] == self.j
            Sv = np.where(cur, s.Sp[c], s.S[c]); iv = np.where(cur, s.iSp[c], s.iS[c])
            v = Sv * s.POW[self.i - iv]
            self.cache["sraw"] = v
        return v

    def col(self, name: str) -> np.ndarray:
        v = self.cache.get(name)
        if v is not None:
            return v
        s, c, n = self.sim, self.c, len(self.c)
        if name == "events_since_last_ref": v = self.j - s.jlast[c]
        elif name == "decayed_count_fx": v = np.floor(65536.0 * self.sraw()).astype(np.int64)
        elif name == "events_since_admission": v = self.j - s.admj[c]
        elif name == "hits_since_admission": v = s.hits[c]
        elif name == "reload_count": v = s.adm[c] - 1
        elif name == "prev_reuse_interval": v = np.where(s.cnt_int[c] >= 1, s.prev_int[c], SENT)
        elif name == "mean_reuse_interval": v = np.where(s.cnt_int[c] >= 1, s.sum_int[c] // np.maximum(s.cnt_int[c], 1), SENT)
        elif name == "in_prev_layer_event": v = s.lastset[c].astype(np.int64)
        elif name == "occupancy_permille": v = np.full(n, (1000 * self.rb) // s.es.budget, np.int64)
        elif name == "pending_misses": v = np.full(n, self.pending, np.int64)
        elif name == "recency_rank":
            o = np.lexsort((c, -s.jlast[c])); v = np.empty(n, np.int64); v[o] = np.arange(n)
        elif name == "frequency_rank":
            o = np.lexsort((c, self.col("events_since_last_ref"), -self.col("decayed_count_fx"))); v = np.empty(n, np.int64); v[o] = np.arange(n)
        elif name == "expert_bytes": v = s.bytes_k[c]
        elif name == "layer_id": v = c // NE
        elif name == "expert_id": v = c % NE
        elif name == "layer_cycle_offset": v = ((c // NE - self.l - 1) % NL) + 1
        else: raise EngineError(f"unknown feature {name}")
        self.cache[name] = v
        return v

    def shielded(self) -> "Dec":
        """The frozen candidate set of the teacher/learned policies (residents not requested by the current event; total fallback), at this decision's state."""
        s = self.sim
        c = np.flatnonzero(s.res & ~s.incur); fb = False
        if len(c) == 0: c = np.flatnonzero(s.res); fb = True
        return Dec(s, c, self.j, self.i, self.l, self.rb, self.pending, fb)

    def matrix(self) -> np.ndarray:
        return np.stack([self.col(nm) for nm in FEATURE_ORDER], axis=1).astype(np.int64)


class Sim:
    """Event-atomic demand-admission replay; experts of an event processed in ascending id; hits update state before later misses are admitted."""

    def __init__(self, es: EventStream, policy, hook: Optional[Callable] = None, need_next_use: bool = False):
        self.es, self.pol, self.hook = es, policy, hook
        mx = max(es.step) + 2 if es.n else 2
        self.POW = np.array([DECAY ** n for n in range(mx + 1)], dtype=np.float64)
        self.bytes_k = np.zeros(K, np.int64)
        for l, b in es.sizes.items(): self.bytes_k[l * NE:(l + 1) * NE] = b
        self.res = np.zeros(K, bool); self.incur = np.zeros(K, bool)
        self.jlast = np.full(K, -1, np.int64); self.S = np.zeros(K); self.iS = np.zeros(K, np.int64); self.Sp = np.zeros(K); self.iSp = np.zeros(K, np.int64)
        self.nreq = np.zeros(K, np.int64); self.lastn = np.zeros(K, np.int64); self.prev_int = np.zeros(K, np.int64); self.sum_int = np.zeros(K, np.int64); self.cnt_int = np.zeros(K, np.int64)
        self.admj = np.zeros(K, np.int64); self.hits = np.zeros(K, np.int64); self.adm = np.zeros(K, np.int64)
        self.lastset = np.zeros(K, bool); self.nl = [0] * NL
        self.rb = 0; self.nres = 0; self.dec_idx = 0; self.fallbacks = 0
        self.pos: Optional[Dict[int, List[int]]] = None
        if need_next_use:
            self.pos = {}
            for j in range(es.n):
                l = es.layer[j]
                for e in es.exp[j]: self.pos.setdefault(l * NE + e, []).append(j)

    def next_use_distance(self, k: int, j: int) -> float:
        p = self.pos.get(int(k), []); x = bisect.bisect_right(p, j)
        return float("inf") if x >= len(p) else float(p[x] - j)

    # -------------------------------------------------- state transitions
    def _request(self, k: int, j: int, i: int, l: int) -> None:
        self.Sp[k] = self.S[k]; self.iSp[k] = self.iS[k]
        self.S[k] = self.S[k] * self.POW[i - self.iS[k]] + 1.0; self.iS[k] = i
        if self.nreq[k] >= 1:
            iv = self.nl[l] - self.lastn[k]; self.prev_int[k] = iv; self.sum_int[k] += iv; self.cnt_int[k] += 1
        self.lastn[k] = self.nl[l]; self.nreq[k] += 1; self.jlast[k] = j

    def run(self) -> Dict[str, Any]:
        es, pol = self.es, self.pol
        pol.start(self)
        dm = dh = dmb = dacc = dev = dtok = dec_ev = 0; ev_all = 0; peak = viol = 0; reloads = 0; plm = [0] * NL
        for j in range(es.n):
            i, l, ex, counted = es.step[j], es.layer[j], es.exp[j], es.counted[j]
            sz = es.sizes[l]; keys = [l * NE + e for e in ex]
            for k in keys: self.incur[k] = True
            miss = 0; ev0 = ev_all
            for idx, k in enumerate(keys):
                pol.pre_access(self)
                if self.res[k]:
                    self._request(k, j, i, l); self.hits[k] += 1; pol.on_hit(self, k)
                    continue
                miss += 1
                while self.rb + sz > es.budget and self.nres > 0:
                    c = np.flatnonzero(self.res & ~self.incur) if pol.shield else np.flatnonzero(self.res)
                    fb = False
                    if pol.shield and len(c) == 0:
                        c = np.flatnonzero(self.res); fb = True; self.fallbacks += 1
                    pending = 1 + sum(1 for k2 in keys[idx + 1:] if not self.res[k2])
                    dec = Dec(self, c, j, i, l, self.rb, pending, fb)
                    v = int(pol.choose(self, dec))
                    if self.hook is not None and self.dec_idx % SAMPLE_EVERY == 0: self.hook(self, dec, v)
                    self.dec_idx += 1
                    self.res[v] = False; self.rb -= int(self.bytes_k[v]); self.nres -= 1; ev_all += 1; pol.on_evict(self, v)
                self._request(k, j, i, l)
                if self.rb + sz <= es.budget and sz <= es.budget:
                    reloads += int(counted and self.adm[k] >= 1); self.res[k] = True; self.rb += sz; self.nres += 1; self.admj[k] = j; self.hits[k] = 0; self.adm[k] += 1; pol.on_admit(self, k)
                else:
                    self.overflow = getattr(self, "overflow", 0) + 1
            # end of event
            self.lastset[l * NE:(l + 1) * NE] = False
            for k in keys: self.lastset[k] = True; self.incur[k] = False
            self.nl[l] += 1
            pol.end_event(self, j, i, l, ex)
            peak = max(peak, self.rb); viol += self.rb > es.budget
            if counted:
                dm += miss; dh += len(ex) - miss; dmb += miss * sz; dacc += len(ex); dev += 1; dec_ev += ev_all - ev0; plm[l] += miss
        return {"decode": {"misses": dm, "hits": dh, "miss_bytes": dmb, "accesses": dacc, "events": dev, "tokens": es.tokens, "evictions": dec_ev},
                "evictions_total": ev_all, "peak_resident_bytes": peak, "budget_violations": viol, "overflow_events": getattr(self, "overflow", 0),
                "overflow_misses": getattr(self, "overflow", 0), "decisions": self.dec_idx, "shield_total_fallbacks": self.fallbacks, "events": es.n, "decode_reloads": reloads, "decode_misses_per_layer": plm}


# ====================================================================== policies
class _Tick:
    shield = False
    def pre_access(self, s): pass
    def on_hit(self, s, k): pass
    def on_admit(self, s, k): pass
    def on_evict(self, s, k): pass
    def end_event(self, s, j, i, l, ex): pass


class PolA(_Tick):
    """Frozen reference b_selected = AgedFrequency(decay 0.999, aging interval 1); victim = min (score, last_ref, (layer, expert)) over ALL residents."""
    name = "A"; shield = False
    def __init__(self, decay: float = 0.999, interval: int = 1): self.decay, self.interval = float(decay), int(interval)
    def start(self, s):
        self.sc = np.zeros(K); self.last = np.zeros(K, np.int64); self.t = 0
    def pre_access(self, s):
        self.t += 1
        if self.interval > 0 and self.t % self.interval == 0: np.multiply(self.sc, self.decay, out=self.sc, where=s.res)
    def on_hit(self, s, k): self.sc[k] += 1.0; self.last[k] = self.t
    def on_admit(self, s, k): self.sc[k] = 1.0; self.last[k] = self.t
    def on_evict(self, s, k): self.sc[k] = 0.0
    def choose(self, s, d):
        c = d.c; return c[np.lexsort((c, self.last[c], self.sc[c]))[0]]


class PolNUEX(PolA):
    """Research 3 NUE_X (LEX): A's aged score + decayed_transition(0.99, top_k) next-event prediction of the candidate's own layer; shield with total fallback.
    victim = min (in_pred, score, last_ref, layer, expert). The predictor is the frozen R2-10 module (argine.research2.r2_10)."""
    name = "NUE_X"; shield = True
    def start(self, s):
        super().start(s)
        from argine.research2.r2_10.causal_walk import CausalContext
        from argine.research2.r2_10.predictors import DecayedTransitionPredictor
        self._CC = CausalContext; self.pred = {}; self.hist = {}; self.pin = np.zeros(K, bool); self.mk = DecayedTransitionPredictor; self.top_k = s.es.top_k; self.n_experts = s.es.n_experts
    def end_event(self, s, j, i, l, ex):
        h = self.hist.setdefault(l, []); h.append((i, tuple(sorted(ex))))
        p = self.pred.get(l)
        if p is None: p = self.pred[l] = self.mk(self.n_experts, 0.99)
        out = p.predict(self._CC(position=len(h) - 1, token_index=i, history=tuple(h), k=self.top_k), k=self.top_k)
        self.pin[l * NE:(l + 1) * NE] = False
        for e in frozenset(out): self.pin[l * NE + int(e)] = True
    def choose(self, s, d):
        c = d.c; return c[np.lexsort((c, self.last[c], self.sc[c], self.pin[c].astype(np.int64)))[0]]


class PolDC(_Tick):
    """DECAYED_COUNT: evict the lowest exact S_k (decay 0.99 per served step); ties: larger events_since_last_ref, layer, expert."""
    name = "DECAYED_COUNT"; shield = True; decay = DECAY
    def start(self, s): pass
    def choose(self, s, d):
        c = d.c; return c[np.lexsort((c, -d.col("events_since_last_ref"), d.sraw()))[0]]


class PolCheap(_Tick):
    """CHEAP: integer decision tree (arrays); evict the highest leaf rank; ties: larger events_since_last_ref, layer, expert."""
    name = "CHEAP"; shield = True
    def __init__(self, tree: Dict[str, Any]): self.t = tree; self.names = tree["features_used"]
    def start(self, s): pass
    def rank(self, d: Dec) -> np.ndarray:
        t = self.t; n = len(d.c); node = np.zeros(n, np.int64)
        X = {nm: d.col(nm) for nm in self.names}
        for _ in range(t["max_depth"]):
            f = np.array(t["feature"])[node]; internal = f >= 0
            if not internal.any(): break
            go_left = np.zeros(n, bool)
            for fi, nm in enumerate(self.names):
                m = internal & (f == fi); thr = np.array(t["threshold"])[node]
                go_left[m] = X[nm][m] <= thr[m]
            nxt = np.where(go_left, np.array(t["left"])[node], np.array(t["right"])[node])
            node = np.where(internal, nxt, node)
        return np.array(t["leaf_rank"])[node]
    def choose(self, s, d):
        c = d.c; r = self.rank(d); return c[np.lexsort((c, -d.col("events_since_last_ref"), -r))[0]]


class PolRich(_Tick):
    name = "RICH"; shield = True
    def __init__(self, model): self.m = model
    def start(self, s): pass
    def choose(self, s, d):
        c = d.c; q = self.m.predict_q(d.matrix()); return c[np.lexsort((c, -d.col("events_since_last_ref"), -q))[0]]


# ====================================================================== frozen BeladyGlobal adapter
def run_belady(es: EventStream) -> Dict[str, Any]:
    """The frozen argine.research.oracle.BeladyGlobal driven through the same event list and accounting (as experiments/R3-05/.../r2.py does)."""
    from argine.research.oracle import BeladyGlobal, next_uses
    from argine.research.policies import Context
    b = BeladyGlobal.__new__(BeladyGlobal)
    b.events = [(es.step[j], es.layer[j], es.exp[j]) for j in range(es.n)]; b.nu = next_uses(b.events)
    b.start(Context(layers=sorted(es.sizes), n_experts=es.n_experts, size=dict(es.sizes), budget=es.budget, top_k=es.top_k))
    dm = dh = dmb = dacc = dev = dec_ev = 0; peak = viol = 0; reloads = 0; plm = [0] * NL; seen = set()
    for j in range(es.n):
        ev0 = b.evictions; missed = b.access(es.step[j], es.layer[j], es.exp[j])
        l = es.layer[j]; keys = [l * NE + e for e in missed]
        peak = max(peak, b.resident_bytes); viol += b.resident_bytes > es.budget
        if es.counted[j]:
            m = len(missed); dm += m; dh += len(es.exp[j]) - m; dmb += m * es.sizes[es.layer[j]]; dacc += len(es.exp[j]); dev += 1; dec_ev += b.evictions - ev0; reloads += sum(1 for k in keys if k in seen); plm[l] += m
        seen.update(keys)
    return {"decode_reloads": reloads, "decode_misses_per_layer": plm, "decode": {"misses": dm, "hits": dh, "miss_bytes": dmb, "accesses": dacc, "events": dev, "tokens": es.tokens, "evictions": dec_ev}, "evictions_total": b.evictions,
            "peak_resident_bytes": peak, "budget_violations": viol, "overflow_events": b.overflow_events, "overflow_misses": b.overflow_misses, "events": es.n}


# ====================================================================== TRAIN teacher labels (A trajectory)
def teacher_rows(es: EventStream, probe=None) -> Dict[str, Any]:
    """Replay A; at every 64th eviction decision (numbered over all served events) emit all shielded candidate rows: X (16 causal features) and
    y = log2(1+d), 21.0 if d = inf (d = next-use distance in served events). `probe` (optional policy) is asked for its victim on the same candidates (non-gating shift diagnostic)."""
    X_parts, y_parts, dec_ids = [], [], []
    stats = {"sampled_decisions": 0, "rows": 0, "fallback_decisions": 0, "inf_rows": 0, "a_victim_is_teacher_max": 0, "probe_agree_under_A_states": 0}

    def hook(sim, dec, v):
        d2 = dec.shielded(); c = d2.c
        d = np.array([sim.next_use_distance(int(k), dec.j) for k in c])
        y = np.where(np.isinf(d), Y_INF, np.log2(1.0 + np.where(np.isinf(d), 0.0, d)))
        X_parts.append(d2.matrix().astype(np.int32)); y_parts.append(y); dec_ids.append(np.full(len(c), sim.dec_idx, np.int64))
        stats["sampled_decisions"] += 1; stats["rows"] += len(c); stats["fallback_decisions"] += int(d2.fallback); stats["inf_rows"] += int(np.isinf(d).sum())
        mx = float(np.max(d)); stats["a_victim_is_teacher_max"] += int(v in set(c.tolist()) and d[int(np.flatnonzero(c == v)[0])] == mx)
        if probe is not None:
            stats["probe_agree_under_A_states"] += int(d[int(np.flatnonzero(c == probe.choose(sim, d2))[0])] == mx)

    sim = Sim(es, PolA(), hook=hook, need_next_use=True); res = sim.run()
    X = np.concatenate(X_parts) if X_parts else np.zeros((0, 16), np.int32)
    y = np.concatenate(y_parts) if y_parts else np.zeros(0); g = np.concatenate(dec_ids) if dec_ids else np.zeros(0, np.int64)
    return {"X": X, "y": y, "g": g, "stats": stats, "replay": res}


def agreement_run(es: EventStream, policy) -> Dict[str, Any]:
    """Run `policy` on its own cache states; at every 64th decision compare its victim's next-use distance with the teacher maximum (non-gating shift diagnostic)."""
    agree = []
    def hook(sim, dec, v):
        c = dec.c; d = np.array([sim.next_use_distance(int(k), dec.j) for k in c])
        agree.append(bool(sim.next_use_distance(v, dec.j) == np.max(d)))
    r = Sim(es, policy, hook=hook, need_next_use=True).run()
    return {"sampled": len(agree), "agree": int(sum(agree)), "agreement": (sum(agree) / len(agree)) if agree else None, "replay": r}
