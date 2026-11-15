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
import shutil
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


# Frozen budgets (contract.json/run_order.json) -- not a general-purpose
# GiB converter; only these three budgets are part of the frozen protocol.
BUDGET_SESSION_IDS = {
    4.0: "4gib-primary",
    1.5: "1.5gib-stress",
    8.0: "8gib-control",
}

BUDGET_FILENAME_LABELS = {
    4.0: "4GIB",
    1.5: "1_5GIB",
    8.0: "8GIB",
}

# Single shared source of truth for every --budget-gib argparse choices=
# list (preflight/execute/diagnose) -- never redefine this tuple literal
# at a second call site.
FROZEN_BUDGET_CHOICES = tuple(sorted(BUDGET_SESSION_IDS))


def budget_gib_to_bytes(budget_gib: float) -> int:
    """Exact byte count for a frozen budget_gib, matching
    contract.json.frozen_baseline.mapping_by_budget_bytes's keys precisely
    (1.5->1610612736, 4.0->4294967296, 8.0->8589934592). Refuses any
    budget_gib outside the frozen set rather than silently computing one."""
    if budget_gib not in BUDGET_SESSION_IDS:
        raise ValueError(f"budget_gib={budget_gib!r} is not one of the frozen R2-12 budgets {sorted(BUDGET_SESSION_IDS)}")
    return int(round(budget_gib * (1024 ** 3)))


def resolve_baseline_params_for_budget(frozen: dict, budget_bytes: int) -> dict:
    """Reads contract.json.frozen_baseline.mapping_by_budget_bytes for the
    exact aged_frequency decay_factor/aging_interval_steps AT THIS BUDGET --
    per item 5, never inferred or copied blindly from another budget."""
    mapping = frozen["contract"]["frozen_baseline"]["mapping_by_budget_bytes"]
    key = str(budget_bytes)
    if key not in mapping:
        raise KeyError(f"contract.json.frozen_baseline.mapping_by_budget_bytes has no entry for budget_bytes={budget_bytes}")
    params = mapping[key]["params"]
    return {"decay_factor": params["decay_factor"], "aging_interval_steps": params["aging_interval_steps"]}


def resolve_predictor_decay_factor(frozen: dict) -> float:
    """The frozen predictor's own decay (contract.json.frozen_predictor) --
    identical at every budget; verified from the frozen contract, not
    copied from the 4 GiB baseline mapping."""
    return frozen["contract"]["frozen_predictor"]["decay_factor"]


def build_budget_plan(frozen: dict, budget_gib: float) -> tuple:
    """Budget-aware generalization of the original 4-GiB-only plan builder.
    budget_gib=4.0 reproduces the exact 4 GiB plan byte-for-byte (only the
    literal 4.0 is replaced by the parameter); 1.5/8.0 come from the SAME
    frozen run_order.json.cell_sequence_table -- no separately authored
    schedule exists or is derived here."""
    if budget_gib not in BUDGET_SESSION_IDS:
        raise ValueError(f"budget_gib={budget_gib!r} is not one of the frozen R2-12 budgets {sorted(BUDGET_SESSION_IDS)}")
    cells = [c for c in frozen["run_order"]["cell_sequence_table"] if c["budget_gib"] == budget_gib]
    if len(cells) != 4:
        raise AssertionError(f"expected exactly 4 cells at budget_gib={budget_gib}, found {len(cells)}")
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
                "run_id": make_run_id(wl, budget_gib, policy, 0, "warmup", cell_index, None),
                "workload_id": wl, "budget_gib": budget_gib, "policy_id": policy,
                "repetition_index": 0, "ordering_block": "warmup",
                "cell_index": cell_index, "position_in_abba_sequence": None,
                "warmup": True, "counted": False,
            })

        rep_counter = {"b_selected": 0, "predicted_protect": 0}
        for position, label in enumerate(cell["sequence"]):
            policy = POLICY_LABEL_TO_ID[label]
            rep_counter[policy] += 1
            launches.append({
                "run_id": make_run_id(wl, budget_gib, policy, rep_counter[policy], "counted", cell_index, position),
                "workload_id": wl, "budget_gib": budget_gib, "policy_id": policy,
                "repetition_index": rep_counter[policy], "ordering_block": "counted",
                "cell_index": cell_index, "position_in_abba_sequence": position,
                "warmup": False, "counted": True,
            })
    return cells, launches


def build_4gib_plan(frozen: dict) -> tuple:
    """Preserved for exact backward compatibility with existing callers
    (selftest, primary-analysis script): identical to
    build_budget_plan(frozen, 4.0). Do not remove -- existing canonical
    4 GiB run_ids/records depend on this code path producing the exact
    same plan it always has."""
    return build_budget_plan(frozen, 4.0)


# ---------------------------------------------------------------------------
# Child-environment construction (Python port of
# tools/r2-12-host-conformance.ps1's New-ArgineChildEnvironment /
# Get-ArginePolicySpecificEnv -- not a redefinition of policy semantics)
# ---------------------------------------------------------------------------

def policy_specific_env(policy_id: str, n_experts: int,
                         decay_factor: float, aging_interval_steps: int,
                         predictor_decay_factor: float) -> dict:
    """Budget-aware generalization (item 5). decay_factor/aging_interval_steps
    are the frozen aged_frequency baseline params FOR THE CALLER'S BUDGET
    (contract.json.frozen_baseline.mapping_by_budget_bytes), required by
    patches/argine-retention-policy.cpp's parse_retention_config_from_env()
    UNCONDITIONALLY -- for predicted_protect too, since PredictedProtectPolicy's
    predictor sits on top of the SAME underlying aged_frequency mechanics,
    modulated by the separate, budget-independent predictor_decay_factor/
    top_k/n_experts fields. At decay_factor=0.99/aging_interval_steps=64
    (the 4 GiB values) this reproduces the previous hardcoded env byte-for-byte."""
    base = {
        "ARGINE_RETENTION_DECAY": str(decay_factor),
        "ARGINE_RETENTION_INTERVAL": str(aging_interval_steps),
    }
    if policy_id == "b_selected":
        return base
    if policy_id == "predicted_protect":
        return {
            **base,
            "ARGINE_RETENTION_PREDICTOR_DECAY": str(predictor_decay_factor),
            "ARGINE_RETENTION_TOPK": "8",
            "ARGINE_RETENTION_NEXPERTS": str(n_experts),
        }
    raise ValueError(f"unknown policy_id {policy_id!r}")


# Per instrumentation_contract.json ("overhead_must_be_measured_or_bounded" /
# "minimal_instrumentation_mode_requirement"): heavy structured
# ARGINE_RETENTION_TRACE logging is required for conformance/scientific
# replay, NOT for the performance/timing path. The frozen
# fields_recorded_per_run (expert_store_reads, expert_store_bytes_read,
# decode timings, ...) are all available from the runtime's unconditional
# stderr diagnostics (patches/argine-experts.cpp Contesto::~Contesto(), the
# "banda"/"copertura" block, explicitly printed "SENZA dipendere da"
# ARGINE_RETENTION_TRACE) and from the native /completion SSE "timings"
# object -- neither needs the trace file. Retention-policy DECISION
# behavior itself is unaffected by the trace flag (patches/argine-store.cpp:
# "Se ARGINE_RETENTION_TRACE non e' impostato ... nessun costo, nessuna
# differenza di comportamento"). enable_trace therefore defaults to False
# here: the performance/counted campaign path never sets it, and only an
# explicit conformance-style caller passes enable_trace=True.
def build_child_env(policy_id: str, abs_expert_store: str, abs_trace_path: str,
                     budget_bytes: int, n_experts: int,
                     decay_factor: float, aging_interval_steps: int,
                     predictor_decay_factor: float,
                     enable_trace: bool = False) -> dict:
    env = {
        "ARGINE_EXPERT_STORE": abs_expert_store,
        "ARGINE_EXPERT_BUDGET": str(budget_bytes),
        "ARGINE_EXPERT_SHARDS": "1",
        "ARGINE_EXPERT_READ": "blocco",
        "ARGINE_RETENTION_POLICY": policy_id,
        "ARGINE_PREFETCH": None,  # explicitly removed, not just empty
    }
    if enable_trace:
        env["ARGINE_RETENTION_TRACE"] = abs_trace_path
    env.update(policy_specific_env(policy_id, n_experts, decay_factor, aging_interval_steps, predictor_decay_factor))
    return env


def assert_trace_absent_for_performance(env_map: dict) -> None:
    """Hard assertion for the canonical performance/counted execution path:
    ARGINE_RETENTION_TRACE must never be present. Raises AssertionError
    (fail-closed) rather than silently continuing if it is."""
    if "ARGINE_RETENTION_TRACE" in env_map:
        raise AssertionError(
            "ARGINE_RETENTION_TRACE present in a canonical performance-path "
            "child environment -- this is the exact defect that caused the "
            "storage/time explosion on the 2026-11-15 run and must never "
            "recur on the canonical path.")


# Confirmed host defect (2026-11-15): a prior session's shell had exported
# ARGINE_RETENTION_TRACE=C:\modelli\conformance\v5\predicted_protect.jsonl.
# build_os_environ started from dict(os.environ) and only popped keys
# EXPLICITLY present in env_map with value None -- since enable_trace=False
# simply OMITS ARGINE_RETENTION_TRACE from env_map (never sets it to None),
# the stale inherited value passed straight through into the child process,
# and llama-server wrote a 14.389 GiB trace file despite the driver
# reporting trace_enabled=false. Fix: strip every inherited ARGINE_*/
# PICOSTAR_* variable from the base copy BEFORE layering env_map on top,
# the same allowlist discipline already proven in tools/exp022_run.py's
# base_env()/ALLOWED_ARGINE_ENV and tools/exp024_run.py (which refuse to
# start if the parent carries any such variable). This kills the whole
# class of inherited-ARGINE-state leaks, not just this one variable.
KNOWN_TRACE_OUTPUT_ENV_VARS = {"ARGINE_RETENTION_TRACE", "ARGINE_RESEARCH_TRACE"}


def strip_inherited_argine_env(base: dict) -> dict:
    """Remove every ARGINE_*/PICOSTAR_* key from `base` (a copy of
    os.environ or an equivalent inherited-environment representation).
    Nothing in this driver's own env_map construction depends on any
    inherited ARGINE_*/PICOSTAR_* value, so stripping unconditionally is
    safe; build_child_env's own keys are layered back on top afterward."""
    return {k: v for k, v in base.items()
            if not (k.upper().startswith("ARGINE_") or k.upper().startswith("PICOSTAR_"))}


def build_os_environ(env_map: dict) -> dict:
    """Start from a copy of this process's own environment (same default as
    .NET ProcessStartInfo.EnvironmentVariables), with every inherited
    ARGINE_*/PICOSTAR_* variable stripped first (see
    strip_inherited_argine_env -- this is the fix for the confirmed
    2026-11-15 inherited-trace-variable leak), then add/override every key
    in env_map by name, removing any whose value is None -- mirrors
    New-ArgineProcessStartInfo exactly, plus the sanitization step."""
    merged = strip_inherited_argine_env(dict(os.environ))
    for k, v in env_map.items():
        if v is None:
            merged.pop(k, None)
        else:
            merged[k] = v
    return merged


def trace_output_variables_present(env: dict) -> list:
    """List of KNOWN_TRACE_OUTPUT_ENV_VARS actually present in `env`, for
    machine-readable diagnostics (diagnose/preflight/execute reports)."""
    return sorted(k for k in KNOWN_TRACE_OUTPUT_ENV_VARS if k in env)


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

MIN_FREE_DISK_BYTES_CAMPAIGN_START = 5 * (1024 ** 3)  # 5 GiB fail-closed margin


def check_disk_space_margin(path: Path, min_free_bytes: Optional[int] = None) -> dict:
    """Fail-closed disk-space preflight (item 9). Stops BEFORE any model
    launch if free space is already below the margin. On the fixed
    performance path (no per-launch multi-GiB trace) this margin is far
    more than any single launch should ever need; it exists as a guard
    against a future regression, not because the fixed path is expected to
    use it.

    min_free_bytes defaults to None rather than directly defaulting to
    MIN_FREE_DISK_BYTES_CAMPAIGN_START: a plain `= MIN_FREE_DISK_BYTES_CAMPAIGN_START`
    default is bound ONCE at function-definition time, so a test (or any
    caller) that reassigns the module-level constant afterward would be
    silently ignored by every call that doesn't pass an explicit override.
    Reading the module constant inside the function body instead makes
    that override actually take effect, which is also what the regression
    test for the disk-space-FAIL case depends on."""
    if min_free_bytes is None:
        min_free_bytes = MIN_FREE_DISK_BYTES_CAMPAIGN_START
    path = Path(path)
    path.mkdir(parents=True, exist_ok=True)
    usage = shutil.disk_usage(path)
    return {
        "path": str(path), "free_bytes": usage.free, "total_bytes": usage.total,
        "min_free_bytes_required": min_free_bytes,
        "ok": usage.free >= min_free_bytes,
    }


def _execute_one_launch(launch: dict, runtime_exe: Path, model_path: Path, store_path: Path,
                         frozen: dict, port: int = PORT,
                         process_launcher: Optional[Callable] = None,
                         enable_trace: bool = False,
                         diagnostic_only: bool = False,
                         max_artifact_bytes: Optional[int] = None) -> dict:
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

    budget_bytes = budget_gib_to_bytes(launch["budget_gib"])
    baseline_params = resolve_baseline_params_for_budget(frozen, budget_bytes)
    predictor_decay_factor = resolve_predictor_decay_factor(frozen)
    env_map = build_child_env(launch["policy_id"], str(store_path), str(trace_path), budget_bytes, 128,
                               baseline_params["decay_factor"], baseline_params["aging_interval_steps"],
                               predictor_decay_factor, enable_trace=enable_trace)
    if not enable_trace:
        # Hard, fail-closed assertion: this is the exact defect (unconditional
        # ARGINE_RETENTION_TRACE) that produced the 2026-11-15 14.4 GiB/launch,
        # ~10-11 min/launch explosion. Never allow it to silently recur.
        assert_trace_absent_for_performance(env_map)
    record["environment"] = {k: v for k, v in env_map.items()}
    record["canonical"] = (not diagnostic_only) and (not enable_trace)
    record["diagnostic_only"] = diagnostic_only
    record["trace_enabled"] = enable_trace
    os_env = build_os_environ(env_map)
    args = build_child_arguments(str(model_path), port, FROZEN_GEN_PARAMS["context"])

    # --- Pre-Popen fail-closed assertion (confirmed host defect fix) -------
    # env_map alone never contained the stale inherited ARGINE_RETENTION_TRACE
    # (it simply omitted the key); the leak happened in os_env, AFTER merging
    # with the parent process's environment. This is the actual environment
    # handed to subprocess.Popen -- checking anything earlier would have
    # missed the exact bug just confirmed on the real host. FAILS BEFORE
    # the model is launched; never sanitizes silently at a later stage.
    present = trace_output_variables_present(os_env)
    retention_trace_env_present = "ARGINE_RETENTION_TRACE" in os_env
    record["pre_popen_env_check"] = {
        "retention_trace_env_present": retention_trace_env_present,
        "trace_output_variables_present": present,
    }
    if not enable_trace and present:
        record["crash_or_failure"] = (
            "pre-Popen fail-closed assertion: trace-output environment "
            f"variable(s) present despite enable_trace=False: {present}. "
            "Model was NOT launched. This is the exact class of inherited-"
            "process-environment leak confirmed on the real host on "
            "2026-11-15 (ARGINE_RETENTION_TRACE inherited from a prior "
            "shell session)."
        )
        record["status"] = "failed_pre_popen_env_check"
        record["ok"] = False
        return record
    if enable_trace and "ARGINE_RETENTION_TRACE" not in os_env:
        record["crash_or_failure"] = (
            "pre-Popen fail-closed assertion: enable_trace=True but "
            "ARGINE_RETENTION_TRACE is absent from the child environment "
            "(explicit path was not applied)."
        )
        record["status"] = "failed_pre_popen_env_check"
        record["ok"] = False
        return record

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

        if enable_trace:
            trace_marker = wait_for_log_marker(proc, stderr_path, "ARGINE_RETENTION_TRACE: aperta con successo", MARKER_TIMEOUT_SEC)
            if not trace_marker["found"]:
                record["crash_or_failure"] = (
                    "runtime exited before TRACE OPEN marker" if trace_marker["died"]
                    else f"TRACE OPEN marker not seen within {MARKER_TIMEOUT_SEC}s"
                )
                return _finalize_failed(record, proc, stdout_f, stderr_f, t0)
        # enable_trace is False on the canonical performance path: ARGINE_RETENTION_TRACE
        # is absent from the child env (asserted above), so the runtime never opens a
        # trace file and never emits the TRACE OPEN marker -- waiting for it here would
        # just be a guaranteed MARKER_TIMEOUT_SEC stall on every single launch. Only
        # STORE/POLICY READY (already awaited above) and /health (awaited below) gate
        # the performance path.

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

        # Per-launch unexpected-artifact-growth guard (item 9). IMPORTANT
        # SEMANTICS (corrected 2026-11-15 after the host confirmed llama-server
        # wrote 14.389 GiB to an EXTERNAL, hardcoded conformance path --
        # C:\modelli\conformance\v5\predicted_protect.jsonl -- that is not
        # under WORK_DIR and this guard never saw): this field measures ONLY
        # the three files THIS driver itself manages and names
        # (trace_path/stdout_path/stderr_path under WORK_DIR). It is NOT a
        # measurement of total filesystem writes by the child process, and
        # must never be read as such. The real defense against an external,
        # unmanaged trace path is the pre-Popen fail-closed assertion above
        # (which now prevents ARGINE_RETENTION_TRACE from ever reaching the
        # child with a stale/inherited value in the first place) -- this
        # guard is a secondary safety net for the driver's OWN artifacts only.
        # external_trace_output_detected is left None rather than approximated:
        # detecting an arbitrary external path would require either an
        # expensive full-disk scan (explicitly avoided per item 7) or
        # hardcoding host-specific paths like C:\modelli\conformance\v5\,
        # which this driver does not do.
        driver_managed_artifact_bytes = sum(
            fp.stat().st_size for fp in (trace_path, stdout_path, stderr_path) if fp.exists()
        )
        record["driver_managed_artifact_bytes_written"] = driver_managed_artifact_bytes
        record["external_trace_output_detected"] = None
        record["external_trace_output_note"] = (
            "Not scanned (avoids expensive full-disk scanning per-launch). "
            "The pre-Popen fail-closed assertion above is the actual defense "
            "against an external/inherited trace path being used by the "
            "child process; this field documents the limit rather than "
            "silently implying full coverage."
        )
        ceiling = max_artifact_bytes if max_artifact_bytes is not None else (
            200 * 1024 * 1024 if not enable_trace else None
        )
        if ceiling is not None and driver_managed_artifact_bytes > ceiling:
            record["crash_or_failure"] = (
                f"artifact-growth guard triggered: {driver_managed_artifact_bytes} "
                f"driver-managed bytes written (trace+stdout+stderr under WORK_DIR) "
                f"exceeds ceiling {ceiling} bytes for this launch. Nothing was "
                "truncated; the run artifacts are left on disk for inspection. "
                "This guard covers only driver-managed files -- see "
                "external_trace_output_note."
            )
            record["status"] = "failed_artifact_growth_guard"
            record["ok"] = False
            return record

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
    budget_gib = getattr(args, "budget_gib", 4.0)
    if budget_gib not in BUDGET_SESSION_IDS:
        print(f"Refusing: budget_gib={budget_gib!r} is not one of the frozen R2-12 budgets "
              f"{sorted(BUDGET_SESSION_IDS)}.", file=sys.stderr)
        return 2
    budget_bytes = budget_gib_to_bytes(budget_gib)
    target_session_id = BUDGET_SESSION_IDS[budget_gib]
    filename_label = BUDGET_FILENAME_LABELS[budget_gib]

    report: dict[str, Any] = {
        "mode": "preflight", "generated_at_utc": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "target_budget_gib": budget_gib, "target_budget_bytes": budget_bytes,
        "target_session_id": target_session_id,
        "runtime_identity_expected_sha256": EXPECTED_RUNTIME_SHA256,
        "model_identity_expected_sha256": EXPECTED_MODEL_SHA256,
        "store_identity_expected_sha256": EXPECTED_STORE_SHA256,
    }
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

    cells, launches = build_budget_plan(frozen, budget_gib)
    baseline_params = resolve_baseline_params_for_budget(frozen, budget_bytes)
    predictor_decay_factor = resolve_predictor_decay_factor(frozen)
    run_ids = [l["run_id"] for l in launches]
    n_warmup = sum(1 for l in launches if l["ordering_block"] == "warmup")
    n_counted = sum(1 for l in launches if l["ordering_block"] == "counted")
    opening_a = sum(1 for c in cells if c["opening_policy"] == "A")
    opening_b = sum(1 for c in cells if c["opening_policy"] == "B")
    n_a_counted = sum(1 for l in launches if l["counted"] and l["policy_id"] == "b_selected")
    n_b_counted = sum(1 for l in launches if l["counted"] and l["policy_id"] == "predicted_protect")

    schedule_checks = {
        "n_cells": len(cells) == 4,
        "n_total_launches": len(launches) == 48,
        "n_warmup": n_warmup == 8,
        "n_counted": n_counted == 40,
        "run_ids_unique": len(set(run_ids)) == len(run_ids),
        "opening_policy_balanced_within_budget": opening_a == opening_b,
        "policy_counted_runs_balanced": n_a_counted == n_b_counted == 20,
        "workload_ids_match_corpus": {c["workload_id"] for c in cells} ==
            {w["workload_id"] for w in frozen["runtime_corpus"]["workloads"]},
        "baseline_params_resolved_from_frozen_contract": set(baseline_params.keys()) == {"decay_factor", "aging_interval_steps"},
    }
    report["schedule_checks"] = schedule_checks
    ok = ok and all(v is True for v in schedule_checks.values() if isinstance(v, bool))
    report["n_launches"] = len(launches)
    report["expected_launches"] = {
        "physical_launches": len(launches), "warmups": n_warmup, "counted": n_counted,
        "policy_counts": {"b_selected_counted": n_a_counted, "predicted_protect_counted": n_b_counted},
    }
    report["resolved_policy_params"] = {
        "budget_gib": budget_gib, "budget_bytes": budget_bytes,
        "baseline_decay_factor": baseline_params["decay_factor"],
        "baseline_aging_interval_steps": baseline_params["aging_interval_steps"],
        "predictor_decay_factor": predictor_decay_factor,
        "source": "contract.json.frozen_baseline.mapping_by_budget_bytes / contract.json.frozen_predictor",
    }

    # Existing canonical progress for this exact budget (item 7).
    n_canonical_already_done = sum(
        1 for l in launches
        if run_record_path(l["run_id"]).exists()
        and (lambda rp=run_record_path(l["run_id"]): (
            json.loads(rp.read_text()).get("ok") is True and json.loads(rp.read_text()).get("canonical") is True
        ))()
    )
    report["existing_canonical_progress"] = {
        "canonical_completed": n_canonical_already_done, "of_total": len(launches),
    }

    # Parent-environment trace-variable visibility (informational; the real
    # defense is the per-launch pre-Popen assertion in _execute_one_launch).
    report["trace_env_absent_in_parent"] = {
        "retention_trace_env_present": "ARGINE_RETENTION_TRACE" in os.environ,
        "trace_output_variables_present": trace_output_variables_present(os.environ),
    }

    disk_check_report = check_disk_space_margin(WORK_DIR)
    report["disk_space_status"] = disk_check_report
    ok = ok and disk_check_report["ok"]

    # Performance path (default, enable_trace unset -> False): trace must be ABSENT.
    env_a = build_child_env("b_selected", "C:\\modelli\\m.psxs", "C:\\run\\b.trace.jsonl", budget_bytes, 128,
                             baseline_params["decay_factor"], baseline_params["aging_interval_steps"], predictor_decay_factor)
    env_b = build_child_env("predicted_protect", "C:\\modelli\\m.psxs", "C:\\run\\p.trace.jsonl", budget_bytes, 128,
                             baseline_params["decay_factor"], baseline_params["aging_interval_steps"], predictor_decay_factor)
    # Conformance path (explicit opt-in): trace must be PRESENT.
    env_a_traced = build_child_env("b_selected", "C:\\modelli\\m.psxs", "C:\\run\\b.trace.jsonl", budget_bytes, 128,
                                    baseline_params["decay_factor"], baseline_params["aging_interval_steps"], predictor_decay_factor,
                                    enable_trace=True)
    assertion_raised = False
    try:
        assert_trace_absent_for_performance(env_a_traced)
    except AssertionError:
        assertion_raised = True
    env_checks = {
        "b_selected_has_required_keys": all(k in env_a for k in (
            "ARGINE_EXPERT_STORE", "ARGINE_EXPERT_BUDGET", "ARGINE_EXPERT_SHARDS",
            "ARGINE_EXPERT_READ", "ARGINE_RETENTION_POLICY",
            "ARGINE_RETENTION_DECAY", "ARGINE_RETENTION_INTERVAL")),
        "predicted_protect_has_extra_keys": all(k in env_b for k in (
            "ARGINE_RETENTION_PREDICTOR_DECAY", "ARGINE_RETENTION_TOPK", "ARGINE_RETENTION_NEXPERTS")),
        "argine_prefetch_explicitly_none": env_a.get("ARGINE_PREFETCH", "MISSING") is None and env_b.get("ARGINE_PREFETCH", "MISSING") is None,
        "argine_expert_shards_is_1": env_a["ARGINE_EXPERT_SHARDS"] == "1" and env_b["ARGINE_EXPERT_SHARDS"] == "1",
        "argine_prefetch_removed_from_os_environ": "ARGINE_PREFETCH" not in build_os_environ(env_a),
        "trace_absent_by_default_on_performance_path": "ARGINE_RETENTION_TRACE" not in env_a
            and "ARGINE_RETENTION_TRACE" not in env_b,
        "trace_present_when_explicitly_enabled": "ARGINE_RETENTION_TRACE" in env_a_traced,
        "hard_assertion_fires_on_traced_env": assertion_raised,
        "assert_trace_absent_passes_on_performance_env": (
            assert_trace_absent_for_performance(env_a) is None
            and assert_trace_absent_for_performance(env_b) is None
        ) if True else False,
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

    def is_canonical_completed(run_id):
        """Mirrors cmd_execute's actual resume-skip predicate: a record is
        only ever skipped (treated as already done) when it exists, is
        ok==True, AND canonical==True. Mere path existence (e.g. a
        quarantined PRECANONICAL_TRACE_ENABLED_ATTEMPTS record left in place
        because this environment cannot delete files) is NOT enough."""
        rp = run_record_path(run_id)
        if not rp.exists():
            return False
        try:
            rec = json.loads(rp.read_text())
        except Exception:
            return False
        return rec.get("ok") is True and rec.get("canonical") is True

    # Find a launch run_id that genuinely has no record at all on disk, to
    # test the true "never seen before" path -- distinct from a run_id whose
    # path exists but is correctly non-canonical (tested separately below).
    truly_fresh_run_id = next((l["run_id"] for l in launches if not run_record_path(l["run_id"]).exists()), None)
    # Any launch run_id whose on-disk record exists with ok==true but
    # canonical!=true (a quarantined precanonical attempt): resume MUST NOT
    # silently treat it as completed.
    stale_precanonical_run_id = next(
        (l["run_id"] for l in launches
         if run_record_path(l["run_id"]).exists()
         and json.loads(run_record_path(l["run_id"]).read_text()).get("ok") is True
         and json.loads(run_record_path(l["run_id"]).read_text()).get("canonical") is not True),
        None)

    resume_checks = {
        "sample_record_persisted": selftest_path.exists(),
        "duplicate_run_id_detected_for_resume_skip": would_skip(selftest_run_id),
        "fresh_run_id_not_skipped": (truly_fresh_run_id is None) or (not is_canonical_completed(truly_fresh_run_id)),
        "stale_precanonical_record_not_treated_as_canonical_completed": (
            stale_precanonical_run_id is None) or (not is_canonical_completed(stale_precanonical_run_id)),
        "output_schema_fields_present": set(sample_record.keys()) >= {
            "run_id", "workload_id", "policy_id", "budget_gib", "repetition_index",
            "ordering_block", "cell_index", "position_in_abba_sequence",
            "timestamps", "environment", "runtime_metrics", "output_token_digest"},
    }
    report["resume_checks"] = resume_checks
    report["resume_checks_detail"] = {
        "truly_fresh_run_id_found": truly_fresh_run_id,
        "stale_precanonical_run_id_found": stale_precanonical_run_id,
    }
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
        from tools.r2_12_benchmark_driver_selftest import (
            run_fake_runtime_integration_test, run_inherited_env_regression_test,
            run_trace_enabled_overrides_stale_inherited_test, run_gate_enrichment_regression_test)
    except Exception:
        sys.path.insert(0, str(REPO_ROOT / "tools"))
        from r2_12_benchmark_driver_selftest import (  # type: ignore
            run_fake_runtime_integration_test, run_inherited_env_regression_test,
            run_trace_enabled_overrides_stale_inherited_test, run_gate_enrichment_regression_test)
    try:
        fake_result = run_fake_runtime_integration_test()
    except Exception as e2:
        fake_result = {"ok": False, "error": f"{type(e2).__name__}: {e2}"}
    try:
        inherited_env_result = run_inherited_env_regression_test()
    except Exception as e2:
        inherited_env_result = {"ok": False, "error": f"{type(e2).__name__}: {e2}"}
    try:
        trace_override_result = run_trace_enabled_overrides_stale_inherited_test()
    except Exception as e2:
        trace_override_result = {"ok": False, "error": f"{type(e2).__name__}: {e2}"}
    try:
        gate_enrichment_result = run_gate_enrichment_regression_test()
    except Exception as e2:
        gate_enrichment_result = {"ok": False, "error": f"{type(e2).__name__}: {e2}"}
    report["fake_runtime_integration_test"] = fake_result
    report["inherited_env_regression_test"] = inherited_env_result
    report["trace_enabled_overrides_stale_inherited_test"] = trace_override_result
    report["gate_enrichment_regression_test"] = gate_enrichment_result
    ok = ok and bool(fake_result.get("ok")) and bool(inherited_env_result.get("ok")) \
        and bool(trace_override_result.get("ok")) and bool(gate_enrichment_result.get("ok"))

    gate = host_preflight_gate(
        Path(args.runtime) if args.runtime else None,
        Path(args.model) if args.model else None,
        Path(args.store) if args.store else None,
        budget_bytes=budget_bytes,
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
    # "whether launch is currently safe" (item 7): everything above EXCEPT
    # operator_attested_clean_environment (which is never true at preflight
    # time -- it is only ever asserted explicitly by the operator at
    # --execute time) and the campaign not yet being fully done.
    report["launch_currently_safe"] = ok and n_canonical_already_done < len(launches)
    report["overall_ok"] = ok

    out_path = RESULTS_DIR / f"PREFLIGHT-{filename_label}-{time.strftime('%Y%m%dT%H%M%SZ', time.gmtime())}.json"
    atomic_write_json(out_path, report)
    print(json.dumps({"overall_ok": ok, "target_budget_gib": budget_gib, "target_session_id": target_session_id,
                       "launch_currently_safe": report["launch_currently_safe"], "report_path": str(out_path),
                       "n_launches": len(launches), "n_warmup": n_warmup, "n_counted": n_counted}, indent=2))
    return 0 if ok else 1


# ---------------------------------------------------------------------------
# Execute mode -- real runtime launches, gated by explicit flags + the gate.
# ---------------------------------------------------------------------------

def classify_existing_record_for_resume(existing: dict) -> str:
    """Pure classification of an existing on-disk record found at a
    launch's run_id path, used by cmd_execute's resume logic. Factored out
    (same pattern as build_execute_gate) so this exact fail-closed
    behavior -- a precanonical/diagnostic record is NEVER silently treated
    as a completed canonical launch -- can be tested directly, without
    argparse, a lock, or a launch loop.
    Returns one of: "skip" (genuinely completed AND canonical -- safe to
    reuse), "halt_precanonical" (ok=true but canonical!=true -- a
    quarantined/diagnostic attempt; must halt), "halt_failed" (a recorded
    failure; must halt)."""
    if existing.get("ok") is True and existing.get("canonical") is True:
        return "skip"
    if existing.get("ok") is True and existing.get("canonical") is not True:
        return "halt_precanonical"
    return "halt_failed"


def build_execute_gate(runtime_exe: Path, model_path: Path, store_path: Path,
                        operator_attested_clean_environment: bool,
                        budget_bytes: int = 4294967296) -> dict:
    """Builds the full pre-execute gate: host_preflight_gate()'s checks
    (hash/RAM/lock/operator-attestation) PLUS the disk-space margin check
    and parent-environment trace-variable visibility, all merged into the
    SAME {"ok": bool, "checks": [ {"name","ok","detail"}, ... ]} schema
    host_preflight_gate() already uses. Factored out of cmd_execute so the
    enrichment logic (previously broken: dict(gate["checks"], ...) crashed
    because "checks" is a LIST, not a dict -- "dictionary update sequence
    element #0 has length 3; 2 is required") can be exercised and tested
    in isolation, with no argparse, no lock, no launch loop, and no
    possibility of reaching _execute_one_launch."""
    gate = host_preflight_gate(runtime_exe, model_path, store_path, budget_bytes,
                                operator_attested_clean_environment=operator_attested_clean_environment)
    disk_check = check_disk_space_margin(WORK_DIR)
    # Report, at the parent-environment level, exactly what the 2026-11-15
    # host failure was: an inherited ARGINE_RETENTION_TRACE (or equivalent)
    # the operator's own shell was carrying before the driver even runs.
    # This is informational here (the real defense is the per-launch
    # pre-Popen assertion, which strips/checks the CHILD env regardless of
    # what the parent carries) -- surfaced early so the operator sees it
    # before approving anything.
    parent_trace_vars = trace_output_variables_present(os.environ)
    gate["retention_trace_env_present"] = "ARGINE_RETENTION_TRACE" in os.environ
    gate["trace_output_variables_present"] = parent_trace_vars
    # host_preflight_gate()'s "checks" is a LIST of {"name","ok","detail"}
    # entries (built by its own rec() helper), never a dict -- dict(gate["checks"], ...)
    # blew up with "dictionary update sequence element #0 has length 3; 2 is
    # required" because dict() tried to read each 3-key check dict as a
    # 2-tuple. Append in the SAME schema instead of coercing the type.
    gate["checks"] = list(gate.get("checks") or []) + [
        {"name": "disk_space_margin", "ok": disk_check["ok"], "detail": json.dumps(disk_check)}
    ]
    gate["disk_space_margin_detail"] = disk_check
    gate["ok"] = gate["ok"] and disk_check["ok"]
    return gate


def cmd_execute(args) -> int:
    budget_gib = getattr(args, "budget_gib", 4.0)
    if budget_gib not in BUDGET_SESSION_IDS:
        print(f"Refusing to execute: budget_gib={budget_gib!r} is not one of the frozen "
              f"R2-12 budgets {sorted(BUDGET_SESSION_IDS)}.", file=sys.stderr)
        return 2
    expected_session_id = BUDGET_SESSION_IDS[budget_gib]
    # Fail-closed session/budget mismatch (item 3): the operator confirmation
    # must name the EXACT session for the requested budget -- "4gib-primary"
    # is never accepted for a 1.5/8 GiB run, and vice versa, so a copy-pasted
    # or stale --confirm-session can never silently start the wrong campaign.
    if not args.i_understand_this_starts_real_inference or args.confirm_session != expected_session_id:
        print(f"Refusing to execute: both --i-understand-this-starts-real-inference "
              f"and --confirm-session={expected_session_id} (matching --budget-gib={budget_gib}) "
              "must be given explicitly.", file=sys.stderr)
        return 2

    budget_bytes = budget_gib_to_bytes(budget_gib)
    frozen = load_frozen_protocol()
    cells, launches = build_budget_plan(frozen, budget_gib)

    runtime_exe = Path(args.runtime)
    model_path = Path(args.model)
    store_path = Path(args.store)

    gate = build_execute_gate(runtime_exe, model_path, store_path,
                               args.operator_attested_clean_environment,
                               budget_bytes=budget_bytes)
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
                verdict = classify_existing_record_for_resume(existing)
                if verdict == "skip":
                    records_by_run_id[launch["run_id"]] = existing
                    continue  # genuinely completed AND canonical -- skip, never rerun
                if verdict == "halt_precanonical":
                    # ok=true but not canonical (e.g. a precanonical trace-enabled
                    # attempt from before this fix, or a diagnostic record) must
                    # NEVER be silently treated as a completed canonical launch.
                    print(f"CAMPAIGN HALTED: run_id={launch['run_id']} has an existing "
                          "record with ok=true but canonical != true (a precanonical "
                          "or diagnostic attempt). It is not reused. Move it aside under "
                          "a precanonical/diagnostic quarantine path before resuming.",
                          file=sys.stderr)
                    return 7
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

            record = _execute_one_launch(launch, runtime_exe, model_path, store_path, frozen, port=args.port,
                                          enable_trace=False)  # canonical performance path: trace OFF
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


def cmd_diagnose(args) -> int:
    """Single-launch, NON-COUNTED, NON-CANONICAL diagnostic (item 10). Real
    model, real runtime, same performance path as the canonical campaign
    (enable_trace=False), but: one workload, one policy, not part of the
    frozen 4 GiB plan's run_ids, never written into a path the canonical
    resume logic reads, and explicitly tagged canonical=False/
    diagnostic_only=True. Does not touch the 48-launch campaign lock, the
    frozen schedule, or RESULTS_DIR's canonical record set. The 48-launch
    campaign remains LOCKED regardless of this diagnostic's outcome --
    running this does not unlock it."""
    budget_gib = getattr(args, "budget_gib", 4.0)
    if budget_gib not in BUDGET_SESSION_IDS:
        print(f"Diagnostic refused: budget_gib={budget_gib!r} is not one of the frozen "
              f"R2-12 budgets {FROZEN_BUDGET_CHOICES}.", file=sys.stderr)
        return 2
    budget_bytes = budget_gib_to_bytes(budget_gib)
    filename_label = BUDGET_FILENAME_LABELS[budget_gib]

    frozen = load_frozen_protocol()
    baseline_params = resolve_baseline_params_for_budget(frozen, budget_bytes)
    predictor_decay_factor = resolve_predictor_decay_factor(frozen)
    runtime_exe = Path(args.runtime)
    model_path = Path(args.model)
    store_path = Path(args.store)

    disk_check = check_disk_space_margin(WORK_DIR)
    if not disk_check["ok"]:
        print("Diagnostic refused: disk-space margin check failed.", file=sys.stderr)
        print(json.dumps(disk_check, indent=2), file=sys.stderr)
        return 3
    # Same parent-environment visibility as cmd_execute (informational; the
    # real defense is _execute_one_launch's own pre-Popen assertion below).
    parent_trace_vars = trace_output_variables_present(os.environ)
    retention_trace_env_present_in_parent = "ARGINE_RETENTION_TRACE" in os.environ

    # Identity/path reflect the selected budget (item 4): the hash key and
    # the output filename both carry budget_gib, so a 1.5/4/8 GiB diagnostic
    # run in quick succession can never collide or be mistaken for another.
    diag_run_id = "DIAG-" + hashlib.sha256(
        f"diagnostic-{budget_gib}-{time.time()}".encode()).hexdigest()[:16]
    launch = {
        "run_id": diag_run_id, "workload_id": "r2-11-coding-v1", "policy_id": "b_selected",
        "budget_gib": budget_gib, "repetition_index": 0, "ordering_block": "diagnostic",
        "cell_index": -1, "position_in_abba_sequence": -1,
        "warmup": False, "counted": False,
    }

    # _execute_one_launch() derives budget_bytes/baseline decay/interval/
    # predictor_decay from launch["budget_gib"] itself (budget_gib_to_bytes +
    # resolve_baseline_params_for_budget + resolve_predictor_decay_factor,
    # same as build_budget_plan's launches) -- nothing else to pass here.
    record = _execute_one_launch(launch, runtime_exe, model_path, store_path, frozen,
                                  port=args.port, enable_trace=False, diagnostic_only=True)

    diag_dir = RESULTS_DIR / "DIAGNOSTIC-ONLY-NOT-CANONICAL"
    diag_dir.mkdir(parents=True, exist_ok=True)
    out_path = diag_dir / f"{filename_label}-{diag_run_id}.json"
    atomic_write_json(out_path, record)

    ts = record.get("timestamps", {})
    rm = record.get("runtime_metrics", {})
    t0 = ts.get("t0_process_start")
    t1_ready = ts.get("t1_model_ready")
    t2 = ts.get("t2_request_sent")
    t3 = ts.get("t3_first_token")
    t4 = ts.get("t4_last_token")

    def dt(a, b):
        return (b - a) if (a is not None and b is not None) else None

    breakdown = {
        "ok": record.get("ok"),
        "crash_or_failure": record.get("crash_or_failure"),
        "canonical": record.get("canonical"),
        "diagnostic_only": record.get("diagnostic_only"),
        "trace_enabled": record.get("trace_enabled"),
        "budget_gib": budget_gib,
        "budget_bytes": budget_bytes,
        "resolved_policy_params": {
            "baseline_decay_factor": baseline_params["decay_factor"],
            "baseline_aging_interval_steps": baseline_params["aging_interval_steps"],
            "predictor_decay_factor": predictor_decay_factor,
        },
        "actual_child_env_retention_decay": record.get("environment", {}).get("ARGINE_RETENTION_DECAY"),
        "actual_child_env_retention_interval": record.get("environment", {}).get("ARGINE_RETENTION_INTERVAL"),
        "actual_child_env_expert_budget": record.get("environment", {}).get("ARGINE_EXPERT_BUDGET"),
        "process_start_to_model_ready_seconds": dt(t0, t1_ready),
        "model_ready_to_request_sent_seconds": dt(t1_ready, t2),
        "ttft_seconds": dt(t2, t3),
        "decode_duration_seconds": dt(t3, t4),
        "total_wall_clock_seconds": dt(t0, t4),
        "decode_tokens_per_second": rm.get("decode_tokens_per_second"),
        "cpu_time_seconds": rm.get("cpu_time_seconds"),
        "peak_resident_memory_bytes": rm.get("peak_resident_memory_bytes"),
        "driver_managed_artifact_bytes_written": record.get("driver_managed_artifact_bytes_written"),
        "external_trace_output_detected": record.get("external_trace_output_detected"),
        "pre_popen_env_check": record.get("pre_popen_env_check"),
        "parent_environment_trace_output_variables_present": parent_trace_vars,
        "parent_environment_retention_trace_env_present": retention_trace_env_present_in_parent,
        "target_seconds_per_launch": "2-4 minutes (120-240s)",
        "stop_and_investigate_threshold_seconds": 300,
        "exceeds_stop_threshold": (dt(t0, t4) or 0) > 300,
        "output_path": str(out_path),
        "note": "Non-counted, non-canonical. Does NOT unlock or advance the "
                "48-launch campaign, which remains LOCKED.",
    }
    print(json.dumps(breakdown, indent=2))
    return 0 if record.get("ok") else 4


def build_arg_parser():
    """Factored out of main() so tests can parse an explicit argv list
    (e.g. build_arg_parser().parse_args(["diagnose", "--budget-gib", "1.5", ...]))
    and assert on the resulting namespace -- the SAME parser main() uses,
    not a duplicated test-only one."""
    import argparse
    p = argparse.ArgumentParser(description=__doc__)
    sub = p.add_subparsers(dest="cmd", required=True)

    pf = sub.add_parser("preflight", help="Zero-inference structural dry-run.")
    pf.add_argument("--runtime", default=r"C:\Users\alpel\Desktop\Argine2\llama-research-v8\llama-server.exe")
    pf.add_argument("--model", default=r"C:\modelli\Qwen_Qwen3-30B-A3B-Q4_K_M.gguf")
    pf.add_argument("--store", default=r"C:\modelli\Qwen_Qwen3-30B-A3B-Q4_K_M.psxs")
    pf.add_argument("--budget-gib", type=float, default=4.0, dest="budget_gib",
                     choices=FROZEN_BUDGET_CHOICES,
                     help="Which frozen R2-12 budget to preflight (default 4.0, the already-completed primary).")
    pf.set_defaults(func=cmd_preflight)

    ex = sub.add_parser("execute", help="Real campaign execution for one frozen budget. DO NOT RUN without authorization.")
    ex.add_argument("--runtime", default=r"C:\Users\alpel\Desktop\Argine2\llama-research-v8\llama-server.exe")
    ex.add_argument("--model", default=r"C:\modelli\Qwen_Qwen3-30B-A3B-Q4_K_M.gguf")
    ex.add_argument("--store", default=r"C:\modelli\Qwen_Qwen3-30B-A3B-Q4_K_M.psxs")
    ex.add_argument("--port", type=int, default=PORT)
    ex.add_argument("--budget-gib", type=float, default=4.0, dest="budget_gib",
                     choices=FROZEN_BUDGET_CHOICES,
                     help="Which frozen R2-12 budget to execute. --confirm-session must name the matching "
                          "session (4gib-primary / 1.5gib-stress / 8gib-control) or the run is refused.")
    ex.add_argument("--i-understand-this-starts-real-inference", action="store_true", dest="i_understand_this_starts_real_inference")
    ex.add_argument("--confirm-session", default="", dest="confirm_session")
    ex.add_argument("--operator-attested-clean-environment", action="store_true", dest="operator_attested_clean_environment")
    ex.set_defaults(func=cmd_execute)

    dg = sub.add_parser("diagnose", help="ONE non-counted, non-canonical real-model diagnostic launch. "
                                          "Does not start or unlock the 48-launch campaign.")
    dg.add_argument("--runtime", default=r"C:\Users\alpel\Desktop\Argine2\llama-research-v8\llama-server.exe")
    dg.add_argument("--model", default=r"C:\modelli\Qwen_Qwen3-30B-A3B-Q4_K_M.gguf")
    dg.add_argument("--store", default=r"C:\modelli\Qwen_Qwen3-30B-A3B-Q4_K_M.psxs")
    dg.add_argument("--port", type=int, default=PORT)
    dg.add_argument("--budget-gib", type=float, default=4.0, dest="budget_gib",
                     choices=FROZEN_BUDGET_CHOICES,
                     help="Which frozen R2-12 budget's env/policy params this ONE diagnostic launch uses "
                          "(default 4.0). Same source of truth as preflight/execute -- never a separate "
                          "hardcoded value. Does not touch or require any campaign session confirmation.")
    dg.set_defaults(func=cmd_diagnose)

    return p


def main() -> int:
    args = build_arg_parser().parse_args()
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
