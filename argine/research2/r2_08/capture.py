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
from . import capture_matrix, prerequisite, workloads

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

OUTCOME_CAPTURED = "CAPTURED"
OUTCOME_CAPTURE_FAILED = "CAPTURE_FAILED"
OUTCOME_DRY_RUN = "DRY_RUN"
OUTCOME_RUNTIME_NOT_FOUND = "RUNTIME_NOT_FOUND"
OUTCOME_RUNTIME_AMBIGUOUS = "RUNTIME_AMBIGUOUS"
OUTCOME_RUNTIME_IDENTITY_UNVERIFIED = "RUNTIME_IDENTITY_UNVERIFIED"


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


def _verified_runtime_sha256(runtime_resolution: runtime_resolver.RuntimeResolution,
                              *, root: Optional[Path] = None,
                              persist_registration: bool = False
                              ) -> Tuple[Optional[str], Optional[Dict[str, Any]], Optional[str]]:
    """Resolve the actual runtime binary SHA-256 for a RUNTIME_RESOLVED runtime.

    Never invents a SHA. Uses existing verified registration, the documented
    canonical identity (only when the resolver selected that exact tier/path),
    or computes via runtime_resolver.register_runtime() at an explicit capture
    moment. Returns (sha256, registration_dict_or_none, error_message_or_none).
    """
    if runtime_resolution.status != runtime_resolver.RUNTIME_RESOLVED:
        return None, None, f"runtime not resolved: {runtime_resolution.status}"
    resolved = runtime_resolution.resolved_path
    if not resolved:
        return None, None, "runtime resolution missing resolved_path"

    reg = store.get_runtime_registration(root)
    if reg and reg.get("path") and reg.get("sha256"):
        try:
            if Path(reg["path"]).resolve() == Path(resolved).resolve():
                return str(reg["sha256"]), reg, None
        except OSError:
            pass

    if runtime_resolution.tier == "documented_canonical":
        expected = runtime_resolver.DOCUMENTED_CANONICAL_RUNTIME["expected_sha256"]
        canon = runtime_resolver._documented_canonical_path()
        try:
            if canon is not None and canon.resolve() == Path(resolved).resolve():
                # The resolver already verified this file matches expected_sha256.
                registration = reg if (reg and reg.get("sha256") == expected) else {
                    "path": str(Path(resolved)),
                    "sha256": expected,
                    "resolution_source": "documented_canonical",
                    "capability_status": "expert-store marker present (documented canonical)",
                }
                if persist_registration and not (reg and reg.get("sha256") == expected):
                    try:
                        store.set_runtime_registration(registration, root)
                    except OSError:
                        pass
                return expected, registration, None
        except OSError:
            pass

    # Explicit capture-time registration (SHA computed only here, never on doctor/status).
    try:
        registration = runtime_resolver.register_runtime(
            resolved, resolution_source=runtime_resolution.tier or "capture")
        if persist_registration:
            try:
                store.set_runtime_registration(registration, root)
            except OSError:
                pass
        return str(registration["sha256"]), registration, None
    except (ValueError, OSError) as e:
        return None, None, f"could not establish verified runtime SHA for {resolved}: {e}"


def runtime_sha256_from_reused_r2_01_capture(*, root: Optional[Path] = None) -> Tuple[Optional[str], Optional[str]]:
    """Return (verified_runtime_sha256, reason_if_missing) for the canonical
    reused R2-01 capture (coding-v1/seed-42). Fails closed if provenance cannot
    establish the actual runtime SHA; never invents a value.
    """
    capture_manifest = ((root or store.evidence_root()) / "R2-01" / "captures"
                        / prerequisite.REUSED_CAPTURE_SOURCE / "manifest.json")
    if not capture_manifest.is_file():
        return None, f"canonical R2-01 capture manifest missing: {capture_manifest}"
    try:
        data = json.loads(capture_manifest.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError) as e:
        return None, f"cannot read canonical R2-01 capture manifest: {e}"
    rr = data.get("runtime_resolution") or {}
    if rr.get("status") != runtime_resolver.RUNTIME_RESOLVED or not rr.get("resolved_path"):
        return None, f"canonical R2-01 capture manifest has no resolved runtime: {rr}"

    resolved = str(rr["resolved_path"])
    reg = store.get_runtime_registration(root)
    if reg and reg.get("path") and reg.get("sha256"):
        try:
            if Path(reg["path"]).resolve() == Path(resolved).resolve():
                return str(reg["sha256"]), None
        except OSError:
            pass

    if rr.get("tier") == "documented_canonical":
        expected = runtime_resolver.DOCUMENTED_CANONICAL_RUNTIME["expected_sha256"]
        try:
            canon = runtime_resolver._documented_canonical_path()
            if canon is not None and canon.resolve() == Path(resolved).resolve():
                return expected, None
        except OSError:
            pass
        # The capture manifest is canonical provenance stating documented_canonical
        # tier; resolver only assigns that tier when SHA matches expected. Accept
        # the verified documented identity if the resolved path basename/build
        # matches the documented candidate path (provenance-bound).
        try:
            rels = runtime_resolver.DOCUMENTED_CANONICAL_RUNTIME["candidate_relative_paths"]
            if any(Path(resolved).name == Path(r).name and "llama-research-v2" in resolved.replace('\\', '/')
                   for r in rels):
                # Still verify hash if file exists.
                if Path(resolved).is_file():
                    if runtime_resolver._sha256_file(Path(resolved)) == expected:
                        return expected, None
                # File missing but manifest provenance is explicit; check registration
                # store may have moved - already checked. Fail closed.
        except (OSError, KeyError):
            pass
        return None, (f"cannot verify documented canonical runtime SHA from provenance "
                      f"for {resolved}; refusing placeholder/guess")

    # Other tiers: only accept if there is an explicit registration matching path.
    if reg and reg.get("sha256"):
        try:
            if Path(reg["path"]).resolve() == Path(resolved).resolve():
                return str(reg["sha256"]), None
        except OSError:
            pass
    return None, (f"canonical R2-01 capture runtime ({resolved}, tier={rr.get('tier')!r}) "
                  f"has no verifiable runtime SHA in provenance/registration; refusing placeholder/guess")


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
    if runtime_resolution.status == runtime_resolver.RUNTIME_NOT_FOUND:
        return {"outcome": OUTCOME_RUNTIME_NOT_FOUND, "runtime_resolution": runtime_resolution.to_json(),
                "message": runtime_resolution.message}
    if runtime_resolution.status == runtime_resolver.RUNTIME_AMBIGUOUS:
        return {"outcome": OUTCOME_RUNTIME_AMBIGUOUS, "runtime_resolution": runtime_resolution.to_json(),
                "message": runtime_resolution.message}
    if runtime_resolution.status != runtime_resolver.RUNTIME_RESOLVED:
        return {"outcome": runtime_resolution.status, "runtime_resolution": runtime_resolution.to_json(),
                "message": runtime_resolution.message}
    if not runtime_resolution.resolved_path:
        return {"outcome": OUTCOME_RUNTIME_NOT_FOUND, "runtime_resolution": runtime_resolution.to_json(),
                "message": "runtime resolved without a path"}

    cmd = plan_capture_command(model_path_p, workload_id, seed, out_dir=out_dir)
    trace_path = expected_trace_path(out_dir)
    runtime_sha256, runtime_registration, runtime_sha_err = _verified_runtime_sha256(
        runtime_resolution, persist_registration=False)
    if dry_run:
        result = {"outcome": OUTCOME_DRY_RUN, "command": cmd, "expected_trace_path": str(trace_path),
                  "runtime_resolution": runtime_resolution.to_json()}
        if runtime_sha256:
            result["runtime_sha256"] = runtime_sha256
        if runtime_registration:
            result["runtime_registration"] = runtime_registration
        return result

    if not runtime_sha256:
        return {"outcome": OUTCOME_RUNTIME_IDENTITY_UNVERIFIED,
                "runtime_resolution": runtime_resolution.to_json(),
                "message": runtime_sha_err or "verified runtime SHA could not be established"}

    out_dir.mkdir(parents=True, exist_ok=True)
    child_env = dict(os.environ)
    child_env["ARGINE_LLAMA_DIR"] = str(Path(runtime_resolution.resolved_path).parent)
    proc = subprocess.run(cmd, capture_output=True, text=True, env=child_env)
    (out_dir / "stdout.log").write_text(proc.stdout or "", encoding="utf-8")
    (out_dir / "stderr.log").write_text(proc.stderr or "", encoding="utf-8")
    validation = validate_trace_file(trace_path)
    if proc.returncode != 0 or not validation.get("ok"):
        return {"outcome": OUTCOME_CAPTURE_FAILED, "exit_code": proc.returncode,
                "validation": validation, "out_dir": str(out_dir),
                "runtime_resolution": runtime_resolution.to_json(),
                "runtime_sha256": runtime_sha256}
    # Explicit registration moment (consistent with R2-01): persist only after a
    # successful capture when not already registered.
    if runtime_resolution.tier != "registered" and runtime_registration is None:
        try:
            runtime_registration = runtime_resolver.register_runtime(
                runtime_resolution.resolved_path, resolution_source=runtime_resolution.tier)
            store.set_runtime_registration(runtime_registration)
            runtime_sha256 = str(runtime_registration["sha256"])
        except (ValueError, OSError):
            pass
    elif runtime_resolution.tier != "registered" and runtime_registration is not None:
        try:
            store.set_runtime_registration(runtime_registration)
        except OSError:
            pass
    return {"outcome": OUTCOME_CAPTURED, "trace_path": str(trace_path),
            "validation": validation, "out_dir": str(out_dir),
            "runtime_resolution": runtime_resolution.to_json(),
            "runtime_sha256": runtime_sha256,
            **({"runtime_registration": runtime_registration} if runtime_registration else {})}
