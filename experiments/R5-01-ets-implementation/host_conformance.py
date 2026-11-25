"""Research 5 Phase 1a -- development-only host conformance probe (gate G1 of R5-00 DECISION_RULES.json).

Runs on the Windows host, on the four frozen DEVELOPMENT prompts only. It never loads an evaluation prompt, never computes
the headroom bound H (Phase 1b) and never computes or prints a timing contrast between C (kind) or C0 (layer_touch) and A
(layer). Every launch record (including its raw server timings) is appended to the development log, as the frozen
IMPLEMENTATION_FREEZE rule requires.

    python host_conformance.py run --model M.gguf --store M.psxs --v5-search-root %USERPROFILE% --out OUTDIR
    python host_conformance.py evaluate --out OUTDIR

Steps of `run` (each launch: fresh process, 512 greedy tokens with ignore_eos, 12 threads, context 4096):
  1. identities: R5 runtime and Windows conformance-test hashes against llama-research-r5-ets/BUILD-INFO.json; the
     llama-research-v5 runtime, accepted only as its historical raw SHA-256 or as the ONE reconstruction frozen in
     V5-RECOVERY.json (v5_identity); model and store SHA-256 recorded;
  2. the model-free conformance test on the real Windows OVERLAPPED path (argine-ets-test.exe);
  3. startup smoke: --version; fail-closed refusals (invalid ARGINE_EXPERT_WAIT, kind + ARGINE_PREFETCH=2,
     kind + ARGINE_EXPERT_READ=blocco) must abort before serving;
  4. arm conformance: A (layer), C0 (layer_touch), C (kind) on every development prompt at 1.5 and 4 GiB (24 launches);
  5. baseline fidelity at 1.5 GiB: per development prompt v5, R5-A, R5-A, v5 (16 launches);
  6. instrumentation completeness: B (layer, traced) and C_I (kind, traced) on code-dev-1 at 1.5 GiB; the raw trace is
     reduced to ets_trace.structure() (booleans and counts) and deleted.
`evaluate` applies G1 mechanically and writes G1_HOST_RESULT.json.
"""
from __future__ import annotations

import argparse
import hashlib
import importlib.util
import json
import os
import re
import subprocess
import sys
import time
import urllib.request
from pathlib import Path
from typing import Any, Dict, List, Optional

HERE = Path(__file__).resolve().parent
REPO = HERE.parents[1]
P0 = REPO / "experiments" / "R5-00-exposed-transfer-stall"
BUILD_INFO = REPO / "llama-research-r5-ets" / "BUILD-INFO.json"
V5_SHA256 = "65f445c42ac145765735984582691964181dd4dd8019f4519edac1fa315df26c"   # llama-research-v5/BUILD-INFO.json
# The historical v5 executable was lost (untracked file, local cleanup). V5-RECOVERY.json freezes ONE deterministic
# reconstruction from the frozen v5 source/toolchain/build contract. It is accepted only if the binary has exactly this raw
# SHA-256 and PE-normalized SHA-256 AND the manifest agrees on every pinned value below. No other v5 rebuild is accepted.
V5_RECOVERY = HERE / "V5-RECOVERY.json"
V5_RECONSTRUCTED = {
    "reconstructed_raw_sha256": "1ed0f0037a56b14abb3b4ce6706e166c489f9166f6be6b22f491a39c1f521c12",
    "reconstructed_pe_normalized_sha256": "a3164821861a56dec259e1e977cdfc6c805bffd062a2ab379f7b4a02843c7815",
    "source_commit": "34997c27d8be83d030014d215ad83b119dd49751",
    "source_patches_tree": "03fe2f75209e19945df01743b7bb42193b7ba7ab",
    "llama_cpp_commit": "254b177307b14d055bbe4c050c9c441a1e06d8a4",
    "compiler": "x86_64-w64-mingw32-g++-posix (GCC) 10-posix 20220113",
    "build_contract_sha256": "64dbf7dc6e243b5b6d6dc9a0d811aac8b66cedd911d3873be11c4c3fdca1e7f4",
}
PORT = 18095
N_PREDICT, CONTEXT, THREADS, SEED = 512, 4096, 12, 42
BUDGETS = {"1.5": 1610612736, "4": 4294967296}
ARMS = {"A": "layer", "C0": "layer_touch", "C": "kind"}
PURITY = 0.01            # R5-00 DECISION_RULES thresholds.scheduling_purity
FIDELITY_SLOWDOWN = 0.02  # R5-00 DECISION_RULES gates.G1_implementation (baseline fidelity)
READY_TIMEOUT_S, REQUEST_TIMEOUT_S, REFUSAL_TIMEOUT_S = 900, 3600, 600
# argine-ets-test.exe plan on Windows (no POSIX deferred backend): exactly these counts (validated on POSIX with
# -DETS_TEST_NO_DEFERRED, see MODEL_FREE_CONFORMANCE.json). Any other count fails G1.
WINDOWS_MODEL_FREE_EXPECTED = {"passed": 136, "failed": 0, "skipped": 3}
COUNTERS_RE = re.compile(r"argine: r5_counters (\{.*\})")
IDENTITY_RE = re.compile(r"argine: attesa expert: (\S+) \((esplicita|default)\), ARGINE_PREFETCH=([^,\s]+)")


def _load(name: str, path: Path):
    spec = importlib.util.spec_from_file_location(name, path)
    mod = importlib.util.module_from_spec(spec)            # type: ignore[arg-type]
    sys.modules[name] = mod
    spec.loader.exec_module(mod)                           # type: ignore[union-attr]
    return mod


def shutdown_mod():
    return _load("r3_04_shutdown", REPO / "tools" / "r3_04_shutdown.py")


def token_digest(tokens: List[int]) -> str:
    tel = _load("r3_telemetry_telemetry", REPO / "experiments" / "R3-01-telemetry-realization" / "r3_telemetry" / "telemetry.py")
    return tel.output_token_digest(tokens)


def pe_normalized_sha256(p: Path) -> str:
    """SHA-256 with the COFF TimeDateStamp and the optional-header CheckSum zeroed (patches/r5-ets/pe_normalized_sha256.py)."""
    return _load("pe_normalized_sha256", REPO / "patches" / "r5-ets" / "pe_normalized_sha256.py").normalized(p)


def contract_sha256(contract: Any) -> str:
    return hashlib.sha256(json.dumps(contract, sort_keys=True, separators=(",", ":")).encode()).hexdigest()


def v5_identity(p: Path, manifest: Path = V5_RECOVERY, expected: Optional[Dict[str, str]] = None) -> Dict[str, Any]:
    """Accept the v5 comparator only as (a) the historical raw SHA-256 or (b) the single frozen reconstruction.
    Fails closed on everything else, including a timestamp-only rebuild, a manifest that disagrees with the pinned values,
    or a missing manifest."""
    exp = V5_RECONSTRUCTED if expected is None else expected
    raw = sha256_file(p)
    if raw == V5_SHA256:
        return {"accepted": True, "kind": "historical", "raw_sha256": raw}
    out: Dict[str, Any] = {"accepted": False, "kind": None, "raw_sha256": raw}
    if raw != exp["reconstructed_raw_sha256"]:
        out["reason"] = "neither the historical v5 SHA-256 nor the frozen reconstructed v5 SHA-256"; return out
    try:
        m = json.loads(Path(manifest).read_text(encoding="utf-8"))
    except (OSError, ValueError) as e:
        out["reason"] = f"recovery manifest unreadable: {e}"; return out
    acc, bc = m.get("accepted_reconstruction") or {}, m.get("build_contract") or {}
    norm = pe_normalized_sha256(p)
    checks = {
        "manifest_verdict": m.get("verdict") == "R5_V5_DETERMINISTIC_RECONSTRUCTION_ACCEPTED",
        "manifest_review_accepts": (m.get("review") or {}).get("conclusion") == "ACCEPT",
        "manifest_keeps_historical_sha": (m.get("historical") or {}).get("historical_raw_sha256") == V5_SHA256,
        "raw_sha256": acc.get("reconstructed_raw_sha256") == exp["reconstructed_raw_sha256"] == raw,
        "pe_normalized_sha256": acc.get("reconstructed_pe_normalized_sha256") == exp["reconstructed_pe_normalized_sha256"] == norm,
        "source_identity": (bc.get("source_commit"), bc.get("source_patches_tree"), bc.get("llama_cpp_commit"))
                           == (exp["source_commit"], exp["source_patches_tree"], exp["llama_cpp_commit"]),
        "toolchain_identity": bc.get("compiler") == exp["compiler"],
        "build_contract": m.get("build_contract_sha256") == contract_sha256(bc) == exp["build_contract_sha256"],
    }
    out.update({"pe_normalized_sha256": norm, "checks": checks, "manifest_sha256": sha256_file(Path(manifest))})
    if all(checks.values()):
        out.update({"accepted": True, "kind": "reconstructed"})
    else:
        out["reason"] = "recovery manifest check failed: " + ", ".join(k for k, v in checks.items() if not v)
    return out


def sha256_file(p: Path) -> str:
    h = hashlib.sha256()
    with open(p, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 24), b""):
            h.update(chunk)
    return h.hexdigest()


# --------------------------------------------------------------------------- prompts (development split only)
def dev_prompts() -> List[Dict[str, str]]:
    w = json.loads((P0 / "WORKLOADS.json").read_text(encoding="utf-8"))
    dev = {p["id"]: p for p in w["development"]["prompts"]}
    ev = {p["id"] for p in w["evaluation"]["prompts"]}
    out = []
    for cat, f in (("coding", "coding"), ("natural-language", "natural-language"), ("reasoning-math", "reasoning-math"), ("structured-json", "structured-json")):
        d = json.loads((REPO / "workloads" / "v1" / f"{f}.json").read_text(encoding="utf-8"))
        for p in d["splits"]["dev"]:
            if p["id"] in dev:
                if p["id"] in ev:
                    raise RuntimeError("an evaluation id reached the development list")
                canon = json.dumps(p, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode()
                if hashlib.sha256(canon).hexdigest() != dev[p["id"]]["prompt_object_sha256"]:
                    raise RuntimeError(f"{p['id']}: prompt object differs from the frozen hash")
                out.append({"id": p["id"], "prompt": p["prompt"]})
    if sorted(x["id"] for x in out) != sorted(dev):
        raise RuntimeError("development prompts incomplete")
    return out


# --------------------------------------------------------------------------- plan
def plan() -> List[Dict[str, Any]]:
    slots: List[Dict[str, Any]] = []
    ids = [p["id"] for p in dev_prompts()]
    for b in ("1.5", "4"):
        for pid in ids:
            for arm in ("A", "C0", "C"):
                slots.append({"step": "arms", "budget": b, "prompt": pid, "arm": arm, "runtime": "r5", "traced": False})
    for pid in ids:
        for rt in ("v5", "r5", "r5", "v5"):
            slots.append({"step": "fidelity", "budget": "1.5", "prompt": pid, "arm": "A", "runtime": rt, "traced": False})
    for arm in ("A", "C"):   # B and C_I
        slots.append({"step": "instrumentation", "budget": "1.5", "prompt": "code-dev-1", "arm": arm, "runtime": "r5", "traced": True})
    for i, s in enumerate(slots):
        s["seq"] = i + 1
    return slots


def build_env(slot: Dict[str, Any], store: Path, trace: Optional[Path], base: Optional[Dict[str, str]] = None) -> Dict[str, str]:
    env = dict(os.environ if base is None else base)
    for k in ("ARGINE_RETENTION_POLICY", "ARGINE_EXPERT_PRETOUCH", "ARGINE_RESEARCH_TRACE", "ARGINE_EXPERT_WAIT", "ARGINE_EXPERT_SHARDS",
              "ARGINE_POLICY_TIMING", "ARGINE_RETENTION_TRACE"):
        env.pop(k, None)
    env.update({"ARGINE_EXPERT_STORE": str(store), "ARGINE_EXPERT_BUDGET": str(BUDGETS[slot["budget"]]),
                "ARGINE_EXPERT_READ": "asincrono", "ARGINE_EXPERT_DIRECT": "1", "ARGINE_PREFETCH": "0"})
    if slot["runtime"] == "r5":
        env["ARGINE_EXPERT_WAIT"] = ARMS[slot["arm"]]
    if trace is not None:
        env["ARGINE_RESEARCH_TRACE"] = str(trace)
    return env


def server_cmd(runtime: Path, model: Path) -> List[str]:
    return [str(runtime), "-m", str(model), "-c", str(CONTEXT), "-t", str(THREADS), "-tb", str(THREADS),
            "--host", "127.0.0.1", "--port", str(PORT)]


def _post(path: str, body: Dict[str, Any], timeout: float) -> Dict[str, Any]:
    req = urllib.request.Request(f"http://127.0.0.1:{PORT}{path}", data=json.dumps(body).encode(), headers={"Content-Type": "application/json"}, method="POST")
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.loads(r.read().decode())


def parse_stderr(text: str) -> Dict[str, Any]:
    m = COUNTERS_RE.findall(text)
    i = IDENTITY_RE.findall(text)
    return {"r5_counters": json.loads(m[-1]) if m else None, "r5_counters_lines": len(m),
            "identity": {"expert_wait": i[-1][0], "how": i[-1][1], "prefetch": i[-1][2]} if i else None}


def run_launch(slot: Dict[str, Any], runtime: Path, model: Path, store: Path, out: Path, prompt: str) -> Dict[str, Any]:
    S = shutdown_mod()
    tag = f"{slot['seq']:03d}-{slot['step']}-{slot['runtime']}-{slot['arm']}-{slot['budget']}GiB-{slot['prompt']}"
    (out / "launches").mkdir(parents=True, exist_ok=True)
    se, so = out / "launches" / f"{tag}.stderr.log", out / "launches" / f"{tag}.stdout.log"
    trace = out / "traces" / f"{tag}.trace" if slot["traced"] else None
    if trace:
        trace.parent.mkdir(parents=True, exist_ok=True)
    rec: Dict[str, Any] = {"artifact": "R5-01-HOST-LAUNCH", **slot, "error": None, "started_unix": time.time()}
    payload: Dict[str, Any] = {}
    with so.open("wb") as fo, se.open("wb") as fe:
        proc = subprocess.Popen(server_cmd(runtime, model), env=build_env(slot, store, trace), stdout=fo, stderr=fe,
                                creationflags=S.popen_creationflags("own_console_helper"))
        try:
            t_end = time.monotonic() + READY_TIMEOUT_S
            while True:
                if proc.poll() is not None:
                    raise RuntimeError(f"runtime exited early: {proc.returncode}")
                try:
                    with urllib.request.urlopen(f"http://127.0.0.1:{PORT}/health", timeout=5) as r:
                        if r.status == 200:
                            break
                except Exception:
                    if time.monotonic() > t_end:
                        raise TimeoutError("runtime readiness timeout")
                    time.sleep(1.0)
            templ = _post("/apply-template", {"messages": [{"role": "user", "content": prompt}]}, 120)["prompt"]
            rec["templated_prompt_sha256"] = hashlib.sha256(templ.encode("utf-8")).hexdigest()
            payload = _post("/completion", {"prompt": templ, "n_predict": N_PREDICT, "ignore_eos": True, "temperature": 0.0, "top_k": 1,
                                            "seed": SEED, "return_tokens": True, "stream": False, "cache_prompt": False}, REQUEST_TIMEOUT_S)
        except Exception as e:                                       # recorded, never swallowed
            rec["error"] = f"{type(e).__name__}: {e}"
        finally:
            rec["shutdown"] = S.stop_server(proc, S.GRACEFUL_TIMEOUT_S, "own_console_helper")
    text = se.read_text(encoding="utf-8", errors="replace")
    toks = payload.get("tokens")
    rec.update({"runtime_path": str(runtime), "n_tokens": len(toks) if toks is not None else None,
                "token_digest": token_digest(toks) if toks is not None else None, "server_timings": payload.get("timings"),
                **parse_stderr(text), "stderr_log": str(se), "finished_unix": time.time()})
    if trace is not None:
        rec["trace"] = reduce_trace(trace)
    return rec


def reduce_trace(trace: Path) -> Dict[str, Any]:
    """Structure only (booleans and counts); the raw trace is regenerable and is deleted after hashing."""
    if not trace.exists():
        return {"present": False}
    et = _load("ets_trace", HERE / "ets_trace.py")
    info = {"present": True, "bytes": trace.stat().st_size, "sha256": sha256_file(trace), "structure": et.structure(trace)}
    trace.unlink()
    return info


def refusal(runtime: Path, model: Path, store: Path, extra: Dict[str, str], expect: str, out: Path, name: str) -> Dict[str, Any]:
    S = shutdown_mod()
    slot = {"budget": "1.5", "runtime": "r5", "arm": "A"}
    env = build_env(slot, store, None)
    env.update(extra)
    se = out / "launches" / f"refusal-{name}.stderr.log"
    se.parent.mkdir(parents=True, exist_ok=True)
    with se.open("wb") as fe:
        proc = subprocess.Popen(server_cmd(runtime, model), env=env, stdout=subprocess.DEVNULL, stderr=fe,
                                creationflags=S.popen_creationflags("own_console_helper"))
        served = False
        t_end = time.monotonic() + REFUSAL_TIMEOUT_S
        while proc.poll() is None and time.monotonic() < t_end:
            try:
                with urllib.request.urlopen(f"http://127.0.0.1:{PORT}/health", timeout=5) as r:
                    served = r.status == 200
                    if served:
                        break
            except Exception:
                time.sleep(1.0)
        if proc.poll() is None:
            S.stop_server(proc, 120, "own_console_helper")
    text = se.read_text(encoding="utf-8", errors="replace")
    ok = (not served) and proc.returncode not in (0, None) and expect in text
    return {"name": name, "env": extra, "served": served, "returncode": proc.returncode, "expected_message": expect,
            "message_found": expect in text, "pass": ok}


def locate_v5(root: Path) -> Optional[Path]:
    for d, _dirs, files in os.walk(root):
        for f in files:
            if f.lower() == "llama-server.exe":
                p = Path(d) / f
                try:
                    if v5_identity(p)["accepted"]:
                        return p
                except OSError:
                    continue
    return None


def cmd_run(a: argparse.Namespace) -> int:
    out = Path(a.out); out.mkdir(parents=True, exist_ok=True)
    bi = json.loads(BUILD_INFO.read_text(encoding="utf-8"))
    rt_dir = BUILD_INFO.parent
    runtime, etstest = rt_dir / "llama-server.exe", rt_dir / "argine-ets-test.exe"
    ident = {"r5_runtime": str(runtime), "r5_runtime_sha256": sha256_file(runtime), "ets_test_sha256": sha256_file(etstest)}
    if ident["r5_runtime_sha256"] != bi["binaries"]["llama-server.exe"] or ident["ets_test_sha256"] != bi["binaries"]["argine-ets-test.exe"]:
        print("R5 runtime or test binary hash differs from BUILD-INFO.json: refusing to run"); return 2
    v5 = Path(a.v5_runtime) if a.v5_runtime else locate_v5(Path(a.v5_search_root))
    v5id = v5_identity(v5) if v5 is not None and v5.is_file() else {"accepted": False, "reason": "not found"}
    if not v5id["accepted"]:
        print("llama-research-v5 runtime (historical sha256 %s, or the reconstruction frozen in V5-RECOVERY.json) not accepted: %s: "
              "refusing to run" % (V5_SHA256, v5id.get("reason"))); return 2
    print(f"llama-research-v5 comparator: {v5id['kind']} identity, sha256 {v5id['raw_sha256']}")
    ident.update({"v5_runtime": str(v5), "v5_runtime_sha256": v5id["raw_sha256"], "v5_identity": v5id,
                  "v5_historical_sha256": V5_SHA256, "model": a.model, "store": a.store})
    print("hashing model and store (read-only) ..."); sys.stdout.flush()
    ident.update({"model_sha256": sha256_file(Path(a.model)), "store_sha256": sha256_file(Path(a.store))})
    (out / "IDENTITY.json").write_text(json.dumps(ident, indent=1), encoding="utf-8")
    prompts = {p["id"]: p["prompt"] for p in dev_prompts()}
    # 2. model-free conformance on the real OVERLAPPED path
    scratch = out / "ets-test-scratch"; scratch.mkdir(exist_ok=True)
    r = subprocess.run([str(etstest), str(scratch)], capture_output=True, text=True, timeout=1800)
    m = re.search(r"RESULT: (\d+) passed, (\d+) failed, (\d+) skipped", r.stdout)
    mf = {"returncode": r.returncode, "passed": int(m.group(1)) if m else None, "failed": int(m.group(2)) if m else None,
          "skipped": int(m.group(3)) if m else None, "stdout_tail": r.stdout[-4000:]}
    (out / "WINDOWS_MODEL_FREE.json").write_text(json.dumps(mf, indent=1), encoding="utf-8")
    print(f"windows model-free conformance: rc={r.returncode} {m.group(0) if m else 'no RESULT line'}")
    # 3. startup smoke and fail-closed refusals
    ver = subprocess.run([str(runtime), "--version"], capture_output=True, text=True, timeout=120)
    smoke = {"version_returncode": ver.returncode, "version_output": (ver.stdout + ver.stderr)[-2000:], "refusals": [
        refusal(runtime, Path(a.model), Path(a.store), {"ARGINE_EXPERT_WAIT": "deferred"}, "ARGINE_EXPERT_WAIT=deferred non riconosciuto", out, "invalid-value"),
        refusal(runtime, Path(a.model), Path(a.store), {"ARGINE_EXPERT_WAIT": "kind", "ARGINE_PREFETCH": "2"}, "configurazione R5 non valida", out, "kind-with-prefetch"),
        refusal(runtime, Path(a.model), Path(a.store), {"ARGINE_EXPERT_WAIT": "kind", "ARGINE_EXPERT_READ": "blocco"}, "configurazione R5 non valida", out, "kind-with-blocco"),
    ]}
    (out / "STARTUP_SMOKE.json").write_text(json.dumps(smoke, indent=1), encoding="utf-8")
    print("startup smoke:", "version rc", ver.returncode, "| refusals", [x["pass"] for x in smoke["refusals"]])
    # 4-6. launches (resumable: a slot with a record and no error is not rerun)
    log = out / "DEV_LOG.jsonl"
    done = set()
    if log.exists():
        for line in log.read_text(encoding="utf-8").splitlines():
            rec = json.loads(line)
            if not rec.get("error"):
                done.add(rec["seq"])
    for slot in plan():
        if slot["seq"] in done:
            continue
        rt = v5 if slot["runtime"] == "v5" else runtime
        print(f"[{slot['seq']:02d}/{len(plan())}] {slot['step']} {slot['runtime']} {slot['arm']} {slot['budget']} GiB {slot['prompt']}"); sys.stdout.flush()
        rec = run_launch(slot, rt, Path(a.model), Path(a.store), out, prompts[slot["prompt"]])
        with log.open("a", encoding="utf-8") as f:
            f.write(json.dumps(rec, sort_keys=True) + "\n")
        if rec.get("error"):
            print("   launch error:", rec["error"])
    return cmd_evaluate(a)


# --------------------------------------------------------------------------- evaluation (G1, mechanical)
def evaluate(records: List[Dict[str, Any]], model_free: Dict[str, Any], smoke: Dict[str, Any]) -> Dict[str, Any]:
    by = {r["seq"]: r for r in records}          # last record per slot wins
    slots = plan()
    missing = [s["seq"] for s in slots if s["seq"] not in by or by[s["seq"]].get("error")]
    recs = [by[s["seq"]] for s in slots if s["seq"] in by]
    crit: Dict[str, Any] = {}
    crit["windows_model_free_tests"] = model_free.get("returncode") == 0 and all(model_free.get(k) == v for k, v in WINDOWS_MODEL_FREE_EXPECTED.items())
    crit["startup_smoke_and_fail_closed"] = smoke.get("version_returncode") == 0 and all(x["pass"] for x in smoke.get("refusals", [])) and len(smoke.get("refusals", [])) == 3
    crit["all_launches_complete"] = not missing
    crit["all_launches_512_tokens"] = all(r.get("n_tokens") == N_PREDICT for r in recs)
    crit["graceful_shutdown"] = all((r.get("shutdown") or {}).get("mode") == "graceful" for r in recs)
    r5 = [r for r in recs if r["runtime"] == "r5"]
    crit["arm_identity_echoed"] = all((r.get("identity") or {}).get("expert_wait") == ARMS[r["arm"]] and (r.get("identity") or {}).get("prefetch") == "0" for r in r5)
    crit["one_counter_record_per_launch"] = all(r.get("r5_counters_lines") == 1 for r in r5)
    cnt = [r.get("r5_counters") or {} for r in r5]
    crit["zero_budget_violations"] = all(c.get("violations") == 0 for c in cnt)
    crit["zero_pending_reads_at_event_boundaries"] = all(c.get("orphan_pending") == 0 and c.get("pending_at_event_end") == 0 for c in cnt)
    crit["zero_fail_closed_and_drain_events"] = all(c.get("followup_mismatch") == 0 and c.get("touch_missing") == 0 and c.get("drained_at_thread_exit") == 0 for c in cnt)
    # token identity: every launch of a prompt (all arms, both budgets, v5 and R5) has the same digest
    dig: Dict[str, set] = {}
    for r in recs:
        dig.setdefault(r["prompt"], set()).add(r.get("token_digest"))
    crit["identical_tokens_all_arms_budgets_and_v5"] = all(len(v) == 1 and None not in v for v in dig.values())
    # scheduling purity on development prompts: C and C0 vs A, misses / bytes / evictions per launch, per budget
    pur = []
    for b in ("1.5", "4"):
        for pid in {r["prompt"] for r in recs}:
            arm = {r["arm"]: (r.get("r5_counters") or {}) for r in recs if r["step"] == "arms" and r["budget"] == b and r["prompt"] == pid}
            if "A" not in arm:
                continue
            for x in ("C0", "C"):
                for k in ("misses", "bytes_read", "evictions"):
                    a_, c_ = arm["A"].get(k), arm.get(x, {}).get(k)
                    if a_ is None or c_ is None:
                        pur.append(False)
                    else:
                        pur.append(abs(c_ - a_) <= PURITY * max(a_, 1))
    crit["scheduling_purity_1pct"] = bool(pur) and all(pur)
    # bookkeeping control exercised: C0 touches instead of re-acquiring, and defers nothing; C defers
    c0 = [r.get("r5_counters") or {} for r in r5 if r["arm"] == "C0"]
    cc = [r.get("r5_counters") or {} for r in r5 if r["arm"] == "C"]
    crit["bookkeeping_control_exercised"] = bool(c0) and all(c.get("touches", 0) > 0 and c.get("deferred_reads") == 0 for c in c0)
    # C0 and C must run the same bookkeeping (they differ only in wait placement): identical deterministic counters
    same_bk = []
    for b in ("1.5", "4"):
        for pid in {r["prompt"] for r in recs}:
            arm = {r["arm"]: (r.get("r5_counters") or {}) for r in recs if r["step"] == "arms" and r["budget"] == b and r["prompt"] == pid}
            if "C0" in arm and "C" in arm:
                same_bk.append(all(arm["C0"].get(k) is not None and arm["C0"].get(k) == arm["C"].get(k)
                                   for k in ("touches", "issued_followup", "first_nodes", "followup_nodes")))
            else:
                same_bk.append(False)
    crit["bookkeeping_identical_C0_C"] = bool(same_bk) and all(same_bk)
    crit["kind_arm_defers"] = bool(cc) and all(c.get("deferred_reads", 0) > 0 for c in cc)
    # baseline fidelity (R5 A vs v5, 1.5 GiB, 4 prompts x 2 launches): tokens identical (above) and not > 2% slower
    fid = [r for r in recs if r["step"] == "fidelity"]
    def ms(rs: List[Dict[str, Any]]) -> float:
        return sum(r["server_timings"]["predicted_ms"] / r["server_timings"]["predicted_n"] for r in rs) / max(len(rs), 1)
    fv5, fr5 = [r for r in fid if r["runtime"] == "v5"], [r for r in fid if r["runtime"] == "r5"]
    fidelity = None
    if len(fv5) == 8 and len(fr5) == 8 and all(r.get("server_timings") for r in fid):
        fidelity = {"v5_mean_ms_per_token": ms(fv5), "r5_A_mean_ms_per_token": ms(fr5)}
        fidelity["relative_slowdown"] = fidelity["r5_A_mean_ms_per_token"] / fidelity["v5_mean_ms_per_token"] - 1.0
    crit["baseline_fidelity_within_2pct"] = fidelity is not None and fidelity["relative_slowdown"] <= FIDELITY_SLOWDOWN
    # instrumentation completeness (structure only) on B and C_I
    ins = [r for r in recs if r["step"] == "instrumentation"]
    crit["instrumentation_complete"] = len(ins) == 2 and all((r.get("trace") or {}).get("present") and all((r["trace"]["structure"]["checks"]).values()) for r in ins)
    # trace overhead (descriptive, Phase 0 requirement): traced vs untraced SAME arm, code-dev-1, 1.5 GiB
    overhead = {}
    for arm, lab in (("A", "B_vs_A"), ("C", "C_I_vs_C")):
        t = [r for r in ins if r["arm"] == arm]
        u = [r for r in recs if r["step"] == "arms" and r["budget"] == "1.5" and r["prompt"] == "code-dev-1" and r["arm"] == arm]
        if t and u and t[0].get("server_timings") and u[0].get("server_timings"):
            overhead[lab] = (t[0]["server_timings"]["predicted_ms"] / t[0]["server_timings"]["predicted_n"]) / (u[0]["server_timings"]["predicted_ms"] / u[0]["server_timings"]["predicted_n"]) - 1.0
    verdict = "R5_G1_HOST_CONFORMANCE_PASS" if all(crit.values()) else "R5_G1_HOST_CONFORMANCE_FAIL"
    return {"artifact": "R5-01-G1-HOST-RESULT", "verdict": verdict, "criteria": crit, "missing_or_failed_slots": missing,
            "baseline_fidelity": fidelity, "trace_overhead_same_arm": overhead,
            "note": "Development prompts only. No timing contrast between C or C0 and A is computed. H (Phase 1b) is not computed."}


def cmd_evaluate(a: argparse.Namespace) -> int:
    out = Path(a.out)
    recs = [json.loads(x) for x in (out / "DEV_LOG.jsonl").read_text(encoding="utf-8").splitlines()] if (out / "DEV_LOG.jsonl").exists() else []
    mf = json.loads((out / "WINDOWS_MODEL_FREE.json").read_text()) if (out / "WINDOWS_MODEL_FREE.json").exists() else {}
    sm = json.loads((out / "STARTUP_SMOKE.json").read_text()) if (out / "STARTUP_SMOKE.json").exists() else {}
    res = evaluate(recs, mf, sm)
    ident = json.loads((out / "IDENTITY.json").read_text(encoding="utf-8")) if (out / "IDENTITY.json").exists() else {}
    res["v5_reference"] = {k: (ident.get("v5_identity") or {}).get(k) for k in ("accepted", "kind", "raw_sha256", "pe_normalized_sha256")}
    res["v5_reference"]["historical_raw_sha256"] = V5_SHA256
    (out / "G1_HOST_RESULT.json").write_text(json.dumps(res, indent=1), encoding="utf-8")
    print(json.dumps({"verdict": res["verdict"], "criteria": res["criteria"]}, indent=1))
    return 0 if res["verdict"].endswith("PASS") else 1


def main(argv: Optional[List[str]] = None) -> int:
    ap = argparse.ArgumentParser()
    sp = ap.add_subparsers(dest="cmd", required=True)
    r = sp.add_parser("run")
    r.add_argument("--model", required=True); r.add_argument("--store", required=True); r.add_argument("--out", required=True)
    g = r.add_mutually_exclusive_group(required=True); g.add_argument("--v5-runtime"); g.add_argument("--v5-search-root")
    e = sp.add_parser("evaluate"); e.add_argument("--out", required=True)
    a = ap.parse_args(argv)
    return cmd_run(a) if a.cmd == "run" else cmd_evaluate(a)


if __name__ == "__main__":
    sys.exit(main())
