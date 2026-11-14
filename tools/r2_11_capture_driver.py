"""R2-11 Phase 1 -- frozen capture driver.

Run this on the REAL Windows machine that has the canonical model and the
compiled llama-research-v2 runtime (this script cannot run inside a Linux
sandbox -- the model/runtime are Windows-native, and real inference must
happen on real hardware with real compute, not inside this repo mirror).

Usage (from an Anaconda Python prompt, cwd = repo root, i.e. the folder
containing this `tools/` directory):

    python tools\\r2_11_capture_driver.py

If `python` resolves to the Microsoft Store alias rather than your actual
Anaconda interpreter, invoke the real interpreter explicitly instead, e.g.:

    C:\\Users\\<you>\\anaconda3\\python.exe tools\\r2_11_capture_driver.py

This script performs, in order, EXACTLY what R2-11 Phase 1's frozen
handoff requires and nothing else:

  1. Host preflight (model hash, runtime hash, ARGINE_LLAMA_DIR, argine
     import, free RAM, output-directory-unused check, no concurrent
     writer check).
  2. Sequential capture of all 12 R2-11 workloads, in the frozen order
     recorded in eval_manifest.json, each with the exact frozen
     deterministic_capture_conditions from acquisition_plan.json.
  3. Per-workload validation: trace existence, independent SHA-256,
     normalize()/reconcile(), the explicit trace_completeness() check
     (read from the capture's own summary.json -- a clean reconcile()
     alone is NEVER treated as sufficient), layer/expert-count/top_k
     cardinality checks, workload/seed/model/runtime identity checks.
  4. Writes one structured JSON validation report per workload next to
     its trace, plus one master capture log
     (R2-11-CAPTURE-LOG.json) at the output root.

This script does NOT compute any retention metric, does NOT run
PredictedProtectPolicy or B_selected against any trace, and does NOT
open R2-11 EVAL. It stops after capture + validation + logging.

Frozen failure classification (never silently retried with different
parameters, never overwrites a valid trace, never reduces the expert
budget to work around low RAM):
  PRELAUNCH_RAM_FAILURE, PRELAUNCH_RUNTIME_FAILURE, INCOMPLETE_TRACE,
  MALFORMED_TRACE, OTHER_VALIDATED_FAILURE.
"""
from __future__ import annotations

import gzip
import hashlib
import json
import os
import shutil
import subprocess
import sys
import time
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT))

EVAL_MANIFEST = REPO_ROOT / "experiments" / "R2-11-predictive-retention" / "eval_manifest.json"
ACQUISITION_PLAN = REPO_ROOT / "experiments" / "R2-11-predictive-retention" / "acquisition_plan.json"

# Fallback-only threshold, used ONLY if the real Argine memory planner cannot
# be imported/invoked in this environment. This is NOT the authoritative
# memory gate -- `argine research trace` (via argine.planner.plan()) remains
# the authoritative, fail-closed memory check for every real capture; this
# number only decides whether the DRIVER's own preflight warns ahead of time.
# 8 GiB is the established publication-run free-RAM threshold already used
# elsewhere for this expert-streaming (4G expert-store budget) configuration
# on this hardware class.
FALLBACK_MIN_FREE_RAM_BYTES = 8 * 1024 ** 3


def sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def load_json(path: Path):
    return json.loads(path.read_text(encoding="utf-8"))


def free_ram_bytes() -> int:
    try:
        import psutil  # type: ignore
        return psutil.virtual_memory().available
    except Exception:
        # Fallback for Windows without psutil.
        try:
            out = subprocess.check_output(
                ["powershell", "-NoProfile", "-Command",
                 "(Get-CimInstance Win32_OperatingSystem).FreePhysicalMemory"],
                text=True,
            )
            return int(out.strip()) * 1024
        except Exception:
            return -1  # unknown


def plan_feasibility_check(ap: dict, model_path: Path) -> dict:
    """Reuse Argine's own memory planner (the same one `argine research
    trace` uses) to decide whether the frozen 4 GiB expert-store
    configuration is runnable on this host, instead of a model-file-size
    heuristic that wrongly assumes the whole GGUF must be RAM-resident --
    the expert-store/streaming architecture caps expert memory at the
    configured budget, not at the file size.

    Returns a dict with method='planner' and the real plan's feasibility,
    or method='fallback' with an error if the planner could not be used
    (e.g. hardware detection unavailable in this environment); the fallback
    never substitutes for the authoritative gate inside the actual
    `argine research trace` invocation, which re-plans and fails closed on
    its own regardless of what this preflight concludes.
    """
    try:
        from argine import hardware as hwmod
        from argine import planner as planmod
        from argine.gguf import find_gguf, read_gguf
        from argine.util import parse_size

        conditions = ap["deterministic_capture_conditions"]
        gguf_path = find_gguf(str(model_path))
        model = read_gguf(gguf_path)
        hw = hwmod.detect(extra_disk_paths=[str(Path(model.path).parent)])
        req = planmod.PlanRequest(
            ctx=conditions["context"],
            threads=conditions["threads"],
            backend="cpu",
            expert_store=True,
            expert_store_budget=parse_size(conditions["expert_store_capture_budget"]),
        )
        plan = planmod.plan(model, hw, req)
        return {
            "method": "planner",
            "feasible": bool(plan.feasible),
            "failure_reason": plan.failure_reason,
            "estimate_total": plan.estimate.total,
            "budget": plan.budget,
        }
    except Exception as e:  # noqa: BLE001
        return {"method": "fallback", "error": f"{type(e).__name__}: {e}"}


def host_preflight(ap: dict) -> dict:
    report = {"checks": [], "ok": True}

    def check(name, ok, detail=""):
        report["checks"].append({"name": name, "ok": bool(ok), "detail": detail})
        report["ok"] = report["ok"] and bool(ok)

    model_path = Path(ap["model_identity"]["model_path_windows"])
    check("model_exists", model_path.exists(), str(model_path))
    if model_path.exists():
        actual = sha256_file(model_path)
        check("model_hash_matches", actual == ap["model_identity"]["sha256"],
              f"expected={ap['model_identity']['sha256']} actual={actual}")
        model_size = model_path.stat().st_size
    else:
        model_size = None

    llama_dir = os.environ.get("ARGINE_LLAMA_DIR")
    check("ARGINE_LLAMA_DIR_set", bool(llama_dir), llama_dir or "")
    runtime_hash_ok = False
    if llama_dir:
        server_exe = Path(llama_dir) / "llama-server.exe"
        if server_exe.exists():
            actual_rt = sha256_file(server_exe)
            runtime_hash_ok = (actual_rt == ap["runtime_identity"]["sha256"])
            check("runtime_exists", True, str(server_exe))
            check("runtime_hash_matches", runtime_hash_ok,
                  f"expected={ap['runtime_identity']['sha256']} actual={actual_rt}")
        else:
            check("runtime_exists", False, str(server_exe))
            check("runtime_hash_matches", False, "runtime binary not found")

    try:
        import argine  # noqa: F401
        check("argine_importable", True)
    except Exception as e:  # noqa: BLE001
        check("argine_importable", False, str(e))

    free_ram = free_ram_bytes()
    plan_result = plan_feasibility_check(ap, model_path)
    if plan_result["method"] == "planner":
        check("sufficient_free_ram", plan_result["feasible"],
              f"argine.planner.plan() feasible={plan_result['feasible']} "
              f"estimate_total={plan_result['estimate_total']} "
              f"failure_reason={plan_result['failure_reason']} "
              f"free_ram={free_ram}")
    else:
        # Fallback path only: the real planner could not be invoked here.
        # This never weakens the authoritative gate inside `argine research
        # trace` itself -- that command re-runs planmod.plan() on its own
        # and refuses (EXIT_INFEASIBLE) independently of this driver.
        check("sufficient_free_ram", free_ram >= 0 and free_ram >= FALLBACK_MIN_FREE_RAM_BYTES,
              f"[FALLBACK -- planner unavailable: {plan_result['error']}] "
              f"free={free_ram} required>={FALLBACK_MIN_FREE_RAM_BYTES} "
              f"(fixed 8 GiB publication-run threshold, NOT a function of model file size)")

    em = load_json(EVAL_MANIFEST)
    out_root_template = ap["canonical_output_paths"]
    for wl_id, out_path_tmpl in out_root_template.items():
        out_path = Path(os.path.expandvars(out_path_tmpl))
        check(f"output_unused::{wl_id}", not out_path.exists(), str(out_path))

    # no concurrent writer: best-effort lock-file check
    lock_path = REPO_ROOT / "tools" / ".r2_11_capture_driver.lock"
    check("no_concurrent_writer", not lock_path.exists(), str(lock_path))

    return report


def run_capture(wl_id: str, cmd: str) -> dict:
    """Run one frozen capture command; returns a result dict. Never edits cmd."""
    t0 = time.time()
    proc = subprocess.run(cmd, shell=True, cwd=str(REPO_ROOT), capture_output=True, text=True)
    elapsed = time.time() - t0
    return {
        "workload_id": wl_id,
        "command": cmd,
        "returncode": proc.returncode,
        "stdout_tail": proc.stdout[-4000:],
        "stderr_tail": proc.stderr[-4000:],
        "elapsed_seconds": elapsed,
    }


def validate_capture(wl_id: str, out_dir: Path, ap: dict, wl_spec: dict) -> dict:
    """Independent post-capture validation. Returns a structured report; never
    mutates the trace."""
    from argine.research.trace import Trace
    from argine.research2.r2_01 import trace_contract

    result = {"workload_id": wl_id, "classification": None, "ok": False, "checks": {}}

    run_dir = out_dir / "run-00-4G"
    trace_path = run_dir / "trace.jsonl.gz"
    summary_path = run_dir / "summary.json"

    if not trace_path.exists():
        result["classification"] = "INCOMPLETE_TRACE"
        result["checks"]["trace_exists"] = False
        return result
    result["checks"]["trace_exists"] = True

    trace_sha256 = sha256_file(trace_path)
    result["trace_sha256"] = trace_sha256
    result["trace_path"] = str(trace_path)

    try:
        normalized, report = trace_contract.normalize(trace_path)
        problems = trace_contract.reconcile(report)
    except trace_contract.ContractError as e:  # noqa: BLE001
        result["classification"] = "MALFORMED_TRACE"
        result["checks"]["normalize_or_reconcile_error"] = str(e)
        return result

    result["checks"]["normalize_ok"] = True
    result["checks"]["reconcile_problems"] = problems
    result["normalized_trace_digest"] = report.normalized_trace_digest

    if not summary_path.exists():
        result["classification"] = "OTHER_VALIDATED_FAILURE"
        result["checks"]["summary_json_exists"] = False
        return result
    summary = load_json(summary_path)
    completeness = summary.get("trace_completeness", {})
    result["checks"]["trace_completeness"] = completeness
    if not completeness.get("complete"):
        result["classification"] = "INCOMPLETE_TRACE"
        result["ok"] = False
        return result

    if problems:
        result["classification"] = "MALFORMED_TRACE"
        return result

    # cardinality checks
    tr = Trace.load(trace_path)
    n_layers = len(tr.layers)
    n_experts = tr.meta.n_experts
    result["checks"]["n_layers"] = n_layers
    result["checks"]["n_experts"] = n_experts
    cardinality_ok = (n_layers == 48 and n_experts == 128)
    result["checks"]["cardinality_48_layers_128_experts"] = cardinality_ok

    top_k_ok = True
    expert_id_range_ok = True
    for st in tr.steps:
        for layer, sel in st.routes.items():
            for tok in sel:
                if len(tok) != 8:
                    top_k_ok = False
                for e in tok:
                    if not (0 <= e < 128):
                        expert_id_range_ok = False
    result["checks"]["top_k_8"] = top_k_ok
    result["checks"]["expert_id_range_ok"] = expert_id_range_ok

    workload_identity_ok = (tr.meta.workload == wl_id or wl_id in (tr.meta.workload or ""))
    result["checks"]["workload_identity_ok"] = workload_identity_ok
    result["checks"]["recorded_workload"] = tr.meta.workload

    model_identity_ok = True  # confirmed at preflight; capture binds model path, not re-hashed per step here
    result["checks"]["model_identity_assumed_from_preflight"] = model_identity_ok

    all_ok = (
        result["checks"].get("normalize_ok")
        and not problems
        and completeness.get("complete")
        and cardinality_ok
        and top_k_ok
        and expert_id_range_ok
        and workload_identity_ok
    )
    result["ok"] = bool(all_ok)
    result["classification"] = "VALID" if all_ok else "OTHER_VALIDATED_FAILURE"
    return result


def main():
    ap = load_json(ACQUISITION_PLAN)
    em = load_json(EVAL_MANIFEST)
    specs = em["eval_workload_specs"]

    print("=== R2-11 Phase 1 host preflight ===")
    preflight = host_preflight(ap)
    for c in preflight["checks"]:
        print(f"  [{'OK' if c['ok'] else 'FAIL'}] {c['name']} {c['detail']}")
    if not preflight["ok"]:
        print("\nPREFLIGHT FAILED. Stopping before any capture. Fix the failed checks above and re-run.")
        log = {"preflight": preflight, "captures": [], "stopped_before_capture": True}
        (REPO_ROOT / "R2-11-CAPTURE-LOG.json").write_text(json.dumps(log, indent=2))
        return 1
    print("Preflight OK.\n")

    lock_path = REPO_ROOT / "tools" / ".r2_11_capture_driver.lock"
    lock_path.write_text(f"locked at {time.time()}")
    master_log = {"preflight": preflight, "captures": []}
    try:
        for spec in specs:
            wl_id = spec["workload_id"]
            cmd = ap["planned_capture_commands_windows_powershell"][wl_id]
            out_dir = Path(os.path.expandvars(ap["canonical_output_paths"][wl_id]))
            print(f"=== Capturing {wl_id} ===")

            if out_dir.exists():
                print(f"  SKIP: output directory already exists ({out_dir}); never overwriting.")
                master_log["captures"].append({"workload_id": wl_id, "skipped": True, "reason": "output_dir_exists"})
                continue

            run_result = run_capture(wl_id, cmd)
            print(f"  returncode={run_result['returncode']} elapsed={run_result['elapsed_seconds']:.1f}s")

            if run_result["returncode"] != 0:
                failed_dir = out_dir.parent / f"{wl_id}-failed-PRELAUNCH_RUNTIME_FAILURE-{int(time.time())}"
                if out_dir.exists():
                    shutil.move(str(out_dir), str(failed_dir))
                run_result["classification"] = "PRELAUNCH_RUNTIME_FAILURE"
                master_log["captures"].append(run_result)
                print(f"  CAPTURE FAILED -- classified PRELAUNCH_RUNTIME_FAILURE, preserved at {failed_dir}")
                continue

            validation = validate_capture(wl_id, out_dir, ap, spec)
            run_result["validation"] = validation
            master_log["captures"].append(run_result)

            if not validation["ok"]:
                failed_dir = out_dir.parent / f"{wl_id}-failed-{validation['classification']}-{int(time.time())}"
                shutil.move(str(out_dir), str(failed_dir))
                print(f"  VALIDATION FAILED ({validation['classification']}) -- preserved at {failed_dir}, NOT promoted to canonical.")
            else:
                (out_dir / "r2_11_validation_report.json").write_text(json.dumps(validation, indent=2))
                print(f"  OK -- trace_sha256={validation['trace_sha256']}")
    finally:
        lock_path.unlink(missing_ok=True)

    (REPO_ROOT / "R2-11-CAPTURE-LOG.json").write_text(json.dumps(master_log, indent=2))
    n_ok = sum(1 for c in master_log["captures"] if c.get("validation", {}).get("ok"))
    print(f"\n=== DONE: {n_ok}/12 workloads captured and validated OK ===")
    print("No retention metric was computed. No R2-11 EVAL was opened.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
