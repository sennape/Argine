"""Research 5 Phase 2 -- the prospective evaluation campaign driver (host, Windows). Fail closed everywhere.

    campaign.py authorize-opening --readiness SHA --statement TEXT   one-shot, explicit opening of the evaluation
    campaign.py run --model GGUF --store PSXS --out DIR [--rerun-infrastructure CAUSE --evidence FILE]
    campaign.py status --out DIR [--json]      operational progress only; never a timing, metric or result
    campaign.py analyze --out DIR              one-shot analysis (run calls it after the last counted launch)
    campaign.py verify --out DIR               re-derives the stored result from the log and the kept traces
    campaign.py finalize --out DIR [--infrastructure CAUSE --evidence FILE]
                                               attributes an unresolved stop or interruption by blame_rules (terminal)

Frozen design (R5-00 SCHEDULE.json, read from the contract, never retyped): campaigns in the order mechanism_1.5GiB
(12 blocks x B, C0_I, C_I = 36 traced launches), timing_1.5GiB (20 blocks x ABBA/BAAB of A and C = 80 untraced) and
timing_4GiB (20 blocks, 80 untraced): 196 counted launches. One uncounted warmup per campaign with the first arm of
block 1 in the identical environment, then 60 s; 10 s between launches.

Pre-declared operational choices (PROTOCOL_2.json): a warmup also precedes the first counted launch after every
(re)start, with that launch's arm, prompt and budget (recorded, never used); `PAUSE` in the output directory stops the
run cleanly after the current launch (not a failure; resuming starts with a warmup); every launch is checked at once
for validity WITHOUT computing any endpoint; nothing is analysed before all 196 counted launches are valid; R5-00
blame_rules govern every failure (see stop()).
"""
from __future__ import annotations

import argparse
import ctypes
import gzip
import hashlib
import importlib.util
import json
import os
import re
import shutil
import subprocess
import sys
import time
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

HERE = Path(__file__).resolve().parent
REPO = HERE.parents[1]
P0_DIR = REPO / "experiments" / "R5-00-exposed-transfer-stall"
R501 = REPO / "experiments" / "R5-01-ets-implementation"
SEAL1 = REPO / "experiments" / "R5-01-g1-host-evidence"
SEAL2 = REPO / "experiments" / "R5-02-g2-host-evidence"
WORKLOADS_V1 = REPO / "workloads" / "v1"
CORPUS_FILES = ("coding", "natural-language", "reasoning-math", "structured-json")
LOG, RESULT, STOPPED, IDENT, RUN_STATE, PAUSE = ("PHASE2_LOG.jsonl", "PHASE2_RESULT.json", "ACQUISITION_STOPPED.json", "IDENTITY_2.json",
                                                 "RUN_STATE.json", "PAUSE")
EXIT_PRECONDITION, EXIT_LAUNCH_INVALID, EXIT_TELEMETRY, EXIT_ABORTED, EXIT_TERMINAL = 3, 4, 5, 6, 7
MIN_FREE_BYTES = 4_000_000_000             # 36 kept traces (about 1.8 GB) + one raw trace + logs, with margin
MIN_FREE_TRACED = 1_000_000_000
WARMUP_COOLDOWN_S = 60
OPENING_STATEMENT = "I authorize opening the Research 5 evaluation once, under contract 9d480ccf"
ARM_WAIT = {"A": "layer", "B": "layer", "C": "kind", "C0_I": "layer_touch", "C_I": "kind"}
ARM_DRIVER = {"A": "A", "B": "A", "C": "C", "C0_I": "C0", "C_I": "C"}         # host_conformance.ARMS keys
TRACED = {"B", "C0_I", "C_I"}
CORRECTNESS_ARMS = {"C", "C_I", "C0_I"}
INFRASTRUCTURE_CAUSES = {"power": 0, "windows-update-or-defender": 1, "disk-full": 2, "operator-interruption": 3}
ABORT_MARKERS = re.compile(r"GGML_ASSERT|GGML_ABORT|\babort(ed|ing)?\b|terminate called|STATUS_HEAP_CORRUPTION|0xC0000374", re.I)


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


def mechanism():
    return _load("mechanism", HERE / "mechanism.py")


def analysis():
    return _load("analysis", HERE / "analysis.py")


def sha256_file(p: Path) -> str:
    h = hashlib.sha256()
    with open(p, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 24), b""):
            h.update(chunk)
    return h.hexdigest()


def lf_sha(p: Path) -> str:
    return hashlib.sha256(p.read_bytes().replace(b"\r\n", b"\n")).hexdigest()


def canon(o: Any) -> bytes:
    return json.dumps(o, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode()


# ---------------------------------------------------------------------------------------------- schedule
def plan() -> List[Dict[str, Any]]:
    """The 196 counted slots, read from the frozen SCHEDULE.json (never retyped)."""
    s = json.loads((P0_DIR / "SCHEDULE.json").read_text(encoding="utf-8"))
    slots, seq = [], 0
    for camp in s["campaign_order"]:
        c = s["campaigns"][camp]
        budget = {1610612736: "1.5", 4294967296: "4"}[c["budget_bytes"]]
        for b in c["blocks"]:
            for pos, arm in enumerate(b["order"], start=1):
                seq += 1
                slots.append({"seq": seq, "campaign": camp, "block": b["block"], "pos": pos, "arm": arm, "prompt": b["prompt"],
                              "budget": budget, "traced": arm in TRACED, "counted": True})
    if [sum(1 for x in slots if x["campaign"] == k) for k in s["campaign_order"]] != [s["launch_counts"][k] for k in s["campaign_order"]]:
        raise SystemExit("schedule does not match the frozen launch counts")
    return slots


def campaign_warmup(camp: str) -> Dict[str, Any]:
    first = next(x for x in plan() if x["campaign"] == camp and x["block"] == 1 and x["pos"] == 1)
    return dict(first, seq=0, counted=False, kind="campaign_warmup")


def resume_warmup(slot: Dict[str, Any]) -> Dict[str, Any]:
    return dict(slot, seq=0, counted=False, kind="resume_warmup", before_seq=slot["seq"])


def marker_path() -> Path:
    base = os.environ.get("LOCALAPPDATA") or str(Path.home())
    return Path(base) / "Argine" / "r5" / "PHASE2_CAMPAIGN.json"


def opening_path() -> Path:
    base = os.environ.get("LOCALAPPDATA") or str(Path.home())
    return Path(base) / "Argine" / "r5" / "EVALUATION_OPENING.json"


# ---------------------------------------------------------------------------------------------- readiness and opening
def readiness_identity() -> Dict[str, str]:
    p2 = _load("phase2", HERE / "phase2.py")
    pr = p2.verify()
    if pr:
        raise SystemExit(f"Phase 2 readiness does not verify: {pr[:3]}")
    rd = json.loads((HERE / "READINESS_2.json").read_text(encoding="utf-8"))
    return {"readiness_sha256": rd["readiness_sha256"], **{f"{n}_lf_sha256": lf_sha(HERE / f"{n}.py") for n in ("campaign", "analysis", "mechanism")},
            "protocol_lf_sha256": lf_sha(HERE / "PROTOCOL_2.json")}


def cmd_authorize(a: argparse.Namespace) -> int:
    """The explicit, prospective opening of the evaluation. One-shot; writes nothing else; reads no prompt."""
    p = opening_path()
    if p.exists():
        print(f"OPENING REFUSED: {p} exists: the evaluation is opened once (anti_rescue)"); return EXIT_PRECONDITION
    try:
        rid = readiness_identity()
    except SystemExit as e:
        print(f"OPENING REFUSED: {e}"); return EXIT_PRECONDITION
    if a.readiness != rid["readiness_sha256"]:
        print(f"OPENING REFUSED: --readiness {a.readiness} is not the committed Phase 2 readiness {rid['readiness_sha256']}"); return EXIT_PRECONDITION
    if a.statement != OPENING_STATEMENT:
        print(f'OPENING REFUSED: the statement must be exactly: "{OPENING_STATEMENT}"'); return EXIT_PRECONDITION
    g1 = json.loads((SEAL1 / "G1_SEAL.json").read_text(encoding="utf-8"))
    g2 = json.loads((SEAL2 / "G2_SEAL.json").read_text(encoding="utf-8"))
    if g1["verdict"] != "R5_G1_HOST_CONFORMANCE_PASS" or g2["verdict"] != "R5_G2_HEADROOM_GATE_PASS":
        print("OPENING REFUSED: G1 and G2 must both be sealed PASS"); return EXIT_PRECONDITION
    rec = {"artifact": "R5-03-EVALUATION-OPENING", "statement": a.statement, "opened_unix": time.time(),
           "opened_utc": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()), **rid,
           "g1_freeze_sha256": json.loads((SEAL1 / "FREEZE.json").read_text(encoding="utf-8"))["freeze_sha256"],
           "g2_freeze_sha256": json.loads((SEAL2 / "FREEZE.json").read_text(encoding="utf-8"))["freeze_sha256"],
           "phase0_freeze_sha256": json.loads((P0_DIR / "FREEZE.json").read_text(encoding="utf-8"))["freeze_sha256"],
           "note": "prospective: written before any evaluation prompt is read by any R5 tool; commit it with the sealed Phase 2 "
                   "evidence (a separate evidence directory), never into experiments/R5-03-prospective-evaluation"}
    rec["opening_sha256"] = hashlib.sha256(canon(rec)).hexdigest()
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(rec, indent=1, sort_keys=True), encoding="utf-8")
    print(f"EVALUATION OPENED (once): {p}\nopening_sha256 {rec['opening_sha256']}"); return 0


def opening_record(rid: Dict[str, str]) -> Dict[str, Any]:
    p = opening_path()
    if not p.exists():
        raise SystemExit("the evaluation has not been opened: run `campaign.py authorize-opening` first (explicit authorization)")
    rec = json.loads(p.read_text(encoding="utf-8"))
    body = {k: v for k, v in rec.items() if k != "opening_sha256"}
    if rec.get("opening_sha256") != hashlib.sha256(canon(body)).hexdigest() or rec.get("statement") != OPENING_STATEMENT:
        raise SystemExit(f"{p} is not a valid opening record")
    if any(rec.get(k) != v for k, v in rid.items()):
        raise SystemExit(f"{p} was written for another readiness, protocol or driver: no second opening, no change after opening")
    return rec


def eval_prompts(opening: Dict[str, Any], corpus_dir: Path = WORKLOADS_V1, workloads: Optional[Dict[str, Any]] = None) -> Dict[str, str]:
    """The 4 evaluation prompts, only after a valid opening. Never printed, logged or written (records keep the templated
    prompt's SHA-256 only)."""
    if not opening or opening.get("statement") != OPENING_STATEMENT:
        raise SystemExit("evaluation prompts requested without a valid opening record")
    w = workloads if workloads is not None else json.loads((P0_DIR / "WORKLOADS.json").read_text(encoding="utf-8"))
    ev = {p["id"]: p for p in w["evaluation"]["prompts"]}
    dev = {p["id"] for p in w["development"]["prompts"]}
    out: Dict[str, str] = {}
    for f in CORPUS_FILES:
        d = json.loads((corpus_dir / f"{f}.json").read_text(encoding="utf-8"))
        for p in d["splits"]["eval"]:
            if p["id"] in ev:
                h = hashlib.sha256(canon(p)).hexdigest()
                if h != ev[p["id"]]["prompt_object_sha256"]:
                    raise SystemExit(f"{p['id']}: prompt object differs from the frozen hash")
                out[p["id"]] = p["prompt"]
    if sorted(out) != sorted(ev) or set(out) & dev or {x["prompt"] for x in plan()} != set(out):
        raise SystemExit("evaluation prompts incomplete or inconsistent with the schedule")
    return out


# ---------------------------------------------------------------------------------------------- host
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


def pid_alive(pid: int) -> bool:
    """Never os.kill (on Windows signal 0 is CTRL_C_EVENT and would interrupt the campaign)."""
    if os.name == "nt":
        k = ctypes.windll.kernel32                                         # type: ignore[attr-defined]
        k.OpenProcess.restype = ctypes.c_void_p
        k.OpenProcess.argtypes = [ctypes.c_ulong, ctypes.c_int, ctypes.c_ulong]
        k.GetExitCodeProcess.argtypes = [ctypes.c_void_p, ctypes.POINTER(ctypes.c_ulong)]
        k.CloseHandle.argtypes = [ctypes.c_void_p]
        h = k.OpenProcess(0x1000, False, int(pid))                         # PROCESS_QUERY_LIMITED_INFORMATION
        if not h:
            return False
        code = ctypes.c_ulong()
        ok = k.GetExitCodeProcess(h, ctypes.byref(code))
        k.CloseHandle(h)
        return bool(ok) and code.value == 259                              # STILL_ACTIVE
    return Path(f"/proc/{int(pid)}").exists()


def boot_unix() -> Optional[float]:
    """Wall-clock time of the last boot (Windows GetTickCount64), recorded to corroborate a power/reboot rerun cause."""
    if os.name != "nt":
        return None
    k = ctypes.windll.kernel32                                             # type: ignore[attr-defined]
    k.GetTickCount64.restype = ctypes.c_ulonglong
    return time.time() - k.GetTickCount64() / 1000.0


def _cmd(args: List[str], timeout: int = 60) -> Optional[str]:
    try:
        r = subprocess.run(args, capture_output=True, text=True, timeout=timeout)
        return (r.stdout or "").strip() or None
    except (OSError, subprocess.SubprocessError):
        return None


def host_record() -> Dict[str, Any]:
    return {"wall_unix": time.time(), "ac_power": power_on_ac(), "power_plan": _cmd(["powercfg", "/getactivescheme"]),
            "cpu_clock_mhz": _cmd(["powershell", "-NoProfile", "-Command",
                                   "Get-CimInstance Win32_Processor | Select-Object CurrentClockSpeed,MaxClockSpeed | ConvertTo-Json -Compress"]),
            "defender": _cmd(["powershell", "-NoProfile", "-Command",
                              "Get-MpComputerStatus | Select-Object RealTimeProtectionEnabled,AntivirusSignatureAge | ConvertTo-Json -Compress"]),
            "windows_update_service": _cmd(["sc", "query", "wuauserv"]),
            "cpu_temperature": "unavailable without elevated sensors (recorded as such)",
            "nvme_temperature": "unavailable without elevated sensors (recorded as such)"}


def frozen() -> Dict[str, Any]:
    return json.loads((SEAL1 / "IMPLEMENTATION_FREEZE.json").read_text(encoding="utf-8"))


def runtime_path() -> Path:
    return REPO / "llama-research-r5-ets-2" / "llama-server.exe"


def check_runtime() -> str:
    rt = runtime_path()
    if not rt.is_file():
        raise SystemExit(f"{rt} not found")
    h = sha256_file(rt)
    if not (h == frozen()["binaries_sha256"]["llama-server.exe"] == driver().R5_AUTHORIZED["llama-server.exe"]):
        raise SystemExit(f"runtime sha256 {h} is not the frozen R5 runtime")
    return h


def check_model_store(a: argparse.Namespace) -> Dict[str, str]:
    imf = frozen()
    got = {"model_sha256": sha256_file(Path(a.model)), "store_sha256": sha256_file(Path(a.store))}
    if (got["model_sha256"], got["store_sha256"]) != (imf["model"]["sha256"], imf["store"]["sha256"]):
        raise SystemExit(f"model/store hash differs from the IMPLEMENTATION_FREEZE: {got}")
    return got


def preconditions(a: argparse.Namespace, out: Path) -> Dict[str, Any]:
    rid = readiness_identity()            # phase 0, G1 seal, G2 seal and the Phase 2 sources and protocol
    g1 = json.loads((SEAL1 / "G1_SEAL.json").read_text(encoding="utf-8"))
    g2 = json.loads((SEAL2 / "G2_SEAL.json").read_text(encoding="utf-8"))
    if g1["verdict"] != "R5_G1_HOST_CONFORMANCE_PASS":
        raise SystemExit("G1 is not sealed PASS")
    if g2["verdict"] != "R5_G2_HEADROOM_GATE_PASS":
        raise SystemExit("G2 is not sealed PASS")
    if any(HERE.glob("**/PHASE2_RESULT*.json")):
        raise SystemExit("a Phase 2 result is already committed: the evaluation is opened once")
    rt_sha = check_runtime()
    opening = opening_record(rid)
    if (out / RESULT).exists():
        raise SystemExit(f"{out / RESULT} exists: Phase 2 was already analysed (one-shot)")
    free = shutil.disk_usage(out).free
    if free < MIN_FREE_BYTES:
        raise SystemExit(f"{free / 1e9:.1f} GB free in {out}: at least {MIN_FREE_BYTES / 1e9:.0f} GB are required")
    mk = marker_path()
    if mk.exists() and Path(json.loads(mk.read_text(encoding="utf-8"))["out"]).resolve() != out.resolve():
        raise SystemExit(f"a Phase 2 campaign was already started elsewhere ({mk}): no second campaign")
    imf = frozen()
    ident = {"runtime": str(runtime_path()), "runtime_sha256": rt_sha, "model": a.model, "store": a.store,
             "model_sha256_frozen": imf["model"]["sha256"], "store_sha256_frozen": imf["store"]["sha256"],
             "opening_sha256": opening["opening_sha256"], **rid}
    prev = out / IDENT
    if prev.exists() and json.loads(prev.read_text(encoding="utf-8")) != ident:
        raise SystemExit(f"{out} belongs to another identity, protocol or driver: no change after the campaign started")
    if not prev.exists() and (out / LOG).exists():
        raise SystemExit(f"{out} holds launches without an identity record")
    prompts = eval_prompts(opening)
    return {"ident": ident, "runtime": runtime_path(), "prompts": prompts}


# ---------------------------------------------------------------------------------------------- launches
COMPLETED_REQUEST = re.compile(r"print_timing|\beval time\s*=")        # llama-server prints these when a request finishes
COUNTERS_ZERO = ("violations", "orphan_pending", "pending_at_event_end", "followup_mismatch", "touch_missing", "drained_at_thread_exit")


def records(out: Path) -> List[Dict[str, Any]]:
    """The acquisition log. A torn LAST line (power lost during an append) is ignored here and moved aside by `run`
    (repair_log); an unparseable line anywhere else is an integrity failure and raises."""
    log = out / LOG
    if not log.exists():
        return []
    lines = [l for l in log.read_text(encoding="utf-8").splitlines() if l.strip()]
    out_recs = []
    for i, l in enumerate(lines):
        try:
            out_recs.append(json.loads(l))
        except ValueError:
            if i == len(lines) - 1:
                break
            raise
    return out_recs


def repair_log(out: Path) -> Optional[str]:
    """Moves a torn last line of the log (no trailing newline or unparseable) to PHASE2_LOG.torn.N.txt and logs that."""
    log = out / LOG
    if not log.exists():
        return None
    b = log.read_bytes()
    if not b or b.endswith(b"\n"):
        last = b.rstrip(b"\n").rsplit(b"\n", 1)[-1] if b else b""
        try:
            if last:
                json.loads(last.decode("utf-8"))
            return None
        except ValueError:
            pass
    cut = b.rstrip(b"\n").rfind(b"\n") + 1
    torn = b[cut:]
    n = len(list(out.glob("PHASE2_LOG.torn.*.txt")))
    (out / f"PHASE2_LOG.torn.{n + 1}.txt").write_bytes(torn)
    with open(log, "r+b") as f:
        f.truncate(cut)
    append(out, {"type": "log_repaired", "torn_bytes": len(torn), "moved_to": f"PHASE2_LOG.torn.{n + 1}.txt", "unix": time.time()})
    return f"torn last log line moved aside ({len(torn)} bytes)"


def append(out: Path, rec: Dict[str, Any]) -> None:
    with (out / LOG).open("a", encoding="utf-8") as f:
        f.write(json.dumps(rec, sort_keys=True) + "\n")
        f.flush()
        os.fsync(f.fileno())


def write_atomic(p: Path, text: str, tries: int = 100) -> None:
    """tmp + os.replace; on Windows a reader holding the target open makes os.replace fail with PermissionError, so retry
    with a short back-off (the monitor reads for a few milliseconds every minute)."""
    tmp = p.with_name(p.name + f".{os.getpid()}.tmp")
    tmp.write_text(text, encoding="utf-8")
    for i in range(tries):
        try:
            os.replace(tmp, p)
            return
        except PermissionError:
            time.sleep(0.05 * (1 + i % 10))
    os.replace(tmp, p)


def slot_key(slot: Dict[str, Any]) -> str:
    """Counted slot: its seq. Campaign warmup: warmup:CAMPAIGN. Resume warmup: warmup:CAMPAIGN:before-SEQ (each warmup
    instance is its own slot for the blame rules; a rerun of it keeps the key)."""
    if slot["counted"]:
        return str(slot["seq"])
    if slot.get("kind") == "resume_warmup":
        return f"warmup:{slot['campaign']}:before-{slot['before_seq']}"
    return f"warmup:{slot['campaign']}"


def driver_slot(slot: Dict[str, Any], attempt: int) -> Dict[str, Any]:
    tag = "c" if slot["counted"] else ("r" + str(slot["before_seq"]) if slot.get("kind") == "resume_warmup" else "w")
    name = f"{slot['campaign']}-b{slot['block']:02d}p{slot['pos']}-{slot['arm']}-{tag}-a{attempt}"
    return {"seq": slot["seq"], "step": name, "budget": slot["budget"], "prompt": slot["prompt"], "arm": ARM_DRIVER[slot["arm"]],
            "runtime": "r5", "traced": slot["traced"]}


def stderr_path(out: Path, dslot: Dict[str, Any]) -> Path:
    """The path host_conformance.run_launch writes (its naming, unchanged)."""
    tag = f"{dslot['seq']:03d}-{dslot['step']}-{dslot['runtime']}-{dslot['arm']}-{dslot['budget']}GiB-{dslot['prompt']}"
    return out / "launches" / f"{tag}.stderr.log"


def keep_trace_factory(slot: Dict[str, Any], out: Path, attempt: int):
    """Replaces host_conformance.reduce_trace inside run_launch, and is FAST: it only logs that the runtime handed the raw
    trace over (trace_received) and leaves the file in place. Validation, hashing and compression happen after the launch
    result has been saved (post_trace), so an interruption there never loses the result."""
    def keep(trace: Path) -> Dict[str, Any]:
        if not trace.exists():
            return {"present": False}
        append(out, {"type": "trace_received", "key": slot_key(slot), "attempt": attempt, "raw": trace.name, "bytes": trace.stat().st_size,
                     "intended_kept": trace.name + ".gz" if slot["counted"] else None, "unix": time.time()})
        return {"present": True, "raw": trace.name, "bytes": trace.stat().st_size}
    return keep


def post_trace(slot: Dict[str, Any], out: Path, attempt: int, info: Optional[Dict[str, Any]]) -> Optional[Dict[str, Any]]:
    """Telemetry validity (pass/fail only), raw hash, and for a counted trace gzip to .partial -> hash -> rename -> trace_kept.
    Idempotent: re-run after an interruption, it reuses a trace already kept and logged."""
    if not info or not info.get("present") or "raw" not in info:
        return info
    key = slot_key(slot)
    raw = out / "traces" / info["raw"]
    gz = raw.with_name(raw.name + ".gz")
    done = [r for r in records(out) if r.get("type") == "trace_kept" and r["key"] == key and r["attempt"] == attempt]
    if done and gz.exists() and sha256_file(gz) == done[-1]["gz_sha256"]:
        d = done[-1]
        if raw.exists():
            raw.unlink()
        return {"present": True, "bytes": info.get("bytes"), "sha256": d["sha256"], "telemetry": d["telemetry"], "kept": gz.name,
                "gz_sha256": d["gz_sha256"], "gz_bytes": gz.stat().st_size}
    if not raw.exists():
        return {"present": True, "bytes": info.get("bytes"), "telemetry": {"valid": False, "reason": "raw trace missing before it was kept"}}
    M = mechanism()
    out_info: Dict[str, Any] = {"present": True, "bytes": raw.stat().st_size, "sha256": sha256_file(raw)}
    try:
        v = M.validate(M.read_lines(raw), ARM_WAIT[slot["arm"]])
        out_info["telemetry"] = {"valid": True, "decode_graphs": v["decode_graphs"], "events": v["events"], "reads": v["reads"]}
    except M.TelemetryError as e:
        out_info["telemetry"] = {"valid": False, "reason": str(e)}
    if slot["counted"]:
        part = gz.with_name(gz.name + ".partial")
        with open(raw, "rb") as src, gzip.open(part, "wb", compresslevel=6) as dst:
            shutil.copyfileobj(src, dst, 1 << 24)
        gz_sha = sha256_file(part)
        os.replace(part, gz)
        out_info.update(kept=gz.name, gz_bytes=gz.stat().st_size, gz_sha256=gz_sha)
        append(out, {"type": "trace_kept", "key": key, "attempt": attempt, "kept": gz.name, "gz_sha256": gz_sha, "sha256": out_info["sha256"],
                     "telemetry": out_info["telemetry"], "unix": time.time()})
    raw.unlink()
    return out_info


def launch_problems(rec: Dict[str, Any]) -> List[str]:
    """R5-00 INTEGRITY and the correctness conditions of one launch. Looks at no timing value beyond its presence."""
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
        p.append("not exactly one end-of-run counter record")
    c = rec.get("r5_counters") or {}
    want = ARM_WAIT[rec["arm"]]
    if (c.get("expert_wait"), c.get("explicit"), c.get("prefetch"), c.get("read_mode")) != (want, 1, "0", "asincrono"):
        p.append(f"ARGINE_EXPERT_WAIT not echoed as {want} (explicit, prefetch 0, asincrono)")
    if ((rec.get("identity") or {}).get("expert_wait"), (rec.get("identity") or {}).get("prefetch")) != (want, "0"):
        p.append("startup identity line does not echo the arm")
    for k in COUNTERS_ZERO:
        if c.get(k) != 0:
            p.append(f"counter {k} = {c.get(k)}")
    budget = {"1.5": 1610612736, "4": 4294967296}[rec["budget"]]
    if c.get("budget") != budget:
        p.append(f"budget {c.get('budget')}")
    if rec["arm"] in ("A", "B", "C0_I") and c.get("deferred_reads") != 0:
        p.append(f"deferred_reads {c.get('deferred_reads')} in a non-deferring arm")
    tr = rec.get("trace")
    if rec.get("traced"):
        if not tr or not tr.get("present") or not (tr.get("telemetry") or {}).get("valid"):
            p.append(f"trace missing or telemetry invalid: {((tr or {}).get('telemetry') or {}).get('reason')}")
        if rec.get("counted") and not (tr or {}).get("kept"):
            p.append("counted trace not kept")
    elif tr is not None:
        p.append("an untraced arm produced a trace")
    for side in ("host_before", "host_after"):
        if (rec.get(side) or {}).get("ac_power") is not True:
            p.append(f"{side}: not on AC power (or unknown)")
    if (rec.get("model_store_after") or {}).get("ok") is not True:
        p.append("model/store hash after the launch differs from the IMPLEMENTATION_FREEZE")
    if rec.get("runtime_sha256") != frozen()["binaries_sha256"]["llama-server.exe"]:
        p.append("runtime hash")
    return p


def correctness(rec: Dict[str, Any], prior: List[Dict[str, Any]]) -> Tuple[List[str], List[str]]:
    """Token digests and runtime abort markers: (correctness failures of C/C_I/C0_I, integrity failures of A/B)."""
    corr, integ = [], []
    arm = rec["arm"]
    if arm in CORRECTNESS_ARMS and ABORT_MARKERS.search(rec.get("stderr_tail") or ""):
        corr.append(f"{arm} launch aborted (runtime abort marker in stderr)")
    d = rec.get("token_digest")
    if d is None:
        return corr, integ
    same = [r for r in prior if r.get("type") == "launch" and r["prompt"] == rec["prompt"] and r.get("token_digest") and not r.get("problems")]
    ab = {r["token_digest"] for r in same if r["arm"] in ("A", "B")}
    cc = [r for r in same if r["arm"] in CORRECTNESS_ARMS]
    if arm in ("A", "B"):
        if ab and d not in ab:
            integ.append(f"{arm} token digest differs from the earlier A/B launches of {rec['prompt']}")
        elif not ab:
            bad = sorted({r["key"] for r in cc if r["token_digest"] != d})
            if bad:
                corr.append(f"earlier C/C_I/C0_I launches {bad} of {rec['prompt']} differ from the first A/B reference digest")
    elif ab and d not in ab:
        corr.append(f"{arm} token digest differs from the A/B reference of {rec['prompt']}")
    elif not ab and any(r["token_digest"] != d for r in cc):
        corr.append(f"{arm} token digest differs from another C/C_I/C0_I launch of {rec['prompt']}")
    return corr, integ


def terminal_reasons(rec: Dict[str, Any], prior: List[Dict[str, Any]]) -> Tuple[List[str], List[str]]:
    """(blocked, invalid): failures that no infrastructure cause can explain, so no rerun is possible.
    blocked = any non-infrastructure failure of C, C_I or C0_I (DECISION_RULES classification_order row 1: counters,
    abort, token digest, deferral in C0_I, wrong arm or budget echo); invalid = integrity failure of A or B (row 3)."""
    corr, integ = correctness(rec, prior)
    c = rec.get("r5_counters") or {}
    arm = rec["arm"]
    bad_counters = [f"{arm} counter {k} = {c.get(k)}" for k in COUNTERS_ZERO if c.get(k) not in (0, None)]
    wrong = []
    if c:
        want = ARM_WAIT[arm]
        if (c.get("expert_wait"), c.get("explicit"), c.get("prefetch"), c.get("read_mode")) != (want, 1, "0", "asincrono") or \
                c.get("budget") != {"1.5": 1610612736, "4": 4294967296}[rec["budget"]]:
            wrong.append(f"{arm} did not run the scheduled arm, prefetch, read mode or budget")
    if arm in CORRECTNESS_ARMS:
        corr += bad_counters + wrong
        if arm == "C0_I" and c.get("deferred_reads") not in (0, None):
            corr.append(f"C0_I deferred_reads {c.get('deferred_reads')} (the control must not defer)")
    else:
        integ += bad_counters + wrong
        if c.get("deferred_reads") not in (0, None):
            integ.append(f"{arm} deferred_reads {c.get('deferred_reads')}")
    return corr, integ


def slot_state(recs: List[Dict[str, Any]]) -> Dict[str, Dict[str, Any]]:
    """Per slot key: attempts, resolved (a successful launch; for counted slots: valid), failures, and for the open attempt
    whether it produced outcome information (trace handed over, saved result, printed timings)."""
    st: Dict[str, Dict[str, Any]] = {}
    new = lambda: {"attempts": 0, "valid": False, "failures": 0, "open": False, "trace_seen": False, "result_saved": False,
                   "open_stderr": None, "open_start": None, "arm": None, "interrupted": False, "outcome_seen": False}
    for r in recs:
        t = r.get("type")
        if t == "attempt_start":
            s = st.setdefault(r["key"], new())
            if s["open"] and not s["result_saved"]:
                s["failures"] += 1
                s["interrupted"] = True
            s.update(attempts=s["attempts"] + 1, open=True, trace_seen=False, result_saved=False, open_stderr=r.get("stderr_log"),
                     open_start=r.get("unix"), arm=r.get("arm"), outcome_seen=False, interrupted=False)
        elif t in ("trace_received", "trace_kept"):
            st.setdefault(r["key"], new())["trace_seen"] = True
        elif t == "launch_result":
            st.setdefault(r["key"], new())["result_saved"] = True
        elif t == "launch":
            s = st.setdefault(r["key"], new())
            s.update(open=False, result_saved=False,
                     outcome_seen=bool((r.get("server_timings") or {}).get("predicted_ms")) or s["trace_seen"] or bool((r.get("trace") or {}).get("present")))
            if r.get("problems"):
                s["failures"] += 1
            else:
                s["valid"] = True                        # counted: valid; warmup: resolved
    return st


def printed_timings(s: Dict[str, Any]) -> bool:
    p = s.get("open_stderr")
    return bool(p) and Path(p).exists() and bool(COMPLETED_REQUEST.search(Path(p).read_text(encoding="utf-8", errors="replace")))


def close_open(st: Dict[str, Dict[str, Any]]) -> Dict[str, Dict[str, Any]]:
    """At start-up only (no run is live). An open attempt with a saved result is completed by the next run (not a failure,
    never rerun); an open attempt without one was interrupted and counts as a failure; whether it had already produced
    outcome information (a trace handed over, or the server's printed timings) is recorded."""
    for s in st.values():
        if s["open"] and not s["result_saved"]:
            s["failures"] += 1
            s["open"] = False
            s["interrupted"] = True
            s["outcome_seen"] = s["trace_seen"] or printed_timings(s)
    return st


def stop(out: Path, code: int, why: List[str], key: Optional[str] = None, aborted: bool = False, correctness_failure: bool = False,
         arm: Optional[str] = None, second_failure: bool = False) -> int:
    terminal = aborted or correctness_failure
    basis = "correctness" if correctness_failure else ("integrity" if aborted else ("second_failure" if second_failure else None))
    write_atomic(out / STOPPED, json.dumps({"stopped": True, "aborted": aborted, "correctness_failure": correctness_failure, "terminal": terminal,
                                            "basis": basis, "arm": arm, "exit_code": code, "key": key, "reasons": why, "analysis_run": False,
                                            "note": "R5-00 blame_rules: a failed launch of C, C_I or C0_I is attributed to the intervention "
                                                    "unless it matches a listed infrastructure cause with documentary evidence; a correctness "
                                                    "failure is never rerun; a failed A or B launch is attributed to the campaign; one rerun "
                                                    "per slot (--rerun-infrastructure CAUSE --evidence FILE); a second failure of the same "
                                                    "slot aborts the campaign; `finalize` attributes a stop for which no cause is claimed"},
                                           indent=1))
    label = "TERMINAL (correctness failure)" if correctness_failure else ("ABORTED" if aborted else "STOPPED")
    print(f"PHASE 2 {label}: " + "; ".join(why)); return code


def windows_events_since(since_unix: float, queries: List[Tuple[str, str]]) -> List[str]:
    """Event-log corroboration (wevtutil, no shell): events of the given (log, XPath provider filter) since a time."""
    if os.name != "nt":
        return []
    ms = max(1, int((time.time() - since_unix) * 1000))
    hits = []
    for log, prov in queries:
        q = f"*[System[{prov} and TimeCreated[timediff(@SystemTime) <= {ms}]]]"
        r = _cmd(["wevtutil", "qe", log, f"/q:{q}", "/c:5", "/rd:true", "/f:text"], timeout=60)
        if r and "Event[" in r:
            hits.append(f"{log}: {prov}")
    return hits


UNEXPECTED_SHUTDOWN_EVENTS = [("System", "Provider[@Name='Microsoft-Windows-Kernel-Power'] and EventID=41"),
                              ("System", "Provider[@Name='EventLog'] and EventID=6008")]
UPDATE_DEFENDER_EVENTS = [("System", "Provider[@Name='Microsoft-Windows-WindowsUpdateClient']"),
                          ("Microsoft-Windows-Windows Defender/Operational", "(EventID=1000 or EventID=1001 or EventID=1002)")]
DISK_FULL_TEXT = re.compile(r"no space|disk full|ENOSPC|ERROR_DISK_FULL|There is not enough space", re.I)


def corroborate(cause: str, failed: List[str], st: Dict[str, Dict[str, Any]], out: Path) -> Optional[str]:
    """A slot is rerun only if its failed attempt produced NO outcome information (no launch record, no trace handed over,
    no printed request timings): then no repetition can depend on an outcome. The cause must also be corroborated by the
    host (the evidence file documents, it does not prove): power -> an unexpected-shutdown event (Kernel-Power 41 or
    EventLog 6008) since the attempt started, or the driver's own AC-power stop; Windows Update or Defender -> an Update
    client or Defender scan event since the attempt started; disk full -> the driver's own disk stop or a disk-full
    message of the failed launch; operator interruption -> nothing more (the attempt produced no outcome information)."""
    for k in failed:
        s = st[k]
        if s.get("outcome_seen") and not k.startswith("warmup:"):         # warmups are never analysed
            return (f"slot {k}: its failed attempt produced outcome information (a launch record, a trace or printed timings): it is not "
                    "rerun for any cause (no outcome-dependent repetition); run `campaign.py finalize`")
    stp = json.loads((out / STOPPED).read_text(encoding="utf-8")) if (out / STOPPED).exists() else {}
    starts = [st[k].get("open_start") or next((r["unix"] for r in records(out) if r.get("type") == "attempt_start" and r["key"] == k), 0) for k in failed]
    since = min(starts) if starts else time.time()
    if cause == "power":
        ac_stop = any("AC power" in x for x in stp.get("reasons", []))
        if not (ac_stop or windows_events_since(since, UNEXPECTED_SHUTDOWN_EVENTS)):
            return "power: no unexpected-shutdown event since the failed attempt started and no AC-power stop by the driver: not corroborated"
    if cause == "windows-update-or-defender":
        if not windows_events_since(since, UPDATE_DEFENDER_EVENTS):
            return "windows-update-or-defender: no Windows Update client or Defender scan event since the failed attempt started: not corroborated"
    if cause == "disk-full":
        own = any("GB free" in x for x in stp.get("reasons", []))
        text = any(DISK_FULL_TEXT.search(json.dumps(r.get("error")) + (r.get("stderr_tail") or ""))
                   for r in records(out) if r.get("type") == "launch" and r.get("key") in failed)
        if not (own or text):
            return "disk-full: no disk stop by the driver and no disk-full message of the failed launch: not corroborated"
    return None


def authorize_rerun(a: argparse.Namespace, out: Path, st: Dict[str, Dict[str, Any]]) -> Optional[str]:
    failed = sorted((k for k, x in st.items() if x["failures"] and not x["valid"]), key=lambda k: (not k.isdigit(), int(k) if k.isdigit() else 0, k))
    stopped = (out / STOPPED).exists()
    if not failed and not stopped:
        return None
    if stopped:
        s = json.loads((out / STOPPED).read_text(encoding="utf-8"))
        if s.get("terminal"):
            return "the campaign ended terminally (correctness failure or abort): run `campaign.py analyze` for the final label; no rerun"
    if any(st[k]["failures"] >= 2 for k in failed) or (stopped and json.loads((out / STOPPED).read_text(encoding="utf-8")).get("basis") == "second_failure"):
        return "a slot failed twice: the campaign is aborted (blame_rules); run `campaign.py finalize` for the attribution"
    if not failed:                                   # stopped before a launch (power, disk, runtime hash): no slot failed
        n = len(list(out.glob("ACQUISITION_STOPPED.*.json")))
        (out / STOPPED).rename(out / f"ACQUISITION_STOPPED.{n + 1}.json")
        append(out, {"type": "resumed_after_stop", "unix": time.time()})
        return None
    if not getattr(a, "rerun_infrastructure", None) or not getattr(a, "evidence", None):
        return f"slot(s) {failed} failed or were interrupted: rerun only with --rerun-infrastructure CAUSE --evidence FILE, or `finalize`"
    if a.rerun_infrastructure not in INFRASTRUCTURE_CAUSES or not Path(a.evidence).is_file():
        return f"--rerun-infrastructure must be one of {sorted(INFRASTRUCTURE_CAUSES)} and --evidence an existing file"
    why = corroborate(a.rerun_infrastructure, failed, st, out)
    if why:
        return why
    ev_dir = out / "evidence"; ev_dir.mkdir(exist_ok=True)
    ev_sha = sha256_file(Path(a.evidence))
    shutil.copy2(a.evidence, ev_dir / f"{ev_sha[:16]}-{Path(a.evidence).name}")
    dr = json.loads((P0_DIR / "DECISION_RULES.json").read_text(encoding="utf-8"))
    append(out, {"type": "rerun_authorized", "keys": failed, "cause": dr["blame_rules"]["infrastructure_causes"][INFRASTRUCTURE_CAUSES[a.rerun_infrastructure]],
                 "cause_key": a.rerun_infrastructure, "evidence": Path(a.evidence).name, "evidence_sha256": ev_sha, "boot_unix": boot_unix(),
                 "unix": time.time()})
    if stopped:
        n = len(list(out.glob("ACQUISITION_STOPPED.*.json")))
        (out / STOPPED).rename(out / f"ACQUISITION_STOPPED.{n + 1}.json")
    return None


def heartbeat(out: Path, phase: str, key: Optional[str]) -> None:
    """Monitoring only: a failure to write the heartbeat never ends or invalidates a launch."""
    try:
        write_atomic(out / RUN_STATE, json.dumps({"pid": os.getpid(), "phase": phase, "key": key, "updated_unix": time.time()}))
    except OSError:
        pass


def acquire_lock(out: Path) -> Optional[str]:
    """Exclusive run lock (RUN_STATE.json created with O_EXCL) taken before anything else; a stale lock of a dead process is
    replaced. Returns a reason if another run is live."""
    p = out / RUN_STATE
    for _ in range(2):
        try:
            fd = os.open(str(p), os.O_CREAT | os.O_EXCL | os.O_WRONLY)
            with os.fdopen(fd, "w", encoding="utf-8") as f:
                f.write(json.dumps({"pid": os.getpid(), "phase": "starting", "key": None, "updated_unix": time.time()}))
            return None
        except FileExistsError:
            try:
                s = json.loads(p.read_text(encoding="utf-8"))
            except (ValueError, OSError):
                s = {}
            if s.get("pid") and pid_alive(int(s["pid"])) and time.time() - float(s.get("updated_unix", 0)) < 7200:
                return f"a run is already live (pid {s['pid']})"
            try:                                         # narrow the race: replace only the stale content we judged
                if json.loads(p.read_text(encoding="utf-8")) != s:
                    continue
            except (ValueError, OSError):
                pass
            try:                                         # stale lock of a dead or silent (> 2 h) process: move it aside atomically
                os.replace(p, p.with_name(f"RUN_STATE.stale.{os.getpid()}.{int(time.time() * 1000)}.json"))
            except OSError:
                pass
    return "could not take the run lock"


def stderr_tail(rec: Dict[str, Any]) -> str:
    try:
        return Path(rec.get("stderr_log") or "").read_text(encoding="utf-8", errors="replace")[-4000:]
    except OSError:
        return ""


def run_one(slot: Dict[str, Any], a: argparse.Namespace, out: Path, pre: Dict[str, Any], attempt: int, host: Dict[str, Any]) -> Dict[str, Any]:
    H = driver()
    key = slot_key(slot)
    rt_sha = sha256_file(pre["runtime"])
    dslot = driver_slot(slot, attempt)
    original = H.reduce_trace
    H.reduce_trace = keep_trace_factory(slot, out, attempt)
    try:
        rec = H.run_launch(dslot, pre["runtime"], Path(a.model), Path(a.store), out, pre["prompts"][slot["prompt"]])
    finally:
        H.reduce_trace = original
    rec.update({k: slot[k] for k in ("seq", "campaign", "block", "pos", "arm", "prompt", "budget", "traced", "counted")})
    rec.update({"key": key, "attempt": attempt, "kind": slot.get("kind", "counted"), "artifact": "R5-03-PHASE2-LAUNCH", "driver_arm": dslot["arm"],
                "host_before": host, "runtime_sha256": rt_sha, "opening_sha256": pre["ident"]["opening_sha256"]})
    append(out, {"type": "launch_result", "key": key, "attempt": attempt, "slot": slot, "rec": rec, "unix": time.time()})   # saved first
    return finish(slot, rec, a, out, attempt)


def finish(slot: Dict[str, Any], rec: Dict[str, Any], a: argparse.Namespace, out: Path, attempt: int, resumed: bool = False) -> Dict[str, Any]:
    """Post-processing of a saved result (also after an interruption): trace, model/store hashes, host record."""
    key = slot_key(slot)
    heartbeat(out, "post-processing", key)
    rec = dict(rec)
    if slot["traced"]:
        rec["trace"] = post_trace(slot, out, attempt, rec.get("trace"))
    try:
        ms = dict(check_model_store(a), ok=True)
    except SystemExit as e:
        ms = {"ok": False, "error": str(e)}
    rec.update({"type": "launch", "host_after": host_record(), "model_store_after": ms, "stderr_tail": stderr_tail(rec),
                "post_processing_resumed": resumed})
    return rec


def record_and_judge(out: Path, slot: Dict[str, Any], rec: Dict[str, Any]) -> Optional[int]:
    """Validity, correctness and integrity of one finished launch; appends the launch record; returns an exit code to stop."""
    key = slot_key(slot)
    prob = launch_problems(rec)
    corr, integ = terminal_reasons(rec, records(out))
    rec["problems"] = prob + [x for x in integ + corr if x not in prob]
    rec["correctness_failures"], rec["integrity_failures"] = corr, integ
    append(out, rec)
    if corr:
        return stop(out, EXIT_TERMINAL, corr, key, correctness_failure=True, arm=slot["arm"])
    if integ:
        return stop(out, EXIT_TERMINAL, integ + ["integrity failure of A or B (no infrastructure cause applies): R5_INVALID"], key, aborted=True, arm=slot["arm"])
    if rec["problems"]:
        failures = slot_state(records(out)).get(key, {}).get("failures", 0)
        if failures >= 2:
            return stop(out, EXIT_ABORTED, rec["problems"] + ["second failure of this slot: campaign aborted; `finalize` attributes it"],
                        key, arm=slot["arm"], second_failure=True)
        code = EXIT_TELEMETRY if any("telemetry" in x or "trace" in x for x in rec["problems"]) else EXIT_LAUNCH_INVALID
        return stop(out, code, rec["problems"], key, arm=slot["arm"])
    return None


def complete_pending(a: argparse.Namespace, out: Path) -> Optional[int]:
    """Attempts whose result was saved but whose post-processing was interrupted are completed (never rerun)."""
    recs = records(out)
    done = {(r["key"], r["attempt"]) for r in recs if r.get("type") == "launch"}
    for r in recs:
        if r.get("type") == "launch_result" and (r["key"], r["attempt"]) not in done:
            print(f"completing the post-processing of {r['key']} attempt {r['attempt']} (saved result; not rerun)")
            rec = finish(r["slot"], r["rec"], a, out, r["attempt"], resumed=True)
            code = record_and_judge(out, r["slot"], rec)
            if code is not None:
                return code
    return None


def build_queue(st: Dict[str, Dict[str, Any]], recs: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    todo = [s for s in plan() if not st.get(str(s["seq"]), {}).get("valid")]
    done_camps = {s["campaign"] for s in plan() if st.get(str(s["seq"]), {}).get("valid")}
    warm_done = {r["campaign"] for r in recs if r.get("type") == "launch" and r.get("kind") == "campaign_warmup" and not r.get("problems")}
    queue: List[Dict[str, Any]] = []
    first = True
    for s in todo:
        if s["campaign"] not in warm_done and s["campaign"] not in done_camps and (s["block"], s["pos"]) == (1, 1):
            queue.append(campaign_warmup(s["campaign"]))
            warm_done.add(s["campaign"])
        elif first:
            queue.append(resume_warmup(s))
        first = False
        queue.append(s)
    return queue


def cmd_run(a: argparse.Namespace) -> int:
    out = Path(a.out); out.mkdir(parents=True, exist_ok=True)
    why = acquire_lock(out)
    if why:
        print(f"PHASE 2 REFUSED: {why}"); return EXIT_PRECONDITION
    try:
        try:
            pre = preconditions(a, out)
        except SystemExit as e:
            print(f"PHASE 2 PRECONDITION FAILED: {e}"); return EXIT_PRECONDITION
        fixed = repair_log(out)
        if fixed:
            print("PHASE 2:", fixed)
        code = complete_pending(a, out)
        if code is not None:
            return code
        st = close_open(slot_state(records(out)))
        why = authorize_rerun(a, out, st)
        if why:
            print(f"PHASE 2 REFUSED: {why}")
            return EXIT_TERMINAL if "terminal" in why else (EXIT_ABORTED if "abort" in why else EXIT_PRECONDITION)
        (out / IDENT).write_text(json.dumps(pre["ident"], indent=1), encoding="utf-8")
        mk = marker_path()
        if not mk.exists():
            mk.parent.mkdir(parents=True, exist_ok=True)
            mk.write_text(json.dumps({"out": str(out.resolve()), "started_unix": time.time(), "identity": pre["ident"]}, indent=1), encoding="utf-8")
        try:
            check_model_store(a)
        except SystemExit as e:
            return stop(out, EXIT_PRECONDITION, [str(e)])
        queue = build_queue(st, records(out))
        if not queue:
            return analyze(out)
        cooldown = json.loads((P0_DIR / "SCHEDULE.json").read_text(encoding="utf-8"))["cooldown_between_launches_s"]
        for slot in queue:
            key = slot_key(slot)
            if (out / PAUSE).exists():
                (out / PAUSE).unlink()
                append(out, {"type": "paused", "before": key, "unix": time.time()})
                print("PHASE 2 PAUSED cleanly before", key, "(not a failure; `run` again to resume, a warmup comes first)")
                return 0
            heartbeat(out, "pre-launch checks", key)
            if power_on_ac() is not True:
                return stop(out, EXIT_PRECONDITION, ["host not on AC power, or the power state is unknown"], key)
            if slot["traced"] and shutil.disk_usage(out).free < MIN_FREE_TRACED:
                return stop(out, EXIT_PRECONDITION, ["less than 1 GB free before a traced launch"], key)
            try:
                check_runtime()
            except SystemExit as e:
                return stop(out, EXIT_PRECONDITION, [str(e)], key)
            host = host_record()
            attempt = 1 + sum(1 for r in records(out) if r.get("type") == "attempt_start" and r["key"] == key)
            heartbeat(out, "launch", key)
            append(out, {"type": "attempt_start", "key": key, "attempt": attempt, "arm": slot["arm"], "unix": time.time(),
                         "stderr_log": str(stderr_path(out, driver_slot(slot, attempt)))})
            label = f"[{slot['seq']}/196]" if slot["counted"] else "[warmup]"
            print(f"{label} {slot['campaign']} block {slot['block']} pos {slot['pos']} {slot['arm']} {slot['budget']} GiB {slot['prompt']}"
                  + ("" if slot["counted"] else " (uncounted)")); sys.stdout.flush()
            rec = run_one(slot, a, out, pre, attempt, host)
            code = record_and_judge(out, slot, rec)
            if code is not None:
                return code
            heartbeat(out, "cooldown", key)
            time.sleep(WARMUP_COOLDOWN_S if not slot["counted"] else cooldown)
        return analyze(out)
    finally:
        rs = out / RUN_STATE
        try:
            if rs.exists() and json.loads(rs.read_text(encoding="utf-8")).get("pid") == os.getpid():
                rs.unlink()
        except (ValueError, OSError):
            pass


def cmd_finalize(a: argparse.Namespace) -> int:
    """Attributes an unresolved stop, abort or interruption by blame_rules and makes it terminal. Without a documented
    infrastructure cause: a failed C, C_I or C0_I slot -> the intervention (R5_IMPLEMENTATION_OR_CAPABILITY_BLOCKED,
    classification_order row 1); otherwise -> the campaign (R5_INVALID). With --infrastructure CAUSE --evidence FILE
    (a documented infrastructure cause for the last failure of an aborted campaign): R5_INVALID (campaign aborted)."""
    out = Path(a.out)
    if (out / RUN_STATE).exists():
        s = json.loads((out / RUN_STATE).read_text(encoding="utf-8"))
        if pid_alive(int(s.get("pid", -1))):
            print("finalize refused: a run is live"); return EXIT_PRECONDITION
    recs = records(out)
    st = close_open(slot_state(recs))
    failed = [k for k, x in st.items() if x["failures"] and not x["valid"]]
    stp = json.loads((out / STOPPED).read_text(encoding="utf-8")) if (out / STOPPED).exists() else None
    if stp and stp.get("terminal"):
        print("already terminal"); return 0
    input_failure = any(r.get("type") == "analysis_input_failure" for r in recs) and not (out / RESULT).exists()
    if not failed and not stp and not input_failure:
        print("nothing to finalize: no failed, interrupted or stopped slot and no analysis-input failure"); return EXIT_PRECONDITION
    arms = {st[k].get("arm") or next((r["arm"] for r in recs if r.get("key") == k and r.get("arm")), None) for k in failed}
    documented = False
    if getattr(a, "infrastructure", None):
        if not (getattr(a, "evidence", None) and Path(a.evidence).is_file()):
            print("finalize refused: --infrastructure needs --evidence FILE"); return EXIT_PRECONDITION
        why = corroborate(a.infrastructure, failed, st, out)
        if why:
            print(f"finalize refused: {why}"); return EXIT_PRECONDITION
        documented = True
    corr = bool(arms & CORRECTNESS_ARMS) and not documented and not (input_failure and not failed)
    rec = dict(stp or {"stopped": True, "reasons": [f"unresolved failed or interrupted slots {failed}"], "key": failed[0] if failed else None})
    rec.update(terminal=True, correctness_failure=corr, aborted=not corr, basis="finalized", finalized_unix=time.time(), failed_slots=failed,
               documented_infrastructure=a.infrastructure if documented else None,
               evidence_sha256=sha256_file(Path(a.evidence)) if documented else None,
               analysis_input_failure=input_failure,
               finalized_reason=("no corroborated infrastructure cause: a failed C, C_I or C0_I slot is attributed by blame_rules to the "
                                 "intervention" if corr else "attributed by blame_rules to the campaign (R5_INVALID)"))
    write_atomic(out / STOPPED, json.dumps(rec, indent=1))
    append(out, {"type": "finalized", "failed_slots": failed, "arms": sorted(x for x in arms if x), "documented": documented, "unix": time.time()})
    print("PHASE 2 FINALIZED:", rec["finalized_reason"]); return 0


# ---------------------------------------------------------------------------------------------- the one-shot analysis
def counted_valid(recs: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    best: Dict[int, Dict[str, Any]] = {}
    for r in recs:
        if r.get("type") == "launch" and r.get("counted") and not r.get("problems"):
            best[r["seq"]] = r
    return [best[s] for s in sorted(best)]


def rerun_report(recs: List[Dict[str, Any]]) -> Dict[str, Any]:
    """Every rerun and every superseded attempt is reported (never used): the reroll channel is visible and bounded."""
    sup = [{k: r.get(k) for k in ("key", "attempt", "arm", "prompt", "budget", "problems", "server_timings")}
           for r in recs if r.get("type") == "launch" and r.get("problems")]
    starts = [(r["key"], r["attempt"]) for r in recs if r.get("type") == "attempt_start"]
    recorded = {(r["key"], r["attempt"]) for r in recs if r.get("type") == "launch"}
    return {"reruns_authorized": [r for r in recs if r.get("type") == "rerun_authorized"],
            "superseded_launches": sup, "interrupted_attempts": [list(x) for x in starts if x not in recorded],
            "counted_reruns": sum(1 for r in recs if r.get("type") == "launch" and r.get("counted") and r.get("attempt", 1) > 1 and not r.get("problems"))}


def result_from(out: Path, recs: List[Dict[str, Any]]) -> Dict[str, Any]:
    A, M = analysis(), mechanism()
    counted = counted_valid(recs)
    sched = plan()
    if [r["seq"] for r in counted] != [s["seq"] for s in sched] or any((r["arm"], r["prompt"], r["campaign"], r["block"], r["pos"]) !=
                                                                    (s["arm"], s["prompt"], s["campaign"], s["block"], s["pos"]) for r, s in zip(counted, sched)):
        raise M.TelemetryError("the 196 counted launches are not complete and in the frozen schedule")
    mech: Dict[int, Dict[str, Any]] = {}
    for r in counted:
        if r["traced"]:
            gz = out / "traces" / r["trace"]["kept"]
            if sha256_file(gz) != r["trace"]["gz_sha256"]:
                raise M.TelemetryError(f"kept trace {gz.name} differs from its recorded hash")
            mech[r["seq"]] = M.metrics(M.read_lines(gz), ARM_WAIT[r["arm"]]).as_dict()
            mech[r["seq"]]["trace_reads"] = r["trace"]["telemetry"].get("reads")
            mech[r["seq"]]["counter_misses"] = r["r5_counters"].get("misses")
    res = A.classify(counted, mech)
    named = {r["kept"] for r in recs if r.get("type") == "trace_kept"} | {r.get("intended_kept") for r in recs if r.get("type") == "trace_received"} \
        | {r.get("raw") for r in recs if r.get("type") == "trace_received"}
    used = {r["trace"]["kept"] for r in counted if r["traced"]}
    leftovers, unexplained = [], []
    for f in sorted((out / "traces").iterdir()) if (out / "traces").exists() else []:
        base = f.name[:-len(".partial")] if f.name.endswith(".partial") else f.name
        if f.name in used:
            continue
        (leftovers if base in named else unexplained).append(f.name)
    if unexplained:
        raise M.TelemetryError(f"traces not named by any log record: {unexplained}")
    return {"artifact": "R5-03-PHASE2-RESULT", **res, "mechanism_per_launch": {str(k): v for k, v in mech.items()},
            "redundant_transfers_check": {str(k): {"trace_reads": v["trace_reads"], "counter_misses": v["counter_misses"],
                                                   "reads_equal_misses": v["trace_reads"] == v["counter_misses"]} for k, v in mech.items()},
            "superseded_or_interrupted_trace_files": leftovers, "reruns": rerun_report(recs),
            "launches": [{k: r.get(k) for k in ("seq", "campaign", "block", "pos", "arm", "prompt", "budget", "attempt", "token_digest")} for r in counted],
            "computed_once": True}


def analyze(out: Path) -> int:
    """One-shot. Refuses, with no side effect, while a run is live, before all 196 counted launches are valid (unless the
    campaign ended terminally), or when the readiness, protocol or code differ from the campaign's identity."""
    if (out / RESULT).exists():
        print("Phase 2 already analysed in this directory (one-shot)"); return EXIT_PRECONDITION
    rs = out / RUN_STATE
    if rs.exists():
        try:
            s = json.loads(rs.read_text(encoding="utf-8"))
        except (ValueError, OSError):
            s = {}
        if s.get("pid") and int(s["pid"]) != os.getpid() and pid_alive(int(s["pid"])):
            print("PHASE 2 analysis refused: a run is live"); return EXIT_PRECONDITION
    A, M = analysis(), mechanism()
    try:
        rid = readiness_identity()
    except SystemExit as e:
        print(f"PHASE 2 analysis refused (no side effect): {e}"); return EXIT_PRECONDITION
    ident = json.loads((out / IDENT).read_text(encoding="utf-8"))
    if any(ident.get(k) != v for k, v in rid.items()):
        print("PHASE 2 analysis refused (no side effect): analysis, driver, protocol or readiness changed since the campaign started")
        return EXIT_PRECONDITION
    stp = json.loads((out / STOPPED).read_text(encoding="utf-8")) if (out / STOPPED).exists() else None
    if stp and stp.get("terminal"):
        corr = bool(stp.get("correctness_failure"))
        res = {"artifact": "R5-03-PHASE2-RESULT", **A.terminal("; ".join(stp.get("reasons", [])), corr), "stop": stp,
               "reruns": rerun_report(records(out)), "computed_once": True}
    elif stp:
        print("PHASE 2 stopped: resolve it under blame_rules (rerun with evidence, or `finalize`) before the analysis"); return EXIT_PRECONDITION
    elif len(counted_valid(records(out))) < len(plan()):
        print(f"PHASE 2 not complete ({len(counted_valid(records(out)))}/196 counted launches valid): nothing is analysed before all are")
        return EXIT_PRECONDITION
    else:
        try:
            res = result_from(out, records(out))
        except (M.TelemetryError, ValueError) as e:
            print(f"PHASE 2 analysis input failure (no result written): {e}. The kept evidence does not support the analysis; under "
                  "blame_rules this is an integrity failure of the campaign: `finalize` records it as R5_INVALID.")
            append(out, {"type": "analysis_input_failure", "reason": str(e), "unix": time.time()})
            return EXIT_TELEMETRY
    res["identity"] = ident
    (out / RESULT).write_text(json.dumps(res, indent=1, sort_keys=True), encoding="utf-8")
    print(f"R5 PHASE 2 CLASSIFICATION: {res['classification']}")
    if res.get("endpoints_computed"):
        g = res["gates"]
        print("gates: " + ", ".join(f"{k} {'PASS' if v else 'FAIL'}" for k, v in g.items()))
        print(f"primary: mean relative reduction {res['primary_1.5GiB']['aggregate_mean_r']:.6f}, 95% lower bound "
              f"{res['primary_1.5GiB']['interval95']['lower']:.6f} (MEI 0.02)")
    print("Only R5_POSITIVE_RUNTIME_IMPROVEMENT is a demonstrated improvement; every other label is not.")
    return 0


# ---------------------------------------------------------------------------------------------- monitor and verify
def status_dict(out: Path) -> Dict[str, Any]:
    recs = records(out)
    st = slot_state(recs)
    live = None
    rs = out / RUN_STATE
    if rs.exists():
        try:
            s = json.loads(rs.read_text(encoding="utf-8"))
            live = s if pid_alive(int(s.get("pid", -1))) else None
        except (ValueError, OSError):
            live = {"pid": None, "phase": "starting", "key": None}          # being written: treat as live, never as failed
    sched = plan()
    camps = {}
    for camp in ("mechanism_1.5GiB", "timing_1.5GiB", "timing_4GiB"):
        seqs = [x["seq"] for x in sched if x["campaign"] == camp]
        camps[camp] = {"valid": sum(1 for q in seqs if st.get(str(q), {}).get("valid")), "of": len(seqs)}
    open_keys = [k for k, x in st.items() if x["open"]]
    pending = [k for k, x in st.items() if x["open"] and x["result_saved"]]
    last = next((r["key"] for r in reversed(recs) if r.get("type") == "attempt_start"), None)
    newest = last if last in open_keys else None
    running = (newest or live.get("key")) if live else None
    interrupted = [k for k in open_keys if not (live and k == newest) and k not in pending]   # newest open attempt of a live run: running
    failed = [k for k, x in st.items() if x["failures"] and not x["valid"]]
    stp = json.loads((out / STOPPED).read_text(encoding="utf-8")) if (out / STOPPED).exists() else None
    return {"campaigns": camps, "counted_valid": sum(v["valid"] for v in camps.values()), "counted_total": 196,
            "running": running, "running_phase": live.get("phase") if live else None,
            "interrupted_attempts_counting_as_failures": interrupted, "failed_slots": failed,
            "saved_results_pending_post_processing": [k for k in pending if not (live and k == newest)],
            "warmups_done": sum(1 for r in recs if r.get("type") == "launch" and not r.get("counted") and not r.get("problems")),
            "reruns_authorized": sum(1 for r in recs if r.get("type") == "rerun_authorized"),
            "paused": bool(recs) and recs[-1].get("type") == "paused", "pause_requested": (out / PAUSE).exists(),
            "stopped": bool(stp), "terminal": bool(stp and stp.get("terminal")), "analysis_done": (out / RESULT).exists()}


def cmd_status(a: argparse.Namespace) -> int:
    d = status_dict(Path(a.out))
    if a.json:
        print(json.dumps(d, sort_keys=True)); return 0
    c = d["campaigns"]
    print(f"counted valid {d['counted_valid']}/196 | mechanism {c['mechanism_1.5GiB']['valid']}/36 | timing 1.5 GiB {c['timing_1.5GiB']['valid']}/80 | "
          f"timing 4 GiB {c['timing_4GiB']['valid']}/80 | warmups {d['warmups_done']} | running: {d['running'] or '-'}"
          f"{' (' + d['running_phase'] + ')' if d['running_phase'] else ''} | interrupted: {d['interrupted_attempts_counting_as_failures'] or '-'} | "
          f"failed: {d['failed_slots'] or '-'} | reruns: {d['reruns_authorized']} | paused: {d['paused']} | stopped: {d['stopped']} | "
          f"terminal: {d['terminal']} | analysis done: {d['analysis_done']}")
    return 0


def cmd_verify(a: argparse.Namespace) -> int:
    out = Path(a.out)
    if not (out / RESULT).exists():
        print("no Phase 2 result in this directory"); return EXIT_PRECONDITION
    stored = json.loads((out / RESULT).read_text(encoding="utf-8"))
    if not stored.get("endpoints_computed"):
        print("terminal result (no endpoint computed): nothing to re-derive"); return 0
    again = result_from(out, records(out))
    again["identity"] = stored["identity"]
    same = json.loads(json.dumps(again, sort_keys=True)) == stored
    print("Phase 2 result re-derived from the log and the kept traces:", "IDENTICAL" if same else "DIFFERENT"); return 0 if same else 1


def main(argv: Optional[List[str]] = None) -> int:
    ap = argparse.ArgumentParser()
    sp = ap.add_subparsers(dest="cmd", required=True)
    o = sp.add_parser("authorize-opening"); o.add_argument("--readiness", required=True); o.add_argument("--statement", required=True)
    r = sp.add_parser("run"); r.add_argument("--model", required=True); r.add_argument("--store", required=True); r.add_argument("--out", required=True)
    r.add_argument("--rerun-infrastructure", choices=sorted(INFRASTRUCTURE_CAUSES)); r.add_argument("--evidence")
    s = sp.add_parser("status"); s.add_argument("--out", required=True); s.add_argument("--json", action="store_true")
    z = sp.add_parser("analyze"); z.add_argument("--out", required=True)
    v = sp.add_parser("verify"); v.add_argument("--out", required=True)
    f = sp.add_parser("finalize"); f.add_argument("--out", required=True)
    f.add_argument("--infrastructure", choices=sorted(INFRASTRUCTURE_CAUSES)); f.add_argument("--evidence")
    a = ap.parse_args(argv)
    if a.cmd == "analyze":
        return analyze(Path(a.out))
    return {"authorize-opening": cmd_authorize, "run": cmd_run, "status": cmd_status, "verify": cmd_verify, "finalize": cmd_finalize}[a.cmd](a)


if __name__ == "__main__":
    sys.exit(main())
