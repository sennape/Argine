"""Independent validation + recomputation of the R3-03B authoritative microbenchmark (run2).

Stdlib only. Does NOT import tools/r3_03b_microbench_analyze.py (independent re-derivation from raw rows/files).
  python validate_evidence.py --run-dir <R3-03B-microbench-run2> [--repo <Argine4>] [--out RESULT.json]
Exit 0 = every check passed.
"""
import argparse, hashlib, json, math, statistics as st, sys
from array import array
from pathlib import Path

POL = ("b_selected", "predicted_protect_sham", "predicted_protect")
EV, REPS, PROCS, TOKEN_CALLS = 24672, 30, 3, 48
LOGGED_EV = 41786
EXPECTED = {"git_head": "a476471c37dbed7a348ca703d64813d6872340bc", "exe_sha256": "d9eb52d7e5de77629da9bad9fa60b174c80598a7ad98aa741f82fb5eda00cbb3",
            "stream_sha256": "0224419aa9928ef6c3bcc1698eddfffcf1ec97a88893755f030b2cd5ff5690b6", "msc_full_ver": 194435229}


def sha(p):
    h = hashlib.sha256()
    with open(p, "rb") as f:
        for b in iter(lambda: f.read(1 << 20), b""):
            h.update(b)
    return h.hexdigest()


def _betacf(a, b, x):
    qab, qap, qam = a + b, a + 1, a - 1
    c, d = 1.0, 1 - qab * x / qap
    d = 1 / (d if abs(d) > 1e-300 else 1e-300); h = d
    for m in range(1, 400):
        m2 = 2 * m
        aa = m * (b - m) * x / ((qam + m2) * (a + m2)); d = 1 + aa * d; c = 1 + aa / c
        d = 1 / (d if abs(d) > 1e-300 else 1e-300); h *= d * c
        aa = -(a + m) * (qab + m) * x / ((a + m2) * (qap + m2)); d = 1 + aa * d; c = 1 + aa / c
        d = 1 / (d if abs(d) > 1e-300 else 1e-300); delta = d * c; h *= delta
        if abs(delta - 1) < 1e-14:
            break
    return h


def t_cdf(t, df):
    x = df / (df + t * t); a, b = df / 2, 0.5
    bt = math.exp(math.lgamma(a + b) - math.lgamma(a) - math.lgamma(b) + a * math.log(x) + b * math.log(1 - x))
    ib = bt * _betacf(a, b, x) / a if x < (a + 1) / (a + b + 2) else 1 - bt * _betacf(b, a, 1 - x) / b
    return 1 - 0.5 * ib if t > 0 else 0.5 * ib


def t975(df):
    lo, hi = 0.0, 100.0
    for _ in range(200):
        mid = (lo + hi) / 2
        lo, hi = (mid, hi) if t_cdf(mid, df) < 0.975 else (lo, mid)
    return (lo + hi) / 2


def u32(path):
    a = array("I")
    with open(path, "rb") as f:
        a.fromfile(f, Path(path).stat().st_size // 4)
    return a


def ci(x):
    n = len(x); m = st.fmean(x); s = st.stdev(x)
    return {"n": n, "mean": m, "median": st.median(x), "sd": s, "ci95_halfwidth": t975(n - 1) * s / math.sqrt(n)}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--run-dir", default=r"C:\Users\alpel\Desktop\Argine4-r3runs\R3-03B-microbench-run2")
    ap.add_argument("--repo", default=None); ap.add_argument("--out", default=None)
    ap.add_argument("--quick", action="store_true", help="verify only the committed host_records against EVIDENCE_MANIFEST.json")
    a = ap.parse_args()
    if a.quick:
        here = Path(__file__).resolve().parent; man = json.loads((here / "EVIDENCE_MANIFEST.json").read_text())
        bad = [k for k, h in man["host_records_sha256"].items() if sha(here / "host_records" / k) != h]
        print(json.dumps({"quick_ok": not bad, "files": len(man["host_records_sha256"]), "mismatch": bad}))
        return 0 if not bad else 1
    run = Path(a.run_dir); raw = run / "raw"; repo = Path(a.repo) if a.repo else Path(__file__).resolve().parents[2]
    checks = []

    def chk(name, ok, detail=""):
        checks.append({"check": name, "ok": bool(ok), "detail": detail})

    # --- identities
    chk("git_head", (run / "git_head.txt").read_text().strip() == EXPECTED["git_head"])
    chk("git_status_clean", (run / "git_status.txt").read_text().strip() == "")
    exe = run / "argine-policy-microbench-r3-03b.exe"
    chk("exe_sha256", sha(exe) == EXPECTED["exe_sha256"], sha(exe))
    chk("exe_sha_recorded", EXPECTED["exe_sha256"] in (run / "microbench_binary.sha256.txt").read_text())
    chk("stream_sha256", sha(repo / "experiments/R3-03B-direct-cost-attribution/routing_stream_A-4GiB.txt") == EXPECTED["stream_sha256"]
        and EXPECTED["stream_sha256"] in (run / "stream.sha256.txt").read_text())
    for rec, src in (("source_microbench", "patches/argine-policy-microbench.cpp"), ("source_policy", "patches/argine-retention-policy.cpp"),
                     ("source_policy_header", "patches/argine-retention-policy.h")):
        h = sha(repo / src)
        chk("source_sha_%s" % rec, h in (run / (rec + ".sha256.txt")).read_text(), h)
    cl = (run / "cl_version.txt").read_text(errors="replace")
    chk("compiler_19_44_35229", "19.44.35229" in cl)
    chk("compiler_flags", (run / "compiler_flags.txt").read_text().strip() == "cl /nologo /O2 /Ob2 /DNDEBUG /MT /EHsc /std:c++17 /arch:AVX2 /DNOMINMAX /W4")
    for i in (1, 2, 3):
        chk("proc%d_stdout_valid" % i, (run / ("proc%d.stdout.txt" % i)).read_text().strip() == "MICROBENCH_VALID proc=%d reps=30" % i)
        chk("proc%d_stderr_empty" % i, (run / ("proc%d.stderr.txt" % i)).stat().st_size == 0)

    # --- per-process structure
    rows_by_proc, ref_all = {}, []
    for p in (1, 2, 3):
        meta = json.loads((raw / ("p%d_meta.json" % p)).read_text())
        chk("p%d_meta" % p, meta["verdict"] == "VALID" and meta["proc"] == p and meta["reps"] == REPS and meta["warmup_replays_per_combo"] == 2
            and meta["events"] == EV and meta["logged_evictions"] == LOGGED_EV and meta["msc_full_ver"] == EXPECTED["msc_full_ver"]
            and meta["budget_bytes"] == 4294967296 and meta["decay"] == 0.99 and meta["interval"] == 64 and meta["predictor_decay"] == 0.99
            and meta["top_k"] == 8 and meta["n_experts"] == 128)
        ref = meta["reference_counts"]; ref_all.append(ref)
        chk("p%d_sham_equals_b_selected_counts" % p, ref["b_selected"] == ref["predicted_protect_sham"] and ref["b_selected"][2] == LOGGED_EV)
        rows = [json.loads(l) for l in (raw / ("p%d_replays.jsonl" % p)).read_text().splitlines()]
        chk("p%d_row_count" % p, len(rows) == 320, len(rows))
        # expected sequence
        exp = []
        for w in range(2):
            exp.append((-1, "-", "null", 1))
            for pi in range(3):
                for m in ("bulk", "outer", "inner"):
                    exp.append((-1, POL[pi], m, 1))
        for r in range(REPS):
            exp.append((r, "-", "null", 0))
            order = [r % 3, (r + 1) % 3, (r + 2) % 3]
            if r & 1:
                order[0], order[2] = order[2], order[0]
            for pi in order:
                ms = ["bulk", "outer", "inner"]
                if r & 1:
                    ms.reverse()
                for m in ms:
                    exp.append((r, POL[pi], m, 0))
        seq = [(x["rep"], x["policy"], x["mode"], x["warmup"]) for x in rows]
        chk("p%d_interleaving_and_warmup_sequence_exact" % p, seq == exp)
        chk("p%d_reps_contiguous_no_optional_stopping" % p, sorted({x["rep"] for x in rows if not x["warmup"]}) == list(range(REPS)))
        bad = [x for x in rows if x["mode"] != "null" and (x["hits"], x["misses"], x["evictions"]) != tuple(ref[x["policy"]])]
        chk("p%d_replay_invariants_all_rows" % p, not bad, "%d violations" % len(bad))
        chk("p%d_events_field" % p, all(x["events"] == EV for x in rows))
        # raw per-call files vs row totals (exact)
        ok = True
        for pol in POL:
            for m in ("outer", "inner"):
                f = raw / ("p%d_calls_%s_%s.u32" % (p, pol, m)); sz = f.stat().st_size
                if sz != REPS * EV * 4:
                    ok = False; continue
                arr = u32(f)
                recs = [x for x in rows if not x["warmup"] and x["policy"] == pol and x["mode"] == m]
                recs.sort(key=lambda x: x["rep"])
                for r, x in enumerate(recs):
                    if sum(arr[r * EV:(r + 1) * EV]) != x["total_ns"]:
                        ok = False
        chk("p%d_raw_percall_files_sum_equals_recorded_totals" % p, ok)
        for k in ("calibration_pairs", "calibration_ticks"):
            chk("p%d_%s_size" % (p, k), (raw / ("p%d_%s.u32" % (p, k))).stat().st_size == 4000000)
        rows_by_proc[p] = rows
    chk("reference_counts_identical_across_processes", ref_all[0] == ref_all[1] == ref_all[2])

    # --- independent recomputation (bulk - null per repetition)
    c_call = {pol: [] for pol in POL}; per_proc = {1: [], 2: [], 3: []}; per_rep_diff = []
    for p in (1, 2, 3):
        by = {}
        for x in rows_by_proc[p]:
            if not x["warmup"]:
                by[(x["rep"], x["policy"], x["mode"])] = x
        for r in range(REPS):
            null = by[(r, "-", "null")]["total_ns"]
            v = {pol: (by[(r, pol, "bulk")]["total_ns"] - null) / EV for pol in POL}
            for pol in POL:
                c_call[pol].append(v[pol])
            d = v[POL[1]] - v[POL[0]]; per_rep_diff.append(d); per_proc[p].append(d)
    res = {"events": EV, "reps_per_process": REPS, "processes": PROCS, "n_reps_total": len(per_rep_diff), "t975_df89": t975(89)}
    res["c_call_ns"] = {pol: ci(c_call[pol]) for pol in POL}
    for pol in POL:
        res["c_call_ns"][pol]["relative_halfwidth"] = res["c_call_ns"][pol]["ci95_halfwidth"] / res["c_call_ns"][pol]["mean"]
    res["c_predictor_ns_per_call"] = ci(per_rep_diff)
    res["c_predictor_ns_per_call"]["relative_precision"] = res["c_predictor_ns_per_call"]["ci95_halfwidth"] / res["c_predictor_ns_per_call"]["mean"]
    cp = res["c_predictor_ns_per_call"]
    res["c_predictor_token_ns"] = {"mean": cp["mean"] * TOKEN_CALLS, "ci95_halfwidth": cp["ci95_halfwidth"] * TOKEN_CALLS, "median": cp["median"] * TOKEN_CALLS, "sd": cp["sd"] * TOKEN_CALLS}
    pm = [st.fmean(per_proc[p]) for p in (1, 2, 3)]
    res["per_process_c_predictor_ns"] = {str(p): st.fmean(per_proc[p]) for p in (1, 2, 3)}
    res["per_process_sd_within"] = {str(p): st.stdev(per_proc[p]) for p in (1, 2, 3)}
    res["process_level_ci95"] = {"mean": st.fmean(pm), "sd_between_processes": st.stdev(pm), "ci95_halfwidth_df2": t975(2) * st.stdev(pm) / math.sqrt(3),
                                 "relative_halfwidth": t975(2) * st.stdev(pm) / math.sqrt(3) / st.fmean(pm), "max_min_spread_ns": max(pm) - min(pm)}
    # lag-1 autocorrelation within processes of per-rep differences
    ac = []
    for p in (1, 2, 3):
        x = per_proc[p]; m = st.fmean(x); num = sum((x[i] - m) * (x[i + 1] - m) for i in range(len(x) - 1)); den = sum((v - m) ** 2 for v in x)
        ac.append(num / den)
    res["lag1_autocorrelation_per_process"] = ac
    pf = [(c_call[POL[2]][i] - c_call[POL[1]][i]) for i in range(len(per_rep_diff))]
    res["full_policy_minus_sham_ns_per_call"] = ci(pf)
    res["full_policy_minus_sham_ms_per_token"] = {k: v * TOKEN_CALLS / 1e6 for k, v in res["full_policy_minus_sham_ns_per_call"].items() if k != "n"}
    # component attribution from inner rows
    comp = {}
    for pol in POL:
        tot = pu = vs = vc = n = 0
        for p in (1, 2, 3):
            for x in rows_by_proc[p]:
                if not x["warmup"] and x["policy"] == pol and x["mode"] == "inner":
                    tot += x["ns_policy_total"]; pu += x["ns_prediction_update"]; vs += x["ns_victim_selection"]; vc += x["victim_calls"]; n += 1
        calls = n * EV
        comp[pol] = {"prediction_update_share": pu / tot, "victim_selection_share": vs / tot, "other_share": 1 - pu / tot - vs / tot,
                     "ns_policy_total_per_call": tot / calls, "prediction_update_ns_per_call": pu / calls, "victim_selection_ns_per_call": vs / calls,
                     "other_ns_per_call": (tot - pu - vs) / calls, "victim_calls_per_event": vc / calls}
    res["components_inner_mode"] = comp
    # timer assessment
    outer_mean, inner_mean = {}, {}
    for pol in POL:
        so = si = 0
        for p in (1, 2, 3):
            so += sum(u32(raw / ("p%d_calls_%s_outer.u32" % (p, pol)))); si += sum(u32(raw / ("p%d_calls_%s_inner.u32" % (p, pol))))
        outer_mean[pol] = so / (PROCS * REPS * EV); inner_mean[pol] = si / (PROCS * REPS * EV)
    res["timer"] = {"per_call_outer_mean_ns": outer_mean, "outer_minus_bulk_c_call_ns": {pol: outer_mean[pol] - res["c_call_ns"][pol]["mean"] for pol in POL},
                    "inner_minus_outer_ns": {pol: inner_mean[pol] - outer_mean[pol] for pol in POL}}
    pairs = array("I"); ticks = array("I")
    for p in (1, 2, 3):
        pairs.extend(u32(raw / ("p%d_calibration_pairs.u32" % p))); ticks.extend(u32(raw / ("p%d_calibration_ticks.u32" % p)))
    res["timer"]["calibration_pairs_ns"] = {"n": len(pairs), "mean": st.fmean(pairs), "median": st.median(pairs), "min": min(pairs), "max": max(pairs)}
    res["timer"]["calibration_ticks_ns"] = {"n": len(ticks), "mean": st.fmean(ticks), "median": st.median(ticks), "min": min(ticks), "max": max(ticks)}
    res["timer"]["instrumentation_delta_difference_sham_minus_b_ns_per_call"] = res["timer"]["inner_minus_outer_ns"][POL[1]] - res["timer"]["inner_minus_outer_ns"][POL[0]]
    # compare with acquisition report
    rep = json.loads((run / "MICROBENCH_REPORT.json").read_text())
    def close(x, y): return abs(x - y) <= 1e-9 * max(1.0, abs(y))
    chk("report_c_call_matches_independent", all(close(res["c_call_ns"][p]["mean"], rep["c_call_ns"][p]["mean"]) and close(res["c_call_ns"][p]["sd"], rep["c_call_ns"][p]["sd"]) for p in POL))
    chk("report_c_predictor_matches_independent", close(cp["mean"], rep["c_predictor_ns_per_call"]["mean"]) and abs(cp["ci95_halfwidth"] - rep["c_predictor_ns_per_call"]["ci95_halfwidth"]) < 0.01)
    chk("report_token_matches_independent", close(res["c_predictor_token_ns"]["mean"], rep["c_predictor_token_ns"]["mean"]))
    chk("report_shares_match", all(abs(comp[p]["prediction_update_share"] - rep["inner"][p]["prediction_update_share"]) < 1e-9 for p in POL))
    chk("no_outlier_removal_all_90_reps_used", len(per_rep_diff) == 90 and all(len(c_call[p]) == 90 for p in POL))
    res["checks"] = checks; res["all_checks_passed"] = all(c["ok"] for c in checks)
    out = json.dumps(res, indent=1)
    if a.out:
        Path(a.out).write_text(out + "\n")
    print(json.dumps({"all_checks_passed": res["all_checks_passed"], "failed": [c for c in checks if not c["ok"]], "n_checks": len(checks)}, indent=1))
    return 0 if res["all_checks_passed"] else 1


if __name__ == "__main__":
    sys.exit(main())
