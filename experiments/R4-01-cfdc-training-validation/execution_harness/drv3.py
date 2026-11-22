"""Execution harness (scratch): resumable copy of engine.Sim.run's loop for policies whose start() is a no-op (DC, CHEAP, RICH).
It exists only because one shell call is limited to 3 minutes; it is validated against the monolithic Sim.run (identical result_sha256)."""
import sys, json, time, pickle, os
sys.path.insert(0, 'experiments/R4-01-cfdc-training-validation'); sys.path.insert(0, '.'); sys.path.insert(0, 'tools')
import numpy as np
import phase1 as P, engine as G, models as M
from engine import *

ARR = ["res", "incur", "jlast", "S", "iS", "Sp", "iSp", "nreq", "lastn", "prev_int", "sum_int", "cnt_int", "admj", "hits", "adm", "lastset"]
SCAL = ["rb", "nres", "dec_idx", "fallbacks"]

def chunked(es, pol, ck, budget_s):
    sim = G.Sim(es, pol); t0 = time.time()
    st = {"j": 0, "acc": dict(dm=0, dh=0, dmb=0, dacc=0, dev=0, dec_ev=0, ev_all=0, peak=0, viol=0, reloads=0, plm=[0] * NL), "nl": [0] * NL, "overflow": 0, "scal": {k: 0 for k in SCAL}, "arr": None}
    if os.path.exists(ck): st = pickle.load(open(ck, "rb"))
    if st["arr"] is not None:
        for k in ARR: setattr(sim, k, st["arr"][k])
        sim.nl = st["nl"]; sim.overflow = st["overflow"]
        for k in SCAL: setattr(sim, k, st["scal"][k])
    a = st["acc"]; pol.start(sim)
    dm, dh, dmb, dacc, dev, dec_ev, ev_all, peak, viol, reloads, plm = (a[k] for k in ("dm", "dh", "dmb", "dacc", "dev", "dec_ev", "ev_all", "peak", "viol", "reloads", "plm"))
    j = st["j"]
    while j < es.n:
        if j % 200 == 0 and time.time() - t0 > budget_s: break
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
        if counted: dm += miss; dh += len(ex) - miss; dmb += miss * sz; dacc += len(ex); dev += 1; dec_ev += ev_all - ev0; plm[l] += miss
        j += 1
    acc = dict(dm=dm, dh=dh, dmb=dmb, dacc=dacc, dev=dev, dec_ev=dec_ev, ev_all=ev_all, peak=peak, viol=viol, reloads=reloads, plm=plm)
    if j < es.n:
        pickle.dump({"j": j, "acc": acc, "nl": sim.nl, "overflow": getattr(sim, "overflow", 0), "scal": {k: getattr(sim, k) for k in SCAL}, "arr": {k: getattr(sim, k) for k in ARR}}, open(ck + ".tmp", "wb")); os.replace(ck + ".tmp", ck)
        return None
    return {"decode": {"misses": dm, "hits": dh, "miss_bytes": dmb, "accesses": dacc, "events": dev, "tokens": es.tokens, "evictions": dec_ev}, "evictions_total": ev_all, "peak_resident_bytes": peak, "budget_violations": viol, "overflow_events": getattr(sim, "overflow", 0),
            "overflow_misses": getattr(sim, "overflow", 0), "decisions": sim.dec_idx, "shield_total_fallbacks": sim.fallbacks, "events": es.n, "decode_reloads": reloads, "decode_misses_per_layer": plm}

if __name__ == "__main__":
    wid, nm, budget = sys.argv[1], sys.argv[2], float(sys.argv[3])
    e = next(x for x in P.splits()["VALIDATION"]["traces"] if x["workload_id"] == wid)
    cheap = P.jread(P.HERE / "CHEAP_MODEL.json"); rich = M.RichModel.from_json(P.jread(P.HERE / "RICH_MODEL.json"))
    pol = {"RICH": lambda: G.PolRich(rich), "CHEAP": lambda: G.PolCheap(cheap["tree"]), "DECAYED_COUNT": lambda: G.PolDC()}[nm]()
    out = P._cache(f"valarm_{wid}_{nm}"); suffix = os.environ.get("DRV_TAG", "")
    if suffix: out = P._cache(f"check{suffix}_{wid}_{nm}")
    if out.exists(): print(wid, nm, "cached"); sys.exit(0)
    es = G.from_trace(P.load_trace("VALIDATION", e), G.BUDGET, e["workload_id"]); ck = str(P.WORK / "cache" / f"ckpt_{suffix}{wid}_{nm}.pkl")
    t = time.time(); r = chunked(es, pol, ck, budget)
    if r is None: print(wid, nm, "partial", round(time.time() - t, 1)); sys.exit(3)
    r["seconds"] = None; r["backend"] = rich.backend if nm == "RICH" else "numpy"
    r["result_sha256"] = P.sha_bytes(P.canon({k: v for k, v in r.items() if k not in ("seconds", "backend")})); r["seconds"] = "chunked"
    out.write_text(P.dumps(r)); print(wid, nm, "done", r["result_sha256"][:12])
