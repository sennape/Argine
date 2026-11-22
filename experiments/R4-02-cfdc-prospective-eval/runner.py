"""Research 4 Phase 2 -- execution runner. A resumable copy of the frozen Phase 1 replay loop (engine.Sim.run) that additionally records the
decode miss bytes of every counted step (needed only for the preregistered non-gating block bootstrap). It changes no policy behaviour: it imports the frozen
engine/models and is tested to return exactly the Phase 1 monolithic results (tests/test_phase2.py). Checkpoint/resume is exact and only technical."""
from __future__ import annotations

import os
import pickle
import sys
import time
from pathlib import Path
from typing import Any, Dict, Optional

HERE = Path(__file__).resolve().parent
P1 = HERE.parent / "R4-01-cfdc-training-validation"
sys.path.insert(0, str(P1)); sys.path.insert(0, str(HERE.parents[1]))
import numpy as np  # noqa: E402
import engine as G  # noqa: E402

NL, NE = G.NL, G.NE
ARR = ["res", "incur", "jlast", "S", "iS", "Sp", "iSp", "nreq", "lastn", "prev_int", "sum_int", "cnt_int", "admj", "hits", "adm", "lastset"]
SCAL = ["rb", "nres", "dec_idx", "fallbacks"]
RESUMABLE = ("RICH", "CHEAP", "DECAYED_COUNT")      # policies whose start() is a no-op; other arms are always run in a single execution


def run_arm(es, pol, ck: Optional[str] = None, budget_s: float = 1e12) -> Optional[Dict[str, Any]]:
    sim = G.Sim(es, pol); t0 = time.time()
    st = {"j": 0, "acc": dict(dm=0, dh=0, dmb=0, dacc=0, dev=0, dec_ev=0, ev_all=0, peak=0, viol=0, reloads=0, plm=[0] * NL, sb={}), "nl": [0] * NL, "overflow": 0, "scal": {k: 0 for k in SCAL}, "arr": None, "chunks": 0}
    if ck and os.path.exists(ck): st = pickle.load(open(ck, "rb"))
    if st["arr"] is not None:
        for k in ARR: setattr(sim, k, st["arr"][k])
        sim.nl = st["nl"]; sim.overflow = st["overflow"]
        for k in SCAL: setattr(sim, k, st["scal"][k])
    a = st["acc"]; pol.start(sim)
    dm, dh, dmb, dacc, dev, dec_ev, ev_all, peak, viol, reloads, plm, sb = (a[k] for k in ("dm", "dh", "dmb", "dacc", "dev", "dec_ev", "ev_all", "peak", "viol", "reloads", "plm", "sb"))
    j = st["j"]
    while j < es.n:
        if j % 200 == 0 and j > st["j"] and time.time() - t0 > budget_s: break
        i, l, ex, counted = es.step[j], es.layer[j], es.exp[j], es.counted[j]
        sz = es.sizes[l]; keys = [l * NE + e for e in ex]
        for k in keys: sim.incur[k] = True
        miss = 0; ev0 = ev_all
        for idx, k in enumerate(keys):
            pol.pre_access(sim)
            if sim.res[k]:
                sim._request(k, j, i, l); sim.hits[k] += 1; pol.on_hit(sim, k); continue
            miss += 1
            while sim.rb + sz > es.budget and sim.nres > 0:
                c = np.flatnonzero(sim.res & ~sim.incur) if pol.shield else np.flatnonzero(sim.res)
                fb = False
                if pol.shield and len(c) == 0:
                    c = np.flatnonzero(sim.res); fb = True; sim.fallbacks += 1
                pending = 1 + sum(1 for k2 in keys[idx + 1:] if not sim.res[k2])
                dec = G.Dec(sim, c, j, i, l, sim.rb, pending, fb)
                v = int(pol.choose(sim, dec)); sim.dec_idx += 1
                sim.res[v] = False; sim.rb -= int(sim.bytes_k[v]); sim.nres -= 1; ev_all += 1; pol.on_evict(sim, v)
            sim._request(k, j, i, l)
            if sim.rb + sz <= es.budget and sz <= es.budget:
                reloads += int(counted and sim.adm[k] >= 1); sim.res[k] = True; sim.rb += sz; sim.nres += 1; sim.admj[k] = j; sim.hits[k] = 0; sim.adm[k] += 1; pol.on_admit(sim, k)
            else: sim.overflow = getattr(sim, "overflow", 0) + 1
        sim.lastset[l * NE:(l + 1) * NE] = False
        for k in keys: sim.lastset[k] = True; sim.incur[k] = False
        sim.nl[l] += 1; pol.end_event(sim, j, i, l, ex)
        peak = max(peak, sim.rb); viol += sim.rb > es.budget
        if counted:
            dm += miss; dh += len(ex) - miss; dmb += miss * sz; dacc += len(ex); dev += 1; dec_ev += ev_all - ev0; plm[l] += miss; sb[i] = sb.get(i, 0) + miss * sz
        j += 1
    acc = dict(dm=dm, dh=dh, dmb=dmb, dacc=dacc, dev=dev, dec_ev=dec_ev, ev_all=ev_all, peak=peak, viol=viol, reloads=reloads, plm=plm, sb=sb)
    if j < es.n:
        if not ck: raise RuntimeError("time budget exhausted without a checkpoint path")
        pickle.dump({"j": j, "acc": acc, "nl": sim.nl, "overflow": getattr(sim, "overflow", 0), "scal": {k: getattr(sim, k) for k in SCAL}, "arr": {k: getattr(sim, k) for k in ARR}, "chunks": st["chunks"] + 1}, open(ck + ".tmp", "wb")); os.replace(ck + ".tmp", ck)
        return None
    return {"decode": {"misses": dm, "hits": dh, "miss_bytes": dmb, "accesses": dacc, "events": dev, "tokens": es.tokens, "evictions": dec_ev}, "evictions_total": ev_all, "peak_resident_bytes": peak, "budget_violations": viol,
            "overflow_events": getattr(sim, "overflow", 0), "overflow_misses": getattr(sim, "overflow", 0), "decisions": sim.dec_idx, "shield_total_fallbacks": sim.fallbacks, "events": es.n, "decode_reloads": reloads,
            "decode_misses_per_layer": plm, "step_miss_bytes": sorted(sb.items()), "chunks": st["chunks"] + 1}


def run_belady_steps(es) -> Dict[str, Any]:
    """The frozen BeladyGlobal driven exactly as engine.run_belady, additionally recording per-step decode miss bytes."""
    from argine.research.oracle import BeladyGlobal, next_uses
    from argine.research.policies import Context
    b = BeladyGlobal.__new__(BeladyGlobal)
    b.events = [(es.step[j], es.layer[j], es.exp[j]) for j in range(es.n)]; b.nu = next_uses(b.events)
    b.start(Context(layers=sorted(es.sizes), n_experts=es.n_experts, size=dict(es.sizes), budget=es.budget, top_k=es.top_k))
    dm = dh = dmb = dacc = dev = dec_ev = 0; peak = viol = 0; reloads = 0; plm = [0] * NL; seen = set(); sb: Dict[int, int] = {}
    for j in range(es.n):
        ev0 = b.evictions; missed = b.access(es.step[j], es.layer[j], es.exp[j]); l = es.layer[j]; keys = [l * NE + e for e in missed]
        peak = max(peak, b.resident_bytes); viol += b.resident_bytes > es.budget
        if es.counted[j]:
            m = len(missed); dm += m; dh += len(es.exp[j]) - m; dmb += m * es.sizes[l]; dacc += len(es.exp[j]); dev += 1; dec_ev += b.evictions - ev0; reloads += sum(1 for k in keys if k in seen); plm[l] += m
            sb[es.step[j]] = sb.get(es.step[j], 0) + m * es.sizes[l]
        seen.update(keys)
    return {"decode_reloads": reloads, "decode_misses_per_layer": plm, "decode": {"misses": dm, "hits": dh, "miss_bytes": dmb, "accesses": dacc, "events": dev, "tokens": es.tokens, "evictions": dec_ev}, "evictions_total": b.evictions,
            "peak_resident_bytes": peak, "budget_violations": viol, "overflow_events": b.overflow_events, "overflow_misses": b.overflow_misses, "events": es.n, "step_miss_bytes": sorted(sb.items()), "chunks": 1}
