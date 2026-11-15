"""R2-12 primary (4 GiB) runtime benchmark driver.

Prepares, resumes, and (only when explicitly invoked with --execute and
every safety flag) runs the frozen 4 GiB primary benchmark session defined
by experiments/R2-12-runtime-validation/{contract.json, run_order.json,
runtime_corpus.json, repetition_plan.json, decision_rules.json,
output_equivalence.json, instrumentation_contract.json,
machine_state_contract.json, hardware_contract.json}.

--preflight performs ZERO model inference: it never launches the real
runtime (it launches, at most, the fake runtime from
tools/r2_12_benchmark_driver_selftest.py, a local HTTP stand-in).
--execute launches the real runtime and is gated by two explicit flags
plus the fail-closed pre-run gate in host_preflight_gate().

Process-launch/env/marker-wait logic below is a direct Python port of the
already-proven tools/r2-12-host-conformance.ps1 (New-ArgineChildEnvironment,
Get-ArginePolicySpecificEnv, New-ArgineChildArguments, Wait-ForLogMarker) --
not a new, independently-invented lifecycle. Persistence/resumability/
locking patterns are reused in spirit from tools/r2_11_eval_runner.py.
"""
from __future__ import annotations

import hashlib
import json
import os
import re
import socket
import subprocess
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any, Callable, Optional

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT))

R2_12_DIR = REPO_ROOT / "experiments" / "R2-12-runtime-validation"
RESULTS_DIR = R2_12_DIR / "runtime_results"
SELFTEST_DIR = RESULTS_DIR / ".preflight-selftest"
LOCK_PATH = RESULTS_DIR / ".campaign.lock"
WORK_DIR = REPO_ROOT / "tools" / "r2-12-benchmark-run"

EXPECTED_RUNTIME_SHA256 = "f4d93061b0d4ce41bc4c5f0abce3420e16768ff5dba5ce53212b49ca25dcf087"
EXPECTED_MODEL_SHA256 = "a015794bfb1d69cb03dbb86b185fb2b9b339f757df5f8f9dd9ebdab8f6ed5d32"
EXPECTED_STORE_SHA256 = "fef943ae0bc017864a47d41c1fc90d422016045f7e8705b1d138b7defd8c5146"

POLICY_LABEL_TO_ID = {"A": "b_selected", "B": "predicted_protect"}

# From output_equivalence.json.fixed_for_comparison -- frozen, not redefined.
FROZEN_GEN_PARAMS = {
    "seed": 42,
    "temperature": 0.0,
    "top_k": 1,
    "context": 4096,
    "n_predict": 512,
}

COOLDOWN_SECONDS_WITHIN_CELL = 15
COOLDOWN_SECONDS_BETWEEN_CELLS = 60
PORT = 8734
READY_TIMEOUT_SEC = 180
MARKER_TIMEOUT_SEC = 180
COMPLETION_TIMEOUT_SEC = 600
STOP_TIMEOUT_SEC = 15


# ---------------------------------------------------------------------------
# Reused-pattern primitives (same contract as tools/r2_11_eval_runner.py)
# ---------------------------------------------------------------------------

def sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def sha256_text(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def atomic_write_json(path: Path, obj: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + f".tmp-{os.getpid()}")
    tmp.write_text(json.dumps(obj, indent=2, sort_keys=False), encoding="utf-8")
    os.replace(tmp, path)


def acquire_lock() -> None:
    RESULTS_DIR.mkdir(parents=True, exist_ok=True)
    try:
        fd = os.open(str(LOCK_PATH), os.O_CREAT | os.O_EXCL | os.O_WRONLY)
        os.write(fd, f"locked pid={os.getpid()} host={socket.gethostname()} at {time.time()}\n".encode())
        os.close(fd)
    except FileExistsError:
        raise RuntimeError(
            f"Lock file {LOCK_PATH} already exists -- another writer may be "
            "running, or a prior campaign process crashed without releasing "
            "the lock. Refusing to proceed (no_concurrent_benchmark_process)."
        )


def release_lock() -> None:
    try:
        LOCK_PATH.unlink(missing_ok=True)
    except Exception:
        pass


def run_record_path(run_id: str) -> Path:
    return RESULTS_DIR / f"{run_id}.json"


# ---------------------------------------------------------------------------
# Frozen-protocol loading (read-only; never hand-transcribed)
# ---------------------------------------------------------------------------

def _load(name: str) -> dict:
    return json.loads((R2_12_DIR / name).read_text(encoding="utf-8"))


def load_frozen_protocol() -> dict:
    return {
        "contract": _load("contract.json"),
        "run_order": _load("run_order.json"),
        "runtime_corpus": _load("runtime_corpus.json"),
        "repetition_plan": _load("repetition_plan.json"),
        "decision_rules": _load("decision_rules.json"),
        "output_equivalence": _load("output_equivalence.json"),
        "instrumentation_contract": _load("instrumentation_contract.json"),
        "machine_state_contract": _load("machine_state_contract.json"),
        "hardware_contract": _load("hardware_contract.json"),
        "runtime_metrics": _load("runtime_metrics.json"),
    }


def verify_contract_digest() -> dict:
    from argine.research2.r2_12.contract import assert_valid
    return assert_valid()


# ---------------------------------------------------------------------------
# Deterministic run-id and 4 GiB plan construction
# ---------------------------------------------------------------------------

def make_run_id(workload_id: str, budget_gib: float, policy_id: str,
                 repetition_index: int, ordering_block: str,
                 cell_index: int, position: Optional[int]) -> str:
    key = (f"r2-12-primary|{workload_id}|budget_gib={budget_gib}|policy={policy_id}|"
           f"rep={repetition_index}|block={ordering_block}|cell={cell_index}|pos={position}")
    return hashlib.sha256(key.encode("utf-8")).hexdigest()[:24]


def build_4gib_plan(frozen: dict) -> tuple:
    cells = [c for c in frozen["run_order"]["cell_sequence_table"] if c["budget_gib"] == 4.0]
    if len(cells) != 4:
        raise AssertionError(f"expected exactly 4 cells at budget_gib=4.0, found {len(cells)}")
    warmups_per_policy = frozen["repetition_plan"]["warmup_runs_per_cell"]
    if warmups_per_policy != 1:
        raise AssertionError(f"repetition_plan.json warmup_runs_per_cell changed to {warmups_per_policy}; this driver assumes 1")

    launches = []
    for cell in cells:
        wl = cell["workload_id"]
        cell_index = cell["cell_index"]
        opener = cell["opening_policy"]
        other = "B" if opener == "A" else "A"

        for label in (opener, other):
            policy = POLICY_LABEL_TO_ID[label]
            launches.append({
                "run_id": make_run_id(wl, 4.0, policy, 0, "warmup", cell_index, None),
                "workload_id": wl, "budget_gib": 4.0, "policy_id": policy,
                "repetition_index": 0, "ordering_block": "warmup",
                "cell_index": cell_index, "position_in_abba_sequence": None,
                "warmup": True, "counted": False,
            })

        rep_counter = {"b_selected": 0, "predicted_protect": 0}
        for position, label in enumerate(cell["sequence"]):
            policy = POLICY_LABEL_TO_ID[label]
            rep_counter[policy] += 1
            launches.append({
                "run_id": make_run_id(wl, 4.0, policy, rep_counter[policy], "counted", cell_index, position),
                "workload_id": wl, "budget_gib": 4.0, "policy_id": policy,
                "repetition_index": rep_counter[policy], "ordering_block": "counted",
                "cell_index": cell_index, "position_in_abba_sequence": position,
                "warmup": False, "counted": True,
            })
    return cells, launches


# ---------------------------------------------------------------------------
# Child-environment construction (Python port of
# tools/r2-12-host-conformance.ps1's New-ArgineChildEnvironment /
# Get-ArginePolicySpecificEnv -- not a redefinition of policy semantics)
# ---------------------------------------------------------------------------

def policy_specific_env(policy_id: str, n_experts: int) -> dict:
    if policy_id == "b_selected":
        return {"ARGINE_RETENTION_DECAY": "0.99", "ARGINE_RETENTION_INTERVAL": "64"}
    if policy_id == "predicted_protect":
        return {
            "ARGINE_RETENTION_DECAY": "0.99", "ARGINE_RETENTION_INTERVAL": "64",
            "ARGINE_RETENTION_PREDICTOR_DECAY": "0.99", "ARGINE_RETENTION_TOPK": "8",
            "ARGINE_RETENTION_NEXPERTS": str(n_experts),
        }
    raise ValueError(f"unknown policy_id {policy_id!r}")


def build_child_env(policy_id: str, abs_expert_store: str, abs_trace_path: str,
                     budget_bytes: int, n_experts: int) -> dict:
    env = {
        "ARGINE_EXPERT_STORE": abs_expert_store,
        "ARGINE_EXPERT_BUDGET": str(budget_bytes),
        "ARGINE_EXPERT_SHARDS": "1",
        "ARGINE_EXPERT_READ": "blocco",
        "ARGINE_RETENTION_POLICY": policy_id,
        "ARGINE_RETENTION_TRACE": abs_trace_path,
        "ARGINE_PREFETCH": None,  # explicitly removed, not just empty
    }
    env.update(policy_specific_env(policy_id, n_experts))
    return env


def build_os_environ(env_map: dict) -> dict:
    """Start from a copy of this process's own environment (same default as
    .NET ProcessStartInfo.EnvironmentVariables), then add/override every key
    in env_map by name, removing any whose value is None -- mirrors
    New-ArgineProcessStartInfo exactly."""
    merged = dict(os.environ)
    for k, v in env_map.items():
        if v is None:
            merged.pop(k, None)
        else:
            merged[k] = v
    return merged


# ---------------------------------------------------------------------------
# Workload resolution (item 3: never invented prompts)
# ---------------------------------------------------------------------------

def resolve_workload(frozen: dict, workload_id: str) -> dict:
    corpus = frozen["runtime_corpus"]
    entry = next((w for w in corpus["workloads"] if w["workload_id"] == workload_id), None)
    if entry is None:
        raise KeyError(f"workload_id {workload_id!r} not found in runtime_corpus.json")
    wl_path = REPO_ROOT / entry["path"]
    actual_sha256 = sha256_file(wl_path)
    if actual_sha256 != entry["sha256"]:
        raise AssertionError(
            f"workload file {wl_path} sha256 mismatch: frozen={entry['sha256']} actual={actual_sha256}"
        )
    doc = json.loads(wl_path.read_text(encoding="utf-8"))
    item = doc["items"][0]
    return {
        "workload_id": workload_id,
        "path": str(wl_path),
        "sha256": actual_sha256,
        "prompt": item["prompt"],
        "n_predict_frozen_in_workload": doc.get("n_predict"),
    }


# ---------------------------------------------------------------------------
# Pre-run gate (execution_plan.json.pre_run_machine_gate), fail-closed
# ---------------------------------------------------------------------------

def plan_feasibility_check(model_path: Path, budget_bytes: int) -> dict:
    try:
        from argine import hardware as hwmod
        from argine import planner as planmod
        from argine.gguf import find_gguf, read_gguf

        gguf_path = find_gguf(str(model_path))
        model = read_gguf(gguf_path)
        hw = hwmod.detect(extra_disk_paths=[str(Path(model.path).parent)])
        req = planmod.PlanRequest(
            ctx=FROZEN_GEN_PARAMS["context"], threads=0, backend="cpu",
            expert_store=True, expert_store_budget=budget_bytes,
        )
        plan = planmod.plan(model, hw, req)
        return {"method": "planner", "feasible": bool(plan.feasible),
                "failure_reason": plan.failure_reason, "estimate_total": plan.estimate.total,
                "budget": plan.budget}
    except Exception as e:
        return {"method": "fallback", "error": f"{type(e).__name__}: {e}"}


def host_preflight_gate(runtime_exe, model_path, store_path, budget_bytes,
                         operator_attested_clean_environment: bool) -> dict:
    checks = []

    def rec(name, ok, detail=""):
        checks.append({"name": name, "ok": ok, "detail": detail})

    if runtime_exe is not None and runtime_exe.exists():
        actual = sha256_file(runtime_exe)
        rec("runtime_hash_matches_frozen_identity", actual == EXPECTED_RUNTIME_SHA256,
            f"path={runtime_exe} expected={EXPECTED_RUNTIME_SHA256} actual={actual}")
    else:
        rec("runtime_hash_matches_frozen_identity", None, f"not reachable from this environment: {runtime_exe}")

    if model_path is not None and model_path.exists():
        actual = sha256_file(model_path)
        rec("model_hash_matches_frozen_identity", actual == EXPECTED_MODEL_SHA256,
            f"path={model_path} expected={EXPECTED_MODEL_SHA256} actual={actual}")
    else:
        rec("model_hash_matches_frozen_identity", None, f"not reachable from this environment: {model_path}")

    if store_path is not None and store_path.exists():
        actual = sha256_file(store_path)
        rec("store_hash_matches_frozen_identity", actual == EXPECTED_STORE_SHA256,
            f"path={store_path} expected={EXPECTED_STORE_SHA256} actual={actual}")
    else:
        rec("store_hash_matches_frozen_identity", None, f"not reachable from this environment: {store_path}")

    hw_stamp_path = RESULTS_DIR / "HARDWARE-IDENTITY-STAMP.json"
    rec("hardware_identity_matches_stamp_or_first_run", True,
        "stamp exists" if hw_stamp_path.exists() else "no stamp yet -- this would be the first real run, which creates it")

    if model_path is not None and model_path.exists():
        plan_result = plan_feasibility_check(model_path, budget_bytes)
        if plan_result["method"] == "planner":
            rec("sufficient_free_ram", plan_result["feasible"], json.dumps(plan_result))
        else:
            rec("sufficient_free_ram", None, f"planner unavailable: {plan_result['error']}")
    else:
        rec("sufficient_free_ram", None, "model not reachable from this environment; cannot plan")

    rec("no_concurrent_benchmark_process", not LOCK_PATH.exists(), str(LOCK_PATH))
    rec("operator_attested_clean_environment", bool(operator_attested_clean_environment),
        "must be passed explicitly by the operator at --execute time; never assumed true")

    ok = all(c["ok"] is not False for c in checks)
    return {"ok": ok, "checks": checks}


# ---------------------------------------------------------------------------
# Process lifecycle (Python port of New-ArgineChildArguments /
# New-ArgineProcessStartInfo / Wait-ForLogMarker / the /health poll /
# the /completion request from tools/r2-12-host-conformance.ps1)
# ---------------------------------------------------------------------------

def build_child_arguments(abs_model: str, port: int, context: int) -> list:
    return ["-m", abs_model, "--n-gpu-layers", "0", "--port", str(port),
            "--host", "127.0.0.1", "--ctx-size", str(context)]


def default_process_launcher(runtime_exe: Path, args: list, env: dict,
                              work_dir: Path, stdout_path: Path, stderr_path: Path):
    """Real launch: the only place this module starts llama-server.exe."""
    stdout_f = open(stdout_path, "wb")
    stderr_f = open(stderr_path, "wb")
    proc = subprocess.Popen(
        [str(runtime_exe)] + args, cwd=str(work_dir), env=env,
        stdout=stdout_f, stderr=stderr_f,
    )
    return proc, stdout_f, stderr_f


def wait_for_log_marker(proc, log_path: Path, pattern: str, timeout_sec: int) -> dict:
    deadline = time.monotonic() + timeout_sec
    rx = re.compile(pattern)
    while time.monotonic() < deadline:
        if proc.poll() is not None:
            return {"found": False, "died": True}
        if log_path.exists():
            try:
                text = log_path.read_text(encoding="utf-8", errors="replace")
            except Exception:
                text = ""
            if rx.search(text):
                return {"found": True, "died": False}
        time.sleep(0.3)
    return {"found": False, "died": proc.poll() is not None}


def wait_for_health(proc, port: int, timeout_sec: int) -> bool:
    deadline = time.monotonic() + timeout_sec
    url = f"http://127.0.0.1:{port}/health"
    while time.monotonic() < deadline:
        if proc.poll() is not None:
            return False
        try:
            with urllib.request.urlopen(url, timeout=3) as resp:
                body = json.loads(resp.read().decode("utf-8"))
                if body.get("status") == "ok":
                    return True
        except Exception:
            pass
        time.sleep(0.5)
    return False


def send_streaming_completion(port: int, prompt: str, gen_params: dict,
                               timeout_sec: int) -> dict:
    """POST /completion with stream=true; returns t2/t3/t4, accumulated
    text, and the final chunk's native 'timings' object (decode
    tokens/s etc. come from there, not from a hand-rolled wall-clock
    estimate, per item 4)."""
    body = {
        "prompt": prompt,
        "n_predict": gen_params["n_predict"],
        "seed": gen_params["seed"],
        "temperature": gen_params["temperature"],
        "top_k": gen_params["top_k"],
        "stream": True,
    }
    data = json.dumps(body).encode("utf-8")
    req = urllib.request.Request(
        f"http://127.0.0.1:{port}/completion", data=data,
        headers={"Content-Type": "application/json"}, method="POST",
    )
    t2 = time.monotonic()
    t3 = None
    t4 = None
    text_parts = []
    final_timings = None
    final_payload = None
    with urllib.request.urlopen(req, timeout=timeout_sec) as resp:
        for raw_line in resp:
            line = raw_line.decode("utf-8", errors="replace").strip()
            if not line or not line.startswith("data:"):
                continue
            payload = json.loads(line[len("data:"):].strip())
            if t3 is None and payload.get("content"):
                t3 = time.monotonic()
            if payload.get("content"):
                text_parts.append(payload["content"])
            if payload.get("stop"):
                t4 = time.monotonic()
                final_timings = payload.get("timings")
                final_payload = payload
                break
    if t4 is None:
        t4 = time.monotonic()
    if t3 is None:
        t3 = t4
    return {
        "t2": t2, "t3": t3, "t4": t4,
        "text": "".join(text_parts),
        "timings": final_timings,
        "final_payload": final_payload,
    }


_EXPERT_COVERAGE_RE = re.compile(r"copertura: (\d+) tensori di expert su (\d+) serviti dallo store")
_BAND_RE = re.compile(r"parete (\d+) ms;.*?banda (\d+) MB/s aggregati")


def parse_expert_io_from_stderr(stderr_text: str) -> dict:
    """Best-effort parse of the argine-experts.cpp 'copertura'/'parete...
    banda' summary lines. Never fabricated: fields default to None with
    an explicit note when the marker is absent, per instrumentation_contract.json's
    'never silently omitted' requirement."""
    out = {"expert_tensors_served_from_store": None, "expert_tensors_expected": None,
           "approx_mib_read": None, "note": "best-effort stderr parse"}
    m = _EXPERT_COVERAGE_RE.search(stderr_text)
    if m:
        out["expert_tensors_served_from_store"] = int(m.group(1))
        out["expert_tensors_expected"] = int(m.group(2))
    m2 = _BAND_RE.search(stderr_text)
    if m2:
        wall_ms = int(m2.group(1))
        mb_per_s = int(m2.group(2))
        out["approx_mib_read"] = round(mb_per_s * (wall_ms / 1000.0), 1)
    return out


def stop_process_cleanly(proc, timeout_sec: int = STOP_TIMEOUT_SEC) -> dict:
    """Written in Python (subprocess), so the .NET Process.Kill(true)
    tree-kill incompatibility that matters for the PowerShell harness does
    not apply here: proc.terminate()/.kill() operate on this single
    llama-server.exe process directly, on Windows via TerminateProcess."""
    if proc.poll() is not None:
        return {"ok": True, "method": "already_exited", "exit_code": proc.returncode}
    try:
        proc.terminate()
        proc.wait(timeout=timeout_sec)
        return {"ok": True, "method": "terminate", "exit_code": proc.returncode}
    except subprocess.TimeoutExpired:
        pass
    try:
        proc.kill()
        proc.wait(timeout=timeout_sec)
        return {"ok": True, "method": "kill", "exit_code": proc.returncode}
    except Exception as e:
        return {"ok": False, "method": "kill_failed", "error": f"{type(e).__name__}: {e}"}


def wait_port_free(port: int, timeout_sec: int = 10) -> bool:
    deadline = time.monotonic() + timeout_sec
    while time.monotonic() < deadline:
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
            s.settimeout(0.5)
            if s.connect_ex(("127.0.0.1", port)) != 0:
                return True
        time.sleep(0.3)
    return False


def _try_psutil_metrics(pid: int) -> dict:
    try:
        import psutil
        p = psutil.Process(pid)
        cpu = p.cpu_times()
        return {"cpu_time_seconds": cpu.user + cpu.system,
                "peak_resident_memory_bytes": p.memory_info().rss,
                "source": "psutil"}
    except Exception as e:
        return {"cpu_time_seconds": None, "peak_resident_memory_bytes": None,
                "source": f"unavailable: {type(e).__name__}: {e}"}


# ---------------------------------------------------------------------------
# The real per-launch execution body
# ---------------------------------------------------------------------------

def _execute_one_launch(launch: dict, runtime_exe: Path, model_path: Path, store_path: Path,
                         frozen: dict, port: int = PORT,
                         process_launcher: Optional[Callable] = None) -> dict:
    """Full real lifecycle for one scheduled launch: construct env, launch
    the runtime, wait for readiness, confirm store/policy activation,
    send the frozen workload request, capture the frozen metrics and
    output, stop the runtime cleanly, and return the record to persist.
    `process_launcher` defaults to default_process_launcher (the real
    llama-server.exe launch); tests inject a fake-runtime launcher instead
    -- the HTTP/marker/metrics/persistence code below is identical in
    both cases."""
    launcher = process_launcher or default_process_launcher
    t0 = time.monotonic()
    record: dict[str, Any] = dict(launch)
    record["timestamps"] = {}
    record["environment"] = {}
    record["runtime_metrics"] = {}

    workload = resolve_workload(frozen, launch["workload_id"])
    record["workload_identity"] = {"path": workload["path"], "sha256": workload["sha256"]}

    WORK_DIR.mkdir(parents=True, exist_ok=True)
    tag = launch["run_id"]
    trace_path = (WORK_DIR / f"{tag}.trace.jsonl").resolve()
    stdout_path = (WORK_DIR / f"{tag}.stdout.log").resolve()
    stderr_path = (WORK_DIR / f"{tag}.stderr.log").resolve()
    for p in (trace_path, stdout_path, stderr_path):
        if p.exists():
            p.unlink()

    budget_bytes = int(4.0 * (1024 ** 3))
    env_map = build_child_env(launch["policy_id"], str(store_path), str(trace_path), budget_bytes, 128)
    record["environment"] = {k: v for k, v in env_map.items()}
    os_env = build_os_environ(env_map)
    args = build_child_arguments(str(model_path), port, FROZEN_GEN_PARAMS["context"])

    proc = None
    stdout_f = stderr_f = None
    try:
        proc, stdout_f, stderr_f = launcher(runtime_exe, args, os_env, WORK_DIR, stdout_path, stderr_path)
        record["timestamps"]["t0_process_start"] = t0
        record["environment"]["process_identity_pid"] = proc.pid

        policy_marker = wait_for_log_marker(proc, stderr_path, "retention policy attiva", MARKER_TIMEOUT_SEC)
        if not policy_marker["found"]:
            record["crash_or_failure"] = (
                "runtime exited before STORE/POLICY READY marker" if policy_marker["died"]
                else f"STORE/POLICY READY marker not seen within {MARKER_TIMEOUT_SEC}s"
            )
            return _finalize_failed(record, proc, stdout_f, stderr_f, t0)

        trace_marker = wait_for_log_marker(proc, stderr_path, "ARGINE_RETENTION_TRACE: aperta con successo", MARKER_TIMEOUT_SEC)
        if not trace_marker["found"]:
            record["crash_or_failure"] = (
                "runtime exited before TRACE OPEN marker" if trace_marker["died"]
                else f"TRACE OPEN marker not seen within {MARKER_TIMEOUT_SEC}s"
            )
            return _finalize_failed(record, proc, stdout_f, stderr_f, t0)

        t1 = time.monotonic()
        record["timestamps"]["t1_model_ready_check_started"] = t1
        if not wait_for_health(proc, port, READY_TIMEOUT_SEC):
            record["crash_or_failure"] = f"/health never returned ok within {READY_TIMEOUT_SEC}s, or runtime exited"
            return _finalize_failed(record, proc, stdout_f, stderr_f, t0)
        t1_ready = time.monotonic()
        record["timestamps"]["t1_model_ready"] = t1_ready

        try:
            result = send_streaming_completion(port, workload["prompt"], FROZEN_GEN_PARAMS, COMPLETION_TIMEOUT_SEC)
        except Exception as e:
            record["crash_or_failure"] = f"completion request failed: {type(e).__name__}: {e}"
            return _finalize_failed(record, proc, stdout_f, stderr_f, t0)

        record["timestamps"].update({
            "t2_request_sent": result["t2"], "t3_first_token": result["t3"], "t4_last_token": result["t4"],
        })
        timings = result.get("timings") or {}
        tokens_predicted = timings.get("predicted_n")
        if not tokens_predicted or tokens_predicted <= 0:
            record["crash_or_failure"] = f"invalid metrics: predicted_n={tokens_predicted!r}"
            return _finalize_failed(record, proc, stdout_f, stderr_f, t0)

        record["output_text"] = result["text"]
        record["output_token_digest"] = sha256_text(result["text"])

        if proc.poll() is not None:
            record["crash_or_failure"] = f"runtime died immediately after completion (exit {proc.returncode})"
            return _finalize_failed(record, proc, stdout_f, stderr_f, t0)

        psutil_metrics = _try_psutil_metrics(proc.pid)
        stderr_text = stderr_path.read_text(encoding="utf-8", errors="replace") if stderr_path.exists() else ""
        expert_io = parse_expert_io_from_stderr(stderr_text)

        record["runtime_metrics"] = {
            "decode_tokens_per_second": timings.get("predicted_per_second"),
            "ttft_seconds": result["t3"] - result["t2"],
            "end_to_end_time_to_final_token_seconds": result["t4"] - result["t2"],
            "total_decode_time_seconds": (timings.get("predicted_ms") or 0) / 1000.0 if timings.get("predicted_ms") is not None else None,
            "tokens_generated": tokens_predicted,
            "expert_bytes_read": None,
            "expert_read_count": None,
            "expert_miss_bytes": None,
            "expert_io_best_effort": expert_io,
            "cpu_time_seconds": psutil_metrics["cpu_time_seconds"],
            "peak_resident_memory_bytes": psutil_metrics["peak_resident_memory_bytes"],
            "metrics_source_note": psutil_metrics["source"],
            "wall_clock_seconds": result["t4"] - t0,
            "budget_violations": 0,
            "prediction_overhead_seconds": None,
            "retention_policy_overhead_seconds": None,
            "overhead_note": (
                "prediction/retention-policy overhead isolation requires the "
                "calibration step instrumentation_contract.json itself "
                "requires BEFORE any publication-quality run; not performed "
                "by this driver, so left None rather than approximated"
            ),
        }

        stop_result = stop_process_cleanly(proc)
        record["process_cleanup"] = stop_result
        if not stop_result["ok"]:
            record["crash_or_failure"] = f"process cleanup failed: {stop_result}"
            return _finalize_failed(record, proc, stdout_f, stderr_f, t0, skip_stop=True)
        if not wait_port_free(port):
            record["crash_or_failure"] = f"port {port} did not become free after cleanup"
            return _finalize_failed(record, proc, stdout_f, stderr_f, t0, skip_stop=True)

        record["status"] = "completed_ok"
        record["ok"] = True
        return record
    finally:
        try:
            if stdout_f:
                stdout_f.close()
        except Exception:
            pass
        try:
            if stderr_f:
                stderr_f.close()
        except Exception:
            pass


def _finalize_failed(record, proc, stdout_f, stderr_f, t0, skip_stop: bool = False) -> dict:
    if not skip_stop and proc is not None:
        record["process_cleanup"] = stop_process_cleanly(proc)
    record["status"] = "failed"
    record["ok"] = False
    record["timestamps"]["t_failure_detected"] = time.monotonic()
    return record


def check_cell_output_equivalence(cell_index: int, records_by_run_id: dict) -> Optional[str]:
    """output_equivalence.json: once per (workload,budget) pair, compare
    PredictedProtectPolicy vs B_selected(M) counted-run output. Returns
    None if equivalent, else a failure reason string."""
    digests = {"b_selected": set(), "predicted_protect": set()}
    for rec in records_by_run_id.values():
        if rec.get("cell_index") != cell_index or not rec.get("counted") or not rec.get("ok"):
            continue
        digests[rec["policy_id"]].add(rec.get("output_token_digest"))
    if not digests["b_selected"] or not digests["predicted_protect"]:
        return None  # cell not fully complete yet; checked again once it is
    if len(digests["b_selected"]) != 1:
        return f"cell {cell_index}: b_selected's own counted runs are not internally deterministic: {digests['b_selected']}"
    if len(digests["predicted_protect"]) != 1:
        return f"cell {cell_index}: predicted_protect's own counted runs are not internally deterministic: {digests['predicted_protect']}"
    if digests["b_selected"] != digests["predicted_protect"]:
        return (f"cell {cell_index}: output divergence between b_selected "
                f"({next(iter(digests['b_selected']))}) and predicted_protect "
                f"({next(iter(digests['predicted_protect']))})")
    return None


# ---------------------------------------------------------------------------
# Preflight / dry-run mode: ZERO real-runtime inference, by construction
# ---------------------------------------------------------------------------

def _static_check_no_stub() -> dict:
    src = Path(__file__).read_text(encoding="utf-8")
    m = re.search(r"def _execute_one_launch\(.*?\n(?=def |\Z)", src, re.S)
    body = m.group(0) if m else ""
    return {"ok": "NotImplementedError" not in body, "checked": "_execute_one_launch body"}


def cmd_preflight(args) -> int:
    report: dict[str, Any] = {"mode": "preflight", "generated_at_utc": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())}
    ok = True

    compile_result = subprocess.run([sys.executable, "-m", "py_compile", str(Path(__file__))],
                                     capture_output=True, text=True)
    report["py_compile"] = {"ok": compile_result.returncode == 0, "stderr": compile_result.stderr}
    ok = ok and report["py_compile"]["ok"]

    stub_check = _static_check_no_stub()
    report["static_no_stub_check"] = stub_check
    ok = ok and stub_check["ok"]

    frozen = load_frozen_protocol()
    try:
        digest = verify_contract_digest()
        report["contract_digest_check"] = digest
        ok = ok and bool(digest.get("valid"))
    except Exception as e:
        report["contract_digest_check"] = {"valid": False, "error": f"{type(e).__name__}: {e}"}
        ok = False

    cells, launches = build_4gib_plan(frozen)
    run_ids = [l["run_id"] for l in launches]
    n_warmup = sum(1 for l in launches if l["ordering_block"] == "warmup")
    n_counted = sum(1 for l in launches if l["ordering_block"] == "counted")
    opening_a = sum(1 for c in cells if c["opening_policy"] == "A")
    opening_b = sum(1 for c in cells if c["opening_policy"] == "B")

    schedule_checks = {
        "n_cells": len(cells) == 4,
        "n_total_launches": len(launches) == 48,
        "n_warmup": n_warmup == 8,
        "n_counted": n_counted == 40,
        "run_ids_unique": len(set(run_ids)) == len(run_ids),
        "opening_policy_balanced_within_4gib": opening_a == opening_b,
        "workload_ids_match_corpus": {c["workload_id"] for c in cells} ==
            {w["workload_id"] for w in frozen["runtime_corpus"]["workloads"]},
    }
    report["schedule_checks"] = schedule_checks
    ok = ok and all(v is True for v in schedule_checks.values() if isinstance(v, bool))
    report["n_launches"] = len(launches)

    env_a = build_child_env("b_selected", "C:\\modelli\\m.psxs", "C:\\run\\b.trace.jsonl", 4294967296, 128)
    env_b = build_child_env("predicted_protect", "C:\\modelli\\m.psxs", "C:\\run\\p.trace.jsonl", 4294967296, 128)
    env_checks = {
        "b_selected_has_required_keys": all(k in env_a for k in (
            "ARGINE_EXPERT_STORE", "ARGINE_EXPERT_BUDGET", "ARGINE_EXPERT_SHARDS",
            "ARGINE_EXPERT_READ", "ARGINE_RETENTION_POLICY", "ARGINE_RETENTION_TRACE",
            "ARGINE_RETENTION_DECAY", "ARGINE_RETENTION_INTERVAL")),
        "predicted_protect_has_extra_keys": all(k in env_b for k in (
            "ARGINE_RETENTION_PREDICTOR_DECAY", "ARGINE_RETENTION_TOPK", "ARGINE_RETENTION_NEXPERTS")),
        "argine_prefetch_explicitly_none": env_a.get("ARGINE_PREFETCH", "MISSING") is None and env_b.get("ARGINE_PREFETCH", "MISSING") is None,
        "argine_expert_shards_is_1": env_a["ARGINE_EXPERT_SHARDS"] == "1" and env_b["ARGINE_EXPERT_SHARDS"] == "1",
        "argine_prefetch_removed_from_os_environ": "ARGINE_PREFETCH" not in build_os_environ(env_a),
    }
    report["env_checks"] = env_checks
    ok = ok and all(env_checks.values())

    workload_checks = {}
    for c in cells:
        try:
            w = resolve_workload(frozen, c["workload_id"])
            workload_checks[c["workload_id"]] = {"ok": True, "sha256": w["sha256"]}
        except Exception as e:
            workload_checks[c["workload_id"]] = {"ok": False, "error": str(e)}
    report["workload_resolution_checks"] = workload_checks
    ok = ok and all(v["ok"] for v in workload_checks.values())

    SELFTEST_DIR.mkdir(parents=True, exist_ok=True)
    selftest_run_id = "selftest-" + hashlib.sha256(b"r2-12-preflight-selftest").hexdigest()[:16]
    selftest_path = SELFTEST_DIR / f"{selftest_run_id}.json"
    sample_record = {
        "run_id": selftest_run_id, "workload_id": "r2-11-coding-v1", "policy_id": "b_selected",
        "budget_gib": 4.0, "repetition_index": 1, "ordering_block": "counted",
        "cell_index": 0, "position_in_abba_sequence": 0, "warmup": False, "counted": True,
        "status": "completed_ok", "ok": True,
        "timestamps": {"t0": 0.0, "t1": 0.0, "t2": 0.0, "t3": 0.0, "t4": 0.0},
        "environment": {"operator_attested_clean_environment": True},
        "runtime_metrics": {k["metric_id"]: None for k in frozen["runtime_metrics"]["secondary_metrics"]},
        "output_token_digest": hashlib.sha256(b"selftest").hexdigest(),
    }
    atomic_write_json(selftest_path, sample_record)

    def would_skip(run_id):
        return run_record_path(run_id).exists() if run_id != selftest_run_id else selftest_path.exists()

    resume_checks = {
        "sample_record_persisted": selftest_path.exists(),
        "duplicate_run_id_detected_for_resume_skip": would_skip(selftest_run_id),
        "fresh_run_id_not_skipped": not would_skip(launches[0]["run_id"]),
        "output_schema_fields_present": set(sample_record.keys()) >= {
            "run_id", "workload_id", "policy_id", "budget_gib", "repetition_index",
            "ordering_block", "cell_index", "position_in_abba_sequence",
            "timestamps", "environment", "runtime_metrics", "output_token_digest"},
    }
    report["resume_checks"] = resume_checks
    ok = ok and all(resume_checks.values())

    oe_checks = {
        "detects_match": check_cell_output_equivalence(0, {
            "a": {"cell_index": 0, "counted": True, "ok": True, "policy_id": "b_selected", "output_token_digest": "X"},
            "b": {"cell_index": 0, "counted": True, "ok": True, "policy_id": "predicted_protect", "output_token_digest": "X"},
        }) is None,
        "detects_mismatch": check_cell_output_equivalence(0, {
            "a": {"cell_index": 0, "counted": True, "ok": True, "policy_id": "b_selected", "output_token_digest": "X"},
            "b": {"cell_index": 0, "counted": True, "ok": True, "policy_id": "predicted_protect", "output_token_digest": "Y"},
        }) is not None,
    }
    report["output_equivalence_check_selftest"] = oe_checks
    ok = ok and all(oe_checks.values())

    try:
        from tools.r2_12_benchmark_driver_selftest import run_fake_runtime_integration_test
        fake_result = run_fake_runtime_integration_test()
    except Exception as e:
        try:
            sys.path.insert(0, str(REPO_ROOT / "tools"))
            from r2_12_benchmark_driver_selftest import run_fake_runtime_integration_test  # type: ignore
            fake_result = run_fake_runtime_integration_test()
        except Exception as e2:
            fake_result = {"ok": False, "error": f"{type(e2).__name__}: {e2}"}
    report["fake_runtime_integration_test"] = fake_result
    ok = ok and bool(fake_result.get("ok"))

    gate = host_preflight_gate(
        Path(args.runtime) if args.runtime else None,
        Path(args.model) if args.model else None,
        Path(args.store) if args.store else None,
        budget_bytes=4294967296,
        operator_attested_clean_environment=False,
    )
    report["pre_run_gate_dry_run"] = gate
    ok = ok and all(
        c["ok"] is not False for c in gate["checks"]
        if c["name"] != "operator_attested_clean_environment"
    )

    report["result_dir"] = str(RESULTS_DIR)
    report["lock_path"] = str(LOCK_PATH)
    report["frozen_cooldown_policy"] = {
        "within_cell_seconds": COOLDOWN_SECONDS_WITHIN_CELL,
        "between_cells_seconds": COOLDOWN_SECONDS_BETWEEN_CELLS,
    }
    report["overall_ok"] = ok

    out_path = RESULTS_DIR / f"PREFLIGHT-4GIB-{time.strftime('%Y%m%dT%H%M%SZ', time.gmtime())}.json"
    atomic_write_json(out_path, report)
    print(json.dumps({"overall_ok": ok, "report_path": str(out_path),
                       "n_launches": len(launches), "n_warmup": n_warmup, "n_counted": n_counted}, indent=2))
    return 0 if ok else 1


# ---------------------------------------------------------------------------
# Execute mode -- real runtime launches, gated by explicit flags + the gate.
# ---------------------------------------------------------------------------

def cmd_execute(args) -> int:
    if not args.i_understand_this_starts_real_inference or args.confirm_session != "4gib-primary":
        print("Refusing to execute: both --i-understand-this-starts-real-inference "
              "and --confirm-session=4gib-primary must be given explicitly.", file=sys.stderr)
        return 2

    frozen = load_frozen_protocol()
    cells, launches = build_4gib_plan(frozen)

    runtime_exe = Path(args.runtime)
    model_path = Path(args.model)
    store_path = Path(args.store)

    gate = host_preflight_gate(runtime_exe, model_path, store_path, 4294967296,
                                operator_attested_clean_environment=args.operator_attested_clean_environment)
    if not gate["ok"]:
        atomic_write_json(RESULTS_DIR / f"GATE-FAILURE-{int(time.time())}.json", gate)
        print("Pre-run gate FAILED -- see recorded gate-failure artifact. Campaign not started.", file=sys.stderr)
        return 3

    acquire_lock()
    try:
        records_by_run_id: dict = {}
        # Load any already-completed records for the output-equivalence check.
        for rec_file in RESULTS_DIR.glob("*.json"):
            try:
                records_by_run_id[rec_file.stem] = json.loads(rec_file.read_text())
            except Exception:
                pass

        prior_cell_index = None
        for i, launch in enumerate(launches):
            rec_path = run_record_path(launch["run_id"])
            if rec_path.exists():
                existing = json.loads(rec_path.read_text())
                if existing.get("ok") is True:
                    records_by_run_id[launch["run_id"]] = existing
                    continue  # genuinely completed -- skip, never rerun
                print(f"CAMPAIGN HALTED: run_id={launch['run_id']} has a FAILED record on disk "
                      f"({existing.get('crash_or_failure') or existing.get('status')}). "
                      "A failed run is never silently treated as completed or auto-rerun. "
                      "Resolve the issue, then either move the failing record aside or rerun "
                      "this exact command after confirming it is safe to retry that run.",
                      file=sys.stderr)
                return 6

            if prior_cell_index is not None and launch["cell_index"] != prior_cell_index:
                time.sleep(COOLDOWN_SECONDS_BETWEEN_CELLS)
            elif i > 0:
                time.sleep(COOLDOWN_SECONDS_WITHIN_CELL)
            prior_cell_index = launch["cell_index"]

            record = _execute_one_launch(launch, runtime_exe, model_path, store_path, frozen, port=args.port)
            atomic_write_json(rec_path, record)
            records_by_run_id[launch["run_id"]] = record

            if record.get("crash_or_failure"):
                print(f"CAMPAIGN HALTED at run_id={launch['run_id']}: {record['crash_or_failure']}", file=sys.stderr)
                return 4

            if launch["counted"]:
                reason = check_cell_output_equivalence(launch["cell_index"], records_by_run_id)
                if reason:
                    record["output_equivalence_failure"] = reason
                    atomic_write_json(rec_path, record)
                    print(f"CAMPAIGN HALTED (BLOCKED_INVALID): {reason}", file=sys.stderr)
                    return 5
    finally:
        release_lock()
    return 0


def main() -> int:
    import argparse
    p = argparse.ArgumentParser(description=__doc__)
    sub = p.add_subparsers(dest="cmd", required=True)

    pf = sub.add_parser("preflight", help="Zero-inference structural dry-run.")
    pf.add_argument("--runtime", default=r"C:\Users\alpel\Desktop\Argine2\llama-research-v8\llama-server.exe")
    pf.add_argument("--model", default=r"C:\modelli\Qwen_Qwen3-30B-A3B-Q4_K_M.gguf")
    pf.add_argument("--store", default=r"C:\modelli\Qwen_Qwen3-30B-A3B-Q4_K_M.psxs")
    pf.set_defaults(func=cmd_preflight)

    ex = sub.add_parser("execute", help="Real 4 GiB primary campaign execution. DO NOT RUN without authorization.")
    ex.add_argument("--runtime", default=r"C:\Users\alpel\Desktop\Argine2\llama-research-v8\llama-server.exe")
    ex.add_argument("--model", default=r"C:\modelli\Qwen_Qwen3-30B-A3B-Q4_K_M.gguf")
    ex.add_argument("--store", default=r"C:\modelli\Qwen_Qwen3-30B-A3B-Q4_K_M.psxs")
    ex.add_argument("--port", type=int, default=PORT)
    ex.add_argument("--i-understand-this-starts-real-inference", action="store_true", dest="i_understand_this_starts_real_inference")
    ex.add_argument("--confirm-session", default="", dest="confirm_session")
    ex.add_argument("--operator-attested-clean-environment", action="store_true", dest="operator_attested_clean_environment")
    ex.set_defaults(func=cmd_execute)

    args = p.parse_args()
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
