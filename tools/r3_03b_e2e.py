"""R3-03B end-to-end acquisition driver (8 GiB primary endpoint, b_selected vs predicted_protect_sham).

Frozen design (see experiments/R3-03B-end-to-end-opening/E2E_FREEZE.json): N = 20 ABBA blocks = 80 counted launches, no warmups,
ARGINE_POLICY_TIMING=1 in every launch, NO event trace, graceful (Ctrl-C) shutdown so the policy_timing line is emitted.

  python tools\\r3_03b_e2e.py --runtime <exe> --runtime-sha256 <sha> --freeze-sha256 <sha> --out-dir <fresh dir>
         --attest-clean-environment [--dry-run] [--resume]
  python tools\\r3_03b_e2e.py --analyze --out-dir <dir>        (offline analysis of completed records; no model)

Never uses a shell. Fails closed on any identity mismatch. No optional stopping: the block count is a constant, not an option.
"""
from __future__ import annotations

import argparse
import hashlib
import importlib.util
import json
import math
import os
import re
import shutil
import statistics
import subprocess
import sys
import time
import urllib.request
from pathlib import Path
from typing import Any, Dict, List, Optional

REPO = Path(__file__).resolve().parents[1]
TOOLS = Path(__file__).resolve().parent
for _p in (str(REPO), str(TOOLS)):
    if _p not in sys.path:
        sys.path.insert(0, _p)
import r3_03b_conformance as C  # noqa: E402  (validated graceful_stop, timing parsing/validation, env builder)

N_BLOCKS = 20                       # frozen prospective choice (protocol cap); not a CLI option
LAUNCHES_PER_BLOCK = 4
TOTAL_LAUNCHES = N_BLOCKS * LAUNCHES_PER_BLOCK
N_PREDICT = 512
COOLDOWN_WITHIN_BLOCK_S = 15        # R2-12 frozen semantics: adjacent paired controls
COOLDOWN_BETWEEN_BLOCKS_S = 60      # R2-12 frozen semantics: between cells/blocks (also before the first launch)
EQUIV_LO, EQUIV_HI = 0.5, 2.0
PORT = 18084
RUNTIME_SHA = "8d2e1def2bd46f9f395a9e5662bd04cc8eafad6c77a337de2abcff1f82685ba5"   # conformance-frozen R3-03B runtime
FREEZE_PATH = REPO / "experiments" / "R3-03B-end-to-end-opening" / "E2E_FREEZE.json"
AMENDMENT_PATH = REPO / "experiments" / "R3-03B-end-to-end-opening" / "E2E_FREEZE_AMENDMENT_1.json"
GRACEFUL_STOP_TIMEOUT_S = 600       # amendment 1: observed Ctrl-C -> exit latencies reached 152 s (conformance driver cap was 180 s)
MAX_ATTEMPTS_PER_SLOT = 2           # frozen failure rule: one rerun of the same slot, then abort
ACCEPTED_RE = re.compile(r"^(\d{3})\.json$")
MIN_FREE_BYTES = 2 * 1024 ** 3
ARMS = {"A": ("A-8GiB", "b_selected"), "B": ("B-8GiB", "predicted_protect_sham")}


def sha256_file(p: Path) -> str:
    h = hashlib.sha256()
    with p.open("rb") as f:
        for b in iter(lambda: f.read(1 << 20), b""):
            h.update(b)
    return h.hexdigest()


def sha256_lf(p: Path) -> str:
    """sha256 of a text file with CRLF normalised to LF (stable across Windows/Git line-ending conversion)."""
    return hashlib.sha256(p.read_bytes().replace(b"\r\n", b"\n")).hexdigest()


def freeze_sha(obj: Dict[str, Any]) -> str:
    o = {k: v for k, v in obj.items() if k != "freeze_sha256"}
    return hashlib.sha256(json.dumps(o, sort_keys=True, separators=(",", ":")).encode()).hexdigest()


# ----------------------------------------------------------------------------- schedule
def abba_schedule(n_blocks: int = N_BLOCKS) -> List[Dict[str, Any]]:
    """Odd blocks (1,3,..): A B B A ; even blocks: B A A B  (A = b_selected, B = predicted_protect_sham)."""
    out: List[Dict[str, Any]] = []
    for b in range(1, n_blocks + 1):
        order = "ABBA" if b % 2 == 1 else "BAAB"
        for pos, arm in enumerate(order, start=1):
            cfg, pol = ARMS[arm]
            out.append({"seq": len(out) + 1, "block": b, "position": pos, "arm": arm, "config_id": cfg, "policy_id": pol,
                        "cooldown_before_s": COOLDOWN_BETWEEN_BLOCKS_S if pos == 1 else COOLDOWN_WITHIN_BLOCK_S})
    return out


def duration_estimate(per_launch_s: float, n_blocks: int = N_BLOCKS) -> Dict[str, float]:
    sched = abba_schedule(n_blocks)
    model = per_launch_s * len(sched)
    cool = float(sum(s["cooldown_before_s"] for s in sched))
    return {"per_launch_s": per_launch_s, "per_block_s": per_launch_s * 4 + 3 * COOLDOWN_WITHIN_BLOCK_S + COOLDOWN_BETWEEN_BLOCKS_S,
            "model_s": model, "cooldown_s": cool, "total_s": model + cool, "total_h": (model + cool) / 3600.0}


# ----------------------------------------------------------------------------- environment / validation
def build_env_e2e(plan: Dict[str, Any], cfg: Dict[str, Any], base_env: Optional[Dict[str, str]] = None) -> Dict[str, str]:
    env = C.build_env(plan, cfg, Path("unused"), True, base_env)
    env.pop("ARGINE_RETENTION_TRACE", None)                      # no event trace in the timed campaign (would contaminate timing)
    return env


def validate_launch(rec: Dict[str, Any], expected_digest: str) -> List[str]:
    f: List[str] = []
    if rec.get("output_token_digest") != expected_digest:
        f.append("token digest != frozen digest")
    if rec.get("n_tokens") != N_PREDICT:
        f.append("n_tokens != 512")
    if rec.get("shutdown_mode") != "graceful":
        f.append(f"shutdown_mode {rec.get('shutdown_mode')} != graceful")
    pt = rec.get("policy_timing_records", [])
    if len(pt) != 1:
        f.append(f"policy_timing records {len(pt)} != 1")
    else:
        f += C.validate_timing_record(pt[0], C.EXPECTED_EVENTS)
    t = rec.get("server_timings") or {}
    if not (t.get("predicted_n") == N_PREDICT and float(t.get("predicted_ms", 0)) > 0):
        f.append("server timings missing predicted_n==512 / predicted_ms>0")
    if rec.get("interrupted"):
        f.append("operator interrupt")
    if rec.get("shutdown_timeout"):
        f.append("graceful shutdown timed out (terminate fallback)")
    if rec.get("duplicate_event_refusals"):
        f.append("duplicate-event refusal in stderr")
    return f


def run_launch(plan: Dict[str, Any], cfg: Dict[str, Any], runtime: Path, out_dir: Path, slot: Dict[str, Any], attempt: int,
               runtime_sha: str) -> Dict[str, Any]:
    import r3_02_acquire as acq
    tag = f"{slot['seq']:03d}-b{slot['block']:02d}p{slot['position']}-{slot['arm']}-try{attempt}"
    se = out_dir / "launches" / f"{tag}.stderr.log"
    so = out_dir / "launches" / f"{tag}.stdout.log"
    cmd = [str(runtime), "-m", plan["model"]["path"], "-c", str(plan["decoding"]["context"]), "-n", str(N_PREDICT),
           "--host", "127.0.0.1", "--port", str(PORT)]
    shutdown = "unknown"
    payload: Dict[str, Any] = {}
    wall = 0.0
    error: Optional[str] = None
    interrupted = False
    request_completed = False
    shutdown_wait = 0.0
    started_unix = time.time()
    with so.open("wb") as fo, se.open("wb") as fe:
        proc = subprocess.Popen(cmd, env=build_env_e2e(plan, cfg), stdout=fo, stderr=fe)       # no shell
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
            t0 = time.monotonic()
            with urllib.request.urlopen(req, timeout=3600) as resp:
                payload = json.loads(resp.read().decode())
            wall = time.monotonic() - t0
            request_completed = True
        except KeyboardInterrupt:                                # operator interrupt: explicit state, never a silent exit
            interrupted = True
            error = "KeyboardInterrupt during launch/inference"
        except Exception as e:                                   # recorded, never swallowed
            error = f"{type(e).__name__}: {e}"
        finally:
            t_stop = time.monotonic()
            if interrupted:
                shutdown = hard_stop(proc)
            else:
                try:
                    shutdown = C.graceful_stop(proc, GRACEFUL_STOP_TIMEOUT_S)
                except KeyboardInterrupt:                        # operator interrupt while waiting for the server to exit
                    interrupted = True
                    error = "KeyboardInterrupt during graceful shutdown wait"
                    shutdown = hard_stop(proc)
            shutdown_wait = time.monotonic() - t_stop
    text = se.read_text(encoding="utf-8", errors="replace")
    tokens = payload.get("tokens")
    rec = {"artifact": "R3-03B-E2E-LAUNCH-RECORD", **slot, "attempt": attempt, "runtime": str(runtime), "runtime_sha256": runtime_sha,
           "output_token_digest": acq.T.output_token_digest(tokens) if tokens is not None else None,
           "n_tokens": len(tokens) if tokens is not None else None, "tokens": tokens, "wall_seconds": wall,
           "server_timings": payload.get("timings"), "policy_timing_records": C.timing_records(text),
           "duplicate_event_refusals": text.count(C.DUPLICATE_MARK), "shutdown_mode": shutdown, "stderr_path": str(se), "error": error,
           "interrupted": interrupted, "request_completed": request_completed, "shutdown_wait_s": shutdown_wait,
           "shutdown_timeout": shutdown == "terminate_fallback", "graceful_timeout_s": GRACEFUL_STOP_TIMEOUT_S,
           "started_unix": started_unix, "finished_unix": time.time()}
    return rec


def hard_stop(proc: "subprocess.Popen[bytes]") -> str:
    """Operator-interrupt path ONLY (never used for a normal launch): make sure no llama-server is left running."""
    if proc.poll() is not None:
        return "already_exited"
    for action in (proc.terminate, proc.kill):
        try:
            action()
            proc.wait(timeout=30)
            return "operator_interrupt_terminated"
        except subprocess.TimeoutExpired:
            continue
        except KeyboardInterrupt:                                # a second Ctrl+C escalates to kill
            continue
    return "operator_interrupt_still_alive"


def write_atomic(path: Path, obj: Any) -> None:
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text(json.dumps(obj, sort_keys=True, indent=1), encoding="utf-8")
    tmp.replace(path)


# ----------------------------------------------------------------------------- analysis (frozen estimator; offline)
def _t975(df: int) -> float:
    p = REPO / "experiments" / "R3-03B-microbench-evidence" / "validate_evidence.py"
    spec = importlib.util.spec_from_file_location("_r3_03b_mb_validate", p)
    m = importlib.util.module_from_spec(spec)                    # type: ignore[arg-type]
    spec.loader.exec_module(m)                                   # type: ignore[union-attr]
    return m.t975(df)


def block_contrasts(records: List[Dict[str, Any]]) -> List[Dict[str, float]]:
    """Per block: O_b = mean wall ms/token(sham) - mean(b_selected); P_b = mean ns_total/512 (ms) (sham) - (b_selected)."""
    by: Dict[int, Dict[str, List[Dict[str, Any]]]] = {}
    for r in records:
        by.setdefault(r["block"], {"A": [], "B": []})[r["arm"]].append(r)
    out = []
    for b in sorted(by):
        a, s = by[b]["A"], by[b]["B"]
        if len(a) != 2 or len(s) != 2:
            raise ValueError(f"block {b} is not a complete ABBA block")
        wall = lambda rs: statistics.fmean(x["server_timings"]["predicted_ms"] / x["server_timings"]["predicted_n"] for x in rs)
        pol = lambda rs: statistics.fmean(x["policy_timing_records"][0]["ns_total"] / x["n_tokens"] / 1e6 for x in rs)
        out.append({"block": b, "O_ms_per_token": wall(s) - wall(a), "P_ms_per_token": pol(s) - pol(a)})
    return out


def fieller(o: List[float], p: List[float], t: Optional[float] = None) -> Dict[str, Any]:
    n = len(o)
    if n < 3 or len(p) != n:
        raise ValueError("need >= 3 paired blocks")
    t = _t975(n - 1) if t is None else t
    mo, mp = statistics.fmean(o), statistics.fmean(p)
    voo = statistics.variance(o) / n
    vpp = statistics.variance(p) / n
    vop = sum((x - mo) * (y - mp) for x, y in zip(o, p)) / (n - 1) / n
    g = math.inf if mp == 0 else t * t * vpp / (mp * mp)      # mean P == 0: Fieller set unbounded
    res: Dict[str, Any] = {"n_blocks": n, "t": t, "mean_O": mo, "mean_P": mp, "g": g}
    if g >= 1.0:
        return {**res, "rho": None, "lo": None, "hi": None, "bounded": False, "classification": "UNTESTABLE"}
    rho = mo / mp
    disc = voo - 2 * rho * vop + rho * rho * vpp - g * (voo - vop * vop / vpp)
    half = t / abs(mp) * math.sqrt(max(disc, 0.0))
    ctr = rho - g * vop / vpp
    lo, hi = sorted(((ctr - half) / (1 - g), (ctr + half) / (1 - g)))
    if lo >= EQUIV_LO and hi <= EQUIV_HI:
        cls = "EXPLAINED"
    elif hi < EQUIV_LO or lo > EQUIV_HI:
        cls = "REJECTED_UNEXPLAINED"
    else:
        cls = "INCONCLUSIVE"
    return {**res, "rho": rho, "lo": lo, "hi": hi, "bounded": True, "classification": cls}


def analyze(out_dir: Path) -> Dict[str, Any]:
    admitted = list(load_accepted(out_dir, abba_schedule(), None).values())
    bc = block_contrasts(admitted)
    return {"blocks": bc, "fieller": fieller([b["O_ms_per_token"] for b in bc], [b["P_ms_per_token"] for b in bc])}


# ----------------------------------------------------------------------------- accepted records / attempts / resume
def load_accepted(out_dir: Path, sched: List[Dict[str, Any]], runtime_sha: Optional[str]) -> Dict[int, Dict[str, Any]]:
    """Accepted records are exactly records/NNN.json (atomically persisted only after validate_launch passed).

    Fails closed: unknown seq, duplicate slot identity, slot/arm mismatch with the frozen schedule, non-admitted content,
    runtime mismatch, or a gap in the accepted prefix. FAILED / INTERRUPTED / ORPHAN markers and *.try*.* files are never accepted.
    """
    by_seq = {s["seq"]: s for s in sched}
    out: Dict[int, Dict[str, Any]] = {}
    rdir = out_dir / "records"
    for p in sorted(rdir.glob("*.json")) if rdir.exists() else []:
        m = ACCEPTED_RE.match(p.name)
        if not m:
            continue
        seq = int(m.group(1))
        rec = json.loads(p.read_text(encoding="utf-8"))
        if seq not in by_seq:
            raise ValueError(f"{p.name}: seq outside the frozen schedule")
        slot = by_seq[seq]
        for k in ("seq", "block", "position", "arm", "config_id", "policy_id"):
            if rec.get(k) != slot[k]:
                raise ValueError(f"{p.name}: {k} {rec.get(k)!r} != frozen schedule {slot[k]!r}")
        if not rec.get("admitted") or rec.get("failures"):
            raise ValueError(f"{p.name}: persisted record is not admitted")
        problems = validate_launch(rec, C.FROZEN_TOKEN_DIGEST)
        if problems:
            raise ValueError(f"{p.name}: fails the frozen admission rule: {problems}")
        if runtime_sha is not None and rec.get("runtime_sha256") != runtime_sha:
            raise ValueError(f"{p.name}: runtime sha256 differs from the campaign runtime")
        out[seq] = rec
    seqs = sorted(out)
    if seqs != list(range(1, len(seqs) + 1)):
        raise ValueError(f"accepted records are not a contiguous prefix of the schedule: {seqs}")
    return out


def prior_attempts(out_dir: Path, seq: int) -> int:
    """Highest try index already used for this slot by any artifact (logs, FAILED/INTERRUPTED/ORPHAN markers)."""
    best = 0
    pat = re.compile(rf"^{seq:03d}[.-].*?try(\d+)")
    for d in (out_dir / "launches", out_dir / "records"):
        for p in d.glob(f"{seq:03d}*") if d.exists() else []:
            m = pat.match(p.name)
            if m:
                best = max(best, int(m.group(1)))
    return best


def classify_orphans(out_dir: Path, sched: List[Dict[str, Any]]) -> List[str]:
    """Attempts that left logs but no accepted record and no marker (e.g. the driver was interrupted by an old driver version)
    get an explicit, never-counted marker so that they stay recorded as incident evidence."""
    by_seq = {s["seq"]: s for s in sched}
    made: List[str] = []
    ldir, rdir = out_dir / "launches", out_dir / "records"
    for p in sorted(ldir.glob("*.stderr.log")) if ldir.exists() else []:
        m = re.match(r"^(\d{3})-.*-try(\d+)\.stderr\.log$", p.name)
        if not m:
            continue
        seq, k = int(m.group(1)), int(m.group(2))
        if (rdir / f"{seq:03d}.json").exists():
            continue
        if any(rdir.glob(f"{seq:03d}.try{k}.*.json")):
            continue
        text = p.read_text(encoding="utf-8", errors="replace")
        marker = rdir / f"{seq:03d}.try{k}.ORPHAN_NONCOUNTED.json"
        write_atomic(marker, {"artifact": "R3-03B-E2E-ATTEMPT-MARKER", "status": "INCOMPLETE_NONCOUNTED_LAUNCH", "slot": by_seq.get(seq), "attempt": k,
                              "stderr_log": p.name, "stderr_sha256": sha256_file(p), "stderr_bytes": p.stat().st_size,
                              "inference_completed_in_log": "stop processing: n_tokens" in text,
                              "cleaning_up_logged": "cleaning up before exit" in text,
                              "policy_timing_present": C.TIMING_MARK in text,
                              "note": "no persisted launch record; never included in any estimator; the same scientific slot is retried under the next try index"})
        made.append(marker.name)
    return made


# ----------------------------------------------------------------------------- main
def load_freeze(expected_sha: str, amendment_sha: str) -> Dict[str, Any]:
    fz = json.loads(FREEZE_PATH.read_text(encoding="utf-8"))
    if freeze_sha(fz) != fz.get("freeze_sha256") or fz["freeze_sha256"] != expected_sha:
        raise SystemExit("REFUSED: E2E_FREEZE.json hash mismatch or --freeze-sha256 does not match")
    am = json.loads(AMENDMENT_PATH.read_text(encoding="utf-8"))
    if freeze_sha(am) != am.get("freeze_sha256") or am["freeze_sha256"] != amendment_sha:
        raise SystemExit("REFUSED: amendment hash mismatch or --amendment-sha256 does not match")
    if am["parent_freeze_sha256"] != fz["freeze_sha256"] or am["supersedes_driver_sha256_lf"] != fz["driver_sha256_lf"]:
        raise SystemExit("REFUSED: amendment does not belong to this freeze")
    if sha256_lf(Path(__file__)) != am["driver_sha256_lf"]:
        raise SystemExit("REFUSED: this driver differs from the amended frozen driver_sha256_lf")
    return fz


def main(argv: Optional[List[str]] = None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--runtime"); ap.add_argument("--runtime-sha256"); ap.add_argument("--freeze-sha256"); ap.add_argument("--amendment-sha256")
    ap.add_argument("--out-dir", required=True)
    ap.add_argument("--attest-clean-environment", action="store_true")
    ap.add_argument("--dry-run", action="store_true"); ap.add_argument("--resume", action="store_true")
    ap.add_argument("--analyze", action="store_true")
    a = ap.parse_args(argv)
    out = Path(a.out_dir)
    if a.analyze:
        print(json.dumps(analyze(out), indent=1)); return 0
    for need in ("runtime", "runtime_sha256", "freeze_sha256", "amendment_sha256"):
        if not getattr(a, need):
            print(f"REFUSED: --{need.replace('_', '-')} is required"); return 1
    fz = load_freeze(a.freeze_sha256, a.amendment_sha256)
    if a.runtime_sha256 != RUNTIME_SHA or fz["runtime_sha256"] != RUNTIME_SHA:
        print("REFUSED: runtime sha256 is not the conformance-frozen R3-03B runtime"); return 1
    plan = json.loads((REPO / "experiments/R3-02-calibration-mechanism/ACQUISITION_PLAN.json").read_text())
    cfgs = {c["config_id"]: c for c in plan["configurations"]}
    sched = abba_schedule()
    assert len(sched) == TOTAL_LAUNCHES == 80
    if a.dry_run:
        accepted = len(load_accepted(out, sched, a.runtime_sha256)) if out.exists() else 0
        print(json.dumps({"dry_run": True, "launches": len(sched), "blocks": N_BLOCKS, "accepted_records_found": accepted,
                          "remaining_accepted_launches": len(sched) - accepted,
                          "next_slot": sched[accepted] if accepted < len(sched) else None,
                          "first_8": sched[:8], "estimate": duration_estimate(fz["duration_estimate"]["per_launch_s"])}, indent=1)); return 0
    if not a.attest_clean_environment:
        print("REFUSED: --attest-clean-environment required (Chrome/all browsers and streaming video closed, no heavy background load, fixed power mode)"); return 1
    runtime = Path(a.runtime)
    if sha256_file(runtime) != a.runtime_sha256:
        print("REFUSED: runtime file sha256 mismatch"); return 1
    if out.exists() and not a.resume:
        print("REFUSED: out-dir exists (use --resume only to continue this campaign)"); return 1
    if a.resume:
        opened = out / "CAMPAIGN_OPENED.json"
        if not opened.exists() or json.loads(opened.read_text())["freeze_sha256"] != a.freeze_sha256 \
                or json.loads(opened.read_text())["runtime_sha256"] != a.runtime_sha256:
            print("REFUSED: --resume requires this campaign's CAMPAIGN_OPENED.json with the same freeze and runtime"); return 1
        for marker in ("CAMPAIGN_COMPLETE.json", "CAMPAIGN_ABORTED.json"):
            if (out / marker).exists():
                print(f"REFUSED: campaign already closed ({marker})"); return 1
    (out / "launches").mkdir(parents=True, exist_ok=True); (out / "records").mkdir(exist_ok=True)
    if shutil.disk_usage(out).free < MIN_FREE_BYTES:
        print("REFUSED: less than 2 GiB free"); return 1
    import r3_02_acquire as acq
    acq.default_free_ram_check(8 * 1024 ** 3, Path(plan["model"]["path"]))        # frozen free-RAM feasibility gate
    if a.resume:
        accepted = load_accepted(out, sched, a.runtime_sha256)                  # fail closed on any inconsistency
        made = classify_orphans(out, sched)
        n_prev = len(list(out.glob("CAMPAIGN_RESUMED_*.json")))
        write_atomic(out / f"CAMPAIGN_RESUMED_{n_prev + 1:02d}.json", {"amendment_sha256": a.amendment_sha256, "accepted_records_at_resume": sorted(accepted),
                     "orphan_markers_created": made, "resumed_unix": time.time()})
    else:
        write_atomic(out / "CAMPAIGN_OPENED.json", {"freeze_sha256": a.freeze_sha256, "runtime_sha256": a.runtime_sha256,
                                                   "started_unix": time.time(), "blocks": N_BLOCKS})
    try:
        for slot in sched:
            done = out / "records" / f"{slot['seq']:03d}.json"
            if done.exists():
                continue
            admitted = False
            first = prior_attempts(out, slot["seq"]) + 1
            for attempt in range(first, MAX_ATTEMPTS_PER_SLOT + 1):       # frozen failure rule: one rerun of the same slot, then abort
                time.sleep(slot["cooldown_before_s"])
                rec = run_launch(plan, cfgs[slot["config_id"]], runtime, out, slot, attempt, a.runtime_sha256)
                if rec.get("interrupted"):
                    rec.update(status="INTERRUPTED_NONCOUNTED", failures=["operator interrupt"], admitted=False)
                    write_atomic(out / "records" / f"{slot['seq']:03d}.try{attempt}.INTERRUPTED.json", rec)
                    write_atomic(out / "CAMPAIGN_INTERRUPTED.json", {"seq": slot["seq"], "attempt": attempt, "child_stop": rec["shutdown_mode"],
                                                                      "note": "resume with --resume; this attempt is never counted"})
                    print(f"E2E_INTERRUPTED at seq {slot['seq']} try{attempt}; child stop: {rec['shutdown_mode']}"); return 130
                fails = validate_launch(rec, C.FROZEN_TOKEN_DIGEST) + ([rec["error"]] if rec.get("error") else [])
                rec["failures"], rec["admitted"] = fails, not fails
                if fails:
                    write_atomic(out / "records" / f"{slot['seq']:03d}.try{attempt}.FAILED.json", rec)   # failed runs are kept
                else:
                    write_atomic(done, rec); admitted = True              # acceptance point: atomic persistence of a validated record
                    break
            if not admitted:
                write_atomic(out / "CAMPAIGN_ABORTED.json", {"seq": slot["seq"], "reason": "second failure of the same slot; no imputation, no replacement"})
                print(f"E2E_ABORTED at seq {slot['seq']}"); return 1
    except KeyboardInterrupt:                                            # interrupt outside a launch (cooldown pause)
        write_atomic(out / "CAMPAIGN_INTERRUPTED.json", {"during": "cooldown or bookkeeping", "note": "no launch was running; resume with --resume"})
        print("E2E_INTERRUPTED (no launch running)"); return 130
    write_atomic(out / "CAMPAIGN_COMPLETE.json", {"launches": len(sched), "analysis": analyze(out)})
    print("E2E_ACQUISITION_COMPLETE"); return 0


if __name__ == "__main__":
    sys.exit(main())
