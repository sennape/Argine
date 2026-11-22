"""R3-03B microbenchmark analysis (protocol: experiments/R3-03B-direct-cost-attribution/PROTOCOL.json).

Reads the raw files written by patches/argine-policy-microbench.cpp (p<N>_replays.jsonl, p<N>_calls_*.u32,
p<N>_calibration_*.u32, p<N>_meta.json) from one run directory and prints a JSON report. No outlier removal,
no trimming. Recorded (non-warmup) replays only.
"""
import argparse, json, math, statistics, struct, sys
from array import array
from pathlib import Path

POL = ("b_selected", "predicted_protect_sham", "predicted_protect")


def t975(df):
    try:
        from scipy import stats  # optional
        return float(stats.t.ppf(0.975, df))
    except Exception:  # Cornish-Fisher style approximation, adequate for df >= 10
        z = 1.959963984540054
        return z + (z**3 + z) / (4 * df) + (5 * z**5 + 16 * z**3 + 3 * z) / (96 * df**2)


def quant(x, p):
    s = sorted(x)
    return s[min(len(s) - 1, int(p * (len(s) - 1) + 0.5))]


def desc(x):
    return {"n": len(x), "mean": statistics.fmean(x), "median": statistics.median(x),
            "sd": statistics.stdev(x) if len(x) > 1 else 0.0, "p90": quant(x, .9), "p99": quant(x, .99),
            "p999": quant(x, .999), "max": max(x)}


def load_u32(path):
    a = array("I")
    with open(path, "rb") as f:
        a.fromfile(f, Path(path).stat().st_size // 4)
    return a


def analyse(run_dir, expected_procs=None, expected_reps=None):
    run_dir = Path(run_dir)
    metas = sorted(run_dir.glob("p*_meta.json"))
    if not metas:
        raise SystemExit("no meta files: run invalid or incomplete")
    if expected_procs is not None and len(metas) != expected_procs:
        raise SystemExit("process count %d != %d: run invalid" % (len(metas), expected_procs))
    rows = []
    for m in metas:
        meta = json.loads(m.read_text())
        if meta.get("verdict") != "VALID":
            raise SystemExit("%s not VALID" % m)
        if expected_reps is not None and meta["reps"] != expected_reps:
            raise SystemExit("repetition count mismatch: run invalid")
        pre = m.name[: -len("meta.json")]
        for line in (run_dir / (pre + "replays.jsonl")).read_text().splitlines():
            r = json.loads(line)
            if not r["warmup"]:
                rows.append(r)
    ev = rows[0]["events"]
    per = {}  # (proc, rep) -> dict
    for r in rows:
        per.setdefault((r["proc"], r["rep"]), {})[(r["policy"], r["mode"])] = r
    c_call = {p: [] for p in POL}
    for k, d in sorted(per.items()):
        null = d[("-", "null")]["total_ns"]
        for p in POL:
            c_call[p].append((d[(p, "bulk")]["total_ns"] - null) / ev)
    diff = [s - b for s, b in zip(c_call[POL[1]], c_call[POL[0]])]
    n = len(diff)
    mean = statistics.fmean(diff); sd = statistics.stdev(diff)
    ci = t975(n - 1) * sd / math.sqrt(n)
    rep = {"schema": "r3-03b-microbench-report-v1", "events": ev, "n_reps_total": n,
           "c_call_ns": {p: desc(c_call[p]) for p in POL},
           "c_predictor_ns_per_call": {"mean": mean, "sd": sd, "ci95_halfwidth": ci},
           "c_predictor_token_ns": {"mean": mean * 48, "ci95_halfwidth": ci * 48},
           "per_process_c_predictor_ns": {}}
    for proc in sorted({k[0] for k in per}):
        dd = [s - b for (pp, _), s, b in zip(sorted(per), c_call[POL[1]], c_call[POL[0]]) if pp == proc]
        rep["per_process_c_predictor_ns"][str(proc)] = statistics.fmean(dd)
    # per-call distributions, token-equivalent sums, timer overhead, instrumentation overhead, shares
    pass_of = None
    rep["per_call_outer_ns"] = {}; rep["inner"] = {}; rep["timer_overhead_ns_per_call"] = {}
    for p in POL:
        outer = array("I"); inner = array("I")
        for m in metas:
            pre = m.name[: -len("meta.json")]
            outer.extend(load_u32(run_dir / (pre + "calls_%s_outer.u32" % p)))
            inner.extend(load_u32(run_dir / (pre + "calls_%s_inner.u32" % p)))
        rep["per_call_outer_ns"][p] = desc(list(outer))
        rep["timer_overhead_ns_per_call"][p] = {"outer_mean_minus_bulk_c_call": statistics.fmean(outer) - statistics.fmean(c_call[p]),
                                                "inner_mean_minus_outer_mean": statistics.fmean(inner) - statistics.fmean(outer)}
        tot = sum(r["ns_policy_total"] for r in rows if r["policy"] == p and r["mode"] == "inner")
        pu = sum(r["ns_prediction_update"] for r in rows if r["policy"] == p and r["mode"] == "inner")
        vs = sum(r["ns_victim_selection"] for r in rows if r["policy"] == p and r["mode"] == "inner")
        rep["inner"][p] = {"prediction_update_share": pu / tot if tot else 0, "victim_selection_share": vs / tot if tot else 0}
    cal = {}
    for kind in ("pairs", "ticks"):
        v = array("I")
        for m in metas:
            v.extend(load_u32(run_dir / (m.name[: -len("meta.json")] + "calibration_%s.u32" % kind)))
        cal[kind] = desc(list(v)) | {"min": min(v)}
    rep["timer_calibration_ns"] = cal
    return rep


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("run_dir"); ap.add_argument("--expected-procs", type=int); ap.add_argument("--expected-reps", type=int)
    a = ap.parse_args()
    print(json.dumps(analyse(a.run_dir, a.expected_procs, a.expected_reps), indent=1))
