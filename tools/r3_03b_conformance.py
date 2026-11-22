"""R3-03B -- real-model INSTRUMENTED SEMANTIC CONFORMANCE (correctness only; NO timing claim).

Four launches of the NEW instrumented runtime on the frozen R3-02 workload at 8 GiB with ARGINE_RETENTION_TRACE:
    A-8GiB-OFF / A-8GiB-ON   (b_selected,             ARGINE_POLICY_TIMING unset / =1)
    B-8GiB-OFF / B-8GiB-ON   (predicted_protect_sham, ARGINE_POLICY_TIMING unset / =1)
The timing record is emitted by the runtime destructor, so the server is stopped GRACEFULLY (Ctrl-C), never with
TerminateProcess; a hard-kill fallback is recorded and makes the ON launch fail with a tooling reason.

PASS requires (every item; see PLAN_RULES in experiments/R3-03B-instrumented-conformance/CONFORMANCE_PLAN.json):
  T1 runtime sha256 == --runtime-sha256 and differs from the R3-03A and frozen R3 runtimes
  T2 output-token digest == frozen digest in all four launches
  T3 r3_03a_trace analysis ok per launch (514 passes, 48 events/pass, 24,672 events, R2 replay, history/score, no
     duplicate (pass,layer), zero budget violations); sham launches replayed as b_selected
  T4 OFF vs ON trace scientific-state identity per policy (canonicalisation below); expected literal byte identity
  T5 sham vs b_selected trajectory equivalence on the OFF launches (declared fields)
  T6 OFF stderr has NO policy_timing line; ON stderr has exactly ONE valid policy_timing line
  T7 no duplicate-semantic-event refusal in any stderr
Canonicalisation (declared before the host run): every trace row is parsed as JSON; any key equal to "policy_timing"
or starting with "ns_" is removed recursively (the current trace format contains none); rows are then compared
as sort_keys JSON. All other fields -- requested/hit/miss experts, protected_before, resident_before/after with
score and last_ref (history/decay), evicted, bytes, policy_params, semantic_event, cache_resident_* and completion
rows -- must be identical. (Observed offline: cache_resident_* were identical across two real runs, R3-03A A vs B 4 GiB.)

  python tools\\r3_03b_conformance.py --runtime <exe> --runtime-sha256 <sha> --out-dir <fresh dir> [--trace-dir <dir>]
  --dry-run validates identities and prints the schedule without launching.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import shutil
import signal
import subprocess
import sys
import time
import urllib.request
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

TOOLS = Path(__file__).resolve().parent
REPO = TOOLS.parent
sys.path.insert(0, str(TOOLS))

R3A_RUNTIME_SHA = "e935005e5131b29d12b113b000fa52e798b4b4419a23724ec70af3f6875fcd76"
FROZEN_RUNTIME_SHA = "3f094f5b5f4963f488b63722f365068f09e2aa3b12e2096969da99f471bbd644"
FROZEN_TOKEN_DIGEST = "ac28efbe13c80f363f508a6f19d4b1d3af189eb26d6e9c81309376033075cdfa"
FROZEN_PASSES = 514
N_LAYERS = 48
N_PREDICT = 512
PORT = 18083
EXPECTED_EVENTS = FROZEN_PASSES * N_LAYERS            # 24,672
PER_LAUNCH_MIN_FREE = 14 * 1024 ** 3
TOTAL_TRACE_ESTIMATE = 4 * 11 * 1024 ** 3 + 3 * 1024 ** 3
GRACEFUL_TIMEOUT_S = 180
PLAN = REPO / "experiments" / "R3-02-calibration-mechanism" / "ACQUISITION_PLAN.json"
# (tag, config_id, timing_on)
LAUNCHES: Tuple[Tuple[str, str, bool], ...] = (
    ("A-8GiB-OFF", "A-8GiB", False), ("A-8GiB-ON", "A-8GiB", True),
    ("B-8GiB-OFF", "B-8GiB", False), ("B-8GiB-ON", "B-8GiB", True))
PAIRS = (("A-8GiB-OFF", "A-8GiB-ON"), ("B-8GiB-OFF", "B-8GiB-ON"))
SHAM_VS_B = ("A-8GiB-OFF", "B-8GiB-OFF")
SHAM_COMPARED_FIELDS = ("requested_experts", "hit_experts", "miss_experts", "evicted", "resident_before", "resident_after",
                        "policy_resident_bytes_after", "cache_resident_bytes_before", "cache_resident_bytes_after",
                        "bytes_of_layer", "budget_bytes", "step", "layer", "semantic_event")
TIMING_MARK = "argine: policy_timing "
DUPLICATE_MARK = "evento semantico (pass, layer) gia' servito"
DESTRUCTOR_MARK = "retention trace diagnostica"


def sha256_file(p: Path) -> str:
    h = hashlib.sha256()
    with p.open("rb") as f:
        for b in iter(lambda: f.read(1 << 20), b""):
            h.update(b)
    return h.hexdigest()


# ----------------------------------------------------------------------------- environment / shutdown
def build_env(plan: Dict[str, Any], cfg: Dict[str, Any], trace: Path, timing_on: bool,
              base_env: Optional[Dict[str, str]] = None) -> Dict[str, str]:
    env = dict(os.environ if base_env is None else base_env)
    env.pop("ARGINE_POLICY_TIMING", None)                     # OFF must never inherit a user-level setting
    env.update({"ARGINE_EXPERT_STORE": plan["store"]["path"], "ARGINE_EXPERT_BUDGET": str(cfg["budget_bytes"]),
                "ARGINE_EXPERT_SHARDS": "1", "ARGINE_EXPERT_READ": "blocco",
                "ARGINE_RETENTION_POLICY": cfg["policy_id"],
                "ARGINE_RETENTION_DECAY": str(cfg["params"]["decay_factor"]),
                "ARGINE_RETENTION_INTERVAL": str(cfg["params"]["aging_interval_steps"]),
                "ARGINE_RETENTION_TRACE": str(trace)})
    for k, v in (("ARGINE_RETENTION_PREDICTOR_DECAY", "predictor_decay"), ("ARGINE_RETENTION_TOPK", "top_k"),
                 ("ARGINE_RETENTION_NEXPERTS", "n_experts")):
        if v in cfg["params"]:
            env[k] = str(cfg["params"][v])
    if timing_on:
        env["ARGINE_POLICY_TIMING"] = "1"
    return env


def graceful_stop(proc: "subprocess.Popen[bytes]", timeout_s: float = GRACEFUL_TIMEOUT_S) -> str:
    """Ask the server to exit normally (so static destructors run); fall back to terminate/kill. Returns the mode."""
    if proc.poll() is not None:
        return "already_exited"
    old = None
    try:
        if os.name == "nt":
            old = signal.signal(signal.SIGINT, signal.SIG_IGN)        # the console event is broadcast to this process too
            proc.send_signal(signal.CTRL_C_EVENT)                     # type: ignore[attr-defined]
        else:
            proc.send_signal(signal.SIGINT)
        try:
            proc.wait(timeout=timeout_s)
            return "graceful"
        except subprocess.TimeoutExpired:
            pass
    finally:
        if old is not None:
            signal.signal(signal.SIGINT, old)
    proc.terminate()
    try:
        proc.wait(timeout=60)
    except subprocess.TimeoutExpired:
        proc.kill(); proc.wait(timeout=60)
    return "terminate_fallback"


def launch(plan: Dict[str, Any], cfg: Dict[str, Any], runtime: Path, trace: Path, outdir: Path, tag: str,
           timing_on: bool) -> Dict[str, Any]:
    import r3_02_acquire as acq
    env = build_env(plan, cfg, trace, timing_on)
    so, se = outdir / f"{tag}.stdout.log", outdir / f"{tag}.stderr.log"
    cmd = [str(runtime), "-m", plan["model"]["path"], "-c", str(plan["decoding"]["context"]), "-n", str(N_PREDICT),
           "--host", "127.0.0.1", "--port", str(PORT)]
    shutdown_mode = "unknown"
    with so.open("wb") as fo, se.open("wb") as fe:
        proc = subprocess.Popen(cmd, env=env, stdout=fo, stderr=fe)          # no shell
        try:
            deadline = time.monotonic() + 900
            while True:
                if proc.poll() is not None:
                    raise RuntimeError(f"runtime exited early: {proc.returncode}")
                try:
                    with urllib.request.urlopen(f"http://127.0.0.1:{PORT}/health", timeout=5) as r:
                        if r.status == 200:
                            break
                except Exception:
                    if time.monotonic() > deadline:
                        raise TimeoutError("runtime readiness timeout")
                    time.sleep(1.0)
            body = {"prompt": acq.workload_prompt_for_request(plan), "n_predict": N_PREDICT, "seed": plan["decoding"]["seed"],
                    "temperature": plan["decoding"]["temperature"], "top_k": plan["decoding"]["top_k"],
                    "return_tokens": True, "stream": False}
            req = urllib.request.Request(f"http://127.0.0.1:{PORT}/completion", data=json.dumps(body).encode(),
                                         headers={"Content-Type": "application/json"}, method="POST")
            with urllib.request.urlopen(req, timeout=3600) as resp:
                payload = json.loads(resp.read().decode())
        finally:
            shutdown_mode = graceful_stop(proc)
    tokens = payload.get("tokens")
    digest = acq.T.output_token_digest(tokens) if tokens is not None else None
    rec = {"artifact": "R3-03B-LAUNCH-RECORD", "tag": tag, "config_id": cfg["config_id"], "policy_id": cfg["policy_id"],
           "timing_on": timing_on, "runtime": str(runtime), "n_predict": N_PREDICT,
           "n_tokens": len(tokens) if tokens is not None else None, "output_token_digest": digest, "tokens": tokens,
           "trace_path": str(trace), "trace_bytes": trace.stat().st_size, "trace_sha256": sha256_file(trace),
           "stderr_path": str(se), "shutdown_mode": shutdown_mode}
    tmp = outdir / f"{tag}.launch.json.tmp"
    tmp.write_text(json.dumps(rec, sort_keys=True), encoding="utf-8")
    tmp.replace(outdir / f"{tag}.launch.json")                 # persisted before any analysis
    return rec


# ----------------------------------------------------------------------------- stderr timing record
def timing_records(stderr_text: str) -> List[Dict[str, Any]]:
    out = []
    for line in stderr_text.splitlines():
        i = line.find(TIMING_MARK)
        if i >= 0:
            try:
                out.append(json.loads(line[i + len(TIMING_MARK):]))
            except ValueError:
                out.append({"_unparseable": line[i:i + 200]})
    return out


def validate_timing_record(rec: Dict[str, Any], expected_calls: int, min_victim_calls: int = 0) -> List[str]:
    f: List[str] = []
    if "_unparseable" in rec:
        return ["policy_timing line is not valid JSON"]
    for k in ("schema", "calls", "ns_total", "ns_prediction_update", "ns_victim_selection", "victim_calls",
              "ns_event_lock_wait", "ns_event_locked_total", "hist_log2"):
        if k not in rec:
            f.append(f"missing field {k}")
    if f:
        return f
    if rec["schema"] != "r3-03b-v1":
        f.append("schema != r3-03b-v1")
    if rec["calls"] != expected_calls:
        f.append(f"calls {rec['calls']} != semantic events {expected_calls}")
    if not (isinstance(rec["hist_log2"], list) and len(rec["hist_log2"]) == 40 and sum(rec["hist_log2"]) == rec["calls"]):
        f.append("histogram length != 40 or sum != calls")
    if rec["ns_total"] <= 0:
        f.append("ns_total not positive")
    if rec["ns_prediction_update"] + rec["ns_victim_selection"] > rec["ns_total"]:
        f.append("components exceed ns_total")
    if rec["victim_calls"] < min_victim_calls:
        f.append(f"victim_calls {rec['victim_calls']} < evictions {min_victim_calls}")
    if rec["ns_event_locked_total"] < rec["ns_total"]:
        f.append("ns_event_locked_total < ns_total (policy runs inside the event lock)")
    if rec["ns_event_lock_wait"] < 0:
        f.append("negative lock wait")
    return f


# ----------------------------------------------------------------------------- trace comparisons (streaming)
def _strip(o: Any) -> Any:
    if isinstance(o, dict):
        return {k: _strip(v) for k, v in o.items() if not (k == "policy_timing" or k.startswith("ns_"))}
    if isinstance(o, list):
        return [_strip(v) for v in o]
    return o


def canonical_line(line: bytes) -> bytes:
    return json.dumps(_strip(json.loads(line)), sort_keys=True, separators=(",", ":")).encode()


def compare_canonical(a: Path, b: Path) -> Dict[str, Any]:
    """OFF-vs-ON scientific-state identity. Fast path: byte-identical files."""
    sa, sb = sha256_file(a), sha256_file(b)
    if sa == sb:
        return {"ok": True, "byte_identical": True, "rows": None, "diff_rows": 0, "first_diff_row": None}
    rows = diff = 0
    first = None
    with a.open("rb") as fa, b.open("rb") as fb:
        while True:
            la, lb = fa.readline(), fb.readline()
            if not la and not lb:
                break
            rows += 1
            if not la or not lb or canonical_line(la) != canonical_line(lb):
                diff += 1
                first = first or rows
    return {"ok": diff == 0, "byte_identical": False, "rows": rows, "diff_rows": diff, "first_diff_row": first}


def compare_sham_vs_b(a: Path, b: Path) -> Dict[str, Any]:
    rows = diff = 0
    first = None
    with a.open("rb") as fa, b.open("rb") as fb:
        while True:
            la, lb = fa.readline(), fb.readline()
            if not la and not lb:
                break
            rows += 1
            ja = json.loads(la) if la else None
            jb = json.loads(lb) if lb else None
            if ja is None or jb is None:
                bad = True
            elif "complete" in ja or "complete" in jb:
                bad = ja != jb
            else:
                bad = any(ja.get(k) != jb.get(k) for k in SHAM_COMPARED_FIELDS)
            if bad:
                diff += 1
                first = first or rows
    return {"ok": diff == 0, "rows": rows, "diff_rows": diff, "first_diff_row": first,
            "fields": list(SHAM_COMPARED_FIELDS)}


# ----------------------------------------------------------------------------- main
def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--runtime", required=True)
    ap.add_argument("--runtime-sha256", required=True)
    ap.add_argument("--out-dir", required=True)
    ap.add_argument("--trace-dir", default=None, help="directory for the four large traces (default: --out-dir)")
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--resume", action="store_true",
                    help="reuse launches that have a valid <tag>.launch.json + trace (re-analysed in full, never overwritten)")
    a = ap.parse_args(argv)
    plan = json.load(PLAN.open())
    runtime = Path(a.runtime)
    out = Path(a.out_dir)
    tdir = Path(a.trace_dir) if a.trace_dir else out
    fails: List[str] = []
    if a.runtime_sha256 in (R3A_RUNTIME_SHA, FROZEN_RUNTIME_SHA):
        fails.append("T1 --runtime-sha256 equals the R3-03A or frozen R3 runtime; the instrumented runtime must be a NEW identity")
    if not runtime.is_file():
        fails.append(f"runtime missing: {runtime}")
    elif sha256_file(runtime) != a.runtime_sha256:
        fails.append("T1 runtime sha256 mismatch")
    for label in ("model", "store"):
        if not Path(plan[label]["path"]).is_file():
            fails.append(f"{label} missing: {plan[label]['path']}")
    cfgs = {c["config_id"]: c for c in plan["configurations"]}
    if any(c not in cfgs for _, c, _ in LAUNCHES):
        fails.append("plan lacks a required configuration")
    sched = [{"tag": t, "config_id": c, "policy_id": cfgs[c]["policy_id"], "budget_bytes": cfgs[c]["budget_bytes"],
              "ARGINE_POLICY_TIMING": "1" if on else "(unset)", "n_predict": N_PREDICT} for t, c, on in LAUNCHES if c in cfgs]
    if a.dry_run or fails:
        print(json.dumps({"mode": "dry-run" if a.dry_run else "refused", "failures": fails, "schedule": sched,
                          "trace_dir": str(tdir), "required_free_bytes_total": TOTAL_TRACE_ESTIMATE}, indent=1))
        return 1 if fails else 0
    out.mkdir(parents=True, exist_ok=True)
    tdir.mkdir(parents=True, exist_ok=True)
    if not a.resume and shutil.disk_usage(tdir).free < TOTAL_TRACE_ESTIMATE:
        print(f"storage gate: free {shutil.disk_usage(tdir).free} < {TOTAL_TRACE_ESTIMATE} needed for four traces; "
              f"use --trace-dir on a larger drive", file=sys.stderr)
        return 1
    import r3_03a_trace as tr
    report: Dict[str, Any] = {"artifact": "R3-03B-INSTRUMENTED-CONFORMANCE", "runtime_sha256": a.runtime_sha256,
                              "timing_evidence": "NONE (correctness only; no timing value is evidence)", "resume": bool(a.resume),
                              "launches": {}, "pairs": {}, "tooling_issues": []}
    traces: Dict[str, Path] = {}
    ok_all = True
    for tag, cid, on in LAUNCHES:
        cfg = cfgs[cid]
        trace, lrec = tdir / f"{tag}.trace.jsonl", out / f"{tag}.launch.json"
        r: Dict[str, Any] = {"policy_id": cfg["policy_id"], "timing_on": on, "failures": [], "reused": False}
        if trace.exists() or lrec.exists():
            if not a.resume:
                print(f"refusing to overwrite existing evidence for {tag} (use --resume or a fresh --out-dir)", file=sys.stderr)
                return 1
            if not (trace.exists() and lrec.exists()):
                print(f"{tag}: trace/launch record incomplete; refusing", file=sys.stderr); return 1
            rec = json.loads(lrec.read_text(encoding="utf-8"))
            bad = []
            if rec.get("tag") != tag or rec.get("timing_on") != on or rec.get("n_tokens") != N_PREDICT:
                bad.append("launch record identity mismatch")
            if rec.get("trace_bytes") != trace.stat().st_size or rec.get("trace_sha256") != sha256_file(trace):
                bad.append("trace size/sha256 differs from the launch record")
            if bad:
                print(f"{tag}: existing evidence invalid: {bad}", file=sys.stderr); return 1
            r["reused"] = True
        else:
            free = shutil.disk_usage(tdir).free
            if free < PER_LAUNCH_MIN_FREE:
                print(f"storage gate: free {free} < {PER_LAUNCH_MIN_FREE} before {tag}", file=sys.stderr); return 1
            rec = launch(plan, cfg, runtime, trace, out, tag, on)
        traces[tag] = trace
        r["token_digest"] = rec["output_token_digest"]; r["shutdown_mode"] = rec.get("shutdown_mode")
        r["trace_sha256"] = rec["trace_sha256"]
        if rec["output_token_digest"] != FROZEN_TOKEN_DIGEST:
            r["failures"].append("T2 output-token digest differs from the frozen digest")
        an = tr.analyse_file(trace, n_layers=N_LAYERS, expected_passes=FROZEN_PASSES, sham=cfg["policy_id"] == "predicted_protect_sham")
        (out / f"{tag}.analysis.json").write_text(json.dumps(an, indent=1, sort_keys=True, default=str), encoding="utf-8")
        if not an.get("ok"):
            r["failures"].append("T3 analysis: " + str(an.get("failure")))
        else:
            r["analysis"] = {k: an[k] for k in ("policy_event_rows", "completion_rows", "passes", "semantic_events",
                                                 "budget_violations", "r2_replay", "history_last_ref")}
            if an["semantic_events"] != EXPECTED_EVENTS:
                r["failures"].append(f"T3 semantic events {an['semantic_events']} != {EXPECTED_EVENTS}")
        err = Path(rec["stderr_path"]).read_text(encoding="utf-8", errors="replace")
        if DUPLICATE_MARK in err:
            r["failures"].append("T7 runtime refused a duplicate semantic event")
        tl = timing_records(err)
        if on:
            if len(tl) != 1:
                reason = f"T6 expected exactly one policy_timing line, found {len(tl)}"
                if rec.get("shutdown_mode") != "graceful":
                    report["tooling_issues"].append(f"{tag}: shutdown_mode={rec.get('shutdown_mode')} (destructor output not guaranteed)")
                    reason += f" (shutdown_mode={rec.get('shutdown_mode')}; destructor line present={DESTRUCTOR_MARK in err})"
                r["failures"].append(reason)
            else:
                ev = an.get("semantic_events", EXPECTED_EVENTS) if an.get("ok") else EXPECTED_EVENTS
                ev_total = an.get("total_evictions") if an.get("ok") else None
                r["timing_record"] = tl[0]
                r["failures"] += ["T6 " + x for x in validate_timing_record(tl[0], ev, int(ev_total or 0))]
        elif tl:
            r["failures"].append("T6 policy_timing line present with timing OFF")
        r["ok"] = not r["failures"]; ok_all &= r["ok"]
        report["launches"][tag] = r
    for off, on in PAIRS:
        c = compare_canonical(traces[off], traces[on])
        report["pairs"][f"{off}~{on}"] = c; ok_all &= c["ok"]
    sc = compare_sham_vs_b(traces[SHAM_VS_B[0]], traces[SHAM_VS_B[1]])
    report["sham_vs_b_selected"] = sc; ok_all &= sc["ok"]
    report["result"] = "R3_03B_INSTRUMENTED_CONFORMANCE_PASS" if ok_all else "R3_03B_INSTRUMENTED_CONFORMANCE_FAIL"
    (out / "CONFORMANCE_RESULT.json").write_text(json.dumps(report, indent=1, sort_keys=True, default=str), encoding="utf-8")
    print(report["result"])
    return 0 if ok_all else 1


if __name__ == "__main__":
    sys.exit(main())
