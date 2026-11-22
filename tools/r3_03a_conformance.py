"""R3-03A -- real-model SEMANTIC CONFORMANCE of the corrected runtime.

This is NOT a timing benchmark.  It launches the corrected runtime (new identity,
never the frozen R3 runtime) on the frozen R3-02 workload for three configurations
with ARGINE_RETENTION_TRACE enabled, and checks semantic equivalence with the frozen
R2 reference.  No timing field is read, compared or reported as evidence.

Checks per launch (every one must hold, otherwise exit code 1):
  C1 output-token digest equals the frozen R3-02 digest (generation unchanged)
  C2 every trace row carries a semantic_event block with first_node == 1
  C3 per forward pass exactly N_LAYERS policy rows, layers 0..N_LAYERS-1 once each
     (48 per decode token for the 48-layer model); total passes == frozen pass count
  C4 no duplicate (pass, layer) rows; trace row count == passes * N_LAYERS
  C5 frozen R2 reference (tools/r2_12_real_trace_conformance.compare) replays the
     trace decision by decision: protected_before, evicted, resident scores
  C6 for the sham configuration the trajectory equals the corrected b_selected
     reference (the sham is replayed as b_selected)
  C7 zero budget violations (resident bytes <= budget after every event, from the trace)
     and no duplicate-semantic-event refusal in the runtime stderr
  C8 runtime SHA-256 equals --runtime-sha256 and differs from the frozen R3 runtime

Usage (Windows host, one command):
  python tools\\r3_03a_conformance.py --runtime <new llama-server.exe> --runtime-sha256 <sha> --out-dir <dir>
  add --dry-run to validate identities and print the schedule without launching.
"""
from __future__ import annotations

import argparse
import copy
import hashlib
import json
import subprocess
import sys
import time
import urllib.request
from pathlib import Path
from typing import Any, Dict, List, Optional

TOOLS = Path(__file__).resolve().parent
REPO = TOOLS.parent
sys.path.insert(0, str(TOOLS))

FROZEN_RUNTIME_SHA = "3f094f5b5f4963f488b63722f365068f09e2aa3b12e2096969da99f471bbd644"
FROZEN_TOKEN_DIGEST = "ac28efbe13c80f363f508a6f19d4b1d3af189eb26d6e9c81309376033075cdfa"
FROZEN_PASSES = 514            # derived from the frozen R3-02 trace replay (24,672 / 48)
N_LAYERS = 48
N_PREDICT = 512
PORT = 18083
MIN_FREE_BYTES = 14 * 1024 ** 3   # per-launch trace gate (A-4GiB trace is 5.1 GB; 8GiB config is larger)
LAUNCHES = ("A-4GiB", "B-4GiB", "C-8GiB")   # b_selected, predicted_protect_sham, predicted_protect
PLAN = REPO / "experiments" / "R3-02-calibration-mechanism" / "ACQUISITION_PLAN.json"


def sha256_file(p: Path) -> str:
    h = hashlib.sha256()
    with p.open("rb") as f:
        for b in iter(lambda: f.read(1 << 20), b""):
            h.update(b)
    return h.hexdigest()


def analyse_trace(events: List[Dict[str, Any]], *, n_layers: int, expected_passes: Optional[int],
                  sham: bool) -> Dict[str, Any]:
    """In-memory convenience wrapper (small traces / tests) over the streaming R3-03A adapter.
    Policy-event rows only (no completion rows), so completions are not required here."""
    import r3_03a_trace as tr
    an = tr.StreamAnalyzer(n_layers, expected_passes, sham=sham, require_completion=False)
    try:
        for ev in events:
            an.feed(json.dumps(ev))
        res = an.finish()
    except tr.TraceAnalysisError as exc:
        return {"ok": False, "failures": [str(exc)], "rows": len(events), "passes": an.n_passes}
    return {"ok": True, "failures": [], "rows": len(events), "passes": res["passes"], "reference_summary": res}


def _launch(plan, cfg, runtime: Path, trace: Path, outdir: Path) -> Dict[str, Any]:
    import os
    import r3_02_acquire as acq
    env = os.environ.copy()
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
    tag = cfg["config_id"]
    so, se = outdir / f"{tag}.stdout.log", outdir / f"{tag}.stderr.log"
    cmd = [str(runtime), "-m", plan["model"]["path"], "-c", str(plan["decoding"]["context"]),
           "-n", str(N_PREDICT), "--host", "127.0.0.1", "--port", str(PORT)]
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
            body = {"prompt": acq.workload_prompt_for_request(plan), "n_predict": N_PREDICT,
                    "seed": plan["decoding"]["seed"], "temperature": plan["decoding"]["temperature"],
                    "top_k": plan["decoding"]["top_k"], "return_tokens": True, "stream": False}
            req = urllib.request.Request(f"http://127.0.0.1:{PORT}/completion", data=json.dumps(body).encode(),
                                         headers={"Content-Type": "application/json"}, method="POST")
            with urllib.request.urlopen(req, timeout=3600) as resp:
                payload = json.loads(resp.read().decode())
        finally:
            proc.terminate()
            try:
                proc.wait(timeout=60)
            except subprocess.TimeoutExpired:
                proc.kill(); proc.wait(timeout=60)
    tokens = payload.get("tokens")
    digest = acq.T.output_token_digest(tokens) if tokens is not None else None
    # persist launch evidence IMMEDIATELY (before any analysis) so an analysis failure can never lose it
    rec = {"artifact": "R3-03A-LAUNCH-RECORD", "config_id": cfg["config_id"], "policy_id": cfg["policy_id"],
           "runtime": str(runtime), "n_predict": N_PREDICT, "n_tokens": len(tokens) if tokens is not None else None,
           "output_token_digest": digest, "tokens": tokens, "trace_path": str(trace),
           "trace_bytes": trace.stat().st_size, "trace_sha256": sha256_file(trace), "stderr_path": str(se)}
    tmp = outdir / f"{tag}.launch.json.tmp"
    tmp.write_text(json.dumps(rec, sort_keys=True), encoding="utf-8")
    tmp.replace(outdir / f"{tag}.launch.json")
    return {"tokens": tokens, "token_digest": digest, "stderr_path": str(se)}


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--runtime", required=True)
    ap.add_argument("--runtime-sha256", required=True)
    ap.add_argument("--out-dir", required=True)
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--resume", action="store_true",
                    help="explicit reuse of launches that already have a valid <tag>.launch.json + trace; "
                         "they are re-analysed in full and recorded as reused; nothing is ever overwritten")
    a = ap.parse_args(argv)
    plan = json.load(PLAN.open())
    runtime = Path(a.runtime)
    fails: List[str] = []
    if a.runtime_sha256 == FROZEN_RUNTIME_SHA:
        fails.append("C8 --runtime-sha256 equals the FROZEN R3 runtime; the corrected runtime must differ")
    if not runtime.is_file():
        fails.append(f"runtime missing: {runtime}")
    elif sha256_file(runtime) != a.runtime_sha256:
        fails.append("C8 runtime sha256 mismatch")
    for label in ("model", "store"):
        if not Path(plan[label]["path"]).is_file():
            fails.append(f"{label} missing: {plan[label]['path']}")
    cfgs = {c["config_id"]: c for c in plan["configurations"]}
    if any(l not in cfgs for l in LAUNCHES):
        fails.append("plan lacks a required configuration")
    sched = [{"config_id": l, "policy_id": cfgs[l]["policy_id"], "budget_bytes": cfgs[l]["budget_bytes"],
              "n_predict": N_PREDICT} for l in LAUNCHES if l in cfgs]
    if a.dry_run or fails:
        print(json.dumps({"mode": "dry-run" if a.dry_run else "refused", "failures": fails, "schedule": sched}, indent=1))
        return 1 if fails else 0
    out = Path(a.out_dir)
    out.mkdir(parents=True, exist_ok=True)
    import shutil
    import r3_03a_trace as tr
    report: Dict[str, Any] = {"artifact": "R3-03A-REAL-MODEL-CONFORMANCE", "runtime_sha256": a.runtime_sha256,
                              "timing_evidence": "NONE (not a timing benchmark)", "resume": bool(a.resume),
                              "launches": {}}
    ok_all = True
    for l in LAUNCHES:
        cfg = cfgs[l]
        trace = out / f"{l}.trace.jsonl"
        lrec = out / f"{l}.launch.json"
        r: Dict[str, Any] = {"policy_id": cfg["policy_id"], "failures": [], "reused": False}
        if trace.exists() or lrec.exists():
            if not a.resume:
                print(f"refusing to overwrite existing evidence for {l} in {out} (use --resume to validate and reuse)",
                      file=sys.stderr); return 1
            if not (trace.exists() and lrec.exists()):
                print(f"{l}: trace/launch record incomplete; NOT reusable, refusing (choose a new --out-dir)", file=sys.stderr)
                return 1
            rec = json.loads(lrec.read_text(encoding="utf-8"))
            bad = []
            if rec.get("config_id") != l or rec.get("n_tokens") != N_PREDICT:
                bad.append("launch record identity/token count mismatch")
            if rec.get("trace_bytes") != trace.stat().st_size or rec.get("trace_sha256") != sha256_file(trace):
                bad.append("trace size/sha256 differs from the launch record")
            if sha256_file(runtime) != a.runtime_sha256:
                bad.append("runtime sha256 mismatch")
            if bad:
                print(f"{l}: existing evidence invalid: {bad}", file=sys.stderr); return 1
            r["reused"] = True
            res = {"token_digest": rec["output_token_digest"], "stderr_path": rec["stderr_path"]}
        else:
            free = shutil.disk_usage(out).free
            if free < MIN_FREE_BYTES:
                print(f"storage gate: free {free} < {MIN_FREE_BYTES} before launch {l}", file=sys.stderr); return 1
            res = _launch(plan, cfg, runtime, trace, out)
        r["token_digest"] = res["token_digest"]
        if res["token_digest"] != FROZEN_TOKEN_DIGEST:
            r["failures"].append("C1 output-token digest differs from the frozen R3-02 digest")
        an = tr.analyse_file(trace, n_layers=N_LAYERS, expected_passes=FROZEN_PASSES,
                             sham=cfg["policy_id"] == "predicted_protect_sham")
        (out / f"{l}.analysis.json").write_text(json.dumps(an, indent=1, sort_keys=True, default=str), encoding="utf-8")
        if not an.get("ok"):
            r["failures"].append("C2-C7 analysis: " + str(an.get("failure")))
        else:
            r["analysis"] = {k: an[k] for k in ("policy_event_rows", "completion_rows", "passes", "semantic_events",
                                                 "budget_violations", "r2_replay", "history_last_ref")}
        r["trace_sha256"] = sha256_file(trace)
        err = Path(res["stderr_path"]).read_text(encoding="utf-8", errors="replace")
        if "evento semantico (pass, layer) gia' servito" in err:
            r["failures"].append("C7 runtime refused a duplicate semantic event")
        r["ok"] = not r["failures"]; ok_all &= r["ok"]
        report["launches"][l] = r
    report["result"] = "R3_03A_RUNTIME_SEMANTIC_CONFORMANCE_PASS" if ok_all else "R3_03A_RUNTIME_SEMANTIC_CONFORMANCE_FAIL"
    (out / "CONFORMANCE_RESULT.json").write_text(json.dumps(report, indent=1, sort_keys=True), encoding="utf-8")
    print(report["result"])
    return 0 if ok_all else 1


if __name__ == "__main__":
    sys.exit(main())
