"""R3-04 timing acquisition driver (three arms A/S/P, per-budget campaigns, fixed frozen schedule). NOT EXECUTED by its author.

  python tools\\r3_04_acquire.py --runtime <exe> --runtime-sha256 <sha> --protocol-freeze-sha256 <sha> --opening-freeze-sha256 <sha>
         --out-dir <fresh dir> --attest-clean-environment [--dry-run] [--resume] [--shutdown-method legacy|own_console_helper]
  python tools\\r3_04_acquire.py --analyze --out-dir <dir>      (refuses unless all 183 launches are accepted; offline, no model)

Never uses a shell. Fails closed on any identity mismatch. No optional stopping: N per budget is a constant of the frozen protocol, there is no N option,
and no estimator runs before the complete fixed schedule is accepted. The opening freeze (OPENING_FREEZE.json) does not exist yet: it is created at the
next host gate, after the model-free shutdown probe and the mechanism phase, and records this driver's sha256_lf.
"""
from __future__ import annotations

import argparse
import importlib.util
import json
import shutil
import subprocess
import sys
import time
import urllib.request
from pathlib import Path
from typing import Any, Dict, List, Optional

TOOLS = Path(__file__).resolve().parent
REPO = TOOLS.parent
for _p in (str(REPO), str(TOOLS)):
    if _p not in sys.path:
        sys.path.insert(0, _p)
import r3_03b_conformance as C  # noqa: E402
import r3_03b_e2e as E  # noqa: E402
import r3_04_shutdown as S  # noqa: E402

PROTO_DIR = REPO / "experiments" / "R3-04-net-runtime-value"


def _load_validate():
    spec = importlib.util.spec_from_file_location("r3_04_validate_protocol", PROTO_DIR / "validate_protocol.py")
    m = importlib.util.module_from_spec(spec)             # type: ignore[arg-type]
    sys.modules["r3_04_validate_protocol"] = m
    spec.loader.exec_module(m)                             # type: ignore[union-attr]
    return m


V = _load_validate()
PORT = 18085
GRACEFUL_STOP_TIMEOUT_S = S.GRACEFUL_TIMEOUT_S
MAX_ATTEMPTS_PER_SLOT = 2
MIN_FREE_BYTES = 2 * 1024 ** 3
OPENING_FREEZE = PROTO_DIR / "OPENING_FREEZE.json"
TOTAL_LAUNCHES = 183


def load_protocol_freeze(expected_sha: str) -> Dict[str, Any]:
    fz = json.loads((PROTO_DIR / "FREEZE.json").read_text(encoding="utf-8"))
    if V.freeze_sha(fz) != fz.get("freeze_sha256") or fz["freeze_sha256"] != expected_sha:
        raise SystemExit("REFUSED: R3-04 protocol FREEZE.json hash mismatch or --protocol-freeze-sha256 does not match")
    for name, h in fz["files_sha256_lf"].items():
        if V.sha_lf(PROTO_DIR / name) != h:
            raise SystemExit(f"REFUSED: protocol file {name} differs from the frozen hash")
    if fz["timing_schedule_sha256"] != V.schedule_sha(V.timing_schedule()):
        raise SystemExit("REFUSED: schedule hash differs from the frozen schedule")
    if fz["runtime_sha256"] != V.RUNTIME_SHA256:
        raise SystemExit("REFUSED: frozen runtime sha256 inconsistency")
    return fz


def load_opening_freeze(expected_sha: str, protocol_sha: str, driver_path: Path) -> Dict[str, Any]:
    if not OPENING_FREEZE.exists():
        raise SystemExit("REFUSED: OPENING_FREEZE.json does not exist (the opening gate has not been passed)")
    of = json.loads(OPENING_FREEZE.read_text(encoding="utf-8"))
    if V.freeze_sha(of) != of.get("freeze_sha256") or of["freeze_sha256"] != expected_sha:
        raise SystemExit("REFUSED: opening freeze hash mismatch or --opening-freeze-sha256 does not match")
    if of["protocol_freeze_sha256"] != protocol_sha:
        raise SystemExit("REFUSED: opening freeze belongs to another protocol freeze")
    if V.sha_lf(driver_path) != of["driver_sha256_lf"]:
        raise SystemExit("REFUSED: this driver differs from the frozen driver_sha256_lf")
    if of.get("runtime_sha256") != V.RUNTIME_SHA256:
        raise SystemExit("REFUSED: opening freeze runtime sha256 differs from the protocol runtime")
    return of


AMEND_DIR = REPO / "experiments" / "R3-04-campaign-efficiency-amendment"
GATE2_DIR = PROTO_DIR / "gate2-mechanism-evidence"


def amended_schedule(blocks: Dict[str, int]) -> List[Dict[str, Any]]:
    """The frozen timing schedule restricted to budgets with blocks > 0: same slots, order, arms and cooldowns; seq renumbered 1..N, frozen_seq kept."""
    out: List[Dict[str, Any]] = []
    for s in V.timing_schedule():
        n = blocks.get(s["budget"], 0)
        if n and (s["kind"] == "warmup" or s["block"] <= n):
            t = dict(s)
            t["frozen_seq"], t["seq"] = s["seq"], len(out) + 1
            out.append(t)
    return out


def load_amendment(expected_sha: str, protocol_sha: str):
    """Campaign-efficiency amendment gate: freeze hash, file hashes, parents (protocol + Gate 2), primary invariants, schedule hash. Fails closed."""
    fz = json.loads((AMEND_DIR / "FREEZE.json").read_text(encoding="utf-8"))
    if V.freeze_sha(fz) != fz.get("freeze_sha256") or fz["freeze_sha256"] != expected_sha:
        raise SystemExit("REFUSED: amendment FREEZE.json hash mismatch or --amendment-sha256 does not match")
    for name, h in fz["files_sha256_lf"].items():
        if V.sha_lf(AMEND_DIR / name) != h:
            raise SystemExit(f"REFUSED: amendment file {name} differs from the frozen hash")
    am = json.loads((AMEND_DIR / "AMENDMENT.json").read_text(encoding="utf-8"))
    if am["parents"]["protocol_freeze_sha256"] != protocol_sha:
        raise SystemExit("REFUSED: amendment belongs to another protocol freeze")
    g2 = json.loads((GATE2_DIR / "FREEZE.json").read_text(encoding="utf-8"))
    if (V.freeze_sha(g2) != g2["freeze_sha256"] or g2["freeze_sha256"] != am["parents"]["gate2_freeze_sha256"] or g2["classification"] != "R3_04_MECHANISM_PASS"
            or g2["shutdown_helper_status"] != "R3_04_SHUTDOWN_HELPER_VALIDATED_ON_REAL_MODEL"):
        raise SystemExit("REFUSED: Gate 2 freeze is not the amendment's parent MECHANISM_PASS / helper-validated freeze")
    blocks = am["design"]["blocks"]
    if blocks.get(V.PRIMARY_BUDGET) != V.BUDGETS[V.PRIMARY_BUDGET]["n_blocks"] or any(n != V.BUDGETS[b]["n_blocks"] for b, n in blocks.items()):
        raise SystemExit("REFUSED: amendment changes a frozen N (primary N and kept budgets must equal the protocol N)")
    if am.get("shutdown_method_required") != "own_console_helper":
        raise SystemExit("REFUSED: amendment does not require the validated shutdown helper")
    sched = amended_schedule(blocks)
    if V.schedule_sha(sched) != am["amended_schedule_sha256"] or len(sched) != am["design"]["physical_launches"]:
        raise SystemExit("REFUSED: amended schedule differs from the frozen amended schedule")
    return am, sched


def slot_config(slot: Dict[str, Any]) -> Dict[str, Any]:
    return V.config(slot["budget"], slot["arm"])


def run_launch(plan: Dict[str, Any], cfg: Dict[str, Any], runtime: Path, out_dir: Path, slot: Dict[str, Any], attempt: int,
               runtime_sha: str, method: str) -> Dict[str, Any]:
    import r3_02_acquire as acq
    tag = f"{slot['seq']:03d}-{slot['budget']}GiB-{slot['kind'][0]}{slot['block']:02d}p{slot['position']}-{slot['arm']}-try{attempt}"
    se, so = out_dir / "launches" / f"{tag}.stderr.log", out_dir / "launches" / f"{tag}.stdout.log"
    cmd = [str(runtime), "-m", plan["model"]["path"], "-c", str(plan["decoding"]["context"]), "-n", str(E.N_PREDICT), "--host", "127.0.0.1", "--port", str(PORT)]
    shutdown: Dict[str, Any] = {"mode": "unknown", "method_used": None, "latency_s": None, "helper_returncode": None}
    payload: Dict[str, Any] = {}
    wall, error, interrupted, completed = 0.0, None, False, False
    started = time.time()
    with so.open("wb") as fo, se.open("wb") as fe:
        proc = subprocess.Popen(cmd, env=E.build_env_e2e(plan, cfg), stdout=fo, stderr=fe, creationflags=S.popen_creationflags(method))   # no shell
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
            body = {"prompt": acq.workload_prompt_for_request(plan), "n_predict": E.N_PREDICT, "seed": plan["decoding"]["seed"], "temperature": plan["decoding"]["temperature"],
                    "top_k": plan["decoding"]["top_k"], "return_tokens": True, "stream": False}
            req = urllib.request.Request(f"http://127.0.0.1:{PORT}/completion", data=json.dumps(body).encode(), headers={"Content-Type": "application/json"}, method="POST")
            t0 = time.monotonic()
            with urllib.request.urlopen(req, timeout=3600) as resp:
                payload = json.loads(resp.read().decode())
            wall, completed = time.monotonic() - t0, True
        except KeyboardInterrupt:
            interrupted, error = True, "KeyboardInterrupt during launch/inference"
        except Exception as e:                                   # recorded, never swallowed
            error = f"{type(e).__name__}: {e}"
        finally:
            try:
                shutdown = hard_stop_dict(proc, method) if interrupted else S.stop_server(proc, GRACEFUL_STOP_TIMEOUT_S, method)   # shutdown only after the response (or failure)
            except KeyboardInterrupt:
                interrupted, error = True, "KeyboardInterrupt during graceful shutdown wait"
                shutdown = {"mode": E.hard_stop(proc), "method_used": method, "latency_s": None, "helper_returncode": None}
    text = se.read_text(encoding="utf-8", errors="replace")
    tokens = payload.get("tokens")
    return {"artifact": "R3-04-LAUNCH-RECORD", **slot, "attempt": attempt, "runtime": str(runtime), "runtime_sha256": runtime_sha,
            "output_token_digest": acq.T.output_token_digest(tokens) if tokens is not None else None, "n_tokens": len(tokens) if tokens is not None else None, "tokens": tokens,
            "wall_seconds": wall, "server_timings": payload.get("timings"), "policy_timing_records": C.timing_records(text),
            "duplicate_event_refusals": text.count(C.DUPLICATE_MARK), "shutdown_mode": shutdown["mode"], "shutdown": shutdown, "shutdown_method_requested": method,
            "stderr_path": str(se), "error": error, "interrupted": interrupted, "request_completed": completed, "shutdown_wait_s": shutdown.get("latency_s"),
            "shutdown_timeout": shutdown["mode"] == "terminate_fallback", "graceful_timeout_s": GRACEFUL_STOP_TIMEOUT_S, "started_unix": started, "finished_unix": time.time()}


def hard_stop_dict(proc, method: str) -> Dict[str, Any]:
    """Operator-interrupt path only: never used for a normal launch (reuses the R3-03B hard_stop unchanged)."""
    return {"mode": E.hard_stop(proc), "method_used": method, "latency_s": None, "helper_returncode": None}


def validate(rec: Dict[str, Any], slot: Dict[str, Any], require_helper: bool = False) -> List[str]:
    f = E.validate_launch(rec, V.TOKEN_DIGEST)
    if require_helper:                                            # amended campaign: the validated own_console_helper must have been requested AND used (no silent legacy fallback)
        sd = rec.get("shutdown") or {}
        if rec.get("shutdown_method_requested") != "own_console_helper" or sd.get("method_used") != "own_console_helper" or sd.get("helper_returncode") != 0:
            f.append("shutdown helper required but not used successfully (fail closed)")
    if rec.get("policy_id") != slot["policy_id"] or rec.get("arm") != slot["arm"] or rec.get("budget") != slot["budget"]:
        f.append("record identity differs from the frozen slot")
    if rec.get("runtime_sha256") != V.RUNTIME_SHA256:
        f.append("runtime sha256 != frozen runtime")
    return f


def flatten(rec: Dict[str, Any]) -> Dict[str, Any]:
    t = rec["server_timings"]
    return {"budget": rec["budget"], "block": rec["block"], "arm": rec["arm"], "kind": rec["kind"], "predicted_ms": t["predicted_ms"], "predicted_n": t["predicted_n"],
            "policy_timing": rec["policy_timing_records"][0]}


def analyze(out_dir: Path, sched: Optional[List[Dict[str, Any]]] = None) -> Dict[str, Any]:
    """Frozen estimators. Refuses to run unless the complete fixed schedule (183 launches, or the amended schedule when amended) is accepted. Warmups never enter an estimator."""
    sched = sched if sched is not None else V.timing_schedule()
    acc = E.load_accepted(out_dir, sched, V.RUNTIME_SHA256)
    if len(acc) != len(sched):
        raise ValueError(f"analysis refused: {len(acc)} of {len(sched)} launches accepted (no interim analysis, no optional stopping)")
    flat = [flatten(r) for r in acc.values()]
    res: Dict[str, Any] = {"budgets": {}}
    for b in V.BUDGET_ORDER:
        rs = [x for x in flat if x["budget"] == b]
        if not rs:
            continue                                              # budget not in the (amended) schedule
        wall, pol, tps = V.contrasts(rs, V.wall_ms_per_token), V.contrasts(rs, V.policy_ms_per_token), V.contrasts(rs, V.tps)
        entry = {"role": V.BUDGETS[b]["role"], "n_blocks": len(wall["blocks"]), "wall_ms_per_token": {k: V.t_interval(wall[k]) for k in ("DELTA_COST", "DELTA_RETENTION", "DELTA_NET")},
                 "policy_ms_per_token": {k: V.t_interval(pol[k]) for k in ("DELTA_COST", "DELTA_RETENTION", "DELTA_NET")},
                 "tokens_per_second_reversed_sign": {k: V.t_interval(tps[k]) for k in ("DELTA_COST", "DELTA_RETENTION", "DELTA_NET")},
                 "rho_cost_fieller": V.fieller(wall["DELTA_COST"], pol["DELTA_COST"]),
                 "delta_retention_adj_wall_minus_policy": V.t_interval([w - p for w, p in zip(wall["DELTA_RETENTION"], pol["DELTA_RETENTION"])])}
        if b == V.PRIMARY_BUDGET:
            adj = [w - p for w, p in zip(wall["DELTA_RETENTION"], pol["DELTA_RETENTION"])]
            entry["PRIMARY_CLASSIFICATION"] = V.classify_primary(wall["DELTA_NET"], adj, None)
        else:
            entry["classification"] = "NONE (descriptive budget; cannot override the primary classification)"
        res["budgets"][b] = entry
    return res


def main(argv: Optional[List[str]] = None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--runtime"); ap.add_argument("--runtime-sha256"); ap.add_argument("--protocol-freeze-sha256"); ap.add_argument("--opening-freeze-sha256"); ap.add_argument("--amendment-sha256")
    ap.add_argument("--out-dir", required=True); ap.add_argument("--shutdown-method", default="legacy", choices=S.METHODS)
    ap.add_argument("--attest-clean-environment", action="store_true"); ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--resume", action="store_true"); ap.add_argument("--analyze", action="store_true")
    a = ap.parse_args(argv)
    out = Path(a.out_dir)
    if a.analyze:
        asched = None
        if a.amendment_sha256:
            asched = load_amendment(a.amendment_sha256, load_protocol_freeze(a.protocol_freeze_sha256)["freeze_sha256"])[1] if a.protocol_freeze_sha256 else None
            if asched is None:
                print("REFUSED: --amendment-sha256 requires --protocol-freeze-sha256"); return 1
        print(json.dumps(analyze(out, asched), indent=1)); return 0
    for need in ("runtime", "runtime_sha256", "protocol_freeze_sha256", "opening_freeze_sha256"):
        if not getattr(a, need):
            print(f"REFUSED: --{need.replace('_', '-')} is required"); return 1
    fz = load_protocol_freeze(a.protocol_freeze_sha256)
    if a.runtime_sha256 != V.RUNTIME_SHA256:
        print("REFUSED: runtime sha256 is not the frozen R3-03B runtime"); return 1
    sched = V.timing_schedule()
    amended = False
    if a.amendment_sha256:
        _am, sched = load_amendment(a.amendment_sha256, fz["freeze_sha256"]); amended = True
        if a.shutdown_method != "own_console_helper":
            print("REFUSED: the amended campaign requires --shutdown-method own_console_helper"); return 1
    else:
        assert len(sched) == TOTAL_LAUNCHES
    if a.dry_run:
        accepted = len(E.load_accepted(out, sched, a.runtime_sha256)) if out.exists() else 0
        print(json.dumps({"dry_run": True, "launches": len(sched), "accepted_records_found": accepted, "next_slot": sched[accepted] if accepted < len(sched) else None,
                          "first_8": sched[:8], "schedule_sha256": V.schedule_sha(sched) if amended else fz["timing_schedule_sha256"], "amended": amended, "opening_freeze_present": OPENING_FREEZE.exists()}, indent=1)); return 0
    _of = load_opening_freeze(a.opening_freeze_sha256, fz["freeze_sha256"], Path(__file__))
    if _of.get("amendment_freeze_sha256") != a.amendment_sha256:            # the opening freeze decides whether the campaign is amended; the flag must match it exactly
        print("REFUSED: --amendment-sha256 must equal the opening freeze's amendment_freeze_sha256 (absent in both for the unamended campaign)"); return 1
    if not a.attest_clean_environment:
        print("REFUSED: --attest-clean-environment required (Chrome/all browsers and streaming video closed, no heavy background load, fixed power mode)"); return 1
    runtime = Path(a.runtime)
    if E.sha256_file(runtime) != a.runtime_sha256:
        print("REFUSED: runtime file sha256 mismatch"); return 1
    if out.exists() and not a.resume:
        print("REFUSED: out-dir exists (use --resume only to continue this campaign)"); return 1
    if a.resume:
        op = out / "CAMPAIGN_OPENED.json"
        if (not op.exists() or json.loads(op.read_text())["protocol_freeze_sha256"] != a.protocol_freeze_sha256 or json.loads(op.read_text())["runtime_sha256"] != a.runtime_sha256
                or json.loads(op.read_text()).get("amendment_freeze_sha256") != a.amendment_sha256):
            print("REFUSED: --resume requires this campaign's CAMPAIGN_OPENED.json with the same freezes and runtime"); return 1
        for marker in ("CAMPAIGN_COMPLETE.json", "CAMPAIGN_ABORTED.json"):
            if (out / marker).exists():
                print(f"REFUSED: campaign already closed ({marker})"); return 1
    (out / "launches").mkdir(parents=True, exist_ok=True); (out / "records").mkdir(exist_ok=True)
    if shutil.disk_usage(out).free < MIN_FREE_BYTES:
        print("REFUSED: less than 2 GiB free"); return 1
    import r3_02_acquire as acq
    plan = json.loads((REPO / "experiments/R3-02-calibration-mechanism/ACQUISITION_PLAN.json").read_text())
    if a.resume:
        accepted = E.load_accepted(out, sched, a.runtime_sha256)
        made = E.classify_orphans(out, sched)
        n_prev = len(list(out.glob("CAMPAIGN_RESUMED_*.json")))
        E.write_atomic(out / f"CAMPAIGN_RESUMED_{n_prev + 1:02d}.json", {"accepted_records_at_resume": sorted(accepted), "orphan_markers_created": made, "resumed_unix": time.time()})
    else:
        E.write_atomic(out / "CAMPAIGN_OPENED.json", {"protocol_freeze_sha256": a.protocol_freeze_sha256, "opening_freeze_sha256": a.opening_freeze_sha256, "amendment_freeze_sha256": a.amendment_sha256, "runtime_sha256": a.runtime_sha256,
                                                      "shutdown_method": a.shutdown_method, "started_unix": time.time(), "launches": len(sched)})
    ram_checked = set()
    try:
        for slot in sched:
            done = out / "records" / f"{slot['seq']:03d}.json"
            if done.exists():
                continue
            if slot["budget"] not in ram_checked:
                acq.default_free_ram_check(V.BUDGETS[slot["budget"]]["bytes"], Path(plan["model"]["path"]))     # frozen free-RAM feasibility gate, per budget
                ram_checked.add(slot["budget"])
            admitted = False
            for attempt in range(E.prior_attempts(out, slot["seq"]) + 1, MAX_ATTEMPTS_PER_SLOT + 1):          # one rerun of the same slot, then abort
                time.sleep(slot["cooldown_before_s"])
                rec = run_launch(plan, slot_config(slot), runtime, out, slot, attempt, a.runtime_sha256, a.shutdown_method)
                if rec.get("interrupted"):
                    rec.update(status="INTERRUPTED_NONCOUNTED", failures=["operator interrupt"], admitted=False)
                    E.write_atomic(out / "records" / f"{slot['seq']:03d}.try{attempt}.INTERRUPTED.json", rec)
                    E.write_atomic(out / "CAMPAIGN_INTERRUPTED.json", {"seq": slot["seq"], "attempt": attempt, "note": "resume with --resume; this attempt is never counted"})
                    print(f"R3_04_INTERRUPTED at seq {slot['seq']} try{attempt}"); return 130
                fails = validate(rec, slot, require_helper=amended) + ([rec["error"]] if rec.get("error") else [])
                rec["failures"], rec["admitted"] = fails, not fails
                if fails:
                    E.write_atomic(out / "records" / f"{slot['seq']:03d}.try{attempt}.FAILED.json", rec)
                else:
                    E.write_atomic(done, rec); admitted = True
                    break
            if not admitted:
                E.write_atomic(out / "CAMPAIGN_ABORTED.json", {"seq": slot["seq"], "reason": "second failure of the same slot; no imputation, no replacement"})
                print(f"R3_04_ABORTED at seq {slot['seq']}"); return 1
    except KeyboardInterrupt:
        E.write_atomic(out / "CAMPAIGN_INTERRUPTED.json", {"during": "cooldown or bookkeeping", "note": "no launch was running; resume with --resume"})
        print("R3_04_INTERRUPTED (no launch running)"); return 130
    E.write_atomic(out / "CAMPAIGN_COMPLETE.json", {"launches": len(sched), "analysis": analyze(out, sched if amended else None)})
    print("R3_04_ACQUISITION_COMPLETE"); return 0


if __name__ == "__main__":
    sys.exit(main())
