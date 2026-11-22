"""R2-01A: fresh-capture readiness (used only when no historical trace can
be recovered -- see trace_source.py and this experiment's README).

Reuses Argine2's existing `argine research trace` command (section 6:
"use existing Argine tracing code"; never reimplements tracing, the
retention algorithm, or HoPi). This module never runs a real capture
automatically: `capture()` only executes a real `research trace`
subprocess when called explicitly with dry_run=False, and nothing in this
package calls it as a side effect of import, of `run`, or of `analyse`.
"""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional

from .. import provenance, runtime_resolver, store
from . import trace_contract, trace_source

OUTCOME_DRY_RUN = "DRY_RUN"
OUTCOME_CAPTURED = "CAPTURED"
OUTCOME_CAPTURE_FAILED = "CAPTURE_FAILED"
OUTCOME_PROTOCOL_INCOMPLETE = "CAPTURE_PROTOCOL_INCOMPLETE"
OUTCOME_MODEL_NOT_FOUND = "MODEL_NOT_FOUND"
OUTCOME_REFUSED_DIRTY_TREE = "REFUSED_DIRTY_TREE"
OUTCOME_RUNTIME_NOT_FOUND = "RUNTIME_NOT_FOUND"
OUTCOME_RUNTIME_AMBIGUOUS = "RUNTIME_AMBIGUOUS"

# Protocol reconstructed from experiments/EXP-019-instrumentation-validation/
# README.md (the 2026-10-12 publication-quality Windows validation run),
# Argine2's own repository. Every field here is either read directly from
# that README, or left unset because the `argine research trace` CLI's own
# default already matches the documented value (never an invented
# substitute for a real one):
#   - batch/ubatch: the README does not pin these for the historical run
#     either (only ctx/threads/threads-batch are given); the CLI's
#     `--batch`/`--ubatch` both default to None ("auto"), which is the
#     same choice the historical run made, so nothing is set here;
#   - read mode: the CLI's `--read-mode` default resolves to "asincrono",
#     matching the documented "Read mode requested = effective =
#     asincrono"; not passed explicitly;
#   - pretouch: the CLI's `--pretouch` is off unless given, matching the
#     documented "PRETOUCH off"; not passed;
#   - 16 cache shards: a fixed constant of the patched runtime itself, not
#     a CLI parameter -- there is nothing to set for it.
PROTOCOL = {
    "model_name": "Qwen3-30B-A3B-Q4_K_M",
    "workload": "coding",
    "workload_version": "v1",
    "workload_sha256": "460949e7abf004374bb0b06dcda89eeaba0e916b5f6053784afe037e6f2162d8",
    "split": "dev",
    "seed": 42,
    "threads": 4,
    "threads_batch": 12,
    "ctx": 4096,
    "expert_budget": "4G",
    "source_experiment": "EXP-019-instrumentation-validation",
    "source_protocol_doc": "experiments/EXP-019-instrumentation-validation/README.md",
}

# The fields above that are essential to reconstructing the protocol (not
# counting pure documentation/pointer fields). If any of these is ever
# missing -- e.g. this module is edited in the future and a field is
# dropped without a replacement -- validate_protocol() fails closed rather
# than silently capturing under an incomplete protocol.
REQUIRED_PROTOCOL_FIELDS = (
    "model_name", "workload", "workload_version", "split", "seed",
    "threads", "threads_batch", "ctx", "expert_budget",
)


class CaptureProtocolIncomplete(Exception):
    def __init__(self, missing_fields: List[str]):
        self.missing_fields = list(missing_fields)
        super().__init__(f"capture protocol incomplete: missing {self.missing_fields}")


def validate_protocol() -> List[str]:
    """Section 7: never invent a missing essential parameter. Returns the
    list of missing/empty required fields (empty list means complete)."""
    return [f for f in REQUIRED_PROTOCOL_FIELDS if not PROTOCOL.get(f)]


def plan_capture_command(model_path: "str | Path", *, out_dir: Path) -> List[str]:
    """The exact `argine research trace` invocation for the documented
    protocol (never a reimplementation of tracing). Raises
    CaptureProtocolIncomplete if the protocol is not complete -- callers
    must check validate_protocol() first (capture() does)."""
    missing = validate_protocol()
    if missing:
        raise CaptureProtocolIncomplete(missing)
    return [
        sys.executable, "-m", "argine", "research", "trace", str(model_path),
        "--expert-store",  # bare flag: Argine2's own <model>.psxs convention resolves it
        "--expert-budget", PROTOCOL["expert_budget"],
        "--workload", PROTOCOL["workload"],
        "--workload-version", PROTOCOL["workload_version"],
        "--split", PROTOCOL["split"],
        "--seed", str(PROTOCOL["seed"]),
        "--threads", str(PROTOCOL["threads"]),
        # Argine2's `--llama-arg ARG` is `action="append"`, one value per
        # occurrence: a value starting with "-" immediately after
        # `--llama-arg` is mis-parsed by argparse as another option
        # ("error: argument --llama-arg: expected one argument"), not as
        # that occurrence's value. The documented repository-defined
        # workaround for a dash-prefixed value is `--llama-arg=VALUE`
        # (the "=" form bypasses argparse's option-like-token check); a
        # plain value (no leading dash) still needs its own separate
        # `--llama-arg VALUE` occurrence. Verified against the real
        # `argine research trace` parser in tests/test_r2_01a_capture.py.
        "--llama-arg=--threads-batch", "--llama-arg", str(PROTOCOL["threads_batch"]),
        "--ctx", str(PROTOCOL["ctx"]),
        "--out", str(out_dir),
    ]


def _expected_run_subdir(out_dir: Path) -> Path:
    # Argine2's `research trace` names each budget/repeat's run directory
    # run-{repeat:02d}-{budget}; a single budget with the default repeat=1
    # produces exactly one: run-00-<budget>.
    return out_dir / f"run-00-{PROTOCOL['expert_budget']}"


def capture(*, model_path: "str | Path", allow_dirty: bool = False, dry_run: bool = True,
           out_root: Optional[Path] = None, argine2_cwd: Optional[Path] = None,
           runtime_path: Optional[str] = None
           ) -> Dict[str, Any]:
    """Plan (dry_run=True, default) or execute (dry_run=False) a fresh
    R2-01 capture. Never called with dry_run=False by this implementation
    task itself; dry_run=False is for the human's own later invocation.

    On a successful real capture: hashes the produced trace, registers it
    (argine.research2.store.set_trace_source_registration) as a
    "fresh_capture" source so `run R2-01` resolves it automatically next
    time, and writes capture provenance to the evidence root (never Git).
    """
    missing = validate_protocol()
    if missing:
        return {"experiment_id": "R2-01", "outcome": OUTCOME_PROTOCOL_INCOMPLETE,
                "missing_fields": missing,
                "message": "essential experimental parameters could not be "
                           "reconstructed from documentation; refusing to guess at "
                           "a capture protocol."}

    model_path = Path(model_path)
    if not model_path.is_file():
        return {"experiment_id": "R2-01", "outcome": OUTCOME_MODEL_NOT_FOUND,
                "message": f"model file not found: {model_path}. A fresh capture "
                           f"needs a local Qwen3-30B-A3B-Q4_K_M GGUF file; model "
                           f"weights are not distributed with the repository."}

    # Resolve the llama.cpp runtime deterministically BEFORE touching the
    # filesystem for this run: a RUNTIME_NOT_FOUND/RUNTIME_AMBIGUOUS result
    # must create no capture directory at all, exactly like
    # MODEL_NOT_FOUND/CAPTURE_PROTOCOL_INCOMPLETE above. Never relies on
    # whatever ARGINE_LLAMA_DIR happens to be inherited from the caller's
    # shell -- see runtime_resolver.resolve().
    runtime_resolution = runtime_resolver.resolve(explicit_runtime=runtime_path)
    if runtime_resolution.status == runtime_resolver.RUNTIME_NOT_FOUND:
        return {"experiment_id": "R2-01", "outcome": OUTCOME_RUNTIME_NOT_FOUND,
                "runtime_resolution": runtime_resolution.to_json(),
                "message": runtime_resolution.message}
    if runtime_resolution.status == runtime_resolver.RUNTIME_AMBIGUOUS:
        return {"experiment_id": "R2-01", "outcome": OUTCOME_RUNTIME_AMBIGUOUS,
                "runtime_resolution": runtime_resolution.to_json(),
                "message": runtime_resolution.message}

    rec = provenance.build_record("R2-01", outcome="PENDING", allow_dirty=allow_dirty)
    run_id = rec["identity"]["run_id"]
    candidate_status = rec["git"]["candidate_status"]

    out_root = Path(out_root) if out_root else store.evidence_root()
    capture_dir = out_root / "R2-01" / "captures" / run_id
    cmd = plan_capture_command(model_path, out_dir=capture_dir)
    expected_run_dir = _expected_run_subdir(capture_dir)

    if dry_run:
        return {
            "experiment_id": "R2-01", "run_id": run_id, "outcome": OUTCOME_DRY_RUN,
            "candidate_status": candidate_status,
            "protocol": dict(PROTOCOL), "command": cmd,
            "out_dir": str(capture_dir),
            "expected_trace_path": str(expected_run_dir / "trace.jsonl.gz"),
            "runtime_resolution": runtime_resolution.to_json(),
            "message": "planned only; pass dry_run=False (CLI: omit --dry-run) to "
                       "actually run the 30B capture. This has NOT executed any "
                       "model.",
        }

    capture_dir.mkdir(parents=True, exist_ok=True)
    # Pin the EXACT resolved runtime into the child subprocess's own
    # environment, overriding (never mutating) whatever ARGINE_LLAMA_DIR
    # the parent process inherited. This is what makes resolution
    # deterministic rather than merely diagnostic: the child cannot fall
    # back to a stale/different ARGINE_LLAMA_DIR behind resolve()'s back.
    child_env = dict(os.environ)
    child_env["ARGINE_LLAMA_DIR"] = str(Path(runtime_resolution.resolved_path).parent)
    proc = subprocess.run(cmd, cwd=str(argine2_cwd) if argine2_cwd else None,
                          capture_output=True, text=True, env=child_env)

    manifest_path = capture_dir / "manifest.json"
    _write_json(manifest_path, {
        **rec,
        "capture_command": cmd,
        "capture_protocol": PROTOCOL,
        "capture_exit_code": proc.returncode,
        "runtime_resolution": runtime_resolution.to_json(),
    })
    _write_text(capture_dir / "stdout.log", proc.stdout)
    _write_text(capture_dir / "stderr.log", proc.stderr)

    trace_path = expected_run_dir / "trace.jsonl.gz"
    if proc.returncode != 0 or not trace_path.is_file():
        return {
            "experiment_id": "R2-01", "run_id": run_id, "outcome": OUTCOME_CAPTURE_FAILED,
            "candidate_status": candidate_status, "exit_code": proc.returncode,
            "capture_dir": str(capture_dir),
            "message": "the `argine research trace` subprocess did not produce the "
                       "expected trace file; see stdout.log/stderr.log under "
                       "capture_dir. No source was registered.",
        }

    registration = trace_source.register_source(
        path=trace_path, source_experiment_id="R2-01", source_run_id=run_id,
        source_type="fresh_capture")
    store.set_trace_source_registration("R2-01", registration.to_json())

    # Optional automatic runtime registration (section 8): a REAL capture
    # that just succeeded is exactly the "explicit registration/candidate
    # capture" moment section 7 reserves for computing a runtime's
    # SHA-256 -- never done merely on status/doctor. Only when resolution
    # was not already a re-validated existing registration (tier
    # "registered"), and resolution already guarantees this was an
    # UNAMBIGUOUS single eligible runtime (never guessed between two).
    if runtime_resolution.tier != "registered":
        try:
            runtime_registration = runtime_resolver.register_runtime(
                runtime_resolution.resolved_path, resolution_source=runtime_resolution.tier)
            store.set_runtime_registration(runtime_registration)
        except (ValueError, OSError):
            pass  # local-state convenience only; never block a successful capture on it

    return {
        "experiment_id": "R2-01", "run_id": run_id, "outcome": OUTCOME_CAPTURED,
        "candidate_status": candidate_status,
        "trace_path": str(trace_path), "capture_dir": str(capture_dir),
        "registration": registration.to_json(),
        "message": "capture succeeded and was registered; "
                   "`argine.bat research2 run R2-01` will now resolve it "
                   "automatically.",
    }


def _write_json(path: Path, data: Dict[str, Any]) -> None:
    import json
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, indent=2, sort_keys=True, default=str), encoding="utf-8")


def _write_text(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text or "", encoding="utf-8")
