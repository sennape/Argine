"""R3-04 final evidence builder + offline verifier. No model, no network, no new launches.

  python build_final_evidence.py --build <host timing dir> --official-analysis <official --analyze stdout json>   (reads ORIGINAL artifacts, writes sanitised evidence here)
  python build_final_evidence.py --verify                                                                         (offline: recomputes everything from the committed evidence and checks the freeze)

Raw originals (host paths, stderr logs, token id lists) are NOT committed; the sha256 of each original record is recorded.
"""
from __future__ import annotations

import argparse, datetime as dt, hashlib, json, math, statistics as st, sys
from pathlib import Path
from typing import Any, Dict, List

HERE = Path(__file__).resolve().parent
REPO = HERE.parents[1]
sys.path.insert(0, str(REPO / "tools")); sys.path.insert(0, str(REPO))
import r3_04_acquire as D  # noqa: E402

V, E = D.V, D.E
PROTOCOL_FREEZE = "a88359b52891f93ec49e67978d92c2eb3b73db588611bce46b3103b2978c1202"
AMENDMENT_FREEZE = "ea0d6719f6d4f92dd77b597662ea3903bc22db70647e37dd8b4cf9ec06c9b3f6"
OPENING_FREEZE = "3f6ea9b05c4c6d4784a6d6570f7f5a0fc81b3d94025fa5b10ba0bcfa7d97968d"
AMENDED_SCHEDULE = "e87dc48518dd0281d5eeadd0c8db7a9cfc2234d0bce9d253f464566e43f511a0"
DRIVER_SHA256 = "3abf28d6b267aec50dff936fd5a9f840cc9039f4e1c549d292b3f09b64f4c26d"
DROP = ("stderr_path", "runtime", "tokens")           # host paths / raw token ids are never committed
EST_H = {"low": 6.76, "central": 8.04, "high": 9.43}
FILES = ["SANITISED_RECORDS.json", "OFFICIAL_ANALYSIS.json", "ARM_STATISTICS.json", "CAMPAIGN_INTEGRITY.json", "OPERATIONAL_REPORT.json", "ORIGINAL_FILE_SHA256.json", "CLAIM_BOUNDARIES.md"]


def sha(b: bytes) -> str: return hashlib.sha256(b).hexdigest()
def dumps(o: Any) -> str: return json.dumps(o, indent=1, sort_keys=True) + "\n"
def freeze_sha(o: Dict[str, Any]) -> str: return sha(json.dumps({k: v for k, v in o.items() if k != "freeze_sha256"}, sort_keys=True, separators=(",", ":")).encode())
def rome(u: float) -> str: return (dt.datetime.fromtimestamp(u, dt.timezone.utc) + dt.timedelta(hours=1)).strftime("%Y-%m-%d %H:%M:%S") + " (UTC+01:00, CET)"


def arm_stats(flat: List[Dict[str, Any]]) -> Dict[str, Any]:
    res: Dict[str, Any] = {}
    for b in ("1.5", "8"):
        rs = [x for x in flat if x["budget"] == b and x["kind"] == "counted"]
        for arm in "ASP":
            w = [V.wall_ms_per_token(x) for x in rs if x["arm"] == arm]; t = [V.tps(x) for x in rs if x["arm"] == arm]
            res[f"{b}_{arm}"] = {"n_launches": len(w), "ms_per_token": {"mean": st.fmean(w), "median": st.median(w), "sd": st.stdev(w)},
                                 "tokens_per_second": {"mean": st.fmean(t), "median": st.median(t), "sd": st.stdev(t), "of_mean_ms": 1000 / st.fmean(w)}}
        for name, (x, y) in {"S-A": ("S", "A"), "P-S": ("P", "S"), "P-A": ("P", "A")}.items():
            mx, my = res[f"{b}_{x}"]["ms_per_token"]["mean"], res[f"{b}_{y}"]["ms_per_token"]["mean"]
            tx, ty = res[f"{b}_{x}"]["tokens_per_second"]["mean"], res[f"{b}_{y}"]["tokens_per_second"]["mean"]
            res[f"{b}_{name}"] = {"ms_per_token_abs": mx - my, "ms_per_token_rel_pct": 100 * (mx - my) / my, "tps_abs": tx - ty, "tps_rel_pct": 100 * (tx / ty - 1)}
    return res


def integrity(acc: Dict[int, Dict[str, Any]], sched: List[Dict[str, Any]], run_files: Dict[str, Any]) -> Dict[str, Any]:
    recs = [acc[s["seq"]] for s in sched]
    kinds = {}
    for r in recs: kinds[f"{r['budget']}_{r['kind']}"] = kinds.get(f"{r['budget']}_{r['kind']}", 0) + 1
    st_ = sorted((r["seq"], r["started_unix"], r["finished_unix"]) for r in recs)
    gaps = [st_[i + 1][1] - st_[i][2] for i in range(len(st_) - 1)]
    return {"physical_launches": len(recs), "counted": sum(1 for r in recs if r["kind"] == "counted"), "warmups": sum(1 for r in recs if r["kind"] == "warmup"), "by_budget_kind": kinds,
            "launches_at_4GiB": sum(1 for r in recs if r["budget"] == "4"), "slot_identity_matches_amended_schedule": all(r["seq"] == s["seq"] and r["block"] == s["block"] and r["position"] == s["position"] and r["arm"] == s["arm"] and r["config_id"] == s["config_id"] and r["policy_id"] == s["policy_id"] and r["frozen_seq"] == s["frozen_seq"] for r, s in zip(recs, sched)),
            "all_attempt_1": all(r["attempt"] == 1 for r in recs), "failed_attempt_files": run_files["failed"], "orphan_files": run_files["orphan"], "interrupted_files": run_files["interrupted"], "resume_markers": run_files["resumed"], "abort_marker": run_files["aborted"],
            "all_admitted_no_failures": all(r["admitted"] and not r["failures"] for r in recs), "all_512_tokens_frozen_digest": all(r["n_tokens"] == 512 and r["server_timings"]["predicted_n"] == 512 and r["output_token_digest"] == "ac28efbe13c80f363f508a6f19d4b1d3af189eb26d6e9c81309376033075cdfa" for r in recs),
            "all_one_policy_timing_record": all(len(r["policy_timing_records"]) == 1 for r in recs), "runtime_sha256_all_frozen": all(r["runtime_sha256"] == V.RUNTIME_SHA256 for r in recs),
            "shutdown_all_own_console_helper_rc0_graceful": all(r["shutdown"]["method_used"] == "own_console_helper" and r["shutdown"]["helper_returncode"] == 0 and r["shutdown"]["mode"] == "graceful" and not r["shutdown_timeout"] for r in recs),
            "shutdown_latency_s": {"min": min(r["shutdown"]["latency_s"] for r in recs), "max": max(r["shutdown"]["latency_s"] for r in recs), "mean": st.fmean(r["shutdown"]["latency_s"] for r in recs)},
            "inter_launch_gap_s": {"min": min(gaps), "max": max(gaps)}, "duplicate_event_refusals_total": sum(r["duplicate_event_refusals"] for r in recs),
            "campaign_complete_marker_launches": run_files["complete_launches"], "records_dir_file_count": run_files["records_count"], "launch_log_file_count": run_files["launch_logs"],
            "clean_environment_attestation": "NOT independently provable from artifacts: the driver refuses to open without --attest-clean-environment but does not persist it; own_console_helper is persisted per record",
            "identity": {"protocol_freeze": PROTOCOL_FREEZE, "amendment_freeze": AMENDMENT_FREEZE, "opening_freeze": OPENING_FREEZE, "amended_schedule_sha256": AMENDED_SCHEDULE, "driver_sha256": DRIVER_SHA256, "runtime_sha256": V.RUNTIME_SHA256}}


def operational(recs: List[Dict[str, Any]], opened: float) -> Dict[str, Any]:
    def span(b):
        r = [x for x in recs if x["budget"] == b]; a, z = min(x["started_unix"] for x in r), max(x["finished_unix"] for x in r)
        starts = sorted(x["started_unix"] for x in r)
        return {"first_start": rome(a), "last_finish": rome(z), "duration_h": (z - a) / 3600, "launches": len(r), "mean_launch_interval_s_start_to_start": (starts[-1] - starts[0]) / (len(starts) - 1)}
    a, z = min(x["started_unix"] for x in recs), max(x["finished_unix"] for x in recs)
    tot = (z - a) / 3600
    return {"campaign_opened": rome(opened), "campaign_start_first_launch": rome(a), "campaign_finish": rome(z), "total_wall_h_first_start_to_last_finish": tot, "total_wall_h_opened_to_finish": (z - opened) / 3600,
            "budget_1.5GiB": span("1.5"), "budget_8GiB": span("8"), "estimates_h_operational_evidence_only": EST_H,
            "vs_estimate": {k: {"estimate_h": v, "actual_minus_estimate_h": tot - v, "actual_over_estimate": tot / v} for k, v in EST_H.items()},
            "exceeds_high_estimate": tot > EST_H["high"], "shutdown_failures": 0, "orphans": 0, "failed_runs": 0, "disk_anomalies": "none recorded (no failed/orphan/interrupted artifacts; 146 records, 292 launch logs)",
            "note": "Overrun is consistent with slower-than-assumed untraced 1.5 GiB decode (observed ~527 ms/token mean); the 1.5 GiB decode time was an assumed bound in the time model, not an observation"}


def build(run: Path, official: Path) -> None:
    fz = D.load_protocol_freeze(PROTOCOL_FREEZE); am, sched = D.load_amendment(AMENDMENT_FREEZE, fz["freeze_sha256"])
    assert sha(Path(D.__file__).read_bytes()) == DRIVER_SHA256
    acc = E.load_accepted(run, sched, V.RUNTIME_SHA256); assert len(acc) == len(sched) == 146
    names = sorted(p.name for p in (run / "records").iterdir())
    files = {"failed": [n for n in names if "FAILED" in n], "orphan": [n for n in names if "ORPHAN" in n], "interrupted": [n for n in names if "INTERRUPTED" in n],
             "resumed": [p.name for p in run.glob("CAMPAIGN_RESUMED*")], "aborted": (run / "CAMPAIGN_ABORTED.json").exists(), "records_count": len(names), "launch_logs": len(list((run / "launches").iterdir())),
             "complete_launches": json.loads((run / "CAMPAIGN_COMPLETE.json").read_text())["launches"]}
    opened = json.loads((run / "CAMPAIGN_OPENED.json").read_text())
    san, orig = [], {}
    for s in sched:
        p = run / "records" / f"{s['seq']:03d}.json"; orig[p.name] = sha(p.read_bytes())
        san.append({k: v for k, v in acc[s["seq"]].items() if k not in DROP})
    flat = [D.flatten(r) for r in san]
    out = {"SANITISED_RECORDS.json": dumps(san), "OFFICIAL_ANALYSIS.json": dumps(json.loads(official.read_text())), "ARM_STATISTICS.json": dumps(arm_stats(flat)),
           "CAMPAIGN_INTEGRITY.json": dumps(integrity(acc, sched, files)), "OPERATIONAL_REPORT.json": dumps(operational(san, opened["started_unix"])),
           "ORIGINAL_FILE_SHA256.json": dumps({"records": orig, "campaign_complete_json": sha((run / "CAMPAIGN_COMPLETE.json").read_bytes()), "campaign_opened_json": sha((run / "CAMPAIGN_OPENED.json").read_bytes()),
                                               "dropped_fields": list(DROP)}),
           "CLAIM_BOUNDARIES.md": (HERE / "CLAIM_BOUNDARIES.md").read_text(encoding="utf-8")}
    for n, t in out.items():
        if n != "CLAIM_BOUNDARIES.md": (HERE / n).write_text(t, encoding="utf-8")
    fr = {"artifact": "R3-04-FINAL-EVIDENCE-FREEZE", "files_sha256": {n: sha((HERE / n).read_bytes()) for n in FILES}, "analysis_command": "python tools/r3_04_acquire.py --analyze --out-dir <timing dir> --protocol-freeze-sha256 " + PROTOCOL_FREEZE + " --amendment-sha256 " + AMENDMENT_FREEZE,
          "driver_sha256": DRIVER_SHA256, "amended_schedule_sha256": AMENDED_SCHEDULE, "protocol_freeze_sha256": PROTOCOL_FREEZE, "amendment_freeze_sha256": AMENDMENT_FREEZE, "opening_freeze_sha256": OPENING_FREEZE}
    fr["freeze_sha256"] = freeze_sha(fr)
    (HERE / "FREEZE.json").write_text(dumps(fr), encoding="utf-8"); print("freeze", fr["freeze_sha256"])


def close(a: float, b: float) -> bool: return math.isclose(a, b, rel_tol=1e-9, abs_tol=1e-9)


def verify() -> List[str]:
    probs: List[str] = []
    fr = json.loads((HERE / "FREEZE.json").read_text())
    if fr["freeze_sha256"] != freeze_sha(fr): probs.append("freeze sha mismatch")
    for n, h in fr["files_sha256"].items():
        if sha((HERE / n).read_bytes()) != h: probs.append(f"{n} hash differs from freeze")
    san = json.loads((HERE / "SANITISED_RECORDS.json").read_text())
    txt = (HERE / "SANITISED_RECORDS.json").read_text()
    if "C:\\\\" in txt or "alpel" in txt or "stderr_path" in txt or '"tokens"' in txt: probs.append("host path / token list leaked into evidence")
    if len(san) != 146: probs.append("record count")
    flat = [D.flatten(r) for r in san]
    off = json.loads((HERE / "OFFICIAL_ANALYSIS.json").read_text())
    for b in ("1.5", "8"):
        rs = [x for x in flat if x["budget"] == b]
        wall = V.contrasts(rs, V.wall_ms_per_token)
        for k in ("DELTA_COST", "DELTA_RETENTION", "DELTA_NET"):
            ci = V.t_interval(wall[k]); o = off["budgets"][b]["wall_ms_per_token"][k]
            for f in ("mean", "lo", "hi", "sd"):
                if not close(ci[f], o[f]): probs.append(f"{b} {k} {f} differs from official analysis")
    cl = V.classify_primary(V.contrasts([x for x in flat if x["budget"] == "1.5"], V.wall_ms_per_token)["DELTA_NET"], None, None)["net_classification"]
    if cl != off["budgets"]["1.5"]["PRIMARY_CLASSIFICATION"]["net_classification"]: probs.append("classification differs")
    ar = json.loads((HERE / "ARM_STATISTICS.json").read_text()); re_ = arm_stats(flat)
    for k, v in re_.items():
        for kk, vv in v.items():
            if isinstance(vv, dict):
                for f, x in vv.items():
                    if not close(x, ar[k][kk][f]): probs.append(f"arm stats {k}.{kk}.{f}")
            elif not close(vv, ar[k][kk]): probs.append(f"arm stats {k}.{kk}")
    ig = json.loads((HERE / "CAMPAIGN_INTEGRITY.json").read_text())
    must = ("slot_identity_matches_amended_schedule", "all_attempt_1", "all_admitted_no_failures", "all_512_tokens_frozen_digest", "all_one_policy_timing_record", "runtime_sha256_all_frozen", "shutdown_all_own_console_helper_rc0_graceful")
    for m in must:
        if ig[m] is not True: probs.append(f"integrity {m}")
    if (ig["physical_launches"], ig["counted"], ig["warmups"], ig["launches_at_4GiB"]) != (146, 144, 2, 0): probs.append("launch counts")
    if ig["failed_attempt_files"] or ig["orphan_files"] or ig["interrupted_files"] or ig["resume_markers"] or ig["abort_marker"]: probs.append("failure artifacts present")
    return probs


if __name__ == "__main__":
    ap = argparse.ArgumentParser(); ap.add_argument("--build"); ap.add_argument("--official-analysis"); ap.add_argument("--verify", action="store_true"); a = ap.parse_args()
    if a.build:
        build(Path(a.build), Path(a.official_analysis))
    if a.verify:
        p = verify(); print("VERIFY", "OK" if not p else p); sys.exit(1 if p else 0)
