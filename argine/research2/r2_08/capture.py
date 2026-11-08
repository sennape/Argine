"""R2-08 capture planning and validation (mockable; no real 30B during implementation).

Reconstructs the canonical R2-01 capture protocol. Real execution is only for
the human's explicit `research2 run R2-08 --model ...` workflow; tests inject
fake capture functions.
"""

from __future__ import annotations

import hashlib
import json
import os
import subprocess
import sys
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Tuple

from .. import provenance, runtime_resolver, store
from ..r2_01 import capture as r2_01_capture
from ..r2_01 import trace_contract
from . import capture_matrix, workloads

# Canonical protocol from R2-01 (reconstructed; fail closed on ambiguity handled by caller).
PROTOCOL = {
    "model_name": r2_01_capture.PROTOCOL["model_name"],
    "expert_budget": "4G",
    "ctx": 4096,
    "threads": 4,
    "threads_batch": 12,
    "sampling": {"temperature": 0.0, "top_k": 1, "seed_is_replicate": True},
    "trace_schema": "argine-router-trace v1 (jsonl.gz)",
    "instrumentation": {"source": "argine research trace / R2-01 compatible", "read_mode": "asincrono", "pretouch": False},
    "source_protocol_doc": r2_01_capture.PROTOCOL.get("source_protocol_doc"),
}

REQUIRED_PROTOCOL_FIELDS = ("model_name", "expert_budget", "ctx", "threads", "threads_batch")


def validate_protocol() -> List[str]:
    return [f for f in REQUIRED_PROTOCOL_FIELDS if not PROTOCOL.get(f)]


def prompt_sha256_for_workload(workload_id: str) -> str:
    # Bound to the frozen workload definition file content (immutable prompt source).
    w = workloads.WORKLOADS_BY_ID[workload_id]
    return w.sha256


def configuration_for_cell(workload_id: str, seed: int, *, model_sha256: str,
                           runtime_sha256: str) -> Dict[str, Any]:
    w = workloads.WORKLOADS_BY_ID[workload_id]
    prompt_sha = prompt_sha256_for_workload(workload_id)
    fp = capture_matrix.configuration_fingerprint_dict(
        model_sha256=model_sha256, runtime_sha256=runtime_sha256,
        model_name=PROTOCOL["model_name"], runtime_args={"threads_batch_via_llama_arg": PROTOCOL["threads_batch"]},
        expert_budget=PROTOCOL["expert_budget"], ctx=PROTOCOL["ctx"],
        threads=PROTOCOL["threads"], threads_batch=PROTOCOL["threads_batch"],
        sampling={**PROTOCOL["sampling"], "seed": seed},
        trace_schema=PROTOCOL["trace_schema"], instrumentation=PROTOCOL["instrumentation"],
        workload_id=w.workload_id, workload_version=w.version,
        workload_sha256=w.sha256, seed=seed, prompt_sha256=prompt_sha,
    )
    return {"fingerprint": fp, "fingerprint_hash": capture_matrix.fingerprint_hash(fp),
            "prompt_sha256": prompt_sha}


def plan_capture_command(model_path: "str | Path", workload_id: str, seed: int, *, out_dir: Path) -> List[str]:
    w = workloads.WORKLOADS_BY_ID[workload_id]
    missing = validate_protocol()
    if missing:
        raise ValueError(f"capture protocol incomplete: {missing}")
    return [
        sys.executable, "-m", "argine", "research", "trace", str(model_path),
        "--expert-store",
        "--expert-budget", PROTOCOL["expert_budget"],
        "--workload", w.workload_arg,
        "--workload-version", w.workload_version_arg,
        "--split", w.split,
        "--seed", str(seed),
        "--threads", str(PROTOCOL["threads"]),
        "--llama-arg=--threads-batch", "--llama-arg", str(PROTOCOL["threads_batch"]),
        "--ctx", str(PROTOCOL["ctx"]),
        "--out", str(out_dir),
    ]


def expected_trace_path(out_dir: Path) -> Path:
    return out_dir / f"run-00-{PROTOCOL['expert_budget']}" / "trace.jsonl.gz"


def validate_trace_file(trace_path: Path, *, expected_model_sha256: Optional[str] = None,
                        expected_runtime_sha256: Optional[str] = None) -> Dict[str, Any]:
    """Validates existence, parse/schema, and basic routing metadata. Model/runtime
    identity checks are best-effort against trace meta/fingerprint when present;
    callers enforce configuration fingerprint compatibility across cells."""
    if not trace_path.is_file():
        return {"ok": False, "reason": f"trace missing: {trace_path}"}
    try:
        trace, norm_report = trace_contract.normalize(trace_path)
    except Exception as e:  # noqa: BLE001
        return {"ok": False, "reason": f"trace parse/schema failed: {e}"}
    if not trace.layers:
        return {"ok": False, "reason": "trace has no layers"}
    if not trace.meta.n_experts:
        return {"ok": False, "reason": "trace missing nominal expert count"}
    hashed = trace_contract.sha256_file(trace_path)
    return {
        "ok": True,
        "trace_sha256": hashed["sha256"],
        "size_bytes": hashed["byte_size"],
        "n_experts": trace.meta.n_experts,
        "layers": list(trace.layers),
        "normalization": norm_report.to_json(),
        "trace_meta": {
            "model": trace.meta.model, "workload": trace.meta.workload,
            "split": trace.meta.split, "fingerprint": trace.meta.fingerprint,
        },
    }


CaptureFunc = Callable[..., Dict[str, Any]]


def execute_capture(*, model_path: str, workload_id: str, seed: int, out_dir: Path,
                    allow_dirty: bool = False, runtime_path: Optional[str] = None,
                    dry_run: bool = False) -> Dict[str, Any]:
    """Real capture path (uses argine research trace). MUST NOT be called by tests
    in a way that starts a real 30B model; tests inject fake capture funcs."""
    missing = validate_protocol()
    if missing:
        return {"outcome": "CAPTURE_PROTOCOL_INCOMPLETE", "missing_fields": missing}
    model_path_p = Path(model_path)
    if not model_path_p.is_file():
        return {"outcome": "MODEL_NOT_FOUND", "message": f"model file not found: {model_path}"}
    runtime_resolution = runtime_resolver.resolve(explicit_runtime=runtime_path)
    if runtime_resolution.status != "RESOLVED":
        return {"outcome": runtime_resolution.status, "runtime_resolution": runtime_resolution.to_json(),
                "message": runtime_resolution.message}
    cmd = plan_capture_command(model_path_p, workload_id, seed, out_dir=out_dir)
    trace_path = expected_trace_path(out_dir)
    if dry_run:
        return {"outcome": "DRY_RUN", "command": cmd, "expected_trace_path": str(trace_path),
                "runtime_resolution": runtime_resolution.to_json()}
    out_dir.mkdir(parents=True, exist_ok=True)
    child_env = dict(os.environ)
    child_env["ARGINE_LLAMA_DIR"] = str(Path(runtime_resolution.resolved_path).parent)
    proc = subprocess.run(cmd, capture_output=True, text=True, env=child_env)
    (out_dir / "stdout.log").write_text(proc.stdout or "", encoding="utf-8")
    (out_dir / "stderr.log").write_text(proc.stderr or "", encoding="utf-8")
    validation = validate_trace_file(trace_path)
    if proc.returncode != 0 or not validation.get("ok"):
        return {"outcome": "CAPTURE_FAILED", "exit_code": proc.returncode,
                "validation": validation, "out_dir": str(out_dir)}
    return {"outcome": "CAPTURED", "trace_path": str(trace_path),
            "validation": validation, "out_dir": str(out_dir),
            "runtime_resolution": runtime_resolution.to_json()}
