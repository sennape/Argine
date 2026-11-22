import sys, os, time, json, subprocess, threading
sys.path.insert(0, 'experiments/R4-01-cfdc-training-validation'); sys.path.insert(0, '.'); sys.path.insert(0, 'tools')
import phase1 as P
TOTAL = float(sys.argv[1]); t0 = time.time()
ids = [e["workload_id"] for e in P.splits()["VALIDATION"]["traces"]]
arms = ["A", "BELADY", "DECAYED_COUNT", "NUE_X", "CHEAP"]
done = lambda w, a: P._cache(f"valarm_{w}_{a}").exists()
tasks = [("fast", w) for w in ids if not all(done(w, a) for a in arms)] + [("rich", w) for w in ids if not done(w, "RICH")]
lock = threading.Lock(); claimed = set()
def worker():
    while True:
        with lock:
            nxt = next((t for t in tasks if t not in claimed), None)
            left = TOTAL - (time.time() - t0)
            if nxt is None or left < 25: return
            if nxt[0] == "fast" and left < 60: return
            claimed.add(nxt)
        kind, w = nxt
        if kind == "fast": cmd = ["python3", os.path.expanduser("~/drv2.py"), w, ",".join(arms)]
        else: cmd = ["python3", os.path.expanduser("~/drv3.py"), w, "RICH", str(max(10, left - 20))]
        r = subprocess.run(cmd, capture_output=True, text=True); print(kind, w, "rc", r.returncode, r.stdout.strip().splitlines()[-1:] , r.stderr.strip()[-200:], flush=True)
        if kind == "rich" and r.returncode == 3:
            with lock: claimed.discard(nxt)
            return
ths = [threading.Thread(target=worker) for _ in range(2)]
[t.start() for t in ths]; [t.join() for t in ths]
for w in ids:
    if all(done(w, a) for a in arms + ["RICH"]) and not P._cache("val_" + w).exists():
        e = next(x for x in P.splits()["VALIDATION"]["traces"] if x["workload_id"] == w)
        out = {"workload_id": w, "category": e["nominal_category"], "trace_sha256": e["trace_sha256"]}
        for a in ["A", "BELADY", "NUE_X", "DECAYED_COUNT", "CHEAP", "RICH"]: out[a] = P.jread(P._cache(f"valarm_{w}_{a}"))
        P._cache("val_" + w).write_text(P.dumps(out)); print("assembled", w)
print("complete traces:", sum(P._cache("val_" + w).exists() for w in ids), "of", len(ids), "arms cached:", sum(done(w, a) for w in ids for a in arms + ["RICH"]), "of", 6 * len(ids))
