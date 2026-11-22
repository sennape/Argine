import sys, json, time
sys.path.insert(0, 'experiments/R4-01-cfdc-training-validation'); sys.path.insert(0, '.'); sys.path.insert(0, 'tools')
import phase1 as P, engine as G, models as M
wid, arms = sys.argv[1], sys.argv[2].split(",")
e = next(x for x in P.splits()["VALIDATION"]["traces"] if x["workload_id"] == wid)
cheap = P.jread(P.HERE / "CHEAP_MODEL.json"); rich = M.RichModel.from_json(P.jread(P.HERE / "RICH_MODEL.json"))
es = None
for nm in arms:
    f = P._cache(f"valarm_{wid}_{nm}")
    if f.exists(): print(wid, nm, "cached"); continue
    if es is None: es = G.from_trace(P.load_trace("VALIDATION", e), G.BUDGET, e["workload_id"])
    runs = {"A": lambda: G.Sim(es, G.PolA()).run(), "BELADY": lambda: G.run_belady(es), "NUE_X": lambda: G.Sim(es, G.PolNUEX()).run(), "DECAYED_COUNT": lambda: G.Sim(es, G.PolDC()).run(),
            "CHEAP": lambda: G.Sim(es, G.PolCheap(cheap["tree"])).run(), "RICH": lambda: G.Sim(es, G.PolRich(rich)).run()}
    t = time.time(); r = runs[nm](); r["seconds"] = round(time.time() - t, 1); r["backend"] = rich.backend if nm == "RICH" else "numpy"
    r["result_sha256"] = P.sha_bytes(P.canon({k: v for k, v in r.items() if k not in ("seconds", "backend")}))
    f.write_text(P.dumps(r)); print(wid, nm, "done in", r["seconds"], "s", flush=True)
if all(P._cache(f"valarm_{wid}_{a}").exists() for a in ["A", "BELADY", "NUE_X", "DECAYED_COUNT", "CHEAP", "RICH"]):
    out = {"workload_id": wid, "category": e["nominal_category"], "trace_sha256": e["trace_sha256"]}
    for a in ["A", "BELADY", "NUE_X", "DECAYED_COUNT", "CHEAP", "RICH"]: out[a] = P.jread(P._cache(f"valarm_{wid}_{a}"))
    P._cache("val_" + wid).write_text(P.dumps(out)); print(wid, "assembled")
