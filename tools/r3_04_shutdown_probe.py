"""R3-04 model-free shutdown probe. Starts a tiny stand-in console child (no model, no llama-server) that records when its Ctrl-C handler
runs, then compares Ctrl-C delivery latency of the legacy R3-03B method against the own-console helper. Tests hypothesis H1 only
(console-event delivery); it says nothing about llama-server-specific stalls (H2).

  python tools\\r3_04_shutdown_probe.py --trials 20 --out probe.json          (Windows)
  python tools\\r3_04_shutdown_probe.py --self-test --trials 3                 (any OS, SIGINT path; logic check only)
"""
from __future__ import annotations

import argparse
import json
import os
import statistics as st
import subprocess
import sys
import tempfile
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import r3_04_shutdown as S  # noqa: E402

CHILD = r'''
import signal, sys, time
ready, fired = sys.argv[1], sys.argv[2]
def h(*a):
    open(fired, "w").write(repr(time.time())); sys.exit(0)
signal.signal(signal.SIGINT, h)
open(ready, "w").write("1")
while True: time.sleep(0.05)
'''


def one_trial(method: str, timeout_s: float, tmp: Path) -> dict:
    ready, fired = tmp / "ready", tmp / "fired"
    for p in (ready, fired):
        p.unlink(missing_ok=True)
    proc = subprocess.Popen([sys.executable, "-c", CHILD, str(ready), str(fired)], creationflags=S.popen_creationflags(method) if os.name == "nt" else 0)
    try:
        t_end = time.monotonic() + 30
        while not ready.exists():
            if time.monotonic() > t_end or proc.poll() is not None:
                return {"method": method, "error": "child not ready"}
            time.sleep(0.05)
        t_req = time.time()
        res = S.stop_server(proc, timeout_s, method)
        lat = (float(fired.read_text()) - t_req) if fired.exists() else None
        return {"method": method, "mode": res["mode"], "delivery_latency_s": lat, "stop_latency_s": res["latency_s"], "helper_returncode": res["helper_returncode"]}
    finally:
        if proc.poll() is None:
            proc.kill()


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--trials", type=int, default=20); ap.add_argument("--timeout", type=float, default=120.0)
    ap.add_argument("--out"); ap.add_argument("--self-test", action="store_true")
    a = ap.parse_args(argv)
    if os.name != "nt" and not a.self_test:
        print("REFUSED: the probe is meaningful only on Windows (use --self-test for a logic check)"); return 1
    methods = ("legacy",) if os.name != "nt" else S.METHODS
    rows = []
    with tempfile.TemporaryDirectory() as d:
        for i in range(a.trials):
            for m in methods:
                rows.append(one_trial(m, a.timeout, Path(d)))
    summ = {}
    for m in methods:
        v = [r["delivery_latency_s"] for r in rows if r["method"] == m and r.get("delivery_latency_s") is not None]
        summ[m] = {"n": len(v), "failed": sum(1 for r in rows if r["method"] == m) - len(v),
                   "median_s": st.median(v) if v else None, "max_s": max(v) if v else None, "over_5s": sum(1 for x in v if x > 5)}
    out = {"artifact": "R3-04-SHUTDOWN-PROBE", "model_free": True, "tests": "H1 (console Ctrl-event delivery) only", "trials": a.trials, "summary": summ, "rows": rows}
    if a.out:
        Path(a.out).write_text(json.dumps(out, indent=1), encoding="utf-8")
    print(json.dumps(summ, indent=1))
    return 0


if __name__ == "__main__":
    sys.exit(main())
