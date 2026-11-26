"""Research 5 Phase 1b -- one-shot acquisition of the 8 pre-declared B launches and gate G2 (development prompts only).

    python acquire.py run --model M.gguf --store M.psxs --out OUTDIR
            acquire (warmup + 8 counted B launches), then compute H ONCE and decide G2
    python acquire.py run ... --rerun-infrastructure CAUSE --evidence FILE
            the ONLY way to continue after a stopped or interrupted slot (R5-00 blame_rules: one rerun per slot, for a
            listed infrastructure cause with documentary evidence; a second failure of the same slot aborts)
    python acquire.py status --out OUTDIR      completed launches (never shows H)
    python acquire.py verify --out OUTDIR      re-derive the stored G2 result from the kept traces; must be identical

B = the baseline arm (ARGINE_EXPERT_WAIT=layer) with ARGINE_RESEARCH_TRACE, 1.5 GiB, on the frozen R5 binary. The
schedule (PROTOCOL_1B.json) is fixed before any Phase 1b data exist: an uncounted warmup launch at every (re)start
(SCHEDULE.json warmup convention; validated, trace deleted, never used), then the 8 counted launches in the palindromic
order code, nl, math, json, json, math, nl, code. After each launch the trace is validated (pass/fail only); H is computed
once, after the 8th valid launch. Every precondition fails closed before the model is touched.
"""
from __future__ import annotations

import argparse
import ctypes
import gzip
import hashlib
import importlib.util
import json
import os
import shutil
import subprocess
import sys
import time
from pathlib import Path
from typing import Any, Dict, List, Optional

HERE = Path(__file__).resolve().parent
REPO = HERE.parents[1]
R501 = REPO / "experiments" / "R5-01-ets-implementation"
SEAL = REPO / "experiments" / "R5-01-g1-host-evidence"
P0_DIR = REPO / "experiments" / "R5-00-exposed-transfer-stall"
BUDGET = "1.5"
ORDER = ["code-dev-1", "nl-dev-1", "math-dev-1", "json-dev-1", "json-dev-1", "math-dev-1", "nl-dev-1", "code-dev-1"]
WARMUP_PROMPT = ORDER[0]
DECODE_GRAPHS = 511                              # 512 greedy tokens: the first comes from the prompt graph
COOLDOWN_S, WARMUP_COOLDOWN_S = 10, 60          # SCHEDULE.json cooldown_between_launches_s and warmup cooldown
EXIT_PRECONDITION, EXIT_LAUNCH_INVALID, EXIT_TELEMETRY, EXIT_ABORTED = 3, 4, 5, 6
LOG, RESULT, STOPPED = "PHASE1B_LOG.jsonl", "G2_RESULT.json", "ACQUISITION_STOPPED.json"
MIN_FREE_BYTES = 2_000_000_000
INFRASTRUCTURE_CAUSES = {                        # R5-00 DECISION_RULES blame_rules.infrastructure_causes, in order
    "power": 0, "windows-update-or-defender": 1, "disk-full": 2, "operator-interruption": 3}


def _load(name: str, path: Path):
    if name in sys.modules:
        return sys.modules[name]
    spec = importlib.util.spec_from_file_location(name, path)
    mod = importlib.util.module_from_spec(spec)            # type: ignore[arg-type]
    sys.modules[name] = mod
    spec.loader.exec_module(mod)                           # type: ignore[union-attr]
    return mod


def driver():
    return _load("host_conformance", R501 / "host_conformance.py")


def headroom():
    return _load("headroom", HERE / "headroom.py")


def sha256_file(p: Path) -> str:
    h = hashlib.sha256()
    with open(p, "rb") as f:
        for b in iter(lambda: f.read(1 << 24), b""):
            h.update(b)
    return h.hexdigest()


def lf_sha(p: Path) -> str:
    return hashlib.sha256(p.read_bytes().replace(b"\r\n", b"\n")).hexdigest()


def plan() -> List[Dict[str, Any]]:
    slots = [{"seq": 0, "step": "warmup", "budget": BUDGET, "prompt": WARMUP_PROMPT, "arm": "A", "runtime": "r5", "traced": True, "counted": False}]
    for i, pid in enumerate(ORDER):
        slots.append({"seq": i + 1, "step": "headroom", "budget": BUDGET, "prompt": pid, "arm": "A", "runtime": "r5", "traced": True, "counted": True})
    return slots


def marker_path() -> Path:
    """One Phase 1b campaign per host: a marker outside the output directory refuses a second output directory."""
    base = os.environ.get("LOCALAPPDATA") or str(Path.home())
    return Path(base) / "Argine" / "r5" / "PHASE1B_CAMPAIGN.json"


# ---------------------------------------------------------------------------------------------- preconditions
def frozen() -> Dict[str, Any]:
    return json.loads((SEAL / "IMPLEMENTATION_FREEZE.json").read_text(encoding="utf-8"))


def development_prompts() -> Dict[str, str]:
    """Development prompts ONLY. An evaluation id requested or returned is a hard error; no evaluation prompt is ever
    selected, sent, logged or written (the committed loader reads the corpus files and keeps the development ids)."""
    w = json.loads((P0_DIR / "WORKLOADS.json").read_text(encoding="utf-8"))
    dev = [p["id"] for p in w["development"]["prompts"]]
    ev = {p["id"] for p in w["evaluation"]["prompts"]}
    if set(ORDER) != set(dev) or set(ORDER) & ev:
        raise SystemExit("Phase 1b schedule does not consist of exactly the frozen development prompts")
    got = {p["id"]: p["prompt"] for p in driver().dev_prompts()}
    if set(got) & ev or set(got) != set(dev):
        raise SystemExit("development prompt loader returned something other than the 4 development prompts")
    return got


def power_on_ac() -> Optional[bool]:
    if os.name != "nt":
        return None

    class SPS(ctypes.Structure):
        _fields_ = [("ACLineStatus", ctypes.c_ubyte), ("BatteryFlag", ctypes.c_ubyte), ("BatteryLifePercent", ctypes.c_ubyte),
                    ("SystemStatusFlag", ctypes.c_ubyte), ("BatteryLifeTime", ctypes.c_ulong), ("BatteryFullLifeTime", ctypes.c_ulong)]
    s = SPS()
    if not ctypes.windll.kernel32.GetSystemPowerStatus(ctypes.byref(s)):   # type: ignore[attr-defined]
        return None
    return {0: False, 1: True}.get(s.ACLineStatus)


def _cmd(args: List[str], timeout: int = 60) -> Optional[str]:
    try:
        r = subprocess.run(args, capture_output=True, text=True, timeout=timeout)
        return (r.stdout or "").strip() or None
    except (OSError, subprocess.SubprocessError):
        return None


def host_record() -> Dict[str, Any]:
    """INSTRUMENTATION.json host_record_per_launch; what cannot be read without elevation is recorded as unavailable."""
    return {"wall_unix": time.time(), "ac_power": power_on_ac(),
            "power_plan": _cmd(["powercfg", "/getactivescheme"]),
            "cpu_clock_mhz": _cmd(["powershell", "-NoProfile", "-Command",
                                   "Get-CimInstance Win32_Processor | Select-Object CurrentClockSpeed,MaxClockSpeed | ConvertTo-Json -Compress"]),
            "defender": _cmd(["powershell", "-NoProfile", "-Command",
                              "Get-MpComputerStatus | Select-Object RealTimeProtectionEnabled,AntivirusSignatureAge | ConvertTo-Json -Compress"]),
            "windows_update_service": _cmd(["sc", "query", "wuauserv"]),
            "cpu_temperature": "unavailable without elevated sensors (recorded as such)",
            "nvme_temperature": "unavailable without elevated sensors (recorded as such)"}


def readiness_identity() -> Dict[str, str]:
    """Binds the acquisition and the G2 result to the frozen readiness record (phase1b.py verify must pass)."""
    p1b = _load("phase1b", HERE / "phase1b.py")
    pr = p1b.verify()
    if pr:
        raise SystemExit(f"Phase 1b readiness does not verify: {pr[:3]}")
    rd = json.loads((HERE / "READINESS.json").read_text(encoding="utf-8"))
    return {"readiness_sha256": rd["readiness_sha256"], "headroom_py_lf_sha256": lf_sha(HERE / "headroom.py"),
            "acquire_py_lf_sha256": lf_sha(HERE / "acquire.py"), "protocol_lf_sha256": lf_sha(HERE / "PROTOCOL_1B.json")}


def preconditions(a: argparse.Namespace, out: Path) -> Dict[str, Any]:
    """Every check BEFORE the model is touched. Raises SystemExit (exit code 3) on the first failure."""
    rid = readiness_identity()            # includes R5-00 phase0 verify and the G1 seal verify
    seal = json.loads((SEAL / "G1_SEAL.json").read_text(encoding="utf-8"))
    if seal["verdict"] != "R5_G1_HOST_CONFORMANCE_PASS":
        raise SystemExit("G1 verdict is not R5_G1_HOST_CONFORMANCE_PASS")
    if any(HERE.glob("**/G2_RESULT*.json")):
        raise SystemExit("a Phase 1b result is already committed: G2 is one-shot (H is not recomputed)")
    imf, H = frozen(), driver()
    runtime = REPO / "llama-research-r5-ets-2" / "llama-server.exe"
    if not runtime.is_file():
        raise SystemExit(f"{runtime} not found")
    rt_sha = sha256_file(runtime)
    if not (rt_sha == imf["binaries_sha256"]["llama-server.exe"] == H.R5_AUTHORIZED["llama-server.exe"]):
        raise SystemExit(f"runtime sha256 {rt_sha} is not the frozen R5 runtime {imf['binaries_sha256']['llama-server.exe']}")
    if (out / RESULT).exists():
        raise SystemExit(f"{out / RESULT} exists: G2 was already decided in this directory (one-shot)")
    free = shutil.disk_usage(out).free
    if free < MIN_FREE_BYTES:
        raise SystemExit(f"{free / 1e9:.1f} GB free in {out}: at least {MIN_FREE_BYTES / 1e9:.0f} GB are required (8 kept traces + one raw trace)")
    mk = marker_path()
    if mk.exists():
        m = json.loads(mk.read_text(encoding="utf-8"))
        if Path(m["out"]).resolve() != out.resolve():
            raise SystemExit(f"a Phase 1b campaign was already started in {m['out']} ({mk}): G2 is one-shot, no second campaign")
    prompts = development_prompts()
    ident = {"runtime": str(runtime), "runtime_sha256": rt_sha, "model": a.model, "store": a.store,
             "model_sha256_frozen": imf["model"]["sha256"], "store_sha256_frozen": imf["store"]["sha256"],
             "runtime_dll_dependencies": "system DLLs only (static build; BUILD-INFO dll_dependencies), not hashed",
             "g1_freeze_sha256": json.loads((SEAL / "FREEZE.json").read_text(encoding="utf-8"))["freeze_sha256"], **rid}
    prev = out / "IDENTITY_1B.json"
    if prev.exists() and json.loads(prev.read_text(encoding="utf-8")) != ident:
        raise SystemExit(f"{out} belongs to another identity, protocol or estimator: use a NEW output directory")
    if not prev.exists() and (out / LOG).exists():
        raise SystemExit(f"{out} holds launches without an identity record: use a NEW output directory")
    return {"ident": ident, "runtime": runtime, "prompts": prompts}


def check_model_store(a: argparse.Namespace) -> Dict[str, str]:
    """R5-00 WORKLOADS model.identity: the gguf and psxs hashes are checked at every launch (before the first, and after each)."""
    imf = frozen()
    got = {"model_sha256": sha256_file(Path(a.model)), "store_sha256": sha256_file(Path(a.store))}
    if (got["model_sha256"], got["store_sha256"]) != (imf["model"]["sha256"], imf["store"]["sha256"]):
        raise SystemExit(f"model/store hash differs from the IMPLEMENTATION_FREEZE: {got}")
    return got


# ---------------------------------------------------------------------------------------------- launches
def keep_trace_factory(counted: bool, out: Optional[Path] = None, seq: Optional[int] = None, attempt: Optional[int] = None):
    """Replaces host_conformance.reduce_trace for Phase 1b: structure, telemetry validation (pass/fail, never H), raw
    hash; a counted launch's trace is kept gzip'd (attempt-numbered file name, never overwritten) and its hashes are
    logged at once (a "trace_kept" record) before any post-launch work; the warmup's trace is deleted."""
    def keep(trace: Path) -> Dict[str, Any]:
        if not trace.exists():
            return {"present": False}
        if out is not None and counted:              # logged first: an interruption from here on is "after the trace"
            append(out, {"type": "trace_received", "seq": seq, "attempt": attempt, "raw": trace.name, "bytes": trace.stat().st_size,
                         "unix": time.time()})
        et = _load("ets_trace", R501 / "ets_trace.py")
        HR = headroom()
        info: Dict[str, Any] = {"present": True, "bytes": trace.stat().st_size, "sha256": sha256_file(trace), "structure": et.structure(trace)}
        try:
            v = HR.validate(HR.read_lines(trace), DECODE_GRAPHS)
            info["telemetry"] = {"valid": True, "decode_graphs": v["decode_graphs"], "events": v["events"]}
        except HR.TelemetryError as e:
            info["telemetry"] = {"valid": False, "reason": str(e)}
        if counted:
            gz = trace.with_name(trace.name + ".gz")
            with open(trace, "rb") as src, gzip.open(gz, "wb", compresslevel=6) as dst:
                shutil.copyfileobj(src, dst, 1 << 24)
            info.update({"kept": gz.name, "gz_bytes": gz.stat().st_size, "gz_sha256": sha256_file(gz)})
            if out is not None:
                append(out, {"type": "trace_kept", "seq": seq, "attempt": attempt, "kept": gz.name, "gz_sha256": info["gz_sha256"],
                             "sha256": info["sha256"], "telemetry": info["telemetry"], "unix": time.time()})
        trace.unlink()
        return info
    return keep


def launch_problems(rec: Dict[str, Any]) -> List[str]:
    """A B launch is valid only if every one of these holds (no H is looked at)."""
    p = []
    if rec.get("error"):
        p.append(f"error: {rec['error']}")
    if rec.get("n_tokens") != 512:
        p.append(f"n_tokens {rec.get('n_tokens')}")
    t = rec.get("server_timings") or {}
    if not (t.get("predicted_n") == 512 and (t.get("predicted_ms") or 0) > 0):
        p.append("server decode timing missing")
    if (rec.get("shutdown") or {}).get("mode") != "graceful":
        p.append("shutdown not graceful")
    if rec.get("r5_counters_lines") != 1:
        p.append("not exactly one r5_counters record")
    c = rec.get("r5_counters") or {}
    if (c.get("expert_wait"), c.get("explicit"), c.get("prefetch"), c.get("read_mode")) != ("layer", 1, "0", "asincrono"):
        p.append("arm identity is not the explicit baseline layer arm, prefetch 0, asincrono")
    for k in ("violations", "orphan_pending", "pending_at_event_end", "followup_mismatch", "touch_missing", "drained_at_thread_exit", "deferred_reads"):
        if c.get(k) != 0:
            p.append(f"counter {k} = {c.get(k)}")
    if c.get("budget") != 1610612736:
        p.append(f"budget {c.get('budget')}")
    tr = rec.get("trace") or {}
    st = tr.get("structure") or {}
    if not tr.get("present") or not all((st.get("checks") or {"x": False}).values()):
        p.append("trace missing or structure checks failed")
    if st.get("expert_wait_config") != "layer" or st.get("decode_graphs") != DECODE_GRAPHS:
        p.append("trace arm or decode-graph count")
    if "telemetry" in tr and not tr["telemetry"].get("valid"):
        p.append(f"telemetry invalid: {tr['telemetry'].get('reason')}")
    if rec.get("counted") and (not tr.get("kept") or "telemetry" not in tr):
        p.append("counted trace not kept or not validated")
    for side in ("host_before", "host_after"):
        if side in rec and (rec[side] or {}).get("ac_power") is not True:
            p.append(f"{side}: not on AC power (or unknown)")
    if "model_store_after" in rec and rec["model_store_after"].get("ok") is not True:
        p.append("model/store hash after the launch differs from the IMPLEMENTATION_FREEZE")
    return p


def records(out: Path) -> List[Dict[str, Any]]:
    log = out / LOG
    return [json.loads(l) for l in log.read_text(encoding="utf-8").splitlines() if l.strip()] if log.exists() else []


def append(out: Path, rec: Dict[str, Any]) -> None:
    with (out / LOG).open("a", encoding="utf-8") as f:
        f.write(json.dumps(rec, sort_keys=True) + "\n")


def slot_state(recs: List[Dict[str, Any]]) -> Dict[int, Dict[str, Any]]:
    """Per counted slot: attempts started, valid launch present, failures (an attempt without a launch record = interrupted)."""
    st: Dict[int, Dict[str, Any]] = {}
    new = lambda: {"attempts": 0, "valid": False, "failures": 0, "open": False, "kept": False, "interrupted_after_trace": False}
    for r in recs:
        if r.get("type") == "attempt_start" and r["seq"] > 0:
            s = st.setdefault(r["seq"], new())
            if s["open"]:                 # the previous attempt never recorded a launch: interrupted
                s["failures"] += 1
                s["interrupted_after_trace"] |= s["kept"]
            s["attempts"] += 1
            s["open"], s["kept"] = True, False
        elif r.get("type") in ("trace_received", "trace_kept") and r["seq"] > 0:
            st.setdefault(r["seq"], new())["kept"] = True
        elif r.get("type") == "launch" and r["seq"] > 0:
            s = st.setdefault(r["seq"], new())
            s["open"] = False
            if launch_problems(r):
                s["failures"] += 1
            else:
                s["valid"] = True
    for s in st.values():
        if s["open"]:
            s["failures"] += 1
            s["interrupted_after_trace"] |= s["kept"]
            s["open"] = False
    return st


def stop(out: Path, code: int, why: List[str], seq: Optional[int] = None, aborted: bool = False) -> int:
    (out / STOPPED).write_text(json.dumps({"stopped": True, "aborted": aborted, "exit_code": code, "seq": seq, "reasons": why, "g2_computed": False,
                                           "note": "no H was computed. R5-00 blame_rules: a failed B launch is attributed to the campaign; "
                                                   "one rerun of the slot is possible only for a listed infrastructure cause with "
                                                   "documentary evidence (acquire.py run --rerun-infrastructure CAUSE --evidence FILE); "
                                                   "a second failure of the same slot aborts the campaign and G2 stays undecided"},
                                          indent=1), encoding="utf-8")
    print(("PHASE 1B ABORTED" if aborted else "PHASE 1B STOPPED") + " (no H computed): " + "; ".join(why)); return code


def authorize_rerun(a: argparse.Namespace, out: Path, st: Dict[int, Dict[str, Any]]) -> Optional[str]:
    """None if the run may continue; otherwise the reason it may not."""
    failed = sorted(s for s, x in st.items() if x["failures"] and not x["valid"])
    stopped = (out / STOPPED).exists()
    if not failed and not stopped:
        return None
    if stopped and json.loads((out / STOPPED).read_text(encoding="utf-8")).get("aborted"):
        return "the campaign was aborted (second failure of a slot): G2 stays undecided"
    if any(st[s]["failures"] >= 2 for s in failed):
        return "a slot failed twice: the campaign is aborted (blame_rules)"
    if not getattr(a, "rerun_infrastructure", None) or not getattr(a, "evidence", None):
        return f"slot(s) {failed or '?'} stopped or interrupted: rerun only with --rerun-infrastructure CAUSE --evidence FILE (blame_rules)"
    if a.rerun_infrastructure not in INFRASTRUCTURE_CAUSES or not Path(a.evidence).is_file():
        return f"--rerun-infrastructure must be one of {sorted(INFRASTRUCTURE_CAUSES)} and --evidence an existing file"
    seen = [s for s in failed if st[s]["interrupted_after_trace"]]
    if seen and a.rerun_infrastructure == "operator-interruption":
        return (f"slot(s) {seen} were interrupted after their counted trace was kept: an operator interruption at that point "
                "could follow a look at the trace, so it is not a rerun cause (the campaign stays stopped; G2 undecided)")
    dr = json.loads((P0_DIR / "DECISION_RULES.json").read_text(encoding="utf-8"))
    append(out, {"type": "rerun_authorized", "slots": failed, "cause": dr["blame_rules"]["infrastructure_causes"][INFRASTRUCTURE_CAUSES[a.rerun_infrastructure]],
                 "evidence": str(a.evidence), "evidence_sha256": sha256_file(Path(a.evidence)), "unix": time.time()})
    if stopped:
        n = len(list(out.glob("ACQUISITION_STOPPED.*.json")))
        (out / STOPPED).rename(out / f"ACQUISITION_STOPPED.{n + 1}.json")
    return None


def cmd_run(a: argparse.Namespace) -> int:
    out = Path(a.out); out.mkdir(parents=True, exist_ok=True)
    try:
        pre = preconditions(a, out)
    except SystemExit as e:
        print(f"PHASE 1B PRECONDITION FAILED: {e}"); return EXIT_PRECONDITION
    st = slot_state(records(out))
    why = authorize_rerun(a, out, st)
    if why:
        print(f"PHASE 1B REFUSED: {why}")
        return EXIT_ABORTED if "abort" in why else EXIT_PRECONDITION
    (out / "IDENTITY_1B.json").write_text(json.dumps(pre["ident"], indent=1), encoding="utf-8")
    mk = marker_path()
    if not mk.exists():
        mk.parent.mkdir(parents=True, exist_ok=True)
        mk.write_text(json.dumps({"out": str(out.resolve()), "started_unix": time.time(), "identity": pre["ident"]}, indent=1), encoding="utf-8")
    H = driver()
    try:
        check_model_store(a)                         # before the first launch of this (re)start
    except SystemExit as e:
        return stop(out, EXIT_PRECONDITION, [str(e)])
    todo = [s for s in plan() if s["seq"] > 0 and not st.get(s["seq"], {}).get("valid")]
    if todo:
        todo = [plan()[0]] + todo                    # a warmup at every (re)start
    for slot in todo:
        if power_on_ac() is not True:
            return stop(out, EXIT_PRECONDITION, ["host not on AC power, or the power state is unknown (SCHEDULE host_conditions)"], slot["seq"])
        attempt = 1 + sum(1 for r in records(out) if r.get("type") == "attempt_start" and r["seq"] == slot["seq"])
        append(out, {"type": "attempt_start", "seq": slot["seq"], "attempt": attempt, "unix": time.time()})
        host = host_record()
        print(f"[{slot['seq']}/8] {slot['step']} B {slot['budget']} GiB {slot['prompt']}" + ("" if slot["counted"] else " (uncounted warmup)")); sys.stdout.flush()
        original = H.reduce_trace
        H.reduce_trace = keep_trace_factory(slot["counted"], out, slot["seq"], attempt)   # the G1 driver itself is unchanged
        try:                                         # attempt-numbered file names: a rerun never overwrites an earlier attempt
            rec = H.run_launch(dict(slot, step=f"{slot['step']}-a{attempt}"), pre["runtime"], Path(a.model), Path(a.store), out,
                               pre["prompts"][slot["prompt"]])
        finally:
            H.reduce_trace = original
        rec["step"] = slot["step"]
        try:
            ms = dict(check_model_store(a), ok=True)
        except SystemExit as e:
            ms = {"ok": False, "error": str(e)}
        rec.update({"type": "launch", "attempt": attempt, "artifact": "R5-02-PHASE1B-LAUNCH", "host_before": host, "host_after": host_record(),
                    "model_store_after": ms, "token_digest_matches_g1": rec.get("token_digest") in g1_token_digests(slot["prompt"])})
        append(out, rec)
        prob = launch_problems(rec)
        if prob:
            failures = slot_state(records(out)).get(slot["seq"], {}).get("failures", 0)
            if failures >= 2:
                return stop(out, EXIT_ABORTED, prob + ["second failure of this slot: campaign aborted (blame_rules)"], slot["seq"], aborted=True)
            return stop(out, EXIT_LAUNCH_INVALID, prob, slot["seq"])
        time.sleep(WARMUP_COOLDOWN_S if slot["seq"] == 0 else COOLDOWN_S)
    return analyze(out)


# ---------------------------------------------------------------------------------------------- the one-shot gate
def g1_token_digests(prompt: str) -> set:
    """Informational: the sealed G1 launches' greedy token digests for this development prompt (not a validity rule)."""
    log = SEAL / "host" / "DEV_LOG.jsonl"
    return {r.get("token_digest") for r in (json.loads(l) for l in log.read_text(encoding="utf-8").splitlines() if l.strip())
            if r.get("prompt") == prompt and r.get("token_digest")}


def counted_valid(recs: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    best: Dict[int, Dict[str, Any]] = {}
    for r in recs:
        if r.get("type") == "launch" and r.get("counted") and not launch_problems(r):
            best[r["seq"]] = r
    return [best[s] for s in sorted(best)]


def g2_from(out: Path, recs: List[Dict[str, Any]]) -> Dict[str, Any]:
    HR = headroom()
    counted = counted_valid(recs)
    if [r["seq"] for r in counted] != list(range(1, 9)) or [r["prompt"] for r in counted] != ORDER:
        raise HR.TelemetryError("the 8 counted launches are not complete and in the pre-declared order")
    per, timing = [], []
    for r in counted:
        gz = out / "traces" / r["trace"]["kept"]
        if sha256_file(gz) != r["trace"]["gz_sha256"]:
            raise HR.TelemetryError(f"kept trace {gz.name} differs from its recorded hash")
        t = r["server_timings"]
        per.append(HR.launch_h(HR.read_lines(gz), expected_decode_tokens=t["predicted_n"] - 1))
        timing.append((t["predicted_ms"], t["predicted_n"]))
    d = HR.decide(per, timing)
    used = {(r["seq"], r["attempt"]) for r in counted}
    superseded = []                                  # every kept counted trace is reported; only the 8 valid launches decide
    for k in (r for r in recs if r.get("type") == "trace_kept" and (r["seq"], r["attempt"]) not in used):
        gz = out / "traces" / k["kept"]
        if not gz.is_file() or sha256_file(gz) != k["gz_sha256"]:
            raise HR.TelemetryError(f"kept trace of superseded attempt {k['seq']}/{k['attempt']} missing or altered")
        try:
            x = HR.launch_h(HR.read_lines(gz), expected_decode_tokens=DECODE_GRAPHS)
            superseded.append({"seq": k["seq"], "attempt": k["attempt"], "trace_gz_sha256": k["gz_sha256"], "h_ms": x.h_ns / 1e6,
                               "decode_graphs": x.decode_tokens, "decides": False})
        except HR.TelemetryError as e:
            superseded.append({"seq": k["seq"], "attempt": k["attempt"], "trace_gz_sha256": k["gz_sha256"], "telemetry_invalid": str(e),
                               "decides": False})
    listed = {r["kept"] for r in recs if r.get("type") == "trace_kept"}
    for f in sorted((out / "traces").iterdir()):     # nothing in traces/ may go unreported
        if f.name in listed:
            continue
        if f.suffix != ".trace":
            raise HR.TelemetryError(f"traces/{f.name} is not accounted for by any trace_kept record")
        entry: Dict[str, Any] = {"raw_unkept": f.name, "sha256": sha256_file(f), "warmup": "-warmup-" in f.name, "decides": False}
        try:
            x = HR.launch_h(HR.read_lines(f), expected_decode_tokens=DECODE_GRAPHS)
            entry.update({"h_ms": x.h_ns / 1e6, "decode_graphs": x.decode_tokens})
        except HR.TelemetryError as e:
            entry["telemetry_invalid"] = str(e)
        superseded.append(entry)
    return {"artifact": "R5-02-G2-RESULT", **d, "superseded_attempts": superseded,
            "launches": [{"seq": r["seq"], "prompt": r["prompt"], "attempt": r["attempt"], "trace_gz_sha256": r["trace"]["gz_sha256"],
                          "trace_sha256": r["trace"]["sha256"], "decode_graphs": x.decode_tokens, "events": x.events,
                          "events_positive": x.events_positive, "H_fraction": f, "H_ms_per_decode_step": x.h_ns / x.decode_tokens / 1e6,
                          "decode_ms_per_decode_step": t[0] / x.decode_tokens, "sum_T_tail_ms": x.tail_ns / 1e6, "sum_W_ms": x.gap_ns / 1e6,
                          "sum_first_node_wait_ms": x.first_wait_ns / 1e6, "first_node_reads": x.first_node_reads,
                          "not_overlapped_reads": x.not_overlapped_reads, "bounded_completion_reads": x.bounded_completion_reads}
                         for r, x, t, f in zip(counted, per, timing, d["per_launch_H_fraction"])],
            "interpretation": "H is an optimistic upper bound on hideable expert-transfer wait (an opening gate only), never an achieved or "
                              "achievable gain. PASS only allows Phase 2 to be opened by its own separate step; FAIL closes the direction "
                              "(R5_NO_MEANINGFUL_OVERLAP_HEADROOM) and the evaluation is never opened.",
            "computed_once": True}


def analyze(out: Path) -> int:
    if (out / RESULT).exists():
        print("G2 already decided in this directory (one-shot)"); return EXIT_PRECONDITION
    HR = headroom()
    try:
        rid = readiness_identity()
    except SystemExit as e:
        return stop(out, EXIT_PRECONDITION, [str(e)])
    ident = json.loads((out / "IDENTITY_1B.json").read_text(encoding="utf-8"))
    if any(ident.get(k) != v for k, v in rid.items()):
        return stop(out, EXIT_PRECONDITION, ["estimator, protocol or readiness changed since the acquisition started"])
    try:
        res = g2_from(out, records(out))
    except HR.TelemetryError as e:
        return stop(out, EXIT_TELEMETRY, [f"telemetry: {e}"])
    res["identity"] = ident
    (out / RESULT).write_text(json.dumps(res, indent=1, sort_keys=True), encoding="utf-8")
    print(f"H = {res['H']:.6f} of decode time (threshold {res['threshold']}); G2 {res['gate_G2']}: {res['label']}")
    print("H is an optimistic bound, not a speedup. Phase 2 is NOT opened by this step.")
    return 0


def cmd_status(a: argparse.Namespace) -> int:
    out = Path(a.out); recs = records(out); st = slot_state(recs)
    valid = sorted(s for s, x in st.items() if x["valid"])
    warm = [r for r in recs if r.get("type") == "launch" and r["seq"] == 0]
    started = [r for r in recs if r.get("type") == "attempt_start"]
    current = started[-1]["seq"] if started and recs[-1].get("type") == "attempt_start" else None
    print(f"warmups done: {sum(1 for r in warm if not launch_problems(r))} | counted launches valid: {len(valid)}/8 {valid} | "
          f"failed: {sorted(s for s, x in st.items() if x['failures'] and not x['valid'])} | running: {current} | "
          f"stopped: {(out / STOPPED).exists()} | G2 decided: {(out / RESULT).exists()}")
    return 0


def cmd_verify(a: argparse.Namespace) -> int:
    out = Path(a.out)
    stored = json.loads((out / RESULT).read_text(encoding="utf-8"))
    again = g2_from(out, records(out))
    again["identity"] = stored["identity"]
    same = json.loads(json.dumps(again, sort_keys=True)) == stored
    print("G2 result re-derived from the kept traces:", "IDENTICAL" if same else "DIFFERENT"); return 0 if same else 1


def main(argv: Optional[List[str]] = None) -> int:
    ap = argparse.ArgumentParser()
    sp = ap.add_subparsers(dest="cmd", required=True)
    r = sp.add_parser("run"); r.add_argument("--model", required=True); r.add_argument("--store", required=True); r.add_argument("--out", required=True)
    r.add_argument("--rerun-infrastructure", choices=sorted(INFRASTRUCTURE_CAUSES)); r.add_argument("--evidence")
    s = sp.add_parser("status"); s.add_argument("--out", required=True)
    v = sp.add_parser("verify"); v.add_argument("--out", required=True)
    a = ap.parse_args(argv)
    return {"run": cmd_run, "status": cmd_status, "verify": cmd_verify}[a.cmd](a)


if __name__ == "__main__":
    sys.exit(main())
