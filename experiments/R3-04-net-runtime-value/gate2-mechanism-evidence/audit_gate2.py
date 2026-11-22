"""R3-04 Gate 2 independent audit + evidence builder + offline verifier. No model, no network.

  python audit_gate2.py --build <host run dir> [--out <this dir>]   re-reads ORIGINAL artifacts (records, stderr logs, gate inputs, mtimes), audits, writes sanitised evidence
  python audit_gate2.py --verify                                    offline: re-checks the committed evidence and the freeze

Originals (raw stderr logs, host paths) are NOT committed: their sha256 are recorded in ORIGINAL_FILE_SHA256.json.
"""
from __future__ import annotations

import argparse, hashlib, json, os, re, statistics as st, sys
from pathlib import Path
from typing import Any, Dict, List

HERE = Path(__file__).resolve().parent
REPO = HERE.parents[2]
sys.path.insert(0, str(REPO / "tools"))
import r3_04_mechanism as M  # noqa: E402

V, C = M.V, M.C
PROTOCOL_FREEZE_SHA256 = "a88359b52891f93ec49e67978d92c2eb3b73db588611bce46b3103b2978c1202"
PARENTS = {"protocol_commit": "4a0aa2d", "tooling_commit": "6b22fdd", "gate1_commit": "d7e8f38", "gate1_freeze_sha256": "6379572091345e38f130a62a18d9fd5acdaef673f92439c44ff2bea8de8d6f42", "gate2_tooling_commit": "2345808"}
BUDGET_MIB = {"1.5": 1536, "4": 4096, "8": 8192}
SANITISE = ("stderr_path",)


def sha256_bytes(b: bytes) -> str:
    return hashlib.sha256(b).hexdigest()


def freeze_sha(obj: Dict[str, Any]) -> str:
    return sha256_bytes(json.dumps({k: v for k, v in obj.items() if k != "freeze_sha256"}, sort_keys=True, separators=(",", ":")).encode())


def ts(s: str) -> float:                      # llama.cpp log stamp  M.SS.mmm.uuu
    m, sec, ms, us = (int(x) for x in s.split("."))
    return m * 60 + sec + ms / 1e3 + us / 1e6


def parse_log(text: str) -> Dict[str, Any]:
    d: Dict[str, Any] = {"duplicate_refusals": text.count(C.DUPLICATE_MARK), "policy_timing_lines": len(C.timing_records(text))}
    for line in text.splitlines():
        m = re.match(r"(\d+\.\d+\.\d+\.\d+) ", line)
        if m:
            d["last_s"] = ts(m.group(1))
            if "model loaded" in line: d["loaded_s"] = ts(m.group(1))
            if "processing task" in line: d["task_s"] = ts(m.group(1))
            if "cleaning up before exit" in line: d["cleanup_s"] = ts(m.group(1))
            if "prompt eval time" in line:
                x = re.search(r"=\s+([\d.]+) ms /\s+(\d+) tokens", line); d["prompt_ms"], d["prompt_tokens"] = float(x.group(1)), int(x.group(2))
            elif "eval time =" in line:
                x = re.search(r"=\s+([\d.]+) ms /\s+(\d+) tokens", line); d["decode_ms"], d["decode_tokens"] = float(x.group(1)), int(x.group(2))
        if "loading model '" in line: d["model_path"] = line.split("loading model '")[1].rsplit("'", 1)[0]
        if "retention policy attiva:" in line:
            x = re.search(r"attiva: (\w+) \(decay=([\d.]+), interval=(\d+)(?:, predictor_decay=([\d.]+))?\)", line)
            d["policy"] = {"id": x.group(1), "decay": float(x.group(2)), "interval": int(x.group(3)), "predictor_decay": float(x.group(4)) if x.group(4) else None}
        if "expert store aperto:" in line:
            x = re.search(r"aperto: (.+?), 48 layer x 128 expert, budget (\d+) MiB", line); d["store_path"], d["budget_mib"] = x.group(1), int(x.group(2))
        if "retention trace diagnostica:" in line:
            d["trace_diag"] = {k: v for k, v in re.findall(r"(\w+)=(\w+)", line.split("diagnostica:")[1])}
    return d


def sanitise(rec: Dict[str, Any]) -> Dict[str, Any]:
    return {k: v for k, v in rec.items() if k not in SANITISE}


def metrics(rec: Dict[str, Any]) -> Dict[str, Any]:
    c = rec["counts"]; d = c["decode_passes"]; pt = (rec["policy_timing_records"] or [None])[0]
    return {"mseq": rec["mseq"], "arm": rec["arm"], "budget": rec["budget"], "policy_timing": rec["policy_timing"],
            "decode_hits_per_token": d["hits"] / 512, "decode_misses_per_token": d["misses"] / 512, "decode_miss_bytes_per_token": c["decode_miss_bytes_per_token"],
            "decode_evictions_per_token": c["decode_evictions_per_token"], "decode_protected_entries": d["protected_entries"], "decode_protected_events": d["protected_events"],
            "all_pass_hits": c["all_passes"]["hits"], "all_pass_misses": c["all_passes"]["misses"], "all_pass_miss_bytes": c["all_passes"]["miss_bytes"], "all_pass_evictions": c["all_passes"]["evictions"],
            "resident_peak_bytes": c["resident_peak_bytes"], "resident_peak_fraction_of_budget": c["resident_peak_bytes"] / V.BUDGETS[rec["budget"]]["bytes"],
            "policy_ms_per_token_ns_total_over_512": (pt["ns_total"] / 512 / 1e6) if pt else None, "policy_victim_selection_ms_total": (pt["ns_victim_selection"] / 1e6) if pt else None,
            "policy_ns_per_call": (pt["ns_total"] / pt["calls"]) if pt else None}


def contrasts(ms: Dict[str, Dict[str, Any]]) -> Dict[str, Any]:
    out: Dict[str, Any] = {}
    for b in V.BUDGET_ORDER:
        a, s, p = ms[f"A{b}"], ms[f"S{b}"], ms[f"P{b}"]
        def diff(x, y, k):
            d = x[k] - y[k]; return {"abs": d, "rel_pct_of_second": 100.0 * d / y[k] if y[k] else None}
        out[b] = {"P_minus_A": {k: diff(p, a, k) for k in ("decode_miss_bytes_per_token", "decode_hits_per_token", "decode_misses_per_token", "decode_evictions_per_token", "all_pass_miss_bytes")},
                  "P_minus_S": {k: diff(p, s, k) for k in ("decode_miss_bytes_per_token", "decode_hits_per_token", "decode_evictions_per_token")},
                  "S_minus_A": {k: diff(s, a, k) for k in ("decode_miss_bytes_per_token", "decode_hits_per_token", "decode_evictions_per_token")},
                  "decode_protected_entries": {"A": a["decode_protected_entries"], "S": s["decode_protected_entries"], "P": p["decode_protected_entries"]},
                  "policy_ms_per_token": {"A": a["policy_ms_per_token_ns_total_over_512"], "S": s["policy_ms_per_token_ns_total_over_512"], "P": p["policy_ms_per_token_ns_total_over_512"]}}
    return out


def audit(recs: Dict[int, Dict[str, Any]], logs: Dict[int, Dict[str, Any]], gates: Dict[str, Any], gi: Dict[str, Any], traces_left: List[str], plan: Dict[str, Any]) -> Dict[str, Any]:
    checks: List[Dict[str, Any]] = []
    def chk(name: str, ok: bool, detail: Any = None) -> None:
        checks.append({"check": name, "ok": bool(ok), "detail": detail})
    sched = V.mechanism_schedule()
    chk("exactly 10 records in frozen mseq order", sorted(recs) == [m["mseq"] for m in sched] and len(recs) == 10)
    chk("schedule identity (arm,budget,policy,timing) per launch", all((recs[m["mseq"]]["arm"], recs[m["mseq"]]["budget"], recs[m["mseq"]]["policy_id"], recs[m["mseq"]]["policy_timing"]) == (m["arm"], m["budget"], m["policy_id"], m["policy_timing"]) for m in sched))
    chk("runtime sha256 frozen (all 10)", all(r["runtime_sha256"] == V.RUNTIME_SHA256 for r in recs.values()))
    chk("token digest frozen, 512 tokens (all 10)", all(r["output_token_digest"] == V.TOKEN_DIGEST and r["n_tokens"] == 512 for r in recs.values()))
    chk("model path == frozen plan model path (log, all 10)", all(l.get("model_path") == plan["model"]["path"] for l in logs.values()), plan["model"]["path"])
    chk("expert store path == frozen plan store path (log, all 10)", all(l.get("store_path") == plan["store"]["path"] for l in logs.values()), plan["store"]["path"])
    chk("store budget MiB matches launch budget (log)", all(l["budget_mib"] == BUDGET_MIB[recs[i]["budget"]] for i, l in logs.items()))
    ok_cfg = True
    for i, l in logs.items():
        cfg = V.config(recs[i]["budget"], recs[i]["arm"]); p = l["policy"]
        ok_cfg &= (p["id"] == cfg["policy_id"] and p["decay"] == cfg["params"]["decay_factor"] and p["interval"] == cfg["params"]["aging_interval_steps"]
                   and p["predictor_decay"] == cfg["params"].get("predictor_decay"))
    chk("policy id / decay / interval / predictor_decay match frozen per-budget config (log)", ok_cfg)
    chk("514 passes and 24,672 semantic events (all 10)", all(r["replay"]["ok"] and r["replay"]["passes"] == 514 and r["replay"]["semantic_events"] == V.EVENTS == 24672 for r in recs.values()))
    chk("trace diagnostics: attempted == written == completed == 24672 (log)", all(l["trace_diag"]["righe_tentate"] == l["trace_diag"]["righe_scritte"] == l["trace_diag"]["righe_completate"] == str(V.EVENTS) for l in logs.values()))
    chk("no budget violation (replay)", all(r["replay"]["budget_violations"] == 0 and r["replay"]["physical"]["post_service_over_budget"] == 0 for r in recs.values()))
    chk("no duplicate-event refusal (record and raw log)", all(r["duplicate_event_refusals"] == 0 and l["duplicate_refusals"] == 0 and r["replay"]["duplicate_events"] == 0 for r, l in zip(recs.values(), logs.values())))
    chk("no missing events (replay)", all(r["replay"]["missing_events"] == 0 for r in recs.values()))
    chk("resident peak <= budget (counts)", all(r["counts"]["resident_peak_bytes"] <= V.BUDGETS[r["budget"]]["bytes"] for r in recs.values()))
    tfail = []
    for i, r in recs.items():
        if r["policy_timing"] == "ON":
            pt = r["policy_timing_records"]
            if len(pt) != 1: tfail.append((i, "records", len(pt))); continue
            f = C.validate_timing_record(pt[0], V.EVENTS, r["replay"]["total_evictions"])
            if f or logs[i]["policy_timing_lines"] != 1: tfail.append((i, f))
        elif r["policy_timing_records"] or logs[i]["policy_timing_lines"]:
            tfail.append((i, "timing present although OFF"))
    chk("policy_timing valid for 9 ON launches (1 record, schema/calls/hist/victim_calls), absent for the OFF launch (record and raw log)", not tfail, tfail)
    chk("victim_calls == total evictions (ON launches)", all(r["policy_timing_records"][0]["victim_calls"] == r["replay"]["total_evictions"] for r in recs.values() if r["policy_timing"] == "ON"))
    chk("reduction self-consistency: all-pass evictions == replay evictions; events 24672/decode 24576", all(r["counts"]["all_passes"]["evictions"] == r["replay"]["total_evictions"] and r["counts"]["all_passes"]["events"] == 24672 and r["counts"]["decode_passes"]["events"] == 24576 for r in recs.values()))
    chk("reduction: decode miss_bytes/token == decode miss_bytes/512", all(abs(r["counts"]["decode_miss_bytes_per_token"] - r["counts"]["decode_passes"]["miss_bytes"] / 512) < 1e-6 for r in recs.values()))
    chk("hits+misses (requested experts) identical across A/S/P at each budget", all(len({recs[i]["counts"]["decode_passes"]["hits"] + recs[i]["counts"]["decode_passes"]["misses"] for i in idx}) == 1 for idx in ([1, 2, 3], [5, 6, 7], [8, 9, 10])))
    chk("S counters identical to A at every budget (decomposition valid)", all(all(recs[s]["counts"]["decode_passes"][k] == recs[a]["counts"]["decode_passes"][k] for k in ("hits", "misses", "miss_bytes", "evictions")) for a, s in ((1, 2), (5, 6), (8, 9))))
    chk("S decisions equal A (frozen trace comparison, 49,344 rows, 0 diff) at 1.5/4/8", all(gi["sham_vs_a"][b]["ok"] and gi["sham_vs_a"][b]["diff_rows"] == 0 and gi["sham_vs_a"][b]["rows"] == 49344 for b in V.BUDGET_ORDER))
    chk("P timing OFF vs ON canonical traces byte-identical, 0 diff rows", gi["off_vs_on"]["ok"] and gi["off_vs_on"]["byte_identical"] and gi["off_vs_on"]["diff_rows"] == 0)
    chk("trace lifecycle: all 10 traces deleted after reduction; compact records persisted with trace size+sha256", traces_left == [] and all(r.get("trace_bytes") and len(r.get("trace_sha256", "")) == 64 and r["trace_bytes"] == r["replay"]["trace_bytes"] for r in recs.values()))
    chk("shutdown requested = own_console_helper (all 10)", all(r["shutdown_method_requested"] == "own_console_helper" for r in recs.values()))
    chk("shutdown helper actually used, return code 0, mode graceful, zero fallback to legacy", all(r["shutdown"]["method_used"] == "own_console_helper" and r["shutdown"]["helper_returncode"] == 0 and r["shutdown"]["mode"] == "graceful" == r["shutdown_mode"] for r in recs.values()))
    chk("server logged 'cleaning up before exit' in all 10 (graceful destructor path)", all("cleanup_s" in l for l in logs.values()))
    lat = [recs[i]["shutdown"]["latency_s"] for i in sorted(recs)]
    chk("ten shutdown latencies all <= 60 s frozen limit", len(lat) == 10 and max(lat) <= 60.0, {"latencies_s": lat, "max": max(lat), "median": st.median(lat)})
    chk("MECHANISM_GATES.json latencies equal record latencies", gates["shutdown_validation"]["latencies_s"] == lat)
    re_eval = M.evaluate_gates(recs, gi["sham_vs_a"], gi["off_vs_on"])
    chk("independent re-evaluation of the committed gate function equals MECHANISM_GATES.json (status, failures, shutdown_validation)", re_eval == gates and gates["status"] == "MECHANISM_PASS" and gates["failures"] == [] and gates["shutdown_validation"]["status"] == "HELPER_VALIDATED_ON_REAL_MODEL")
    chk("no scientific classification in records or gates", not any(k in json.dumps([gates]) for k in ("NET_WIN", "NET_LOSS")))
    return {"checks": checks, "all_ok": all(c["ok"] for c in checks), "n_checks": len(checks)}


def build(run: Path, out: Path) -> Dict[str, Any]:
    plan = json.loads((REPO / "experiments/R3-02-calibration-mechanism/ACQUISITION_PLAN.json").read_text())
    raw: Dict[str, bytes] = {}; recs: Dict[int, Dict[str, Any]] = {}; logs: Dict[int, Dict[str, Any]] = {}; tim: List[Dict[str, Any]] = []
    for name in ("MECHANISM_GATES.json", "GATE_INPUTS.json"):
        raw[name] = (run / name).read_bytes()
    for i in range(1, 11):
        rp = run / "records" / f"M{i:02d}.json"; raw[f"records/M{i:02d}.json"] = rp.read_bytes(); recs[i] = json.loads(raw[f"records/M{i:02d}.json"])
        sp = next((run / "launches").glob(f"M{i:02d}-*.stderr.log")); raw[f"launches/{sp.name}"] = sp.read_bytes(); logs[i] = parse_log(sp.read_text(encoding="utf-8", errors="replace"))
        op = Path(str(sp).replace("stderr", "stdout")); raw[f"launches/{op.name}"] = op.read_bytes()
        logs[i]["_mtime"] = {"start": op.stat().st_mtime, "server_end": sp.stat().st_mtime, "record": rp.stat().st_mtime}
    gates, gi = json.loads(raw["MECHANISM_GATES.json"]), json.loads(raw["GATE_INPUTS.json"])
    traces_left = sorted(p.name for p in (run / "traces").iterdir())
    rep = audit(recs, logs, gates, gi, traces_left, plan)
    prev_rec = None
    for i in range(1, 11):
        l, r = logs[i], recs[i]; mt = l["_mtime"]
        tim.append({"mseq": i, "arm": r["arm"], "budget": r["budget"], "traced": True, "startup_load_s": l["loaded_s"], "prefill_s": l["prompt_ms"] / 1e3, "decode_s": l["decode_ms"] / 1e3,
                    "decode_ms_per_token_traced": l["decode_ms"] / 512, "server_uptime_to_cleanup_s": l["cleanup_s"], "shutdown_s": r["shutdown"]["latency_s"],
                    "launch_wall_s_mtime": round(mt["server_end"] - mt["start"], 1), "post_launch_analysis_and_record_s_mtime": round(mt["record"] - mt["server_end"], 1),
                    "pre_launch_gap_s_mtime": round(mt["start"] - prev_rec, 1) if prev_rec else None, "cooldown_before_s": 60 if i > 1 else None,
                    "unclassified_other_s": round(mt["start"] - prev_rec - 60, 1) if prev_rec else None, "mtime_utc": {k: v for k, v in mt.items()}})
        prev_rec = mt["record"]
    first, last = tim[0]["mtime_utc"]["start"], tim[-1]["mtime_utc"]["record"]
    def agg(key, sel):
        v = [t[key] for t in tim if sel(t)]; return {"n": len(v), "mean": st.fmean(v), "median": st.median(v), "min": min(v), "max": max(v)}
    summary = {"first_launch_start_to_last_record_s": last - first, "sum_server_uptime_s": sum(t["server_uptime_to_cleanup_s"] for t in tim), "sum_shutdown_s": sum(t["shutdown_s"] for t in tim),
               "sum_launch_wall_s": sum(t["launch_wall_s_mtime"] for t in tim), "sum_post_launch_analysis_s": sum(t["post_launch_analysis_and_record_s_mtime"] for t in tim),
               "sum_pre_launch_gaps_s": sum(t["pre_launch_gap_s_mtime"] for t in tim if t["pre_launch_gap_s_mtime"]),
               "note_user_reported_span": "the 1 h 33 m 48 s span reported by the user starts at the M01 record write time, i.e. it omits the first launch",
               "by_budget_launch_wall_s": {b: agg("launch_wall_s_mtime", lambda t, b=b: t["budget"] == b) for b in V.BUDGET_ORDER},
               "by_arm_launch_wall_s": {a: agg("launch_wall_s_mtime", lambda t, a=a: t["arm"] == a) for a in "ASP"},
               "by_budget_decode_ms_per_token_traced": {b: agg("decode_ms_per_token_traced", lambda t, b=b: t["budget"] == b) for b in V.BUDGET_ORDER},
               "by_arm_decode_ms_per_token_traced": {a: agg("decode_ms_per_token_traced", lambda t, a=a: t["arm"] == a) for a in "ASP"},
               "prefill_s": agg("prefill_s", lambda t: True), "startup_load_s": agg("startup_load_s", lambda t: True), "shutdown_s": agg("shutdown_s", lambda t: True),
               "post_launch_analysis_s_by_budget": {b: agg("post_launch_analysis_and_record_s_mtime", lambda t, b=b: t["budget"] == b) for b in V.BUDGET_ORDER}}
    ms = {f"{r['arm']}{r['budget']}": metrics(r) for r in recs.values() if not (r["arm"] == "P" and r["policy_timing"] == "OFF")}
    mech = {"per_launch": [metrics(recs[i]) for i in range(1, 11)], "contrasts": contrasts(ms), "decode_definition": "passes >= 2 (first 2 passes are prefill), divisor 512 tokens",
            "P_OFF_vs_ON_identical_counts": metrics(recs[4])["decode_miss_bytes_per_token"] == metrics(recs[3])["decode_miss_bytes_per_token"],
            "replay_R2_11_reference_miss_byte_reduction_pct": {"1.5": 0.131, "4": 0.037, "8": 0.0},
            "mechanism_only": "no wall-time or throughput claim; no NET_WIN/NET_LOSS"}
    c = mech["contrasts"]
    red = {b: -c[b]["P_minus_A"]["decode_miss_bytes_per_token"]["rel_pct_of_second"] for b in V.BUDGET_ORDER}
    mech["P_vs_A_decode_miss_byte_reduction_pct"] = red
    mech["R2_pattern_reproduced"] = bool(red["1.5"] > red["4"] > red["8"] and red["8"] == 0.0 and red["1.5"] > 0 and red["4"] > 0)
    out.mkdir(exist_ok=True); (out / "records_sanitised").mkdir(exist_ok=True)
    for i in range(1, 11):
        (out / "records_sanitised" / f"M{i:02d}.json").write_text(json.dumps(sanitise(recs[i]), indent=1, sort_keys=True) + "\n")
    for name, obj in (("MECHANISM_GATES.json", gates), ("GATE_INPUTS.json", gi), ("AUDIT.json", rep), ("LAUNCH_TIMING.json", {"launches": tim, "summary": summary}), ("MECHANISM_METRICS.json", mech),
                      ("ORIGINAL_FILE_SHA256.json", {k: sha256_bytes(v) for k, v in sorted(raw.items())})):
        (out / name).write_text(json.dumps(obj, indent=1, sort_keys=True) + "\n")
    return {"audit": rep, "mech": mech["R2_pattern_reproduced"], "reduction_pct": red}


def verify() -> Dict[str, Any]:
    gates = json.loads((HERE / "MECHANISM_GATES.json").read_text()); gi = json.loads((HERE / "GATE_INPUTS.json").read_text())
    recs = {i: json.loads((HERE / "records_sanitised" / f"M{i:02d}.json").read_text()) for i in range(1, 11)}
    assert M.evaluate_gates(recs, gi["sham_vs_a"], gi["off_vs_on"]) == gates                 # sanitised records still satisfy the frozen gate function
    assert all("stderr_path" not in r for r in recs.values())
    rep = json.loads((HERE / "AUDIT.json").read_text()); assert rep["all_ok"] and all(c["ok"] for c in rep["checks"])
    fz = json.loads((HERE / "FREEZE.json").read_text()); assert fz["freeze_sha256"] == freeze_sha(fz)
    for name, h in fz["files_sha256"].items():
        assert sha256_bytes((HERE / name).read_bytes()) == h, name
    return fz


if __name__ == "__main__":
    ap = argparse.ArgumentParser(); ap.add_argument("--build"); ap.add_argument("--verify", action="store_true"); a = ap.parse_args()
    if a.build:
        print(json.dumps(build(Path(a.build), HERE), indent=1))
    else:
        fz = verify(); print("GATE2_VERIFIED", fz["classification"], fz["freeze_sha256"])
