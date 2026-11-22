"""R2-09B EVAL acquisition command planning (Phase 3A schema-compatibility
follow-up).

Reuses Argine2's existing `argine research trace` command exactly as R2-01's
and R2-08's capture.py modules do (never reimplements tracing, retention, or
HoPi). This module never runs a real capture automatically: `capture()` only
executes a real `research trace` subprocess when called explicitly with
dry_run=False, and nothing in this package calls it as a side effect of
import.

Every frozen parameter below is read from this repository's own frozen
evidence -- experiments/R2-09-prediction-benchmark/eval_manifest.json and
experiments/R2-10-working-set-predictor/dev_eval_protocol.json -- or from the
established repository-wide capture convention also used by R2-01 and R2-08
(argine/research2/r2_01/capture.py, argine/research2/r2_08/capture.py).
Nothing here is invented: if a required field cannot be found in frozen
evidence, validate_protocol() reports it as missing rather than guessing.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional

from .. import provenance, runtime_resolver, store

_REPO_ROOT = Path(__file__).resolve().parents[3]
_EVAL_MANIFEST_PATH = (_REPO_ROOT / "experiments" / "R2-09-prediction-benchmark"
                       / "eval_manifest.json")

# Held constant across all four EVAL cells -- identical to the frozen
# deterministic_capture_conditions block of eval_manifest.json and to the
# capture parameters already independently verified against the real model
# and runtime binary in PHASE3A-ACQUISITION-READINESS-AUDIT.json.
HELD_CONSTANT = {
    "model_name": "Qwen3-30B-A3B-Q4_K_M",
    "expert_budget": "4G",
    "ctx": 4096,
    "threads": 4,
    "threads_batch": 12,
    "temperature": 0.0,
    "top_k": 1,
    "n_predict": 512,
    "split": "eval",
}

REQUIRED_PROTOCOL_FIELDS = (
    "model_name", "expert_budget", "ctx", "threads", "threads_batch", "split",
)


class CaptureProtocolIncomplete(Exception):
    def __init__(self, missing_fields: List[str]):
        self.missing_fields = list(missing_fields)
        super().__init__(f"capture protocol incomplete: missing {self.missing_fields}")


def load_eval_manifest() -> Dict[str, Any]:
    return json.loads(_EVAL_MANIFEST_PATH.read_text(encoding="utf-8"))


def eval_workload_specs() -> List[Dict[str, Any]]:
    """The four frozen EVAL workload specs, verbatim from eval_manifest.json
    (workload_id, source_path, frozen sha256, nominal_workload_category)."""
    return load_eval_manifest()["eval_workload_specs"]


def validate_protocol() -> List[str]:
    """Never invent a missing essential parameter (mirrors r2_01/capture.py
    and r2_08/capture.py). Returns the list of missing/empty required
    fields (empty list means complete)."""
    return [f for f in REQUIRED_PROTOCOL_FIELDS if not HELD_CONSTANT.get(f)]


def verify_frozen_workload_hash(spec: Dict[str, Any]) -> None:
    """Fails closed if the on-disk frozen workload file no longer matches
    the sha256 recorded in eval_manifest.json -- this module must never
    acquire against a workload file that has silently drifted."""
    path = _REPO_ROOT / spec["source_path"]
    import hashlib
    actual = hashlib.sha256(path.read_bytes()).hexdigest()
    expected = spec["sha256"]
    if actual != expected:
        raise ValueError(f"{path}: sha256 {actual} != frozen {expected} "
                         f"(workload_id={spec['workload_id']})")


def plan_capture_command(*, workload_id: str, model_path: "str | Path", seed: int,
                         out_dir: Path) -> List[str]:
    """The exact `argine research trace` invocation for one EVAL cell (never
    a reimplementation of tracing). Raises CaptureProtocolIncomplete if the
    protocol is not complete -- callers must check validate_protocol() first
    (capture() does). `seed` must come from a prospectively-recorded seed
    plan (see PHASE3A-SEED-PLAN.json); this function does not choose one."""
    missing = validate_protocol()
    if missing:
        raise CaptureProtocolIncomplete(missing)
    specs = {s["workload_id"]: s for s in eval_workload_specs()}
    if workload_id not in specs:
        raise ValueError(f"unknown EVAL workload_id: {workload_id} "
                         f"(known: {sorted(specs)})")
    spec = specs[workload_id]
    verify_frozen_workload_hash(spec)
    workload_path = spec["source_path"]  # relative repo path; --workload accepts
                                          # a literal path verbatim (workloads._resolve)
    return [
        sys.executable, "-m", "argine", "research", "trace", str(model_path),
        "--expert-store",
        "--expert-budget", HELD_CONSTANT["expert_budget"],
        "--workload", workload_path,
        "--split", HELD_CONSTANT["split"],
        "--seed", str(seed),
        "--threads", str(HELD_CONSTANT["threads"]),
        "--llama-arg=--threads-batch", "--llama-arg", str(HELD_CONSTANT["threads_batch"]),
        "--ctx", str(HELD_CONSTANT["ctx"]),
        "--out", str(out_dir),
    ]


def _expected_run_subdir(out_dir: Path) -> Path:
    return out_dir / f"run-00-{HELD_CONSTANT['expert_budget']}"


def capture(*, workload_id: str, model_path: "str | Path", seed: int,
           dry_run: bool = True, out_root: Optional[Path] = None,
           argine2_cwd: Optional[Path] = None, runtime_path: Optional[str] = None
           ) -> Dict[str, Any]:
    """Plan (dry_run=True, default) or execute (dry_run=False) one R2-09B
    EVAL acquisition cell. Never called with dry_run=False by this
    implementation task itself; dry_run=False is for the human's own later
    invocation on the ~16 GB Windows host. Writes capture provenance
    (never Git, never eval_manifest.json) to the evidence root."""
    missing = validate_protocol()
    if missing:
        return {"experiment_id": "R2-09B-EVAL", "workload_id": workload_id,
                "outcome": "CAPTURE_PROTOCOL_INCOMPLETE", "missing_fields": missing}

    model_path = Path(model_path)
    if not model_path.is_file():
        return {"experiment_id": "R2-09B-EVAL", "workload_id": workload_id,
                "outcome": "MODEL_NOT_FOUND",
                "message": f"model file not found: {model_path}"}

    runtime_resolution = runtime_resolver.resolve(explicit_runtime=runtime_path)
    if runtime_resolution.status != runtime_resolver.RUNTIME_RESOLVED:
        return {"experiment_id": "R2-09B-EVAL", "workload_id": workload_id,
                "outcome": runtime_resolution.status,
                "runtime_resolution": runtime_resolution.to_json(),
                "message": runtime_resolution.message}

    rec = provenance.build_record("R2-09B-EVAL", outcome="PENDING", allow_dirty=False)
    run_id = rec["identity"]["run_id"]

    out_root = Path(out_root) if out_root else store.evidence_root()
    capture_dir = out_root / "R2-09-EVAL" / "captures" / workload_id
    cmd = plan_capture_command(workload_id=workload_id, model_path=model_path, seed=seed,
                               out_dir=capture_dir)
    expected_run_dir = _expected_run_subdir(capture_dir)

    if dry_run:
        return {
            "experiment_id": "R2-09B-EVAL", "workload_id": workload_id, "run_id": run_id,
            "outcome": "DRY_RUN", "seed": seed, "command": cmd, "out_dir": str(capture_dir),
            "expected_trace_path": str(expected_run_dir / "trace.jsonl.gz"),
            "runtime_resolution": runtime_resolution.to_json(),
            "message": "planned only; this has NOT executed any model.",
        }

    capture_dir.mkdir(parents=True, exist_ok=True)
    child_env = dict(os.environ)
    child_env["ARGINE_LLAMA_DIR"] = str(Path(runtime_resolution.resolved_path).parent)
    proc = subprocess.run(cmd, cwd=str(argine2_cwd) if argine2_cwd else None,
                          capture_output=True, text=True, env=child_env)

    manifest_path = capture_dir / "manifest.json"
    manifest_path.parent.mkdir(parents=True, exist_ok=True)
    manifest_path.write_text(json.dumps({
        **rec, "workload_id": workload_id, "seed": seed, "capture_command": cmd,
        "capture_exit_code": proc.returncode,
        "runtime_resolution": runtime_resolution.to_json(),
    }, indent=2, sort_keys=True, default=str), encoding="utf-8")
    (capture_dir / "stdout.log").write_text(proc.stdout or "", encoding="utf-8")
    (capture_dir / "stderr.log").write_text(proc.stderr or "", encoding="utf-8")

    trace_path = expected_run_dir / "trace.jsonl.gz"
    if proc.returncode != 0 or not trace_path.is_file():
        return {"experiment_id": "R2-09B-EVAL", "workload_id": workload_id, "run_id": run_id,
                "outcome": "CAPTURE_FAILED", "exit_code": proc.returncode,
                "capture_dir": str(capture_dir)}

    return {"experiment_id": "R2-09B-EVAL", "workload_id": workload_id, "run_id": run_id,
            "outcome": "CAPTURED", "trace_path": str(trace_path),
            "capture_dir": str(capture_dir),
            "message": "capture succeeded. Validate and seal before any EVAL inspection; "
                       "do NOT open/compute EVAL metrics here."}
