"""R3-04 mechanism / conformance phase driver (10 trace-enabled launches, NO timing claim). NOT EXECUTED by its author.

Exact aggregate retention metrics (decode miss bytes/token, hits, evictions, protected-set effect, resident peak) are derived from the retention trace of
one launch per (arm, budget); plus conformance of A/S/P on the reused R3-03B runtime (frozen R2 replay via the R3-03A analyser), S-vs-A decision identity, and
P timing-OFF vs ON canonical identity. Each trace is reduced to a compact record (sha256 + size kept) and deleted by this driver once it is no longer needed.

  python tools\\r3_04_mechanism.py --runtime <exe> --runtime-sha256 <sha> --protocol-freeze-sha256 <sha> --out-dir <fresh dir>
         --attest-clean-environment [--dry-run] [--resume] [--shutdown-method legacy|own_console_helper]
  python tools\\r3_04_mechanism.py --gates --out-dir <dir>          (offline: evaluate the fail-closed gates from the compact records)
"""
from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import sys
import time
import urllib.request
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional

TOOLS = Path(__file__).resolve().parent
REPO = TOOLS.parent
for _p in (str(REPO), str(TOOLS)):
    if _p not in sys.path:
        sys.path.insert(0, _p)
import r3_03b_conformance as C  # noqa: E402
import r3_04_acquire as A  # noqa: E402  (protocol/opening loaders, shared validate_protocol module)
import r3_04_shutdown as S  # noqa: E402

V = A.V
PORT = 18086
PER_LAUNCH_MIN_FREE = 14 * 1024 ** 3
INITIAL_MIN_FREE = 25 * 1024 ** 3
N_LAYERS, PASSES = 48, 514
SHUTDOWN_LATENCY_LIMIT_S = 60.0   # SHUTDOWN_PLAN success criterion (tooling report only; not a scientific gate)


def write_atomic(path: Path, obj: Any) -> None:
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text(json.dumps(obj, sort_keys=True, indent=1), encoding="utf-8")
    tmp.replace(path)


def mechanism_counts(trace: Path, prefill_passes: int = V.PREFILL_PASSES, n_decode_tokens: int = V.N_PREDICT) -> Dict[str, Any]:
    """Exact counters from the POLICY_EVENT rows (completion rows are skipped). miss bytes = misses x bytes_of_layer (per-expert slice size of that layer)."""
    tot = {"events": 0, "hits": 0, "misses": 0, "miss_bytes": 0, "evictions": 0, "protected_events": 0, "protected_entries": 0}
    dec = dict(tot)
    peak = 0
    with trace.open("rb") as f:
        for line in f:
            row = json.loads(line)
            if "complete" in row:
                continue
            p = row["semantic_event"]["pass"]
            d = {"events": 1, "hits": len(row["hit_experts"]), "misses": len(row["miss_experts"]), "miss_bytes": len(row["miss_experts"]) * row["bytes_of_layer"],
                 "evictions": len(row["evicted"]), "protected_events": 1 if row.get("protected_before") else 0, "protected_entries": len(row.get("protected_before") or [])}
            peak = max(peak, row["policy_resident_bytes_after"])
            for k, v in d.items():
                tot[k] += v
                if p >= prefill_passes:
                    dec[k] += v
    return {"all_passes": tot, "decode_passes": dec, "prefill_passes_assumed": prefill_passes, "decode_tokens_divisor": n_decode_tokens, "resident_peak_bytes": peak,
            "decode_miss_bytes_per_token": dec["miss_bytes"] / n_decode_tokens, "decode_evictions_per_token": dec["evictions"] / n_decode_tokens, "decode_hits_per_token": dec["hits"] / n_decode_tokens}


def shutdown_path_failure(rec: Dict[str, Any]) -> Optional[str]:
    """Fail-closed check of the requested shutdown path. A requested own_console_helper that failed or fell back to the legacy request is a failure, never silently accepted."""
    if rec.get("shutdown_method_requested") != "own_console_helper":
        return None
    sd = rec.get("shutdown") or {}
    if sd.get("method_used") != "own_console_helper" or sd.get("helper_returncode") != 0:
        return f"requested own_console_helper but method_used={sd.get('method_used')!r}, helper_returncode={sd.get('helper_returncode')!r}"
    return None


def shutdown_validation(recs: Dict[int, Dict[str, Any]]) -> Dict[str, Any]:
    """Real-model validation of the shutdown helper (reported next to, never inside, the mechanism status). SHUTDOWN_PLAN criterion: no launch above 60 s."""
    lat = [(r.get("shutdown") or {}).get("latency_s") for r in recs.values()]
    ok = bool(recs) and all(isinstance(x, (int, float)) for x in lat) and all(r.get("shutdown_mode") == "graceful" and shutdown_path_failure(r) is None
                                                                            and (r.get("shutdown_method_requested") == "own_console_helper") for r in recs.values())
    ok = ok and max(lat) <= SHUTDOWN_LATENCY_LIMIT_S
    return {"status": "HELPER_VALIDATED_ON_REAL_MODEL" if ok else "HELPER_NOT_VALIDATED", "latency_limit_s": SHUTDOWN_LATENCY_LIMIT_S,
            "max_latency_s": max([x for x in lat if isinstance(x, (int, float))], default=None), "latencies_s": lat}


def evaluate_gates(recs: Dict[int, Dict[str, Any]], sham_vs_a: Dict[str, Dict[str, Any]], off_vs_on: Optional[Dict[str, Any]]) -> Dict[str, Any]:
    """Fail-closed gates (pure function of the compact records). The size or sign of the retention improvement is never a gate."""
    fails: List[str] = []
    sched = V.mechanism_schedule()
    if sorted(recs) != [m["mseq"] for m in sched]:
        fails.append("missing or extra mechanism records")
    for m in sched:
        r = recs.get(m["mseq"])
        if r is None:
            continue
        tag = f"M{m['mseq']:02d}"
        if r.get("output_token_digest") != V.TOKEN_DIGEST: fails.append(f"{tag}: token digest != frozen")
        if r.get("policy_id") != m["policy_id"] or r.get("budget") != m["budget"]: fails.append(f"{tag}: identity != frozen schedule")
        if r.get("runtime_sha256") != V.RUNTIME_SHA256: fails.append(f"{tag}: runtime sha256")
        rp = r.get("replay") or {}
        if not rp.get("ok") or rp.get("semantic_events") != V.EVENTS or rp.get("passes") != PASSES: fails.append(f"{tag}: replay/semantics (ok={rp.get('ok')}, events={rp.get('semantic_events')}, passes={rp.get('passes')})")
        if rp.get("budget_violations"): fails.append(f"{tag}: budget violation")
        if r.get("duplicate_event_refusals"): fails.append(f"{tag}: duplicate-event refusal")
        if r.get("shutdown_mode") != "graceful": fails.append(f"{tag}: shutdown not graceful")
        why = shutdown_path_failure(r)
        if why: fails.append(f"{tag}: {why}")
        pt = r.get("policy_timing_records", [])
        if m["policy_timing"] == "ON":
            if len(pt) != 1: fails.append(f"{tag}: policy_timing records {len(pt)} != 1")
            else:
                fails += [f"{tag}: {x}" for x in C.validate_timing_record(pt[0], V.EVENTS, (rp.get("total_evictions") or 0))]
        elif pt:
            fails.append(f"{tag}: policy_timing present although OFF")
    for b in V.BUDGET_ORDER:
        c = sham_vs_a.get(b)
        if not c or not c.get("ok"): fails.append(f"{b} GiB: S decisions differ from A (decomposition invalid) or comparison missing")
    if not off_vs_on or not off_vs_on.get("ok"): fails.append("P timing OFF vs ON canonical traces differ or comparison missing")
    return {"status": "MECHANISM_PASS" if not fails else "MECHANISM_FAIL", "failures": fails, "shutdown_validation": shutdown_validation(recs)}


def run_traced_launch(plan, cfg, runtime: Path, trace: Path, outdir: Path, tag: str, timing_on: bool, method: str) -> Dict[str, Any]:
    import r3_02_acquire as acq
    env = C.build_env(plan, cfg, trace, timing_on)
    so, se = outdir / f"{tag}.stdout.log", outdir / f"{tag}.stderr.log"
    cmd = [str(runtime), "-m", plan["model"]["path"], "-c", str(plan["decoding"]["context"]), "-n", str(C.N_PREDICT), "--host", "127.0.0.1", "--port", str(PORT)]
    payload: Dict[str, Any] = {}
    with so.open("wb") as fo, se.open("wb") as fe:
        proc = subprocess.Popen(cmd, env=env, stdout=fo, stderr=fe, creationflags=S.popen_creationflags(method))          # no shell
        shutdown: Dict[str, Any] = {"mode": "unknown"}
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
            body = {"prompt": acq.workload_prompt_for_request(plan), "n_predict": C.N_PREDICT, "seed": plan["decoding"]["seed"], "temperature": plan["decoding"]["temperature"],
                    "top_k": plan["decoding"]["top_k"], "return_tokens": True, "stream": False}
            req = urllib.request.Request(f"http://127.0.0.1:{PORT}/completion", data=json.dumps(body).encode(), headers={"Content-Type": "application/json"}, method="POST")
            with urllib.request.urlopen(req, timeout=7200) as resp:
                payload = json.loads(resp.read().decode())
        finally:
            shutdown = S.stop_server(proc, S.GRACEFUL_TIMEOUT_S, method)
    text = se.read_text(encoding="utf-8", errors="replace")
    tokens = payload.get("tokens")
    return {"tag": tag, "output_token_digest": acq.T.output_token_digest(tokens) if tokens is not None else None, "n_tokens": len(tokens) if tokens is not None else None,
            "policy_timing_records": C.timing_records(text), "duplicate_event_refusals": text.count(C.DUPLICATE_MARK), "shutdown_mode": shutdown["mode"], "shutdown": shutdown,
            "trace_bytes": trace.stat().st_size if trace.exists() else None, "trace_sha256": C.sha256_file(trace) if trace.exists() else None, "stderr_path": str(se)}


def replay_analysis(trace: Path, sham: bool) -> Dict[str, Any]:
    import r3_03a_trace as T3
    return T3.analyse_file(trace, n_layers=N_LAYERS, expected_passes=PASSES, sham=sham)


def main(argv: Optional[List[str]] = None, *, launcher: Callable[..., Dict[str, Any]] = run_traced_launch, analyser: Callable[..., Dict[str, Any]] = replay_analysis) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--runtime"); ap.add_argument("--runtime-sha256"); ap.add_argument("--protocol-freeze-sha256"); ap.add_argument("--out-dir", required=True)
    ap.add_argument("--shutdown-method", default="legacy", choices=S.METHODS)
    ap.add_argument("--attest-clean-environment", action="store_true"); ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--resume", action="store_true"); ap.add_argument("--gates", action="store_true")
    a = ap.parse_args(argv)
    out = Path(a.out_dir)
    if a.gates:
        g = json.loads((out / "GATE_INPUTS.json").read_text())
        recs = {int(p.stem[1:]): json.loads(p.read_text()) for p in sorted((out / "records").glob("M[0-9][0-9].json"))}
        print(json.dumps(evaluate_gates(recs, g["sham_vs_a"], g["off_vs_on"]), indent=1)); return 0
    for need in ("runtime", "runtime_sha256", "protocol_freeze_sha256"):
        if not getattr(a, need):
            print(f"REFUSED: --{need.replace('_', '-')} is required"); return 1
    fz = A.load_protocol_freeze(a.protocol_freeze_sha256)
    if a.runtime_sha256 != V.RUNTIME_SHA256:
        print("REFUSED: runtime sha256 is not the frozen R3-03B runtime"); return 1
    sched = V.mechanism_schedule()
    if fz["mechanism_schedule_sha256"] != V.schedule_sha(sched):
        print("REFUSED: mechanism schedule hash differs from the frozen schedule"); return 1
    if a.dry_run:
        print(json.dumps({"dry_run": True, "launches": len(sched), "schedule": sched, "schedule_sha256": fz["mechanism_schedule_sha256"]}, indent=1)); return 0
    if not a.attest_clean_environment:
        print("REFUSED: --attest-clean-environment required"); return 1
    runtime = Path(a.runtime)
    if C.sha256_file(runtime) != a.runtime_sha256:
        print("REFUSED: runtime file sha256 mismatch"); return 1
    if out.exists() and not a.resume:
        print("REFUSED: out-dir exists (use --resume only to continue this phase)"); return 1
    (out / "launches").mkdir(parents=True, exist_ok=True); (out / "records").mkdir(exist_ok=True); (out / "traces").mkdir(exist_ok=True)
    if shutil.disk_usage(out).free < INITIAL_MIN_FREE:
        print("REFUSED: less than 25 GiB free"); return 1
    plan = json.loads((REPO / "experiments/R3-02-calibration-mechanism/ACQUISITION_PLAN.json").read_text())
    import r3_02_acquire as acq
    sham_vs_a: Dict[str, Dict[str, Any]] = {}
    off_vs_on: Optional[Dict[str, Any]] = None
    keep: Dict[str, Path] = {}
    gi = out / "GATE_INPUTS.json"
    if gi.exists():
        d = json.loads(gi.read_text()); sham_vs_a, off_vs_on = d["sham_vs_a"], d["off_vs_on"]
    for tp in sorted((out / "traces").glob("M*.jsonl")):                    # resume: keep traces of recorded launches, drop half-written ones
        if (out / "records" / f"{tp.name[:3]}.json").exists():
            keep[tp.stem] = tp
        else:
            tp.unlink()
    try:
        for m in sched:
            rp = out / "records" / f"M{m['mseq']:02d}.json"
            if rp.exists():
                continue
            if shutil.disk_usage(out).free < PER_LAUNCH_MIN_FREE:
                print("REFUSED: less than 14 GiB free before a traced launch"); return 1
            if m["mseq"] == 1 or sched[m["mseq"] - 2]["budget"] != m["budget"]:
                acq.default_free_ram_check(V.BUDGETS[m["budget"]]["bytes"], Path(plan["model"]["path"]))
            time.sleep(V.COOLDOWN_BETWEEN_BLOCKS_S)
            tag = f"M{m['mseq']:02d}-{m['config_id']}-{m['policy_timing']}"
            trace = out / "traces" / f"{tag}.jsonl"
            cfg = V.config(m["budget"], m["arm"])
            rec = launcher(plan, cfg, runtime, trace, out / "launches", tag, m["policy_timing"] == "ON", a.shutdown_method)
            rec.update({"artifact": "R3-04-MECHANISM-RECORD", **m, "runtime_sha256": a.runtime_sha256, "budget": m["budget"], "shutdown_method_requested": a.shutdown_method})
            bad = rec.get("shutdown_mode") != "graceful" and "shutdown not graceful" or shutdown_path_failure(rec)
            if bad:                                                            # fail closed: no further model launches; the failed launch is kept for inspection, never as an accepted record
                write_atomic(out / "records" / f"M{m['mseq']:02d}.FAILED.json", rec)
                print(f"R3_04_MECHANISM_ABORTED at M{m['mseq']:02d}: {bad}"); return 1
            res = analyser(trace, m["arm"] == "S")
            rec["replay"] = res
            rec["counts"] = mechanism_counts(trace) if res.get("ok") else None
            write_atomic(rp, rec)                                              # compact record persisted before any trace is deleted
            keep[tag] = trace
            if m["arm"] == "S":
                a_tag = next(t for t in keep if t.startswith(f"M{m['mseq'] - 1:02d}-"))
                sham_vs_a[m["budget"]] = C.compare_sham_vs_b(keep[a_tag], trace)
                for t in (a_tag, tag):
                    keep.pop(t).unlink(missing_ok=True)
            elif m["policy_timing"] == "OFF":
                on_tag = next(t for t in keep if t.startswith(f"M{m['mseq'] - 1:02d}-"))
                off_vs_on = C.compare_canonical(keep[on_tag], trace)
                for t in (on_tag, tag):
                    keep.pop(t).unlink(missing_ok=True)
            elif m["arm"] == "P" and not (m["budget"] == V.PRIMARY_BUDGET):
                keep.pop(tag).unlink(missing_ok=True)
            write_atomic(gi, {"sham_vs_a": sham_vs_a, "off_vs_on": off_vs_on})
    except KeyboardInterrupt:
        print("R3_04_MECHANISM_INTERRUPTED; resume with --resume (traces of the current launch are not trusted)"); return 130
    recs = {int(p.stem[1:]): json.loads(p.read_text()) for p in sorted((out / "records").glob("M[0-9][0-9].json"))}
    g = evaluate_gates(recs, sham_vs_a, off_vs_on)
    write_atomic(out / "MECHANISM_GATES.json", g)
    print(g["status"]); return 0 if g["status"] == "MECHANISM_PASS" else 1


if __name__ == "__main__":
    sys.exit(main())
